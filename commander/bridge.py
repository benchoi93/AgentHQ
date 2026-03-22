"""Telegram ↔ AgentHQ bridge for the 대장 commander session.

Features:
  1. Receives Telegram messages and queues them.
  2. Waits a minimum interval between sends (lets Claude finish processing).
  3. Coalesces rapid messages sent within a short window.
  4. Sends periodic heartbeat pings (only when idle).

Usage:
    python3 commander/bridge.py                    # default config.yaml
    python3 commander/bridge.py --config path.yaml
"""

import argparse
import asyncio
import json
import logging
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
    cfg.setdefault("coalesce_window", 3)
    cfg.setdefault("min_send_interval", 30)  # min seconds between sends
    return cfg


# ---------------------------------------------------------------------------
# Relay WebSocket
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
# Message queue + dispatcher
# ---------------------------------------------------------------------------


class MessageQueue:
    """Queues messages and dispatches with coalescing and min interval."""

    def __init__(self, relay: RelayConnection, coalesce_window: float, min_interval: float) -> None:
        self._relay = relay
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._coalesce_window = coalesce_window
        self._min_interval = min_interval
        self._last_send = 0.0

    @property
    def is_idle(self) -> bool:
        return self._queue.empty() and (time.time() - self._last_send > self._min_interval)

    async def enqueue(self, text: str) -> None:
        await self._queue.put(text)
        log.info("Queued: %s (queue size: %d)", text[:80], self._queue.qsize())

    async def dispatch_loop(self) -> None:
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

            combined = "\n".join(parts)
            if len(parts) > 1:
                log.info("Coalesced %d messages", len(parts))

            # Wait for min interval since last send
            elapsed = time.time() - self._last_send
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                log.info("Waiting %.0fs before sending (min interval)…", wait)
                await asyncio.sleep(wait)

            # Send
            self._last_send = time.time()
            await self._relay.send(combined)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


async def heartbeat_loop(queue: MessageQueue, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        if queue.is_idle:
            await queue.enqueue("[heartbeat] check active tasks and report any updates")
        else:
            log.info("Skipping heartbeat — commander busy")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(config_path: str) -> None:
    cfg = _load_config(config_path)
    allowed_user = int(cfg["telegram_user_id"])

    relay = RelayConnection(cfg)
    relay_task = asyncio.create_task(relay.connect_loop())

    queue = MessageQueue(
        relay,
        coalesce_window=float(cfg["coalesce_window"]),
        min_interval=float(cfg["min_send_interval"]),
    )
    dispatch_task = asyncio.create_task(queue.dispatch_loop())

    hb_task = asyncio.create_task(
        heartbeat_loop(queue, int(cfg["heartbeat_interval"]))
    )

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
        "Bridge starting — commander=%s, heartbeat=%ds, coalesce=%ds, min_interval=%ds",
        cfg["commander_session_id"],
        cfg["heartbeat_interval"],
        cfg["coalesce_window"],
        cfg["min_send_interval"],
    )

    try:
        await dp.start_polling(bot)
    finally:
        hb_task.cancel()
        dispatch_task.cancel()
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
