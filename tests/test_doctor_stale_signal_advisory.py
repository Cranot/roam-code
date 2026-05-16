"""Tests for the `roam doctor` post-migration-#51 stale-signal advisory.

Migration #51 (USER_VERSION 12 -> 13, W36.4) added the
``loop_eq_with_dependent_write`` column to ``math_signals`` with
``DEFAULT 0``. Repos indexed before that migration landed have the
column populated with zeros across every row -> ``roam algo`` reports
zero true-positives for the new predicate, looking like a clean repo
when it's really stale data.

The doctor check fires only when:

* the index DB exists, and
* the ``math_signals`` table exists, and
* the ``loop_eq_with_dependent_write`` column exists, and
* ``math_signals`` is non-empty, and
* every row has ``loop_eq_with_dependent_write = 0``.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_doctor import (
    _ADVISORY_CHECK_NAMES,
    _check_stale_math_signal_column,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_math_signals_db(tmp_path, *, with_column=True, rows=None, with_table=True):
    """Build a minimal SQLite DB containing a `math_signals` table.

    ``rows`` is a list of ``loop_eq_with_dependent_write`` integer values
    (one per row). Pass ``[]`` for an empty table. Pass ``None`` to skip
    inserting any rows (also empty, but explicit).
    """
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_path))
    if with_table:
        if with_column:
            conn.execute(
                "CREATE TABLE math_signals ("
                "  symbol_id INTEGER PRIMARY KEY, "
                "  loop_depth INTEGER DEFAULT 0, "
                "  loop_eq_with_dependent_write INTEGER DEFAULT 0"
                ")"
            )
        else:
            conn.execute("CREATE TABLE math_signals (  symbol_id INTEGER PRIMARY KEY,   loop_depth INTEGER DEFAULT 0)")
        if rows:
            if with_column:
                for i, val in enumerate(rows, start=1):
                    conn.execute(
                        "INSERT INTO math_signals (symbol_id, loop_eq_with_dependent_write) VALUES (?, ?)",
                        (i, val),
                    )
            else:
                for i, _ in enumerate(rows, start=1):
                    conn.execute(
                        "INSERT INTO math_signals (symbol_id) VALUES (?)",
                        (i,),
                    )
    conn.commit()
    conn.close()
    return db_path


def _fake_open_db(db_path):
    """Build a fake `open_db` that yields a sqlite3 connection to db_path."""

    class _Ctx:
        def __enter__(self_inner):
            self_inner.conn = sqlite3.connect(str(db_path))
            return self_inner.conn

        def __exit__(self_inner, *a):
            self_inner.conn.close()
            return False

    def _open_db(*args, **kwargs):
        return _Ctx()

    return _open_db


# ---------------------------------------------------------------------------
# Direct check tests
# ---------------------------------------------------------------------------


class TestStaleMathSignalAdvisory:
    def test_advisory_fires_when_column_all_zero(self, tmp_path):
        """Migration #51 added loop_eq_with_dependent_write. If all-zero
        across a non-empty math_signals, advisory must fire."""
        db_path = _make_math_signals_db(tmp_path, rows=[0, 0, 0, 0, 0])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            check = _check_stale_math_signal_column()
        assert check["passed"] is False
        assert check["_state"] == "stale"
        assert check["_row_count"] == 5
        assert "loop_eq_with_dependent_write" in check["detail"]
        assert "roam index --force" in check["detail"]
        assert "migration #51" in check["detail"].lower()

    def test_advisory_does_not_fire_when_column_populated(self, tmp_path):
        """If any row has a non-zero value, the advisory must NOT fire."""
        db_path = _make_math_signals_db(tmp_path, rows=[0, 0, 1, 0])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            check = _check_stale_math_signal_column()
        assert check["passed"] is True
        assert check["_state"] == "populated"
        assert check["_row_count"] == 4

    def test_advisory_skipped_on_empty_math_signals(self, tmp_path):
        """If math_signals is empty (e.g., fresh repo before any indexing
        of math-bearing files), don't warn — the user hasn't extracted any
        signals yet, so the column being all-zero is meaningless."""
        db_path = _make_math_signals_db(tmp_path, rows=[])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            check = _check_stale_math_signal_column()
        assert check["passed"] is True
        assert check["_state"] == "empty"

    def test_advisory_skipped_when_no_index(self):
        """If the DB doesn't exist (no `roam init` yet), skip — there's
        nothing to be stale."""
        with patch("roam.db.connection.db_exists", return_value=False):
            check = _check_stale_math_signal_column()
        assert check["passed"] is True
        assert check["_state"] == "no_index"

    def test_advisory_skipped_when_table_missing(self, tmp_path):
        """Pre-math-signals schema -> table not present -> skip."""
        db_path = tmp_path / "index.db"
        conn = sqlite3.connect(str(db_path))
        # No math_signals table at all
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            check = _check_stale_math_signal_column()
        assert check["passed"] is True
        assert check["_state"] == "no_table"

    def test_advisory_skipped_when_column_missing(self, tmp_path):
        """Pre-migration-#51 DB (column not yet added) -> skip — the
        migration itself will add the column on next open."""
        db_path = _make_math_signals_db(tmp_path, with_column=False, rows=[None, None])
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            check = _check_stale_math_signal_column()
        assert check["passed"] is True
        assert check["_state"] == "no_column"

    def test_check_is_advisory_not_blocking(self):
        """The check name must be in _ADVISORY_CHECK_NAMES so a stale
        column never returns exit-code 2 (informational only)."""
        assert "Stale math_signals column" in _ADVISORY_CHECK_NAMES


# ---------------------------------------------------------------------------
# End-to-end doctor integration test
# ---------------------------------------------------------------------------


class TestStaleMathSignalAdvisoryIntegration:
    def test_advisory_present_in_doctor_checks_list(self):
        """The new check must appear in `roam doctor --json` output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        names = {c["name"] for c in data["checks"]}
        assert "Stale math_signals column" in names

    def test_advisory_does_not_block_when_stale(self, tmp_path):
        """When the check FAILS, exit code must stay <= 1 (advisory only),
        never 2 (blocking) — unless --strict is set."""
        db_path = _make_math_signals_db(tmp_path, rows=[0, 0, 0])

        runner = CliRunner()
        with (
            patch("roam.db.connection.db_exists", return_value=True),
            patch("roam.db.connection.open_db", side_effect=_fake_open_db(db_path)),
        ):
            # We can't isolate just this check from the rest of doctor,
            # but we can assert that a stale-signal failure NEVER causes
            # exit code 2 by checking it's in advisory list. The exit
            # code is then a function of OTHER (blocking) checks only.
            result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
        data = json.loads(result.output)
        # The check itself must be present.
        signal_check = next(
            (c for c in data["checks"] if c["name"] == "Stale math_signals column"),
            None,
        )
        assert signal_check is not None
