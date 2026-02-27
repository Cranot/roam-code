"""Tests for roam docs-coverage -- documentation coverage analysis."""

from __future__ import annotations

import json

import pytest

from roam.exit_codes import EXIT_GATE_FAILURE
from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def docs_project(tmp_path):
    """Project with mix of documented and undocumented functions."""
    proj = tmp_path / "docs_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "documented.py").write_text(
        "def well_documented():\n"
        '    """This function has great docs."""\n'
        "    return 42\n"
        "\n"
        "def also_documented():\n"
        '    """Another well-documented function."""\n'
        "    return 99\n"
    )

    (proj / "undocumented.py").write_text("def missing_docs():\n    return 1\n\ndef also_missing():\n    return 2\n")

    git_init(proj)
    index_in_process(proj)
    return proj


class TestDocsCoverageSmoke:
    def test_exits_zero(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        result = invoke_cli(cli_runner, ["docs-coverage"], cwd=docs_project)
        assert result.exit_code == 0

    def test_with_threshold(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        # Use threshold 0 so it always passes
        result = invoke_cli(cli_runner, ["docs-coverage", "--threshold", "0"], cwd=docs_project)
        assert result.exit_code == 0


class TestDocsCoverageJSON:
    def test_json_envelope(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        result = invoke_cli(cli_runner, ["docs-coverage"], cwd=docs_project, json_mode=True)
        data = parse_json_output(result, "docs-coverage")
        assert_json_envelope(data, "docs-coverage")

    def test_json_summary_has_verdict(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        result = invoke_cli(cli_runner, ["docs-coverage"], cwd=docs_project, json_mode=True)
        data = parse_json_output(result, "docs-coverage")
        assert "verdict" in data["summary"]

    def test_json_has_coverage_data(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        result = invoke_cli(cli_runner, ["docs-coverage"], cwd=docs_project, json_mode=True)
        data = parse_json_output(result, "docs-coverage")
        summary = data["summary"]
        # Should have coverage percentage
        assert "coverage" in summary or "coverage_pct" in summary or "documented" in summary


class TestDocsCoverageText:
    def test_output_mentions_coverage(self, cli_runner, docs_project, monkeypatch):
        monkeypatch.chdir(docs_project)
        result = invoke_cli(cli_runner, ["docs-coverage"], cwd=docs_project)
        output = result.output.lower()
        assert "coverage" in output or "documented" in output


# --- Legacy tests using indexed_project fixture ---


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
