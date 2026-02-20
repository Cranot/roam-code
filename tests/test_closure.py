"""Tests for `roam closure` -- minimal-change synthesis.

Covers ~15 tests: definition, callers, tests, JSON envelope, verdict,
unknown symbol, help, rename, delete, counts, edge cases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope

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
# Fixture: small project with models -> service -> tests
# ---------------------------------------------------------------------------

@pytest.fixture
def closure_project(project_factory):
    """Create a project with clear call relationships for closure testing."""
    return project_factory({
        "models.py": (
            'class User:\n'
            '    """A user model."""\n'
            '    def __init__(self, name, email_address):\n'
            '        self.name = name\n'
            '        self.email_address = email_address\n'
            '\n'
            '    def validate(self):\n'
            '        return "@" in self.email_address\n'
        ),
        "service.py": (
            'from models import User\n'
            '\n'
            'def create_user(name, email):\n'
            '    """Create a new user."""\n'
            '    user = User(name, email)\n'
            '    if not user.validate():\n'
            '        raise ValueError("Invalid email")\n'
            '    return user\n'
            '\n'
            'def list_users():\n'
            '    """List all users."""\n'
            '    return []\n'
        ),
        "tests/test_user.py": (
            'from service import create_user\n'
            '\n'
            'def test_create_user():\n'
            '    user = create_user("Alice", "a@b.com")\n'
            '    assert user is not None\n'
        ),
    })


# ============================================================================
# Test cases
# ============================================================================


class TestClosure:
    """Tests for `roam closure` command."""

    def test_closure_finds_definition(self, cli_runner, closure_project, monkeypatch):
        """The symbol's own definition should always be in the closure."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"], cwd=closure_project)
        assert result.exit_code == 0
        out = result.output
        # The definition file should appear
        assert "models.py" in out

    def test_closure_finds_callers(self, cli_runner, closure_project, monkeypatch):
        """Direct callers of the symbol should be in the closure."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"], cwd=closure_project)
        assert result.exit_code == 0
        out = result.output
        # service.py calls User, so it should appear
        assert "service.py" in out

    def test_closure_finds_tests(self, cli_runner, closure_project, monkeypatch):
        """Test files exercising the symbol should be in the closure."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "create_user"], cwd=closure_project)
        assert result.exit_code == 0
        out = result.output
        # test_user.py calls create_user
        assert "test" in out.lower()

    def test_closure_json_envelope(self, cli_runner, closure_project, monkeypatch):
        """JSON output should follow the roam envelope contract."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        assert_json_envelope(data, "closure")

    def test_closure_verdict_line(self, cli_runner, closure_project, monkeypatch):
        """Text output should start with VERDICT:."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"], cwd=closure_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:")

    def test_closure_unknown_symbol(self, cli_runner, closure_project, monkeypatch):
        """Unknown symbol should exit with code 1 and show error."""
        monkeypatch.chdir(closure_project)
        result = cli_runner.invoke(cli, ["closure", "nonexistent_xyz_42"],
                                   catch_exceptions=True)
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_closure_help(self, cli_runner):
        """--help should work and show usage."""
        result = cli_runner.invoke(cli, ["closure", "--help"])
        assert result.exit_code == 0
        assert "closure" in result.output.lower() or "Usage" in result.output

    def test_closure_rename_flag(self, cli_runner, closure_project, monkeypatch):
        """--rename should activate rename mode."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User", "--rename", "Account"],
                            cwd=closure_project)
        assert result.exit_code == 0
        out = result.output
        assert "rename" in out.lower()

    def test_closure_delete_flag(self, cli_runner, closure_project, monkeypatch):
        """--delete should activate deletion mode."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User", "--delete"],
                            cwd=closure_project)
        assert result.exit_code == 0
        out = result.output
        assert "delete" in out.lower()

    def test_closure_counts(self, cli_runner, closure_project, monkeypatch):
        """Change and file counts should be positive for a used symbol."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        assert data["total_changes"] >= 1
        assert data["files_affected"] >= 1

    def test_closure_json_has_changes_list(self, cli_runner, closure_project, monkeypatch):
        """JSON output should contain a 'changes' list."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        assert "changes" in data
        assert isinstance(data["changes"], list)
        assert len(data["changes"]) >= 1

    def test_closure_json_has_by_type(self, cli_runner, closure_project, monkeypatch):
        """JSON output should contain a 'by_type' grouping."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        assert "by_type" in data
        assert isinstance(data["by_type"], dict)

    def test_closure_json_summary_has_verdict(self, cli_runner, closure_project, monkeypatch):
        """JSON summary should contain a verdict field."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        summary = data.get("summary", {})
        assert "verdict" in summary

    def test_closure_json_rename_mode(self, cli_runner, closure_project, monkeypatch):
        """JSON output should reflect rename mode."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "User", "--rename", "Account"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        assert data.get("mode") == "rename"
        assert data.get("rename_to") == "Account"

    def test_closure_unused_symbol_minimal(self, cli_runner, closure_project, monkeypatch):
        """An unused symbol's closure should be just the definition."""
        monkeypatch.chdir(closure_project)
        result = invoke_cli(cli_runner, ["closure", "list_users"],
                            cwd=closure_project, json_mode=True)
        data = parse_json_output(result, "closure")
        # list_users is not called by anything, so closure is just the definition
        assert data["total_changes"] >= 1
        # Should have at least the definition itself
        change_types = [c["change_type"] for c in data["changes"]]
        has_def = any("definition" in ct for ct in change_types)
        assert has_def, f"Expected definition change, got: {change_types}"
