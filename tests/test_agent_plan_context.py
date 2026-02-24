"""Tests for multi-agent v2 commands: agent-plan and agent-context."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


@pytest.fixture
def agent_project(project_factory):
    return project_factory({
        "auth/login.py": (
            "from auth.tokens import create_token\n"
            "def authenticate(u, p): return create_token(u)\n"
        ),
        "auth/tokens.py": (
            "def create_token(user): return 'tok'\n"
            "def verify_token(t): return True\n"
        ),
        "billing/invoice.py": (
            "from billing.tax import calc_tax\n"
            "def create_invoice(order): return calc_tax(order)\n"
        ),
        "billing/tax.py": (
            "def calc_tax(order): return order * 0.1\n"
        ),
        "api/routes.py": (
            "from auth.login import authenticate\n"
            "from billing.invoice import create_invoice\n"
            "def handle(r): authenticate(r, r); return create_invoice(r)\n"
        ),
        "models.py": (
            "class User:\n"
            "    pass\n"
            "class Order:\n"
            "    pass\n"
        ),
    })


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def test_agent_plan_json(agent_project, cli_runner):
    result = invoke_cli(
        cli_runner,
        ["agent-plan", "--agents", "3"],
        cwd=agent_project,
        json_mode=True,
    )
    data = parse_json_output(result, command="agent-plan")
    assert_json_envelope(data, command="agent-plan")
    assert "tasks" in data
    assert len(data["tasks"]) == 3
    assert "merge_sequence" in data
    assert "handoffs" in data
    assert "claude_teams" in data

    task = data["tasks"][0]
    assert "task_id" in task
    assert "agent_id" in task
    assert "phase" in task
    assert "depends_on_partitions" in task
    assert "write_files" in task


def test_agent_plan_claude_teams_format(agent_project, cli_runner):
    result = invoke_cli(
        cli_runner,
        ["agent-plan", "--agents", "2", "--format", "claude-teams"],
        cwd=agent_project,
        json_mode=True,
    )
    data = parse_json_output(result, command="agent-plan")
    assert_json_envelope(data, command="agent-plan")
    assert data.get("format") == "claude-teams"
    assert "agents" in data
    assert "coordination" in data


def test_agent_context_json(agent_project, cli_runner):
    result = invoke_cli(
        cli_runner,
        ["agent-context", "--agent-id", "1", "--agents", "3"],
        cwd=agent_project,
        json_mode=True,
    )
    data = parse_json_output(result, command="agent-context")
    assert_json_envelope(data, command="agent-context")
    assert "agent" in data
    assert "write_files" in data
    assert "read_only_dependencies" in data
    assert "interface_contracts" in data
    assert "instructions" in data


def test_agent_context_invalid_agent(agent_project, cli_runner):
    result = invoke_cli(
        cli_runner,
        ["agent-context", "--agent-id", "9", "--agents", "2"],
        cwd=agent_project,
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "larger --agents" in result.output
