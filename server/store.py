from __future__ import annotations

from datetime import datetime
from typing import Optional

import aiosqlite

from server.config import DB_PATH

_db: Optional[aiosqlite.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    machine TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ip TEXT,
    agent_version TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER,
    path TEXT NOT NULL DEFAULT '',
    last_activity TEXT NOT NULL,
    metadata TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS known_projects (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT
);

CREATE TABLE IF NOT EXISTS sync_files (
    path TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    hash TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_callbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'report',
    status TEXT NOT NULL DEFAULT 'info',
    summary TEXT NOT NULL,
    task_id TEXT,
    created_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_callbacks_session ON session_callbacks(session_id);
CREATE INDEX IF NOT EXISTS idx_callbacks_created ON session_callbacks(created_at);

-- Performance indexes (issue #1 from perf audit)
CREATE INDEX IF NOT EXISTS idx_sessions_agent_id ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_hidden ON sessions(hidden);
CREATE INDEX IF NOT EXISTS idx_commands_agent_status ON commands(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_known_projects_agent_id ON known_projects(agent_id);
CREATE INDEX IF NOT EXISTS idx_agents_machine ON agents(machine);
CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents(last_seen);

CREATE TABLE IF NOT EXISTS usage_hourly (
    machine TEXT NOT NULL,
    hour TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    message_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (machine, hour, model)
);

CREATE INDEX IF NOT EXISTS idx_usage_hourly_hour ON usage_hourly(hour);
CREATE INDEX IF NOT EXISTS idx_usage_hourly_machine ON usage_hourly(machine);
"""


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.executescript(_SCHEMA)
        # Migrate: add columns if missing (existing DBs)
        for migration in [
            "ALTER TABLE sessions ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE agents ADD COLUMN agent_version TEXT",
        ]:
            try:
                await _db.execute(migration)
            except Exception:
                pass  # column already exists
        await _db.commit()
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# --- Agent operations ---

async def upsert_agent(
    agent_id: str, name: str, machine: str,
    ip: Optional[str] = None, agent_version: Optional[str] = None,
) -> None:
    db = await get_db()
    now = datetime.utcnow().isoformat()
    await db.execute(
        """
        INSERT INTO agents (id, name, machine, last_seen, ip, agent_version)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            machine = excluded.machine,
            last_seen = excluded.last_seen,
            ip = excluded.ip,
            agent_version = excluded.agent_version
        """,
        (agent_id, name, machine, now, ip, agent_version),
    )
    await db.commit()


async def list_agents(stale_seconds: int = 120) -> list[dict]:
    """List agents, excluding those that haven't heartbeated recently."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM agents
           WHERE julianday('now') - julianday(last_seen) <= ? / 86400.0
           ORDER BY last_seen DESC""",
        (stale_seconds,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# --- Session operations ---

async def upsert_session(
    session_id: str,
    agent_id: str,
    project: str,
    status: str,
    pid: Optional[int],
    path: str,
    last_activity: str,
) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO sessions (id, agent_id, project, status, pid, path, last_activity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            agent_id = excluded.agent_id,
            project = excluded.project,
            status = excluded.status,
            pid = excluded.pid,
            path = excluded.path,
            last_activity = excluded.last_activity
        WHERE hidden = 0
        """,
        (session_id, agent_id, project, status, pid, path, last_activity),
    )
    await db.commit()


async def list_sessions(
    machine: Optional[str] = None, status: Optional[str] = None,
) -> list[dict]:
    db = await get_db()
    query = """
        SELECT s.*, a.name AS agent_name, a.machine, a.agent_version
        FROM sessions s
        JOIN agents a ON s.agent_id = a.id
        WHERE s.status != 'offline' AND s.hidden = 0
    """
    params: list = []
    if machine:
        query += " AND a.machine = ?"
        params.append(machine)
    if status:
        query += " AND s.status = ?"
        params.append(status)
    query += " ORDER BY s.last_activity DESC"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_session(session_id: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT s.*, a.name AS agent_name, a.machine, a.agent_version
        FROM sessions s
        JOIN agents a ON s.agent_id = a.id
        WHERE s.id = ?
        """,
        (session_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_session(session_id: str) -> bool:
    """Hide a session (soft-delete). It won't reappear from heartbeat upserts."""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE sessions SET hidden = 1 WHERE id = ?", (session_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def mark_missing_sessions_offline(
    agent_id: str, active_session_ids: list[str],
) -> None:
    """Mark sessions for this agent that are no longer reported as offline."""
    db = await get_db()
    if active_session_ids:
        placeholders = ",".join("?" for _ in active_session_ids)
        await db.execute(
            f"UPDATE sessions SET status = 'offline' WHERE agent_id = ? AND id NOT IN ({placeholders})",
            [agent_id, *active_session_ids],
        )
    else:
        await db.execute(
            "UPDATE sessions SET status = 'offline' WHERE agent_id = ?",
            (agent_id,),
        )
    await db.commit()


async def mark_stale_agent_sessions_offline(stale_seconds: int = 60) -> None:
    """Mark all sessions offline for agents that haven't heartbeated recently."""
    db = await get_db()
    await db.execute(
        """
        UPDATE sessions SET status = 'offline'
        WHERE status != 'offline'
          AND agent_id IN (
            SELECT id FROM agents
            WHERE julianday('now') - julianday(last_seen) > ? / 86400.0
          )
        """,
        (stale_seconds,),
    )
    await db.commit()


# --- Command operations ---

async def create_command(agent_id: str, cmd_type: str, payload: str) -> int:
    db = await get_db()
    now = datetime.utcnow().isoformat()
    cursor = await db.execute(
        "INSERT INTO commands (agent_id, type, payload, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
        (agent_id, cmd_type, payload, now),
    )
    await db.commit()
    return cursor.lastrowid


async def get_pending_commands(agent_id: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM commands WHERE agent_id = ? AND status = 'pending' ORDER BY created_at",
        (agent_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_command(command_id: int, status: str, result: Optional[str] = None) -> None:
    db = await get_db()
    now = datetime.utcnow().isoformat()
    if result is not None:
        await db.execute(
            "UPDATE commands SET status = ?, completed_at = ?, result = ? WHERE id = ?",
            (status, now, result, command_id),
        )
    else:
        await db.execute(
            "UPDATE commands SET status = ?, completed_at = ? WHERE id = ?",
            (status, now, command_id),
        )
    await db.commit()


async def get_command(command_id: int) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM commands WHERE id = ?", (command_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def unhide_session(session_id: str) -> bool:
    """Restore a hidden session."""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE sessions SET hidden = 0 WHERE id = ?", (session_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def upsert_known_projects(
    agent_id: str, projects: list[dict],
) -> None:
    """Replace known projects for an agent."""
    db = await get_db()
    await db.execute("DELETE FROM known_projects WHERE agent_id = ?", (agent_id,))
    if projects:
        await db.executemany(
            "INSERT OR REPLACE INTO known_projects (id, agent_id, name, path, last_activity) VALUES (?, ?, ?, ?, ?)",
            [(p["id"], agent_id, p["name"], p["path"], p["last_activity"]) for p in projects],
        )
    await db.commit()


async def list_known_projects(machine: Optional[str] = None) -> list[dict]:
    db = await get_db()
    query = """
        SELECT kp.*, a.machine
        FROM known_projects kp
        JOIN agents a ON kp.agent_id = a.id
    """
    params: list = []
    if machine:
        query += " WHERE a.machine = ?"
        params.append(machine)
    query += " ORDER BY kp.last_activity DESC"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def batch_heartbeat(
    agent_id: str,
    name: str,
    machine: str,
    ip: Optional[str],
    agent_version: Optional[str],
    sessions: list[dict],
    active_session_ids: list[str],
    projects: Optional[list[dict]],
) -> list[dict]:
    """Perform all heartbeat DB ops in a single transaction (1 fsync).

    Returns list of pending commands (already marked as dispatched).
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    # 1. Upsert agent
    await db.execute(
        """
        INSERT INTO agents (id, name, machine, last_seen, ip, agent_version)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            machine = excluded.machine,
            last_seen = excluded.last_seen,
            ip = excluded.ip,
            agent_version = excluded.agent_version
        """,
        (agent_id, name, machine, now, ip, agent_version),
    )

    # 2. Upsert sessions
    for sess in sessions:
        await db.execute(
            """
            INSERT INTO sessions (id, agent_id, project, status, pid, path, last_activity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent_id = excluded.agent_id,
                project = excluded.project,
                status = excluded.status,
                pid = excluded.pid,
                path = excluded.path,
                last_activity = excluded.last_activity
            WHERE hidden = 0
            """,
            (sess["id"], agent_id, sess["project"], sess["status"],
             sess["pid"], sess["path"], sess["last_activity"]),
        )

    # 3. Mark missing sessions offline
    if active_session_ids:
        placeholders = ",".join("?" for _ in active_session_ids)
        await db.execute(
            f"UPDATE sessions SET status = 'offline' WHERE agent_id = ? AND id NOT IN ({placeholders})",
            [agent_id, *active_session_ids],
        )
    else:
        await db.execute(
            "UPDATE sessions SET status = 'offline' WHERE agent_id = ?",
            (agent_id,),
        )

    # 4. Replace known projects (executemany)
    if projects is not None:
        await db.execute("DELETE FROM known_projects WHERE agent_id = ?", (agent_id,))
        if projects:
            await db.executemany(
                "INSERT OR REPLACE INTO known_projects (id, agent_id, name, path, last_activity) VALUES (?, ?, ?, ?, ?)",
                [(p["id"], agent_id, p["name"], p["path"], p["last_activity"]) for p in projects],
            )

    # 5. Fetch and batch-dispatch pending commands
    cursor = await db.execute(
        "SELECT * FROM commands WHERE agent_id = ? AND status = 'pending' ORDER BY created_at",
        (agent_id,),
    )
    rows = await cursor.fetchall()
    pending = [dict(row) for row in rows]

    if pending:
        ids = [cmd["id"] for cmd in pending]
        placeholders = ",".join("?" for _ in ids)
        await db.execute(
            f"UPDATE commands SET status = 'dispatched', completed_at = ? WHERE id IN ({placeholders})",
            [now, *ids],
        )

    # Single commit for the entire heartbeat
    await db.commit()
    return pending


async def get_agent_by_machine(machine: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM agents WHERE machine = ? ORDER BY last_seen DESC LIMIT 1",
        (machine,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# --- Sync file operations ---

async def upsert_sync_file(path: str, content: str, hash: str, agent_id: str) -> bool:
    db = await get_db()
    now = datetime.utcnow().isoformat()
    cursor = await db.execute(
        """
        INSERT INTO sync_files (path, content, hash, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            content = excluded.content,
            hash = excluded.hash,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (path, content, hash, agent_id, now),
    )
    await db.commit()
    return cursor.rowcount > 0


# --- Session callback operations ---

async def create_callback(
    session_id: str, project: str, event_type: str,
    status: str, summary: str, task_id: Optional[str] = None,
) -> int:
    db = await get_db()
    now = datetime.utcnow().isoformat()
    cursor = await db.execute(
        """INSERT INTO session_callbacks
           (session_id, project, event_type, status, summary, task_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, project, event_type, status, summary, task_id, now),
    )
    await db.commit()
    return cursor.lastrowid


async def list_callbacks(
    session_id: Optional[str] = None, unacknowledged_only: bool = True, limit: int = 50,
) -> list[dict]:
    db = await get_db()
    query = "SELECT * FROM session_callbacks WHERE 1=1"
    params: list = []
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    if unacknowledged_only:
        query += " AND acknowledged = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def acknowledge_callback(callback_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE session_callbacks SET acknowledged = 1 WHERE id = ?",
        (callback_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


# --- Usage hourly operations ---

async def upsert_usage_hourly(rows: list[dict]) -> int:
    """Upsert hourly usage data reported by an agent. Returns count of rows upserted."""
    if not rows:
        return 0
    db = await get_db()
    now = datetime.utcnow().isoformat()
    await db.executemany(
        """
        INSERT INTO usage_hourly
            (machine, hour, model, input_tokens, output_tokens,
             cache_creation_tokens, cache_read_tokens, cost_usd, message_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(machine, hour, model) DO UPDATE SET
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cache_creation_tokens = excluded.cache_creation_tokens,
            cache_read_tokens = excluded.cache_read_tokens,
            cost_usd = excluded.cost_usd,
            message_count = excluded.message_count,
            updated_at = excluded.updated_at
        """,
        [
            (
                r["machine"], r["hour"], r["model"],
                r["input_tokens"], r["output_tokens"],
                r["cache_creation_tokens"], r["cache_read_tokens"],
                r["cost_usd"], r["message_count"], now,
            )
            for r in rows
        ],
    )
    await db.commit()
    return len(rows)


async def query_usage_hourly(
    since: str,
    until: str | None = None,
    machine: str | None = None,
) -> list[dict]:
    """Query hourly usage data. since/until are ISO timestamps."""
    db = await get_db()
    query = "SELECT * FROM usage_hourly WHERE hour >= ?"
    params: list = [since]
    if until:
        query += " AND hour < ?"
        params.append(until)
    if machine:
        query += " AND machine = ?"
        params.append(machine)
    query += " ORDER BY hour, machine, model"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_sync_manifest() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT path, hash, updated_by, updated_at FROM sync_files ORDER BY path"
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_sync_file(path: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM sync_files WHERE path = ?", (path,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None
