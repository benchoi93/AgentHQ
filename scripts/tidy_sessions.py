#!/usr/bin/env python3
"""Daily cron (gpu01): ask the commander session to tidy AgentHQ sessions.

Injects a [session-tidy] prompt into the commander's terminal through the same
relay WebSocket the Telegram bridge uses. The policy itself lives in
commander/session/CLAUDE.md ("Session folders & sleeping sessions"); the
commander stops idle sessions, files folders, and reports once on Telegram.
Crontab: 30 6 * * * python3 /home/chois/gitsrcs/AgentHQ/scripts/tidy_sessions.py
"""
import asyncio
import sys
from pathlib import Path

import aiohttp
import yaml

CONFIG = Path(__file__).resolve().parent.parent / "commander" / "config.yaml"
PROMPT = (
    "[session-tidy] Daily housekeeping (policy: CLAUDE.md > Session folders & sleeping sessions).\n"
    "1. list_idle_sessions(7) -> stop_session every candidate that is not PROTECTED and has no "
    "in-progress task in active_tasks. Stopped sessions keep their folder and wake on send_to_session.\n"
    "2. list_sessions -> any session whose folder is still the path default (active, papers, proposals, "
    "tools, gitsrcs, home, ...) gets a topical one via set_session_folder: Teaching, Proposals, Papers, "
    "Research, Infra, Collab. Never change a folder that already has a topical name.\n"
    "3. send_telegram ONE line: '\U0001f9f9 Tidy: stopped N (names), filed M, live K'. Nothing changed -> send nothing."
)


async def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text())
    base = cfg["agenthq_url"].rstrip("/").replace("http", "ws", 1)
    url = f"{base}/ws/relay/{cfg['commander_session_id']}?token={cfg['agenthq_token']}&role=client"
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, timeout=15) as ws:
            await ws.send_json({"type": "input", "content": PROMPT})
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                print("relay:", (msg.data or "")[:120] if msg.type == aiohttp.WSMsgType.TEXT else msg.type)
            except asyncio.TimeoutError:
                print("sent (no confirmation)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
