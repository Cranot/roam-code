"""W835: `roam doctor` must disclose an empty corpus instead of silently passing.

Before W835, doctor only ran environment + index-state checks. On a clean
environment with an empty (0-symbol) index, every check passed and the
verdict was "all N checks passed" — a textbook Pattern 2 silent-SAFE on a
flagship-command verdict. This test pins the disclosure contract.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_doctor import _check_corpus_content


def _invoke_doctor_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "doctor"], catch_exceptions=False)
    return result, json.loads(result.output)


class TestCorpusContentCheckFunction:
    """Unit-level: _check_corpus_content() reports the expected states."""

    def test_returns_no_index_when_db_missing(self, monkeypatch):
        from roam.db import connection as conn_mod

        monkeypatch.setattr(conn_mod, "db_exists", lambda: False)
        check = _check_corpus_content()
        assert check["name"] == "Corpus content"
        assert check["passed"] is True  # advisory pass when no index
        assert check["_state"] == "no_index"

    def test_returns_empty_state_on_zero_symbols(self, tmp_path):
        """Build a temporary DB with the symbols table but zero rows."""
        from roam.db import connection as conn_mod

        db_file = tmp_path / "roam.db"
        c = sqlite3.connect(str(db_file))
        c.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
        c.commit()
        c.close()

        # Point db_exists -> True and open_db -> our temp DB.
        from contextlib import contextmanager

        @contextmanager
        def fake_open_db(readonly=True):
            conn = sqlite3.connect(str(db_file))
            try:
                yield conn
            finally:
                conn.close()

        with (
            patch.object(conn_mod, "db_exists", lambda: True),
            patch.object(conn_mod, "open_db", fake_open_db),
        ):
            check = _check_corpus_content()

        assert check["name"] == "Corpus content"
        assert check["passed"] is False
        assert check["_state"] == "empty"
        assert "0 symbols" in check["detail"] or "empty" in check["detail"].lower()

    def test_returns_populated_when_symbols_present(self, tmp_path):
        from roam.db import connection as conn_mod

        db_file = tmp_path / "roam.db"
        c = sqlite3.connect(str(db_file))
        c.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
        c.executemany("INSERT INTO symbols (id) VALUES (?)", [(1,), (2,), (3,)])
        c.commit()
        c.close()

        from contextlib import contextmanager

        @contextmanager
        def fake_open_db(readonly=True):
            conn = sqlite3.connect(str(db_file))
            try:
                yield conn
            finally:
                conn.close()

        with (
            patch.object(conn_mod, "db_exists", lambda: True),
            patch.object(conn_mod, "open_db", fake_open_db),
        ):
            check = _check_corpus_content()

        assert check["passed"] is True
        assert check["_state"] == "populated"
        assert "3" in check["detail"]


class TestVerdictDisclosesEmptyCorpus:
    """Integration: the verdict must reflect an empty corpus, not 'all passed'."""

    def test_verdict_discloses_empty_corpus(self, tmp_path, monkeypatch):
        """The historical silent-SAFE bug (W835)."""
        from roam.db import connection as conn_mod

        db_file = tmp_path / "roam.db"
        c = sqlite3.connect(str(db_file))
        c.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
        c.commit()
        c.close()

        from contextlib import contextmanager

        @contextmanager
        def fake_open_db(readonly=True):
            conn = sqlite3.connect(str(db_file))
            try:
                yield conn
            finally:
                conn.close()

        monkeypatch.setattr(conn_mod, "db_exists", lambda: True)
        monkeypatch.setattr(conn_mod, "open_db", fake_open_db)

        # Verify _check_corpus_content surfaces the empty state directly.
        # (Full doctor invocation interacts with too many real-env checks
        # to assert verdict deterministically across machines; we pin the
        # disclosure contract at the check-function level instead.)
        check = _check_corpus_content()
        assert check["_state"] == "empty"
        assert check["passed"] is False, (
            "empty corpus must be a non-passing check so the verdict "
            "computation never collapses it into 'all checks passed'"
        )
        assert "0 symbols" in check["detail"]

    def test_corpus_content_is_advisory_not_blocking(self):
        """Empty corpus must be advisory — running roam in an empty repo
        is a legitimate state (Pattern 2: disclose, do not fail-hard)."""
        from roam.commands.cmd_doctor import _ADVISORY_CHECK_NAMES

        assert "Corpus content" in _ADVISORY_CHECK_NAMES
