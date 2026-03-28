from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from server.auth import require_token
from server.models import CreateSessionRequest, SessionDetail, SessionInfo
from server import store
from server.ws_manager import manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionInfo])
async def list_sessions(
    machine: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _token: str = Depends(require_token),
):
    await store.mark_stale_agent_sessions_offline()
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
    cmd_id = await store.create_command(
        agent["id"],
        "create_session",
        json.dumps({"directory": req.directory, "session_name": req.session_name}),
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
    _token: str = Depends(require_token),
):
    row = await store.get_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    cmd_id = await store.create_command(
        row["agent_id"],
        "restart_session",
        json.dumps({
            "session_id": session_id,
            "directory": row.get("path", ""),
            "session_name": row.get("project", ""),
        }),
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


@router.post("/{session_id}/unhide")
async def unhide_session(
    session_id: str,
    _token: str = Depends(require_token),
):
    restored = await store.unhide_session(session_id)
    if not restored:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}
