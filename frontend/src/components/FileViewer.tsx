import { useEffect, useState, useRef } from "react";
import { File, X } from "lucide-react";
import type { FileMessage } from "../types";

const EXT_LANG: Record<string, string> = {
  ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
  ".jsx": "jsx", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
  ".toml": "toml", ".md": "markdown", ".html": "html", ".css": "css",
  ".sh": "bash", ".rs": "rust", ".go": "go", ".java": "java", ".sql": "sql",
  ".xml": "xml", ".rb": "ruby", ".php": "php",
};

function langFromPath(path: string): string {
  const ext = "." + path.split(".").pop()?.toLowerCase();
  return EXT_LANG[ext] || "text";
}

interface OpenTab {
  path: string;
  content: string | null;
  error: string | null;
  loading: boolean;
}

interface FileViewerProps {
  messages: FileMessage[];
  sendMessage: (data: unknown) => void;
  selectedFile: string | null;
  onCloseFile: () => void;
}

export default function FileViewer({
  messages,
  sendMessage,
  selectedFile,
  onCloseFile,
}: FileViewerProps) {
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // When selectedFile changes, open a tab for it
  useEffect(() => {
    if (!selectedFile) return;
    setActiveTab(selectedFile);
    setTabs((prev) => {
      if (prev.some((t) => t.path === selectedFile)) return prev;
      return [...prev, { path: selectedFile, content: null, error: null, loading: true }];
    });
    sendMessage({ type: "read", path: selectedFile });
  }, [selectedFile, sendMessage]);

  // Process read_response / error messages
  useEffect(() => {
    if (messages.length === 0) return;
    const msg = messages[messages.length - 1];
    if (msg.type === "read_response" && msg.content !== undefined) {
      setTabs((prev) =>
        prev.map((t) =>
          t.path === msg.path ? { ...t, content: msg.content!, error: null, loading: false } : t,
        ),
      );
    } else if (msg.type === "error" && msg.path) {
      setTabs((prev) =>
        prev.map((t) =>
          t.path === msg.path ? { ...t, error: msg.error || "Error", content: null, loading: false } : t,
        ),
      );
    }
  }, [messages]);

  function closeTab(path: string, e?: React.MouseEvent) {
    e?.stopPropagation();
    setTabs((prev) => prev.filter((t) => t.path !== path));
    if (activeTab === path) {
      const remaining = tabs.filter((t) => t.path !== path);
      setActiveTab(remaining.length > 0 ? remaining[remaining.length - 1].path : null);
      if (remaining.length === 0) onCloseFile();
    }
  }

  const current = tabs.find((t) => t.path === activeTab);

  if (tabs.length === 0) {
    return (
      <div className="h-full flex items-center justify-center bg-slate-950">
        <div className="text-center">
          <File className="w-10 h-10 text-slate-800 mx-auto mb-3" />
          <p className="text-slate-600 text-sm">Select a file to view</p>
          <p className="text-slate-700 text-xs mt-1">Browse files in the left panel</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-slate-950">
      {/* Tab bar */}
      <div className="flex items-center border-b border-slate-800 bg-slate-900/60 overflow-x-auto flex-shrink-0">
        {tabs.map((tab) => {
          const isActive = tab.path === activeTab;
          const fileName = tab.path.split("/").pop() || tab.path;
          return (
            <button
              key={tab.path}
              onClick={() => setActiveTab(tab.path)}
              className={`group flex items-center gap-1.5 px-3 py-1.5 text-xs border-r border-slate-800
                         transition-colors flex-shrink-0 max-w-[180px]
                         ${isActive
                           ? "bg-slate-950 text-slate-200 border-b-2 border-b-blue-500"
                           : "bg-slate-900/40 text-slate-500 hover:text-slate-300 border-b-2 border-b-transparent"
                         }`}
            >
              <File className="w-3 h-3 flex-shrink-0" />
              <span className="truncate">{fileName}</span>
              <button
                onClick={(e) => closeTab(tab.path, e)}
                className="p-0.5 rounded hover:bg-slate-700 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
              >
                <X className="w-3 h-3" />
              </button>
            </button>
          );
        })}
        {/* Language badge */}
        {current && (
          <span className="ml-auto px-2 text-[10px] text-slate-600 flex-shrink-0">
            {langFromPath(current.path)}
          </span>
        )}
      </div>

      {/* File content */}
      <div ref={contentRef} className="flex-1 overflow-auto log-scroll">
        {current?.loading && (
          <p className="p-4 text-slate-600 text-xs italic">Loading...</p>
        )}
        {current?.error && (
          <p className="p-4 text-red-400 text-xs">{current.error}</p>
        )}
        {current?.content !== null && current?.content !== undefined && (
          <pre className="text-[12px] leading-[1.6] text-slate-300 font-mono whitespace-pre overflow-x-auto">
            <table className="w-full border-collapse">
              <tbody>
                {current.content.split("\n").map((line, i) => (
                  <tr key={i} className="hover:bg-slate-900/60">
                    <td className="w-12 text-right pr-4 pl-2 text-slate-600 select-none align-top sticky left-0 bg-slate-950">
                      {i + 1}
                    </td>
                    <td className="pr-4">{line || " "}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </pre>
        )}
      </div>
    </div>
  );
}
