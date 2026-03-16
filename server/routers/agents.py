from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from server.auth import require_token
from server.models import AgentHeartbeat, AgentInfo
from server import store

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _agent_id(name: str, machine: str) -> str:
    """Deterministic agent id from name + machine."""
    return hashlib.sha256(f"{name}@{machine}".encode()).hexdigest()[:16]


@router.post("/heartbeat", response_model=dict)
async def heartbeat(
    payload: AgentHeartbeat,
    request: Request,
    _token: str = Depends(require_token),
):
    client_ip = request.client.host if request.client else None
    agent_id = _agent_id(payload.agent_name, payload.machine)

    await store.upsert_agent(
        agent_id=agent_id,
        name=payload.agent_name,
        machine=payload.machine,
        ip=client_ip,
    )

    reported_ids = []
    for sess in payload.sessions:
        reported_ids.append(sess.id)
        # Convert float timestamp to ISO string for storage
        if isinstance(sess.last_activity, (int, float)):
            activity_str = datetime.fromtimestamp(
                sess.last_activity, tz=timezone.utc
            ).isoformat()
        else:
            activity_str = sess.last_activity
        await store.upsert_session(
            session_id=sess.id,
            agent_id=agent_id,
            project=sess.project,
            status=sess.status,
            pid=sess.pid,
            path=sess.path,
            last_activity=activity_str,
        )

    await store.mark_missing_sessions_offline(agent_id, reported_ids)

    # Store known projects from .claude/projects/ history
    if payload.known_projects:
        projects = []
        for kp in payload.known_projects:
            if isinstance(kp.last_activity, (int, float)):
                activity_str = datetime.fromtimestamp(
                    kp.last_activity, tz=timezone.utc
                ).isoformat()
            else:
                activity_str = kp.last_activity
            projects.append({
                "id": kp.id,
                "name": kp.name,
                "path": kp.path,
                "last_activity": activity_str,
            })
        await store.upsert_known_projects(agent_id, projects)

    pending = await store.get_pending_commands(agent_id)
    for cmd in pending:
        await store.update_command(cmd["id"], "dispatched")

    return {"ok": True, "agent_id": agent_id, "commands": pending}


@router.post("/commands/{command_id}/result")
async def command_result(
    command_id: int,
    payload: dict,
    _token: str = Depends(require_token),
):
    await store.update_command(command_id, payload["status"], payload.get("result"))
    return {"ok": True}


@router.get("", response_model=list[AgentInfo])
async def list_agents(_token: str = Depends(require_token)):
    rows = await store.list_agents()
    return [AgentInfo(**row) for row in rows]
