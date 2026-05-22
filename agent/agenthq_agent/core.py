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
import re
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
    cfg.setdefault("auto_compact_idle_minutes", 30)   # 0 to disable
    cfg.setdefault("auto_clear_idle_minutes", 300)     # 5hr, 0 to disable
    cfg.setdefault("rate_limit_watcher_enabled", False)  # auto cc→cb switching
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
# Account Pool
# ---------------------------------------------------------------------------

def _resolve_account(cfg: dict[str, Any], account: str) -> str:
    """Resolve an account name to its config_dir path.

    Falls back to default_account if no account specified,
    or empty string if accounts aren't configured.
    """
    accounts = cfg.get("accounts", {})
    if not accounts:
        return ""
    if not account:
        account = cfg.get("default_account", "")
    if account and account in accounts:
        return accounts[account].get("config_dir", "")
    return ""


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


def _build_account_map(cfg: dict[str, Any]) -> dict[str, str]:
    """Build config_dir → account name mapping from config."""
    return {
        acct.get("config_dir", ""): name
        for name, acct in cfg.get("accounts", {}).items()
        if acct.get("config_dir")
    }


def discover_all_sessions(
    extra: list[dict[str, Any]],
    extra_project_dirs: list[str] | None = None,
    backend: SessionBackend | None = None,
    account_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Discover sessions: managed sessions (via backend) + config entries.

    No automatic process detection — use the + button with project
    suggestions from .claude/projects/ to create sessions instead.
    """
    merged: dict[str, dict[str, Any]] = {}
    # Include managed sessions (created via UI)
    if backend is not None:
        for s in backend.discover_managed_sessions(account_map):
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
_session_compacted: dict[str, bool] = {}  # sid -> whether compacted since last user input


async def _discover_async(
    extra: list[dict[str, Any]],
    extra_project_dirs: list[str] | None = None,
    account_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    global _cached_sessions
    sessions = await asyncio.to_thread(
        discover_all_sessions, extra, extra_project_dirs, _backend, account_map,
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
    account_map = _build_account_map(cfg)

    while True:
        try:
            sessions = await _discover_async(
                cfg.get("extra_sessions", []),
                cfg.get("extra_project_dirs"),
                account_map,
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
            log.warning("Heartbeat timed out, retrying in 2s")
            await asyncio.sleep(2)
            continue  # immediate retry — skip the full interval sleep
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
        log.info("create_session: directory=%r name=%r account=%r",
                 payload.get("directory"), payload.get("session_name"), payload.get("account"))
        # Resolve account name → config_dir
        config_dir = _resolve_account(cfg, payload.get("account", ""))
        result = await asyncio.to_thread(
            _backend.create_session,
            payload["directory"],
            payload.get("session_name", ""),
            config_dir,
        )
        log.info("create_session result: %s", result)
        status = "completed" if result.get("ok") else "failed"
        if result.get("ok"):
            await _unhide_session(cfg, http, result.get("session_id", ""))
        await _report_command(cfg, http, cmd_id, status, json.dumps(result))
    elif cmd_type == "restart_session":
        config_dir = _resolve_account(cfg, payload.get("account", ""))
        result = await asyncio.to_thread(
            _backend.restart_session,
            payload["session_id"],
            payload.get("directory", ""),
            payload.get("session_name", ""),
            config_dir,
        )
        status = "completed" if result.get("ok") else "failed"
        if result.get("ok"):
            await _unhide_session(cfg, http, result.get("session_id", ""))
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


async def _unhide_session(
    cfg: dict[str, Any], http: aiohttp.ClientSession, session_id: str,
) -> None:
    """Clear a server-side soft-delete (hidden flag) for a session.

    Session ids are deterministic (``sha256(node:path)``), so a recreated
    session reuses the id of a previously deleted one. The heartbeat upsert
    skips hidden rows (``ON CONFLICT(id) DO UPDATE ... WHERE hidden = 0``), so a
    session the agent just (re)created would stay invisible forever. Whenever
    the agent explicitly creates a session, un-hide it. A 404 simply means the
    server has never seen this id yet — the next heartbeat will add it.
    """
    if not session_id:
        return
    url = cfg["server_url"].rstrip("/") + f"/api/sessions/{session_id}/unhide"
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    try:
        async with http.post(url, headers=headers) as resp:
            if resp.status not in (200, 404):
                log.warning("Failed to un-hide session %s: %d", session_id, resp.status)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        log.warning("Failed to un-hide session %s", session_id)


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
            async with http.ws_connect(ws_url) as ws:
                log.info("Log WS connected for session %s", session["id"])
                last_size = log_file.stat().st_size
                last_send = time.monotonic()
                while True:
                    await asyncio.sleep(poll_interval)
                    now = time.monotonic()
                    try:
                        current_size = log_file.stat().st_size
                    except OSError:
                        continue
                    if current_size > last_size:
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
                            last_send = now
                    elif now - last_send > 30:
                        await ws.send_json({"type": "ping"})
                        last_send = now
                    else:
                        last_size = current_size
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
                        _session_compacted[sid] = False  # new input → allow re-compact

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
# Idle Session Token Management
# ---------------------------------------------------------------------------

_MAX_ARCHIVES = 50  # keep last N archive files

# Track when sessions were last cleared/compacted to prevent re-triggering loops.
# Maps session_id -> epoch time when the action was performed.
_session_cleared: dict[str, float] = {}


async def idle_manager_loop(cfg: dict[str, Any], http: aiohttp.ClientSession) -> None:
    """Tiered idle management: compact idle sessions, archive+clear long-idle ones."""
    compact_minutes = cfg.get("auto_compact_idle_minutes", 30)
    clear_minutes = cfg.get("auto_clear_idle_minutes", 300)

    if not compact_minutes and not clear_minutes:
        log.info("Idle manager: disabled (both thresholds set to 0)")
        return

    compact_threshold = compact_minutes * 60 if compact_minutes else float("inf")
    clear_threshold = clear_minutes * 60 if clear_minutes else float("inf")
    check_interval = 300  # 5 minutes

    # Wait for agent to settle
    await asyncio.sleep(60)

    log.info("Idle manager: compact=%dmin, clear=%dmin, check every %ds",
             compact_minutes or 0, clear_minutes or 0, check_interval)

    state_dir = Path(cfg.get("_config_dir", Path.home() / ".agenthq"))
    archive_dir = state_dir / "archives"

    while True:
        try:
            if _backend is None:
                await asyncio.sleep(check_interval)
                continue

            idle_info = await asyncio.to_thread(_backend.get_sessions_idle_info)
            if not idle_info:
                await asyncio.sleep(check_interval)
                continue

            now = time.time()

            for sid, idle_sec in idle_info.items():
                info = _backend.sessions.get(sid)
                if not info:
                    continue

                tmux_name = info.get("tmux_name", "")

                # Skip dead panes
                if hasattr(_backend, "_is_pane_dead") and _backend._is_pane_dead(tmux_name):
                    continue

                session_dict = {
                    "id": sid,
                    "project": info.get("project", ""),
                    "path": info.get("path", ""),
                }

                # Tier 2: archive + clear (long idle)
                if idle_sec >= clear_threshold:
                    # Guard: don't re-clear a session that was already cleared
                    # recently.  Only clear again after clear_threshold has
                    # elapsed since the last clear AND tmux reports new idle
                    # time (meaning session_activity actually moved forward
                    # between the last clear and now).
                    last_cleared = _session_cleared.get(sid, 0.0)
                    if last_cleared > 0:
                        time_since_clear = now - last_cleared
                        if time_since_clear < clear_threshold:
                            continue

                    project = info.get("project", "unknown")
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    archive_path = archive_dir / f"{project}_{sid[:8]}_{ts}.txt"

                    archived = await asyncio.to_thread(
                        _backend.archive_session, sid, archive_path,
                    )
                    log.info(
                        "Idle manager: clearing session %s (%s, idle %.0fmin)%s",
                        sid, project, idle_sec / 60,
                        f" — archived to {archive_path}" if archived else "",
                    )

                    await asyncio.to_thread(
                        _backend.send_keys, session_dict, "/clear",
                    )
                    _session_cleared[sid] = now
                    _session_compacted.pop(sid, None)

                    # Prune old archives
                    try:
                        archives = sorted(archive_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
                        for old in archives[:-_MAX_ARCHIVES]:
                            old.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue

                # Tier 1: compact (moderate idle)
                if idle_sec >= compact_threshold:
                    if _session_compacted.get(sid, False):
                        continue
                    project = info.get("project", "unknown")
                    log.info("Idle manager: compacting session %s (%s, idle %.0fmin)",
                             sid, project, idle_sec / 60)
                    await asyncio.to_thread(
                        _backend.send_keys, session_dict, "/compact",
                    )
                    _session_compacted[sid] = True

                # If session is below both thresholds, it became active again
                # — reset the cleared/compacted tracking so future idle
                # periods are handled fresh.
                if idle_sec < compact_threshold:
                    if sid in _session_cleared:
                        log.info("Idle manager: session %s active again, resetting clear guard",
                                 sid)
                        del _session_cleared[sid]
                    _session_compacted.pop(sid, None)

            # Prune stale entries
            active_sids = set(_backend.sessions.keys())
            for sid in list(_session_compacted.keys()):
                if sid not in active_sids:
                    del _session_compacted[sid]
            for sid in list(_session_cleared.keys()):
                if sid not in active_sids:
                    del _session_cleared[sid]

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Idle manager error: %s", exc, exc_info=True)

        await asyncio.sleep(check_interval)


# ---------------------------------------------------------------------------
# Rate-Limit Detection & Account Switching
# ---------------------------------------------------------------------------

# Patterns that indicate Claude Code hit a rate/usage limit.
# Checked against the last ~10 lines of terminal output (short window
# to avoid matching stale messages that have scrolled up).
_RATE_LIMIT_PATTERNS: list[re.Pattern] = [
    re.compile(r"usage\s+limit\s+(reached|exceeded|hit)", re.IGNORECASE),
    # "rate limited" (past tense) or "rate limit" + error verb — avoids
    # matching informational text like "rate limit info" or discussions.
    re.compile(r"rate\s+limited", re.IGNORECASE),
    re.compile(r"rate\s+limit\s+(reached|exceeded|hit|error)", re.IGNORECASE),
    re.compile(r"too\s+many\s+requests", re.IGNORECASE),
    re.compile(r"(error|status|HTTP)\s*429", re.IGNORECASE),
    re.compile(r"429\s+(Too Many|rate)", re.IGNORECASE),
    re.compile(r"capacity\s+(reached|exceeded)", re.IGNORECASE),
    re.compile(r"out\s+of\s+(api\s+)?credits?", re.IGNORECASE),
    re.compile(r"billing.*limit\s+(reached|exceeded|hit)", re.IGNORECASE),
    re.compile(r"exceeded.*quota", re.IGNORECASE),
]

# Track which sessions have already been switched to avoid restart loops.
# Maps session_id → timestamp of last switch.
_rate_limit_switched: dict[str, float] = {}

# Track which accounts are known rate-limited.
# Maps config_dir → timestamp when rate limit was detected.
_account_rate_limited: dict[str, float] = {}

# Cooldown: don't re-switch a session within this many seconds.
_RATE_LIMIT_SWITCH_COOLDOWN = 600  # 10 minutes

# How long to consider an account rate-limited before retrying it.
_ACCOUNT_RATE_LIMIT_TTL = 1800  # 30 minutes

# How many terminal lines to scan for rate-limit text.
# Kept small to avoid matching stale messages in scrollback.
_RATE_LIMIT_SCAN_LINES = 10

# How often to check if sessions can return to the default account.
_RETURN_TO_DEFAULT_INTERVAL = 1800  # 30 minutes

# Patterns that indicate Claude Code is stuck at an OAuth login prompt.
_OAUTH_PROMPT_PATTERNS: list[re.Pattern] = [
    re.compile(r"Paste code here if prompted", re.IGNORECASE),
    re.compile(r"Use the url below to sign in", re.IGNORECASE),
    re.compile(r"oauth/authorize\?", re.IGNORECASE),
]


def _detect_rate_limit(text: str) -> str | None:
    """Return the matching pattern string if rate-limit text is found, else None."""
    for pat in _RATE_LIMIT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _detect_oauth_prompt(text: str) -> str | None:
    """Return the matching pattern string if an OAuth login prompt is found."""
    for pat in _OAUTH_PROMPT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def _get_fallback_account(
    cfg: dict[str, Any], current_config_dir: str,
) -> tuple[str, str]:
    """Return (account_name, config_dir) for the next account to try.

    Skips the current account AND any account that is still within its
    rate-limit TTL.  Returns ("", "") if no viable account is available.
    """
    accounts = cfg.get("accounts", {})
    if len(accounts) < 2:
        return ("", "")
    now = time.time()
    for name, acct in accounts.items():
        acct_dir = acct.get("config_dir", "")
        if not acct_dir or acct_dir == current_config_dir:
            continue
        # Skip accounts that are known to be rate-limited
        limited_at = _account_rate_limited.get(acct_dir, 0.0)
        if now - limited_at < _ACCOUNT_RATE_LIMIT_TTL:
            continue
        return (name, acct_dir)
    return ("", "")


def _resolve_default_config_dir(cfg: dict[str, Any]) -> str:
    """Return config_dir for the default_account, or "" if not configured."""
    default_name = cfg.get("default_account", "")
    if not default_name:
        return ""
    accounts = cfg.get("accounts", {})
    acct = accounts.get(default_name, {})
    return acct.get("config_dir", "")


async def rate_limit_watcher_loop(
    cfg: dict[str, Any], http: aiohttp.ClientSession,
) -> None:
    """Periodically check sessions for rate-limit messages and switch accounts.

    Also returns sessions to the default account when rate limits clear.
    """
    if not cfg.get("rate_limit_watcher_enabled", False):
        log.info("Rate-limit watcher disabled by config (rate_limit_watcher_enabled=false)")
        return
    check_interval = 30  # seconds
    last_return_check = 0.0  # timestamp of last "return to default" sweep

    # Wait for backend to be ready
    while _backend is None:
        await asyncio.sleep(check_interval)

    log.info("Rate-limit watcher started (interval=%ds, cooldown=%ds, scan_lines=%d)",
             check_interval, _RATE_LIMIT_SWITCH_COOLDOWN, _RATE_LIMIT_SCAN_LINES)

    while True:
        try:
            now = time.time()

            # --- Return sessions to default account when rate limit clears ---
            default_dir = _resolve_default_config_dir(cfg)
            if (default_dir
                    and now - last_return_check >= _RETURN_TO_DEFAULT_INTERVAL):
                last_return_check = now
                # Only return if default account is no longer rate-limited
                default_limited_at = _account_rate_limited.get(default_dir, 0.0)
                if now - default_limited_at >= _ACCOUNT_RATE_LIMIT_TTL:
                    for sid, info in list(_backend.sessions.items()):
                        current_dir = info.get("config_dir", "")
                        if current_dir == default_dir:
                            continue  # already on default
                        if not current_dir:
                            continue
                        if info.get("no_auto_switch"):
                            continue
                        tmux_name = info.get("tmux_name", "")
                        if not _backend._tmux_alive(tmux_name):
                            continue
                        # Don't return if recently switched (session might be
                        # actively using the fallback account)
                        last_switch = _rate_limit_switched.get(sid, 0.0)
                        if now - last_switch < _RETURN_TO_DEFAULT_INTERVAL:
                            continue
                        project = info.get("project", "?")
                        default_name = cfg.get("default_account", "?")
                        log.info(
                            "Returning session %s (%s) to default account '%s'",
                            sid, project, default_name,
                        )
                        result = await asyncio.to_thread(
                            _backend.restart_session, sid, "", "", default_dir,
                        )
                        if result.get("ok"):
                            log.info("Returned session %s (%s) to '%s'",
                                     sid, project, default_name)
                        else:
                            log.warning("Failed to return session %s to default: %s",
                                        sid, result.get("error"))

            for sid, info in list(_backend.sessions.items()):
                tmux_name = info.get("tmux_name", "")
                if not _backend._tmux_alive(tmux_name):
                    continue

                # Skip sessions that opted out of auto-switching
                if info.get("no_auto_switch"):
                    continue

                # Skip if recently switched
                last_switch = _rate_limit_switched.get(sid, 0.0)
                if now - last_switch < _RATE_LIMIT_SWITCH_COOLDOWN:
                    continue

                # Capture only the last few lines to avoid stale matches
                text = await asyncio.to_thread(
                    _backend.capture_last_lines, sid, _RATE_LIMIT_SCAN_LINES,
                )
                if not text:
                    continue

                current_dir = info.get("config_dir", "")
                project = info.get("project", "?")

                # --- OAuth prompt detection (use wider scan for login screens) ---
                oauth_text = await asyncio.to_thread(
                    _backend.capture_last_lines, sid, 30,
                )
                oauth_match = _detect_oauth_prompt(oauth_text) if oauth_text else None
                if oauth_match:
                    log.warning(
                        "OAuth prompt detected in session %s (%s) — account '%s' needs re-auth",
                        sid, project, current_dir,
                    )
                    # Mark this account as unusable so we don't switch back to it
                    _account_rate_limited[current_dir] = now
                    fallback_name, fallback_dir = _get_fallback_account(cfg, current_dir)
                    if fallback_dir:
                        log.info(
                            "Switching session %s (%s) away from OAuth-stuck account to '%s'",
                            sid, project, fallback_name,
                        )
                        result = await asyncio.to_thread(
                            _backend.restart_session, sid, "", "", fallback_dir,
                        )
                        if result.get("ok"):
                            log.info("Switched session %s (%s) to account '%s'",
                                     sid, project, fallback_name)
                        else:
                            log.warning("Failed to switch session %s (%s) to '%s': %s",
                                        sid, project, fallback_name, result.get("error"))
                    else:
                        log.warning(
                            "OAuth-stuck session %s (%s) — no viable fallback account",
                            sid, project,
                        )
                    _rate_limit_switched[sid] = now
                    continue

                # --- Rate-limit detection ---
                match = _detect_rate_limit(text)
                if not match:
                    continue

                # Mark the current account as rate-limited
                _account_rate_limited[current_dir] = now

                fallback_name, fallback_dir = _get_fallback_account(cfg, current_dir)
                if not fallback_dir:
                    log.warning(
                        "Rate limit detected in session %s (%s) [%s] but no viable fallback "
                        "(all accounts rate-limited)",
                        sid, project, match,
                    )
                    _rate_limit_switched[sid] = now
                    continue

                log.info(
                    "Rate limit detected in session %s (%s) [%s] — switching to account '%s'",
                    sid, project, match, fallback_name,
                )

                # Restart the session with the fallback account
                result = await asyncio.to_thread(
                    _backend.restart_session, sid, "", "", fallback_dir,
                )
                if result.get("ok"):
                    log.info(
                        "Switched session %s (%s) to account '%s'",
                        sid, project, fallback_name,
                    )
                else:
                    log.warning(
                        "Failed to switch session %s (%s) to '%s': %s",
                        sid, project, fallback_name, result.get("error"),
                    )
                _rate_limit_switched[sid] = now

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Rate-limit watcher error: %s", exc, exc_info=True)

        await asyncio.sleep(check_interval)


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
# Usage reporter — collects local JSONL token data and sends to server
# ---------------------------------------------------------------------------

_USAGE_REPORT_INTERVAL = 60  # seconds
_USAGE_HOURS_BACK = 192      # look back 8 days to match Claude Code session window

# Pricing per million tokens (must stay in sync with server/routers/usage.py)
_PRICING = {
    "opus": {"input": 15.0, "output": 75.0, "cache_creation": 18.75, "cache_read": 1.5},
    "sonnet": {"input": 3.0, "output": 15.0, "cache_creation": 3.75, "cache_read": 0.3},
    "haiku": {"input": 0.25, "output": 1.25, "cache_creation": 0.3, "cache_read": 0.03},
}


def _extract_usage_tokens(data: dict) -> dict | None:
    """Extract token counts from a JSONL entry."""
    sources: list[dict] = []
    msg = data.get("message", {})
    if isinstance(msg, dict) and "usage" in msg:
        sources.append(msg["usage"])
    if "usage" in data:
        sources.append(data["usage"])
    sources.append(data)
    for src in sources:
        if not isinstance(src, dict):
            continue
        input_t = src.get("input_tokens") or src.get("inputTokens") or 0
        output_t = src.get("output_tokens") or src.get("outputTokens") or 0
        if input_t > 0 or output_t > 0:
            return {
                "input_tokens": int(input_t),
                "output_tokens": int(output_t),
                "cache_creation_tokens": int(
                    src.get("cache_creation_input_tokens")
                    or src.get("cacheCreationInputTokens") or 0),
                "cache_read_tokens": int(
                    src.get("cache_read_input_tokens")
                    or src.get("cacheReadInputTokens") or 0),
            }
    return None


def _extract_usage_model(data: dict) -> str:
    msg = data.get("message", {})
    for c in [msg.get("model") if isinstance(msg, dict) else None, data.get("model")]:
        if c and isinstance(c, str):
            return c
    return "unknown"


def _calc_cost(model: str, tokens: dict) -> float:
    ml = model.lower()
    if "opus" in ml:
        r = _PRICING["opus"]
    elif "haiku" in ml:
        r = _PRICING["haiku"]
    else:
        r = _PRICING["sonnet"]
    return (
        tokens["input_tokens"] / 1e6 * r["input"]
        + tokens["output_tokens"] / 1e6 * r["output"]
        + tokens["cache_creation_tokens"] / 1e6 * r["cache_creation"]
        + tokens["cache_read_tokens"] / 1e6 * r["cache_read"]
    )


def _parse_ts(data: dict):
    """Parse timestamp from JSONL entry → datetime or None."""
    from datetime import datetime as _dt, timezone as _tz
    ts_raw = data.get("timestamp")
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        return _dt.fromtimestamp(ts_raw, tz=_tz.utc)
    if isinstance(ts_raw, str):
        try:
            d = _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_tz.utc)
            return d
        except ValueError:
            return None
    return None


def _collect_usage_hourly(cfg_dirs: list[str] | None = None) -> list[dict]:
    """Scan JSONL files and return hourly-aggregated usage rows.

    Returns list of dicts with keys:
      hour, model, input_tokens, output_tokens, cache_creation_tokens,
      cache_read_tokens, cost_usd, message_count
    """
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from collections import defaultdict as _dd

    cutoff = _dt.now(_tz.utc) - timedelta(hours=_USAGE_HOURS_BACK)
    buckets: dict[tuple[str, str], dict] = _dd(lambda: {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_tokens": 0, "cache_read_tokens": 0,
        "cost_usd": 0.0, "message_count": 0,
    })

    for projects_dir in _claude_projects_dirs(cfg_dirs):
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        ts = _parse_ts(data)
                        if ts is None or ts < cutoff:
                            continue
                        tokens = _extract_usage_tokens(data)
                        if tokens is None:
                            continue
                        model = _extract_usage_model(data)
                        cost = data.get("costUSD") or data.get("cost_usd") or _calc_cost(model, tokens)
                        hour_key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
                        key = (hour_key, model)
                        b = buckets[key]
                        b["input_tokens"] += tokens["input_tokens"]
                        b["output_tokens"] += tokens["output_tokens"]
                        b["cache_creation_tokens"] += tokens["cache_creation_tokens"]
                        b["cache_read_tokens"] += tokens["cache_read_tokens"]
                        b["cost_usd"] += float(cost)
                        b["message_count"] += 1
            except Exception:
                continue

    return [
        {
            "hour": hour, "model": model,
            "input_tokens": b["input_tokens"],
            "output_tokens": b["output_tokens"],
            "cache_creation_tokens": b["cache_creation_tokens"],
            "cache_read_tokens": b["cache_read_tokens"],
            "cost_usd": round(b["cost_usd"], 6),
            "message_count": b["message_count"],
        }
        for (hour, model), b in sorted(buckets.items())
    ]


async def usage_reporter_loop(cfg: dict[str, Any], http: aiohttp.ClientSession) -> None:
    """Periodically collect local JSONL usage data and POST to the server."""
    url = cfg["server_url"].rstrip("/") + "/api/usage/report"
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    interval = cfg.get("usage_report_interval", _USAGE_REPORT_INTERVAL)
    machine = platform.node()

    while True:
        try:
            rows = await asyncio.to_thread(
                _collect_usage_hourly, cfg.get("extra_project_dirs"),
            )
            if rows:
                payload = {"machine": machine, "rows": rows}
                async with http.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        log.info("Usage report sent (%d hourly rows)", len(rows))
                    else:
                        body = await resp.text()
                        log.warning("Usage report %d: %s", resp.status, body[:200])
            else:
                log.debug("No usage data to report")
        except asyncio.TimeoutError:
            log.debug("Usage report timed out")
        except aiohttp.ClientError as exc:
            log.debug("Usage report failed: %s", exc)
        except Exception as exc:
            log.error("Usage report error: %s", exc, exc_info=True)
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

    # Backfill config_dir for sessions created before account pool feature
    default_config_dir = _resolve_account(cfg, cfg.get("default_account", ""))
    if default_config_dir and hasattr(_backend, "backfill_config_dir"):
        _backend.backfill_config_dir(default_config_dir)

    # Auto-create default sessions if configured. Track the ids we create so we
    # can un-hide them once the HTTP session is up (a previously deleted default
    # session reuses the same deterministic id and would otherwise stay hidden).
    created_default_sids: list[str] = []
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
                if result.get("session_id"):
                    created_default_sids.append(result["session_id"])
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
        # Un-hide any default sessions we just (re)created so a prior soft-delete
        # doesn't keep them invisible.
        for sid in created_default_sids:
            await _unhide_session(cfg, http, sid)
        await asyncio.gather(
            resilient("heartbeat", heartbeat_loop, cfg, http),
            resilient("session_manager", session_manager, cfg, http),
            resilient("sync", sync_loop, cfg, http),
            resilient("git_sync", git_sync_loop, cfg, http),
            resilient("idle_manager", idle_manager_loop, cfg, http),
            resilient("usage_reporter", usage_reporter_loop, cfg, http),
            resilient("rate_limit_watcher", rate_limit_watcher_loop, cfg, http),
        )
