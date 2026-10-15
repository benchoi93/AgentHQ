// Sidebar / dashboard folders for sessions.
// A session's folder is its manual `group_name` if set, otherwise the parent directory of the
// project (active, papers, proposals, tools, gitsrcs, ...). Folders span machines; rows show a
// machine badge instead.

export interface Groupable {
  machine: string;
  path: string;
  group_name?: string;
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

/** Sessions bucketed by folder, folders sorted by name. */
export function groupSessions<T extends Groupable>(list: T[]): [string, T[]][] {
  const acc = new Map<string, T[]>();
  for (const s of list) {
    const k = effectiveGroup(s);
    if (!acc.has(k)) acc.set(k, []);
    acc.get(k)!.push(s);
  }
  return Array.from(acc.entries()).sort(([a], [b]) => a.localeCompare(b));
}
