"""Dependency vulnerability severity must be independent of available fixed versions."""

import json
import sqlite3
from contextlib import closing

import pytest

from roam.security.vuln_store import ingest_pip_audit


@pytest.mark.parametrize("wrapped", [False, True], ids=["list", "dependencies"])
@pytest.mark.parametrize("fix_versions", [["2.3.1"], [], None], ids=["fixed", "empty", "absent"])
def test_pip_report_preserves_reported_severity(tmp_path, monkeypatch, wrapped, fix_versions):
    monkeypatch.setenv("HOME", str(tmp_path))
    vuln = {"id": "PYSEC-2024-1001", "severity": "high"}
    if fix_versions is not None:
        vuln["fix_versions"] = fix_versions
    dependencies = [{"name": "example-package", "version": "2.3.0", "vulns": [vuln]}]
    report = tmp_path / "vulnerabilities.json"
    report.write_text(json.dumps({"dependencies": dependencies} if wrapped else dependencies), encoding="utf-8")

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        results = ingest_pip_audit(conn, str(report))
        stored = conn.execute("SELECT severity FROM vulnerabilities").fetchall()

    assert len(stored) == 1
    assert stored[0]["severity"] == "high"
    assert len(results) == 1
    assert results[0]["severity"] == "high"


@pytest.mark.parametrize("wrapped", [False, True], ids=["list", "dependencies"])
def test_pip_report_missing_severity_defaults_to_unknown(tmp_path, monkeypatch, wrapped):
    monkeypatch.setenv("HOME", str(tmp_path))
    dependencies = [{"name": "example-package", "vulns": [{"id": "PYSEC-2024-1002"}]}]
    report = tmp_path / "vulnerabilities.json"
    report.write_text(json.dumps({"dependencies": dependencies} if wrapped else dependencies), encoding="utf-8")

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        results = ingest_pip_audit(conn, str(report))
        stored = conn.execute("SELECT severity FROM vulnerabilities").fetchall()

    assert len(stored) == 1
    assert stored[0]["severity"] == "unknown"
    assert len(results) == 1
    assert results[0]["severity"] == "unknown"
