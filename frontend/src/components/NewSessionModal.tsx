import { useState, useEffect, type FormEvent } from "react";
import { X, Loader2, FolderOpen } from "lucide-react";
import { createSession, getAgents, getProjectSuggestions } from "../api";
import type { Agent, ProjectSuggestion } from "../types";

interface NewSessionModalProps {
  onClose: () => void;
  onCreated: () => void;
}

export default function NewSessionModal({ onClose, onCreated }: NewSessionModalProps) {
  const [machine, setMachine] = useState("");
  const [directory, setDirectory] = useState("");
  const [sessionName, setSessionName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [suggestions, setSuggestions] = useState<ProjectSuggestion[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);

  // Fetch agents for machine dropdown
  useEffect(() => {
    getAgents().then(setAgents).catch(() => {});
  }, []);

  // Fetch project suggestions when machine changes
  useEffect(() => {
    if (!machine) {
      setSuggestions([]);
      return;
    }
    setLoadingSuggestions(true);
    getProjectSuggestions(machine)
      .then(setSuggestions)
      .catch(() => setSuggestions([]))
      .finally(() => setLoadingSuggestions(false));
  }, [machine]);

  function selectSuggestion(s: ProjectSuggestion) {
    setDirectory(s.path);
    setSessionName(s.name);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!machine.trim() || !directory.trim()) return;

    setLoading(true);
    setError("");
    try {
      await createSession({
        machine: machine.trim(),
        directory: directory.trim(),
        session_name: sessionName.trim() || undefined,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create session");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h2 className="text-sm font-semibold text-slate-100">New Session</h2>
          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Machine <span className="text-red-400">*</span>
            </label>
            <select
              value={machine}
              onChange={(e) => setMachine(e.target.value)}
              disabled={loading}
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm
                         text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                         disabled:opacity-50"
            >
              <option value="">Select machine...</option>
              {agents.map((a) => (
                <option key={a.id} value={a.machine}>
                  {a.name} ({a.machine})
                </option>
              ))}
            </select>
          </div>

          {/* Project suggestions */}
          {machine && (
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">
                Recent Projects
              </label>
              {loadingSuggestions ? (
                <div className="text-xs text-slate-500 py-2">Loading...</div>
              ) : suggestions.length > 0 ? (
                <div className="max-h-40 overflow-y-auto rounded-lg border border-slate-700 bg-slate-800/50">
                  {suggestions.map((s) => (
                    <button
                      type="button"
                      key={s.id}
                      onClick={() => selectSuggestion(s)}
                      className={`w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-slate-700/50 transition-colors
                                  border-b border-slate-800 last:border-b-0
                                  ${directory === s.path ? "bg-blue-900/20 text-blue-300" : "text-slate-300"}`}
                    >
                      <FolderOpen className="w-3 h-3 flex-shrink-0 text-slate-500" />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium truncate">{s.name}</div>
                        <div className="text-[10px] text-slate-500 truncate">{s.path}</div>
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-slate-600 py-1">No project history found</div>
              )}
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Directory <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={directory}
              onChange={(e) => setDirectory(e.target.value)}
              placeholder="e.g. /home/user/project"
              disabled={loading}
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm
                         text-slate-100 placeholder:text-slate-500
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                         disabled:opacity-50"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Session Name <span className="text-slate-600">(optional)</span>
            </label>
            <input
              type="text"
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
              placeholder="e.g. my-feature-branch"
              disabled={loading}
              className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm
                         text-slate-100 placeholder:text-slate-500
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                         disabled:opacity-50"
            />
          </div>

          {error && (
            <div className="bg-red-900/30 border border-red-800 rounded-lg px-3 py-2 text-red-300 text-xs">
              {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 text-sm text-slate-300 hover:text-slate-100 rounded-lg
                         hover:bg-slate-800 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !machine.trim() || !directory.trim()}
              className="px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white
                         rounded-lg transition-colors disabled:bg-slate-700 disabled:text-slate-500
                         disabled:cursor-not-allowed flex items-center gap-2"
            >
              {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {loading ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
