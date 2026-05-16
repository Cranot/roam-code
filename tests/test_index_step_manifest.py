"""Tests for the W82 / ROADMAP A8 per-sub-step manifest column.

Covers:
- Schema migration creates the ``index_manifest.steps_status`` column.
- Round-trip: a manifest written with ``steps_status`` reads back with the
  decoded dict intact.
- End-to-end: a clean ``roam index`` run records its sub-steps as
  ``ok`` (or ``skipped:*``), never as ``failed:*``.
- Simulated failure: when a sub-step raises, ``_record_step`` captures
  ``failed:<ExceptionClass>`` with the message excerpt.
- Doctor: the new ``Index step manifest`` advisory fires on a manifest
  row that carries a failed step and names the failing step.

The doctor check is wired as advisory (NOT blocking) so a failed
optional sub-step never escalates ``roam doctor`` to exit code 2 —
the cmd's existing advisory split is preserved.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process

from roam.db.connection import USER_VERSION, ensure_schema
from roam.index.manifest import (
    collect_manifest,
    latest_manifest,
    record_indexer_run,
    write_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """Brand-new SQLite connection with the full roam schema applied."""
    db_path = tmp_path / "step_manifest_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def step_project(tmp_path):
    """A small git-tracked project we can index end-to-end."""
    proj = tmp_path / "step_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n")
    (proj / "util.py").write_text("def add(a, b):\n    return a + b\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_steps_status_column_exists(tmp_path):
    """Fresh DB carries the W82 ``steps_status`` column on ``index_manifest``."""
    conn = _fresh_db(tmp_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(index_manifest)")}
        assert "steps_status" in cols, f"index_manifest missing steps_status column. Got: {sorted(cols)}"
        # USER_VERSION is mirrored into PRAGMA — A8 piggybacks on the
        # discipline test elsewhere, but we also pin it here.
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == int(USER_VERSION)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_write_with_steps_status_round_trips(tmp_path):
    """A manifest written with steps_status reads back as a dict."""
    conn = _fresh_db(tmp_path)
    try:
        synthetic_steps = {
            "clustering": {"status": "ok", "duration_ms": 1234.5},
            "taint_analysis": {
                "status": "failed:MemoryError",
                "error_excerpt": "out of memory",
                "duration_ms": 12.3,
            },
            "effect_analysis": {"status": "skipped:module_missing"},
        }
        manifest = {
            "indexed_at": 1700000000,
            "roam_version": "12.99.0",
            "schema_version": int(USER_VERSION),
            "parser_versions": {"tree_sitter": "0.25.2"},
            "grammar_versions": None,
            "config_hash": "a" * 64,
            "git_head": None,
            "git_dirty_hash": None,
            "enabled_extras": [],
            "index_profile": "all",
            "notes": None,
            "steps_status": synthetic_steps,
        }
        row_id = write_manifest(conn, manifest)
        assert row_id > 0

        latest = latest_manifest(conn)
        assert latest is not None
        assert latest["steps_status"] == synthetic_steps
    finally:
        conn.close()


def test_write_without_steps_status_keeps_column_null(tmp_path):
    """A manifest without steps_status reads back with that field as None."""
    conn = _fresh_db(tmp_path)
    try:
        manifest = collect_manifest(tmp_path, profile="all", conn=conn)
        # collect_manifest now defaults steps_status to None when not provided.
        assert manifest["steps_status"] is None
        write_manifest(conn, manifest)
        latest = latest_manifest(conn)
        assert latest is not None
        assert latest["steps_status"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# End-to-end: real indexer writes steps_status
# ---------------------------------------------------------------------------


def test_indexer_records_steps_status_on_clean_run(step_project):
    """A real `roam index` run leaves a steps_status JSON with succeeded steps."""
    out, rc = index_in_process(step_project)
    assert rc == 0, f"roam index failed:\n{out}"

    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=step_project) as conn:
        latest = latest_manifest(conn)
        assert latest is not None
        steps = latest.get("steps_status") or {}

    # The exact step inventory depends on what modules import cleanly in
    # the test env (effects / taint may be skipped:module_missing on a
    # lean install), but we MUST see at least the resolvers, clustering
    # and health steps populated — the always-on instrumentation.
    assert isinstance(steps, dict) and steps, "Expected non-empty steps_status, got: " + repr(steps)
    # No step should be in a ``failed:*`` state on a clean tiny repo.
    for step, entry in steps.items():
        status = entry["status"] if isinstance(entry, dict) else str(entry)
        assert not status.startswith("failed:"), f"Unexpected failure on clean run: {step} -> {status}"

    # Every recorded step that ran (not skipped) should carry a duration_ms.
    for step, entry in steps.items():
        if not isinstance(entry, dict):
            continue
        status = entry["status"]
        if status == "ok" or status.startswith("ok"):
            assert "duration_ms" in entry, f"{step} ran ok but has no duration_ms"


# ---------------------------------------------------------------------------
# Simulated failure
# ---------------------------------------------------------------------------


def test_record_step_captures_failed_status_with_excerpt():
    """``_record_step`` folds an exception class + message into the entry."""
    from roam.index.indexer import Indexer

    idx = Indexer.__new__(Indexer)  # bypass __init__ — we only need _record_step
    idx._record_step(
        "clustering",
        "failed:MemoryError",
        error="Unable to allocate 4.2 GiB for community matrix",
        duration_ms=18.42,
    )
    entry = idx._step_status["clustering"]
    assert entry["status"] == "failed:MemoryError"
    assert entry["error_excerpt"].startswith("Unable to allocate")
    assert entry["duration_ms"] == 18.42


def test_step_context_manager_records_failure_then_reraises():
    """The ``_step`` context manager records failed + re-raises the exception."""
    from roam.index.indexer import Indexer

    idx = Indexer.__new__(Indexer)
    with pytest.raises(RuntimeError, match="boom"):
        with idx._step("taint_analysis"):
            raise RuntimeError("boom — analysis crashed")

    entry = idx._step_status["taint_analysis"]
    assert entry["status"] == "failed:RuntimeError"
    assert "boom" in entry["error_excerpt"]
    assert "duration_ms" in entry


def test_step_context_manager_records_ok_on_clean_exit():
    """Clean exit defaults to ``ok``."""
    from roam.index.indexer import Indexer

    idx = Indexer.__new__(Indexer)
    with idx._step("git_analysis"):
        pass

    entry = idx._step_status["git_analysis"]
    assert entry["status"] == "ok"
    assert "duration_ms" in entry


def test_record_indexer_run_persists_steps_status(tmp_path, step_project):
    """``record_indexer_run`` threads steps_status into the dedicated column."""
    conn = _fresh_db(tmp_path)
    try:
        synthetic = {
            "clustering": {"status": "failed:OutOfMemoryError", "duration_ms": 1.0},
        }
        row_id = record_indexer_run(
            conn,
            step_project,
            profile="all",
            steps_status=synthetic,
        )
        assert row_id is not None and row_id > 0

        # Verify directly via the SQL column — not via latest_manifest —
        # so we know the value really hit the dedicated column rather
        # than getting smuggled into notes.
        raw = conn.execute(
            "SELECT steps_status FROM index_manifest WHERE id = ?",
            (row_id,),
        ).fetchone()[0]
        assert raw is not None
        decoded = json.loads(raw)
        assert decoded == synthetic
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Doctor advisory
# ---------------------------------------------------------------------------


def test_doctor_advisory_passes_when_all_steps_ok(step_project, monkeypatch):
    """Clean indexer run → ``Index step manifest`` advisory is a PASS."""
    out, rc = index_in_process(step_project)
    assert rc == 0, f"roam index failed:\n{out}"

    # Run the check in the project's working dir so db_exists() / open_db()
    # see the right index.
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(str(step_project))
        from roam.commands.cmd_doctor import _check_index_step_failures

        check = _check_index_step_failures()
    finally:
        os.chdir(old_cwd)

    assert check["name"] == "Index step manifest"
    assert check["passed"] is True
    assert check["_state"] in {"all_ok", "no_data"}


def test_doctor_advisory_names_failing_step(step_project):
    """A manifest carrying a ``failed:*`` step fires the advisory + names the step."""
    # Index once so a real manifest row exists, then mutate steps_status
    # on the latest row to simulate a sub-step failure that the doctor
    # check must surface.
    out, rc = index_in_process(step_project)
    assert rc == 0, f"roam index failed:\n{out}"

    from roam.db.connection import open_db

    with open_db(project_root=step_project) as conn:
        # Overwrite steps_status on the most recent row with a failure.
        fake = json.dumps(
            {
                "clustering": {
                    "status": "ok",
                    "duration_ms": 5.0,
                },
                "taint_analysis": {
                    "status": "failed:MemoryError",
                    "error_excerpt": "out of memory mid-DFS",
                    "duration_ms": 12.3,
                },
            },
            sort_keys=True,
        )
        conn.execute(
            "UPDATE index_manifest "
            "   SET steps_status = ? "
            " WHERE id = (SELECT id FROM index_manifest "
            "             ORDER BY indexed_at DESC, id DESC LIMIT 1)",
            (fake,),
        )
        conn.commit()

    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(str(step_project))
        from roam.commands.cmd_doctor import _check_index_step_failures

        check = _check_index_step_failures()
    finally:
        os.chdir(old_cwd)

    assert check["passed"] is False
    assert check["_state"] == "failures"
    assert "taint_analysis" in check["detail"]
    assert "MemoryError" in check["detail"]
    # Retry guidance must point at an executable command.
    assert "roam index" in check["detail"]
    # Surface count is correct
    assert check["_steps_failed"] == 1


def test_doctor_advisory_treats_no_index_as_pass(tmp_path, monkeypatch):
    """No index → advisory pass with the ``no_index`` state, not an error."""
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_doctor import _check_index_step_failures

    check = _check_index_step_failures()
    assert check["passed"] is True
    assert check["_state"] == "no_index"


def test_doctor_check_is_advisory_not_blocking():
    """Confirm the new check name is on the advisory allowlist.

    A failed sub-step is degraded data, not a broken install — it must
    not escalate ``roam doctor`` to the blocking exit code.
    """
    from roam.commands.cmd_doctor import _ADVISORY_CHECK_NAMES

    assert "Index step manifest" in _ADVISORY_CHECK_NAMES
