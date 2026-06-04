import * as vscode from "vscode";
import { AgentHqClient } from "./client";
import { SessionsTreeProvider, SessionNode } from "./tree";

// Lifecycle commands. Each one resolves the target Session either from a
// TreeView selection (right-click) or via a QuickPick when invoked from
// the command palette, so every command works in both contexts.

export async function createSessionFromWorkspace(client: AgentHqClient, tree: SessionsTreeProvider): Promise<void> {
  const folder = await pickWorkspaceFolder();
  if (!folder) return;
  const agent = await pickAgent(client);
  if (!agent) return;
  const name = await vscode.window.showInputBox({
    prompt: "Session name (optional)",
    placeHolder: "Leave blank to auto-name",
  });
  if (name === undefined) return;

  try {
    await client.createSession({
      machine: agent.machine,
      directory: folder.uri.fsPath,
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

async function pickWorkspaceFolder(): Promise<vscode.WorkspaceFolder | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) {
    vscode.window.showErrorMessage("AgentHQ: open a folder first — create-session needs a directory path.");
    return undefined;
  }
  if (folders.length === 1) return folders[0];
  const pick = await vscode.window.showQuickPick(
    folders.map((f) => ({ label: f.name, description: f.uri.fsPath, folder: f })),
    { placeHolder: "Workspace folder to use as session directory" },
  );
  return pick?.folder;
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
