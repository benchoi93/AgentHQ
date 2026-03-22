"""Telegram ↔ AgentHQ bridge for the 대장 commander session.

Simple design:
  1. Receives Telegram messages → coalesces rapid messages (3s window) → sends to relay.
  2. tmux naturally buffers input while Claude is busy — no need for prompt detection.
  3. Periodic heartbeat pings (skipped if a message was sent recently).

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


def _load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key in ["telegram_bot_token", "telegram_user_id", "agenthq_url",
                "agenthq_token", "commander_session_id"]:
        if not cfg.get(key):
            raise ValueError(f"Missing required config key: {key}")
    cfg.setdefault("heartbeat_interval", 120)
    cfg.setdefault("coalesce_window", 3)
    return cfg


# ---------------------------------------------------------------------------
# Relay WebSocket
# ---------------------------------------------------------------------------


class RelayConnection:
    def __init__(self, cfg: dict) -> None:
        base = cfg["agenthq_url"].rstrip("/").replace("http", "ws", 1)
        sid = cfg["commander_session_id"]
        token = cfg["agenthq_token"]
        self._url = f"{base}/ws/relay/{sid}?token={token}&role=client"
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = False
        self.last_send = 0.0

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
                await asyncio.sleep(5)
        if self._http:
            await self._http.close()

    async def send(self, text: str) -> None:
        await self._ready.wait()
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"type": "input", "content": text})
            self.last_send = time.time()
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
# Coalescing queue — batches rapid messages, sends immediately
# ---------------------------------------------------------------------------


class CoalescingQueue:
    def __init__(self, relay: RelayConnection, window: float = 3.0) -> None:
        self._relay = relay
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._window = window

    async def enqueue(self, text: str) -> None:
        await self._queue.put(text)

    async def run(self) -> None:
        while True:
            text = await self._queue.get()
            # Wait briefly for more messages to coalesce
            await asyncio.sleep(self._window)
            parts = [text]
            while not self._queue.empty():
                try:
                    parts.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            combined = "\n".join(parts)
            if len(parts) > 1:
                log.info("Coalesced %d messages", len(parts))
            await self._relay.send(combined)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


async def heartbeat_loop(relay: RelayConnection, queue: CoalescingQueue, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        # Skip heartbeat if we sent something recently (within interval)
        if time.time() - relay.last_send < interval:
            log.info("Skipping heartbeat — recent activity")
            continue
        await queue.enqueue("[heartbeat] check active tasks and report any updates")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(config_path: str) -> None:
    cfg = _load_config(config_path)
    allowed_user = int(cfg["telegram_user_id"])

    relay = RelayConnection(cfg)
    relay_task = asyncio.create_task(relay.connect_loop())

    queue = CoalescingQueue(relay, window=float(cfg["coalesce_window"]))
    queue_task = asyncio.create_task(queue.run())

    hb_task = asyncio.create_task(
        heartbeat_loop(relay, queue, int(cfg["heartbeat_interval"]))
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

    log.info("Bridge starting — commander=%s, heartbeat=%ds",
             cfg["commander_session_id"], cfg["heartbeat_interval"])

    try:
        await dp.start_polling(bot)
    finally:
        hb_task.cancel()
        queue_task.cancel()
        relay_task.cancel()
        await relay.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="대장 Telegram ↔ AgentHQ bridge")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.yaml"),
    )
    args = parser.parse_args()
    asyncio.run(main(args.config))
