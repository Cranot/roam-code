"""Keep SQLite's evidence visible and release read-only diagnostic handles."""

from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_doctor as doctor


class DiagnosticConnection:
    def __init__(self, rows=None, error=None):
        self.rows = rows
        self.error = error
        self.closed = False

    def execute(self, sql):
        assert sql == "PRAGMA integrity_check"
        if self.error:
            raise self.error
        return self

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


def _fake_connection(tmp_path, monkeypatch, rows=None, error=None):
    path = tmp_path / "index # percent%.db"
    path.touch()
    conn = DiagnosticConnection(rows, error)
    calls = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        if str(args[0]) not in {str(path), path.resolve().as_uri() + "?mode=ro"}:
            return real_connect(*args, **kwargs)
        calls.append((args, kwargs))
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    return path, conn, calls


def test_fts_integrity_reason_survives(tmp_path, monkeypatch):
    detail = "malformed inverted index for FTS5 table main.symbol_fts"
    path, conn, _ = _fake_connection(tmp_path, monkeypatch, [(detail,)])
    check = doctor._check_sqlite(str(path))
    assert check["passed"] is False
    assert detail in check["detail"]
    assert check["integrity_errors"] == [detail]
    assert check["integrity_errors_truncated"] is False
    assert conn.closed


def test_exception_closes_connection(tmp_path, monkeypatch):
    path, conn, _ = _fake_connection(
        tmp_path, monkeypatch, error=sqlite3.DatabaseError("database disk image is malformed")
    )
    check = doctor._check_sqlite(str(path))
    assert check["passed"] is False
    assert "malformed" in check["detail"]
    assert conn.closed


def test_diagnostic_connection_is_read_only(tmp_path, monkeypatch):
    path, conn, calls = _fake_connection(tmp_path, monkeypatch, [("ok",)])
    assert doctor._check_sqlite(str(path))["passed"] is True
    assert calls == [((path.resolve().as_uri() + "?mode=ro",), {"uri": True, "timeout": 5})]
    assert conn.closed


@pytest.mark.parametrize("rows", [[], [("ok",), ("broken index",)]])
def test_only_exact_ok_is_a_pass(tmp_path, monkeypatch, rows):
    path, _, _ = _fake_connection(tmp_path, monkeypatch, rows)
    check = doctor._check_sqlite(str(path))
    assert check["passed"] is False
    assert check["integrity_errors"]


def test_error_evidence_is_bounded_and_discloses_truncation(tmp_path, monkeypatch):
    path, _, _ = _fake_connection(tmp_path, monkeypatch, [("x" * 1000,)] * 40)
    check = doctor._check_sqlite(str(path))
    assert len(check["integrity_errors"]) <= 20
    assert all(len(s) <= 500 for s in check["integrity_errors"])
    assert check["integrity_errors_truncated"] is True
    assert len(check["detail"]) < 1800


def test_real_valid_db_is_unchanged(tmp_path):
    path = tmp_path / "index # database.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE VIRTUAL TABLE symbol_fts USING fts5(name)")
    conn.execute("INSERT INTO symbol_fts VALUES ('fixture')")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    assert doctor._check_sqlite(str(path))["passed"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_doctor_cli_carries_integrity_failure(tmp_path, monkeypatch, json_mode):
    detail = "malformed inverted index for FTS5 table main.symbol_fts"
    path, _, _ = _fake_connection(tmp_path, monkeypatch, [(detail,)])
    # Isolate unrelated environment checks; run the real SQLite helper and
    # doctor aggregator/serialization/exit-code path with the failing result.
    for name in vars(doctor).copy():
        if name.startswith("_check_") and name != "_check_sqlite":
            monkeypatch.setattr(doctor, name, lambda *a, **kw: {"name": "control", "passed": True, "detail": "ok"})
    monkeypatch.setattr(
        doctor,
        "_check_index_exists",
        lambda: {
            "name": "Index exists",
            "passed": True,
            "detail": "ok",
            "_db_path": str(path),
        },
    )
    result = CliRunner().invoke(cli, (["--json"] if json_mode else []) + ["doctor"])
    assert result.exit_code == 2, result.output
    assert detail in result.stdout
    if json_mode:
        payload = json.loads(result.stdout)
        assert payload["summary"]["blocking_failed"] == 1
        check = next(c for c in payload["checks"] if c["name"] == "SQLite operational")
        assert check["integrity_errors"] == [detail]
