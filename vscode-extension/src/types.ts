// Mirrors of server/models.py types — kept narrow to the fields the
// extension actually reads. Extend as needed if Phase 2 surfaces more.

export interface Agent {
  id: string;
  name: string;
  machine: string;
  last_seen: string;
  ip?: string;
  agent_version?: string;
}

export type SessionStatus =
  | "running"
  | "idle"
  | "error"
  | "stopped"
  | "offline"
  | "manual";

export interface Session {
  id: string;
  agent_name: string;
  machine: string;
  project: string;
  status: SessionStatus | string;
  pid: number | null;
  last_activity: string;
  path: string;
  agent_version?: string;
  account?: string;
}

export interface CreateSessionRequest {
  machine: string;
  directory: string;
  session_name?: string;
  account?: string;
}

export interface ProjectSuggestion {
  id: string;
  name: string;
  path: string;
  machine: string;
  last_activity: string;
}

export interface Callback {
  id: number;
  session_id: string;
  project: string;
  event_type: string;
  status: string;
  summary: string;
  task_id: string | null;
  created_at: string;
  acknowledged: number;
}
