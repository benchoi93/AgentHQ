# AgentHQ — AI Session Orchestrator

A web-based dashboard for managing Claude Code sessions across multiple machines. Create, monitor, and interact with Claude Code instances running on different servers from a single unified interface.

![AgentHQ Dashboard](sample.png)

## Why AgentHQ?

Running Claude Code across multiple machines (dev laptops, GPU servers, cloud VMs) means juggling SSH sessions and losing track of what's running where. AgentHQ gives you:

- **One dashboard** to see all Claude Code sessions across all machines
- **Web terminal** to interact with any session from your browser
- **Start/Stop/Restart** controls — spin up Claude Code on any machine with one click
- **File browser** to view project files alongside the terminal
- **Project suggestions** pulled from `~/.claude/projects/` history
- **Telegram commander** — control all sessions from your phone via a Telegram bot

## Architecture

```
  Browser (React + xterm.js)          Telegram
         │                               │
    REST + WebSocket               bridge.py
         │                               │
  ┌──────┴───────────────────────────────┘
  │          FastAPI + SQLite            │
  │     Docker (nginx + uvicorn)        │
  └──────┬──────────────────────────────┘
         │
    ┌────┼────┬────────┐
    │    │    │        │
  Agent Agent Agent  Agent
  (WSL) (GPU) (Cloud) (Dev)
               │
          Commander session
        (Claude + MCP tools)
```

Each **agent** runs on a machine, heartbeats to the server, and manages tmux sessions running Claude Code. The **server** stores state in SQLite and relays WebSocket connections between the browser and agents. The **frontend** is a React SPA with an embedded terminal (xterm.js). The **commander** is an optional Telegram bot that lets you control all sessions from your phone.

## Quick Start

### 1. Deploy the Server

```bash
git clone git@github.com:benchoi93/AgentHQ.git && cd AgentHQ
cp env.example .env
# Edit .env: set AGENTHQ_TOKEN to a strong secret

docker compose up -d --build
# Access at http://<your-server>:8420
```

### 2. Run an Agent on Each Machine

```bash
pip install aiohttp pyyaml
cd agent
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
server_url: "http://<your-server>:8420"
token: "your-token-here"        # must match AGENTHQ_TOKEN
machine_name: "my-gpu-server"   # human-readable name
```

Run the agent:

```bash
# Foreground
python -m agenthq_agent --config config.yaml

# Background (persists after logout)
nohup python -m agenthq_agent --config config.yaml > agent.log 2>&1 &

# Or in tmux (can reattach later)
tmux new-session -d -s agenthq-agent 'python -m agenthq_agent --config config.yaml'
```

The agent will appear in the dashboard within 10 seconds.

### 3. Set Up the Commander (Optional)

The commander lets you control AgentHQ sessions from Telegram. It consists of two parts: a Telegram bridge daemon and an MCP server that gives a dedicated Claude Code session tools to manage other sessions.

```bash
pip install mcp aiohttp aiogram pyyaml
cd commander
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
telegram_bot_token: "your-bot-token"     # from @BotFather
telegram_user_id: 123456789              # your Telegram user ID
agenthq_url: "http://<your-server>:8420"
agenthq_token: "your-token-here"         # must match AGENTHQ_TOKEN
commander_session_id: ""                 # fill after creating the commander session
heartbeat_interval: 60
```

Set up the MCP config for the commander session:

```bash
cd session
cp .mcp.json.example .mcp.json
# Edit .mcp.json with the same tokens and paths
```

Create a session for the commander in the dashboard (pointing to `commander/session/`), then copy its session ID into `config.yaml`. Start the bridge:

```bash
nohup python bridge.py > bridge.log 2>&1 &
```

Now send `/status` to your Telegram bot to verify it works.

### 4. Create a Session

Click the **+** button in the dashboard, select a machine, pick a project from the suggestions (populated from `~/.claude/projects/` history), and click **Create**. This spawns a tmux session running `claude --dangerously-skip-permissions` on the selected machine.

## Features

### Session Management
- **Create** sessions on any connected machine via the web UI
- **Start/Stop/Restart** — control session lifecycle from the header bar
- **Auto-discovery** of project history from `~/.claude/projects/`
- **Persistent sessions** — managed sessions survive agent restarts (stored in `managed_sessions.json`)

### Web Terminal
- Full interactive terminal via xterm.js + PTY
- Connects to tmux sessions on remote machines
- Auto-resizing to fit the browser window

### File Browser
- Browse project files in the sidebar
- View file contents alongside the terminal
- Real-time file tree updates via WebSocket

### Multi-Machine Support
- Sessions grouped by machine in the sidebar
- WSL agents auto-detect Windows-side `~/.claude/projects/`
- Path decoding handles both Linux (`-home-user-project`) and Windows (`C--Users-user-project`) Claude project encodings

### Dashboard
- Session list with status indicators (running/stopped/offline)
- Filter by machine or status
- Dark theme, responsive layout
- Bearer token authentication

### Commander (Telegram Bot)
- Control all sessions from Telegram on your phone — no browser or SSH needed
- Slash commands: `/status`, `/check`, `/tell`, `/train`, `/test`, `/build`, `/logs`, `/diff`, `/new`, `/explore`, `/machines`
- A dedicated Claude Code session with MCP tools that can read output from, send input to, and create sessions across all machines
- Message coalescing (3s window) to batch rapid inputs
- Periodic heartbeat pings to check on active tasks

## Configuration

### Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTHQ_TOKEN` | (required) | Shared auth token for agents and the web UI |
| `AGENTHQ_PORT` | `8420` | Port exposed by Docker |
| `AGENTHQ_DB_PATH` | `agenthq.db` | SQLite database path |

### Agent Configuration (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `server_url` | `http://localhost:8420` | AgentHQ server URL |
| `token` | (required) | Must match `AGENTHQ_TOKEN` |
| `machine_name` | hostname | Human-readable machine name |
| `heartbeat_interval` | `10` | Seconds between heartbeats |
| `sync_enabled` | `true` | Sync `.claude/` folder to server |
| `extra_sessions` | `[]` | Manually registered sessions |
| `extra_project_dirs` | `[]` | Additional `.claude/projects/` dirs to scan |

### Commander Configuration (`commander/config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `telegram_bot_token` | (required) | Telegram bot token from @BotFather |
| `telegram_user_id` | (required) | Your Telegram user ID (only this user can control the bot) |
| `agenthq_url` | `http://localhost:8420` | AgentHQ server URL |
| `agenthq_token` | (required) | Must match `AGENTHQ_TOKEN` |
| `commander_session_id` | (required) | Session ID of the commander's Claude Code session |
| `heartbeat_interval` | `60` | Seconds between heartbeat pings |

### Extra Sessions (Config-Based)

For sessions that aren't created through the UI, add them to `config.yaml`:

```yaml
extra_sessions:
  - name: "my-project"
    path: "/home/user/projects/my-project"
```

## Directory Structure

```
server/              FastAPI backend
  routers/           REST + WebSocket endpoints
  store.py           SQLite data layer
  ws_manager.py      WebSocket connection manager
  auth.py            Bearer token auth
  models.py          Pydantic models

agent/               Lightweight Python agent
  agenthq_agent/
    core.py          All agent logic (discovery, heartbeat, terminals, files)
    cli.py           CLI entrypoint

commander/           Telegram bot + MCP tools
  bridge.py          Telegram ↔ AgentHQ relay daemon
  mcp_server.py      MCP tools for the commander Claude session
  session/           Commander session config (CLAUDE.md + .mcp.json)

frontend/            React + TypeScript + Vite
  src/
    pages/           Dashboard + SessionDetail
    components/      TerminalView, FileTree, NewSessionModal, etc.
    hooks/           useWebSocket, useTerminalWebSocket

docker/              Dockerfiles + nginx config
docker-compose.yml
```

## Tech Stack

- **Backend:** Python 3.10+, FastAPI, aiosqlite, uvicorn
- **Frontend:** React 18, TypeScript, Vite, TailwindCSS v4, xterm.js
- **Agent:** Python 3.10+, aiohttp, asyncio, tmux
- **Commander:** Python 3.10+, aiogram (Telegram), MCP protocol
- **Deploy:** Docker Compose, nginx reverse proxy

## How It Works

1. **Agent heartbeat** — Each agent POSTs to `/api/agents/heartbeat` every 10 seconds with its session list and known projects. The server stores this in SQLite and returns any pending commands.

2. **Session creation** — When you click Create in the UI, the server queues a `create_session` command. The agent picks it up on the next heartbeat and spawns a tmux session running Claude Code.

3. **Terminal streaming** — The agent opens a PTY, attaches to the tmux session, and connects via WebSocket to the server. The server relays data between the agent's PTY and the browser's xterm.js. All data is base64-encoded JSON over WebSocket.

4. **File browsing** — The agent connects a files WebSocket per session. When the browser requests a directory listing or file content, the server forwards the request to the agent, which reads from disk and responds.

5. **Session lifecycle** — Stop kills the tmux session and removes it from the agent's managed list. Start/Restart creates a new tmux session. The agent persists managed sessions to `managed_sessions.json` so they survive restarts.

6. **Commander** — The Telegram bridge (`bridge.py`) receives messages, coalesces them in a 3-second window, and relays them via WebSocket to a dedicated Claude Code session. That session has MCP tools (`mcp_server.py`) that can list sessions, read terminal output, send input, create new sessions, and send replies back to Telegram. The user only sees Telegram messages — all Claude responses go through `send_telegram()`.

## License

MIT
