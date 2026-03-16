"""Windows session backend using ConPTY (via pywinpty) and native process management.

Requires Windows 10 1809+ and the `pywinpty` package.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import aiohttp

from .base import SessionBackend

log = logging.getLogger("agenthq-agent")

# Lazy-imported so the module can be parsed on Unix (for type checking)
_winpty = None
_winreg = None


def _ensure_winpty():
    global _winpty
    if _winpty is None:
        import winpty as _winpty  # noqa: N811
    return _winpty


def _ensure_winreg():
    global _winreg
    if _winreg is None:
        import winreg as _winreg  # noqa: N811
    return _winreg


def _find_claude_exe() -> str:
    """Locate the claude CLI executable on Windows."""
    # Check PATH first
    for p in os.environ.get("PATH", "").split(os.pathsep):
        for name in ("claude.exe", "claude.cmd", "claude.bat"):
            candidate = Path(p) / name
            if candidate.is_file():
                return str(candidate)
    # Check common locations
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        for subdir in ("Programs", "Microsoft", ""):
            for name in ("claude.exe", "claude.cmd"):
                candidate = Path(appdata) / subdir / "claude" / name
                if candidate.is_file():
                    return str(candidate)
    # npm global
    npm_prefix = Path(os.environ.get("APPDATA", "")) / "npm"
    for name in ("claude.cmd", "claude"):
        candidate = npm_prefix / name
        if candidate.is_file():
            return str(candidate)
    return "claude"  # fallback, hope it's on PATH


def _to_win_path(p: str) -> str:
    """Convert /mnt/X/... WSL path to X:/... Windows path."""
    import re
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", p)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\")
        return f"{drive}:{rest}"
    return p


class WindowsBackend(SessionBackend):
    """Windows session backend using ConPTY (pywinpty) and native processes."""

    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self._procs: dict[str, subprocess.Popen] = {}  # sid -> Popen
        self._pty_procs: dict[str, Any] = {}  # sid -> winpty.PtyProcess

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def load_sessions(self) -> None:
        path = self.state_dir / "managed_sessions.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for sid, info in data.items():
                self.sessions[sid] = info
            log.info("Restored %d managed session(s) from disk", len(self.sessions))
            self.save_sessions()
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning("Failed to load managed sessions: %s", exc)

    def save_sessions(self) -> None:
        path = self.state_dir / "managed_sessions.json"
        try:
            path.write_text(json.dumps(self.sessions, indent=2), encoding="utf-8")
        except OSError as exc:
            log.debug("Failed to save managed sessions: %s", exc)

    # -----------------------------------------------------------------------
    # Process helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check whether a process is still running using kernel32."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        except (OSError, AttributeError):
            return False

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        """Kill a process and all its children on Windows."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # -----------------------------------------------------------------------
    # Session lifecycle
    # -----------------------------------------------------------------------

    def is_session_alive(self, session_id: str) -> bool:
        return session_id in self.sessions

    def create_session(self, directory: str, name: str = "") -> dict[str, Any]:
        from ..core import _session_id

        directory = _to_win_path(directory)
        path = Path(directory)
        if not path.is_dir():
            return {"ok": False, "error": f"Directory not found: {directory}"}

        project = name or path.name
        sid = _session_id(directory)

        if sid in self.sessions:
            return {"ok": True, "session_id": sid, "message": "Session already registered"}

        self.sessions[sid] = {
            "project": project,
            "path": directory,
        }
        self.save_sessions()
        return {"ok": True, "session_id": sid,
                "message": f"Registered session {project}"}

    def restart_session(
        self, session_id: str, directory: str = "", name: str = "",
    ) -> dict[str, Any]:
        info = self.sessions.get(session_id)

        if info:
            directory = info["path"]
            name = name or info["project"]
            # Kill existing process
            pid = info.get("pid")
            if pid and self._pid_alive(pid):
                self._kill_process_tree(pid)
        elif not directory:
            return {"ok": False,
                    "error": f"Session {session_id} not found and no directory provided"}

        # Clean up old proc reference
        old_proc = self._procs.pop(session_id, None)
        if old_proc:
            try:
                old_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # Remove from sessions before re-creating
        self.sessions.pop(session_id, None)

        result = self.create_session(directory, name)
        if result.get("ok"):
            # Re-key under the original session_id (create_session generates its own)
            new_sid = result.get("session_id", session_id)
            if new_sid != session_id and new_sid in self.sessions:
                self.sessions[session_id] = self.sessions.pop(new_sid)
                if new_sid in self._procs:
                    self._procs[session_id] = self._procs.pop(new_sid)
            self.sessions_needing_restart.add(session_id)
            result["session_id"] = session_id
            result["message"] = f"Restarted session {session_id}"
        return result

    def stop_session(self, session_id: str) -> dict[str, Any]:
        info = self.sessions.get(session_id)
        if not info:
            return {"ok": False,
                    "error": f"Session {session_id} not found in managed sessions"}

        pid = info.get("pid")
        if pid and self._pid_alive(pid):
            self._kill_process_tree(pid)

        self._procs.pop(session_id, None)
        del self.sessions[session_id]
        self.save_sessions()
        self.sessions_needing_stop.add(session_id)
        return {"ok": True, "session_id": session_id,
                "message": f"Stopped session (PID {pid})"}

    # -----------------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------------

    def discover_managed_sessions(self) -> list[dict[str, Any]]:
        result = []
        for sid, info in list(self.sessions.items()):
            # Check if a ConPTY process is active for this session
            pty = self._pty_procs.get(sid)
            has_pty = pty is not None and (hasattr(pty, 'isalive') and pty.isalive())
            result.append({
                "id": sid,
                "project": info["project"],
                "status": "running" if has_pty else "idle",
                "pid": info.get("pid"),
                "path": info["path"],
                "last_activity": time.time(),
            })
        return result

    # -----------------------------------------------------------------------
    # Pane / send-keys
    # -----------------------------------------------------------------------

    def find_pane(self, session: dict[str, Any]) -> str | None:
        sid = session.get("id", "")
        if sid in self._procs and self._procs[sid].poll() is None:
            return sid  # use session_id as pane handle on Windows
        info = self.sessions.get(sid)
        if info and info.get("pid") and self._pid_alive(info["pid"]):
            return sid
        return None

    def ensure_pane(self, session: dict[str, Any]) -> str | None:
        pane = self.find_pane(session)
        if pane:
            return pane
        # Auto-create by launching a new process
        path = session.get("path", "")
        if not path or not Path(path).is_dir():
            return None
        sid = session.get("id", "")
        project = session.get("project", Path(path).name)
        claude_exe = _find_claude_exe()
        try:
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            proc = subprocess.Popen(
                [claude_exe],
                cwd=path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            self._procs[sid] = proc
            self.sessions[sid] = {
                "project": project,
                "path": path,
                "pid": proc.pid,
            }
            self.save_sessions()
            log.info("Auto-created process (PID %d) for %s", proc.pid, sid)
            return sid
        except (FileNotFoundError, OSError) as exc:
            log.warning("Failed to auto-create process for %s: %s", sid, exc)
            return None

    def send_keys(self, session: dict[str, Any], content: str) -> str | None:
        sid = session.get("id", "")
        proc = self._procs.get(sid)
        if proc and proc.poll() is None and proc.stdin:
            try:
                proc.stdin.write((content + "\n").encode("utf-8"))
                proc.stdin.flush()
                return f"[sent to process PID {proc.pid}]"
            except (BrokenPipeError, OSError) as exc:
                return f"stdin write error: {exc}"
        return None

    # -----------------------------------------------------------------------
    # ConPTY terminal
    # -----------------------------------------------------------------------

    async def _conpty_terminal(
        self, ws_url: str, cmd: str, label: str,
        http: aiohttp.ClientSession, cwd: str | None = None,
    ) -> None:
        """ConPTY-backed interactive terminal over WebSocket using pywinpty."""
        winpty = _ensure_winpty()
        pty_proc = None

        def _cleanup() -> None:
            if pty_proc is not None:
                try:
                    if pty_proc.isalive():
                        pty_proc.terminate(force=True)
                except (OSError, AttributeError):
                    pass

        try:
            async with http.ws_connect(ws_url) as ws:
                log.info("ConPTY waiting for resize: %s", label)

                init_rows, init_cols = 24, 80
                try:
                    first_msg = await asyncio.wait_for(ws.receive(), timeout=30)
                    if first_msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(first_msg.data)
                        if data.get("type") == "resize":
                            init_cols = data.get("cols", 80)
                            init_rows = data.get("rows", 24)
                except (asyncio.TimeoutError, Exception):
                    pass

                env = os.environ.copy()
                env.pop("CLAUDECODE", None)
                pty_proc = winpty.PtyProcess.spawn(
                    cmd,
                    cwd=cwd,
                    dimensions=(init_rows, init_cols),
                    env=env,
                )
                log.info("ConPTY started: %s (%dx%d)", label, init_cols, init_rows)

                async def pty_reader() -> None:
                    loop = asyncio.get_event_loop()
                    while pty_proc.isalive():
                        try:
                            data = await loop.run_in_executor(
                                None, pty_proc.read, 4096,
                            )
                        except EOFError:
                            break
                        except Exception:
                            break
                        if not data:
                            break
                        raw = data.encode("utf-8") if isinstance(data, str) else data
                        await ws.send_json({
                            "type": "output",
                            "data": base64.b64encode(raw).decode("ascii"),
                        })

                async def ws_reader() -> None:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except (json.JSONDecodeError, ValueError):
                                continue
                            msg_type = data.get("type", "")
                            if msg_type == "input":
                                raw = base64.b64decode(data["data"])
                                text = raw.decode("utf-8", errors="replace")
                                pty_proc.write(text)
                            elif msg_type == "resize":
                                cols = data.get("cols", 80)
                                rows = data.get("rows", 24)
                                try:
                                    pty_proc.setwinsize(rows, cols)
                                except (OSError, AttributeError):
                                    pass
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.ERROR):
                            break

                await asyncio.gather(pty_reader(), ws_reader())
        except asyncio.CancelledError:
            log.debug("ConPTY task cancelled: %s", label)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("ConPTY error (%s): %s", label, exc)
        except ImportError:
            log.error(
                "ConPTY terminal requires pywinpty. Install with: pip install pywinpty"
            )
        finally:
            _cleanup()

    async def attach_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession, label: str,
    ) -> None:
        path = _to_win_path(session.get("path", ""))
        if not path or not Path(path).is_dir():
            return
        claude_exe = _find_claude_exe()
        cmd = f"{claude_exe} --dangerously-skip-permissions"
        await self._conpty_terminal(ws_url, cmd, label, http, cwd=path)

    async def attach_claude_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession,
    ) -> None:
        sid = session["id"]
        path = _to_win_path(session.get("path", ""))
        if not path or not Path(path).is_dir():
            return
        claude_exe = _find_claude_exe()
        await self._conpty_terminal(
            ws_url, claude_exe, f"claude-terminal:{sid}", http, cwd=path,
        )
