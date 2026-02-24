from __future__ import annotations

import json
import sys
from pathlib import Path

from roam.exit_codes import EXIT_GATE_FAILURE

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def test_docs_coverage_runs(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["docs-coverage"], cwd=indexed_project)
    assert result.exit_code == 0, f"docs-coverage failed: {result.output}"
    assert "Documentation coverage" in result.output


def test_docs_coverage_json(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["docs-coverage"],
        cwd=indexed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "docs-coverage")
    assert_json_envelope(data, "docs-coverage")
    summary = data["summary"]
    assert "coverage_pct" in summary
    assert "public_symbols" in summary
    assert "documented_symbols" in summary
    assert "missing_docs" in data
    assert "stale_docs" in data


def test_docs_coverage_threshold_gate_fail(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["docs-coverage", "--threshold", "101"],
        cwd=indexed_project,
    )
    assert result.exit_code == EXIT_GATE_FAILURE


def test_docs_coverage_threshold_gate_fail_json(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["docs-coverage", "--threshold", "101"],
        cwd=indexed_project,
        json_mode=True,
    )
    assert result.exit_code == EXIT_GATE_FAILURE
    data = json.loads(result.output)
    assert data["summary"]["gate_passed"] is False
