import { useEffect, useRef, useCallback, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { Send, ChevronUp, ChevronDown } from "lucide-react";
import "@xterm/xterm/css/xterm.css";
import { useTerminalWebSocket } from "../hooks/useTerminalWebSocket";

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

export default function TerminalView({ wsUrl, fontSize = 13 }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const sendResizeRef = useRef<(cols: number, rows: number) => void>(() => {});
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

    // Handle resize — debounced to avoid lag from iOS keyboard open/close
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        fitAddon.fit();
        sendResizeRef.current(terminal.cols, terminal.rows);
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
      if (
        event.type === "keydown" &&
        event.key === "v" &&
        (event.ctrlKey || event.metaKey) &&
        !event.shiftKey
      ) {
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

  // Send initial resize when connected — re-fit first to ensure correct dimensions
  useEffect(() => {
    if (connected && terminalRef.current && fitAddonRef.current) {
      fitAddonRef.current.fit();
      sendResize(terminalRef.current.cols, terminalRef.current.rows);
      // Re-fit after a short delay to catch late CSS layout changes
      const timer = setTimeout(() => {
        if (fitAddonRef.current && terminalRef.current) {
          fitAddonRef.current.fit();
          sendResize(terminalRef.current.cols, terminalRef.current.rows);
        }
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [connected, sendResize]);

  // Mobile input bar: send text (pasted or typed) to terminal + Enter
  const handleMobileSend = useCallback(() => {
    if (!mobileInput) return;
    // Send text followed by Enter (\r) so the command executes immediately
    sendInput(mobileInput + "\r");
    setMobileInput("");
    setSendFlash(true);
    setTimeout(() => setSendFlash(false), 400);
    // Re-focus input for rapid successive sends
    setTimeout(() => mobileInputRef.current?.focus(), 50);
  }, [mobileInput, sendInput]);

  // Handle form submit (works better than onKeyDown on iOS)
  const handleMobileSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    handleMobileSend();
  }, [handleMobileSend]);

  return (
    <div className="h-full relative flex flex-col">
      <div ref={containerRef} className="flex-1 min-h-0 w-full overflow-hidden" />
      {!connected && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60">
          <p className="text-slate-500 text-sm italic">Connecting to terminal...</p>
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
            <form
              onSubmit={handleMobileSubmit}
              className="flex-shrink-0 flex items-center gap-1.5 px-2 py-1.5 bg-slate-900 border-t border-slate-700"
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
          )}
        </>
      )}
    </div>
  );
}
