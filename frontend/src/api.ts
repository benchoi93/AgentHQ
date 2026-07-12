import type { Agent, Session, CreateSessionRequest, ProjectSuggestion } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL || window.location.origin;

function getHeaders(): HeadersInit {
  const token = localStorage.getItem("agenthq_token");
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: getHeaders() });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function getAgents(): Promise<Agent[]> {
  return request<Agent[]>("/api/agents");
}

export function getSessions(filter?: {
  machine?: string;
  status?: string;
}): Promise<Session[]> {
  const params = new URLSearchParams();
  if (filter?.machine) params.set("machine", filter.machine);
  if (filter?.status) params.set("status", filter.status);
  const qs = params.toString();
  return request<Session[]>(`/api/sessions${qs ? `?${qs}` : ""}`);
}

export function getSession(id: string): Promise<Session> {
  return request<Session>(`/api/sessions/${id}`);
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${id}`, {
    method: "DELETE",
    headers: getHeaders(),
  });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
}

export async function createSession(req: CreateSessionRequest): Promise<{ ok: boolean; command_id: number }> {
  const res = await fetch(`${BASE_URL}/api/sessions/create`, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify(req),
  });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function restartSession(id: string, account?: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${id}/restart`, {
    method: "POST",
    headers: { ...getHeaders(), "Content-Type": "application/json" },
    body: account ? JSON.stringify({ account }) : undefined,
  });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
}

export async function stopSession(id: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${id}/stop`, {
    method: "POST",
    headers: getHeaders(),
  });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
}

export async function pinSession(id: string, pinned: boolean): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${id}/pin`, {
    method: "POST",
    headers: { ...getHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ pinned }),
  });
  if (res.status === 401) {
    localStorage.removeItem("agenthq_token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
}

export function getProjectSuggestions(machine?: string): Promise<ProjectSuggestion[]> {
  const params = new URLSearchParams();
  if (machine) params.set("machine", machine);
  const qs = params.toString();
  return request<ProjectSuggestion[]>(`/api/sessions/suggestions/projects${qs ? `?${qs}` : ""}`);
}

export interface SessionActivity {
  is_working: boolean
  last_output_age_sec: number | null
}

export function getSessionActivity(): Promise<Record<string, SessionActivity>> {
  return request<Record<string, SessionActivity>>('/api/sessions/activity')
}

export interface UsageModelBreakdown {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  message_count: number;
}

export interface UsageCurrentResponse {
  window_start: string;
  window_end: string;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_tokens: number;
  total_cache_read_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  message_count: number;
  burn_rate_tokens_per_min: number;
  burn_rate_cost_per_hour: number;
  by_model: Record<string, UsageModelBreakdown>;
  by_machine?: Record<string, UsageModelBreakdown>;
  plan_limits: Record<string, { token_limit: number; cost_limit: number }>;
}

export interface UsageHourlyEntry {
  hour: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  cost_usd: number;
  message_count: number;
  by_model: Record<string, any>;
}

export interface UsageDailyEntry {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  message_count: number;
}

export interface UsageHistoryResponse {
  hours: UsageHourlyEntry[];
  daily: UsageDailyEntry[];
}

export function getUsageCurrent(machine?: string): Promise<UsageCurrentResponse> {
  const params = new URLSearchParams();
  if (machine) params.set('machine', machine);
  const qs = params.toString();
  return request<UsageCurrentResponse>(`/api/usage/current${qs ? `?${qs}` : ''}`);
}

export function getUsageHistory(hours?: number, machine?: string): Promise<UsageHistoryResponse> {
  const params = new URLSearchParams();
  if (hours) params.set('hours', hours.toString());
  if (machine) params.set('machine', machine);
  const qs = params.toString();
  return request<UsageHistoryResponse>(`/api/usage/history${qs ? `?${qs}` : ''}`);
}

export function getTerminalText(sessionId: string): Promise<{ text: string }> {
  return request<{ text: string }>(`/api/sessions/${sessionId}/terminal-text`);
}

export function getWsUrl(path: string): string {
  const token = localStorage.getItem("agenthq_token") || "";
  const base = BASE_URL.replace(/^http/, "ws");
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}
