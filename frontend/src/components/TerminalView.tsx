import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
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

export default function TerminalView({ wsUrl, fontSize = 13 }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const sendResizeRef = useRef<(cols: number, rows: number) => void>(() => {});

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
      scrollback: 10000,
      scrollOnUserInput: true,
      scrollSensitivity: 3,
      fastScrollSensitivity: 10,
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(containerRef.current);
    fitAddon.fit();

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    // Handle resize
    const observer = new ResizeObserver(() => {
      fitAddon.fit();
      sendResizeRef.current(terminal.cols, terminal.rows);
    });
    observer.observe(containerRef.current);

    return () => {
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

  return (
    <div className="h-full relative">
      <div ref={containerRef} className="h-full w-full overflow-hidden" />
      {!connected && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60">
          <p className="text-slate-500 text-sm italic">Connecting to terminal...</p>
        </div>
      )}
    </div>
  );
}
