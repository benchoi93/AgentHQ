"""MCP server providing AgentHQ and Telegram tools for the 대장 commander session.

Run as a subprocess by Claude Code via .mcp.json (stdio transport).
Config via environment variables: AGENTHQ_URL, AGENTHQ_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

import asyncio
import base64
import json
import os
import re

import aiohttp
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENTHQ_URL = os.environ.get("AGENTHQ_URL", "http://localhost:8420")
AGENTHQ_TOKEN = os.environ.get("AGENTHQ_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

mcp = FastMCP("commander")

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
        lines.append(
            f"• {s.get('project', '?')} | {s.get('status', '?')} | "
            f"machine={s.get('machine', '?')} | id={s['id']}"
        )
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

    Args:
        session_id: Target session ID.
        message: Text to send (will be followed by Enter automatically).
    """
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
