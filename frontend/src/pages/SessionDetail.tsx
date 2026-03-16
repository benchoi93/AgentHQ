import { useEffect, useState, useRef, useCallback, lazy, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Circle,
  RefreshCw,
  RotateCcw,
  Play,
  Square,
  FolderOpen,
  PanelLeftClose,
  PanelLeft,
  X,
} from "lucide-react";
import { getSession, getSessions, getWsUrl, restartSession, stopSession } from "../api";
import type { Session, FileMessage } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";
import FileTree from "../components/FileTree";
import FileViewer from "../components/FileViewer";

const TerminalView = lazy(() => import("../components/TerminalView"));

const STATUS_COLORS: Record<string, string> = {
  running: "text-status-running",
  idle: "text-status-idle",
  stopped: "text-status-error",
  error: "text-status-error",
};

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [session, setSession] = useState<Session | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [actionPending, setActionPending] = useState<string | null>(null);

  const fileReloadRef = useRef<(() => void) | null>(null);

  const filesWsUrl = id ? getWsUrl(`/ws/files/${id}`) : null;
  const terminalWsUrl = id ? getWsUrl(`/ws/terminal/${id}`) : null;

  const files = useWebSocket<FileMessage>({ url: filesWsUrl });

  const handleReload = useCallback(() => {
    setReloadKey((k) => k + 1);
    fileReloadRef.current?.();
  }, []);

  const handleAction = useCallback(async (action: "restart" | "stop" | "start") => {
    if (!id || actionPending) return;
    setActionPending(action);
    try {
      if (action === "stop") {
        await stopSession(id);
      } else {
        // Both "start" and "restart" use the restart endpoint
        await restartSession(id);
      }
      // Wait for agent to process, then reload
      setTimeout(() => {
        setReloadKey((k) => k + 1);
        setActionPending(null);
      }, 2500);
    } catch {
      setActionPending(null);
    }
  }, [id, actionPending]);

  // Fetch current session + poll for status updates
  // Also resets state when id changes (consolidated to avoid race conditions)
  useEffect(() => {
    if (!id) return;
    // Reset state for the new session
    setSelectedFile(null);
    setSession(null);
    setError("");
    setLoading(true);
    setReloadKey((k) => k + 1);

    let cancelled = false;
    async function fetchDetail() {
      try {
        const data = await getSession(id!);
        if (!cancelled) {
          setSession(data);
          setError("");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load session");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchDetail();
    const interval = setInterval(fetchDetail, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [id]);

  // Fetch all sessions for the sidebar
  useEffect(() => {
    let cancelled = false;
    async function fetchSessions() {
      try {
        const data = await getSessions();
        if (!cancelled) setSessions(data);
      } catch { /* ignore */ }
    }
    fetchSessions();
    const interval = setInterval(fetchSessions, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  if (loading) {
    return (
      <div className="h-screen bg-slate-950 flex items-center justify-center">
        <RefreshCw className="w-6 h-6 text-slate-500 animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen bg-slate-950 flex items-center justify-center">
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm max-w-md">
          {error}
        </div>
      </div>
    );
  }

  const isRunning = session?.status === "running";
  const isStopped = session?.status === "stopped" || session?.status === "offline";

  return (
    <div className="h-screen bg-slate-950 flex flex-col overflow-hidden">
      {/* Header bar */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm flex-shrink-0">
        <div className="px-3">
          <div className="flex items-center h-11 gap-2">
            <button
              onClick={() => navigate("/")}
              className="p-1 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => setSidebarOpen((v) => !v)}
              className="p-1 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
              title={sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            >
              {sidebarOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeft className="w-4 h-4" />}
            </button>
            <div className="w-px h-5 bg-slate-800" />
            {session && (
              <>
                <Circle
                  className={`w-2 h-2 flex-shrink-0 fill-current ${STATUS_COLORS[session.status] || "text-slate-500"}`}
                />
                <span className="text-sm font-medium text-slate-200 truncate">
                  {session.project}
                </span>
                <div className="flex items-center gap-1.5 text-xs text-slate-500 ml-auto flex-shrink-0">
                  {/* Session control buttons */}
                  {isStopped ? (
                    <button
                      onClick={() => handleAction("start")}
                      disabled={!!actionPending}
                      title="Start session"
                      className="flex items-center gap-1 px-2 py-1 rounded text-green-400 hover:text-green-300 hover:bg-green-900/30 transition-colors disabled:opacity-50"
                    >
                      <Play className={`w-3 h-3 ${actionPending === "start" ? "animate-pulse" : ""}`} />
                      <span className="text-[11px]">{actionPending === "start" ? "Starting..." : "Start"}</span>
                    </button>
                  ) : isRunning ? (
                    <>
                      <button
                        onClick={() => handleAction("restart")}
                        disabled={!!actionPending}
                        title="Restart session"
                        className="flex items-center gap-1 px-2 py-1 rounded text-orange-400 hover:text-orange-300 hover:bg-orange-900/30 transition-colors disabled:opacity-50"
                      >
                        <RotateCcw className={`w-3 h-3 ${actionPending === "restart" ? "animate-spin" : ""}`} />
                        <span className="text-[11px]">{actionPending === "restart" ? "Restarting..." : "Restart"}</span>
                      </button>
                      <button
                        onClick={() => handleAction("stop")}
                        disabled={!!actionPending}
                        title="Stop session"
                        className="flex items-center gap-1 px-2 py-1 rounded text-red-400 hover:text-red-300 hover:bg-red-900/30 transition-colors disabled:opacity-50"
                      >
                        <Square className={`w-3 h-3 ${actionPending === "stop" ? "animate-pulse" : ""}`} />
                        <span className="text-[11px]">{actionPending === "stop" ? "Stopping..." : "Stop"}</span>
                      </button>
                    </>
                  ) : null}
                  <span className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400 text-[11px]">
                    {session.machine}
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden">

        {sidebarOpen && (
          <>
            {/* === COL 1: Session / project list === */}
            <div className="w-48 flex-shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/30">
              <div className="px-3 py-1.5 border-b border-slate-800 flex-shrink-0">
                <span className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
                  Sessions
                </span>
              </div>
              <div className="flex-1 overflow-y-auto">
                {Object.entries(
                  sessions.reduce<Record<string, typeof sessions>>((acc, s) => {
                    const key = s.machine || "Unknown";
                    if (!acc[key]) acc[key] = [];
                    acc[key].push(s);
                    return acc;
                  }, {})
                )
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([machineName, machineSessions]) => (
                    <div key={machineName}>
                      <div className="px-3 py-1 bg-slate-900/60 border-b border-slate-800/50 sticky top-0">
                        <span className="text-[9px] font-semibold text-slate-600 uppercase tracking-widest">
                          {machineName}
                        </span>
                      </div>
                      {machineSessions.map((s) => (
                        <button
                          key={s.id}
                          onClick={() => {
                            if (s.id === id) {
                              setReloadKey((k) => k + 1);
                              fileReloadRef.current?.();
                            } else {
                              navigate(`/session/${s.id}`);
                            }
                          }}
                          className={`w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors border-l-2
                                     ${s.id === id
                                       ? "bg-slate-800/60 border-l-blue-500 text-slate-200"
                                       : "border-l-transparent text-slate-400 hover:bg-slate-800/30 hover:text-slate-300"
                                     }`}
                        >
                          <Circle
                            className={`w-1.5 h-1.5 flex-shrink-0 fill-current ${STATUS_COLORS[s.status] || "text-slate-600"}`}
                          />
                          <div className="text-xs font-medium truncate">{s.project}</div>
                        </button>
                      ))}
                    </div>
                  ))}
              </div>
            </div>

            {/* === COL 2: File tree === */}
            <div className="w-52 flex-shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/20">
              <div className="px-3 py-1.5 border-b border-slate-800 flex items-center gap-1.5 flex-shrink-0">
                <FolderOpen className="w-3 h-3 text-slate-500" />
                <span className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
                  Files
                </span>
                <button
                  onClick={() => fileReloadRef.current?.()}
                  title="Reload file tree"
                  className="ml-auto p-0.5 rounded text-slate-500 hover:text-slate-300 hover:bg-slate-700/50 transition-colors"
                >
                  <RefreshCw className="w-3 h-3" />
                </button>
                <Circle
                  className={`w-1.5 h-1.5 ${files.connected ? "text-green-500 fill-green-500" : "text-slate-700 fill-slate-700"}`}
                />
              </div>
              <div className="flex-1 min-h-0">
                <FileTree
                  key={id}
                  messages={files.messages}
                  sendMessage={files.sendMessage}
                  connected={files.connected}
                  selectedFile={selectedFile}
                  onSelectFile={setSelectedFile}
                  reloadRef={fileReloadRef}
                />
              </div>
            </div>
          </>
        )}

        {/* === MAIN: split top/bottom — file viewer + terminal === */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">

          {/* File viewer (top half, only when file is open) */}
          {selectedFile && (
            <div className="flex-1 flex flex-col min-h-0 border-b border-slate-800">
              <div className="flex items-center px-3 py-1 border-b border-slate-800 bg-slate-900/40 flex-shrink-0">
                <span className="text-[11px] text-slate-400 font-mono truncate flex-1">
                  {selectedFile}
                </span>
                <button
                  onClick={() => setSelectedFile(null)}
                  className="p-0.5 text-slate-600 hover:text-slate-300 transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
              <div className="flex-1 min-h-0">
                <FileViewer
                  messages={files.messages}
                  sendMessage={files.sendMessage}
                  selectedFile={selectedFile}
                  onCloseFile={() => setSelectedFile(null)}
                />
              </div>
            </div>
          )}

          {/* Terminal (bottom half, or full height if no file) */}
          <div className="flex-1 flex flex-col min-h-0">
            <div className="flex items-center border-b border-slate-800 flex-shrink-0 px-2">
              <span className="text-[11px] font-medium text-slate-400 px-1 py-1.5">Terminal</span>
              <button
                onClick={handleReload}
                title="Reload"
                className="ml-auto p-1 rounded text-slate-600 hover:text-slate-300 hover:bg-slate-700/50 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
              </button>
            </div>
            <div className="flex-1 min-h-0">
              {isStopped ? (
                <div className="h-full flex flex-col items-center justify-center text-slate-500 text-sm gap-3">
                  <Square className="w-8 h-8 text-slate-600" />
                  <p>Session stopped</p>
                  <button
                    onClick={() => handleAction("start")}
                    disabled={!!actionPending}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    <Play className="w-4 h-4" />
                    {actionPending === "start" ? "Starting..." : "Start Session"}
                  </button>
                </div>
              ) : (
                <Suspense fallback={<div className="h-full flex items-center justify-center text-slate-500 text-sm">Loading terminal...</div>}>
                  <TerminalView key={`term-${id}-${reloadKey}`} wsUrl={terminalWsUrl} />
                </Suspense>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
