import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, LogOut, Filter, Bot, Plus } from "lucide-react";
import { getSessions } from "../api";
import type { Session } from "../types";
import SessionCard from "../components/SessionCard";
import NewSessionModal from "../components/NewSessionModal";

export default function Dashboard() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [machineFilter, setMachineFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [showNewSession, setShowNewSession] = useState(false);
  const navigate = useNavigate();

  const fetchSessions = useCallback(async () => {
    try {
      const filter: { machine?: string; status?: string } = {};
      if (machineFilter) filter.machine = machineFilter;
      if (statusFilter) filter.status = statusFilter;
      const data = await getSessions(filter);
      setSessions(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch sessions");
    } finally {
      setLoading(false);
    }
  }, [machineFilter, statusFilter]);

  useEffect(() => {
    fetchSessions();
    const interval = setInterval(fetchSessions, 5000);
    return () => clearInterval(interval);
  }, [fetchSessions]);

  const machines = Array.from(new Set(sessions.map((s) => s.machine))).sort();

  const grouped = sessions.reduce<Record<string, Session[]>>((acc, s) => {
    const key = s.machine || "Unknown";
    if (!acc[key]) acc[key] = [];
    acc[key].push(s);
    return acc;
  }, {});

  function handleLogout() {
    localStorage.removeItem("agenthq_token");
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-3">
              <Bot className="w-6 h-6 text-slate-400" />
              <h1 className="text-lg font-semibold text-slate-100">AgentHQ</h1>
              <span className="text-sm text-slate-500">
                {sessions.length} session{sessions.length !== 1 ? "s" : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowNewSession(true)}
                className="p-2 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="New Session"
              >
                <Plus className="w-4 h-4" />
              </button>
              <button
                onClick={fetchSessions}
                className="p-2 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
              <button
                onClick={handleLogout}
                className="p-2 text-slate-400 hover:text-slate-200 rounded-lg hover:bg-slate-800 transition-colors"
                title="Sign out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <Filter className="w-4 h-4 text-slate-500" />
          <select
            value={machineFilter}
            onChange={(e) => setMachineFilter(e.target.value)}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm
                       text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All machines</option>
            {machines.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm
                       text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="idle">Idle</option>
            <option value="error">Error</option>
          </select>
        </div>

        {/* Content */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <RefreshCw className="w-6 h-6 text-slate-500 animate-spin" />
          </div>
        )}

        {error && (
          <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && sessions.length === 0 && (
          <div className="text-center py-20">
            <Bot className="w-12 h-12 text-slate-700 mx-auto mb-3" />
            <p className="text-slate-500">No sessions found</p>
            <p className="text-slate-600 text-sm mt-1">
              Sessions will appear once agents start reporting
            </p>
          </div>
        )}

        {!loading &&
          Object.entries(grouped)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([machine, machineSessions]) => (
              <div key={machine} className="mb-8">
                <div className="flex items-center gap-2 mb-3">
                  <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider">
                    {machine}
                  </h2>
                  <span className="text-xs text-slate-600">
                    ({machineSessions.length})
                  </span>
                  {machineSessions[0]?.agent_version && (
                    <span className="px-1.5 py-0.5 bg-slate-800 rounded text-xs text-slate-500 font-mono">
                      v{machineSessions[0].agent_version}
                    </span>
                  )}
                  {machineSessions[0] && !machineSessions[0].agent_version && (
                    <span className="px-1.5 py-0.5 bg-amber-900/30 border border-amber-800/50 rounded text-xs text-amber-500 font-mono">
                      version unknown
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {machineSessions.map((session) => (
                    <SessionCard key={session.id} session={session} onDeleted={fetchSessions} />
                  ))}
                </div>
              </div>
            ))}
      </main>

      {showNewSession && (
        <NewSessionModal
          onClose={() => setShowNewSession(false)}
          onCreated={() => {
            setShowNewSession(false);
            fetchSessions();
          }}
        />
      )}
    </div>
  );
}
