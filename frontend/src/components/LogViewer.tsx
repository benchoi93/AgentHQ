import { useEffect, useRef } from "react";
import type { LogMessage } from "../types";

interface LogViewerProps {
  messages: LogMessage[];
}

export default function LogViewer({ messages }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !autoScrollRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    // Auto-scroll if within 50px of the bottom
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    autoScrollRef.current = atBottom;
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="h-full overflow-y-auto bg-slate-950 p-4 font-mono text-sm leading-relaxed log-scroll"
    >
      {messages.length === 0 && (
        <p className="text-slate-600 italic">Waiting for logs...</p>
      )}
      {messages.map((msg, i) => (
        <div key={i} className="flex gap-3 hover:bg-slate-900/50">
          <span className="text-slate-600 flex-shrink-0 select-none text-xs leading-relaxed">
            {new Date(msg.timestamp).toLocaleTimeString()}
          </span>
          <span className="text-green-400/80 whitespace-pre-wrap break-all">
            {msg.content}
          </span>
        </div>
      ))}
    </div>
  );
}
