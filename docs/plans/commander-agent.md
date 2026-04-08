# 대장 (Commander) Agent — Telegram ↔ AgentHQ Router

**Status**: In Progress
**Date**: 2026-03-20
**Author**: Seongjin Choi

## Overview

A commander agent that bridges Telegram and AgentHQ sessions. The user messages it on Telegram, it routes to the right Claude Code session, delivers the message, monitors progress, and streams summarized updates back to Telegram.

The key design choice: **대장 is itself a Claude Code session** — routing, summarization, and notification decisions are handled by Claude's intelligence, not hand-coded rules.

## Architecture

```
Telegram
    ↕  Telegram Bot API
Bridge Service (aiogram, thin, stateless)
    ↕  AgentHQ WebSocket relay (/ws/relay/{commander_session_id})
AgentHQ Server
    ↕
대장 Session (Claude Code + MCP server for AgentHQ tools)
    ↕  MCP tool calls → AgentHQ REST/WS API
Target Sessions
```

### Three Components

### 1. Bridge Service (thin, stateless)

Translates between Telegram and AgentHQ's WebSocket relay. No intelligence.

- Telegram message in → forward to 대장 session via `/ws/relay/{commander_session_id}` as `{"type": "input", "content": "..."}`
- 대장 output on relay → send to user's Telegram chat
- Single allowed Telegram user ID from config
- Runs as a long-lived async Python service
- Sends periodic heartbeat (`[heartbeat] check active tasks`) to 대장 every 60s to trigger proactive monitoring

### 2. MCP Server for AgentHQ Tools

Gives 대장 its "hands." An MCP server exposing:

| Tool | Description |
|------|-------------|
| `list_sessions` | List all active sessions with project, status, machine |
| `get_session_output(session_id, lines?)` | Read recent terminal output from a session |
| `send_to_session(session_id, text)` | Send input to a target session via relay WS |
| `get_session_status(session_id)` | Check if session is busy/idle/error |
| `send_telegram(text)` | Send a message to user on Telegram (for proactive notifications) |

### 3. 대장 Session (Claude Code)

A Claude Code session with:
- The MCP server attached (via `.mcp.json` or `--mcp`)
- A CLAUDE.md system prompt defining its commander role
- `--dangerously-skip-permissions` for autonomous operation

## Proactive Monitoring — Heartbeat (Option 2)

The bridge service sends a periodic `[heartbeat] check active tasks` message to 대장 via the relay every ~60s. 대장 receives it as input and decides whether to poll session outputs and send notifications. All intelligence about "should I notify?" lives in Claude's judgment via CLAUDE.md prompt.

## Notification Style

- **On completion**: Summarize what was done and the result
- **On error**: Alert immediately with the error and assessment
- **Long tasks (>2 min)**: Brief progress update every 2-3 min
- **Idle**: Don't send anything
- **Format**: Concise Telegram messages, minimal emoji (✅ ❌ ⏳)

## Routing Strategy

1. Match by project name or keywords in the message
2. If ambiguous, ask the user
3. If no matching session, inform user (session creation not in MVP)
4. Sticky context — remember recent conversation session

## Interaction Examples

```
User (Telegram): "run the tests in traffic-sim"

대장:
  1. list_sessions → finds "traffic-sim" (abc123, idle)
  2. send_to_session(abc123, "run pytest")
  3. Polls get_session_output periodically

대장 → Telegram: "⏳ Running pytest in traffic-sim..."
  ... 2 min later ...
대장 → Telegram: "✅ traffic-sim: 42/42 tests passed (1m47s)"
```

```
User (Telegram): "what's going on?"

대장:
  1. list_sessions → 3 sessions active
  2. get_session_output for each

대장 → Telegram:
  "📊 Status:
   • traffic-sim: idle, last activity 5m ago
   • model-training: running, epoch 34/100
   • paper-draft: idle, last edit to methodology.tex"
```

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 대장 polling vs push | Polling via heartbeat | Simpler; 대장 decides when to check |
| Output format | 대장 summarizes | Claude summarizes terminal → human-readable Telegram |
| Notification trigger | 대장 judges | Sees output, decides "error" or "completed" — no regex |
| Session creation | Not in MVP | User creates via UI; add later |
| Multi-user | Not in MVP | Single Telegram user ID allowlist |
| Routing intelligence | Claude-based | 대장 matches project names, asks if ambiguous |

## Future Enhancements

- Voice messages: Telegram voice → Whisper → route to session
- File sharing: Screenshots from Telegram → session
- Session creation: "Start a new session for ~/projects/new-thing"
- Multi-machine awareness: "What's running on the lab server?"
- Notification hooks: Sessions proactively notify on completion
- Multi-user support: Map Telegram IDs to AgentHQ permissions
- Claude Code Channels integration when out of research preview

## Tech Stack

- **Bridge**: Python, aiogram (async Telegram bot), aiohttp (WebSocket client)
- **MCP Server**: Python, mcp SDK (`@modelcontextprotocol/sdk` or python `mcp`)
- **대장 Session**: Claude Code with custom MCP + CLAUDE.md
- **Config**: YAML (Telegram token, AgentHQ URL, user ID, heartbeat interval)
