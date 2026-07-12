import { useEffect, useState, useRef, useCallback, lazy, Suspense, useMemo } from "react";
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
  Plus,
  X,
  ClipboardCopy,
  Pin,
} from "lucide-react";
import { createSession, deleteSession, getSession, getSessions, getTerminalText, getWsUrl, pinSession, restartSession, stopSession } from "../api";
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
  // Default sidebar closed on mobile (< 768px)
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 768);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [actionPending, setActionPending] = useState<string | null>(null);

  const fileReloadRef = useRef<(() => void) | null>(null);
  const [termTextModal, setTermTextModal] = useState<string | null>(null);
  const termTextRef = useRef<HTMLTextAreaElement>(null);

  const filesWsUrl = id ? getWsUrl(`/ws/files/${id}`) : null;

  const files = useWebSocket<FileMessage>({ url: filesWsUrl });

  // Deduplicated sidebar: one entry per unique path+machine. Prefer a
  // pinned session so toggling pin on the visible row matches the row
  // shown across dedup re-runs.
  const sidebarSessions = useMemo(() => {
    const seen = new Map<string, Session>();
    for (const s of sessions) {
      const key = `${s.machine}:${s.path}`;
      const prev = seen.get(key);
      if (!prev || (s.pinned && !prev.pinned)) {
        seen.set(key, s);
      }
    }
    return Array.from(seen.values());
  }, [sessions]);

  const handleTogglePin = useCallback(async (s: Session) => {
    const next = !s.pinned;
    // Optimistic update on every session sharing this id.
    setSessions((prev) =>
      prev.map((x) => (x.id === s.id ? { ...x, pinned: next } : x))
    );
    try {
      await pinSession(s.id, next);
    } catch {
      // Revert on error.
      setSessions((prev) =>
        prev.map((x) => (x.id === s.id ? { ...x, pinned: !next } : x))
      );
    }
  }, []);

  // All sibling terminals for the current session (same path+machine, running).
  // Primary (URL) session is always first; siblings follow in API order.
  const terminalIds = useMemo(() => {
    if (!id) return [];
    if (!session) return [id];
    const siblings = sessions
      .filter(s => s.path === session.path && s.machine === session.machine && s.status === "running" && s.id !== id)
      .map(s => s.id);
    return [id, ...siblings];
  }, [session, sessions, id]);

  // Grid class based on terminal count (single column on mobile)
  const gridClass = useMemo(() => {
    const n = terminalIds.length;
    if (n <= 1) return "grid-cols-1 grid-rows-1";
    if (n === 2) return "grid-cols-1 sm:grid-cols-2 grid-rows-1";
    return "grid-cols-1 sm:grid-cols-2 sm:grid-rows-2"; // 3 or 4
  }, [terminalIds.length]);

  // Smaller font when multiple terminals share the grid
  const termFontSize = terminalIds.length > 1 ? 11 : 13;

  const handleReload = useCallback(() => {
    setReloadKey((k) => k + 1);
    fileReloadRef.current?.();
  }, []);

  const handleCopyTerminal = useCallback(async () => {
    if (!id) return;
    try {
      const { text } = await getTerminalText(id);
      setTermTextModal(text || "(no output)");
      setTimeout(() => termTextRef.current?.select(), 50);
    } catch {
      setTermTextModal("(failed to fetch terminal text)");
    }
  }, [id]);

  const addTerminalPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleAddTerminal = useCallback(async () => {
    if (!session || actionPending || terminalIds.length >= 4) return;
    setActionPending("add");
    try {
      await createSession({
        machine: session.machine,
        directory: session.path,
      });
      // Poll every 2s for up to 30s until the new session appears
      let attempts = 0;
      const knownIds = new Set(terminalIds);
      addTerminalPollRef.current = setInterval(async () => {
        attempts++;
        try {
          const data = await getSessions();
          setSessions(data);
          const found = data.some(
            s => s.path === session.path && s.machine === session.machine
              && s.status === "running" && !knownIds.has(s.id)
          );
          if (found || attempts >= 15) {
            if (addTerminalPollRef.current) clearInterval(addTerminalPollRef.current);
            addTerminalPollRef.current = null;
            setActionPending(null);
          }
        } catch {
          if (attempts >= 15) {
            if (addTerminalPollRef.current) clearInterval(addTerminalPollRef.current);
            addTerminalPollRef.current = null;
            setActionPending(null);
          }
        }
      }, 2000);
    } catch {
      setActionPending(null);
    }
  }, [session, actionPending, terminalIds]);

  const handleDeleteTerminal = useCallback(async (termId: string) => {
    if (actionPending) return;
    if (termId === id) return; // Don't allow deleting the primary (original) session
    setActionPending("delete");
    try {
      await stopSession(termId);
      // Give agent time to stop the tmux session, then delete from server
      setTimeout(async () => {
        try {
          await deleteSession(termId);
        } catch { /* ignore — might already be gone */ }
        try {
          const data = await getSessions();
          setSessions(data);
        } catch { /* ignore */ }
        setActionPending(null);
      }, 3000);
    } catch {
      setActionPending(null);
    }
  }, [id, actionPending]);

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (addTerminalPollRef.current) clearInterval(addTerminalPollRef.current);
    };
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
  useEffect(() => {
    if (!id) return;
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

  // Show full-page spinner only on first load (no session data yet).
  // During SPA navigation between sessions, keep the layout stable so
  // the terminal container doesn't lose its CSS dimensions.
  if (loading && !session) {
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
                  {isStopped ? (
                    <button
                      onClick={() => handleAction("start")}
                      disabled={!!actionPending}
                      title="Start session"
                      className="flex items-center gap-1 px-2 py-1 rounded text-green-400 hover:text-green-300 hover:bg-green-900/30 transition-colors disabled:opacity-50"
                    >
                      <Play className={`w-3 h-3 ${actionPending === "start" ? "animate-pulse" : ""}`} />
                      <span className="text-[11px] hidden sm:inline">{actionPending === "start" ? "Starting..." : "Start"}</span>
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
                        <span className="text-[11px] hidden sm:inline">{actionPending === "restart" ? "Restarting..." : "Restart"}</span>
                      </button>
                      <button
                        onClick={() => handleAction("stop")}
                        disabled={!!actionPending}
                        title="Stop session"
                        className="flex items-center gap-1 px-2 py-1 rounded text-red-400 hover:text-red-300 hover:bg-red-900/30 transition-colors disabled:opacity-50"
                      >
                        <Square className={`w-3 h-3 ${actionPending === "stop" ? "animate-pulse" : ""}`} />
                        <span className="text-[11px] hidden sm:inline">{actionPending === "stop" ? "Stopping..." : "Stop"}</span>
                      </button>
                    </>
                  ) : null}
                  {session.account && (
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (actionPending) return;
                        const target = session.account === "cc" ? "cb" : "cc";
                        setActionPending("switch");
                        try {
                          await restartSession(id!, target);
                          setTimeout(() => { setReloadKey((k) => k + 1); setActionPending(null); }, 3000);
                        } catch { setActionPending(null); }
                      }}
                      disabled={!!actionPending}
                      title={`Switch to ${session.account === "cc" ? "cb" : "cc"} account`}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase cursor-pointer
                                  transition-all hover:scale-110 disabled:opacity-50
                                  ${session.account === "cc"
                                    ? "bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/30 hover:bg-blue-500/25"
                                    : "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30 hover:bg-amber-500/25"
                                  }`}>
                      {actionPending === "switch" ? "..." : session.account}
                    </button>
                  )}
                  <span className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400 text-[11px]">
                    {session.machine}
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Copy terminal text modal */}
      {termTextModal !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setTermTextModal(null)}>
          <div className="bg-slate-900 border border-slate-700 rounded-lg shadow-xl w-[90vw] max-w-3xl max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-700">
              <span className="text-sm font-medium text-slate-300">Terminal Text</span>
              <button onClick={() => setTermTextModal(null)} className="p-1 text-slate-400 hover:text-slate-200"><X className="w-4 h-4" /></button>
            </div>
            <textarea
              ref={termTextRef}
              readOnly
              value={termTextModal}
              className="flex-1 m-2 p-3 bg-slate-950 text-slate-200 text-xs font-mono rounded border border-slate-700 resize-none focus:outline-none"
            />
          </div>
        </div>
      )}

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden">

        {sidebarOpen && (
          <>
            {/* Backdrop on mobile to close sidebar */}
            <div
              className="fixed inset-0 bg-black/40 z-20 md:hidden"
              onClick={() => setSidebarOpen(false)}
            />
            {/* === COL 1: Session list (deduplicated by path+machine) === */}
            <div className="w-48 flex-shrink-0 flex flex-col border-r border-slate-800 bg-slate-900/30
                            fixed md:relative inset-y-0 left-0 z-30 md:z-auto bg-slate-950 md:bg-slate-900/30 pt-11 md:pt-0">
              <div className="px-3 py-1.5 border-b border-slate-800 flex-shrink-0">
                <span className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
                  Sessions
                </span>
              </div>
              <div className="flex-1 overflow-y-auto">
                {(() => {
                  const renderRow = (s: Session, showMachine: boolean) => {
                    const siblings = sessions.filter(
                      ss => ss.path === s.path && ss.machine === s.machine && ss.status === "running"
                    ).length;
                    const isActive = s.id === id || (session && s.path === session.path && s.machine === session.machine);
                    return (
                      <div
                        key={s.id}
                        className={`group w-full flex items-stretch transition-colors border-l-2
                                   ${isActive
                                     ? "bg-slate-800/60 border-l-blue-500 text-slate-200"
                                     : "border-l-transparent text-slate-400 hover:bg-slate-800/30 hover:text-slate-300"
                                   }`}
                      >
                        <button
                          onClick={() => {
                            if (isActive) {
                              setReloadKey((k) => k + 1);
                              fileReloadRef.current?.();
                            } else {
                              navigate(`/session/${s.id}`);
                            }
                            if (window.innerWidth < 768) setSidebarOpen(false);
                          }}
                          className="flex-1 min-w-0 text-left px-3 py-1.5 flex items-center gap-2"
                        >
                          <Circle
                            className={`w-1.5 h-1.5 flex-shrink-0 fill-current ${STATUS_COLORS[s.status] || "text-slate-600"}`}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-medium truncate">{s.project}</div>
                            {showMachine && (
                              <div className="text-[9px] text-slate-600 uppercase tracking-widest truncate">
                                {s.machine}
                              </div>
                            )}
                          </div>
                          {siblings > 1 && (
                            <span className="text-[9px] text-slate-600 bg-slate-800 px-1 rounded">
                              {siblings}
                            </span>
                          )}
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleTogglePin(s); }}
                          title={s.pinned ? "Unpin from top" : "Pin to top"}
                          aria-label={s.pinned ? "Unpin" : "Pin to top"}
                          className={`px-2 flex items-center transition-colors
                                     ${s.pinned
                                       ? "text-yellow-400 hover:text-yellow-300"
                                       : "text-slate-500 hover:text-slate-300 md:text-slate-700 md:opacity-0 md:group-hover:opacity-100"
                                     }`}
                        >
                          <Pin
                            className={`w-3 h-3 ${s.pinned ? "fill-current" : ""}`}
                          />
                        </button>
                      </div>
                    );
                  };

                  const pinned = sidebarSessions.filter((s) => s.pinned);
                  const unpinned = sidebarSessions.filter((s) => !s.pinned);
                  const byMachine = unpinned.reduce<Record<string, Session[]>>((acc, s) => {
                    const key = s.machine || "Unknown";
                    if (!acc[key]) acc[key] = [];
                    acc[key].push(s);
                    return acc;
                  }, {});

                  return (
                    <>
                      {pinned.length > 0 && (
                        <div>
                          <div className="px-3 py-1 bg-yellow-900/10 border-b border-slate-800/50 sticky top-0 flex items-center gap-1.5">
                            <Pin className="w-2.5 h-2.5 text-yellow-500/70 fill-yellow-500/70" />
                            <span className="text-[9px] font-semibold text-yellow-600/80 uppercase tracking-widest">
                              Pinned
                            </span>
                          </div>
                          {pinned.map((s) => renderRow(s, true))}
                        </div>
                      )}
                      {Object.entries(byMachine)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([machineName, machineSessions]) => (
                          <div key={machineName}>
                            <div className="px-3 py-1 bg-slate-900/60 border-b border-slate-800/50 sticky top-0">
                              <span className="text-[9px] font-semibold text-slate-600 uppercase tracking-widest">
                                {machineName}
                              </span>
                            </div>
                            {machineSessions.map((s) => renderRow(s, false))}
                          </div>
                        ))}
                    </>
                  );
                })()}
              </div>
            </div>

            {/* === COL 2: File tree (hidden on mobile) === */}
            <div className="w-52 flex-shrink-0 hidden md:flex flex-col border-r border-slate-800 bg-slate-900/20">
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

        {/* === MAIN: split top/bottom — file viewer + terminal grid === */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">

          {/* File viewer (top half, only when file is open — hidden on mobile) */}
          {selectedFile && (
            <div className="flex-1 hidden md:flex flex-col min-h-0 border-b border-slate-800">
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

          {/* Terminal grid */}
          <div className="flex-1 flex flex-col min-h-0">
            <div className="hidden sm:flex items-center border-b border-slate-800 flex-shrink-0 px-2">
              <span className="text-[11px] font-medium text-slate-400 px-1 py-1.5">
                Terminals
                {terminalIds.length > 1 && (
                  <span className="text-slate-600 ml-1">({terminalIds.length})</span>
                )}
              </span>
              <button
                onClick={handleAddTerminal}
                disabled={!!actionPending || !isRunning || terminalIds.length >= 4}
                title={terminalIds.length >= 4 ? "Maximum 4 terminals" : "Add terminal"}
                className="ml-auto p-1 rounded text-slate-600 hover:text-slate-300 hover:bg-slate-700/50 transition-colors disabled:opacity-30"
              >
                <Plus className={`w-3 h-3 ${actionPending === "add" ? "animate-pulse" : ""}`} />
              </button>
              <button
                onClick={handleCopyTerminal}
                title="Copy terminal text"
                className="p-1 rounded text-slate-600 hover:text-slate-300 hover:bg-slate-700/50 transition-colors"
              >
                <ClipboardCopy className="w-3 h-3" />
              </button>
              <button
                onClick={handleReload}
                title="Reload"
                className="p-1 rounded text-slate-600 hover:text-slate-300 hover:bg-slate-700/50 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
              </button>
            </div>
            <div className={`flex-1 min-h-0 overflow-hidden grid ${gridClass} gap-px bg-slate-800`}>
              {isStopped ? (
                <div className="bg-slate-950 h-full flex flex-col items-center justify-center text-slate-500 text-sm gap-3">
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
                terminalIds.map((termId) => {
                  const termSession = sessions.find(s => s.id === termId);
                  return (
                  <div key={`pane-${termId}-${reloadKey}`} className="bg-slate-950 min-h-0 min-w-0 overflow-hidden relative">
                    {termSession?.account && terminalIds.length > 1 && (
                      <span className={`absolute top-1 left-1 z-10 px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wide uppercase
                                       ${termSession.account === "cc"
                                         ? "bg-blue-500/20 text-blue-400 ring-1 ring-blue-500/30"
                                         : "bg-amber-500/20 text-amber-400 ring-1 ring-amber-500/30"
                                       }`}>
                        {termSession.account}
                      </span>
                    )}
                    {termId !== id && (
                      <button
                        onClick={() => handleDeleteTerminal(termId)}
                        disabled={!!actionPending}
                        title="Stop and remove terminal"
                        className="absolute top-1 right-1 z-10 p-1 rounded bg-slate-800/90 text-slate-400 hover:text-red-400 hover:bg-red-900/50 transition-colors disabled:opacity-30 border border-slate-700/50"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                    <Suspense fallback={<div className="h-full flex items-center justify-center text-slate-500 text-sm">Loading terminal...</div>}>
                      <TerminalView wsUrl={getWsUrl(`/ws/terminal/${termId}`)} fontSize={termFontSize} />
                    </Suspense>
                  </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
