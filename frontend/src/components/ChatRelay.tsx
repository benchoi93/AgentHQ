import { useState, useRef, useEffect, type FormEvent } from "react";
import { Send } from "lucide-react";
import type { RelayMessage } from "../types";

interface ChatRelayProps {
  messages: RelayMessage[];
  sendMessage: (data: unknown) => void;
  connected: boolean;
}

export default function ChatRelay({
  messages,
  sendMessage,
  connected,
}: ChatRelayProps) {
  const [input, setInput] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || !connected) return;
    sendMessage({ type: "input", content: trimmed });
    setInput("");
    inputRef.current?.focus();
  }

  return (
    <div className="h-full flex flex-col">
      {/* Messages */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto p-4 space-y-3 log-scroll"
      >
        {messages.length === 0 && (
          <p className="text-slate-600 italic text-sm text-center mt-8">
            Send a message to the session...
          </p>
        )}
        {messages.map((msg, i) => {
          const isUser = msg.type === "input";
          return (
            <div
              key={i}
              className={`flex ${isUser ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap break-words ${
                  isUser
                    ? "bg-blue-600 text-white rounded-br-none"
                    : "bg-slate-800 text-slate-200 rounded-bl-none"
                }`}
              >
                {msg.content}
              </div>
            </div>
          );
        })}
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-slate-800 p-3 flex items-center gap-2"
      >
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={connected ? "Type a message..." : "Disconnected"}
          disabled={!connected}
          className="flex-1 px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm
                     text-slate-100 placeholder:text-slate-500
                     focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                     disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled={!connected || !input.trim()}
          className="p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500
                     text-white rounded-lg transition-colors disabled:cursor-not-allowed"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>
    </div>
  );
}
