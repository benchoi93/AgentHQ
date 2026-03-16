import { useNavigate } from "react-router-dom";
import { Circle, Clock, FolderOpen, Trash2 } from "lucide-react";
import type { Session } from "../types";
import { deleteSession } from "../api";

const STATUS_COLORS: Record<string, string> = {
  running: "text-status-running",
  idle: "text-status-idle",
  stopped: "text-status-error",
  offline: "text-slate-500",
  error: "text-status-error",
};

const STATUS_BG: Record<string, string> = {
  running: "bg-green-500/10",
  idle: "bg-yellow-500/10",
  stopped: "bg-red-500/10",
  offline: "bg-slate-500/10",
  error: "bg-red-500/10",
};

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

interface SessionCardProps {
  session: Session;
  onDeleted?: () => void;
}

export default function SessionCard({ session, onDeleted }: SessionCardProps) {
  const navigate = useNavigate();

  async function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm(`Delete session "${session.project}"?`)) return;
    try {
      await deleteSession(session.id);
      onDeleted?.();
    } catch {
      // silently ignore — next refresh will show current state
    }
  }

  return (
    <div
      onClick={() => navigate(`/session/${session.id}`)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && navigate(`/session/${session.id}`)}
      className="w-full text-left bg-slate-900 border border-slate-800 rounded-lg p-4
                 hover:border-slate-700 hover:shadow-lg hover:shadow-slate-950/50
                 transition-all duration-150 group cursor-pointer relative"
    >
      {/* Delete button */}
      <button
        onClick={handleDelete}
        className="absolute top-2 right-2 p-1.5 text-slate-600 hover:text-red-400
                   rounded-lg hover:bg-slate-800 transition-colors opacity-0 group-hover:opacity-100"
        title="Delete session"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>

      {/* Top row: project name + status */}
      <div className="flex items-start justify-between gap-2 mb-3 pr-6">
        <h3 className="text-sm font-medium text-slate-200 truncate group-hover:text-slate-100">
          {session.project}
        </h3>
        <span
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0
                      ${STATUS_COLORS[session.status]} ${STATUS_BG[session.status]}`}
        >
          <Circle className="w-2 h-2 fill-current" />
          {session.status}
        </span>
      </div>

      {/* Working directory */}
      {session.path && (
        <div className="flex items-center gap-1.5 mb-2 text-xs text-slate-500 truncate">
          <FolderOpen className="w-3 h-3 flex-shrink-0" />
          <span className="truncate">{session.path}</span>
        </div>
      )}

      {/* Bottom row: metadata */}
      <div className="flex items-center gap-3 text-xs text-slate-500 mt-2">
        {session.provider && (
          <span className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400">
            {session.provider}
          </span>
        )}
        {session.model && (
          <span className="truncate">{session.model}</span>
        )}
        <span>PID {session.pid}</span>
        <span className="flex items-center gap-1 ml-auto flex-shrink-0">
          <Clock className="w-3 h-3" />
          {relativeTime(session.last_activity)}
        </span>
      </div>
    </div>
  );
}
