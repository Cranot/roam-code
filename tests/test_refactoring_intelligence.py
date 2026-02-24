"""Tests for proactive refactoring intelligence commands (#140, #141)."""

from __future__ import annotations

import sys
from pathlib import Path

from roam.cli import cli

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def test_suggest_refactoring_runs(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["suggest-refactoring"], cwd=indexed_project)
    assert result.exit_code == 0, result.output
    assert "VERDICT:" in result.output


def test_suggest_refactoring_json(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["--detail", "suggest-refactoring", "--limit", "5"],
        cwd=indexed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "suggest-refactoring")
    assert_json_envelope(data, "suggest-refactoring")
    assert "recommendations" in data
    assert isinstance(data["recommendations"], list)


def test_suggest_refactoring_high_threshold_empty(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["suggest-refactoring", "--min-score", "101"],
        cwd=indexed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "suggest-refactoring")
    assert data["summary"]["candidates"] == 0


def test_plan_refactor_runs(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["plan-refactor", "create_user"],
        cwd=indexed_project,
    )
    assert result.exit_code == 0, result.output
    assert "Steps:" in result.output


def test_plan_refactor_json(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(
        cli_runner,
        ["--detail", "plan-refactor", "create_user"],
        cwd=indexed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "plan-refactor")
    assert_json_envelope(data, "plan-refactor")
    assert "plan" in data
    assert isinstance(data["plan"], list)
    assert len(data["plan"]) >= 1


def test_plan_refactor_symbol_not_found(cli_runner, indexed_project, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = cli_runner.invoke(
        cli,
        ["plan-refactor", "nonexistent_symbol_zzzz"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
