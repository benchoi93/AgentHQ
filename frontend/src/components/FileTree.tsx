import { useState, useEffect, useCallback } from "react";
import {
  ChevronRight,
  ChevronDown,
  File,
  Folder,
  FolderOpen,
} from "lucide-react";
import type { FileEntry, FileMessage } from "../types";

interface TreeNode {
  entry: FileEntry;
  children?: FileEntry[];
  expanded: boolean;
}

interface FileTreeProps {
  messages: FileMessage[];
  sendMessage: (data: unknown) => void;
  connected: boolean;
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
  reloadRef?: React.MutableRefObject<(() => void) | null>;
}

export default function FileTree({
  messages,
  sendMessage,
  connected,
  selectedFile,
  onSelectFile,
  reloadRef,
}: FileTreeProps) {
  const [tree, setTree] = useState<Map<string, TreeNode>>(new Map());
  const [rootEntries, setRootEntries] = useState<FileEntry[]>([]);

  // Request root listing on connect, retry until we get entries
  useEffect(() => {
    if (!connected) return;
    sendMessage({ type: "list", path: "." });
    const interval = setInterval(() => {
      setRootEntries((cur) => {
        if (cur.length === 0) sendMessage({ type: "list", path: "." });
        return cur;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, [connected, sendMessage]);

  // Process list_response messages
  useEffect(() => {
    if (messages.length === 0) return;
    const msg = messages[messages.length - 1];
    if (msg.type === "list_response" && msg.entries) {
      if (msg.path === ".") setRootEntries(msg.entries);
      setTree((prev) => {
        const next = new Map(prev);
        const existing = next.get(msg.path);
        next.set(msg.path, {
          entry: existing?.entry ?? {
            name: msg.path.split("/").pop() || ".",
            path: msg.path,
            type: "directory",
          },
          children: msg.entries!,
          expanded: true,
        });
        return next;
      });
    }
  }, [messages]);

  const handleToggle = useCallback(
    (path: string) => {
      setTree((prev) => {
        const next = new Map(prev);
        const existing = next.get(path);
        if (existing?.expanded) {
          next.set(path, { ...existing, expanded: false });
        } else if (existing?.children) {
          next.set(path, { ...existing, expanded: true });
        } else {
          next.set(path, {
            entry: existing?.entry ?? {
              name: path.split("/").pop() || path,
              path,
              type: "directory",
            },
            children: undefined,
            expanded: true,
          });
          sendMessage({ type: "list", path });
        }
        return next;
      });
    },
    [sendMessage],
  );

  function TreeItem({ entry, depth }: { entry: FileEntry; depth: number }) {
    const node = tree.get(entry.path);
    const isDir = entry.type === "directory";
    const expanded = node?.expanded ?? false;
    const isSelected = entry.path === selectedFile;

    return (
      <div>
        <button
          onClick={() => (isDir ? handleToggle(entry.path) : onSelectFile(entry.path))}
          className={`w-full flex items-center gap-1 py-[3px] pr-2 text-[12px] leading-tight
                      hover:bg-slate-700/40 transition-colors
                      ${isSelected ? "bg-blue-600/20 text-slate-100" : "text-slate-400"}`}
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
        >
          {isDir ? (
            expanded ? (
              <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
            )
          ) : (
            <span className="w-3.5 flex-shrink-0" />
          )}
          {isDir ? (
            expanded ? (
              <FolderOpen className="w-3.5 h-3.5 flex-shrink-0 text-blue-400/80" />
            ) : (
              <Folder className="w-3.5 h-3.5 flex-shrink-0 text-blue-400/80" />
            )
          ) : (
            <File className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
          )}
          <span className="truncate ml-0.5">{entry.name}</span>
        </button>
        {isDir && expanded && node?.children?.map((child) => (
          <TreeItem key={child.path} entry={child} depth={depth + 1} />
        ))}
      </div>
    );
  }

  const handleReload = useCallback(() => {
    setTree(new Map());
    setRootEntries([]);
    sendMessage({ type: "list", path: "." });
  }, [sendMessage]);

  // Expose reload to parent via ref
  useEffect(() => {
    if (reloadRef) reloadRef.current = handleReload;
    return () => { if (reloadRef) reloadRef.current = null; };
  }, [reloadRef, handleReload]);

  if (!connected) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-slate-600 text-xs italic">Agent not connected</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto log-scroll py-1">
      {rootEntries.length === 0 ? (
        <p className="text-slate-600 text-xs italic px-3 py-4">Loading...</p>
      ) : (
        rootEntries.map((entry) => (
          <TreeItem key={entry.path} entry={entry} depth={0} />
        ))
      )}
    </div>
  );
}
