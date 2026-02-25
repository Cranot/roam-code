"""Tests for `roam plan` -- Agent Work Planner command."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
)

from roam.commands.cmd_plan import plan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cli():
    """Wrap plan command in a minimal CLI group for testing."""

    @click.group()
    @click.option("--json", "json_mode", is_flag=True)
    @click.pass_context
    def cli(ctx, json_mode):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_mode

    cli.add_command(plan)
    return cli


def invoke_plan(runner, args, cwd=None, json_mode=False):
    """Invoke the plan command directly."""
    cli = _make_cli()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(["plan"] + list(args))

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result, label="plan"):
    assert result.exit_code == 0, f"{label} failed (exit {result.exit_code}):\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {label}: {e}\nOutput was:\n{result.output[:500]}")


# ---------------------------------------------------------------------------
# Fixture: a small project with clear call relationships
# ---------------------------------------------------------------------------


@pytest.fixture
def plan_project(tmp_path):
    """Create a project with symbols and call relationships."""
    proj = tmp_path / "plan_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def display(self):\n"
        "        return self.name.title()\n"
    )
    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name):\n"
        "    user = User(name)\n"
        "    return user\n"
        "\n"
        "def _helper():\n"
        "    return 42\n"
    )
    (proj / "api.py").write_text(
        "from service import create_user\n\ndef handle_request(data):\n    return create_user(data['name'])\n"
    )
    (proj / "test_service.py").write_text(
        "from service import create_user\n"
        "\n"
        "def test_create():\n"
        "    u = create_user('Alice')\n"
        "    assert u.name == 'Alice'\n"
    )

    git_init(proj)
    old = os.getcwd()
    os.chdir(str(proj))
    try:
        index_in_process(proj)
    finally:
        os.chdir(old)
    return proj


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanBasic:
    """Basic smoke tests for roam plan."""

    def test_plan_runs(self, cli_runner, plan_project, monkeypatch):
        """roam plan with a valid symbol exits 0."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project)
        assert result.exit_code == 0, f"plan exited {result.exit_code}:\n{result.output}"

    def test_plan_json_envelope(self, cli_runner, plan_project, monkeypatch):
        """JSON output follows the standard roam envelope contract."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert_json_envelope(data, "plan")
        assert data["command"] == "plan"

    def test_plan_has_read_order(self, cli_runner, plan_project, monkeypatch):
        """JSON envelope contains a non-empty read_order list."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert "read_order" in data, f"Missing read_order in: {list(data.keys())}"
        assert isinstance(data["read_order"], list)
        # Should have at least the target file in read order
        assert len(data["read_order"]) >= 1

    def test_plan_has_invariants(self, cli_runner, plan_project, monkeypatch):
        """JSON envelope contains an invariants list."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert "invariants" in data, f"Missing invariants in: {list(data.keys())}"
        assert isinstance(data["invariants"], list)

    def test_plan_has_safe_points(self, cli_runner, plan_project, monkeypatch):
        """JSON envelope contains a safe_points list."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert "safe_points" in data, f"Missing safe_points in: {list(data.keys())}"
        assert isinstance(data["safe_points"], list)

    def test_plan_has_tests(self, cli_runner, plan_project, monkeypatch):
        """JSON envelope contains a tests section."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert "tests" in data, f"Missing tests in: {list(data.keys())}"
        tests = data["tests"]
        assert isinstance(tests, dict)
        assert "pytest_command" in tests
        assert "count" in tests

    def test_plan_has_post_change(self, cli_runner, plan_project, monkeypatch):
        """JSON envelope contains a post_change list with command/reason pairs."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        assert "post_change" in data, f"Missing post_change in: {list(data.keys())}"
        pc = data["post_change"]
        assert isinstance(pc, list)
        assert len(pc) >= 1
        for item in pc:
            assert "command" in item
            assert "reason" in item

    def test_plan_verdict_line(self, cli_runner, plan_project, monkeypatch):
        """Text output starts with VERDICT: line."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project)
        assert result.exit_code == 0
        assert result.output.startswith("VERDICT:"), f"Output did not start with VERDICT:\n{result.output[:200]}"

    def test_plan_verdict_in_json_summary(self, cli_runner, plan_project, monkeypatch):
        """JSON summary dict contains a verdict field."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        summary = data["summary"]
        assert "verdict" in summary, f"Missing verdict in summary: {summary}"
        assert "plan" in summary["verdict"].lower()


class TestPlanTasks:
    """Task-mode variations."""

    def test_plan_task_refactor(self, cli_runner, plan_project, monkeypatch):
        """--task refactor works and includes refactor in verdict."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["create_user", "--task", "refactor"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["summary"]["task"] == "refactor"
        assert "refactor" in data["summary"]["verdict"].lower()

    def test_plan_task_debug(self, cli_runner, plan_project, monkeypatch):
        """--task debug works and shows debug in text output."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["create_user", "--task", "debug"],
            cwd=plan_project,
        )
        assert result.exit_code == 0
        assert "debug" in result.output.lower()

    def test_plan_task_debug_json(self, cli_runner, plan_project, monkeypatch):
        """--task debug JSON output has task=debug in summary."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["create_user", "--task", "debug"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["summary"]["task"] == "debug"

    def test_plan_task_extend(self, cli_runner, plan_project, monkeypatch):
        """--task extend works."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["create_user", "--task", "extend"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["summary"]["task"] == "extend"

    def test_plan_task_understand(self, cli_runner, plan_project, monkeypatch):
        """--task understand produces post_change with understand-specific commands."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["create_user", "--task", "understand"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["summary"]["task"] == "understand"
        commands = [p["command"] for p in data["post_change"]]
        # understand task should suggest context and/or impact commands
        assert any("context" in c or "impact" in c or "trace" in c for c in commands)


class TestPlanTargets:
    """Target resolution: --file, --symbol, positional, error cases."""

    def test_plan_file_target(self, cli_runner, plan_project, monkeypatch):
        """--file option resolves a file path correctly."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["--file", "service.py"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["command"] == "plan"
        assert "read_order" in data

    def test_plan_symbol_option(self, cli_runner, plan_project, monkeypatch):
        """--symbol option resolves a symbol correctly."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["--symbol", "create_user"],
            cwd=plan_project,
            json_mode=True,
        )
        data = _parse_json(result, "plan")
        assert data["command"] == "plan"
        summary = data["summary"]
        assert summary.get("task") == "refactor"

    def test_plan_no_target_gives_error(self, cli_runner, plan_project, monkeypatch):
        """No target and no --staged produces a non-zero exit or error message."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, [], cwd=plan_project)
        # Should fail: either exit code != 0 or output contains an error message
        has_error_message = (
            "provide" in result.output.lower() or "error" in result.output.lower() or result.exit_code != 0
        )
        assert has_error_message, (
            f"Expected error for no-target, got exit={result.exit_code}, output={result.output[:200]}"
        )

    def test_plan_not_found_symbol(self, cli_runner, plan_project, monkeypatch):
        """Unknown symbol gives a graceful error message (no traceback)."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["nonexistent_symbol_xyz123"],
            cwd=plan_project,
        )
        # Should not crash with exception but report gracefully
        assert "Traceback" not in result.output
        assert (
            "not found" in result.output.lower() or "cannot plan" in result.output.lower() or result.exit_code != 0
        ), f"Expected graceful error, got: {result.output[:300]}"

    def test_plan_not_found_json(self, cli_runner, plan_project, monkeypatch):
        """Unknown symbol in JSON mode returns a JSON envelope with error field."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(
            cli_runner,
            ["nonexistent_symbol_xyz123"],
            cwd=plan_project,
            json_mode=True,
        )
        # May return exit 0 with error in JSON or exit 1
        if result.exit_code == 0 and result.output.strip().startswith("{"):
            data = json.loads(result.output)
            assert data["command"] == "plan"
            summary = data.get("summary", {})
            assert "error" in summary or "not found" in summary.get("verdict", "").lower()


class TestPlanSections:
    """Verify that text output contains the expected section headers."""

    def test_plan_text_has_all_sections(self, cli_runner, plan_project, monkeypatch):
        """Text output contains all 6 section headers."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project)
        assert result.exit_code == 0
        out = result.output
        assert "READ ORDER" in out
        assert "INVARIANTS" in out
        assert "SAFE MODIFICATION POINTS" in out
        assert "TOUCH CAREFULLY" in out
        assert "TEST SHORTLIST" in out
        assert "POST-CHANGE VERIFICATION" in out

    def test_plan_read_order_entries_have_file_info(self, cli_runner, plan_project, monkeypatch):
        """Each read_order entry in JSON has file, reason, rank fields."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        for entry in data["read_order"]:
            assert "file" in entry, f"read_order entry missing 'file': {entry}"
            assert "reason" in entry, f"read_order entry missing 'reason': {entry}"
            assert "rank" in entry, f"read_order entry missing 'rank': {entry}"

    def test_plan_safe_points_have_zero_incoming(self, cli_runner, plan_project, monkeypatch):
        """All safe_points in JSON have incoming_edges == 0."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        for sp in data["safe_points"]:
            assert sp["incoming_edges"] == 0, f"safe_point {sp['name']} has {sp['incoming_edges']} incoming edges"

    def test_plan_touch_carefully_high_fan_in(self, cli_runner, plan_project, monkeypatch):
        """All touch_carefully entries have incoming_edges >= 3."""
        monkeypatch.chdir(plan_project)
        result = invoke_plan(cli_runner, ["create_user"], cwd=plan_project, json_mode=True)
        data = _parse_json(result, "plan")
        for tc in data["touch_carefully"]:
            assert tc["incoming_edges"] >= 3, f"touch_carefully {tc['name']} has only {tc['incoming_edges']} callers"
