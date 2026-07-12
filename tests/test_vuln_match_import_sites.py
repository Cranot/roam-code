"""Real-index integration tests for CVE-to-import-site matching."""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.index.indexer import Indexer
from roam.security import vuln_store
from roam.security.vuln_store import (
    _reset_import_reachability_cache,
    ingest_npm_audit,
    ingest_pip_audit,
    match_vuln_to_symbols,
)


def _index(root: Path) -> None:
    (root / ".git").mkdir()
    old_cwd = Path.cwd()
    try:
        os.chdir(root)
        Indexer(project_root=root).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old_cwd)


def test_real_index_matches_python_import_sites_and_preserves_legacy_default(tmp_path, monkeypatch) -> None:
    root = tmp_path / "python-project"
    root.mkdir()
    (root / "app.py").write_text("import requests\nimport yaml\n", encoding="utf-8")
    _index(root)
    monkeypatch.chdir(root)

    with open_db(readonly=True) as conn:
        requests_matches = match_vuln_to_symbols(conn, "requests", project_root=root)
        pyyaml_matches = match_vuln_to_symbols(conn, "PyYAML", project_root=root)
        legacy_matches = match_vuln_to_symbols(conn, "requests")

    assert any(match["match_kind"] == "import_site" and match["file_path"] == "app.py" for match in requests_matches)
    assert any(match["match_kind"] == "import_site" for match in pyyaml_matches)
    assert legacy_matches == []


def test_local_variable_name_is_not_import_site_evidence(tmp_path, monkeypatch) -> None:
    root = tmp_path / "javascript-project"
    root.mkdir()
    (root / "app.js").write_text("const express = 1;\n", encoding="utf-8")
    _index(root)
    monkeypatch.chdir(root)

    with open_db(readonly=True) as conn:
        matches = match_vuln_to_symbols(conn, "express", project_root=root)

    assert not any(match["match_kind"] == "import_site" for match in matches)


def test_pip_and_npm_ingest_store_imported_file(tmp_path, monkeypatch) -> None:
    python_root = tmp_path / "python-project"
    python_root.mkdir()
    (python_root / "app.py").write_text("import requests\n", encoding="utf-8")
    pip_report = python_root / "pip-audit.json"
    pip_report.write_text(
        json.dumps([{"name": "requests", "vulns": [{"id": "CVE-2026-0001"}]}]),
        encoding="utf-8",
    )
    _index(python_root)
    monkeypatch.chdir(python_root)
    with open_db(readonly=False) as conn:
        ingest_pip_audit(conn, str(pip_report), project_root=python_root)
        pip_row = conn.execute(
            "SELECT matched_file FROM vulnerabilities WHERE cve_id = ?",
            ("CVE-2026-0001",),
        ).fetchone()
    assert pip_row["matched_file"] == "app.py"

    vulns_result = CliRunner().invoke(
        cli,
        ["vulns", "--import-file", str(pip_report), "--format", "pip-audit"],
        catch_exceptions=False,
    )
    assert vulns_result.exit_code == 0
    assert "app.py:1 (imported)" in vulns_result.output

    vuln_reach_result = CliRunner().invoke(cli, ["vuln-reach"], catch_exceptions=False)
    assert vuln_reach_result.exit_code == 0
    assert "IMPORT-REACHABLE" in vuln_reach_result.output
    assert "imported at app.py; no call-graph trace available" in vuln_reach_result.output

    json_result = CliRunner().invoke(
        cli,
        ["--json", "vulns"],
        catch_exceptions=False,
    )
    assert json_result.exit_code == 0
    json_vulns = json.loads(json_result.output)["vulnerabilities"]
    assert any(vuln["value"]["match_evidence"] == "app.py:1 (imported)" for vuln in json_vulns)

    javascript_root = tmp_path / "javascript-project"
    javascript_root.mkdir()
    (javascript_root / "app.js").write_text("const _ = require('lodash');\n", encoding="utf-8")
    npm_report = javascript_root / "npm-audit.json"
    npm_report.write_text(
        json.dumps(
            {
                "vulnerabilities": {
                    "lodash": {
                        "severity": "high",
                        "via": [{"url": "https://github.com/advisories/GHSA-test-0001"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _index(javascript_root)
    monkeypatch.chdir(javascript_root)
    with open_db(readonly=False) as conn:
        ingest_npm_audit(conn, str(npm_report), project_root=javascript_root)
        npm_row = conn.execute(
            "SELECT matched_file FROM vulnerabilities WHERE cve_id = ?",
            ("GHSA-test-0001",),
        ).fetchone()
    assert npm_row["matched_file"] == "app.js"


def test_import_scan_cache_reuses_and_resets_project_scan(tmp_path, monkeypatch) -> None:
    root = tmp_path / "cached-project"
    root.mkdir()
    (root / "app.py").write_text("import requests\nimport yaml\n", encoding="utf-8")
    _index(root)
    monkeypatch.chdir(root)

    real_scan = vuln_store.scan_import_reachability
    calls = 0

    def counting_scan(project_root):
        nonlocal calls
        calls += 1
        return real_scan(project_root)

    monkeypatch.setattr(vuln_store, "scan_import_reachability", counting_scan)
    _reset_import_reachability_cache()
    with open_db(readonly=True) as conn:
        match_vuln_to_symbols(conn, "requests", project_root=root)
        match_vuln_to_symbols(conn, "PyYAML", project_root=root)
        assert calls == 1
        _reset_import_reachability_cache()
        match_vuln_to_symbols(conn, "requests", project_root=root)
        assert calls == 2
    _reset_import_reachability_cache()
