import * as vscode from "vscode";
import { AgentHqClient } from "./client";
import { SessionsTreeProvider } from "./tree";
import { Callback, Session } from "./types";

// Polls /api/sessions/{id}/callbacks for every known session and surfaces
// new "completed" / "error" reports as VS Code toasts. Maintains a status
// bar item showing total session count and any unacked callbacks. We
// remember which callback IDs we've already notified about in-memory so a
// single new report doesn't fire repeatedly across polls.

export class CallbackWatcher {
  private timer: NodeJS.Timeout | undefined;
  private seen = new Set<number>();
  private bootstrapped = false;
  private statusBar: vscode.StatusBarItem;

  constructor(
    private readonly client: AgentHqClient,
    private readonly tree: SessionsTreeProvider,
  ) {
    this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.statusBar.command = "agenthq.refresh";
    this.statusBar.tooltip = "AgentHQ — click to refresh";
    this.statusBar.show();
  }

  start(): void {
    void this.tick();
    const cfg = vscode.workspace.getConfiguration("agenthq");
    const intervalSec = Math.max(5, cfg.get<number>("callbackPollSec") ?? 15);
    this.timer = setInterval(() => void this.tick(), intervalSec * 1000);
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    this.statusBar.dispose();
  }

  private async tick(): Promise<void> {
    const sessions = this.tree.getSessions();
    this.updateStatusBar(sessions);
    if (sessions.length === 0) return;

    // Limit concurrent fan-out. We only need fresh notifications, not a
    // full history sync, so a small `limit` per session keeps the load light.
    const results = await Promise.allSettled(
      sessions.map((s) => this.client.listCallbacks(s.id, 5).then((cbs) => ({ s, cbs }))),
    );

    const fresh: { session: Session; callback: Callback }[] = [];
    for (const r of results) {
      if (r.status !== "fulfilled") continue;
      for (const cb of r.value.cbs) {
        if (this.seen.has(cb.id)) continue;
        this.seen.add(cb.id);
        if (this.bootstrapped) fresh.push({ session: r.value.s, callback: cb });
      }
    }

    if (!this.bootstrapped) {
      // First poll: mark everything as already-seen so we only toast
      // callbacks that arrive *after* the extension activates.
      this.bootstrapped = true;
      return;
    }

    for (const { session, callback } of fresh) {
      this.notify(session, callback);
    }
  }

  private notify(session: Session, cb: Callback): void {
    const label = `${session.project || session.id} (${session.machine}): ${cb.summary}`;
    if (cb.status === "error") {
      vscode.window.showErrorMessage(`AgentHQ · ${label}`);
    } else if (cb.status === "completed") {
      vscode.window.showInformationMessage(`AgentHQ ✓ ${label}`);
    } else if (cb.status === "in_progress") {
      // Quietly update status bar; no toast for in-progress to avoid noise
      this.statusBar.text = `$(sync~spin) AgentHQ · ${session.project || session.id}`;
    }
  }

  private updateStatusBar(sessions: Session[]): void {
    const running = sessions.filter((s) => s.status === "running").length;
    const total = sessions.length;
    this.statusBar.text = `$(server-process) AgentHQ ${running}/${total}`;
  }
}
