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
import re
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

    def backfill_config_dir(self, default_config_dir: str) -> int:
        """Backfill config_dir for sessions that were created before account pool.

        Returns the number of sessions updated.
        """
        updated = 0
        for sid, info in self.sessions.items():
            if not info.get("config_dir"):
                info["config_dir"] = default_config_dir
                updated += 1
        if updated:
            self.save_sessions()
            log.info("Backfilled config_dir on %d session(s) → %s", updated, default_config_dir)
        return updated

    # -----------------------------------------------------------------------
    # Tmux defaults for AgentHQ sessions
    # -----------------------------------------------------------------------

    @staticmethod
    def _apply_tmux_defaults(tmux_name: str) -> None:
        """Apply AgentHQ defaults to a tmux session: mouse, scrollback, etc."""
        for cmd in [
            # mouse ON: tmux attach holds the OUTER alternate screen, so xterm.js
            # has no native scrollback while attached — mouse off makes scrolling
            # impossible. With mouse on, xterm.js forwards the wheel to tmux and
            # the custom WheelUp/DownPane bindings below route it correctly.
            ["tmux", "set-option", "-t", tmux_name, "mouse", "on"],
            ["tmux", "set-window-option", "-t", tmux_name, "alternate-screen", "on"],
            ["tmux", "set-option", "-t", tmux_name, "history-limit", "50000"],
            # Keep pane alive when Claude exits — prevents session death cycle.
            # Dead panes are respawned by _respawn_if_dead() on next heartbeat.
            ["tmux", "set-option", "-t", tmux_name, "remain-on-exit", "on"],
        ]:
            subprocess.run(cmd, capture_output=True, timeout=5)
        TmuxBackend._apply_wheel_bindings()

    @staticmethod
    def _apply_wheel_bindings() -> None:
        """Install mode-agnostic mouse-wheel bindings (server-global root table).

        Routes the wheel by how the pane's app renders, NOT by its mouse mode:
          - inline / normal buffer (modern Claude, alternate_on=0): wheel enters
            tmux copy-mode and scrolls the pane history. The DEFAULT tmux wheel
            binding can't do this — it keys on #{mouse_any_flag}, which Claude
            sets to 1 for click support, so it forwards the wheel to Claude,
            which ignores it (this is the long-standing scroll bug).
          - full-screen TUI (older Claude, alternate_on=1) or already in
            copy-mode: `send -M` so the app / copy-mode scrolls its own view.
        Keying on #{alternate_on} instead of #{mouse_any_flag} is what makes
        scrolling work for both rendering modes and ends the mouse on/off
        flip-flop. These are -n (root-table) binds, global to the tmux server,
        so one install covers every session; re-running is idempotent.
        """
        cond = "#{||:#{pane_in_mode},#{alternate_on}}"
        for cmd in [
            ["tmux", "bind-key", "-n", "WheelUpPane",
             "if", "-F", cond, "send -M", "copy-mode -e ; send -M"],
            ["tmux", "bind-key", "-n", "WheelDownPane",
             "if", "-F", cond, "send -M", "send -M"],
        ]:
            subprocess.run(cmd, capture_output=True, timeout=5)

    @staticmethod
    def _is_pane_dead(tmux_name: str) -> bool:
        """Check if a tmux pane's process has exited (pane is dead).

        With remain-on-exit, dead panes persist but show 'Pane is dead'.
        Returns True if the pane exists but the process has exited.
        """
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", tmux_name, "-F", "#{pane_dead}"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                return False
            return result.stdout.strip() == "1"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _respawn_if_dead(self, tmux_name: str) -> bool:
        """Check if a tmux pane is dead and respawn it if so.

        With remain-on-exit, dead panes show 'Pane is dead' but the session
        stays alive. This respawns Claude Code in the same pane, preserving
        the session's CLAUDE_CONFIG_DIR for correct account credentials.
        Returns True if pane was respawned.
        """
        if not TmuxBackend._is_pane_dead(tmux_name):
            return False
        # Look up config_dir for this session so respawn uses correct account
        config_dir = ""
        for info in self.sessions.values():
            if info.get("tmux_name") == tmux_name:
                config_dir = info.get("config_dir", "")
                break
        claude_cmd = self._build_claude_cmd(config_dir)
        try:
            cmd = ["tmux", "respawn-pane", "-k", "-t", tmux_name]
            if isinstance(claude_cmd, list):
                cmd.extend(claude_cmd)
            else:
                cmd.append(claude_cmd)
            subprocess.run(cmd, capture_output=True, timeout=5)
            log.info("Respawned dead pane in '%s' (config_dir=%s)",
                     tmux_name, config_dir or "(default)")
            TmuxBackend._auto_accept_trust(tmux_name)
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    # Patterns that indicate we should NOT send Enter (OAuth/login prompts).
    _UNSAFE_ENTER_PATTERNS = re.compile(
        r"OAuth error|Select login method|Paste code here|"
        r"oauth/authorize|sign in|Invalid code|"
        r"Choose the text style|Let's get started",
        re.IGNORECASE,
    )
    # Patterns where Enter is safe (trust dialogs, MCP confirmations).
    _SAFE_ENTER_PATTERNS = re.compile(
        r"trust this folder|Is this a project|"
        r"MCP server|Enter to confirm|"
        r"Do you want to trust",
        re.IGNORECASE,
    )

    @staticmethod
    def _capture_pane(tmux_name: str, lines: int = 30) -> str:
        """Capture last N lines from a tmux pane."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_name, "-p", "-S", f"-{lines}"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    @staticmethod
    def _auto_accept_trust(tmux_name: str) -> None:
        """Send Enter to auto-accept Claude Code's trust dialogs.

        Claude Code shows trust prompts on first launch in a new directory
        and when connecting to MCP servers for the first time.
        --dangerously-skip-permissions doesn't skip these dialogs, so we
        send Enter at multiple intervals to catch them reliably.

        Before each Enter, captures the screen to avoid interacting with
        OAuth/login prompts, which would cause 'Invalid code' errors.
        """
        import threading

        def _send_enter():
            # Check at 3s, 6s, and 10s to catch workspace trust,
            # MCP server trust, and other startup prompts.
            for delay in (3, 3, 4):
                time.sleep(delay)
                text = TmuxBackend._capture_pane(tmux_name, 30)
                # Skip if screen shows OAuth/login/onboarding prompts
                if TmuxBackend._UNSAFE_ENTER_PATTERNS.search(text):
                    log.warning(
                        "Skipping auto-accept Enter for '%s' — "
                        "screen shows login/OAuth prompt",
                        tmux_name,
                    )
                    continue
                try:
                    subprocess.run(
                        ["tmux", "send-keys", "-t", tmux_name, "", "Enter"],
                        capture_output=True, timeout=5,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            log.info("Auto-accepted trust dialogs for '%s'", tmux_name)

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

    @staticmethod
    def _build_claude_cmd(config_dir: str = "") -> str | list[str]:
        """Build the claude launch command, optionally with CLAUDE_CONFIG_DIR.

        tmux new-session spawns its shell inside the tmux *server* process,
        so setting env= on the subprocess.run() call only affects the short-
        lived tmux client — NOT the session.  To inject an env var into the
        session we wrap the command in a shell with an inline export.
        """
        base = "claude --dangerously-skip-permissions"
        if not config_dir:
            return ["claude", "--dangerously-skip-permissions"]
        # Use shell form so tmux interprets it as a single command string
        return f"CLAUDE_CONFIG_DIR={config_dir} {base}"

    def create_session(self, directory: str, name: str = "", config_dir: str = "") -> dict[str, Any]:
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
                "config_dir": config_dir,
            }
            self.save_sessions()
            return {"ok": True, "session_id": sid,
                    "message": f"Adopted existing tmux session '{tmux_name}'"}
        try:
            claude_cmd = self._build_claude_cmd(config_dir)
            cmd = ["tmux", "new-session", "-d", "-s", tmux_name, "-c", directory]
            if isinstance(claude_cmd, list):
                cmd.extend(claude_cmd)
            else:
                cmd.append(claude_cmd)
            subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=10, check=True,
            )
            self._apply_tmux_defaults(tmux_name)
            self._auto_accept_trust(tmux_name)
            self.sessions[sid] = {
                "project": project,
                "path": directory,
                "tmux_name": tmux_name,
                "config_dir": config_dir,
            }
            self.save_sessions()
            acct_msg = f" (account: {config_dir})" if config_dir else ""
            return {"ok": True, "session_id": sid,
                    "message": f"tmux session '{tmux_name}' created{acct_msg}"}
        except subprocess.CalledProcessError as exc:
            return {"ok": False, "error": f"tmux error: {exc.stderr}"}

    def restart_session(
        self, session_id: str, directory: str = "", name: str = "",
        config_dir: str = "",
    ) -> dict[str, Any]:
        info = self.sessions.get(session_id)

        if info:
            tmux_name = info["tmux_name"]
            directory = info["path"]
            project = info["project"]
            # Preserve account from previous session unless overridden
            if not config_dir:
                config_dir = info.get("config_dir", "")
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
            claude_cmd = self._build_claude_cmd(config_dir)
            cmd = ["tmux", "new-session", "-d", "-s", tmux_name, "-c", directory]
            if isinstance(claude_cmd, list):
                cmd.extend(claude_cmd)
            else:
                cmd.append(claude_cmd)
            subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=10, check=True,
            )
            self._apply_tmux_defaults(tmux_name)
            self._auto_accept_trust(tmux_name)
            self.sessions[session_id] = {
                "project": project,
                "path": directory,
                "tmux_name": tmux_name,
                "config_dir": config_dir,
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

    def discover_managed_sessions(self, account_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Return list of session dicts for the heartbeat.

        *account_map*: mapping of config_dir → account name (e.g.
        ``{"/home/user/.claude": "cc", "/home/user/.claude-b": "cb"}``).
        """
        result = []
        for sid, info in self.sessions.items():
            tmux_name = info["tmux_name"]
            if self._tmux_alive(tmux_name):
                status = "dead" if self._is_pane_dead(tmux_name) else "running"
                account = ""
                if account_map:
                    account = account_map.get(info.get("config_dir", ""), "")
                result.append({
                    "id": sid,
                    "project": info["project"],
                    "status": status,
                    "pid": None,
                    "path": info["path"],
                    "last_activity": time.time(),
                    "account": account,
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
        # Always deliver via tmux buffer + bracketed paste, then a SEPARATE
        # Enter — never `send-keys content Enter` as one burst. Claude Code's
        # TUI auto-detects a fast multi-char burst as a paste and buffers it,
        # absorbing the trailing Enter; the text then gets stuck in the input
        # box, unsubmitted (observed with long single-line messages AND
        # multi-line / coalesced Telegram messages — only very short bursts
        # happened to submit, by staying under the paste-detection threshold).
        # paste-buffer -p emits proper bracketed-paste markers so the TUI
        # closes the paste, and the standalone Enter (after a short render
        # delay) submits cleanly regardless of length or newlines.
        import os
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(content)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["tmux", "load-buffer", tmp_path],
                    capture_output=True, timeout=5,
                )
                subprocess.run(
                    ["tmux", "paste-buffer", "-p", "-t", pane],
                    capture_output=True, timeout=5,
                )
                # Let the TUI render the paste before the standalone submit.
                time.sleep(0.15)
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane, "Enter"],
                    capture_output=True, timeout=5,
                )
            finally:
                os.unlink(tmp_path)
            return f"[sent to tmux:{pane}]"
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return f"tmux error: {exc}"

    # -----------------------------------------------------------------------
    # PTY terminal
    # -----------------------------------------------------------------------

    async def _pty_terminal(
        self, ws_url: str, cmd: list[str], label: str,
        http: aiohttp.ClientSession, cwd: str | None = None,
        tmux_pane: str | None = None,
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
                # Ensure TERM is set — daemonized agents (nohup, no controlling
                # tty) sometimes inherit an env without TERM, which makes
                # `tmux attach` fail immediately with "terminal does not
                # support clear" and the PTY emits no output.
                env = {**os.environ, "TERM": os.environ.get("TERM") or "xterm-256color"}
                proc = subprocess.Popen(
                    cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                    close_fds=True, start_new_session=True,
                    # After setsid, set fd 0 (the PTY slave) as the
                    # controlling terminal so SIGWINCH is delivered on
                    # resize — without this, ioctl(TIOCSWINSZ) on the
                    # master has no foreground pgrp to signal.
                    preexec_fn=lambda: fcntl.ioctl(
                        0, termios.TIOCSCTTY, 0),
                    cwd=cwd,
                    env=env,
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
                                try:
                                    os.write(master_fd, raw)
                                except OSError:
                                    break
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
                                # Force full screen redraw so new/reconnecting
                                # viewers get the complete screen state
                                if tmux_pane:
                                    try:
                                        subprocess.run(
                                            ["tmux", "refresh-client",
                                             "-t", tmux_pane],
                                            capture_output=True, timeout=5,
                                        )
                                    except (subprocess.TimeoutExpired,
                                            FileNotFoundError):
                                        pass
                                    # Re-assert mouse reporting to the (re)connecting
                                    # viewer so the wheel scrolls. tmux emits the
                                    # mouse-enable (?1002h/?1006h) only ONCE per client,
                                    # at attach. The browser's xterm.js is a late-joining
                                    # MIRROR of this persistent `tmux attach`, so it never
                                    # sees that one-time enable → xterm.js stays out of
                                    # mouse mode → it never forwards the wheel as SGR, and
                                    # web-terminal scrolling is dead even though tmux mouse
                                    # is on. refresh-client redraws content but does NOT
                                    # re-emit the mode; toggling mouse off→on does, and the
                                    # PTY relay pumps it to the browser. Resize fires on
                                    # every browser connect, so this re-arms each viewer.
                                    for state in ("off", "on"):
                                        try:
                                            subprocess.run(
                                                ["tmux", "set-option", "-t",
                                                 tmux_pane, "mouse", state],
                                                capture_output=True, timeout=5,
                                            )
                                        except (subprocess.TimeoutExpired,
                                                FileNotFoundError):
                                            pass
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

    @staticmethod
    def _prepare_pane_for_attach(pane: str) -> None:
        """Sync helper: detach stale clients and configure tmux options.

        Runs blocking subprocess calls — must be called via to_thread().
        """
        # Detach stale tmux clients left over from previous agent connections.
        try:
            result = subprocess.run(
                ["tmux", "list-clients", "-t", pane,
                 "-F", "#{client_name}"],
                capture_output=True, text=True, timeout=5,
            )
            for client_name in result.stdout.strip().splitlines():
                if client_name:
                    subprocess.run(
                        ["tmux", "detach-client", "-t", client_name],
                        capture_output=True, timeout=5,
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        # Set window-size=latest so tmux uses the most recently active client's
        # size instead of the smallest, then attach.
        for cmd in [
            ["tmux", "set-option", "-t", pane, "window-size", "latest"],
            # mouse ON + custom wheel bindings (see _apply_tmux_defaults /
            # _apply_wheel_bindings): xterm.js has no scrollback while attached
            # (tmux holds the outer alt screen), so mouse must be on for the
            # wheel to reach tmux copy-mode.
            ["tmux", "set-option", "-t", pane, "mouse", "on"],
            ["tmux", "set-window-option", "-t", pane, "alternate-screen", "on"],
            ["tmux", "set-option", "-t", pane, "history-limit", "50000"],
        ]:
            subprocess.run(cmd, capture_output=True, timeout=5)
        TmuxBackend._apply_wheel_bindings()

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
        # Prepare pane in a thread to avoid blocking the event loop
        await asyncio.to_thread(self._prepare_pane_for_attach, pane)
        await self._pty_terminal(
            ws_url, ["tmux", "attach-session", "-t", pane],
            label, http, tmux_pane=pane,
        )

    # -----------------------------------------------------------------------
    # Terminal capture helpers
    # -----------------------------------------------------------------------

    def capture_last_lines(self, session_id: str, n: int = 50) -> str:
        """Capture the last *n* lines from a session's tmux pane."""
        info = self.sessions.get(session_id)
        if not info:
            return ""
        tmux_name = info.get("tmux_name", "")
        if not self._tmux_alive(tmux_name):
            return ""
        try:
            proc = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_name, "-p",
                 "-S", f"-{n}"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                return proc.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""

    # -----------------------------------------------------------------------
    # Idle management
    # -----------------------------------------------------------------------

    def get_sessions_idle_info(self) -> dict[str, float]:
        """Return {session_id: idle_seconds} using tmux window_activity.

        Uses window_activity (last output in the window) rather than
        session_activity (last client interaction), because session_activity
        does not update from programmatic send-keys or process output —
        only from attached-client keystrokes.
        """
        now = time.time()
        result: dict[str, float] = {}
        for sid, info in self.sessions.items():
            tmux_name = info.get("tmux_name", "")
            if not self._tmux_alive(tmux_name):
                continue
            try:
                proc = subprocess.run(
                    ["tmux", "display", "-p", "-t", tmux_name,
                     "#{window_activity}"],
                    capture_output=True, text=True, timeout=3,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    epoch = int(proc.stdout.strip())
                    result[sid] = now - epoch
            except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
                pass
        return result

    def get_sessions_input_activity(self) -> dict[str, float]:
        """Return {session_id: epoch_seconds_of_last_user_input}.

        Uses tmux session_activity, which updates ONLY on attached-client
        keystrokes — NOT on programmatic send-keys (so the agent's own
        /compact and /clear injections won't falsely count as user input)
        and NOT on process output (so a busy Claude pane doesn't count).
        The agent's PTY pump is the attached client, so genuine user input
        from the browser/relay forwards through it as client keystrokes.
        """
        result: dict[str, float] = {}
        for sid, info in self.sessions.items():
            tmux_name = info.get("tmux_name", "")
            if not self._tmux_alive(tmux_name):
                continue
            try:
                proc = subprocess.run(
                    ["tmux", "display", "-p", "-t", tmux_name,
                     "#{session_activity}"],
                    capture_output=True, text=True, timeout=3,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    result[sid] = float(proc.stdout.strip())
            except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
                pass
        return result

    def archive_session(self, session_id: str, archive_path: Path) -> bool:
        """Capture full tmux scrollback and save to archive_path."""
        info = self.sessions.get(session_id)
        if not info:
            return False
        tmux_name = info.get("tmux_name", "")
        if not self._tmux_alive(tmux_name):
            return False
        try:
            proc = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_name, "-p", "-S", "-"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return False
            from datetime import datetime
            header = (
                f"=== AgentHQ Session Archive ===\n"
                f"Project: {info.get('project', '?')}\n"
                f"Path: {info.get('path', '?')}\n"
                f"Session: {session_id}\n"
                f"Archived: {datetime.utcnow().isoformat()}\n"
                f"================================\n\n"
            )
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text(header + proc.stdout, encoding="utf-8")
            log.info("Archived session %s to %s", session_id, archive_path)
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Failed to archive session %s: %s", session_id, exc)
            return False

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
