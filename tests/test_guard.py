"""Tests for `roam guard` sub-agent preflight bundle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def test_guard_text(indexed_project, cli_runner, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["guard", "User"])
    assert result.exit_code == 0
    output = result.output
    assert "GUARD:" in output
    assert "Risk:" in output
    assert "Callers (" in output
    assert "Callees (" in output
    assert "Tests (" in output
    assert "Layer analysis:" in output


def test_guard_json(indexed_project, cli_runner, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["guard", "User"], json_mode=True)
    data = parse_json_output(result, "guard")
    assert_json_envelope(data, "guard")

    summary = data["summary"]
    assert "risk_score" in summary
    assert "risk_level" in summary
    assert "signals" in summary

    assert "definition" in data
    assert "callers" in data
    assert "callees" in data
    assert "tests" in data
    assert "risk" in data
    assert "layer_analysis" in data


def test_guard_unknown_symbol(indexed_project, cli_runner, monkeypatch):
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["guard", "DoesNotExist"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
