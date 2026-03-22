"""Telegram ↔ AgentHQ bridge for the 대장 commander session.

Features:
  1. Receives Telegram messages and queues them.
  2. Monitors the commander terminal for the idle prompt (❯).
  3. Only sends the next queued message when the prompt is ready.
  4. Sends periodic heartbeat pings (only when idle).
  5. Coalesces rapid messages sent within a short window.

Usage:
    python3 commander/bridge.py                    # default config.yaml
    python3 commander/bridge.py --config path.yaml
"""

import argparse
import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path

import aiohttp
import yaml
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge")

# Strip ANSI escape codes from terminal output
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Za-z]|\x1b[>=<]|\r")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    required = [
        "telegram_bot_token",
        "telegram_user_id",
        "agenthq_url",
        "agenthq_token",
        "commander_session_id",
    ]
    for key in required:
        if not cfg.get(key):
            raise ValueError(f"Missing required config key: {key}")
    cfg.setdefault("heartbeat_interval", 120)
    cfg.setdefault("coalesce_window", 3)  # seconds to wait for more messages
    return cfg


# ---------------------------------------------------------------------------
# Relay WebSocket — sends input to commander via tmux send-keys
# ---------------------------------------------------------------------------


class RelayConnection:
    """Persistent WebSocket connection to the commander session's relay endpoint."""

    def __init__(self, cfg: dict) -> None:
        base = cfg["agenthq_url"].rstrip("/").replace("http", "ws", 1)
        sid = cfg["commander_session_id"]
        token = cfg["agenthq_token"]
        self._url = f"{base}/ws/relay/{sid}?token={token}&role=client"
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = False

    async def connect_loop(self) -> None:
        self._http = aiohttp.ClientSession()
        while not self._stop:
            try:
                self._ws = await self._http.ws_connect(self._url)
                log.info("Relay WebSocket connected")
                self._ready.set()
                async for msg in self._ws:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Relay connection error: %s", exc)
            self._ready.clear()
            if not self._stop:
                log.info("Reconnecting relay in 5s…")
                await asyncio.sleep(5)
        if self._http:
            await self._http.close()

    async def send(self, text: str) -> None:
        await self._ready.wait()
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "input", "content": text})
            log.info("→ relay: %s", text[:120])
        else:
            log.warning("Relay WS not connected, dropping message")

    async def close(self) -> None:
        self._stop = True
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._http:
            await self._http.close()


# ---------------------------------------------------------------------------
# Terminal monitor — watches for idle prompt (❯)
# ---------------------------------------------------------------------------


class PromptMonitor:
    """Watches the commander's terminal output for the ❯ prompt."""

    def __init__(self, cfg: dict) -> None:
        base = cfg["agenthq_url"].rstrip("/").replace("http", "ws", 1)
        sid = cfg["commander_session_id"]
        token = cfg["agenthq_token"]
        self._url = f"{base}/ws/terminal/{sid}?token={token}&role=client"
        self._idle = asyncio.Event()
        self._idle.set()  # assume idle at start
        self._stop = False
        self._last_output = time.time()

    async def watch_loop(self) -> None:
        """Connect to terminal WS and watch for prompt."""
        while not self._stop:
            try:
                async with aiohttp.ClientSession() as http:
                    async with http.ws_connect(self._url) as ws:
                        log.info("Terminal monitor connected")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    if data.get("type") == "output" and "data" in data:
                                        raw = base64.b64decode(data["data"]).decode("utf-8", errors="replace")
                                        clean = _ANSI_RE.sub("", raw)
                                        self._last_output = time.time()

                                        # Check for idle prompt
                                        if "❯" in clean and clean.strip().endswith("❯"):
                                            if not self._idle.is_set():
                                                log.info("Prompt detected — idle")
                                            self._idle.set()
                                        elif clean.strip():
                                            # Any non-empty output that isn't just the prompt = busy
                                            self._idle.clear()
                                except (json.JSONDecodeError, KeyError):
                                    pass
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Terminal monitor error: %s", exc)
            if not self._stop:
                await asyncio.sleep(3)

    async def wait_for_idle(self, timeout: float = 300) -> bool:
        """Wait until prompt is idle or timeout."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("Prompt idle timeout (%ds)", timeout)
            return False

    @property
    def is_idle(self) -> bool:
        return self._idle.is_set()

    def mark_busy(self) -> None:
        self._idle.clear()


# ---------------------------------------------------------------------------
# Message queue + dispatcher
# ---------------------------------------------------------------------------


class MessageQueue:
    """Queues messages and dispatches them one at a time when prompt is idle."""

    def __init__(self, relay: RelayConnection, monitor: PromptMonitor, coalesce_window: float = 3.0) -> None:
        self._relay = relay
        self._monitor = monitor
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._coalesce_window = coalesce_window

    async def enqueue(self, text: str) -> None:
        await self._queue.put(text)
        log.info("Queued: %s (queue size: %d)", text[:80], self._queue.qsize())

    async def dispatch_loop(self) -> None:
        """Process queue: wait for idle, coalesce, send."""
        while True:
            # Wait for first message
            text = await self._queue.get()

            # Coalesce: wait briefly for more messages
            await asyncio.sleep(self._coalesce_window)
            parts = [text]
            while not self._queue.empty():
                try:
                    parts.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Combine messages
            if len(parts) > 1:
                combined = "\n".join(parts)
                log.info("Coalesced %d messages", len(parts))
            else:
                combined = parts[0]

            # Wait for prompt to be idle
            if not self._monitor.is_idle:
                log.info("Waiting for idle prompt before sending…")
                await self._monitor.wait_for_idle(timeout=300)

            # Send and mark busy
            self._monitor.mark_busy()
            await self._relay.send(combined)

            # Brief pause to let Claude start processing
            await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Heartbeat — only sends when idle
# ---------------------------------------------------------------------------


async def heartbeat_loop(queue: MessageQueue, monitor: PromptMonitor, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        if monitor.is_idle:
            await queue.enqueue("[heartbeat] check active tasks and report any updates")
        else:
            log.info("Skipping heartbeat — commander busy")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(config_path: str) -> None:
    cfg = _load_config(config_path)
    allowed_user = int(cfg["telegram_user_id"])

    # --- Relay connection ---
    relay = RelayConnection(cfg)
    relay_task = asyncio.create_task(relay.connect_loop())

    # --- Terminal prompt monitor ---
    monitor = PromptMonitor(cfg)
    monitor_task = asyncio.create_task(monitor.watch_loop())

    # --- Message queue ---
    queue = MessageQueue(relay, monitor, coalesce_window=float(cfg["coalesce_window"]))
    dispatch_task = asyncio.create_task(queue.dispatch_loop())

    # --- Heartbeat ---
    hb_task = asyncio.create_task(
        heartbeat_loop(queue, monitor, int(cfg["heartbeat_interval"]))
    )

    # --- Telegram bot ---
    bot = Bot(token=cfg["telegram_bot_token"])
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(msg: types.Message) -> None:
        if msg.from_user and msg.from_user.id != allowed_user:
            return
        await msg.answer("대장 bridge active. Send messages to route to sessions.")

    @dp.message()
    async def on_message(msg: types.Message) -> None:
        if not msg.from_user or msg.from_user.id != allowed_user:
            return
        text = msg.text or msg.caption or ""
        if not text.strip():
            return
        await queue.enqueue(text)
        log.info("Telegram → queue: %s", text[:120])

    log.info(
        "Bridge starting — commander=%s, heartbeat=%ds, coalesce=%ds",
        cfg["commander_session_id"],
        cfg["heartbeat_interval"],
        cfg["coalesce_window"],
    )

    try:
        await dp.start_polling(bot)
    finally:
        hb_task.cancel()
        dispatch_task.cancel()
        monitor_task.cancel()
        relay_task.cancel()
        await relay.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="대장 Telegram ↔ AgentHQ bridge")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
        help="Path to config.yaml (default: commander/config.yaml)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.config))
