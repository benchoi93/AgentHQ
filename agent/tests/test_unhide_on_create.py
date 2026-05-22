"""Tests for un-hiding sessions on (re)creation.

Regression: session ids are deterministic (`sha256(node:path)`), so a session
that was soft-deleted (hidden=1) on the server keeps the same id when the agent
recreates it. The heartbeat upsert refuses to touch hidden rows
(`... ON CONFLICT(id) DO UPDATE ... WHERE hidden = 0`), so a recreated session
stays invisible forever. The agent must explicitly un-hide a session it just
created.
"""
from __future__ import annotations

import asyncio

from agenthq_agent import core


class _FakeResp:
    def __init__(self, status: int = 200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return {}


class _FakeHTTP:
    """Records calls; mimics aiohttp.ClientSession.post() async-context usage."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResp(self.status)


CFG = {
    "server_url": "http://srv:30001/",
    "token": "secret-token",
}


def test_unhide_session_posts_to_endpoint():
    http = _FakeHTTP()
    asyncio.run(core._unhide_session(CFG, http, "abc123def456"))

    assert len(http.calls) == 1
    url, kwargs = http.calls[0]
    assert url == "http://srv:30001/api/sessions/abc123def456/unhide"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"


def test_unhide_session_swallows_errors():
    # A non-200 (e.g. session never existed) must not raise.
    http = _FakeHTTP(status=404)
    asyncio.run(core._unhide_session(CFG, http, "missing"))
    assert len(http.calls) == 1


def test_create_session_command_triggers_unhide(monkeypatch):
    """A successful create_session command must un-hide the new session id."""
    class _FakeBackend:
        def create_session(self, directory, name="", config_dir=""):
            return {"ok": True, "session_id": "newsid000001"}

    monkeypatch.setattr(core, "_backend", _FakeBackend())

    reported: list[tuple] = []

    async def _fake_report(cfg, http, cmd_id, status, result):
        reported.append((cmd_id, status))

    monkeypatch.setattr(core, "_report_command", _fake_report)

    http = _FakeHTTP()
    cmd = {
        "id": 7,
        "type": "create_session",
        "payload": {"directory": "/home/u/proj", "session_name": "proj"},
    }
    asyncio.run(core._handle_command(CFG, http, cmd))

    unhide_urls = [u for (u, _) in http.calls if u.endswith("/unhide")]
    assert unhide_urls == ["http://srv:30001/api/sessions/newsid000001/unhide"]
    assert reported and reported[0][1] == "completed"


def test_failed_create_does_not_unhide(monkeypatch):
    class _FakeBackend:
        def create_session(self, directory, name="", config_dir=""):
            return {"ok": False, "error": "tmux not found"}

    monkeypatch.setattr(core, "_backend", _FakeBackend())

    async def _fake_report(cfg, http, cmd_id, status, result):
        pass

    monkeypatch.setattr(core, "_report_command", _fake_report)

    http = _FakeHTTP()
    cmd = {
        "id": 8,
        "type": "create_session",
        "payload": {"directory": "/home/u/proj", "session_name": "proj"},
    }
    asyncio.run(core._handle_command(CFG, http, cmd))

    assert [u for (u, _) in http.calls if u.endswith("/unhide")] == []
