"""Tests for the ROADMAP B5 skip-on-unchanged-HEAD optimisation.

``collect_git_stats`` re-walks ``git log`` + recomputes the cochange /
file_stats / complexity tables. On a warm ``roam index`` run that's
1-10s of wasted work when HEAD hasn't moved. The optimisation reads the
last manifest's ``git_head`` column, compares it against live
``git rev-parse HEAD``, and short-circuits when they match.

Covers:
- ``_head_unchanged_since_last_run`` returns False on the first run
  (empty manifest table -> full pass must run).
- The helper returns True when the recorded HEAD matches live HEAD.
- The helper returns False when commits land between runs.
- ``collect_git_stats`` end-to-end: a second invocation against the same
  HEAD does NOT call ``parse_git_log`` (the heavyweight pass).
- ``collect_git_stats`` end-to-end: a commit between invocations forces
  a fresh ``parse_git_log`` call.
- A non-git directory is still handled (regression: skip-check must not
  short-circuit the existing non-git early-exit).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init

from roam.db.connection import ensure_schema
from roam.index import git_stats
from roam.index.git_stats import _head_unchanged_since_last_run, collect_git_stats
from roam.index.manifest import collect_manifest, write_manifest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """Brand-new SQLite connection with the full roam schema applied."""
    db_path = tmp_path / "git_stats_skip_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def git_project(tmp_path):
    """A small git-tracked project with one commit on HEAD."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n")
    git_init(proj)
    return proj


def _record_manifest(conn: sqlite3.Connection, project_root: Path) -> dict:
    """Build + persist a manifest pointing at the current HEAD."""
    manifest = collect_manifest(project_root, conn=conn, profile="all")
    write_manifest(conn, manifest)
    conn.commit()
    return manifest


# ---------------------------------------------------------------------------
# Unit: _head_unchanged_since_last_run
# ---------------------------------------------------------------------------


class TestHeadUnchangedHelper:
    def test_first_run_returns_false(self, tmp_path, git_project):
        """No manifest row yet -> can't skip; must run the full pass."""
        conn = _fresh_db(tmp_path)
        try:
            assert _head_unchanged_since_last_run(conn, git_project) is False
        finally:
            conn.close()

    def test_recorded_head_matches_live_head(self, tmp_path, git_project):
        """Manifest's ``git_head`` matches live HEAD -> skip."""
        conn = _fresh_db(tmp_path)
        try:
            manifest = _record_manifest(conn, git_project)
            assert manifest.get("git_head"), (
                "Test setup broken: manifest didn't capture a HEAD sha for the git-tracked project."
            )
            assert _head_unchanged_since_last_run(conn, git_project) is True
        finally:
            conn.close()

    def test_new_commit_invalidates_skip(self, tmp_path, git_project):
        """A commit between manifest + check forces a re-run."""
        conn = _fresh_db(tmp_path)
        try:
            _record_manifest(conn, git_project)
            # Land a new commit -> live HEAD moves; recorded HEAD stale.
            (git_project / "app.py").write_text("def hello():\n    return 'world!'\n")
            git_commit(git_project, msg="update hello")
            assert _head_unchanged_since_last_run(conn, git_project) is False
        finally:
            conn.close()

    def test_non_git_dir_returns_false(self, tmp_path):
        """No git -> live HEAD unresolvable -> can't claim "unchanged"."""
        empty = tmp_path / "empty"
        empty.mkdir()
        conn = _fresh_db(tmp_path)
        try:
            # No manifest row yet either; helper should bail at the
            # latest_manifest() check before it ever shells out.
            assert _head_unchanged_since_last_run(conn, empty) is False
        finally:
            conn.close()

    def test_recorded_head_without_live_head_returns_false(self, tmp_path):
        """Manifest carries a sha but the project isn't a git repo now
        (e.g. ``.git`` was deleted) -> live HEAD lookup fails -> re-run."""
        empty = tmp_path / "empty"
        empty.mkdir()
        conn = _fresh_db(tmp_path)
        try:
            # Hand-write a manifest row with a sha but no real repo on disk.
            write_manifest(
                conn,
                {
                    "indexed_at": int(time.time()),
                    "roam_version": "test",
                    "schema_version": 1,
                    "parser_versions": {},
                    "grammar_versions": None,
                    "config_hash": "abc",
                    "git_head": "deadbeef" * 5,
                    "git_dirty_hash": None,
                    "enabled_extras": [],
                    "index_profile": "all",
                    "notes": None,
                },
            )
            conn.commit()
            assert _head_unchanged_since_last_run(conn, empty) is False
        finally:
            conn.close()

    def test_manifest_sqlite_failure_returns_false(self, tmp_path, git_project, monkeypatch):
        """Expected manifest read failures disable the skip optimization."""
        conn = _fresh_db(tmp_path)

        def _boom(_conn):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr("roam.index.manifest.latest_manifest", _boom)
        try:
            assert _head_unchanged_since_last_run(conn, git_project) is False
        finally:
            conn.close()

    def test_manifest_unexpected_failure_propagates(self, tmp_path, git_project, monkeypatch):
        """Programmer errors in manifest handling must not be swallowed."""
        conn = _fresh_db(tmp_path)

        def _boom(_conn):
            raise RuntimeError("manifest invariant violated")

        monkeypatch.setattr("roam.index.manifest.latest_manifest", _boom)
        try:
            with pytest.raises(RuntimeError, match="manifest invariant violated"):
                _head_unchanged_since_last_run(conn, git_project)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# End-to-end: collect_git_stats skips the heavy pass
# ---------------------------------------------------------------------------


class TestCollectGitStatsSkip:
    def test_first_run_executes_full_pass(self, tmp_path, git_project):
        """No manifest yet -> ``parse_git_log`` must be invoked."""
        conn = _fresh_db(tmp_path)
        try:
            with mock.patch.object(git_stats, "parse_git_log", wraps=git_stats.parse_git_log) as spy:
                collect_git_stats(conn, git_project)
                assert spy.call_count == 1, f"First run should call parse_git_log once; got {spy.call_count}"
        finally:
            conn.close()

    def test_warm_run_skips_when_head_unchanged(self, tmp_path, git_project):
        """Manifest written for current HEAD -> second call short-circuits
        before parse_git_log is invoked. This is the B5 win."""
        conn = _fresh_db(tmp_path)
        try:
            _record_manifest(conn, git_project)
            with mock.patch.object(git_stats, "parse_git_log") as spy:
                collect_git_stats(conn, git_project)
                assert spy.call_count == 0, (
                    "B5 regression: collect_git_stats called parse_git_log even though manifest HEAD matched live HEAD."
                )
        finally:
            conn.close()

    def test_new_commit_re_runs_full_pass(self, tmp_path, git_project):
        """Commit between calls -> skip-check fails -> full pass re-runs."""
        conn = _fresh_db(tmp_path)
        try:
            _record_manifest(conn, git_project)
            # Land a new commit so live HEAD diverges from recorded HEAD.
            (git_project / "app.py").write_text("def hello():\n    return 'world!'\n")
            git_commit(git_project, msg="update hello")
            with mock.patch.object(git_stats, "parse_git_log", wraps=git_stats.parse_git_log) as spy:
                collect_git_stats(conn, git_project)
                assert spy.call_count == 1, (
                    f"After a new commit, parse_git_log should be invoked again; got {spy.call_count} calls."
                )
        finally:
            conn.close()

    def test_non_git_dir_short_circuits_before_skip_check(self, tmp_path):
        """Non-git path returns immediately at the ``_is_git_repo`` gate;
        parse_git_log must not be reached. Guards against the skip-check
        accidentally swallowing the non-git early-exit."""
        empty = tmp_path / "empty"
        empty.mkdir()
        conn = _fresh_db(tmp_path)
        try:
            with mock.patch.object(git_stats, "parse_git_log") as spy:
                collect_git_stats(conn, empty)
                assert spy.call_count == 0
        finally:
            conn.close()

    def test_skip_emits_log_signal(self, tmp_path, git_project, caplog):
        """Users wondering why a warm index is faster should see a log
        line. The message must mention "HEAD unchanged" so it's grep-able."""
        import logging

        conn = _fresh_db(tmp_path)
        try:
            _record_manifest(conn, git_project)
            with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
                collect_git_stats(conn, git_project)
            messages = [r.message for r in caplog.records]
            assert any("HEAD unchanged" in m for m in messages), (
                f"Expected an INFO log message naming 'HEAD unchanged'; got: {messages!r}"
            )
        finally:
            conn.close()
