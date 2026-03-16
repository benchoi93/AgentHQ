"""Platform backend selection for AgentHQ agent."""
from __future__ import annotations

import platform
from pathlib import Path

from .base import SessionBackend


def get_backend(state_dir: Path) -> SessionBackend:
    """Return the appropriate session backend for the current platform."""
    if platform.system() == "Windows":
        from .windows import WindowsBackend
        return WindowsBackend(state_dir)
    else:
        from .tmux import TmuxBackend
        return TmuxBackend(state_dir)


__all__ = ["SessionBackend", "get_backend"]
