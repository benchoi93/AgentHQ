"""Abstract base class for platform-specific session backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import aiohttp


class SessionBackend(ABC):
    """Interface for platform-specific session management.

    Implementations handle:
      - Creating/stopping/restarting sessions (tmux, ConPTY, etc.)
      - Session liveness checks
      - Session discovery
      - Terminal I/O (PTY attachment)
      - Chat relay (send-keys)
      - Managed session persistence
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.sessions: dict[str, dict[str, Any]] = {}
        self.sessions_needing_restart: set[str] = set()
        self.sessions_needing_stop: set[str] = set()

    @abstractmethod
    def create_session(self, directory: str, name: str = "") -> dict[str, Any]:
        """Spawn a Claude Code session. Returns {"ok": bool, ...}."""

    @abstractmethod
    def stop_session(self, session_id: str) -> dict[str, Any]:
        """Kill a managed session and remove it. Returns {"ok": bool, ...}."""

    @abstractmethod
    def restart_session(
        self, session_id: str, directory: str = "", name: str = "",
    ) -> dict[str, Any]:
        """Kill and relaunch a managed session. Returns {"ok": bool, ...}."""

    @abstractmethod
    def is_session_alive(self, session_id: str) -> bool:
        """Check whether a managed session is still running."""

    @abstractmethod
    def discover_managed_sessions(self) -> list[dict[str, Any]]:
        """Return list of running managed sessions as heartbeat dicts."""

    @abstractmethod
    def send_keys(self, session: dict[str, Any], content: str) -> str | None:
        """Send input to a session. Returns status message or None on failure."""

    @abstractmethod
    def find_pane(self, session: dict[str, Any]) -> str | None:
        """Find the terminal pane/handle for a session. Returns identifier or None."""

    @abstractmethod
    def ensure_pane(self, session: dict[str, Any]) -> str | None:
        """Find or auto-create a terminal pane for a session."""

    @abstractmethod
    async def attach_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession, label: str,
    ) -> None:
        """Attach an interactive shell terminal over WebSocket."""

    @abstractmethod
    async def attach_claude_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession,
    ) -> None:
        """Attach an interactive Claude Code terminal over WebSocket."""

    @abstractmethod
    def load_sessions(self) -> None:
        """Load managed sessions from persistent storage."""

    @abstractmethod
    def save_sessions(self) -> None:
        """Persist managed sessions to disk."""
