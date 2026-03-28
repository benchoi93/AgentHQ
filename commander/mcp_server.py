"""MCP server providing AgentHQ and Telegram tools for the 대장 commander session.

Run as a subprocess by Claude Code via .mcp.json (stdio transport).
Config via environment variables: AGENTHQ_URL, AGENTHQ_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

import asyncio
import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENTHQ_URL = os.environ.get("AGENTHQ_URL", "http://localhost:8420")
AGENTHQ_TOKEN = os.environ.get("AGENTHQ_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE_FILE = Path(
    os.environ.get(
        "COMMANDER_STATE_FILE",
        str(Path(__file__).parent / "commander_state.json"),
    )
)

mcp = FastMCP("commander")

# ---------------------------------------------------------------------------
# Deny-list for command guardrails
# ---------------------------------------------------------------------------

_DENY_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brm\s+-[^\s]*r[^\s]*\s",   # rm -rf / rm -r
        r"\brm\s+-[^\s]*f[^\s]*\s",   # rm -f (recursive forms)
        r"git\s+push\s+.*--force",     # git push --force
        r"git\s+push\s+.*-f\b",        # git push -f
        r"\bdrop\s+table\b",           # DROP TABLE
        r"\btruncate\s+table\b",       # TRUNCATE TABLE
        r"\bdrop\s+database\b",        # DROP DATABASE
        r"git\s+reset\s+--hard",       # git reset --hard
        r"git\s+clean\s+-[^\s]*f",     # git clean -f / -fd
        r"chmod\s+-[^\s]*R.*777",      # chmod -R 777
        r"mkfs\b",                      # mkfs (format disk)
        r"dd\s+if=.*of=/dev/",          # dd to device
    ]
]


def _check_deny_list(command: str) -> str | None:
    """Return a human-readable reason if the command is blocked, else None."""
    # Ensure the pattern for rm -rf matches even without trailing space
    rm_rf = re.compile(r"\brm\s+-[^\s]*r[^\s]*", re.IGNORECASE)
    if rm_rf.search(command):
        return "blocked: rm with recursive flag is not allowed"
    for pat in _DENY_PATTERNS:
        if pat.search(command):
            return f"blocked: matches guardrail pattern '{pat.pattern}'"
    return None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(
    r"""
      \x1b          # ESC
      (?:
        \[ [0-9;?]* [A-Za-z]    # CSI sequences  (e.g. \x1b[0m, \x1b[?2004h)
      | \] .*? (?:\x07|\x1b\\)  # OSC sequences  (e.g. \x1b]0;title\x07)
      | [()][0-9A-Za-z]         # charset select (e.g. \x1b(B)
      | [>=<]                   # keypad modes
      | \x1b                    # bare ESC-ESC (reset)
      )
    | \r                        # carriage return (PTY line endings)
    """,
    re.VERBOSE,
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and carriage returns from PTY output."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Persistent state helpers
# ---------------------------------------------------------------------------

_MAX_ROUTING_HISTORY = 50
_MAX_AUDIT_LOG = 200


def _load_state_file() -> dict:
    """Load state from disk; return empty scaffold on first run or corruption."""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "active_tasks": {},
        "routing_history": [],
        "user_preferences": {},
        "last_known_sessions": [],
        "audit_log": [],
    }


def _save_state_file(state: dict) -> None:
    """Atomically write state to disk."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_FILE)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENTHQ_TOKEN}"}


def _ws_url(path: str) -> str:
    """Convert REST base URL to WebSocket URL and append path with token."""
    base = AGENTHQ_URL.rstrip("/").replace("http", "ws", 1)
    sep = "&" if "?" in path else "?"
    return f"{base}{path}{sep}token={AGENTHQ_TOKEN}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def save_state(key: str, value: str) -> str:
    """Persist a value in commander_state.json under the given key.

    Use dot-notation for nested keys (e.g. "user_preferences.autonomy").
    Value is stored as a JSON-parsed object if valid JSON, otherwise as a string.

    Special managed keys:
      - "active_tasks"         : dict of {task_id → task record}
      - "routing_history"      : auto-trimmed to last 50 entries when a list is appended
      - "user_preferences"     : arbitrary preferences dict
      - "last_known_sessions"  : list of session IDs from last heartbeat

    Args:
        key: Dot-separated key path (e.g. "active_tasks.abc123").
        value: JSON string or plain string to store.
    """
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = value

    state = _load_state_file()

    parts = key.split(".", 1)
    if len(parts) == 2:
        top, sub = parts
        if top not in state or not isinstance(state[top], dict):
            state[top] = {}
        state[top][sub] = parsed
    else:
        state[key] = parsed

    # Trim routing_history to last _MAX_ROUTING_HISTORY entries
    if isinstance(state.get("routing_history"), list):
        state["routing_history"] = state["routing_history"][-_MAX_ROUTING_HISTORY:]

    _save_state_file(state)
    return f"Saved: {key}"


@mcp.tool()
async def load_state(key: str = "") -> str:
    """Load a value from commander_state.json.

    Args:
        key: Dot-separated key path (e.g. "active_tasks.abc123").
             Pass an empty string (or omit) to return the entire state.
    """
    state = _load_state_file()

    if not key:
        return json.dumps(state, indent=2, default=str)

    parts = key.split(".", 1)
    if len(parts) == 2:
        top, sub = parts
        result = state.get(top, {}).get(sub)
    else:
        result = state.get(key)

    if result is None:
        return f"(no value for key '{key}')"
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def list_sessions() -> str:
    """List all AgentHQ sessions with project, status, machine, and session ID."""
    async with aiohttp.ClientSession() as http:
        async with http.get(
            f"{AGENTHQ_URL}/api/sessions", headers=_auth_headers()
        ) as resp:
            if resp.status != 200:
                return f"Error: HTTP {resp.status} — {await resp.text()}"
            sessions = await resp.json()

    if not sessions:
        return "No sessions found."

    lines = []
    for s in sessions:
        status = s.get("status", "?")
        marker = "💀" if status == "dead" else "•"
        line = (
            f"{marker} {s.get('project', '?')} | {status} | "
            f"machine={s.get('machine', '?')} | path={s.get('path', '?')} | id={s['id']}"
        )
        if status == "dead":
            line += "  ← use restart_session or stop_session"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
async def get_session_output(session_id: str, lines: int = 50) -> str:
    """Read recent terminal output from a session.

    Connects to the terminal WebSocket, collects buffered replay data
    (server sends up to ~200 messages on connect), decodes base64 PTY data,
    and returns the last N lines with ANSI codes stripped.

    Args:
        session_id: Target session ID.
        lines: Number of trailing lines to return (default 50).
    """
    url = _ws_url(f"/ws/terminal/{session_id}?role=client")
    chunks: list[bytes] = []

    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url) as ws:
                # Collect buffered messages for up to 3 seconds.
                async def _collect():
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("type") == "output" and "data" in data:
                                chunks.append(base64.b64decode(data["data"]))
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                try:
                    await asyncio.wait_for(_collect(), timeout=3)
                except asyncio.TimeoutError:
                    pass  # expected — collection window elapsed
    except Exception as exc:
        return f"Error connecting to terminal WS: {exc}"

    if not chunks:
        return "(no output captured)"

    raw_text = b"".join(chunks).decode("utf-8", errors="replace")
    clean = _strip_ansi(raw_text)
    # Split into lines, drop blanks at edges, return last N
    all_lines = [l for l in clean.splitlines() if l.strip()]
    tail = all_lines[-lines:]
    return "\n".join(tail) if tail else "(output was blank after cleaning)"


@mcp.tool()
async def send_to_session(session_id: str, message: str) -> str:
    """Send a text message to a target Claude Code session via the relay WebSocket.

    The agent-side relay handler will call `tmux send-keys` to type the message
    into the session's terminal pane.

    Commands matching the deny-list (rm -rf, git push --force, DROP TABLE, etc.)
    are blocked and will NOT be sent. All send attempts (including blocked ones)
    are appended to the audit log in commander_state.json.

    Args:
        session_id: Target session ID.
        message: Text to send (will be followed by Enter automatically).
    """
    # --- Guardrail: deny-list check ---
    block_reason = _check_deny_list(message)

    # --- Audit log ---
    now = datetime.now(timezone.utc).isoformat()
    state = _load_state_file()
    audit_entry = {
        "ts": now,
        "session_id": session_id,
        "message": message,
        "blocked": block_reason is not None,
        "block_reason": block_reason,
    }
    if not isinstance(state.get("audit_log"), list):
        state["audit_log"] = []
    state["audit_log"].append(audit_entry)
    state["audit_log"] = state["audit_log"][-_MAX_AUDIT_LOG:]
    _save_state_file(state)

    if block_reason:
        return f"BLOCKED — {block_reason}. Command was NOT sent. Entry logged in audit log."

    url = _ws_url(f"/ws/relay/{session_id}?role=client")

    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url) as ws:
                await ws.send_json({"type": "input", "content": message})

                # Wait up to 5s for confirmation from the agent
                result_holder = []

                async def _wait_confirm():
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("type") == "output":
                                result_holder.append(
                                    data.get("content", "(no content)")
                                )
                                return
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            result_holder.append(
                                "WebSocket closed before confirmation"
                            )
                            return

                try:
                    await asyncio.wait_for(_wait_confirm(), timeout=5)
                    return result_holder[0] if result_holder else "Sent"
                except asyncio.TimeoutError:
                    return "Sent (no confirmation within 5s — message may still be delivered)"
    except Exception as exc:
        return f"Error sending to session: {exc}"

    return "Sent (connection closed)"


@mcp.tool()
async def create_session(machine: str, directory: str, session_name: str = "") -> str:
    """Create a new Claude Code session on a target machine.

    Queues a create_session command via the AgentHQ API. The agent on the
    target machine will pick it up on its next heartbeat (~10s) and spawn
    a new tmux session running Claude Code in the given directory.

    Args:
        machine: Target machine name (e.g. "cege-u-tol-gpu-02").
        directory: Absolute path to the project directory on the target machine.
        session_name: Optional display name (defaults to directory basename).
    """
    url = f"{AGENTHQ_URL}/api/sessions/create"
    payload = {
        "machine": machine,
        "directory": directory,
        "session_name": session_name,
    }

    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                url, json=payload, headers=_auth_headers()
            ) as resp:
                body = await resp.json()
                if resp.status == 200 and body.get("ok"):
                    name = session_name or directory.rstrip("/").split("/")[-1]
                    return (
                        f"Session creation queued for '{name}' on {machine}. "
                        f"Command ID: {body.get('command_id')}. "
                        f"It will appear within ~10s on the next agent heartbeat."
                    )
                if resp.status == 404:
                    return f"Error: No agent found for machine '{machine}'."
                return f"Error: HTTP {resp.status} — {body}"
    except Exception as exc:
        return f"Error creating session: {exc}"


@mcp.tool()
async def stop_session(session_id: str) -> str:
    """Stop a Claude Code session by killing the tmux session.

    Sends a stop command to the agent which kills the Claude process
    and the tmux session, then removes it from managed sessions.

    Args:
        session_id: Target session ID (from list_sessions).
    """
    url = f"{AGENTHQ_URL}/api/sessions/{session_id}/stop"
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, headers=_auth_headers()) as resp:
                body = await resp.json()
                if resp.status == 404:
                    return f"Error: Session '{session_id}' not found."
                if resp.status != 200 or not body.get("ok"):
                    return f"Error: HTTP {resp.status} — {body}"
                cmd_id = body.get("command_id")

            # Poll for result (up to 30s)
            poll_url = f"{AGENTHQ_URL}/api/agents/commands/{cmd_id}"
            for _ in range(30):
                await asyncio.sleep(1)
                async with http.get(poll_url, headers=_auth_headers()) as resp:
                    if resp.status != 200:
                        continue
                    cmd = await resp.json()
                    if cmd.get("status") in ("completed", "failed"):
                        result = cmd.get("result", "")
                        try:
                            r = json.loads(result)
                            return r.get("message", result)
                        except (json.JSONDecodeError, TypeError):
                            return result

            return f"Stop command {cmd_id} queued — agent will process on next heartbeat."
    except Exception as exc:
        return f"Error stopping session: {exc}"


@mcp.tool()
async def restart_session(session_id: str) -> str:
    """Restart a Claude Code session by killing and recreating the tmux session.

    Useful when Claude Code has crashed (pane shows as 'dead') or is stuck.
    The agent kills the existing tmux session and spawns a fresh one with
    Claude Code in the same directory.

    Args:
        session_id: Target session ID (from list_sessions).
    """
    url = f"{AGENTHQ_URL}/api/sessions/{session_id}/restart"
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, headers=_auth_headers()) as resp:
                body = await resp.json()
                if resp.status == 404:
                    return f"Error: Session '{session_id}' not found."
                if resp.status != 200 or not body.get("ok"):
                    return f"Error: HTTP {resp.status} — {body}"
                cmd_id = body.get("command_id")

            # Poll for result (up to 30s)
            poll_url = f"{AGENTHQ_URL}/api/agents/commands/{cmd_id}"
            for _ in range(30):
                await asyncio.sleep(1)
                async with http.get(poll_url, headers=_auth_headers()) as resp:
                    if resp.status != 200:
                        continue
                    cmd = await resp.json()
                    if cmd.get("status") in ("completed", "failed"):
                        result = cmd.get("result", "")
                        try:
                            r = json.loads(result)
                            return r.get("message", result)
                        except (json.JSONDecodeError, TypeError):
                            return result

            return f"Restart command {cmd_id} queued — agent will process on next heartbeat."
    except Exception as exc:
        return f"Error restarting session: {exc}"


@mcp.tool()
async def list_machines() -> str:
    """List all machines and their session counts."""
    async with aiohttp.ClientSession() as http:
        async with http.get(
            f"{AGENTHQ_URL}/api/sessions", headers=_auth_headers()
        ) as resp:
            if resp.status != 200:
                return f"Error: HTTP {resp.status} — {await resp.text()}"
            sessions = await resp.json()

    if not sessions:
        return "No sessions found."

    machines: dict[str, list[str]] = {}
    for s in sessions:
        m = s.get("machine", "unknown")
        machines.setdefault(m, []).append(s.get("project", "?"))

    lines = []
    for m, projects in sorted(machines.items()):
        lines.append(f"🖥 {m} ({len(projects)} sessions)")
        for p in sorted(projects):
            lines.append(f"   • {p}")
    return "\n".join(lines)


@mcp.tool()
async def run_shell(machine: str, command: str, cwd: str = None, timeout: int = 30) -> str:
    """Run a shell command on a target machine's agent.

    Queues the command via the AgentHQ API. The agent picks it up on its
    next heartbeat and runs it via subprocess. Polls for the result.

    Args:
        machine: Target machine name (e.g. "workspace-he1tbf9ytu0u-0").
        command: Shell command to run (e.g. "git pull && ./run.sh restart").
        cwd: Optional working directory for the command.
        timeout: Command timeout in seconds (default 30).
    """
    url = f"{AGENTHQ_URL}/api/agents/run-shell"
    payload = {"machine": machine, "command": command, "timeout": timeout}
    if cwd:
        payload["cwd"] = cwd

    try:
        async with aiohttp.ClientSession() as http:
            # Queue the command
            async with http.post(
                url, json=payload, headers=_auth_headers()
            ) as resp:
                body = await resp.json()
                if resp.status != 200 or not body.get("ok"):
                    return f"Error: {body}"
                cmd_id = body["command_id"]

            # Poll for result (up to timeout + 20s)
            poll_url = f"{AGENTHQ_URL}/api/agents/commands/{cmd_id}"
            for _ in range(timeout + 20):
                await asyncio.sleep(1)
                async with http.get(
                    poll_url, headers=_auth_headers()
                ) as resp:
                    if resp.status != 200:
                        continue
                    cmd = await resp.json()
                    if cmd.get("status") in ("completed", "failed"):
                        result = cmd.get("result", "")
                        try:
                            r = json.loads(result)
                            parts = []
                            if r.get("stdout"):
                                parts.append(r["stdout"])
                            if r.get("stderr"):
                                parts.append(f"STDERR: {r['stderr']}")
                            if r.get("error"):
                                parts.append(f"ERROR: {r['error']}")
                            return "\n".join(parts) if parts else f"Exit code: {r.get('returncode', '?')}"
                        except (json.JSONDecodeError, TypeError):
                            return result

            return f"Command {cmd_id} timed out waiting for result"
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
async def send_telegram(message: str) -> str:
    """Send a plain-text message to the user on Telegram.

    Args:
        message: Text to send.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json=payload) as resp:
                body = await resp.json()
                if resp.status == 200 and body.get("ok"):
                    return "Telegram message sent."
                return f"Telegram error: {body}"
    except Exception as exc:
        return f"Error sending Telegram message: {exc}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
