import * as vscode from "vscode";
import { AgentHqClient } from "./client";
import { Agent, Session } from "./types";

// Two-level tree: agents (machine roots) → sessions hosted on that agent.
// Sessions whose agent is offline (machine has no live Agent record) get
// grouped under a synthetic "(no live agent)" root so they remain visible
// for delete/cleanup actions.

type Node = AgentNode | SessionNode | OfflineGroupNode | vscode.TreeItem;

export class AgentNode extends vscode.TreeItem {
  readonly kind = "agent" as const;
  constructor(public readonly agent: Agent, sessionCount: number) {
    super(
      `${agent.machine}  ·  ${sessionCount} session${sessionCount === 1 ? "" : "s"}`,
      vscode.TreeItemCollapsibleState.Expanded,
    );
    this.id = `agent:${agent.id}`;
    this.contextValue = "agent";
    this.iconPath = new vscode.ThemeIcon("server");
    const seen = agent.last_seen ? new Date(agent.last_seen).toLocaleString() : "?";
    this.tooltip = `${agent.name} (${agent.machine})\nLast seen: ${seen}${agent.ip ? `\nIP: ${agent.ip}` : ""}`;
  }
}

export class OfflineGroupNode extends vscode.TreeItem {
  readonly kind = "offline-group" as const;
  constructor(machine: string, count: number) {
    super(`${machine}  ·  (no live agent, ${count})`, vscode.TreeItemCollapsibleState.Collapsed);
    this.id = `offline:${machine}`;
    this.contextValue = "offline-agent";
    this.iconPath = new vscode.ThemeIcon("server-environment");
  }
}

export class SessionNode extends vscode.TreeItem {
  readonly kind = "session" as const;
  constructor(public readonly session: Session) {
    super(session.project || session.id, vscode.TreeItemCollapsibleState.None);
    this.id = `session:${session.id}`;
    this.contextValue = "session";
    this.description = `${session.status}${session.account ? ` · ${session.account}` : ""}`;
    this.tooltip = [
      `id: ${session.id}`,
      `machine: ${session.machine}`,
      `path: ${session.path}`,
      `status: ${session.status}`,
      session.pid ? `pid: ${session.pid}` : null,
      session.last_activity ? `last activity: ${new Date(session.last_activity).toLocaleString()}` : null,
    ]
      .filter(Boolean)
      .join("\n");
    this.iconPath = iconForStatus(session.status);
    this.command = {
      command: "agenthq.attachTerminal",
      title: "Attach Terminal",
      arguments: [this],
    };
  }
}

function iconForStatus(status: string): vscode.ThemeIcon {
  switch (status) {
    case "running":
      return new vscode.ThemeIcon("circle-filled", new vscode.ThemeColor("charts.green"));
    case "idle":
      return new vscode.ThemeIcon("circle-outline", new vscode.ThemeColor("charts.yellow"));
    case "error":
      return new vscode.ThemeIcon("error", new vscode.ThemeColor("charts.red"));
    case "stopped":
      return new vscode.ThemeIcon("circle-slash", new vscode.ThemeColor("charts.foreground"));
    case "offline":
      return new vscode.ThemeIcon("debug-disconnect", new vscode.ThemeColor("charts.foreground"));
    case "manual":
      return new vscode.ThemeIcon("person");
    default:
      return new vscode.ThemeIcon("circle-outline");
  }
}

export class SessionsTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined | void>();
  readonly onDidChangeTreeData = this.emitter.event;

  private agents: Agent[] = [];
  private sessions: Session[] = [];
  private lastError: string | undefined;
  private pollTimer: NodeJS.Timeout | undefined;

  constructor(private readonly client: AgentHqClient) {}

  start(): void {
    this.refresh();
    const cfg = vscode.workspace.getConfiguration("agenthq");
    const intervalSec = Math.max(2, cfg.get<number>("refreshIntervalSec") ?? 10);
    this.pollTimer = setInterval(() => this.refresh(), intervalSec * 1000);
  }

  dispose(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.emitter.dispose();
  }

  async refresh(): Promise<void> {
    try {
      const [agents, sessions] = await Promise.all([
        this.client.listAgents(),
        this.client.listSessions(),
      ]);
      this.agents = agents;
      this.sessions = sessions;
      this.lastError = undefined;
    } catch (err) {
      this.lastError = err instanceof Error ? err.message : String(err);
    }
    this.emitter.fire();
  }

  getSessions(): Session[] {
    return this.sessions;
  }

  getTreeItem(element: Node): vscode.TreeItem {
    return element;
  }

  getChildren(element?: Node): Node[] {
    if (this.lastError && !element) {
      // Surface the error as a single read-only item so the user sees why
      // the tree is empty without having to dig into the output channel.
      const item = new vscode.TreeItem(
        `Error: ${this.lastError}`,
        vscode.TreeItemCollapsibleState.None,
      );
      item.iconPath = new vscode.ThemeIcon("warning");
      return [item as unknown as Node];
    }

    if (!element) {
      const liveMachines = new Set(this.agents.map((a) => a.machine));
      const agentNodes: Node[] = this.agents.map(
        (a) =>
          new AgentNode(
            a,
            this.sessions.filter((s) => s.machine === a.machine).length,
          ),
      );
      const orphanMachines = new Set(
        this.sessions.filter((s) => !liveMachines.has(s.machine)).map((s) => s.machine),
      );
      for (const m of orphanMachines) {
        agentNodes.push(
          new OfflineGroupNode(
            m,
            this.sessions.filter((s) => s.machine === m).length,
          ),
        );
      }
      return agentNodes;
    }

    if (element instanceof AgentNode) {
      return this.sessions
        .filter((s) => s.machine === element.agent.machine)
        .sort(sessionSort)
        .map((s) => new SessionNode(s));
    }

    if (element instanceof OfflineGroupNode) {
      // OfflineGroupNode's id is `offline:<machine>` — parse back the machine name
      const machine = element.id?.replace(/^offline:/, "") || "";
      return this.sessions
        .filter((s) => s.machine === machine)
        .sort(sessionSort)
        .map((s) => new SessionNode(s));
    }
    return [];
  }
}

function sessionSort(a: Session, b: Session): number {
  // Sort by status priority then project name. Running first, then idle,
  // then everything else — matches what the dashboard surfaces.
  const order: Record<string, number> = { running: 0, idle: 1, error: 2, manual: 3, stopped: 4, offline: 5 };
  const ao = order[a.status] ?? 9;
  const bo = order[b.status] ?? 9;
  if (ao !== bo) return ao - bo;
  return (a.project || a.id).localeCompare(b.project || b.id);
}
