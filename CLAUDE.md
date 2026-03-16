# AgentHQ — Unified AI Session Orchestrator

Web UI to monitor, stream logs, and relay chat to Claude Code sessions running across multiple machines.

## Architecture

```
  React Frontend (Vite+Tailwind)
         │ REST + WebSocket
  FastAPI Backend (SQLite)
    │        │        │        │
  Agent    Agent    Agent    Agent
  (Win)   (WSL)   (Cloud)  (GPU)
```

**Hosted on:** GPU server (Ubuntu + Docker), accessible on UMN network port 30002.

## Directory Structure

```
server/          FastAPI backend (auth, REST, WebSocket, SQLite store)
agent/           Lightweight agent (auto-discovery, heartbeat, log stream, chat relay)
frontend/        React + TypeScript + TailwindCSS v4 dashboard
docker/          Dockerfiles + nginx config
docker-compose.yml
```

## Deploy on GPU Server

```bash
# 1. Clone and configure
git clone git@github.com:benchoi93/AgentHQ.git && cd AgentHQ
cp env.example .env
# Edit .env: set AGENTHQ_TOKEN to a strong secret

# 2. Launch
docker compose up -d --build

# 3. Access
# http://<gpu-server-ip>:30002
```

## Run Agent on Each Machine

```bash
pip install aiohttp pyyaml
cd agent
cp config.yaml.example config.yaml
# Edit config.yaml: set server_url, token, machine_name

python agenthq_agent.py --config config.yaml
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTHQ_TOKEN` | (required) | Shared auth token |
| `AGENTHQ_PORT` | `30002` | Exposed port |
| `AGENTHQ_DB_PATH` | `agenthq.db` | SQLite database path |

## Features (v0.1)

- Session monitoring: status, project, machine, last activity
- Real-time log streaming via WebSocket
- Chat relay to tmux-wrapped sessions
- Session creation with project suggestions from ~/.claude/projects/
- Managed tmux sessions + config-based session registration
- Dark theme, responsive dashboard
- Simple bearer token auth

## Tech Stack

- **Backend:** Python, FastAPI, aiosqlite, uvicorn
- **Frontend:** React 18, TypeScript, Vite, TailwindCSS v4
- **Agent:** Python, aiohttp, asyncio
- **Deploy:** Docker Compose, nginx reverse proxy

## Development Guidelines

### WebSocket Architecture

The frontend uses two custom hooks for WebSocket connections:
- `useWebSocket` — general JSON message WebSocket (files, logs, relay)
- `useTerminalWebSocket` — binary terminal data over WebSocket (terminal, claude)

**Session switching lifecycle:** When `id` changes in `SessionDetail`:
1. WebSocket URLs change → hooks close old connection, open new one
2. `connected` is explicitly set to `false` at effect start to guarantee a `false→true` transition
3. `disposed` flag (closure-scoped per effect) prevents stale callbacks from old WS
4. `reconnectCount` is reset to 0 on each new URL
5. Child components use `key={id}` to force full remount on session switch

**Server-side agent WS management:** The `ConnectionManager` enforces one-agent-per-session. When a new agent connects for a session that already has an agent WS, the old WS is explicitly closed (`_replace_agent`). Disconnect handlers verify identity (`ws is dict[sid]`) before removing, preventing the race where an old connection's cleanup removes a newer connection.

### Common Pitfalls (Do Not Repeat)
- Never use bare `.pop(session_id)` in disconnect handlers — always check `dict.get(sid) is ws` first
- Never add `useCallback` deps on entire hook return objects — reference stable sub-properties (e.g., `logs.clearMessages` not `logs`)
- When adding `disposed` guards to `onclose`, remember this blocks `setConnected(false)` — must set it explicitly at effect start
- Cap message arrays to prevent unbounded growth (MAX_MESSAGES = 5000)

## Current Status

- [x] Backend (FastAPI + SQLite + WebSocket)
- [x] Agent (discovery + heartbeat + logs + relay)
- [x] Frontend (dashboard + log viewer + chat relay)
- [x] Docker deployment
- [ ] End-to-end testing on GPU server
- [ ] Add Codex/Gemini session support
