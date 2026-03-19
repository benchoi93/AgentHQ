import { useEffect, useRef, useState, useCallback } from "react";

interface UseTerminalWebSocketOptions {
  url: string | null;
  onData: (data: Uint8Array) => void;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

interface UseTerminalWebSocketReturn {
  sendInput: (data: string) => void;
  sendResize: (cols: number, rows: number) => void;
  connected: boolean;
}

// Coalescing window for batching rapid keystrokes (ms).
// Keeps interactive typing snappy while reducing message count during pastes.
const INPUT_COALESCE_MS = 4;

export function useTerminalWebSocket({
  url,
  onData,
  reconnectInterval = 3000,
  maxReconnectAttempts = 10,
}: UseTerminalWebSocketOptions): UseTerminalWebSocketReturn {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onDataRef = useRef(onData);
  onDataRef.current = onData;

  // Input coalescing refs — accumulate keystrokes and flush after a short delay
  const inputBufRef = useRef("");
  const inputFlushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushInput = useCallback(() => {
    inputFlushTimer.current = null;
    const buf = inputBufRef.current;
    if (!buf) return;
    inputBufRef.current = "";
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    const bytes = new TextEncoder().encode(buf);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    wsRef.current.send(JSON.stringify({ type: "input", data: btoa(binary) }));
  }, []);

  const sendInput = useCallback((data: string) => {
    inputBufRef.current += data;
    if (!inputFlushTimer.current) {
      inputFlushTimer.current = setTimeout(flushInput, INPUT_COALESCE_MS);
    }
  }, [flushInput]);

  const sendResize = useCallback((cols: number, rows: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  }, []);

  useEffect(() => {
    if (!url) {
      setConnected(false);
      return;
    }

    setConnected(false);
    reconnectCount.current = 0;
    let disposed = false;

    function connect() {
      if (disposed) return;
      const ws = new WebSocket(url!);
      wsRef.current = ws;

      ws.onopen = () => {
        if (disposed) { ws.close(); return; }
        setConnected(true);
        reconnectCount.current = 0;
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        try {
          const parsed = JSON.parse(event.data);
          if (parsed.type === "output" && parsed.data) {
            const binary = Uint8Array.from(atob(parsed.data), (c) => c.charCodeAt(0));
            onDataRef.current(binary);
          }
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        setConnected(false);
        wsRef.current = null;
        if (reconnectCount.current < maxReconnectAttempts) {
          reconnectCount.current++;
          reconnectTimer.current = setTimeout(connect, reconnectInterval);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      disposed = true;
      // Flush any pending input before tearing down
      if (inputFlushTimer.current) {
        clearTimeout(inputFlushTimer.current);
        inputFlushTimer.current = null;
      }
      flushInput();
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [url, reconnectInterval, maxReconnectAttempts, flushInput]);

  return { sendInput, sendResize, connected };
}
