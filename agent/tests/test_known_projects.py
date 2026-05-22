"""Tests for project-suggestion listing.

Regression: after a directory reorg, `.claude/projects/` still holds entries
encoding the old paths (e.g. `/data2/...`). `list_known_projects` decoded those
to best-effort paths and suggested them even though the directory no longer
exists, so the dashboard's "+" offered dead paths that fail with "Directory
not found". Suggestions must skip paths that don't exist on disk.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from agenthq_agent import core


def test_list_known_projects_skips_missing_paths(monkeypatch):
    base = tempfile.mkdtemp()  # e.g. /tmp/tmpXXXXXX  (no dashes)
    try:
        real_proj = os.path.join(base, "realproj")
        os.mkdir(real_proj)

        projects_dir = os.path.join(base, "projects")
        os.mkdir(projects_dir)
        # Entry whose decoded path exists ("/a/b/realproj" -> "-a-b-realproj")
        enc_exists = "-" + real_proj.strip("/").replace("/", "-")
        os.mkdir(os.path.join(projects_dir, enc_exists))
        # Entry encoding a path that no longer exists (stale, like /data2/...)
        os.mkdir(os.path.join(projects_dir, "-nonexistent-aqtest-GoneProject"))

        from pathlib import Path
        monkeypatch.setattr(
            core, "_claude_projects_dirs",
            lambda cfg_dirs=None: [Path(projects_dir)],
        )

        result = core.list_known_projects()
        paths = [p["path"] for p in result]

        assert real_proj in paths, f"existing project should be listed: {paths}"
        assert all("GoneProject" not in p for p in paths), \
            f"stale/non-existent path must be filtered: {paths}"
        assert not any(p.startswith("/nonexistent") for p in paths)
    finally:
        shutil.rmtree(base, ignore_errors=True)
