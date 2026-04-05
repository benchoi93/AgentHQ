from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import store
from server.config import CORS_ORIGINS
from server.routers import agents, sessions, sync, ws

log = logging.getLogger("agenthq-server")

_STALE_CHECK_INTERVAL = 30  # seconds


async def _stale_session_cleanup() -> None:
    """Background task: mark sessions offline for stale agents every 30s."""
    while True:
        try:
            await store.mark_stale_agent_sessions_offline()
        except Exception as exc:
            log.warning("Stale session cleanup error: %s", exc)
        await asyncio.sleep(_STALE_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB and tables exist
    await store.get_db()
    task = asyncio.create_task(_stale_session_cleanup())
    yield
    # Shutdown: cancel background task and close DB
    task.cancel()
    await store.close_db()


app = FastAPI(
    title="AgentHQ",
    description="Unified AI session orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(sessions.router)
app.include_router(sync.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
