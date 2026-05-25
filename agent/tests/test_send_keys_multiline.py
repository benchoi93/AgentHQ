"""Regression: every relayed message must SUBMIT, not get stuck in the input.

Bug: TmuxBackend.send_keys typed text and Enter as one `send-keys content
Enter` burst. Claude Code's TUI auto-detects a fast multi-char burst as a
*paste*, buffers it, and absorbs the trailing Enter — so the text gets stuck
in the input box and is never submitted. Observed with long single-line
messages (~77 chars) AND multi-line / coalesced Telegram messages; only very
short bursts happened to submit (staying under the paste-detection threshold).

Fix: always deliver via tmux buffer + bracketed paste (`paste-buffer -p`)
followed by a SEPARATE Enter (after a short render delay), regardless of length
or newlines. The standalone Enter is never absorbed because the paste is
already closed.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agenthq_agent.backends import tmux as tmux_mod
from agenthq_agent.backends.tmux import TmuxBackend


def _make_backend(monkeypatch):
    backend = TmuxBackend(Path("/tmp"))
    monkeypatch.setattr(backend, "ensure_pane", lambda session: "fakepane")
    calls: list[list[str]] = []

    def _fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tmux_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(tmux_mod.time, "sleep", lambda *_: None)
    return backend, calls


def _assert_submitted_via_paste(calls, pane: str = "fakepane") -> None:
    flat = [" ".join(c) for c in calls]
    # Content is delivered as a bracketed paste ...
    assert any("paste-buffer" in c for c in flat), flat
    # ... and the FINAL action is a standalone Enter (not bundled with text).
    assert calls[-1] == ["tmux", "send-keys", "-t", pane, "Enter"], calls
    # The buggy naive burst (content + Enter in one send-keys) is never used.
    assert not any(
        c[:4] == ["tmux", "send-keys", "-t", pane] and len(c) > 5
        for c in calls
    ), calls


@pytest.mark.parametrize(
    "content",
    [
        "go",                                                   # very short single line
        "test 1\ntest 1\ntest 1",                               # short multi-line (coalesced)
        "testing sending through telegram with moderately long message",  # long single line
        "x" * 600,                                              # very long
        "line one\nline two",                                   # single message, one newline
    ],
)
def test_all_messages_submit_via_bracketed_paste(monkeypatch, content):
    backend, calls = _make_backend(monkeypatch)
    result = backend.send_keys({"id": "s"}, content)
    assert result == "[sent to tmux:fakepane]"
    _assert_submitted_via_paste(calls)


def test_no_pane_returns_none(monkeypatch):
    backend, calls = _make_backend(monkeypatch)
    monkeypatch.setattr(backend, "ensure_pane", lambda session: None)
    assert backend.send_keys({"id": "s"}, "hello") is None
    assert calls == []
