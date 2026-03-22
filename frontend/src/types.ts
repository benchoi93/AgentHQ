export interface Agent {
  id: string;
  name: string;
  machine: string;
  last_seen: string;
  ip?: string;
  agent_version?: string;
}

export interface CreateSessionRequest {
  machine: string;
  directory: string;
  session_name?: string;
}

export interface Session {
  id: string;
  agent_name: string;
  machine: string;
  project: string;
  status: "running" | "idle" | "error" | "stopped" | "offline" | "manual";
  pid: number | null;
  last_activity: string;
  path: string;
  model?: string;
  provider?: string;
  agent_version?: string;
}

export interface LogMessage {
  type: "log";
  content: string;
  timestamp: string;
}

export interface RelayMessage {
  type: "input" | "output";
  content: string;
  timestamp?: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number | null;
}

export interface FileMessage {
  type: "list_response" | "read_response" | "error";
  path: string;
  entries?: FileEntry[];
  content?: string;
  error?: string;
}

export interface TerminalMessage {
  type: "terminal";
  content: string;
  timestamp: number;
}

export interface ProjectSuggestion {
  id: string;
  name: string;
  path: string;
  machine: string;
  last_activity: string;
}
