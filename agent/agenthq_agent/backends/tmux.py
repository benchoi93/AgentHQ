"""Tmux-based session backend for Unix/WSL systems.

Extracted from core.py — all tmux, pty, fcntl, termios, and /proc logic
lives here. No new functionality; pure refactor.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import fcntl
import json
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path
from typing import Any

import aiohttp

from .base import SessionBackend

log = logging.getLogger("agenthq-agent")


class TmuxBackend(SessionBackend):
    """Unix session backend using tmux + PTY."""

    # Cache last known client terminal size per label so reconnections
    # start the PTY at the correct dimensions instead of defaulting to 80x24.
    _last_pty_size: dict[str, tuple[int, int]] = {}  # label -> (rows, cols)

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def load_sessions(self) -> None:
        path = self.state_dir / "managed_sessions.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for sid, info in data.items():
                if self._tmux_alive(info.get("tmux_name", "")):
                    self.sessions[sid] = info
            log.info("Restored %d managed session(s) from disk", len(self.sessions))
            self.save_sessions()  # prune dead ones from file
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning("Failed to load managed sessions: %s", exc)

    def save_sessions(self) -> None:
        path = self.state_dir / "managed_sessions.json"
        try:
            path.write_text(json.dumps(self.sessions, indent=2))
        except OSError as exc:
            log.debug("Failed to save managed sessions: %s", exc)

    # -----------------------------------------------------------------------
    # Tmux defaults for AgentHQ sessions
    # -----------------------------------------------------------------------

    @staticmethod
    def _apply_tmux_defaults(tmux_name: str) -> None:
        """Apply AgentHQ defaults to a tmux session: mouse, scrollback, etc."""
        for cmd in [
            ["tmux", "set-option", "-t", tmux_name, "mouse", "on"],
            ["tmux", "set-window-option", "-t", tmux_name, "alternate-screen", "on"],
            ["tmux", "set-option", "-t", tmux_name, "history-limit", "50000"],
            # Keep pane alive when Claude exits — prevents session death cycle.
            # Dead panes are respawned by _respawn_if_dead() on next heartbeat.
            ["tmux", "set-option", "-t", tmux_name, "remain-on-exit", "on"],
        ]:
            subprocess.run(cmd, capture_output=True, timeout=5)

    @staticmethod
    def _respawn_if_dead(tmux_name: str) -> bool:
        """Check if a tmux pane is dead and respawn it if so.

        With remain-on-exit, dead panes show 'Pane is dead' but the session
        stays alive. This respawns Claude Code in the same pane.
        Returns True if pane was respawned.
        """
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", tmux_name, "-F", "#{pane_dead}"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                return False
            is_dead = result.stdout.strip() == "1"
            if is_dead:
                subprocess.run(
                    ["tmux", "respawn-pane", "-k", "-t", tmux_name,
                     "claude", "--dangerously-skip-permissions"],
                    capture_output=True, timeout=5,
                )
                log.info("Respawned dead pane in '%s'", tmux_name)
                TmuxBackend._auto_accept_trust(tmux_name)
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    @staticmethod
    def _auto_accept_trust(tmux_name: str) -> None:
        """Send Enter to auto-accept Claude Code's 'Trust this folder?' dialog.

        Claude Code shows a trust prompt on first launch in a new directory.
        --dangerously-skip-permissions doesn't skip this dialog, so we send
        Enter after a brief delay to accept it automatically.
        """
        import threading

        def _send_enter():
            time.sleep(3)  # Wait for Claude Code to show the trust dialog
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", tmux_name, "", "Enter"],
                    capture_output=True, timeout=5,
                )
                log.info("Auto-accepted trust dialog for '%s'", tmux_name)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        threading.Thread(target=_send_enter, daemon=True).start()

    # -----------------------------------------------------------------------
    # Session lifecycle
    # -----------------------------------------------------------------------

    @staticmethod
    def _tmux_alive(tmux_name: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", tmux_name],
                capture_output=True, timeout=3,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def is_session_alive(self, session_id: str) -> bool:
        info = self.sessions.get(session_id)
        if not info:
            return False
        return self._tmux_alive(info["tmux_name"])

    def create_session(self, directory: str, name: str = "") -> dict[str, Any]:
        from ..core import _session_id

        path = Path(directory)
        if not path.is_dir():
            return {"ok": False, "error": f"Directory not found: {directory}"}

        project = name or path.name

        # Find next available session ID for this path.
        # suffix=0 keeps backward compat for the first session.
        suffix = 0
        sid = _session_id(directory, suffix=suffix)
        while sid in self.sessions and self._tmux_alive(self.sessions[sid]["tmux_name"]):
            suffix += 1
            sid = _session_id(directory, suffix=suffix)

        base_tmux = f"agenthq-{project}".replace(" ", "-").replace("/", "-")[:50]
        tmux_name = base_tmux if suffix == 0 else f"{base_tmux}-{suffix}"[:50]

        # If tmux session already exists (e.g. agent restarted), adopt it
        if self._tmux_alive(tmux_name):
            self.sessions[sid] = {
                "project": project,
                "path": directory,
                "tmux_name": tmux_name,
            }
            self.save_sessions()
            return {"ok": True, "session_id": sid,
                    "message": f"Adopted existing tmux session '{tmux_name}'"}
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_name, "-c", directory,
                 "claude", "--dangerously-skip-permissions"],
                capture_output=True, text=True, timeout=10, check=True,
            )
            self._apply_tmux_defaults(tmux_name)
            self._auto_accept_trust(tmux_name)
            self.sessions[sid] = {
                "project": project,
                "path": directory,
                "tmux_name": tmux_name,
            }
            self.save_sessions()
            return {"ok": True, "session_id": sid,
                    "message": f"tmux session '{tmux_name}' created"}
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "error": f"tmux error: {exc.stderr}"}

    def restart_session(
        self, session_id: str, directory: str = "", name: str = "",
    ) -> dict[str, Any]:
        info = self.sessions.get(session_id)

        if info:
            tmux_name = info["tmux_name"]
            directory = info["path"]
            project = info["project"]
        elif directory:
            project = name or Path(directory).name
            tmux_name = f"agenthq-{project}".replace(" ", "-").replace("/", "-")[:50]
        else:
            return {"ok": False,
                    "error": f"Session {session_id} not found and no directory provided"}

        # Kill the existing tmux session if still alive
        if self._tmux_alive(tmux_name):
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", tmux_name],
                    capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_name, "-c", directory,
                 "claude", "--dangerously-skip-permissions"],
                capture_output=True, text=True, timeout=10, check=True,
            )
            self._apply_tmux_defaults(tmux_name)
            self._auto_accept_trust(tmux_name)
            self.sessions[session_id] = {
                "project": project,
                "path": directory,
                "tmux_name": tmux_name,
            }
            self.save_sessions()
            self.sessions_needing_restart.add(session_id)
            return {"ok": True, "session_id": session_id,
                    "message": f"Restarted tmux session '{tmux_name}'"}
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "error": f"tmux error: {exc.stderr}"}

    def stop_session(self, session_id: str) -> dict[str, Any]:
        info = self.sessions.get(session_id)
        if not info:
            return {"ok": False,
                    "error": f"Session {session_id} not found in managed sessions"}

        tmux_name = info["tmux_name"]
        if self._tmux_alive(tmux_name):
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", tmux_name],
                    capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        del self.sessions[session_id]
        self.save_sessions()
        self.sessions_needing_stop.add(session_id)
        return {"ok": True, "session_id": session_id,
                "message": f"Stopped tmux session '{tmux_name}'"}

    # -----------------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------------

    def discover_managed_sessions(self) -> list[dict[str, Any]]:
        result = []
        for sid, info in self.sessions.items():
            if self._tmux_alive(info["tmux_name"]):
                result.append({
                    "id": sid,
                    "project": info["project"],
                    "status": "running",
                    "pid": None,
                    "path": info["path"],
                    "last_activity": time.time(),
                })
        return result

    # -----------------------------------------------------------------------
    # Pane / send-keys
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_ancestor_pids(pid: int) -> set[int]:
        """Walk the process tree upward via /proc."""
        pids: set[int] = set()
        try:
            cur = pid
            while cur and cur > 1:
                pids.add(cur)
                stat = Path(f"/proc/{cur}/stat").read_text()
                ppid = int(stat.split(") ", 1)[1].split()[1])
                if ppid <= 1:
                    break
                cur = ppid
        except (OSError, ValueError, IndexError):
            pass
        return pids

    def find_pane(self, session: dict[str, Any]) -> str | None:
        sid = session.get("id", "")
        managed = self.sessions.get(sid)
        if managed and self._tmux_alive(managed["tmux_name"]):
            return managed["tmux_name"]
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F",
                 "#{pane_id} #{pane_pid} #{pane_current_path}"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                return None
            pid = session.get("pid")
            path = session.get("path", "")
            ancestors = self._get_ancestor_pids(pid) if pid else set()
            for line in result.stdout.strip().splitlines():
                parts = line.split(None, 2)
                if len(parts) < 3:
                    continue
                pane_id, pane_pid, pane_path = parts
                pane_pid_int = int(pane_pid)
                if pane_pid_int in ancestors:
                    return pane_id
                if path and pane_path and os.path.realpath(path) == os.path.realpath(pane_path):
                    return pane_id
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def ensure_pane(self, session: dict[str, Any]) -> str | None:
        pane = self.find_pane(session)
        if pane:
            return pane
        path = session.get("path", "")
        if not path or not Path(path).is_dir():
            return None
        sid = session.get("id", "")
        project = session.get("project", Path(path).name)
        tmux_name = f"agenthq-{project}".replace(" ", "-").replace("/", "-")[:50]
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_name, "-c", path],
                capture_output=True, text=True, timeout=10, check=True,
            )
            self.sessions[sid] = {
                "project": project,
                "path": path,
                "tmux_name": tmux_name,
            }
            self.save_sessions()
            log.info("Auto-created tmux session '%s' for %s", tmux_name, sid)
            return tmux_name
        except subprocess.CalledProcessError as exc:
            log.warning("Failed to auto-create tmux session for %s: %s",
                        sid, exc.stderr)
            return None

    def send_keys(self, session: dict[str, Any], content: str) -> str | None:
        pane = self.ensure_pane(session)
        if not pane:
            return None
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", pane, content, "Enter"],
                capture_output=True, timeout=5,
            )
            return f"[sent to tmux:{pane}]"
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return f"tmux error: {exc}"

    # -----------------------------------------------------------------------
    # PTY terminal
    # -----------------------------------------------------------------------

    async def _pty_terminal(
        self, ws_url: str, cmd: list[str], label: str,
        http: aiohttp.ClientSession, cwd: str | None = None,
    ) -> None:
        """Generic PTY-backed interactive terminal over WebSocket.

        Waits for the first resize message from a client before spawning the
        process, so the PTY starts with the correct terminal dimensions.
        """
        proc: subprocess.Popen | None = None
        master_fd: int | None = None
        fd_closed = False

        def _cleanup_proc() -> None:
            nonlocal fd_closed
            if master_fd is not None and not fd_closed:
                fd_closed = True
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                    proc.wait(timeout=3)

        try:
            async with http.ws_connect(ws_url, heartbeat=20) as ws:
                # Start PTY immediately — use cached size from previous connection
                # if available, otherwise default to 80x24.  Clients can still
                # send resize after connecting; the ws_reader handles it.
                init_rows, init_cols = self._last_pty_size.get(label, (24, 80))
                cur_rows, cur_cols = init_rows, init_cols
                log.info("PTY connecting: %s", label)

                master_fd, slave_fd = pty.openpty()
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                            struct.pack("HHHH", init_rows, init_cols, 0, 0))
                proc = subprocess.Popen(
                    cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                    close_fds=True, preexec_fn=os.setsid, cwd=cwd,
                )
                os.close(slave_fd)
                log.info("PTY started: %s (%dx%d)", label, init_cols, init_rows)

                def _pty_read_coalesced(fd: int, max_bytes: int = 16384,
                                       linger_s: float = 0.008) -> bytes:
                    """Read from PTY, batching data that arrives within linger_s."""
                    buf = bytearray()
                    while len(buf) < max_bytes:
                        if buf:
                            # Already have data — wait briefly for more
                            ready, _, _ = select.select([fd], [], [], linger_s)
                            if not ready:
                                break
                        chunk = os.read(fd, max_bytes - len(buf))
                        if not chunk:
                            break
                        buf.extend(chunk)
                    return bytes(buf)

                async def pty_reader() -> None:
                    loop = asyncio.get_event_loop()
                    while True:
                        try:
                            data = await loop.run_in_executor(
                                None, _pty_read_coalesced, master_fd,
                            )
                        except OSError:
                            break
                        if not data:
                            break
                        await ws.send_json({
                            "type": "output",
                            "data": base64.b64encode(data).decode("ascii"),
                        })

                async def ws_reader() -> None:
                    nonlocal cur_rows, cur_cols
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except (json.JSONDecodeError, ValueError):
                                continue
                            msg_type = data.get("type", "")
                            if msg_type == "input":
                                raw = base64.b64decode(data["data"])
                                os.write(master_fd, raw)
                            elif msg_type == "resize":
                                cols = data.get("cols", 80)
                                rows = data.get("rows", 24)
                                if rows != cur_rows or cols != cur_cols:
                                    log.info("PTY resize: %s %dx%d -> %dx%d",
                                             label, cur_cols, cur_rows, cols, rows)
                                    fcntl.ioctl(
                                        master_fd, termios.TIOCSWINSZ,
                                        struct.pack("HHHH", rows, cols, 0, 0),
                                    )
                                    cur_rows, cur_cols = rows, cols
                                    self._last_pty_size[label] = (rows, cols)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.ERROR):
                            break

                await asyncio.gather(pty_reader(), ws_reader())
        except asyncio.CancelledError:
            log.debug("PTY task cancelled: %s", label)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("PTY error (%s): %s", label, exc)
        finally:
            _cleanup_proc()

    async def attach_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession, label: str,
    ) -> None:
        sid = session["id"]
        pane = self.ensure_pane(session)
        if not pane:
            log.debug("No tmux pane for session %s, skipping terminal", sid)
            return
        # Respawn Claude if the pane is dead (remain-on-exit keeps pane alive)
        self._respawn_if_dead(pane)
        # Set window-size=latest so tmux uses the most recently active client's
        # size instead of the smallest, then attach.
        subprocess.run(
            ["tmux", "set-option", "-t", pane, "window-size", "latest"],
            capture_output=True, timeout=5,
        )
        # Enable mouse mode so scroll wheel events are forwarded to tmux
        subprocess.run(
            ["tmux", "set-option", "-t", pane, "mouse", "on"],
            capture_output=True, timeout=5,
        )
        # Keep alternate-screen on (default) so full-screen apps (Claude Code,
        # brew progress) render correctly with cursor positioning. Scrollback
        # is handled by tmux's own history buffer (mouse scroll enters copy mode).
        subprocess.run(
            ["tmux", "set-window-option", "-t", pane, "alternate-screen", "on"],
            capture_output=True, timeout=5,
        )
        # Large scrollback for tmux copy-mode history
        subprocess.run(
            ["tmux", "set-option", "-t", pane, "history-limit", "50000"],
            capture_output=True, timeout=5,
        )
        await self._pty_terminal(
            ws_url, ["tmux", "attach-session", "-t", pane],
            label, http,
        )

    async def attach_claude_terminal(
        self, ws_url: str, session: dict[str, Any],
        http: aiohttp.ClientSession,
    ) -> None:
        sid = session["id"]
        path = session.get("path", "")
        if not path or not Path(path).is_dir():
            return
        await self._pty_terminal(
            ws_url, ["claude"], f"claude-terminal:{sid}", http, cwd=path,
        )
