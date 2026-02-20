"""Tests for roam budget command."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    git_commit,
    index_in_process,
    roam,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def budget_project(tmp_path, monkeypatch):
    """Project with snapshot baseline for budget testing."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()

    (src / "models.py").write_text(
        'class User:\n'
        '    def __init__(self, name):\n'
        '        self.name = name\n'
        '\n'
        '    def display_name(self):\n'
        '        return self.name.title()\n'
    )

    (src / "service.py").write_text(
        'from models import User\n'
        '\n'
        'def create_user(name):\n'
        '    return User(name)\n'
    )

    git_init(proj)

    # Index and snapshot
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"

    from roam.cli import cli
    from click.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot", "--tag", "baseline"],
                           catch_exceptions=False)
    assert result.exit_code == 0, f"snapshot failed: {result.output}"

    return proj


@pytest.fixture
def budget_no_snapshot(tmp_path, monkeypatch):
    """Indexed project WITHOUT any snapshot."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text(
        'def main():\n'
        '    print("hello")\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"

    # Remove any auto-created snapshots
    from roam.db.connection import open_db
    with open_db() as conn:
        conn.execute("DELETE FROM snapshots")
        conn.commit()

    return proj


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestBudget:
    """Test the budget CLI command."""

    def test_budget_runs(self, cli_runner, budget_project, monkeypatch):
        """Command exits 0 (or 1 if budgets exceeded)."""
        monkeypatch.chdir(budget_project)
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project)
        # May be 0 (all pass) or 1 (some fail); should not crash
        assert result.exit_code in (0, 1)

    def test_budget_json_envelope(self, cli_runner, budget_project, monkeypatch):
        """Valid JSON envelope with command='budget'."""
        monkeypatch.chdir(budget_project)
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project,
                            json_mode=True)
        data = json.loads(result.output)
        assert_json_envelope(data, "budget")

    def test_budget_init_creates_file(self, cli_runner, budget_project, monkeypatch):
        """--init creates .roam/budget.yaml."""
        monkeypatch.chdir(budget_project)
        config_path = budget_project / ".roam" / "budget.yaml"
        if config_path.exists():
            config_path.unlink()

        result = invoke_cli(cli_runner, ["budget", "--init"], cwd=budget_project)
        assert result.exit_code == 0
        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert "budgets:" in content
        assert "health_score" in content

    def test_budget_init_no_overwrite(self, cli_runner, budget_project, monkeypatch):
        """--init on existing file warns."""
        monkeypatch.chdir(budget_project)
        config_path = budget_project / ".roam" / "budget.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("existing content\n")

        result = invoke_cli(cli_runner, ["budget", "--init"], cwd=budget_project)
        assert result.exit_code == 0
        assert "already exists" in result.output.lower()
        # Content should NOT have been overwritten
        assert config_path.read_text() == "existing content\n"

    def test_budget_all_pass(self, cli_runner, budget_project, monkeypatch):
        """Within budget -> all PASS, exit 0."""
        monkeypatch.chdir(budget_project)
        # With no changes from baseline, all should pass
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project)
        # Should pass since snapshot == current
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_budget_no_snapshot_skip(self, cli_runner, budget_no_snapshot, monkeypatch):
        """Without snapshot -> all SKIP."""
        monkeypatch.chdir(budget_no_snapshot)
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_no_snapshot,
                            json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["skipped"] == data["summary"]["rules_checked"]
        assert data["has_before_snapshot"] is False

    def test_budget_explain_flag(self, cli_runner, budget_project, monkeypatch):
        """--explain shows reason text."""
        monkeypatch.chdir(budget_project)

        # Create config with reason field
        config_path = budget_project / ".roam" / "budget.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            'version: "1"\n'
            'budgets:\n'
            '  - name: "Health floor"\n'
            '    metric: health_score\n'
            '    max_decrease: 5\n'
            '    reason: "Keep health above baseline"\n',
            encoding="utf-8",
        )

        result = invoke_cli(cli_runner, ["budget", "--explain"],
                            cwd=budget_project)
        assert result.exit_code == 0
        assert "reason:" in result.output.lower() or "Keep health" in result.output

    def test_budget_default_budgets(self, cli_runner, budget_project, monkeypatch):
        """Without config -> uses defaults."""
        monkeypatch.chdir(budget_project)

        # Remove any config file
        for ext in ("yaml", "yml"):
            cfg = budget_project / ".roam" / f"budget.{ext}"
            if cfg.exists():
                cfg.unlink()

        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project,
                            json_mode=True)
        data = json.loads(result.output)
        # Default has 6 rules
        assert data["summary"]["rules_checked"] == 6
        rule_names = [r["name"] for r in data["rules"]]
        assert "Health score floor" in rule_names
        assert "No new cycles" in rule_names

    def test_budget_verdict_line(self, cli_runner, budget_project, monkeypatch):
        """Text starts with 'VERDICT:'."""
        monkeypatch.chdir(budget_project)
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project)
        assert result.output.strip().startswith("VERDICT:")

    def test_budget_json_has_rules_array(self, cli_runner, budget_project, monkeypatch):
        """JSON output has rules array with expected fields."""
        monkeypatch.chdir(budget_project)
        result = invoke_cli(cli_runner, ["budget"], cwd=budget_project,
                            json_mode=True)
        data = json.loads(result.output)
        assert "rules" in data
        assert isinstance(data["rules"], list)
        if data["rules"]:
            rule = data["rules"][0]
            assert "name" in rule
            assert "metric" in rule
            assert "status" in rule

    def test_budget_evaluate_max_increase(self):
        """max_increase: FAIL if delta exceeds threshold."""
        from roam.commands.cmd_budget import _evaluate_rule

        rule = {"name": "No new cycles", "metric": "cycles", "max_increase": 0}
        before = {"cycles": 3}
        after = {"cycles": 5}
        result = _evaluate_rule(rule, before, after)
        assert result["status"] == "FAIL"
        assert result["delta"] == 2

    def test_budget_evaluate_max_decrease(self):
        """max_decrease: FAIL if decrease exceeds threshold."""
        from roam.commands.cmd_budget import _evaluate_rule

        rule = {"name": "Health floor", "metric": "health_score", "max_decrease": 5}
        before = {"health_score": 80}
        after = {"health_score": 70}
        result = _evaluate_rule(rule, before, after)
        assert result["status"] == "FAIL"

    def test_budget_evaluate_max_increase_pct(self):
        """max_increase_pct: FAIL if percentage exceeds threshold."""
        from roam.commands.cmd_budget import _evaluate_rule

        rule = {"name": "Complexity", "metric": "avg_complexity",
                "max_increase_pct": 10}
        before = {"avg_complexity": 10.0}
        after = {"avg_complexity": 12.0}
        result = _evaluate_rule(rule, before, after)
        assert result["status"] == "FAIL"
        assert result["delta"] == 2.0
