import * as vscode from "vscode";
import { AgentHqClient } from "./client";
import { SessionsTreeProvider, SessionNode } from "./tree";

// Lifecycle commands. Each one resolves the target Session either from a
// TreeView selection (right-click) or via a QuickPick when invoked from
// the command palette, so every command works in both contexts.

export async function createNewSession(client: AgentHqClient, tree: SessionsTreeProvider): Promise<void> {
  // Pick the agent first — the directory must be a path on the agent's
  // filesystem, not the local VS Code workspace. (Workspace folders are
  // useless when VS Code is on Windows and the agent runs on Linux.)
  const agent = await pickAgent(client);
  if (!agent) return;

  const directory = await pickRemoteDirectory(client, agent.machine);
  if (!directory) return;

  const name = await vscode.window.showInputBox({
    prompt: `Session name on ${agent.machine} (optional)`,
    placeHolder: "Leave blank to auto-name from the directory",
  });
  if (name === undefined) return;

  try {
    await client.createSession({
      machine: agent.machine,
      directory,
      session_name: name || undefined,
    });
    vscode.window.showInformationMessage(
      `AgentHQ: session creation requested on ${agent.machine}. It will appear once the agent picks up the command.`,
    );
    setTimeout(() => tree.refresh(), 1500);
  } catch (err) {
    vscode.window.showErrorMessage(`AgentHQ: ${asError(err).message}`);
  }
}

// Two-step picker: show recent projects on the chosen agent, plus an
// "Enter a different path..." escape hatch for new directories. Mirrors
// the NewSessionModal flow in the web dashboard.
async function pickRemoteDirectory(client: AgentHqClient, machine: string): Promise<string | undefined> {
  const suggestions = await client.projectSuggestions(machine).catch(() => []);

  interface Item extends vscode.QuickPickItem {
    tag: "manual" | "suggestion";
    path?: string;
  }
  const manualItem: Item = {
    label: "$(edit) Enter a directory path manually…",
    description: `on ${machine}`,
    tag: "manual",
  };
  const suggestionItems: Item[] = suggestions.map((s): Item => ({
    label: `$(folder) ${s.name}`,
    description: s.path,
    detail: s.last_activity ? `last activity: ${new Date(s.last_activity).toLocaleString()}` : undefined,
    tag: "suggestion",
    path: s.path,
  }));
  const items: Item[] = [manualItem, ...suggestionItems];

  const pick = await vscode.window.showQuickPick<Item>(items, {
    placeHolder: `Directory on ${machine} — pick a recent project or enter a new path`,
    matchOnDescription: true,
    matchOnDetail: true,
  });
  if (!pick) return undefined;
  if (pick.tag === "suggestion") return pick.path;

  return vscode.window.showInputBox({
    prompt: `Absolute directory path on ${machine}`,
    placeHolder: machine.toLowerCase().includes("win") ? "C:\\path\\to\\project" : "/home/user/project",
    validateInput: (v) => (v.trim() ? undefined : "Directory cannot be empty"),
  });
}

export async function stopSession(client: AgentHqClient, tree: SessionsTreeProvider, node?: SessionNode): Promise<void> {
  const session = node?.session ?? (await pickSession(tree, "Stop which session?"));
  if (!session) return;
  try {
    await client.stopSession(session.id);
    setTimeout(() => tree.refresh(), 500);
  } catch (err) {
    vscode.window.showErrorMessage(`AgentHQ: ${asError(err).message}`);
  }
}

export async function restartSession(client: AgentHqClient, tree: SessionsTreeProvider, node?: SessionNode): Promise<void> {
  const session = node?.session ?? (await pickSession(tree, "Restart which session?"));
  if (!session) return;
  try {
    await client.restartSession(session.id);
    setTimeout(() => tree.refresh(), 500);
  } catch (err) {
    vscode.window.showErrorMessage(`AgentHQ: ${asError(err).message}`);
  }
}

export async function deleteSession(client: AgentHqClient, tree: SessionsTreeProvider, node?: SessionNode): Promise<void> {
  const session = node?.session ?? (await pickSession(tree, "Delete which session?"));
  if (!session) return;
  const yes = await vscode.window.showWarningMessage(
    `Delete session "${session.project || session.id}" on ${session.machine}? This removes it from the server registry.`,
    { modal: true },
    "Delete",
  );
  if (yes !== "Delete") return;
  try {
    await client.deleteSession(session.id);
    setTimeout(() => tree.refresh(), 500);
  } catch (err) {
    vscode.window.showErrorMessage(`AgentHQ: ${asError(err).message}`);
  }
}

async function pickAgent(client: AgentHqClient) {
  const agents = await client.listAgents();
  if (agents.length === 0) {
    vscode.window.showErrorMessage("AgentHQ: no agents registered.");
    return undefined;
  }
  if (agents.length === 1) return agents[0];
  const pick = await vscode.window.showQuickPick(
    agents.map((a) => ({
      label: a.machine,
      description: a.name,
      detail: `last seen: ${new Date(a.last_seen).toLocaleString()}`,
      agent: a,
    })),
    { placeHolder: "Which agent should host the session?" },
  );
  return pick?.agent;
}

async function pickSession(tree: SessionsTreeProvider, placeHolder: string) {
  const sessions = tree.getSessions();
  if (sessions.length === 0) {
    vscode.window.showInformationMessage("AgentHQ: no sessions to pick from.");
    return undefined;
  }
  const pick = await vscode.window.showQuickPick(
    sessions.map((s) => ({
      label: `$(server-process) ${s.project || s.id}`,
      description: `${s.machine} · ${s.status}`,
      detail: s.path,
      session: s,
    })),
    { placeHolder, matchOnDescription: true, matchOnDetail: true },
  );
  return pick?.session;
}

function asError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e));
}
