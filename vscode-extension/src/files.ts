import * as vscode from "vscode";
import WebSocket from "ws";
import { AgentHqClient } from "./client";

// URI scheme: agenthq://<session-id>/<path-inside-session-root>
//
// The session ID lives in the URI authority; the path is relative to the
// agent session's base directory. The agent enforces the boundary check
// (see _safe_resolve in agent/agenthq_agent/core.py), so even a hostile
// path like "../../etc/passwd" is rejected at the agent.
//
// Wire protocol (mirrors agent/agenthq_agent/core.py: files_for_session):
//   client→server:
//     {type:"stat",  path}
//     {type:"list",  path}                                     (existing)
//     {type:"read_bytes", path}
//     {type:"write", path, data:<base64>, create, overwrite}
//     {type:"delete", path, recursive}
//     {type:"mkdir", path}
//     {type:"rename", path, new_path, overwrite}
//   server→client:
//     {type:"stat_response", path, kind, size, mtime, ctime}
//     {type:"list_response", path, entries:[{name,path,type,size}]}
//     {type:"read_bytes_response", path, data:<base64>}
//     {type:"write_response", path, size, mtime}
//     {type:"delete_response", path}
//     {type:"mkdir_response", path}
//     {type:"rename_response", path, new_path}
//     {type:"error", op?, path, error}
//
// Responses are correlated by (type-prefix, path) FIFO. VS Code's
// FileSystemProvider does not normally fire concurrent ops on the same
// (type, path) pair, so this is sufficient — see issueRequest below.

const REQUEST_TIMEOUT_MS = 20_000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15_000;

export const SCHEME = "agenthq";

type Pending = {
  expect: string; // response type to match
  resolve: (msg: any) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
};

class SessionFiles {
  private ws: WebSocket | undefined;
  private connecting: Promise<WebSocket> | undefined;
  private reconnectAttempts = 0;
  private disposed = false;
  // FIFO queue per (responseType, path) — see issueRequest.
  private readonly pending = new Map<string, Pending[]>();

  constructor(
    private readonly client: AgentHqClient,
    readonly sessionId: string,
  ) {}

  dispose(): void {
    this.disposed = true;
    this.ws?.close();
    this.ws = undefined;
    for (const queue of this.pending.values()) {
      for (const p of queue) {
        clearTimeout(p.timer);
        p.reject(new vscode.FileSystemError("Provider disposed"));
      }
    }
    this.pending.clear();
  }

  private async getSocket(): Promise<WebSocket> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return this.ws;
    if (this.connecting) return this.connecting;
    this.connecting = this.connect();
    try {
      const ws = await this.connecting;
      return ws;
    } finally {
      this.connecting = undefined;
    }
  }

  private async connect(): Promise<WebSocket> {
    if (this.disposed) throw new vscode.FileSystemError("Provider disposed");
    const url = await this.client.wsUrl(`/ws/files/${encodeURIComponent(this.sessionId)}`);
    if (!url) throw new vscode.FileSystemError("AgentHQ token not set");
    return await new Promise<WebSocket>((resolve, reject) => {
      const ws = new WebSocket(url);
      const onError = (err: Error) => {
        ws.removeAllListeners();
        reject(err);
      };
      ws.once("open", () => {
        ws.removeListener("error", onError);
        this.ws = ws;
        this.reconnectAttempts = 0;
        ws.on("message", (raw) => this.onMessage(raw));
        ws.on("close", () => this.onClose());
        ws.on("error", () => ws.close());
        resolve(ws);
      });
      ws.once("error", onError);
    });
  }

  private onClose(): void {
    this.ws = undefined;
    if (this.disposed) return;
    // Fail in-flight requests so VS Code surfaces a real error rather
    // than spinning indefinitely. Subsequent ops will trigger reconnect.
    for (const queue of this.pending.values()) {
      for (const p of queue) {
        clearTimeout(p.timer);
        p.reject(new vscode.FileSystemError("AgentHQ files connection closed"));
      }
    }
    this.pending.clear();
    // Backoff reconnect — but only when there's demand, so don't proactively
    // re-open here. getSocket() will call connect() on the next op.
    this.reconnectAttempts += 1;
  }

  private onMessage(raw: WebSocket.RawData): void {
    let msg: any;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      return;
    }
    const type = String(msg.type || "");
    const path = String(msg.path ?? "");
    if (type === "error") {
      // Error responses don't carry the original op string in the existing
      // protocol — match on (path, *any-op*) by popping from any queue
      // whose key prefix matches. Tier 1: try queues with `op` field if
      // present; Tier 2: take the first queue holding this path.
      const op = String(msg.op || "");
      if (op) {
        const key = `${responseTypeForOp(op)}:${path}`;
        if (this.popAndReject(key, msg.error || "AgentHQ error")) return;
      }
      // Fallback: reject the oldest pending request for this path.
      for (const [key, queue] of this.pending) {
        if (!key.endsWith(`:${path}`)) continue;
        const p = queue.shift();
        if (queue.length === 0) this.pending.delete(key);
        clearTimeout(p!.timer);
        p!.reject(toFsError(msg.error || "AgentHQ error", path));
        return;
      }
      return;
    }
    const key = `${type}:${path}`;
    const queue = this.pending.get(key);
    if (!queue || queue.length === 0) return;
    const p = queue.shift()!;
    if (queue.length === 0) this.pending.delete(key);
    clearTimeout(p.timer);
    p.resolve(msg);
  }

  private popAndReject(key: string, err: string): boolean {
    const queue = this.pending.get(key);
    if (!queue || queue.length === 0) return false;
    const p = queue.shift()!;
    if (queue.length === 0) this.pending.delete(key);
    clearTimeout(p.timer);
    p.reject(toFsError(err, key.split(":").slice(1).join(":")));
    return true;
  }

  private async issueRequest<T>(
    payload: Record<string, unknown>,
    expectType: string,
    path: string,
  ): Promise<T> {
    const ws = await this.getSocket();
    return await new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        // Remove this pending from its queue on timeout to avoid leaks.
        const key = `${expectType}:${path}`;
        const queue = this.pending.get(key);
        if (queue) {
          const idx = queue.findIndex((p) => p.timer === timer);
          if (idx >= 0) queue.splice(idx, 1);
          if (queue.length === 0) this.pending.delete(key);
        }
        reject(new vscode.FileSystemError(`AgentHQ: ${payload.type} timed out`));
      }, REQUEST_TIMEOUT_MS);
      const key = `${expectType}:${path}`;
      const queue = this.pending.get(key) ?? [];
      queue.push({ expect: expectType, resolve: resolve as any, reject, timer });
      this.pending.set(key, queue);
      ws.send(JSON.stringify({ ...payload, path }));
    });
  }

  stat(path: string): Promise<StatResponse> {
    return this.issueRequest<StatResponse>({ type: "stat" }, "stat_response", path);
  }

  list(path: string): Promise<ListResponse> {
    return this.issueRequest<ListResponse>({ type: "list" }, "list_response", path);
  }

  readBytes(path: string): Promise<ReadBytesResponse> {
    return this.issueRequest<ReadBytesResponse>({ type: "read_bytes" }, "read_bytes_response", path);
  }

  write(path: string, dataB64: string, create: boolean, overwrite: boolean): Promise<WriteResponse> {
    return this.issueRequest<WriteResponse>(
      { type: "write", data: dataB64, create, overwrite },
      "write_response",
      path,
    );
  }

  delete(path: string, recursive: boolean): Promise<{ path: string }> {
    return this.issueRequest({ type: "delete", recursive }, "delete_response", path);
  }

  mkdir(path: string): Promise<{ path: string }> {
    return this.issueRequest({ type: "mkdir" }, "mkdir_response", path);
  }

  rename(src: string, dst: string, overwrite: boolean): Promise<{ path: string; new_path: string }> {
    return this.issueRequest(
      { type: "rename", new_path: dst, overwrite },
      "rename_response",
      src,
    );
  }
}

interface StatResponse {
  type: "stat_response";
  path: string;
  kind: "file" | "directory";
  size: number;
  mtime: number;
  ctime: number;
}

interface ListResponse {
  type: "list_response";
  path: string;
  entries: { name: string; path: string; type: "file" | "directory"; size?: number | null }[];
}

interface ReadBytesResponse {
  type: "read_bytes_response";
  path: string;
  data: string; // base64
}

interface WriteResponse {
  type: "write_response";
  path: string;
  size: number;
  mtime: number;
}

function responseTypeForOp(op: string): string {
  switch (op) {
    case "stat":
      return "stat_response";
    case "list":
      return "list_response";
    case "read_bytes":
      return "read_bytes_response";
    case "write":
      return "write_response";
    case "delete":
      return "delete_response";
    case "mkdir":
      return "mkdir_response";
    case "rename":
      return "rename_response";
    default:
      return "";
  }
}

function toFsError(msg: string, path: string): vscode.FileSystemError {
  const m = msg.toLowerCase();
  if (m.includes("not found") || m.includes("source not found")) {
    return vscode.FileSystemError.FileNotFound(path);
  }
  if (m.includes("access denied") || m.includes("permission denied")) {
    return vscode.FileSystemError.NoPermissions(path);
  }
  if (m.includes("not a file")) {
    return vscode.FileSystemError.FileIsADirectory(path);
  }
  if (m.includes("not a directory")) {
    return vscode.FileSystemError.FileNotADirectory(path);
  }
  if (m.includes("already exists") || m.includes("file exists") || m.includes("destination exists")) {
    return vscode.FileSystemError.FileExists(path);
  }
  return new vscode.FileSystemError(msg);
}

// Strip the leading "/" that vscode.Uri.path always carries and normalise
// "" → "." (the convention the existing frontend uses for the root). The
// agent's _safe_resolve treats both as the session base, but matching the
// frontend convention keeps the wire traffic uniform.
function pathFromUri(uri: vscode.Uri): string {
  const p = uri.path.replace(/^\/+/, "");
  return p === "" ? "." : p;
}

export class AgentHqFileSystemProvider implements vscode.FileSystemProvider {
  private readonly sessions = new Map<string, SessionFiles>();
  private readonly emitter = new vscode.EventEmitter<vscode.FileChangeEvent[]>();
  readonly onDidChangeFile = this.emitter.event;

  constructor(private readonly client: AgentHqClient) {}

  dispose(): void {
    for (const s of this.sessions.values()) s.dispose();
    this.sessions.clear();
    this.emitter.dispose();
  }

  private sessionFor(uri: vscode.Uri): SessionFiles {
    const sid = uri.authority;
    if (!sid) {
      throw new vscode.FileSystemError("AgentHQ URI missing session id (authority).");
    }
    let s = this.sessions.get(sid);
    if (!s) {
      s = new SessionFiles(this.client, sid);
      this.sessions.set(sid, s);
    }
    return s;
  }

  // We don't currently push agent-side file change events, so watch is a
  // no-op. VS Code will fall back to user-triggered refresh / save-on-edit.
  watch(_uri: vscode.Uri): vscode.Disposable {
    return new vscode.Disposable(() => {});
  }

  async stat(uri: vscode.Uri): Promise<vscode.FileStat> {
    const s = this.sessionFor(uri);
    const path = pathFromUri(uri);
    const r = await s.stat(path);
    return {
      type: r.kind === "directory" ? vscode.FileType.Directory : vscode.FileType.File,
      ctime: r.ctime,
      mtime: r.mtime,
      size: r.size,
    };
  }

  async readDirectory(uri: vscode.Uri): Promise<[string, vscode.FileType][]> {
    const s = this.sessionFor(uri);
    const r = await s.list(pathFromUri(uri));
    return r.entries.map((e) => [
      e.name,
      e.type === "directory" ? vscode.FileType.Directory : vscode.FileType.File,
    ]);
  }

  async readFile(uri: vscode.Uri): Promise<Uint8Array> {
    const s = this.sessionFor(uri);
    const r = await s.readBytes(pathFromUri(uri));
    return Uint8Array.from(Buffer.from(r.data, "base64"));
  }

  async writeFile(
    uri: vscode.Uri,
    content: Uint8Array,
    options: { create: boolean; overwrite: boolean },
  ): Promise<void> {
    const s = this.sessionFor(uri);
    const data = Buffer.from(content).toString("base64");
    await s.write(pathFromUri(uri), data, options.create, options.overwrite);
    this.emitter.fire([{ type: vscode.FileChangeType.Changed, uri }]);
  }

  async delete(uri: vscode.Uri, options: { recursive: boolean }): Promise<void> {
    const s = this.sessionFor(uri);
    await s.delete(pathFromUri(uri), options.recursive);
    this.emitter.fire([{ type: vscode.FileChangeType.Deleted, uri }]);
  }

  async createDirectory(uri: vscode.Uri): Promise<void> {
    const s = this.sessionFor(uri);
    await s.mkdir(pathFromUri(uri));
    this.emitter.fire([{ type: vscode.FileChangeType.Created, uri }]);
  }

  async rename(
    oldUri: vscode.Uri,
    newUri: vscode.Uri,
    options: { overwrite: boolean },
  ): Promise<void> {
    if (oldUri.authority !== newUri.authority) {
      throw new vscode.FileSystemError("AgentHQ: cannot rename across sessions");
    }
    const s = this.sessionFor(oldUri);
    await s.rename(pathFromUri(oldUri), pathFromUri(newUri), options.overwrite);
    this.emitter.fire([
      { type: vscode.FileChangeType.Deleted, uri: oldUri },
      { type: vscode.FileChangeType.Created, uri: newUri },
    ]);
  }
}
