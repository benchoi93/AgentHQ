"""AgentHQ Agent core — platform-agnostic orchestration.

Features:
  - Session discovery (via platform backend)
  - Heartbeat with command dispatch
  - Log streaming
  - Chat relay (via platform backend)
  - File browsing
  - Terminal capture (via platform backend)
  - Session creation (via platform backend)
  - .claude folder sync
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml

from . import __version__
from .backends import SessionBackend, get_backend

log = logging.getLogger("agenthq-agent")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_HEARTBEAT_INTERVAL = 10
DEFAULT_LOG_POLL_INTERVAL = 2
DEFAULT_TERMINAL_POLL_INTERVAL = 1
DEFAULT_SYNC_INTERVAL = 30


def load_config(cli_args: argparse.Namespace) -> dict[str, Any]:
    """Merge YAML config file with CLI overrides. CLI wins."""
    cfg: dict[str, Any] = {}
    if cli_args.config and Path(cli_args.config).exists():
        with open(cli_args.config, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    if cli_args.server:
        cfg["server_url"] = cli_args.server
    if cli_args.token:
        cfg["token"] = cli_args.token
    if cli_args.machine:
        cfg["machine_name"] = cli_args.machine
    cfg.setdefault("server_url", "http://localhost:8420")
    cfg.setdefault("token", "")
    cfg.setdefault("machine_name", platform.node())
    cfg.setdefault("heartbeat_interval", DEFAULT_HEARTBEAT_INTERVAL)
    cfg.setdefault("log_poll_interval", DEFAULT_LOG_POLL_INTERVAL)
    cfg.setdefault("terminal_poll_interval", DEFAULT_TERMINAL_POLL_INTERVAL)
    cfg.setdefault("sync_interval", DEFAULT_SYNC_INTERVAL)
    cfg.setdefault("extra_sessions", [])
    cfg.setdefault("extra_project_dirs", [])
    cfg.setdefault("default_sessions", [])
    cfg.setdefault("sync_enabled", True)
    # Track config dir for state file storage
    if cli_args.config and Path(cli_args.config).exists():
        cfg["_config_dir"] = str(Path(cli_args.config).resolve().parent)
    return cfg


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _is_wsl() -> bool:
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _windows_home_in_wsl() -> Path | None:
    try:
        result = subprocess.run(
            ["wslpath", "-u", subprocess.run(
                ["cmd.exe", "/C", "echo", "%USERPROFILE%"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            p = Path(result.stdout.strip())
            if p.is_dir():
                return p
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    mnt_users = Path("/mnt/c/Users")
    if mnt_users.is_dir():
        for entry in mnt_users.iterdir():
            if entry.name in ("Public", "Default", "Default User", "All Users"):
                continue
            if (entry / ".claude").is_dir():
                return entry
    return None


IS_WSL = _is_wsl()


# ---------------------------------------------------------------------------
# Session Discovery
# ---------------------------------------------------------------------------

def _session_id(project_path: str, pid: int | None = None, suffix: int = 0) -> str:
    raw = f"{platform.node()}:{project_path}"
    if suffix > 0:
        raw += f":{suffix}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _claude_projects_dirs(cfg_dirs: list[str] | None = None) -> list[Path]:
    dirs: list[Path] = []
    native = Path.home() / ".claude" / "projects"
    if native.is_dir():
        dirs.append(native)
    if IS_WSL:
        win_home = _windows_home_in_wsl()
        if win_home:
            win_projects = win_home / ".claude" / "projects"
            if win_projects.is_dir():
                dirs.append(win_projects)
    for d in cfg_dirs or []:
        p = Path(d)
        if p.is_dir():
            dirs.append(p)
    return dirs


def _wslpath(win_path: str) -> str:
    """Convert a Windows path to a WSL path (pure string, no subprocess).

    Handles: C:\\Users\\... → /mnt/c/Users/...
             C:/Users/...  → /mnt/c/Users/...
    """
    p = win_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:]
        return f"/mnt/{drive}{rest}"
    return p


def _decode_claude_project_dir(entry: Path) -> tuple[str, str]:
    """Decode a .claude/projects/ directory name into (project_name, project_path).

    Claude encodes absolute paths in two formats:
      Linux:   -home-chois-gitsrcs-AgentHQ        (leading '-' = '/')
      Windows: C--Users-chois-Gitsrcs-AgentHQ      (drive letter, '--' = ':\\')

    Since dir names may contain '-' or '_', we greedily reconstruct
    the path by checking which segments exist on disk.
    """
    raw = entry.name

    # Detect Windows drive format: "C--Users-..." (letter + double dash)
    is_win_format = (
        len(raw) >= 3
        and raw[0].isalpha()
        and raw[1:3] == "--"
    )

    if is_win_format:
        drive = raw[0]
        # After "C--", split remaining on '-'
        parts = raw[3:].split("-") if len(raw) > 3 else []
        if platform.system() == "Windows":
            prefix = f"{drive.upper()}:"
        else:
            prefix = f"/mnt/{drive.lower()}"
        segments = parts
    elif raw.startswith("-"):
        # Linux format: "-home-chois-..."
        parts = raw[1:].split("-")
        # Check if first segment is a single uppercase letter (WSL Linux-side scan of Windows dirs)
        if IS_WSL and len(parts) >= 1 and len(parts[0]) == 1 and parts[0].isalpha() and parts[0].isupper():
            prefix = f"/mnt/{parts[0].lower()}"
            segments = parts[1:]
        else:
            prefix = ""
            segments = parts
    else:
        return raw, str(entry)

    if not segments:
        name = prefix.rsplit("/", 1)[-1] if "/" in prefix else raw
        return name, prefix or str(entry)

    # Greedily reconstruct path by checking the filesystem.
    # Claude encodes both '/' and '_' as '-', so try '-', '_', and '/'
    # separators when resolving each segment group.
    project_path = prefix
    i = 0
    while i < len(segments):
        best = None
        for j in range(len(segments), i, -1):
            for sep in ("-", "_"):
                candidate = project_path + "/" + sep.join(segments[i:j])
                if Path(candidate).exists():
                    best = candidate
                    i = j
                    break
            if best:
                break
        if best:
            project_path = best
        else:
            project_path = project_path + "/" + segments[i]
            i += 1

    name = project_path.rsplit("/", 1)[-1] if "/" in project_path else project_path
    return name, project_path


def list_known_projects(cfg_dirs: list[str] | None = None) -> list[dict[str, Any]]:
    """List all projects from .claude/projects/ history for session suggestions."""
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for projects_dir in _claude_projects_dirs(cfg_dirs):
        for entry in projects_dir.iterdir():
            if not entry.is_dir():
                continue
            name, project_path = _decode_claude_project_dir(entry)
            if not name or name.startswith("."):
                continue
            sid = _session_id(project_path)
            if sid in seen:
                continue
            seen.add(sid)
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            projects.append({
                "id": sid,
                "name": name,
                "path": project_path,
                "last_activity": mtime,
            })
    projects.sort(key=lambda p: p["last_activity"], reverse=True)
    return projects


def discover_all_sessions(
    extra: list[dict[str, Any]],
    extra_project_dirs: list[str] | None = None,
    backend: SessionBackend | None = None,
) -> list[dict[str, Any]]:
    """Discover sessions: managed sessions (via backend) + config entries.

    No automatic process detection — use the + button with project
    suggestions from .claude/projects/ to create sessions instead.
    """
    merged: dict[str, dict[str, Any]] = {}
    # Include managed sessions (created via UI)
    if backend is not None:
        for s in backend.discover_managed_sessions():
            merged[s["id"]] = s
    # Include extra sessions from config
    for ex in extra:
        path = ex.get("path", "")
        sid = _session_id(path)
        if sid not in merged:
            merged[sid] = {
                "id": sid,
                "project": ex.get("name", Path(path).name),
                "status": "running",
                "pid": None,
                "path": path,
                "last_activity": time.time(),
            }
    return list(merged.values())


# ---------------------------------------------------------------------------
# Shared session cache
# ---------------------------------------------------------------------------

_cached_sessions: list[dict[str, Any]] = []
_cache_lock = asyncio.Lock()
_backend: SessionBackend | None = None  # set by run()


async def _discover_async(
    extra: list[dict[str, Any]],
    extra_project_dirs: list[str] | None = None,
) -> list[dict[str, Any]]:
    global _cached_sessions
    sessions = await asyncio.to_thread(
        discover_all_sessions, extra, extra_project_dirs, _backend,
    )
    async with _cache_lock:
        _cached_sessions = sessions
    return sessions


# ---------------------------------------------------------------------------
# Heartbeat + Command Processing
# ---------------------------------------------------------------------------

_known_projects_cache: dict[str, Any] = {"projects": [], "ts": 0.0}
_KNOWN_PROJECTS_TTL = 300  # refresh every 5 min
_known_projects_refreshing = False


async def _refresh_known_projects(cfg_dirs: list[str] | None) -> None:
    """Refresh known projects cache in background — never blocks heartbeat."""
    global _known_projects_refreshing
    if _known_projects_refreshing:
        return
    _known_projects_refreshing = True
    try:
        projects = await asyncio.to_thread(list_known_projects, cfg_dirs)
        _known_projects_cache["projects"] = projects
        _known_projects_cache["ts"] = time.time()
        log.debug("Refreshed known projects (%d)", len(projects))
    except Exception as exc:
        log.debug("Known projects scan failed: %s", exc)
    finally:
        _known_projects_refreshing = False


async def heartbeat_loop(cfg: dict[str, Any], http: aiohttp.ClientSession) -> None:
    url = cfg["server_url"].rstrip("/") + "/api/agents/heartbeat"
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    interval = cfg["heartbeat_interval"]

    while True:
        try:
            sessions = await _discover_async(
                cfg.get("extra_sessions", []),
                cfg.get("extra_project_dirs"),
            )
            # Refresh known projects in background (never blocks heartbeat)
            if time.time() - _known_projects_cache["ts"] >= _KNOWN_PROJECTS_TTL:
                asyncio.create_task(_refresh_known_projects(cfg.get("extra_project_dirs")))
            payload = {
                "agent_name": cfg["machine_name"],
                "machine": platform.node(),
                "agent_version": __version__,
                "sessions": sessions,
                "known_projects": _known_projects_cache["projects"],
            }
            async with http.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    log.info("Heartbeat OK (%d sessions)", len(sessions))
                    # Process commands from server
                    for cmd in body.get("commands", []):
                        asyncio.create_task(_safe_handle_command(cfg, http, cmd))
                else:
                    body = await resp.text()
                    log.warning("Heartbeat %d: %s", resp.status, body[:200])
        except asyncio.TimeoutError:
            log.warning("Heartbeat timed out")
        except aiohttp.ClientError as exc:
            log.warning("Heartbeat failed: %s: %s", type(exc).__name__, exc)
        except Exception as exc:
            log.error("Heartbeat error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


async def _safe_handle_command(cfg: dict[str, Any], http: aiohttp.ClientSession, cmd: dict) -> None:
    """Wrapper that ensures command exceptions are logged and reported."""
    try:
        await _handle_command(cfg, http, cmd)
    except Exception as exc:
        log.error("Unhandled error processing command %s: %s", cmd.get("id"), exc, exc_info=True)
        try:
            await _report_command(cfg, http, cmd["id"], "failed", f"Agent error: {exc}")
        except Exception:
            pass


async def _handle_command(cfg: dict[str, Any], http: aiohttp.ClientSession, cmd: dict) -> None:
    """Process a command dispatched by the server."""
    cmd_id = cmd["id"]
    cmd_type = cmd["type"]
    payload = json.loads(cmd["payload"]) if isinstance(cmd["payload"], str) else cmd["payload"]
    log.info("Processing command %d: %s", cmd_id, cmd_type)

    if _backend is None:
        await _report_command(cfg, http, cmd_id, "failed", "Backend not initialized")
        return

    if cmd_type == "create_session":
        log.info("create_session: directory=%r name=%r", payload.get("directory"), payload.get("session_name"))
        result = await asyncio.to_thread(
            _backend.create_session,
            payload["directory"],
            payload.get("session_name", ""),
        )
        log.info("create_session result: %s", result)
        status = "completed" if result.get("ok") else "failed"
        await _report_command(cfg, http, cmd_id, status, json.dumps(result))
    elif cmd_type == "restart_session":
        result = await asyncio.to_thread(
            _backend.restart_session,
            payload["session_id"],
            payload.get("directory", ""),
            payload.get("session_name", ""),
        )
        status = "completed" if result.get("ok") else "failed"
        await _report_command(cfg, http, cmd_id, status, json.dumps(result))
    elif cmd_type == "stop_session":
        result = await asyncio.to_thread(
            _backend.stop_session,
            payload["session_id"],
        )
        status = "completed" if result.get("ok") else "failed"
        await _report_command(cfg, http, cmd_id, status, json.dumps(result))
    elif cmd_type == "run_shell":
        shell_cmd = payload.get("command", "")
        cwd = payload.get("cwd")
        timeout = payload.get("timeout", 30)
        log.info("run_shell: %r (cwd=%r, timeout=%d)", shell_cmd, cwd, timeout)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                shell_cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            result = {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout[-4000:] if proc.stdout else "",
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": "Command timed out"}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        status = "completed" if result.get("ok") else "failed"
        await _report_command(cfg, http, cmd_id, status, json.dumps(result))
    else:
        await _report_command(cfg, http, cmd_id, "failed", f"Unknown command: {cmd_type}")


async def _report_command(
    cfg: dict[str, Any], http: aiohttp.ClientSession,
    cmd_id: int, status: str, result: str,
) -> None:
    url = cfg["server_url"].rstrip("/") + f"/api/agents/commands/{cmd_id}/result"
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    try:
        async with http.post(url, json={"status": status, "result": result}, headers=headers) as resp:
            if resp.status != 200:
                log.warning("Failed to report command %d result: %d", cmd_id, resp.status)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        log.warning("Failed to report command %d result", cmd_id)


# ---------------------------------------------------------------------------
# Log Streaming
# ---------------------------------------------------------------------------

def _find_log_file(session_path: str) -> Path | None:
    candidates = [
        Path(session_path) / ".claude" / "conversation.log",
        Path(session_path) / ".claude" / "logs" / "latest.log",
    ]
    for c in candidates:
        if c.exists():
            return c
    session_p = Path(session_path)
    if session_p.is_dir():
        logs = sorted(session_p.glob("**/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            return logs[0]
    return None


async def stream_logs_for_session(
    cfg: dict[str, Any], session: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    poll_interval = cfg.get("log_poll_interval", DEFAULT_LOG_POLL_INTERVAL)
    # Wait for log file to appear instead of returning immediately,
    # because an early return marks the task as done() and the session
    # manager would restart ALL tasks (including the terminal).
    log_file = _find_log_file(session["path"])
    while not log_file:
        await asyncio.sleep(poll_interval * 5)
        log_file = _find_log_file(session["path"])
    log.debug("Found log file for session %s: %s", session["id"], log_file)

    ws_url = cfg["server_url"].rstrip("/").replace("http", "ws", 1)
    ws_url += f"/ws/logs/{session['id']}?token={cfg['token']}&role=agent"
    poll_interval = cfg.get("log_poll_interval", DEFAULT_LOG_POLL_INTERVAL)

    while True:
        try:
            async with http.ws_connect(ws_url, heartbeat=20) as ws:
                log.info("Log WS connected for session %s", session["id"])
                last_size = log_file.stat().st_size
                while True:
                    await asyncio.sleep(poll_interval)
                    try:
                        current_size = log_file.stat().st_size
                    except OSError:
                        continue
                    if current_size <= last_size:
                        last_size = current_size
                        continue
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_content = f.read()
                    last_size = current_size
                    if new_content.strip():
                        await ws.send_json({
                            "type": "log",
                            "session_id": session["id"],
                            "content": new_content,
                            "timestamp": time.time(),
                        })
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("Log stream error for %s: %s", session["id"], exc)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Chat Relay
# ---------------------------------------------------------------------------

async def relay_for_session(
    cfg: dict[str, Any], session: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    ws_url = cfg["server_url"].rstrip("/").replace("http", "ws", 1)
    ws_url += f"/ws/relay/{session['id']}?token={cfg['token']}&role=agent"
    sid = session["id"]

    while True:
        try:
            async with http.ws_connect(ws_url, heartbeat=20) as ws:
                log.info("Relay WS connected for session %s", sid)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("type") != "input":
                            continue
                        content = data.get("content", "")

                        if _backend is not None:
                            result = _backend.send_keys(session, content)
                            if result:
                                await ws.send_json({"type": "output", "content": result})
                            else:
                                await ws.send_json({"type": "output",
                                                    "content": "No terminal pane found for session"})
                        else:
                            await ws.send_json({"type": "output",
                                                "content": "Backend not initialized"})

                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("Relay error for %s: %s", sid, exc)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# File Browsing
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".html", ".css", ".scss", ".sh", ".bash", ".zsh",
    ".env", ".cfg", ".ini", ".conf", ".xml", ".sql", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".r",
    ".dockerfile", ".gitignore", ".editorconfig", ".lock", ".csv", ".log",
    ".vue", ".svelte", ".astro",
}
_MAX_FILE_SIZE = 1_000_000

_TEXT_NAMES = {
    "dockerfile", "makefile", "justfile", "procfile", "gemfile",
    "rakefile", "vagrantfile", ".gitignore", ".dockerignore",
    "claude.md", "readme", "license", "changelog",
}


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS or path.name.lower() in _TEXT_NAMES


def _list_directory(base: Path, rel_path: str) -> dict[str, Any]:
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        return {"type": "error", "path": rel_path, "error": "Access denied"}
    if not target.is_dir():
        return {"type": "error", "path": rel_path, "error": "Not a directory"}
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".") and child.is_dir():
                if child.name not in {".github", ".vscode"}:
                    continue
            if child.name in {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"}:
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child.relative_to(base)),
                    "type": "directory" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else None,
                })
            except OSError:
                continue
    except PermissionError:
        return {"type": "error", "path": rel_path, "error": "Permission denied"}
    return {"type": "list_response", "path": rel_path, "entries": entries}


def _read_file(base: Path, rel_path: str) -> dict[str, Any]:
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        return {"type": "error", "path": rel_path, "error": "Access denied"}
    if not target.is_file():
        return {"type": "error", "path": rel_path, "error": "Not a file"}
    if target.stat().st_size > _MAX_FILE_SIZE:
        return {"type": "error", "path": rel_path, "error": "File too large (>1 MB)"}
    if not _is_text_file(target):
        return {"type": "error", "path": rel_path, "error": f"Binary file ({target.suffix})"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"type": "read_response", "path": rel_path, "content": content}
    except OSError as exc:
        return {"type": "error", "path": rel_path, "error": str(exc)}


async def files_for_session(
    cfg: dict[str, Any], session: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    ws_url = cfg["server_url"].rstrip("/").replace("http", "ws", 1)
    ws_url += f"/ws/files/{session['id']}?token={cfg['token']}&role=agent"
    base = Path(session.get("path", "."))
    if not base.is_dir():
        return

    while True:
        try:
            async with http.ws_connect(ws_url, heartbeat=20) as ws:
                log.info("Files WS connected for session %s", session["id"])
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        req_type = data.get("type", "")
                        req_path = data.get("path", ".")
                        if req_type == "list":
                            resp = await asyncio.to_thread(_list_directory, base, req_path)
                        elif req_type == "read":
                            resp = await asyncio.to_thread(_read_file, base, req_path)
                        else:
                            resp = {"type": "error", "path": req_path, "error": f"Unknown: {req_type}"}
                        await ws.send_json(resp)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("Files error for %s: %s", session["id"], exc)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Terminal Capture (delegated to backend)
# ---------------------------------------------------------------------------

async def terminal_for_session(
    cfg: dict[str, Any], session: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    """Interactive shell terminal via backend (tmux attach on Unix, ConPTY on Windows)."""
    if _backend is None:
        return
    sid = session["id"]
    ws_url = cfg["server_url"].rstrip("/").replace("http", "ws", 1)
    ws_url += f"/ws/terminal/{sid}?token={cfg['token']}&role=agent"
    while True:
        try:
            await _backend.attach_terminal(ws_url, session, http, f"terminal:{sid}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Terminal error for %s: %s", sid, exc)
        await asyncio.sleep(5)


async def claude_terminal_for_session(
    cfg: dict[str, Any], session: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    """Interactive Claude Code terminal via backend."""
    if _backend is None:
        return
    sid = session["id"]
    ws_url = cfg["server_url"].rstrip("/").replace("http", "ws", 1)
    ws_url += f"/ws/terminal/{sid}__claude?token={cfg['token']}&role=agent"
    while True:
        try:
            await _backend.attach_claude_terminal(ws_url, session, http)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Claude terminal error for %s: %s", sid, exc)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# .claude Folder Sync
# ---------------------------------------------------------------------------

SYNC_IGNORE_DIRS = {"projects", "logs", "tmp", "cache", "statsig", "ide"}
SYNC_MAX_FILE_SIZE = 100_000  # 100 KB
DEFAULT_GIT_SYNC_INTERVAL = 300  # 5 minutes


def _scan_claude_dir() -> dict[str, dict[str, str]]:
    """Scan ~/.claude/ for syncable files. Returns {rel_path: {path, content, hash}}."""
    claude_dir = Path.home() / ".claude"
    if not claude_dir.is_dir():
        return {}
    files: dict[str, dict[str, str]] = {}
    for child in claude_dir.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(claude_dir)
        if any(part in SYNC_IGNORE_DIRS for part in rel.parts):
            continue
        if child.stat().st_size > SYNC_MAX_FILE_SIZE:
            continue
        try:
            content = child.read_text(encoding="utf-8")
            h = hashlib.sha256(content.encode()).hexdigest()
            files[str(rel)] = {"path": str(rel), "content": content, "hash": h}
        except (OSError, UnicodeDecodeError):
            continue
    return files


def _write_sync_file(rel_path: str, content: str) -> None:
    target = Path.home() / ".claude" / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


async def sync_loop(cfg: dict[str, Any], http: aiohttp.ClientSession) -> None:
    """Periodically sync .claude/ folder with server."""
    if not cfg.get("sync_enabled", True):
        return
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    base_url = cfg["server_url"].rstrip("/")
    interval = cfg.get("sync_interval", DEFAULT_SYNC_INTERVAL)
    agent_id = hashlib.sha256(
        f"{cfg['machine_name']}@{platform.node()}".encode()
    ).hexdigest()[:16]

    # Wait for first heartbeat to establish connection
    await asyncio.sleep(interval)

    while True:
        try:
            local_files = await asyncio.to_thread(_scan_claude_dir)

            # Get server manifest
            async with http.get(
                f"{base_url}/api/sync/manifest", headers=headers,
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(interval)
                    continue
                server_data = await resp.json()

            server_files = {f["path"]: f["hash"] for f in server_data.get("files", [])}

            # Upload local files that differ
            to_upload = [
                info for path, info in local_files.items()
                if info["hash"] != server_files.get(path)
            ]
            if to_upload:
                async with http.post(
                    f"{base_url}/api/sync/upload?agent_id={agent_id}",
                    json={"files": to_upload},
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        updated = body.get("updated", [])
                        if updated:
                            log.info("Sync: uploaded %d files", len(updated))

            # Download server files we don't have or that differ
            for path, server_hash in server_files.items():
                local_hash = local_files.get(path, {}).get("hash")
                if local_hash != server_hash:
                    async with http.get(
                        f"{base_url}/api/sync/file",
                        params={"path": path},
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            file_data = await resp.json()
                            await asyncio.to_thread(
                                _write_sync_file, path, file_data["content"],
                            )
                            log.info("Sync: downloaded %s", path)

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.debug("Sync error: %s", exc)
        except Exception as exc:
            log.warning("Sync error: %s", exc)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Git-based .claude sync
# ---------------------------------------------------------------------------

def _git(claude_dir: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a git command in the .claude directory."""
    return subprocess.run(
        ["git", *args],
        cwd=claude_dir,
        capture_output=True,
        text=True,
        timeout=60,
        **kwargs,
    )


def _resolve_conflicts_with_claude(claude_dir: Path) -> bool:
    """Use Claude CLI to resolve git merge conflicts. Returns True if resolved."""
    result = _git(claude_dir, "diff", "--name-only", "--diff-filter=U")
    conflicted = [f for f in result.stdout.strip().split("\n") if f]
    if not conflicted:
        return True

    log.info("Git sync: resolving %d conflicted files with Claude", len(conflicted))

    for filepath in conflicted:
        full_path = claude_dir / filepath
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            log.warning("Git sync: cannot read conflicted file %s, using ours", filepath)
            _git(claude_dir, "checkout", "--ours", filepath)
            _git(claude_dir, "add", filepath)
            continue

        prompt = (
            f"This Claude Code settings file (~/.claude/{filepath}) has git merge "
            f"conflicts from syncing across machines. Resolve the conflicts by "
            f"intelligently merging both sides — keep all unique entries, prefer "
            f"newer/more complete values when they truly conflict. "
            f"Output ONLY the resolved file content with no explanation, "
            f"no markdown fences, no extra text.\n\n{content}"
        )

        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                full_path.write_text(result.stdout, encoding="utf-8")
                _git(claude_dir, "add", filepath)
                log.info("Git sync: resolved %s with Claude", filepath)
            else:
                log.warning("Git sync: Claude failed on %s (rc=%d), using ours",
                            filepath, result.returncode)
                _git(claude_dir, "checkout", "--ours", filepath)
                _git(claude_dir, "add", filepath)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            log.warning("Git sync: Claude unavailable (%s), using ours for %s", exc, filepath)
            _git(claude_dir, "checkout", "--ours", filepath)
            _git(claude_dir, "add", filepath)

    # Continue the rebase with resolved files
    result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=claude_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    if result.returncode != 0:
        log.warning("Git sync: rebase --continue failed, aborting: %s", result.stderr)
        _git(claude_dir, "rebase", "--abort")
        return False
    return True


def _git_sync_once(claude_dir: Path) -> None:
    """Single git sync cycle: commit local changes, pull --rebase, push."""
    if not (claude_dir / ".git").is_dir():
        log.debug("Git sync: ~/.claude is not a git repo, skipping")
        return

    # Check if remote is configured
    result = _git(claude_dir, "remote")
    if not result.stdout.strip():
        log.debug("Git sync: no remote configured, skipping")
        return

    machine = platform.node()

    # Stage and commit local changes
    result = _git(claude_dir, "status", "--porcelain")
    if result.stdout.strip():
        _git(claude_dir, "add", "-A")
        result = _git(claude_dir, "commit", "-m",
                       f"auto-sync from {machine}")
        if result.returncode == 0:
            log.info("Git sync: committed local changes from %s", machine)

    # Pull with rebase
    result = _git(claude_dir, "pull", "--rebase")
    if result.returncode != 0:
        stderr = result.stderr or ""
        stdout = result.stdout or ""
        if "CONFLICT" in stderr or "CONFLICT" in stdout:
            log.info("Git sync: merge conflicts detected, invoking Claude resolver")
            if not _resolve_conflicts_with_claude(claude_dir):
                return
        else:
            log.warning("Git sync: pull --rebase failed: %s", stderr.strip())
            # Abort if a rebase is in progress
            if (claude_dir / ".git" / "rebase-merge").exists() or \
               (claude_dir / ".git" / "rebase-apply").exists():
                _git(claude_dir, "rebase", "--abort")
            return

    # Push
    result = _git(claude_dir, "push")
    if result.returncode != 0:
        log.warning("Git sync: push failed: %s", (result.stderr or "").strip())
    else:
        log.info("Git sync: pushed successfully")


async def git_sync_loop(cfg: dict[str, Any], _http: aiohttp.ClientSession) -> None:
    """Periodically git sync ~/.claude/ folder with remote repository."""
    if not cfg.get("git_sync_enabled", False):
        return

    claude_dir = Path.home() / ".claude"
    interval = cfg.get("git_sync_interval", DEFAULT_GIT_SYNC_INTERVAL)

    # Wait for agent to settle
    await asyncio.sleep(30)

    log.info("Git sync: starting (interval=%ds)", interval)

    while True:
        try:
            await asyncio.to_thread(_git_sync_once, claude_dir)
        except Exception as exc:
            log.warning("Git sync error: %s", exc)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------

async def session_manager(cfg: dict[str, Any], http: aiohttp.ClientSession) -> None:
    tasks: dict[str, dict[str, asyncio.Task]] = {}
    interval = cfg["heartbeat_interval"]

    while True:
        async with _cache_lock:
            sessions = list(_cached_sessions)
        if not sessions:
            sessions = await _discover_async(
                cfg.get("extra_sessions", []),
                cfg.get("extra_project_dirs"),
            )
        active_ids = {s["id"] for s in sessions}

        # Kill tasks for sessions that need restart or stop
        if _backend is not None:
            restart_ids = _backend.sessions_needing_restart & set(tasks.keys())
            stop_ids = _backend.sessions_needing_stop & set(tasks.keys())
            for sid in restart_ids | stop_ids:
                action = "Restarting" if sid in restart_ids else "Stopping"
                log.info("%s tasks for session %s", action, sid)
                for t in tasks[sid].values():
                    t.cancel()
                await asyncio.gather(*tasks[sid].values(), return_exceptions=True)
                del tasks[sid]
                _backend.sessions_needing_restart.discard(sid)
                _backend.sessions_needing_stop.discard(sid)

        for s in sessions:
            sid = s["id"]
            if sid not in tasks:
                log.info("Starting tasks for session %s (%s)", sid, s["project"])
                task_factories = [
                    ("logs", stream_logs_for_session),
                    ("relay", relay_for_session),
                    ("files", files_for_session),
                    ("terminal", terminal_for_session),
                ]
                tasks[sid] = {
                    name: asyncio.create_task(fn(cfg, s, http))
                    for name, fn in task_factories
                }
            else:
                # Restart only individual dead tasks, not all of them.
                # Previously a single dead task (e.g. log stream with no log
                # file) would kill ALL tasks including the terminal PTY.
                task_factories = {
                    "logs": stream_logs_for_session,
                    "relay": relay_for_session,
                    "files": files_for_session,
                    "terminal": terminal_for_session,
                }
                for name, t in list(tasks[sid].items()):
                    if t.done():
                        log.info("Restarting dead %s task for session %s (%s)",
                                 name, sid, s["project"])
                        fn = task_factories[name]
                        tasks[sid][name] = asyncio.create_task(fn(cfg, s, http))

        gone = [sid for sid in tasks if sid not in active_ids]
        for sid in gone:
            log.info("Cleaning up tasks for session %s", sid)
            for t in tasks[sid].values():
                t.cancel()
            await asyncio.gather(*tasks[sid].values(), return_exceptions=True)
            del tasks[sid]

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _check_pidfile(state_dir: Path) -> None:
    """Ensure only one agent runs at a time. Exit if another is alive."""
    pidfile = state_dir / "agent.pid"
    if pidfile.exists():
        try:
            old_pid = int(pidfile.read_text().strip())
            os.kill(old_pid, 0)  # check if alive (signal 0 = no-op)
            log.error("Another agent is already running (PID %d). Exiting.", old_pid)
            raise SystemExit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale pidfile — previous agent died
    pidfile.write_text(str(os.getpid()))
    import atexit
    atexit.register(lambda: pidfile.unlink(missing_ok=True))


async def run(cfg: dict[str, Any]) -> None:
    global _backend
    # Store managed sessions next to the config file, or in ~/.agenthq/
    state_dir = Path(cfg.get("_config_dir", Path.home() / ".agenthq"))
    state_dir.mkdir(parents=True, exist_ok=True)
    _check_pidfile(state_dir)
    _backend = get_backend(state_dir)
    _backend.load_sessions()

    # Auto-create default sessions if configured
    for ds in cfg.get("default_sessions", []):
        ds_path = os.path.expanduser(ds["path"])
        ds_name = ds.get("name", Path(ds_path).name)
        # Check if a session for this path already exists
        already_exists = any(
            info.get("path") == ds_path for info in _backend.sessions.values()
        )
        if not already_exists and Path(ds_path).is_dir():
            result = _backend.create_session(ds_path, ds_name)
            if result.get("ok"):
                log.info("Auto-created default session '%s' at %s", ds_name, ds_path)
            else:
                log.warning("Failed to auto-create default session '%s': %s", ds_name, result.get("error"))

    log.info(
        "AgentHQ agent starting: server=%s machine=%s%s (backend=%s)",
        cfg["server_url"], cfg["machine_name"],
        " (WSL)" if IS_WSL else "",
        type(_backend).__name__,
    )

    async def resilient(name, fn, *args):
        while True:
            try:
                await fn(*args)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("%s crashed, restarting in 5s: %s", name, exc, exc_info=True)
                await asyncio.sleep(5)

    async with aiohttp.ClientSession() as http:
        await asyncio.gather(
            resilient("heartbeat", heartbeat_loop, cfg, http),
            resilient("session_manager", session_manager, cfg, http),
            resilient("sync", sync_loop, cfg, http),
            resilient("git_sync", git_sync_loop, cfg, http),
        )
