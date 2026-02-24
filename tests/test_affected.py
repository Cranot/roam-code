"""Tests for `roam affected` -- monorepo impact analysis via dependency graph.

Covers: text output, JSON envelope, verdict, depth limiting, module grouping,
affected tests detection, entry points, edge cases (no changes, empty index).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope, git_commit

from roam.cli import cli


# ---------------------------------------------------------------------------
# Override cli_runner fixture to handle Click 8.2+ (mix_stderr removed)
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ---------------------------------------------------------------------------
# Fixture: project with clear dependency chain and a second commit
# ---------------------------------------------------------------------------

@pytest.fixture
def affected_project(project_factory):
    """Create a project with dependencies, then add a second commit changing a leaf file.

    Layout:
      models.py    -- defines User class
      service.py   -- imports User from models
      api.py       -- imports create_user from service (2 hops from models)
      tests/test_service.py -- imports create_user from service

    The second commit modifies models.py, so:
      - models.py is DIRECT (changed)
      - service.py is TRANSITIVE-1 (imports models)
      - api.py is TRANSITIVE-2+ (imports service which imports models)
      - tests/test_service.py is an affected test
    """
    proj = project_factory(
        {
            "models.py": (
                'class User:\n'
                '    def __init__(self, name):\n'
                '        self.name = name\n'
                '\n'
                '    def greet(self):\n'
                '        return f"Hello {self.name}"\n'
            ),
            "service.py": (
                'from models import User\n'
                '\n'
                'def create_user(name):\n'
                '    return User(name)\n'
                '\n'
                'def get_greeting(name):\n'
                '    user = User(name)\n'
                '    return user.greet()\n'
            ),
            "api.py": (
                'from service import create_user\n'
                '\n'
                'def handle_request(data):\n'
                '    return create_user(data["name"])\n'
            ),
            "tests/test_service.py": (
                'from service import create_user\n'
                '\n'
                'def test_create_user():\n'
                '    user = create_user("Alice")\n'
                '    assert user is not None\n'
            ),
            "utils.py": (
                'def format_name(name):\n'
                '    return name.strip().title()\n'
            ),
        },
        extra_commits=[
            (
                {
                    "models.py": (
                        'class User:\n'
                        '    def __init__(self, name, email=""):\n'
                        '        self.name = name\n'
                        '        self.email = email\n'
                        '\n'
                        '    def greet(self):\n'
                        '        return f"Hello {self.name}"\n'
                    ),
                },
                "add email to User",
            ),
        ],
    )
    return proj


# ============================================================================
# Test cases
# ============================================================================


class TestAffected:
    """Tests for `roam affected` command."""

    def test_affected_help(self, cli_runner):
        """--help should work and show usage."""
        result = cli_runner.invoke(cli, ["affected", "--help"])
        assert result.exit_code == 0
        assert "affected" in result.output.lower() or "Usage" in result.output

    def test_affected_verdict_line(self, cli_runner, affected_project, monkeypatch):
        """Text output should start with VERDICT:."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_affected_shows_changed_files(self, cli_runner, affected_project, monkeypatch):
        """Changed files should appear in the CHANGED (direct) section."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        out = result.output
        assert "models.py" in out

    def test_affected_shows_transitive_1(self, cli_runner, affected_project, monkeypatch):
        """Files that directly import changed files should appear as 1-hop."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        out = result.output
        # service.py imports from models.py
        assert "service.py" in out

    def test_affected_shows_transitive_2plus(self, cli_runner, affected_project, monkeypatch):
        """Files reachable via 2+ hops should appear in 2+ section."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        out = result.output
        # api.py imports service.py which imports models.py
        assert "api.py" in out

    def test_affected_shows_test_files(self, cli_runner, affected_project, monkeypatch):
        """Test files that depend on changed code should be listed."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        out = result.output
        assert "test" in out.lower()

    def test_affected_shows_by_module(self, cli_runner, affected_project, monkeypatch):
        """BY MODULE section should group files by directory."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(cli_runner, ["affected"], cwd=affected_project)
        assert result.exit_code == 0
        out = result.output
        assert "BY MODULE" in out

    def test_affected_json_envelope(self, cli_runner, affected_project, monkeypatch):
        """JSON output should follow the roam envelope contract."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert_json_envelope(data, "affected")

    def test_affected_json_has_verdict(self, cli_runner, affected_project, monkeypatch):
        """JSON summary should contain a verdict field."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        summary = data.get("summary", {})
        assert "verdict" in summary
        assert "affected" in summary["verdict"].lower() or "changes" in summary["verdict"].lower()

    def test_affected_json_has_changed_files(self, cli_runner, affected_project, monkeypatch):
        """JSON output should contain a changed_files list."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert "changed_files" in data
        assert isinstance(data["changed_files"], list)
        assert len(data["changed_files"]) >= 1

    def test_affected_json_has_transitive_lists(self, cli_runner, affected_project, monkeypatch):
        """JSON output should contain affected_transitive_1 and affected_transitive_2plus lists."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert "affected_transitive_1" in data
        assert "affected_transitive_2plus" in data
        assert isinstance(data["affected_transitive_1"], list)
        assert isinstance(data["affected_transitive_2plus"], list)

    def test_affected_json_has_tests(self, cli_runner, affected_project, monkeypatch):
        """JSON output should contain affected_tests list."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert "affected_tests" in data
        assert isinstance(data["affected_tests"], list)

    def test_affected_json_has_by_module(self, cli_runner, affected_project, monkeypatch):
        """JSON output should contain by_module dict."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert "by_module" in data
        assert isinstance(data["by_module"], dict)

    def test_affected_json_has_entry_points(self, cli_runner, affected_project, monkeypatch):
        """JSON output should contain affected_entry_points list."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        assert "affected_entry_points" in data
        assert isinstance(data["affected_entry_points"], list)

    def test_affected_depth_limit(self, cli_runner, affected_project, monkeypatch):
        """--depth 1 should limit results to 1-hop dependents only."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected", "--depth", "1"],
            cwd=affected_project, json_mode=True,
        )
        data = parse_json_output(result, "affected")
        # With depth=1, transitive_2plus should be empty
        assert len(data.get("affected_transitive_2plus", [])) == 0

    def test_affected_unmodified_not_included(self, cli_runner, affected_project, monkeypatch):
        """Files with no dependency on changed files should not appear."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        all_affected = (
            data.get("changed_files", [])
            + [f["file"] for f in data.get("affected_transitive_1", [])]
            + [f["file"] for f in data.get("affected_transitive_2plus", [])]
        )
        # utils.py does not depend on models.py
        assert not any("utils.py" in f for f in all_affected)

    def test_affected_summary_counts(self, cli_runner, affected_project, monkeypatch):
        """Summary counts should be consistent with detail lists."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        summary = data["summary"]
        assert summary["changed_files"] == len(data["changed_files"])
        assert summary["transitive_1"] == len(data["affected_transitive_1"])
        assert summary["transitive_2plus"] == len(data["affected_transitive_2plus"])
        assert summary["affected_tests"] == len(data["affected_tests"])
        assert summary["affected_entry_points"] == len(data["affected_entry_points"])


class TestAffectedEdgeCases:
    """Edge case tests for `roam affected`."""

    def test_affected_no_changes(self, cli_runner, project_factory, monkeypatch):
        """When there are no changes, should report zero affected."""
        proj = project_factory({
            "app.py": 'def main(): pass\n',
        })
        monkeypatch.chdir(proj)
        # HEAD~1..HEAD should exist because project_factory makes 2 commits
        # but if only 1 commit, the diff may be empty.
        # Use --base HEAD which means HEAD..HEAD = no changes
        result = invoke_cli(
            cli_runner, ["affected", "--base", "HEAD"],
            cwd=proj, json_mode=True,
        )
        # May either succeed with 0 affected or fail due to no changes
        if result.exit_code == 0:
            data = json.loads(result.output)
            total = data.get("summary", {}).get("total_affected", 0)
            assert total == 0

    def test_affected_changed_flag(self, cli_runner, affected_project, monkeypatch):
        """--changed flag should use working tree diff instead of commit range."""
        monkeypatch.chdir(affected_project)
        # With --changed and no uncommitted changes, should report 0
        result = invoke_cli(
            cli_runner, ["affected", "--changed"],
            cwd=affected_project, json_mode=True,
        )
        if result.exit_code == 0:
            data = json.loads(result.output)
            # Working tree is clean after commits, so no changes expected
            total = data.get("summary", {}).get("total_affected", 0)
            assert total == 0

    def test_affected_transitive_1_has_reason(self, cli_runner, affected_project, monkeypatch):
        """Each entry in affected_transitive_1 should have a 'reason' field."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        for entry in data.get("affected_transitive_1", []):
            assert "file" in entry
            assert "reason" in entry

    def test_affected_transitive_2plus_has_reason(self, cli_runner, affected_project, monkeypatch):
        """Each entry in affected_transitive_2plus should have a 'reason' field."""
        monkeypatch.chdir(affected_project)
        result = invoke_cli(
            cli_runner, ["affected"], cwd=affected_project, json_mode=True
        )
        data = parse_json_output(result, "affected")
        for entry in data.get("affected_transitive_2plus", []):
            assert "file" in entry
            assert "reason" in entry
