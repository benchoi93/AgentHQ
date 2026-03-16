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

  const sendInput = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      // base64 encode via TextEncoder to handle UTF-8 correctly
      // (btoa() only supports Latin-1 and breaks on multi-byte chars)
      const bytes = new TextEncoder().encode(data);
      let binary = "";
      for (let i = 0; i < bytes.length; i++) {
        binary += String.fromCharCode(bytes[i]);
      }
      const encoded = btoa(binary);
      wsRef.current.send(JSON.stringify({ type: "input", data: encoded }));
    }
  }, []);

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
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [url, reconnectInterval, maxReconnectAttempts]);

  return { sendInput, sendResize, connected };
}
