"""Tests for global --agent CLI mode (backlog #124)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


def test_agent_mode_outputs_compact_json(cli_runner, indexed_project):
    """--agent should force JSON and use compact envelope fields."""
    result = invoke_cli(cli_runner, ["--agent", "health"], cwd=indexed_project)
    data = parse_json_output(result, command="health")

    assert data.get("command") == "health"
    assert isinstance(data.get("summary"), dict)
    assert "version" not in data
    assert "schema" not in data
    assert "_meta" not in data


def test_agent_mode_respects_explicit_budget(cli_runner, indexed_project):
    """--agent with a tight --budget should produce truncated JSON metadata."""
    result = invoke_cli(
        cli_runner,
        ["--agent", "--budget", "20", "health"],
        cwd=indexed_project,
    )
    data = parse_json_output(result, command="health")

    summary = data.get("summary", {})
    assert summary.get("truncated") is True
    assert summary.get("budget_tokens") == 20


def test_agent_mode_conflicts_with_sarif(cli_runner):
    """--agent and --sarif are incompatible output modes."""
    result = invoke_cli(cli_runner, ["--agent", "--sarif", "health"])

    assert result.exit_code == 2
    assert "--agent cannot be combined with --sarif" in result.output
