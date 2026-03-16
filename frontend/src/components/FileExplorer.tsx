import { useState, useEffect, useCallback, useRef } from "react";
import {
  ChevronRight,
  ChevronDown,
  File,
  Folder,
  FolderOpen,
  X,
  ArrowLeft,
} from "lucide-react";
import type { FileEntry, FileMessage } from "../types";

/* ---------- language detection for syntax colouring ---------- */

const EXT_LANG: Record<string, string> = {
  ".py": "python",
  ".js": "javascript",
  ".ts": "typescript",
  ".tsx": "tsx",
  ".jsx": "jsx",
  ".json": "json",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".toml": "toml",
  ".md": "markdown",
  ".html": "html",
  ".css": "css",
  ".sh": "bash",
  ".rs": "rust",
  ".go": "go",
  ".java": "java",
  ".sql": "sql",
  ".xml": "xml",
  ".rb": "ruby",
  ".php": "php",
};

function langFromPath(path: string): string {
  const ext = "." + path.split(".").pop()?.toLowerCase();
  return EXT_LANG[ext] || "text";
}

/* ---------- tree node state ---------- */

interface TreeNode {
  entry: FileEntry;
  children?: FileEntry[];
  expanded: boolean;
  loading: boolean;
}

/* ---------- component props ---------- */

interface FileExplorerProps {
  messages: FileMessage[];
  sendMessage: (data: unknown) => void;
  connected: boolean;
}

/* ---------- TreeItem ---------- */

function TreeItem({
  entry,
  node,
  depth,
  onToggle,
  onFileClick,
  selectedPath,
}: {
  entry: FileEntry;
  node?: TreeNode;
  depth: number;
  onToggle: (path: string) => void;
  onFileClick: (path: string) => void;
  selectedPath: string | null;
}) {
  const isDir = entry.type === "directory";
  const expanded = node?.expanded ?? false;
  const isSelected = entry.path === selectedPath;

  return (
    <div>
      <button
        onClick={() => (isDir ? onToggle(entry.path) : onFileClick(entry.path))}
        className={`w-full flex items-center gap-1 py-0.5 pr-2 text-xs hover:bg-slate-800/60 transition-colors
                    ${isSelected ? "bg-slate-800 text-slate-100" : "text-slate-400"}`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {isDir ? (
          expanded ? (
            <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
          )
        ) : (
          <span className="w-3.5" />
        )}
        {isDir ? (
          expanded ? (
            <FolderOpen className="w-3.5 h-3.5 flex-shrink-0 text-blue-400" />
          ) : (
            <Folder className="w-3.5 h-3.5 flex-shrink-0 text-blue-400" />
          )
        ) : (
          <File className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
        )}
        <span className="truncate">{entry.name}</span>
      </button>
      {isDir && expanded && node?.children && (
        <TreeChildren
          entries={node.children}
          depth={depth + 1}
          nodes={undefined}
          onToggle={onToggle}
          onFileClick={onFileClick}
          selectedPath={selectedPath}
          getNode={undefined}
        />
      )}
    </div>
  );
}

/* ---------- TreeChildren (renders a list; uses parent's getNode) ---------- */

function TreeChildren({
  entries,
  depth,
  onToggle,
  onFileClick,
  selectedPath,
}: {
  entries: FileEntry[];
  depth: number;
  nodes: unknown;
  onToggle: (path: string) => void;
  onFileClick: (path: string) => void;
  selectedPath: string | null;
  getNode: unknown;
}) {
  return (
    <>
      {entries.map((e) => (
        <TreeItemConnected
          key={e.path}
          entry={e}
          depth={depth}
          onToggle={onToggle}
          onFileClick={onFileClick}
          selectedPath={selectedPath}
        />
      ))}
    </>
  );
}

/* thin wrapper — the real node lookup happens in the parent via context */
function TreeItemConnected(props: {
  entry: FileEntry;
  depth: number;
  onToggle: (path: string) => void;
  onFileClick: (path: string) => void;
  selectedPath: string | null;
}) {
  return <TreeItem {...props} />;
}

/* ---------- main FileExplorer ---------- */

export default function FileExplorer({
  messages,
  sendMessage,
  connected,
}: FileExplorerProps) {
  const [tree, setTree] = useState<Map<string, TreeNode>>(new Map());
  const [rootEntries, setRootEntries] = useState<FileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const contentRef = useRef<HTMLPreElement>(null);
  const rootRequested = useRef(false);

  // Request root listing on connect
  useEffect(() => {
    if (connected && !rootRequested.current) {
      rootRequested.current = true;
      sendMessage({ type: "list", path: "." });
    }
  }, [connected, sendMessage]);

  // Reset on disconnect
  useEffect(() => {
    if (!connected) {
      rootRequested.current = false;
    }
  }, [connected]);

  // Process incoming messages
  useEffect(() => {
    if (messages.length === 0) return;
    const msg = messages[messages.length - 1];

    if (msg.type === "list_response" && msg.entries) {
      if (msg.path === ".") {
        setRootEntries(msg.entries);
      }
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
          loading: false,
        });
        return next;
      });
    } else if (msg.type === "read_response" && msg.content !== undefined) {
      setFileContent(msg.content);
      setFileError(null);
      setLoadingFile(false);
    } else if (msg.type === "error") {
      if (loadingFile && msg.path === selectedFile) {
        setFileError(msg.error || "Unknown error");
        setFileContent(null);
        setLoadingFile(false);
      }
    }
  }, [messages, loadingFile, selectedFile]);

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
            loading: true,
          });
          sendMessage({ type: "list", path });
        }
        return next;
      });
    },
    [sendMessage],
  );

  const handleFileClick = useCallback(
    (path: string) => {
      setSelectedFile(path);
      setFileContent(null);
      setFileError(null);
      setLoadingFile(true);
      sendMessage({ type: "read", path });
    },
    [sendMessage],
  );

  // Provide node lookup to tree items
  const getNode = useCallback((path: string) => tree.get(path), [tree]);

  // Enhance TreeItem to use getNode
  function ConnectedTreeItem({
    entry,
    depth,
  }: {
    entry: FileEntry;
    depth: number;
  }) {
    const node = getNode(entry.path);
    return (
      <div>
        <button
          onClick={() =>
            entry.type === "directory"
              ? handleToggle(entry.path)
              : handleFileClick(entry.path)
          }
          className={`w-full flex items-center gap-1 py-0.5 pr-2 text-xs hover:bg-slate-800/60 transition-colors
                      ${entry.path === selectedFile ? "bg-slate-800 text-slate-100" : "text-slate-400"}`}
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          {entry.type === "directory" ? (
            node?.expanded ? (
              <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
            )
          ) : (
            <span className="w-3.5" />
          )}
          {entry.type === "directory" ? (
            node?.expanded ? (
              <FolderOpen className="w-3.5 h-3.5 flex-shrink-0 text-blue-400" />
            ) : (
              <Folder className="w-3.5 h-3.5 flex-shrink-0 text-blue-400" />
            )
          ) : (
            <File className="w-3.5 h-3.5 flex-shrink-0 text-slate-500" />
          )}
          <span className="truncate">{entry.name}</span>
        </button>
        {entry.type === "directory" && node?.expanded && node.children && (
          <>
            {node.children.map((child) => (
              <ConnectedTreeItem
                key={child.path}
                entry={child}
                depth={depth + 1}
              />
            ))}
          </>
        )}
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-slate-600 text-xs italic">Agent not connected</p>
      </div>
    );
  }

  // File viewer mode
  if (selectedFile && (fileContent !== null || fileError || loadingFile)) {
    return (
      <div className="h-full flex flex-col">
        {/* File header */}
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-slate-800 bg-slate-900/50">
          <button
            onClick={() => {
              setSelectedFile(null);
              setFileContent(null);
              setFileError(null);
            }}
            className="p-0.5 text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
          </button>
          <File className="w-3.5 h-3.5 text-slate-500" />
          <span className="text-xs text-slate-300 truncate">{selectedFile}</span>
          <span className="text-[10px] text-slate-600 ml-auto">{langFromPath(selectedFile)}</span>
          <button
            onClick={() => {
              setSelectedFile(null);
              setFileContent(null);
              setFileError(null);
            }}
            className="p-0.5 text-slate-500 hover:text-slate-300 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        {/* File content */}
        <div className="flex-1 overflow-auto bg-slate-950 log-scroll">
          {loadingFile && (
            <p className="p-4 text-slate-600 text-xs italic">Loading...</p>
          )}
          {fileError && (
            <p className="p-4 text-red-400 text-xs">{fileError}</p>
          )}
          {fileContent !== null && (
            <pre
              ref={contentRef}
              className="p-3 text-xs leading-relaxed text-slate-300 font-mono whitespace-pre overflow-x-auto"
            >
              {fileContent.split("\n").map((line, i) => (
                <div key={i} className="flex hover:bg-slate-900/50">
                  <span className="inline-block w-10 text-right pr-3 text-slate-600 select-none flex-shrink-0">
                    {i + 1}
                  </span>
                  <span className="flex-1">{line}</span>
                </div>
              ))}
            </pre>
          )}
        </div>
      </div>
    );
  }

  // Tree view mode
  return (
    <div className="h-full overflow-y-auto log-scroll py-1">
      {rootEntries.length === 0 ? (
        <p className="text-slate-600 text-xs italic px-3 py-4">Loading tree...</p>
      ) : (
        rootEntries.map((entry) => (
          <ConnectedTreeItem key={entry.path} entry={entry} depth={0} />
        ))
      )}
    </div>
  );
}
