from __future__ import annotations

import asyncio
import json
from datetime import datetime

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from server.config import AGENTHQ_TOKEN
from server.ws_manager import manager

log = logging.getLogger("agenthq.ws")

router = APIRouter(tags=["websocket"])

# Timeout for idle WebSocket connections (seconds)
_WS_TIMEOUT_CLIENT = 300   # 5 min for browser clients
_WS_TIMEOUT_AGENT = 3600   # 1 hour for agents (event-driven, can be idle)


def _validate_token(token: str) -> bool:
    if not AGENTHQ_TOKEN:
        return False
    return token == AGENTHQ_TOKEN


async def _receive_text(ws: WebSocket, timeout: float = _WS_TIMEOUT_CLIENT) -> str:
    """Receive text with timeout to detect dead connections."""
    return await asyncio.wait_for(ws.receive_text(), timeout=timeout)


def _safe_json(raw: str) -> dict | None:
    """Parse JSON, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


# --- Log streaming ---

@router.websocket("/ws/logs/{session_id}")
async def ws_logs(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    role: str = Query("viewer"),
):
    if not _validate_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if role == "agent":
        await websocket.accept()
        try:
            while True:
                raw = await _receive_text(websocket, timeout=_WS_TIMEOUT_AGENT)
                data = _safe_json(raw)
                if data is None:
                    continue
                data.setdefault("type", "log")
                data.setdefault("session_id", session_id)
                data.setdefault("timestamp", datetime.utcnow().isoformat())
                await manager.broadcast_log(session_id, data)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
    else:
        await manager.subscribe_logs(session_id, websocket)
        try:
            while True:
                await _receive_text(websocket)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            manager.unsubscribe_logs(session_id, websocket)


# --- Relay (bidirectional chat) ---

@router.websocket("/ws/relay/{session_id}")
async def ws_relay(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    role: str = Query("client"),
):
    if not _validate_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    is_agent = role == "agent"
    timeout = _WS_TIMEOUT_AGENT if is_agent else _WS_TIMEOUT_CLIENT
    await manager.connect_relay(session_id, websocket, is_agent=is_agent)

    try:
        while True:
            raw = await _receive_text(websocket, timeout=timeout)
            data = _safe_json(raw)
            if data is None:
                continue
            data.setdefault("session_id", session_id)
            data.setdefault("timestamp", datetime.utcnow().isoformat())

            if is_agent:
                data.setdefault("type", "output")
                await manager.relay_to_clients(session_id, data)
            else:
                data.setdefault("type", "input")
                await manager.relay_to_agent(session_id, data)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        manager.disconnect_relay(session_id, websocket, is_agent=is_agent)


# --- Terminal ---

@router.websocket("/ws/terminal/{session_id}")
async def ws_terminal(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    role: str = Query("client"),
):
    if not _validate_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    is_agent = role == "agent"
    timeout = _WS_TIMEOUT_AGENT if is_agent else _WS_TIMEOUT_CLIENT
    log.info("Terminal WS connect: session=%s role=%s", session_id, role)
    await manager.connect_terminal(session_id, websocket, is_agent=is_agent)

    try:
        while True:
            raw = await _receive_text(websocket, timeout=timeout)
            data = _safe_json(raw)
            if data is None:
                continue
            data.setdefault("session_id", session_id)
            data.setdefault("timestamp", datetime.utcnow().isoformat())

            if is_agent:
                await manager.terminal_to_clients(session_id, data)
            else:
                await manager.terminal_to_agent(session_id, data)
    except WebSocketDisconnect as exc:
        log.info("Terminal WS disconnect: session=%s role=%s code=%s", session_id, role, exc.code)
    except asyncio.TimeoutError:
        log.info("Terminal WS timeout: session=%s role=%s", session_id, role)
    finally:
        manager.disconnect_terminal(session_id, websocket, is_agent=is_agent)


# --- File browsing ---

@router.websocket("/ws/files/{session_id}")
async def ws_files(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    role: str = Query("client"),
):
    if not _validate_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    is_agent = role == "agent"
    timeout = _WS_TIMEOUT_AGENT if is_agent else _WS_TIMEOUT_CLIENT
    await manager.connect_files(session_id, websocket, is_agent=is_agent)

    try:
        while True:
            raw = await _receive_text(websocket, timeout=timeout)
            data = _safe_json(raw)
            if data is None:
                continue
            data.setdefault("session_id", session_id)

            if is_agent:
                await manager.files_to_clients(session_id, data)
            else:
                await manager.files_to_agent(session_id, data)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        manager.disconnect_files(session_id, websocket, is_agent=is_agent)
