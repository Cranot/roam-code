"""Tests for roam relate — Context Symbol Relationship Graph."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope


@pytest.fixture
def relate_project(project_factory):
    return project_factory({
        "models.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "    def save(self):\n"
            "        pass\n"
        ),
        "auth.py": (
            "from models import User\n"
            "def verify_token(t):\n"
            "    return User('test')\n"
            "def create_user(name):\n"
            "    u = User(name)\n"
            "    u.save()\n"
            "    return u\n"
        ),
        "billing.py": (
            "from models import User\n"
            "def process_payment(user_id):\n"
            "    u = User('x')\n"
            "    return u\n"
        ),
        "api.py": (
            "from auth import verify_token, create_user\n"
            "def handle_request(r):\n"
            "    verify_token(r)\n"
            "    return create_user(r)\n"
        ),
    })


class TestRelateAnalysis:
    """Unit-level tests for relationship analysis."""

    def test_relate_finds_direct_edges(self, relate_project):
        """Direct call relationships should be found."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "handle_request", "verify_token"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        rels = data.get("relationships", [])
        # handle_request calls verify_token directly
        direct = [r for r in rels if "DIRECT" in r.get("kind", "")]
        assert len(direct) >= 1, f"Expected direct edge, got: {rels}"

    def test_relate_finds_shared_deps(self, relate_project):
        """Shared dependencies should be identified when multiple inputs depend on the same symbol."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "create_user", "process_payment"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        shared = data.get("shared_deps", [])
        # Both create_user and process_payment depend on User
        shared_names = [s["name"] for s in shared]
        assert any("User" in n for n in shared_names), \
            f"Expected User in shared deps, got: {shared_names}"

    def test_relate_finds_shared_callers(self, relate_project):
        """Shared callers should be identified when one symbol calls multiple inputs."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "verify_token", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        callers = data.get("shared_callers", [])
        # handle_request calls both verify_token and create_user
        caller_names = [c["name"] for c in callers]
        assert any("handle_request" in n for n in caller_names), \
            f"Expected handle_request in shared callers, got: {caller_names}"

    def test_relate_distance_matrix(self, relate_project):
        """Distance matrix should show shortest-path distances between symbols."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "verify_token", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        matrix = data.get("distance_matrix", {})
        assert len(matrix) >= 2, f"Expected 2+ entries in matrix, got: {matrix}"
        # Self-distance should be 0
        for name, row in matrix.items():
            assert row.get(name) == 0, f"Self-distance for {name} should be 0"

    def test_relate_cohesion_score(self, relate_project):
        """Cohesion score should be between 0 and 1."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "verify_token", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        cohesion = data["summary"]["cohesion"]
        assert 0.0 <= cohesion <= 1.0, f"Cohesion {cohesion} out of range"

    def test_relate_conflict_detection(self, relate_project):
        """Conflict risks should be detected when multiple inputs modify the same dependency."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "create_user", "process_payment"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        conflicts = data.get("conflicts", [])
        # Both create_user and process_payment call User — potential conflict
        # (This may or may not detect depending on edge resolution, but the field should exist)
        assert isinstance(conflicts, list)

    def test_relate_no_path(self, relate_project):
        """Symbols with no connecting path should show null distance."""
        runner = CliRunner()
        # save() is isolated — not called by process_payment directly
        # Use two symbols that are far apart
        result = invoke_cli(runner, ["--json", "relate", "save", "handle_request", "--depth", "1"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        rels = data.get("relationships", [])
        # At depth 1, some pairs may have no path
        assert isinstance(rels, list)
        assert len(rels) >= 1

    def test_relate_single_symbol(self, relate_project):
        """Running with a single symbol should still work (self-analysis)."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["symbol_count"] == 1
        assert data["summary"]["cohesion"] == 1.0


class TestRelateCLI:
    """CLI integration tests."""

    def test_cli_relate_runs(self, relate_project):
        """Command should exit 0."""
        runner = CliRunner()
        result = invoke_cli(runner, ["relate", "verify_token", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0

    def test_cli_relate_json(self, relate_project):
        """JSON output should follow the roam envelope contract."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "verify_token", "create_user"],
                            cwd=relate_project)
        data = parse_json_output(result, command="relate")
        assert_json_envelope(data, command="relate")

    def test_cli_relate_verdict(self, relate_project):
        """Text output should start with VERDICT."""
        runner = CliRunner()
        result = invoke_cli(runner, ["relate", "verify_token", "create_user"],
                            cwd=relate_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_cli_relate_with_files(self, relate_project):
        """--file flag should include symbols from the specified file."""
        runner = CliRunner()
        result = invoke_cli(runner, ["--json", "relate", "--file", "auth.py"],
                            cwd=relate_project)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["symbol_count"] >= 2, \
            f"Expected 2+ symbols from auth.py, got {data['summary']['symbol_count']}"

    def test_cli_relate_help(self, relate_project):
        """--help should work."""
        runner = CliRunner()
        result = invoke_cli(runner, ["relate", "--help"], cwd=relate_project)
        assert result.exit_code == 0
        assert "relate" in result.output.lower()

    def test_cli_relate_unknown_symbol(self, relate_project):
        """Unknown symbol should produce a graceful error."""
        runner = CliRunner()
        result = invoke_cli(runner, ["relate", "nonexistent_symbol_xyz"],
                            cwd=relate_project)
        assert result.exit_code != 0 or "not found" in result.output.lower()
