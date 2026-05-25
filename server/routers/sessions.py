from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from server.auth import require_token
from server.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from server.models import CreateSessionRequest, RestartSessionRequest, SessionDetail, SessionInfo
from server import store
from server.ws_manager import manager

logger = logging.getLogger(__name__)


class SessionReport(BaseModel):
    """Payload for session→commander callback."""
    event_type: str = "report"       # report, task_complete, error, progress
    status: str = "info"             # completed, error, in_progress, info
    summary: str                     # Human-readable message
    task_id: Optional[str] = None    # Commander task ID to update

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionInfo])
async def list_sessions(
    machine: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _token: str = Depends(require_token),
):
    rows = await store.list_sessions(machine=machine, status=status)
    return [SessionInfo(**row) for row in rows]


@router.get("/activity")
async def session_activity(
    _token: str = Depends(require_token),
):
    """Return per-session activity status based on terminal buffer recency."""
    return manager.get_activity_status()


# Must be before /{session_id} to avoid being captured by the path param
@router.get("/suggestions/projects")
async def project_suggestions(
    machine: Optional[str] = Query(None),
    _token: str = Depends(require_token),
):
    rows = await store.list_known_projects(machine=machine)
    return rows


@router.post("/create")
async def create_session_cmd(
    req: CreateSessionRequest,
    _token: str = Depends(require_token),
):
    agent = await store.get_agent_by_machine(req.machine)
    if not agent:
        raise HTTPException(status_code=404, detail="No agent found for that machine")
    payload = {"directory": req.directory, "session_name": req.session_name}
    if req.account:
        payload["account"] = req.account
    cmd_id = await store.create_command(
        agent["id"],
        "create_session",
        json.dumps(payload),
    )
    return {"ok": True, "command_id": cmd_id}


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    _token: str = Depends(require_token),
):
    row = await store.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(**row)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    _token: str = Depends(require_token),
):
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    await manager.cleanup_session(session_id)
    return {"ok": True}


@router.post("/{session_id}/restart")
async def restart_session(
    session_id: str,
    req: RestartSessionRequest | None = None,
    _token: str = Depends(require_token),
):
    row = await store.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    payload: dict = {
        "session_id": session_id,
        "directory": row.get("path", ""),
        "session_name": row.get("project", ""),
    }
    if req and req.account:
        payload["account"] = req.account
    cmd_id = await store.create_command(
        row["agent_id"],
        "restart_session",
        json.dumps(payload),
    )
    return {"ok": True, "command_id": cmd_id}


@router.post("/{session_id}/stop")
async def stop_session(
    session_id: str,
    _token: str = Depends(require_token),
):
    row = await store.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    cmd_id = await store.create_command(
        row["agent_id"],
        "stop_session",
        json.dumps({"session_id": session_id}),
    )
    return {"ok": True, "command_id": cmd_id}


@router.get("/debug/ws-state")
async def ws_debug_state(
    _token: str = Depends(require_token),
):
    """Diagnostic: show which sessions have active WebSocket connections."""
    return {
        "terminal_agents": list(manager.terminal_agents.keys()),
        "terminal_clients": {
            sid: len(clients) for sid, clients in manager.terminal_clients.items() if clients
        },
        "terminal_buffer": {
            sid: len(buf) for sid, buf in manager.terminal_buffer.items()
        },
        "relay_agents": list(manager.relay_agents.keys()),
        "relay_clients": {
            sid: len(clients) for sid, clients in manager.relay_clients.items() if clients
        },
    }


@router.post("/{session_id}/report")
async def session_report(
    session_id: str,
    report: SessionReport,
    _token: str = Depends(require_token),
):
    """Receive a callback from a session (2-way communication).

    Sessions call this endpoint to push events to the commander.
    The server stores the callback and sends a Telegram notification.
    """
    # Sessions auto-detect their identity from the tmux name (the project),
    # not the hex id, so accept either — see get_session_by_id_or_name.
    row = await store.get_session_by_id_or_name(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    project = row.get("project", session_id)
    canonical_id = row.get("id", session_id)

    # Store in DB (under the canonical hex id, not the name the caller used)
    cb_id = await store.create_callback(
        session_id=canonical_id,
        project=project,
        event_type=report.event_type,
        status=report.status,
        summary=report.summary,
        task_id=report.task_id,
    )

    # Send Telegram notification
    emoji = {"completed": "✅", "error": "❌", "in_progress": "⏳", "info": "📋"}.get(
        report.status, "📋"
    )
    tg_msg = f"{emoji} {project}: {report.summary}"
    tg_ok = await _send_telegram(tg_msg)

    return {
        "ok": True,
        "callback_id": cb_id,
        "telegram_sent": tg_ok,
    }


@router.get("/{session_id}/callbacks")
async def list_session_callbacks(
    session_id: str,
    limit: int = Query(20, ge=1, le=200),
    _token: str = Depends(require_token),
):
    """List recent callbacks from a session."""
    callbacks = await store.list_callbacks(session_id=session_id, limit=limit)
    return callbacks


async def _send_telegram(message: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            })
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


@router.get("/{session_id}/terminal-text")
async def get_terminal_text(
    session_id: str,
    _token: str = Depends(require_token),
):
    """Return recent terminal output as plain text (ANSI stripped)."""
    text = manager.get_terminal_text(session_id)
    return {"text": text}


@router.post("/{session_id}/unhide")
async def unhide_session(
    session_id: str,
    _token: str = Depends(require_token),
):
    restored = await store.unhide_session(session_id)
    if not restored:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}
