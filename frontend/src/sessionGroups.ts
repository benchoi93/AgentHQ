// Sidebar / dashboard folders for sessions.
// A session's folder is its manual `group_name` if set, otherwise the parent directory of the
// project (active, papers, proposals, tools, gitsrcs, ...). Folders span machines; rows show a
// machine badge instead. Rows order by sort_order (server-side, set by drag) then name; folder
// order is a per-browser preference (localStorage), unknown folders go last alphabetically.

export interface Groupable {
  machine: string;
  path: string;
  project: string;
  group_name?: string;
  sort_order?: number;
}

/** Parent-directory bucket of a project path; "home" when the parent is a home root. */
export function autoGroup(path: string): string {
  const parts = path.replace(/\\/g, "/").replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length < 2) return "home";
  const parent = parts[parts.length - 2];
  const grand = parts[parts.length - 3] ?? "";
  // /home/<user>/<proj>, /data/<user>/<proj>, /Users/<user>/<proj>, C:/Users/<user>/<proj>
  if (/^(home|data\d*|Users)$/.test(grand)) return "home";
  return parent.toLowerCase();
}

export function effectiveGroup(s: Groupable): string {
  const manual = (s.group_name ?? "").trim();
  return manual || autoGroup(s.path);
}

export function bySortOrder(a: Groupable, b: Groupable): number {
  return (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.project.localeCompare(b.project);
}

const FOLDER_ORDER_KEY = "agenthq_folder_order";
export function getFolderOrder(): string[] {
  try { return JSON.parse(localStorage.getItem(FOLDER_ORDER_KEY) || "[]"); } catch { return []; }
}
export function setFolderOrder(order: string[]): void {
  try { localStorage.setItem(FOLDER_ORDER_KEY, JSON.stringify(order)); } catch { /* ignore */ }
}

/** Sessions bucketed by folder; members by sort_order, folders by saved order then name. */
export function groupSessions<T extends Groupable>(list: T[], folderOrder: string[] = getFolderOrder()): [string, T[]][] {
  const acc = new Map<string, T[]>();
  for (const s of list) {
    const k = effectiveGroup(s);
    if (!acc.has(k)) acc.set(k, []);
    acc.get(k)!.push(s);
  }
  const rank = (name: string) => { const i = folderOrder.indexOf(name); return i < 0 ? Infinity : i; };
  return Array.from(acc.entries())
    .map(([k, v]) => [k, v.sort(bySortOrder)] as [string, T[]])
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}
