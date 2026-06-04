# AgentHQ VS Code Extension (Phase 1)

Connects VS Code to an [AgentHQ](../README.md) server so you can:

- See every agent and session in a sidebar tree
- Attach a VS Code terminal to any running session (talks to the agent's tmux PTY over the same WebSocket protocol as the web dashboard)
- Start a session in the current workspace folder on a chosen agent
- Stop / restart / delete sessions from the tree's context menu
- Get toasts when sessions report `completed` or `error` callbacks
- See `running/total` count in the status bar

This is **Phase 1 — thin client.** No backend changes required.

## Phase 2 (planned)

Add file editing via `vscode.FileSystemProvider` backed by an extended
`/ws/files/{id}` protocol with `write` and `watch` request types. Until
then, browse files in the existing web dashboard or via `Remote-SSH`.

## Build

```bash
cd vscode-extension
npm install
npm run compile
```

Then in VS Code: `F5` to launch the Extension Development Host, or
`npm run package` (after `npm i -g @vscode/vsce`) to build a `.vsix`
installable via "Extensions: Install from VSIX…".

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `agenthq.serverUrl` | `http://localhost:30001` | Base URL of your AgentHQ server. Use the same one you open in the browser. |
| `agenthq.refreshIntervalSec` | `10` | How often to poll `/api/agents` + `/api/sessions`. |
| `agenthq.callbackPollSec` | `15` | How often to poll session callbacks for toast notifications. |

Token is stored in `vscode.SecretStorage`. Run **AgentHQ: Set API Token**
from the command palette (Ctrl/Cmd-Shift-P) to set it; **AgentHQ: Clear
API Token** wipes it.

## Wire protocol

Terminal WS is identical to the browser hook
(`frontend/src/hooks/useTerminalWebSocket.ts`):

```jsonc
// client → server
{"type": "input",  "data": "<base64-utf8>"}
{"type": "resize", "cols": 80, "rows": 24}

// server → client
{"type": "output", "data": "<base64-raw-pty-bytes>"}
```

If you change the browser hook's protocol, mirror the change in
`src/terminal.ts` — there is no shared types package between them.
