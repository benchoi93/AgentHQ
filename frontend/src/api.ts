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

export async function restartSession(id: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${id}/restart`, {
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

export function getWsUrl(path: string): string {
  const token = localStorage.getItem("agenthq_token") || "";
  const base = BASE_URL.replace(/^http/, "ws");
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}
