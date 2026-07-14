import { useEffect, useRef, useCallback, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { Send, ChevronUp, ChevronDown, ChevronsDown } from "lucide-react";
import "@xterm/xterm/css/xterm.css";
import { useTerminalWebSocket } from "../hooks/useTerminalWebSocket";

/**
 * Copy text to clipboard with HTTP fallback.
 * navigator.clipboard requires HTTPS; on plain HTTP we fall back
 * to the legacy execCommand('copy') via a temporary textarea.
 */
function copyToClipboard(text: string): void {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => copyFallback(text));
  } else {
    copyFallback(text);
  }
}

function copyFallback(text: string): void {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

interface TerminalViewProps {
  wsUrl: string | null;
  fontSize?: number;
}

// Matches terminal auto-responses that xterm.js generates:
// DA1: \033[?...c  DA2: \033[>...c  DSR cursor: \033[...R  Window: \033[...t
const TERMINAL_RESPONSE_RE = /^\x1b\[[\?>]?[\d;]*[cRt]$/;

function isTerminalResponse(data: string): boolean {
  return TERMINAL_RESPONSE_RE.test(data);
}

// Detect touch device (mobile/tablet)
const IS_TOUCH = typeof window !== "undefined" && ("ontouchstart" in window || navigator.maxTouchPoints > 0);

// Mobile quick-keys: TUIs (Claude Code, fzf, vim selectors) all accept these
// escape sequences. Sent verbatim via sendInput() to the PTY, identical to
// what xterm.js emits when the physical key is pressed on desktop.
const QUICK_KEYS: ReadonlyArray<{ label: string; send: string; title: string }> = [
  { label: "1", send: "1", title: "Select 1" },
  { label: "2", send: "2", title: "Select 2" },
  { label: "3", send: "3", title: "Select 3" },
  { label: "4", send: "4", title: "Select 4" },
  { label: "5", send: "5", title: "Select 5" },
  { label: "↑", send: "\x1b[A", title: "Arrow up" },
  { label: "↓", send: "\x1b[B", title: "Arrow down" },
  { label: "⇥", send: "\t", title: "Tab" },
  { label: "⏎", send: "\r", title: "Enter" },
  { label: "Esc", send: "\x1b", title: "Escape" },
  { label: "^C", send: "\x03", title: "Ctrl+C (interrupt)" },
];

export default function TerminalView({ wsUrl, fontSize = 13 }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const sendResizeRef = useRef<(cols: number, rows: number) => void>(() => {});
  // Track last-sent terminal dimensions (cols/rows) to suppress redundant resize messages
  const lastSizeRef = useRef<{ cols: number; rows: number }>({ cols: 0, rows: 0 });
  // Track container pixel dimensions to ignore ResizeObserver events not caused by real layout changes
  const lastContainerRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  const [mobileInput, setMobileInput] = useState("");
  const [inputBarOpen, setInputBarOpen] = useState(false);
  const [sendFlash, setSendFlash] = useState(false);
  const mobileInputRef = useRef<HTMLInputElement>(null);

  const onData = useCallback((data: Uint8Array) => {
    terminalRef.current?.write(data);
  }, []);

  const { sendInput, sendResize, connected } = useTerminalWebSocket({
    url: wsUrl,
    onData,
  });

  sendResizeRef.current = sendResize;

  // Fit terminal and send resize only if cols/rows actually changed.
  const fitAndResize = useCallback(() => {
    const fitAddon = fitAddonRef.current;
    const terminal = terminalRef.current;
    if (!fitAddon || !terminal) return;

    fitAddon.fit();

    const { cols, rows } = terminal;
    const last = lastSizeRef.current;
    if (cols !== last.cols || rows !== last.rows) {
      lastSizeRef.current = { cols, rows };
      sendResizeRef.current(cols, rows);
    }
  }, []);

  // Initialize xterm.js
  useEffect(() => {
    if (!containerRef.current) return;

    const terminal = new Terminal({
      cursorBlink: true,
      fontSize,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace",
      theme: {
        background: "#0a0e1a",
        foreground: "#e2e8f0",
        cursor: "#3b82f6",
        selectionBackground: "#334155",
      },
      allowProposedApi: true,
      scrollback: IS_TOUCH ? 2000 : 10000,
      scrollOnUserInput: true,
      scrollSensitivity: 3,
      fastScrollSensitivity: 10,
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(containerRef.current);
    fitAddon.fit();

    // Fix iOS mobile input: configure the hidden textarea to prevent
    // character preview at bottom of screen and auto-zoom
    if (IS_TOUCH) {
      const textarea = containerRef.current.querySelector("textarea");
      if (textarea) {
        textarea.setAttribute("autocomplete", "off");
        textarea.setAttribute("autocorrect", "off");
        textarea.setAttribute("autocapitalize", "off");
        textarea.setAttribute("spellcheck", "false");
        textarea.setAttribute("inputmode", "text");
        // Font size >= 16px prevents iOS zoom-on-focus
        textarea.style.fontSize = "16px";
        textarea.style.opacity = "0";
      }
    }

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    // Handle resize — debounced to avoid lag from iOS keyboard open/close.
    // Only triggers fit() when container pixel dimensions actually changed,
    // preventing feedback loops where fit() adjusts xterm internals which
    // re-triggers the observer even though the container didn't resize.
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width: w, height: h } = entry.contentRect;
      const last = lastContainerRef.current;
      if (Math.abs(w - last.w) < 1 && Math.abs(h - last.h) < 1) return;
      lastContainerRef.current = { w, h };

      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        fitAndResize();
      }, IS_TOUCH ? 200 : 50);
    });
    observer.observe(containerRef.current);

    return () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      observer.disconnect();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [fontSize]);

  // Wire up keyboard input
  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;

    // Block Ctrl+V / Cmd+V from sending \x16 to the remote terminal.
    // Without this, Claude Code receives the raw control char and tries
    // to read the *server-side* clipboard → "no image found on clipboard".
    // Returning false tells xterm.js to skip processing the key but lets
    // the browser fire the native paste event on xterm's internal textarea,
    // which xterm handles with proper bracketed-paste wrapping (\e[200~…\e[201~).
    terminal.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown" || event.shiftKey) return true;

      // Let browser-level shortcuts pass through instead of going to PTY
      if ((event.ctrlKey || event.metaKey) && ["r", "t", "w", "l", "n"].includes(event.key)) {
        return false;
      }

      // Ctrl+C / Cmd+C: copy selected text to clipboard instead of
      // sending \x03 (SIGINT) to the PTY.  When nothing is selected,
      // fall through so the terminal receives SIGINT as usual.
      if (
        event.key === "c" &&
        (event.ctrlKey || event.metaKey) &&
        terminal.hasSelection()
      ) {
        copyToClipboard(terminal.getSelection());
        return false;
      }

      // Ctrl+V / Cmd+V: let the browser fire the native paste event
      // on xterm's internal textarea instead of sending \x16 to the PTY.
      if (event.key === "v" && (event.ctrlKey || event.metaKey)) {
        return false;
      }

      return true;
    });

    const disposable = terminal.onData((data) => {
      // Filter out terminal auto-responses (DA, DSR, etc.) that xterm.js
      // generates in response to queries from the remote shell/tmux.
      // These arrive too late over WebSocket and get echoed as shell input.
      if (isTerminalResponse(data)) return;
      sendInput(data);
    });

    return () => disposable.dispose();
  }, [sendInput]);

  // Deterministic mouse-wheel scroll. The browser xterm.js is a late-joining
  // MIRROR of the agent's single `tmux attach` PTY and unreliably enters mouse
  // mode (it misses tmux's one-time `?1002h`), so the native wheel — which only
  // forwards when xterm is in mouse mode — often does nothing. Instead inject
  // SGR wheel bytes straight into the PTY (identical to the ↑/↓ buttons'
  // scrollPage): with tmux `mouse on`, tmux scrolls the app itself (Claude in
  // its alt screen) or enters copy-mode (inline Claude / plain shell). Capture
  // phase + stopImmediatePropagation so xterm never double-handles the same
  // wheel event; bypasses xterm mouse mode entirely.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const t = terminalRef.current;
      if (!t) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      const col = Math.max(1, Math.floor(t.cols / 2));
      const row = Math.max(1, Math.floor(t.rows / 2));
      const code = e.deltaY < 0 ? 64 : 65; // 64 = up, 65 = down
      const notches = Math.max(1, Math.min(10, Math.round(Math.abs(e.deltaY) / 40)));
      sendInput(`\x1b[<${code};${col};${row}M`.repeat(notches));
    };
    el.addEventListener("wheel", onWheel, { passive: false, capture: true });
    return () => el.removeEventListener("wheel", onWheel, { capture: true } as EventListenerOptions);
  }, [sendInput]);

  // Send initial resize when connected — re-fit first to ensure correct dimensions
  useEffect(() => {
    if (connected && terminalRef.current && fitAddonRef.current) {
      // Reset so fitAndResize() always sends on reconnect — the PTY
      // restarts at 80x24 but cols/rows haven't changed client-side.
      lastSizeRef.current = { cols: 0, rows: 0 };
      fitAndResize();
      terminalRef.current.focus();
      // Re-fit after a short delay to catch late CSS layout changes
      const timer = setTimeout(() => {
        fitAndResize();
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [connected, fitAndResize]);

  // Handle form submit — read value directly from DOM to avoid iOS IME race
  const handleMobileSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    // Read directly from the input element (iOS may not have flushed onChange yet)
    const text = mobileInputRef.current?.value || mobileInput;
    if (!text) return;
    sendInput(text + "\r");
    setMobileInput("");
    if (mobileInputRef.current) mobileInputRef.current.value = "";
    setSendFlash(true);
    setTimeout(() => setSendFlash(false), 400);
    setTimeout(() => mobileInputRef.current?.focus(), 50);
  }, [mobileInput, sendInput]);

  // Page-scroll the terminal. In the NORMAL buffer we move xterm's own
  // viewport offset. In the ALTERNATE buffer (a full-screen TUI like Claude
  // Code) xterm has no scrollback — `tmux attach` holds the outer alt screen —
  // so scrollLines() is a no-op. There we instead inject SGR mouse-wheel
  // events into the PTY: with tmux `mouse on`, tmux forwards them to the
  // mouse-aware app (Claude Code scrolls its own view) or enters copy-mode for
  // a plain shell. This mirrors how the desktop wheel already reaches the app.
  const scrollPage = useCallback((dir: "up" | "down" | "bottom") => {
    const t = terminalRef.current;
    if (!t) return;
    if (t.buffer.active.type === "alternate") {
      // SGR wheel: \e[<64;col;rowM = up, \e[<65;col;rowM = down. The
      // coordinates only tell tmux which pane to target; screen-center is
      // always in-bounds. One tap ≈ a few notches; "bottom" sends a long burst.
      const col = Math.max(1, Math.floor(t.cols / 2));
      const row = Math.max(1, Math.floor(t.rows / 2));
      const code = dir === "up" ? 64 : 65;
      const notches = dir === "bottom" ? 50 : 5;
      sendInput(`\x1b[<${code};${col};${row}M`.repeat(notches));
      return;
    }
    if (dir === "bottom") { t.scrollToBottom(); return; }
    const lines = Math.max(1, t.rows - 2);
    t.scrollLines(dir === "up" ? -lines : lines);
  }, [sendInput]);

  return (
    <div className="h-full relative flex flex-col">
      <div ref={containerRef} className="flex-1 min-h-0 w-full overflow-hidden" />
      {!connected && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60">
          <p className="text-slate-500 text-sm italic">Connecting to terminal...</p>
        </div>
      )}
      {/* Scroll controls — visible affordance that works in both buffers:
          xterm's own viewport in the normal buffer, and injected SGR wheel
          events to the PTY in the alt buffer (full-screen TUIs). See scrollPage. */}
      {connected && (
        <div className="absolute top-2 right-2 z-10 flex flex-col gap-1">
          <button
            onClick={() => scrollPage("up")}
            className="p-1.5 rounded bg-slate-800/80 border border-slate-700/50 text-slate-400
                       hover:bg-slate-700 hover:text-slate-200 active:bg-slate-600 transition-colors"
            title="Scroll up"
          >
            <ChevronUp className="w-4 h-4" />
          </button>
          <button
            onClick={() => scrollPage("down")}
            className="p-1.5 rounded bg-slate-800/80 border border-slate-700/50 text-slate-400
                       hover:bg-slate-700 hover:text-slate-200 active:bg-slate-600 transition-colors"
            title="Scroll down"
          >
            <ChevronDown className="w-4 h-4" />
          </button>
          <button
            onClick={() => scrollPage("bottom")}
            className="p-1.5 rounded bg-slate-800/80 border border-slate-700/50 text-slate-400
                       hover:bg-slate-700 hover:text-slate-200 active:bg-slate-600 transition-colors"
            title="Jump to bottom"
          >
            <ChevronsDown className="w-4 h-4" />
          </button>
        </div>
      )}
      {/* Mobile input bar — toggle button + text input for paste */}
      {IS_TOUCH && connected && (
        <>
          {!inputBarOpen && (
            <button
              onClick={() => { setInputBarOpen(true); setTimeout(() => mobileInputRef.current?.focus(), 100); }}
              className="absolute bottom-2 right-2 z-10 p-2 rounded-lg bg-slate-800/90 border border-slate-700/50
                         text-slate-400 active:bg-slate-700 active:text-slate-200 transition-colors"
              title="Open input bar"
            >
              <ChevronUp className="w-4 h-4" />
            </button>
          )}
          {inputBarOpen && (
            <div className="flex-shrink-0 flex flex-col bg-slate-900 border-t border-slate-700">
              {/* Quick-keys strip — tap to send single keystrokes / escape
                  sequences to the PTY without opening the on-screen keyboard.
                  Horizontally scrollable so phones <360px wide still fit. */}
              <div className="flex items-center gap-1 px-2 py-1.5 overflow-x-auto border-b border-slate-800">
                {QUICK_KEYS.map((k) => (
                  <button
                    key={k.label}
                    type="button"
                    onClick={() => sendInput(k.send)}
                    title={k.title}
                    className="flex-shrink-0 min-w-[36px] px-2.5 py-1.5 rounded
                               bg-slate-800 border border-slate-700 text-slate-200 text-sm
                               active:bg-slate-700 active:text-white transition-colors"
                  >
                    {k.label}
                  </button>
                ))}
              </div>
            <form
              onSubmit={handleMobileSubmit}
              className="flex items-center gap-1.5 px-2 py-1.5"
            >
              <button
                type="button"
                onClick={() => setInputBarOpen(false)}
                className="p-1.5 rounded text-slate-500 active:text-slate-300 transition-colors flex-shrink-0"
              >
                <ChevronDown className="w-4 h-4" />
              </button>
              <input
                ref={mobileInputRef}
                type="text"
                value={mobileInput}
                onChange={(e) => setMobileInput(e.target.value)}
                placeholder="Type or paste here..."
                enterKeyHint="send"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
                className="flex-1 min-w-0 px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm
                           text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                style={{ fontSize: "16px" }}
              />
              <button
                type="submit"
                disabled={!mobileInput}
                className={`p-2 rounded-lg text-white transition-colors flex-shrink-0
                           ${sendFlash
                             ? "bg-green-600"
                             : "bg-blue-600 active:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500"
                           }`}
              >
                <Send className="w-4 h-4" />
              </button>
            </form>
            </div>
          )}
        </>
      )}
    </div>
  );
}
