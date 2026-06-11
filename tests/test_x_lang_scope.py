"""Tests for ``roam x-lang --scope`` and the ``consider_scope`` envelope.

Closes the Rank-19 finding: x-lang used to bail out silently on large
graphs.  The wired-up CLI now exposes ``--scope <prefix>`` and emits a
``state: "consider_scope"`` envelope when no scope is given on a graph
above the bridge-file threshold.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import git_init, index_in_process


def _seed_many_bridge_files(db_path: Path, n_py: int = 1200) -> None:
    """Inject many synthetic .py file rows.

    The protobuf bridge counts every ``.py`` (and a handful of other
    target extensions) as a "target file" — so once the project has a
    single ``.proto`` plus N ``.py`` rows the bridge-file count crosses
    the 1000-file threshold without us having to actually index 1000
    real Python files.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # Use the ``_pb2.py`` suffix so the ProtobufBridge's ``detect()``
        # recognises them as generated stubs and counts them as bridge
        # target files.
        rows = [(900000 + i, f"src/auto/mod_{i:05d}_pb2.py") for i in range(n_py)]
        conn.executemany(
            "INSERT OR IGNORE INTO files (id, path) VALUES (?, ?)",
            rows,
        )
        # Also drop in a vendor/ sibling tree so we can test that --scope
        # genuinely narrows the candidate set.
        vendor_rows = [(950000 + i, f"vendor/lib_{i:04d}.py") for i in range(100)]
        conn.executemany(
            "INSERT OR IGNORE INTO files (id, path) VALUES (?, ?)",
            vendor_rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def huge_proto_project(tmp_path, monkeypatch):
    """Tiny indexed proto project with synthetic .py rows to exceed threshold."""
    proj = tmp_path / "xlang_huge"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    proto_dir = proj / "proto"
    proto_dir.mkdir()
    (proto_dir / "user.proto").write_text('syntax = "proto3";\npackage user;\nmessage U {\n  string id = 1;\n}\n')
    src = proj / "src"
    src.mkdir()
    (src / "user_pb2.py").write_text("# generated\nclass U:\n    id: str = ''\n")
    (src / "app.py").write_text("from src.user_pb2 import U\n\ndef use(u):\n    return u.id\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    db_path = proj / ".roam" / "index.db"
    _seed_many_bridge_files(db_path, n_py=1200)
    return proj


class _SubprocessResult:
    """Duck-typed stand-in for click.testing.Result (exit_code + output)."""

    def __init__(self, proc):
        self.exit_code = proc.returncode
        self.output = proc.stdout


def _invoke_xlang(args, cwd, json_mode=True):
    """Invoke x-lang in a SUBPROCESS, not via CliRunner.

    This file's huge-graph test flaked under xdist when an unidentified
    earlier test on the same worker left in-process state that made every
    bridge invisible ("no cross-language bridges detected" on a project
    with 1200 seeded stub rows; passes solo and in every targeted batch).
    A fresh interpreter is hermetic by construction — module registries,
    caches, and monkeypatch leftovers cannot reach it. Costs ~1s per call.
    """
    import subprocess
    import sys

    argv = [sys.executable, "-m", "roam"]
    if json_mode:
        argv.append("--json")
    argv += ["x-lang", *[str(a) for a in args]]
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=180)
    return _SubprocessResult(proc)


class TestXLangScope:
    """``--scope`` and ``consider_scope`` envelope behaviour."""

    def test_x_lang_recommends_scope_on_huge_graph(self, huge_proto_project):
        """Default invocation on a huge graph must NOT bail silently — it
        should emit a ``state: "consider_scope"`` envelope with the
        recommended scope name."""
        result = _invoke_xlang([], huge_proto_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        assert data["command"] == "x-lang"
        summary = data["summary"]
        assert summary.get("state") == "consider_scope"
        assert summary.get("partial_success") is True
        # Verdict must be self-contained and name a follow-up command.
        assert "roam x-lang --scope" in summary["verdict"]
        # Recommended scope must be a real prefix.
        assert isinstance(summary.get("recommended_scope"), str)
        assert summary["recommended_scope"]
        assert summary.get("bridge_files", 0) > 1000

    def test_x_lang_with_scope_runs(self, huge_proto_project):
        """When ``--scope src/`` is supplied the command runs to completion
        — no ``consider_scope`` state, exit zero, envelope is complete."""
        result = _invoke_xlang(["--scope", "src/"], huge_proto_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        assert data["command"] == "x-lang"
        summary = data["summary"]
        # When the scope is narrow enough, we expect the analysis to run
        # — i.e. NOT a consider_scope envelope.
        assert summary.get("state") != "consider_scope"
        assert "bridges" in summary
        assert "links" in summary

    def test_x_lang_scope_filters_files(self, huge_proto_project):
        """``--scope vendor/`` excludes the proto bridge entirely; an empty
        or no-bridges envelope is fine, but no crash + state must be
        explicit."""
        result = _invoke_xlang(["--scope", "vendor/"], huge_proto_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        # vendor/ has only .py files, no .proto — bridge should not activate.
        summary = data["summary"]
        assert summary["bridges"] == 0
