import * as vscode from "vscode";
import { Auth } from "./auth";
import {
  Agent,
  Callback,
  CreateSessionRequest,
  ProjectSuggestion,
  Session,
} from "./types";

// Thin REST client for AgentHQ. WebSocket connections live in their
// own modules (terminal.ts today; files.ts when Phase 2 lands) because
// they need streaming + reconnect semantics distinct from one-shot HTTP.

export class AgentHqClient {
  constructor(private readonly auth: Auth) {}

  baseUrl(): string {
    const cfg = vscode.workspace.getConfiguration("agenthq");
    const raw = (cfg.get<string>("serverUrl") || "").trim();
    return raw.replace(/\/$/, "");
  }

  wsBaseUrl(): string {
    return this.baseUrl().replace(/^http/i, "ws");
  }

  // Build the URL for a WebSocket endpoint with the token query param
  // attached. Returns undefined if no token is set yet.
  async wsUrl(path: string): Promise<string | undefined> {
    const token = await this.auth.getToken();
    if (!token) return undefined;
    const base = this.wsBaseUrl();
    const sep = path.includes("?") ? "&" : "?";
    return `${base}${path}${sep}token=${encodeURIComponent(token)}&role=client`;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = await this.auth.getToken();
    if (!token) {
      throw new Error("AgentHQ token not set. Run 'AgentHQ: Set API Token'.");
    }
    const base = this.baseUrl();
    if (!base) {
      throw new Error("AgentHQ: server URL not set. Open Settings → search 'agenthq.serverUrl'.");
    }
    const url = `${base}${path}`;
    const headers: Record<string, string> = {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      ...((init?.headers as Record<string, string>) || {}),
    };
    let res: Response;
    try {
      res = await fetch(url, { ...init, headers });
    } catch (err) {
      // Node's fetch (undici) throws a bare "fetch failed" TypeError for
      // anything from DNS failures to TLS errors. Surface the URL and the
      // underlying cause so the user knows where to look.
      const cause = (err as { cause?: { code?: string; message?: string } }).cause;
      const detail = cause?.code || cause?.message || (err instanceof Error ? err.message : String(err));
      throw new Error(
        `AgentHQ: cannot reach ${url} (${detail}). Check 'agenthq.serverUrl' setting and that the server is reachable from this machine.`,
      );
    }
    if (res.status === 401) {
      throw new Error(`AgentHQ: 401 unauthorized at ${url}. Token may be wrong — run 'AgentHQ: Set API Token'.`);
    }
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`AgentHQ ${res.status} ${res.statusText} (${url})${body ? `: ${body}` : ""}`);
    }
    // DELETE may legitimately return empty body
    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return undefined as unknown as T;
    }
    return res.json() as Promise<T>;
  }

  listAgents(): Promise<Agent[]> {
    return this.request<Agent[]>("/api/agents");
  }

  listSessions(filter?: { machine?: string; status?: string }): Promise<Session[]> {
    const q = new URLSearchParams();
    if (filter?.machine) q.set("machine", filter.machine);
    if (filter?.status) q.set("status", filter.status);
    const qs = q.toString();
    return this.request<Session[]>(`/api/sessions${qs ? `?${qs}` : ""}`);
  }

  getSession(id: string): Promise<Session> {
    return this.request<Session>(`/api/sessions/${encodeURIComponent(id)}`);
  }

  createSession(req: CreateSessionRequest): Promise<{ ok: boolean; command_id: number }> {
    return this.request("/api/sessions/create", {
      method: "POST",
      body: JSON.stringify(req),
    });
  }

  deleteSession(id: string): Promise<void> {
    return this.request<void>(`/api/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  restartSession(id: string, account?: string): Promise<void> {
    return this.request<void>(`/api/sessions/${encodeURIComponent(id)}/restart`, {
      method: "POST",
      body: account ? JSON.stringify({ account }) : "{}",
    });
  }

  stopSession(id: string): Promise<void> {
    return this.request<void>(`/api/sessions/${encodeURIComponent(id)}/stop`, {
      method: "POST",
    });
  }

  listCallbacks(sessionId: string, limit = 10): Promise<Callback[]> {
    return this.request<Callback[]>(
      `/api/sessions/${encodeURIComponent(sessionId)}/callbacks?limit=${limit}`,
    );
  }

  projectSuggestions(machine?: string): Promise<ProjectSuggestion[]> {
    const qs = machine ? `?machine=${encodeURIComponent(machine)}` : "";
    return this.request<ProjectSuggestion[]>(`/api/sessions/suggestions/projects${qs}`);
  }
}
