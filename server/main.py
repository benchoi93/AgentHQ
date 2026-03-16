from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import store
from server.config import CORS_ORIGINS
from server.routers import agents, sessions, sync, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB and tables exist
    await store.get_db()
    yield
    # Shutdown: close DB connection
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
