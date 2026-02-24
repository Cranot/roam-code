"""Tests for roam partition -- multi-agent partition manifest command."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, invoke_cli, parse_json_output, assert_json_envelope


# ---------------------------------------------------------------------------
# Shared fixture: a multi-module project with clear architectural layers
# ---------------------------------------------------------------------------

@pytest.fixture
def partition_project(project_factory):
    """A project with distinct modules suitable for partitioning."""
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
        "tests/test_auth.py": (
            "def test_authenticate(): pass\n"
            "def test_create_token(): pass\n"
        ),
    })


@pytest.fixture
def minimal_project(project_factory):
    """A tiny single-file project."""
    return project_factory({
        "app.py": "def main(): pass\n",
    })


# ---------------------------------------------------------------------------
# Unit tests for compute_partition_manifest
# ---------------------------------------------------------------------------


class TestPartitionManifest:
    """Tests for the compute_partition_manifest engine."""

    def test_manifest_returns_partitions(self, partition_project):
        """Result has a 'partitions' list."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                assert "partitions" in result
                assert isinstance(result["partitions"], list)
                assert len(result["partitions"]) == 3
        finally:
            os.chdir(old_cwd)

    def test_manifest_auto_agents(self, partition_project):
        """n_agents=None auto-detects from cluster count."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=None)
                assert result["total_partitions"] >= 2
                assert result["n_agents"] >= 2
        finally:
            os.chdir(old_cwd)

    def test_manifest_has_verdict(self, partition_project):
        """Manifest includes a verdict string."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                assert "verdict" in result
                assert "2 partitions" in result["verdict"]
                assert "conflict probability" in result["verdict"]
        finally:
            os.chdir(old_cwd)

    def test_manifest_conflict_probability_in_range(self, partition_project):
        """overall_conflict_probability is between 0 and 1."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                cp = result["overall_conflict_probability"]
                assert 0.0 <= cp <= 1.0, f"Conflict probability {cp} out of range"
        finally:
            os.chdir(old_cwd)

    def test_partition_has_required_fields(self, partition_project):
        """Each partition has all required fields."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        required = {
            "id", "label", "role", "files", "file_count", "symbol_count",
            "key_symbols", "complexity", "churn", "test_coverage",
            "conflict_risk", "cross_partition_edges", "cochange_score",
            "agent", "difficulty_score", "difficulty_label",
        }

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    missing = required - set(p.keys())
                    assert not missing, (
                        f"Partition {p.get('id')} missing fields: {missing}"
                    )
        finally:
            os.chdir(old_cwd)

    def test_partition_test_coverage_in_range(self, partition_project):
        """test_coverage is between 0.0 and 1.0."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    tc = p["test_coverage"]
                    assert 0.0 <= tc <= 1.0, (
                        f"Partition {p['id']} test_coverage {tc} out of range"
                    )
        finally:
            os.chdir(old_cwd)

    def test_partition_complexity_non_negative(self, partition_project):
        """complexity is non-negative."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    assert p["complexity"] >= 0, (
                        f"Partition {p['id']} has negative complexity"
                    )
        finally:
            os.chdir(old_cwd)

    def test_partition_conflict_risk_valid_label(self, partition_project):
        """conflict_risk is LOW, MEDIUM, or HIGH."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                for p in result["partitions"]:
                    assert p["conflict_risk"] in ("LOW", "MEDIUM", "HIGH"), (
                        f"Invalid conflict_risk: {p['conflict_risk']}"
                    )
        finally:
            os.chdir(old_cwd)

    def test_partition_key_symbols_have_pagerank(self, partition_project):
        """key_symbols entries have name, kind, pagerank, file."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                for p in result["partitions"]:
                    for s in p["key_symbols"]:
                        assert "name" in s
                        assert "kind" in s
                        assert "pagerank" in s
                        assert "file" in s
                        assert isinstance(s["pagerank"], float)
        finally:
            os.chdir(old_cwd)

    def test_dependencies_structure(self, partition_project):
        """dependencies list has proper structure."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                for dep in result["dependencies"]:
                    assert "from" in dep
                    assert "to" in dep
                    assert "edge_count" in dep
                    assert dep["edge_count"] > 0
                    assert dep["from"] != dep["to"]
        finally:
            os.chdir(old_cwd)

    def test_conflict_hotspots_structure(self, partition_project):
        """conflict_hotspots entries reference multiple partitions."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                for h in result["conflict_hotspots"]:
                    assert "file" in h
                    assert "partition_count" in h
                    assert h["partition_count"] >= 2
                    assert "partitions" in h
                    assert len(h["partitions"]) >= 2
        finally:
            os.chdir(old_cwd)

    def test_merge_order_valid(self, partition_project):
        """merge_order is a list of valid partition IDs."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                assert "merge_order" in result
                assert isinstance(result["merge_order"], list)
                for pid in result["merge_order"]:
                    assert 1 <= pid <= 3
        finally:
            os.chdir(old_cwd)

    def test_empty_graph(self, project_factory):
        """Empty project produces an empty manifest."""
        proj = project_factory({
            "empty.txt": "# nothing\n",
        })
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                assert result["total_partitions"] == 0
                assert result["partitions"] == []
        finally:
            os.chdir(old_cwd)

    def test_agents_assigned_to_all_partitions(self, partition_project):
        """Every partition has an agent assignment."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=3)
                for p in result["partitions"]:
                    assert "agent" in p
                    assert p["agent"].startswith("Worker-")
        finally:
            os.chdir(old_cwd)

    def test_minimal_project(self, minimal_project):
        """Single-file project is handled correctly."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest

        old_cwd = os.getcwd()
        try:
            os.chdir(str(minimal_project))
            with open_db(readonly=True) as conn:
                result = compute_partition_manifest(conn, n_agents=2)
                assert result["total_partitions"] == 2
                assert result["overall_conflict_probability"] >= 0.0
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# claude-teams format tests
# ---------------------------------------------------------------------------


class TestClaudeTeamsFormat:
    """Tests for claude-teams output format."""

    def test_claude_teams_structure(self, partition_project):
        """claude-teams format has agents and coordination."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest, _to_claude_teams

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                manifest = compute_partition_manifest(conn, n_agents=2)
                teams = _to_claude_teams(manifest)
                assert "agents" in teams
                assert "coordination" in teams
                assert len(teams["agents"]) == 2
        finally:
            os.chdir(old_cwd)

    def test_claude_teams_agent_has_scope(self, partition_project):
        """Each agent has scope with write_files and read_only_deps."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest, _to_claude_teams

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                manifest = compute_partition_manifest(conn, n_agents=2)
                teams = _to_claude_teams(manifest)
                for agent in teams["agents"]:
                    assert "scope" in agent
                    assert "write_files" in agent["scope"]
                    assert "read_only_deps" in agent["scope"]
                    assert "constraints" in agent
                    assert "role" in agent
        finally:
            os.chdir(old_cwd)

    def test_claude_teams_coordination_has_merge_order(self, partition_project):
        """Coordination section includes merge_order and hotspots."""
        from roam.db.connection import open_db
        from roam.commands.cmd_partition import compute_partition_manifest, _to_claude_teams

        old_cwd = os.getcwd()
        try:
            os.chdir(str(partition_project))
            with open_db(readonly=True) as conn:
                manifest = compute_partition_manifest(conn, n_agents=2)
                teams = _to_claude_teams(manifest)
                coord = teams["coordination"]
                assert "merge_order" in coord
                assert "conflict_hotspots" in coord
                assert "overall_conflict_probability" in coord
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Role suggestion tests
# ---------------------------------------------------------------------------


class TestRoleSuggestion:
    """Tests for the _suggest_role helper."""

    def test_api_role(self):
        from roam.commands.cmd_partition import _suggest_role
        from collections import Counter
        role = _suggest_role(["api/routes.py", "api/middleware.py"], Counter())
        assert role == "API Layer"

    def test_auth_role(self):
        from roam.commands.cmd_partition import _suggest_role
        from collections import Counter
        role = _suggest_role(["auth/login.py", "auth/tokens.py"], Counter())
        assert role == "Auth Layer"

    def test_test_role(self):
        from roam.commands.cmd_partition import _suggest_role
        from collections import Counter
        role = _suggest_role(["tests/test_foo.py"], Counter())
        assert role == "Test Layer"

    def test_language_fallback(self):
        from roam.commands.cmd_partition import _suggest_role
        from collections import Counter
        role = _suggest_role(["foo/bar.py"], Counter({"Python": 5}))
        assert role == "Python Module"

    def test_general_fallback(self):
        from roam.commands.cmd_partition import _suggest_role
        from collections import Counter
        role = _suggest_role([], Counter())
        assert role == "General Module"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestPartitionCommand:
    """Tests for the `roam partition` CLI command."""

    def test_cli_partition_runs(self, partition_project, cli_runner):
        """Command exits with code 0."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "3"], cwd=partition_project
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"

    def test_cli_partition_verdict_first(self, partition_project, cli_runner):
        """Text output starts with VERDICT."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2"], cwd=partition_project
        )
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_cli_partition_json(self, partition_project, cli_runner):
        """JSON output is a valid envelope with command='partition'."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "3"],
            cwd=partition_project, json_mode=True,
        )
        data = parse_json_output(result, command="partition")
        assert_json_envelope(data, command="partition")
        assert "partitions" in data
        assert "dependencies" in data
        assert "conflict_hotspots" in data
        assert "merge_order" in data

    def test_cli_partition_json_summary(self, partition_project, cli_runner):
        """JSON summary has required fields."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2"],
            cwd=partition_project, json_mode=True,
        )
        data = parse_json_output(result, command="partition")
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_partitions" in summary
        assert "overall_conflict_probability" in summary

    def test_cli_partition_auto_agents(self, partition_project, cli_runner):
        """Without --agents, auto-detects cluster count."""
        result = invoke_cli(
            cli_runner, ["partition"],
            cwd=partition_project, json_mode=True,
        )
        data = parse_json_output(result, command="partition")
        assert data["summary"]["total_partitions"] >= 2

    def test_cli_partition_format_json(self, partition_project, cli_runner):
        """--format json outputs valid JSON even without --json flag."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2", "--format", "json"],
            cwd=partition_project,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "partitions" in data

    def test_cli_partition_format_claude_teams(self, partition_project, cli_runner):
        """--format claude-teams outputs the teams structure."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2", "--format", "claude-teams"],
            cwd=partition_project,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "agents" in data
        assert "coordination" in data

    def test_cli_partition_format_claude_teams_json(self, partition_project, cli_runner):
        """--json --format claude-teams wraps in envelope."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2", "--format", "claude-teams"],
            cwd=partition_project, json_mode=True,
        )
        data = parse_json_output(result, command="partition")
        assert_json_envelope(data, command="partition")
        assert data.get("format") == "claude-teams"
        assert "agents" in data
        assert "coordination" in data

    def test_cli_partition_text_contains_partition_sections(self, partition_project, cli_runner):
        """Text output has PARTITION sections."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "2"], cwd=partition_project
        )
        assert result.exit_code == 0
        assert "PARTITION 1" in result.output
        assert "PARTITION 2" in result.output

    def test_cli_partition_text_contains_conflict_info(self, partition_project, cli_runner):
        """Text output shows conflict risk per partition."""
        result = invoke_cli(
            cli_runner, ["partition", "--agents", "3"], cwd=partition_project
        )
        assert result.exit_code == 0
        assert "Conflict risk:" in result.output

    def test_cli_partition_help(self, cli_runner):
        """--help works."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["partition", "--help"])
        assert result.exit_code == 0
        assert "--agents" in result.output
        assert "--format" in result.output
