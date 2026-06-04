import * as vscode from "vscode";
import WebSocket from "ws";
import { AgentHqClient } from "./client";
import { Session } from "./types";

// Wire protocol (mirrors frontend/src/hooks/useTerminalWebSocket.ts):
//   client→server: {type:"input", data:"<base64-utf8>"} | {type:"resize", cols, rows}
//   server→client: {type:"output", data:"<base64-raw-pty-bytes>"}
//
// Keep this in lockstep with the browser hook — any protocol change in
// useTerminalWebSocket needs a matching change here.

const RECONNECT_MS = 3000;
const MAX_RECONNECTS = 10;
// Match the browser's keystroke coalescing window (frontend uses 4ms).
// Bigger = fewer messages during paste, smaller = snappier feedback.
const INPUT_COALESCE_MS = 4;

interface TerminalEntry {
  terminal: vscode.Terminal;
  pty: AgentHqPty;
}

const openTerminals = new Map<string, TerminalEntry>();

export async function attachToSession(
  client: AgentHqClient,
  session: Session,
): Promise<void> {
  const existing = openTerminals.get(session.id);
  if (existing) {
    existing.terminal.show();
    return;
  }
  const wsUrl = await client.wsUrl(`/ws/terminal/${encodeURIComponent(session.id)}`);
  if (!wsUrl) {
    vscode.window.showErrorMessage("AgentHQ token not set. Run 'AgentHQ: Set API Token'.");
    return;
  }
  const pty = new AgentHqPty(wsUrl, session);
  const terminal = vscode.window.createTerminal({
    name: `AgentHQ · ${session.project || session.id}`,
    pty,
    iconPath: new vscode.ThemeIcon("server-process"),
  });
  openTerminals.set(session.id, { terminal, pty });
  pty.onTerminalDisposed(() => openTerminals.delete(session.id));
  terminal.show();
}

class AgentHqPty implements vscode.Pseudoterminal {
  private readonly writeEmitter = new vscode.EventEmitter<string>();
  private readonly closeEmitter = new vscode.EventEmitter<number>();
  readonly onDidWrite = this.writeEmitter.event;
  readonly onDidClose = this.closeEmitter.event;

  private ws: WebSocket | undefined;
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | undefined;
  private disposed = false;
  private dims: vscode.TerminalDimensions | undefined;

  // Input coalescing — matches the browser's pattern of buffering rapid
  // keystrokes and flushing on a short timer, so single-key typing and
  // pastes both reach the agent as compact messages.
  private inputBuf = "";
  private flushTimer: NodeJS.Timeout | undefined;
  private readonly onDisposeEmitter = new vscode.EventEmitter<void>();
  readonly onTerminalDisposed = this.onDisposeEmitter.event;

  constructor(
    private readonly url: string,
    private readonly session: Session,
  ) {}

  // vscode.Pseudoterminal: called when the terminal is shown for the first time
  open(initialDimensions: vscode.TerminalDimensions | undefined): void {
    if (initialDimensions) this.dims = initialDimensions;
    this.connect();
  }

  // vscode.Pseudoterminal: user closed the terminal panel
  close(): void {
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.flushInput();
    this.ws?.close();
    this.ws = undefined;
    this.onDisposeEmitter.fire();
    this.onDisposeEmitter.dispose();
    this.writeEmitter.dispose();
    this.closeEmitter.dispose();
  }

  // vscode.Pseudoterminal: keystrokes arrive here as strings (escape
  // sequences included). Mirror the browser hook's input coalescing.
  handleInput(data: string): void {
    this.inputBuf += data;
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => this.flushInput(), INPUT_COALESCE_MS);
    }
  }

  // vscode.Pseudoterminal: terminal panel resized
  setDimensions(dimensions: vscode.TerminalDimensions): void {
    this.dims = dimensions;
    this.sendResize(dimensions.columns, dimensions.rows);
  }

  private connect(): void {
    if (this.disposed) return;
    this.writeEmitter.fire(banner(this.session, this.reconnectAttempts > 0));
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.on("open", () => {
      if (this.disposed) {
        ws.close();
        return;
      }
      this.reconnectAttempts = 0;
      if (this.dims) {
        this.sendResize(this.dims.columns, this.dims.rows);
      }
    });

    ws.on("message", (raw) => {
      if (this.disposed) return;
      try {
        const parsed = JSON.parse(raw.toString("utf-8"));
        if (parsed.type === "output" && typeof parsed.data === "string") {
          // Server frames raw PTY bytes as base64. xterm.js writes the
          // decoded bytes directly; VS Code's Pseudoterminal API takes a
          // string and re-interprets escape sequences, so decoding to UTF-8
          // is correct as long as Claude's output is UTF-8 (it is).
          const text = Buffer.from(parsed.data, "base64").toString("utf-8");
          this.writeEmitter.fire(text);
        }
      } catch {
        // Ignore malformed frames; nothing actionable on the client side.
      }
    });

    ws.on("close", () => {
      if (this.disposed) return;
      this.ws = undefined;
      if (this.reconnectAttempts >= MAX_RECONNECTS) {
        this.writeEmitter.fire("\r\n\x1b[31m[AgentHQ] disconnected — max reconnect attempts reached.\x1b[0m\r\n");
        this.closeEmitter.fire(1);
        return;
      }
      this.reconnectAttempts += 1;
      this.reconnectTimer = setTimeout(() => this.connect(), RECONNECT_MS);
    });

    ws.on("error", () => {
      ws.close();
    });
  }

  private flushInput(): void {
    this.flushTimer = undefined;
    const buf = this.inputBuf;
    if (!buf) return;
    this.inputBuf = "";
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const b64 = Buffer.from(buf, "utf-8").toString("base64");
    this.ws.send(JSON.stringify({ type: "input", data: b64 }));
  }

  private sendResize(cols: number, rows: number): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "resize", cols, rows }));
  }
}

function banner(session: Session, reconnecting: boolean): string {
  const tag = reconnecting ? "[AgentHQ] reconnecting…" : "[AgentHQ] connecting…";
  return `\x1b[36m${tag}\x1b[0m  ${session.machine}/${session.project || session.id}\r\n`;
}
