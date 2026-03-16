from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, Field, field_validator


# --- Heartbeat (agent -> server) ---

class SessionPayload(BaseModel):
    id: str
    project: str
    status: str = "running"  # running | idle | error | manual
    pid: Optional[int] = None
    path: str = ""
    last_activity: Union[float, str] = Field(default_factory=time.time)

    @field_validator("last_activity", mode="before")
    @classmethod
    def coerce_last_activity(cls, v: Union[float, int, str]) -> float:
        """Accept float timestamps or ISO strings, normalise to float."""
        if isinstance(v, (int, float)):
            return float(v)
        # Try parsing ISO string → timestamp
        try:
            return datetime.fromisoformat(v).timestamp()
        except (ValueError, TypeError):
            return time.time()


class KnownProject(BaseModel):
    id: str
    name: str
    path: str
    last_activity: Union[float, str] = Field(default_factory=time.time)


class AgentHeartbeat(BaseModel):
    agent_name: str
    machine: str
    sessions: list[SessionPayload] = []
    known_projects: list[KnownProject] = []


# --- Responses ---

class AgentInfo(BaseModel):
    id: str
    name: str
    machine: str
    last_seen: str
    ip: Optional[str] = None


class SessionInfo(BaseModel):
    id: str
    agent_name: str
    machine: str
    project: str
    status: str
    pid: Optional[int] = None
    path: str = ""
    last_activity: str = ""


class SessionDetail(SessionInfo):
    metadata: Optional[dict] = None


# --- WebSocket messages ---

class LogEntry(BaseModel):
    type: str = "log"
    session_id: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RelayMessage(BaseModel):
    type: str  # "input" | "output"
    session_id: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# --- Create session ---

class CreateSessionRequest(BaseModel):
    machine: str
    directory: str
    session_name: str = ""


# --- Sync ---

class SyncFileEntry(BaseModel):
    path: str
    content: str
    hash: str


class SyncManifest(BaseModel):
    files: list[SyncFileEntry]
