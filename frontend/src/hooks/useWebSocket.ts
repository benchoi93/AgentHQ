import { useEffect, useRef, useState, useCallback } from "react";

const MAX_MESSAGES = 5000;

interface UseWebSocketOptions {
  url: string | null;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
  replaceMode?: boolean;
}

interface UseWebSocketReturn<T> {
  messages: T[];
  sendMessage: (data: unknown) => void;
  connected: boolean;
  clearMessages: () => void;
}

export function useWebSocket<T = unknown>({
  url,
  reconnectInterval = 3000,
  maxReconnectAttempts = 10,
  replaceMode = false,
}: UseWebSocketOptions): UseWebSocketReturn<T> {
  const [messages, setMessages] = useState<T[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearMessages = useCallback(() => setMessages([]), []);

  const sendMessage = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    if (!url) {
      setMessages([]);
      setConnected(false);
      return;
    }

    // Reset all transient state for the new connection
    setMessages([]);
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
        setMessages([]);
        reconnectCount.current = 0;
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        try {
          const parsed = JSON.parse(event.data) as T;
          if (replaceMode) {
            setMessages([parsed]);
          } else {
            setMessages((prev) => {
              const next = [...prev, parsed];
              return next.length > MAX_MESSAGES ? next.slice(-MAX_MESSAGES) : next;
            });
          }
        } catch {
          // ignore non-JSON messages
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
  }, [url, reconnectInterval, maxReconnectAttempts, replaceMode]);

  return { messages, sendMessage, connected, clearMessages };
}
