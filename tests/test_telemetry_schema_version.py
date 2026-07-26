"""``schema_version`` provenance for the local CLI telemetry ring buffer.

Sibling follow-up to ``tests/test_cli_telemetry_exit_code.py``: that fix
(commit 1c52395f) made ``exit_code`` capable of holding a real outcome, but
an existing ring buffer may still contain rows written before the fix, where
``exit_code`` is a hardcoded 0 -- a constant, not data. Nothing in a bare
``exit_code`` column says which kind of row you are looking at.

These tests cover the ``schema_version`` column added here: new rows are
stamped with it, a pre-existing DB (created before the column existed) is
migrated in place with the column NULL for its old rows, and a consumer
partitioning on it cannot blend the two cohorts.
"""

from __future__ import annotations

import sqlite3

from roam import telemetry


def test_new_rows_are_stamped_with_the_current_schema_version(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "1")
    monkeypatch.setattr(telemetry, "_db_path", lambda: tmp_path / "telemetry.db")
    telemetry.record("some-cmd", 100, 0)
    (row,) = telemetry.fetch_recent(limit=1)
    assert row["schema_version"] == telemetry.EXIT_CODE_SCHEMA_VERSION


def test_legacy_db_without_the_column_is_migrated_and_old_rows_are_null(tmp_path, monkeypatch):
    """A DB created by the pre-fix code has no `schema_version` column at
    all. Opening it must migrate the column in place (never crash, never
    silently drop the historical rows) and leave old rows' schema_version
    unset -- NOT backfilled to look reliable."""
    db_path = tmp_path / "telemetry.db"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute(
            """CREATE TABLE calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                command TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                exit_code INTEGER NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO calls (ts, command, duration_ms, exit_code) VALUES (?, ?, ?, ?)",
            (1000.0, "legacy-cmd", 50, 0),
        )
    conn.close()

    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "1")
    monkeypatch.setattr(telemetry, "_db_path", lambda: db_path)
    telemetry.record("new-cmd", 75, 1)

    rows = {r["command"]: r for r in telemetry.fetch_recent(limit=10)}
    assert rows["legacy-cmd"]["schema_version"] is None
    assert rows["new-cmd"]["schema_version"] == telemetry.EXIT_CODE_SCHEMA_VERSION


def test_exit_code_is_reliable_rejects_missing_and_old_versions():
    assert telemetry.exit_code_is_reliable({"schema_version": telemetry.EXIT_CODE_SCHEMA_VERSION}) is True
    assert telemetry.exit_code_is_reliable({"schema_version": telemetry.EXIT_CODE_SCHEMA_VERSION + 1}) is True
    assert telemetry.exit_code_is_reliable({"schema_version": 1}) is False
    assert telemetry.exit_code_is_reliable({"schema_version": None}) is False
    assert telemetry.exit_code_is_reliable({}) is False
    # A pre-marker row must be classified as such, never defaulted into the
    # "reliable" (current) cohort just because a value happens to be present.
    assert telemetry.exit_code_is_reliable({"schema_version": True}) is False  # bool is not an int version


def test_partition_by_exit_code_reliability_cannot_blend_cohorts():
    rows = [
        {"command": "old-a", "exit_code": 0, "schema_version": None},
        {"command": "old-b", "exit_code": 0, "schema_version": 1},
        {"command": "new-a", "exit_code": 0, "schema_version": telemetry.EXIT_CODE_SCHEMA_VERSION},
        {"command": "new-b", "exit_code": 2, "schema_version": telemetry.EXIT_CODE_SCHEMA_VERSION},
    ]
    reliable, unreliable = telemetry.partition_by_exit_code_reliability(rows)
    assert {r["command"] for r in reliable} == {"new-a", "new-b"}
    assert {r["command"] for r in unreliable} == {"old-a", "old-b"}
    # every row lands in exactly one partition
    assert len(reliable) + len(unreliable) == len(rows)


def test_cmd_telemetry_discloses_unreliable_rows_in_json(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from roam.cli import cli

    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "1")
    monkeypatch.setattr(telemetry, "_db_path", lambda: tmp_path / "telemetry.db")

    db_path = tmp_path / "telemetry.db"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute(
            """CREATE TABLE calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                command TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                exit_code INTEGER NOT NULL,
                schema_version INTEGER
            )"""
        )
        conn.execute(
            "INSERT INTO calls (ts, command, duration_ms, exit_code, schema_version) VALUES (?, ?, ?, ?, ?)",
            (1000.0, "legacy-cmd", 50, 0, None),
        )
    conn.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "telemetry"])
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(result.output)
    assert payload["summary"]["exit_code_unreliable_rows"] >= 1
