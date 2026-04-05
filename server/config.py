from __future__ import annotations

import os


AGENTHQ_TOKEN: str = os.environ.get("AGENTHQ_TOKEN", "")
DB_PATH: str = os.environ.get("AGENTHQ_DB_PATH", "agenthq.db")
PORT: int = int(os.environ.get("AGENTHQ_PORT", "8420"))
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get("AGENTHQ_CORS_ORIGINS", "").split(",")
    if o.strip()
]

# Telegram integration for session callbacks
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
