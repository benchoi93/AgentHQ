import * as vscode from "vscode";
import { Auth } from "./auth";
import { AgentHqClient } from "./client";
import { CallbackWatcher } from "./callbacks";
import { AgentHqFileSystemProvider, SCHEME as FS_SCHEME, showLog as showFsLog } from "./files";
import {
  createNewSession,
  deleteSession,
  restartSession,
  stopSession,
} from "./lifecycle";
import { attachToSession } from "./terminal";
import { SessionsTreeProvider, SessionNode } from "./tree";

let tree: SessionsTreeProvider | undefined;
let watcher: CallbackWatcher | undefined;
let fsProvider: AgentHqFileSystemProvider | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const auth = new Auth(context.secrets);
  const client = new AgentHqClient(auth);
  tree = new SessionsTreeProvider(client);
  watcher = new CallbackWatcher(client, tree);
  fsProvider = new AgentHqFileSystemProvider(client);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("agenthq.sessions", tree),
    // isCaseSensitive: agent runs on Linux/macOS where paths are case-sensitive.
    // We accept the small mismatch on Windows agents (rare today) rather than
    // silently lowercasing identifiers and losing files.
    vscode.workspace.registerFileSystemProvider(FS_SCHEME, fsProvider, {
      isCaseSensitive: true,
    }),
    { dispose: () => tree?.dispose() },
    { dispose: () => watcher?.dispose() },
    { dispose: () => fsProvider?.dispose() },
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("agenthq.refresh", () => tree?.refresh()),
    vscode.commands.registerCommand("agenthq.showLog", () => showFsLog()),

    vscode.commands.registerCommand("agenthq.setToken", async () => {
      const entered = await vscode.window.showInputBox({
        prompt: "AgentHQ API token",
        password: true,
        ignoreFocusOut: true,
        placeHolder: "Paste your AGENTHQ_TOKEN here",
      });
      if (!entered) return;
      await auth.setToken(entered.trim());
      vscode.window.showInformationMessage("AgentHQ: token saved.");
      tree?.refresh();
    }),

    vscode.commands.registerCommand("agenthq.clearToken", async () => {
      await auth.clearToken();
      vscode.window.showInformationMessage("AgentHQ: token cleared.");
      tree?.refresh();
    }),

    vscode.commands.registerCommand("agenthq.attachTerminal", async (arg?: SessionNode) => {
      const session = arg?.session ?? (await pickSessionForAttach(client));
      if (!session) return;
      await attachToSession(client, session);
    }),

    vscode.commands.registerCommand("agenthq.createSession", () =>
      createNewSession(client, tree!),
    ),
    vscode.commands.registerCommand("agenthq.openWorkspace", async (arg?: SessionNode) => {
      const session = arg?.session ?? (await pickSessionForAttach(client));
      if (!session) return;
      const uri = vscode.Uri.parse(`${FS_SCHEME}://${encodeURIComponent(session.id)}/`);
      const name = `AgentHQ · ${session.project || session.id} (${session.machine})`;
      // Open as a new workspace folder so the Explorer treats it as the
      // workspace root. forceNewWindow keeps the current window untouched
      // — multiple sessions can be open in parallel windows.
      await vscode.commands.executeCommand("vscode.openFolder", uri, {
        forceNewWindow: true,
        noRecentEntry: false,
      });
      // Note: openFolder either replaces the workspace or opens a new
      // window; control doesn't reliably return here, so any cleanup we
      // need must happen before this call.
      void name;
    }),
    vscode.commands.registerCommand("agenthq.stopSession", (n?: SessionNode) =>
      stopSession(client, tree!, n),
    ),
    vscode.commands.registerCommand("agenthq.restartSession", (n?: SessionNode) =>
      restartSession(client, tree!, n),
    ),
    vscode.commands.registerCommand("agenthq.deleteSession", (n?: SessionNode) =>
      deleteSession(client, tree!, n),
    ),

    vscode.workspace.onDidChangeConfiguration((e) => {
      if (
        e.affectsConfiguration("agenthq.serverUrl") ||
        e.affectsConfiguration("agenthq.refreshIntervalSec")
      ) {
        // Re-init the tree's poll cadence and pick up a new server URL on
        // the next refresh — no extension reload required.
        tree?.refresh();
      }
    }),
  );

  // Token-gated startup: if no token is set yet, prompt once. The user can
  // dismiss; the welcome view tells them how to set it later.
  const token = await auth.getToken();
  if (!token) {
    vscode.window
      .showInformationMessage(
        "AgentHQ: no API token configured.",
        "Set Token",
      )
      .then((choice) => {
        if (choice === "Set Token") {
          vscode.commands.executeCommand("agenthq.setToken");
        }
      });
  }

  tree.start();
  watcher.start();
}

async function pickSessionForAttach(client: AgentHqClient) {
  const sessions = await client.listSessions().catch((err) => {
    vscode.window.showErrorMessage(`AgentHQ: ${err instanceof Error ? err.message : String(err)}`);
    return [];
  });
  if (sessions.length === 0) return undefined;
  const pick = await vscode.window.showQuickPick(
    sessions.map((s) => ({
      label: `$(server-process) ${s.project || s.id}`,
      description: `${s.machine} · ${s.status}`,
      detail: s.path,
      session: s,
    })),
    { placeHolder: "Attach terminal to which session?", matchOnDescription: true, matchOnDetail: true },
  );
  return pick?.session;
}

export function deactivate(): void {
  tree?.dispose();
  watcher?.dispose();
  fsProvider?.dispose();
}
