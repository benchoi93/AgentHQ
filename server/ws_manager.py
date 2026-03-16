from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("agenthq.ws_manager")

# Keep last N terminal output messages per session so new clients see current state
_TERMINAL_BUFFER_SIZE = 200


class ConnectionManager:
    """Tracks WebSocket connections per session and handles broadcasting."""

    def __init__(self) -> None:
        # session_id -> set of WebSocket connections (frontend viewers)
        self.log_subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        # session_id -> set of WebSocket connections (relay participants)
        self.relay_clients: dict[str, set[WebSocket]] = defaultdict(set)
        # session_id -> the agent's relay WebSocket (at most one agent per session)
        self.relay_agents: dict[str, WebSocket] = {}
        # session_id -> files connections
        self.files_clients: dict[str, set[WebSocket]] = defaultdict(set)
        self.files_agents: dict[str, WebSocket] = {}
        # session_id -> terminal connections
        self.terminal_clients: dict[str, set[WebSocket]] = defaultdict(set)
        self.terminal_agents: dict[str, WebSocket] = {}
        # session_id -> recent terminal output for replay on client connect
        self.terminal_buffer: dict[str, deque[dict[str, Any]]] = {}

    # --- Session cleanup ---

    async def cleanup_session(self, session_id: str) -> None:
        """Remove all WebSocket state for a session. Call when session is
        deleted or goes permanently offline."""
        # Close and remove agent WebSockets
        for agent_dict in (self.files_agents, self.terminal_agents, self.relay_agents):
            ws = agent_dict.pop(session_id, None)
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        # Close and remove client WebSockets
        for client_dict in (self.files_clients, self.terminal_clients, self.relay_clients, self.log_subscribers):
            clients = client_dict.pop(session_id, None)
            if clients:
                for ws in clients:
                    try:
                        await ws.close()
                    except Exception:
                        pass
        # Remove terminal buffer
        self.terminal_buffer.pop(session_id, None)

    # --- Log streaming ---

    async def subscribe_logs(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.log_subscribers[session_id].add(ws)

    def unsubscribe_logs(self, session_id: str, ws: WebSocket) -> None:
        self.log_subscribers[session_id].discard(ws)
        if not self.log_subscribers[session_id]:
            del self.log_subscribers[session_id]

    async def broadcast_log(self, session_id: str, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.log_subscribers.get(session_id, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.log_subscribers[session_id].discard(ws)

    # --- Files (bidirectional file browsing) ---

    async def connect_files(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        await ws.accept()
        if is_agent:
            await self._replace_agent(self.files_agents, session_id, ws)
        else:
            self.files_clients[session_id].add(ws)

    def disconnect_files(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        if is_agent:
            # Only remove if this ws is still the registered agent
            if self.files_agents.get(session_id) is ws:
                del self.files_agents[session_id]
        else:
            self.files_clients[session_id].discard(ws)
            if not self.files_clients[session_id]:
                del self.files_clients[session_id]

    async def files_to_agent(self, session_id: str, data: dict[str, Any]) -> bool:
        agent_ws = self.files_agents.get(session_id)
        if agent_ws is None:
            return False
        try:
            await agent_ws.send_json(data)
            return True
        except Exception:
            if self.files_agents.get(session_id) is agent_ws:
                del self.files_agents[session_id]
            return False

    async def files_to_clients(self, session_id: str, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.files_clients.get(session_id, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.files_clients[session_id].discard(ws)

    # --- Terminal (agent pushes snapshots, clients subscribe) ---

    async def connect_terminal(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        await ws.accept()
        if is_agent:
            await self._replace_agent(self.terminal_agents, session_id, ws)
        else:
            # Replay buffered output so new client sees current terminal state
            for msg in self.terminal_buffer.get(session_id, []):
                try:
                    await ws.send_json(msg)
                except Exception:
                    return
            self.terminal_clients[session_id].add(ws)

    def disconnect_terminal(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        if is_agent:
            # Only remove if this ws is still the registered agent
            if self.terminal_agents.get(session_id) is ws:
                del self.terminal_agents[session_id]
                self.terminal_buffer.pop(session_id, None)
        else:
            self.terminal_clients[session_id].discard(ws)
            if not self.terminal_clients[session_id]:
                del self.terminal_clients[session_id]

    async def terminal_to_agent(self, session_id: str, data: dict[str, Any]) -> bool:
        agent_ws = self.terminal_agents.get(session_id)
        if agent_ws is None:
            return False
        try:
            await agent_ws.send_json(data)
            return True
        except Exception:
            if self.terminal_agents.get(session_id) is agent_ws:
                del self.terminal_agents[session_id]
            return False

    async def terminal_to_clients(self, session_id: str, data: dict[str, Any]) -> None:
        # Buffer output for replay to future clients
        if data.get("type") == "output":
            buf = self.terminal_buffer.get(session_id)
            if buf is None:
                buf = deque(maxlen=_TERMINAL_BUFFER_SIZE)
                self.terminal_buffer[session_id] = buf
            buf.append(data)
        dead: list[WebSocket] = []
        for ws in self.terminal_clients.get(session_id, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.terminal_clients[session_id].discard(ws)

    # --- Relay (bidirectional chat) ---

    async def connect_relay(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        await ws.accept()
        if is_agent:
            await self._replace_agent(self.relay_agents, session_id, ws)
        else:
            self.relay_clients[session_id].add(ws)

    def disconnect_relay(
        self, session_id: str, ws: WebSocket, *, is_agent: bool = False,
    ) -> None:
        if is_agent:
            # Only remove if this ws is still the registered agent
            if self.relay_agents.get(session_id) is ws:
                del self.relay_agents[session_id]
        else:
            self.relay_clients[session_id].discard(ws)
            if not self.relay_clients[session_id]:
                del self.relay_clients[session_id]

    async def relay_to_agent(self, session_id: str, data: dict[str, Any]) -> bool:
        """Forward a message from a frontend client to the session's agent."""
        agent_ws = self.relay_agents.get(session_id)
        if agent_ws is None:
            return False
        try:
            await agent_ws.send_json(data)
            return True
        except Exception:
            if self.relay_agents.get(session_id) is agent_ws:
                del self.relay_agents[session_id]
            return False

    async def relay_to_clients(self, session_id: str, data: dict[str, Any]) -> None:
        """Broadcast a message from the agent to all frontend clients."""
        dead: list[WebSocket] = []
        for ws in self.relay_clients.get(session_id, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.relay_clients[session_id].discard(ws)

    # --- Internal helpers ---

    async def _replace_agent(
        self, agent_dict: dict[str, WebSocket], session_id: str, new_ws: WebSocket,
    ) -> None:
        """Register a new agent WS, closing the old one if it exists.

        This prevents the race condition where an old agent's disconnect
        handler unregisters the new agent's WebSocket.
        """
        old_ws = agent_dict.get(session_id)
        if old_ws is not None and old_ws is not new_ws:
            log.info("Replacing stale agent WS for session %s", session_id)
            try:
                await old_ws.close()
            except Exception:
                pass
        agent_dict[session_id] = new_ws


manager = ConnectionManager()
