"""Tests for the --sarif global CLI flag.

Validates that `roam --sarif <command>` produces valid SARIF 2.1.0 JSON
for dead, health, complexity, and rules commands.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli, index_in_process, git_init, git_commit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def invoke_sarif(runner, args, cwd):
    """Invoke a roam CLI command with --sarif flag."""
    from roam.cli import cli

    full_args = ["--sarif"] + args
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def parse_sarif(result):
    """Parse SARIF JSON from a CliRunner result."""
    assert result.exit_code == 0, (
        f"Command failed (exit {result.exit_code}):\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid SARIF JSON: {e}\nOutput was:\n{result.output[:500]}"
        )


def assert_valid_sarif(data):
    """Assert basic SARIF 2.1.0 structure."""
    assert "$schema" in data, "Missing $schema in SARIF output"
    assert data["version"] == "2.1.0", f"Expected version 2.1.0, got {data['version']}"
    assert "runs" in data, "Missing runs array in SARIF output"
    assert isinstance(data["runs"], list), "runs must be an array"
    assert len(data["runs"]) >= 1, "runs must have at least one entry"
    run = data["runs"][0]
    assert "tool" in run, "Missing tool in run"
    assert "driver" in run["tool"], "Missing driver in tool"
    assert "name" in run["tool"]["driver"], "Missing name in driver"
    assert "results" in run, "Missing results in run"
    assert isinstance(run["results"], list), "results must be an array"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def indexed_proj(project_factory):
    """Create a small indexed Python project for SARIF tests."""
    return project_factory({
        "src/models.py": (
            'class User:\n'
            '    """A user model."""\n'
            '    def __init__(self, name, email):\n'
            '        self.name = name\n'
            '        self.email = email\n'
            '\n'
            '    def display_name(self):\n'
            '        return self.name.title()\n'
        ),
        "src/service.py": (
            'from models import User\n'
            '\n'
            'def create_user(name, email):\n'
            '    user = User(name, email)\n'
            '    return user\n'
            '\n'
            'def unused_helper():\n'
            '    """This function is never called."""\n'
            '    return 42\n'
        ),
        "src/utils.py": (
            'def format_name(first, last):\n'
            '    return f"{first} {last}"\n'
            '\n'
            'UNUSED_CONSTANT = "never_referenced"\n'
        ),
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSarifFlagDead:
    """Test --sarif flag with the dead command."""

    def test_sarif_flag_dead(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        # Should find some dead code results
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagHealth:
    """Test --sarif flag with the health command."""

    def test_sarif_flag_health(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["health"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagComplexity:
    """Test --sarif flag with the complexity command."""

    def test_sarif_flag_complexity(self, indexed_proj):
        runner = CliRunner()
        # Use threshold 0 to ensure all symbols are included
        result = invoke_sarif(runner, ["complexity", "--threshold", "0"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"


class TestSarifFlagRules:
    """Test --sarif flag with the rules command."""

    def test_sarif_flag_rules(self, indexed_proj):
        runner = CliRunner()
        # Rules without a .roam/rules directory should still produce valid SARIF
        result = invoke_sarif(runner, ["rules"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "roam-code"
        # No rules means 0 results
        assert len(run["results"]) == 0


class TestSarifStructure:
    """Test SARIF output structural validity."""

    def test_sarif_output_has_schema(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert "$schema" in data
        assert "sarif" in data["$schema"].lower()

    def test_sarif_output_has_runs(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        data = parse_sarif(result)
        assert "runs" in data
        assert isinstance(data["runs"], list)
        assert len(data["runs"]) == 1

    def test_sarif_output_has_tool(self, indexed_proj):
        runner = CliRunner()
        result = invoke_sarif(runner, ["health"], cwd=indexed_proj)
        data = parse_sarif(result)
        run = data["runs"][0]
        assert "tool" in run
        driver = run["tool"]["driver"]
        assert driver["name"] == "roam-code"
        assert "version" in driver
        assert "rules" in driver


class TestSarifHelp:
    """Test --sarif is a recognized CLI flag."""

    def test_sarif_flag_recognized(self):
        """Verify --sarif is accepted as a global flag (no 'no such option' error)."""
        from roam.cli import cli
        runner = CliRunner()
        # Just pass --sarif without a subcommand; should show usage, not an error
        result = runner.invoke(cli, ["--sarif", "--help"])
        assert result.exit_code == 0
        # The custom format_help does not list options, so instead verify
        # that the flag is a known parameter on the cli group.
        param_names = [p.name for p in cli.params]
        assert "sarif_mode" in param_names


class TestSarifAndJsonIndependent:
    """Test --sarif and --json work independently."""

    def test_sarif_and_json_exclusive(self, indexed_proj):
        runner = CliRunner()
        # SARIF produces SARIF format
        sarif_result = invoke_sarif(runner, ["dead"], cwd=indexed_proj)
        sarif_data = parse_sarif(sarif_result)
        assert "$schema" in sarif_data
        assert "version" in sarif_data
        assert sarif_data["version"] == "2.1.0"

        # JSON produces JSON envelope format
        json_result = invoke_cli(runner, ["dead"], cwd=indexed_proj, json_mode=True)
        json_data = json.loads(json_result.output)
        assert "command" in json_data
        assert json_data["command"] == "dead"

        # They are different formats
        assert "$schema" not in json_data
        assert "command" not in sarif_data


class TestSarifNoFindings:
    """Test SARIF with no findings produces valid empty output."""

    def test_sarif_no_findings(self, project_factory):
        # Create a project where everything is used (no dead code)
        proj = project_factory({
            "main.py": (
                'from helper import do_work\n'
                '\n'
                'def main():\n'
                '    do_work()\n'
                '\n'
                'main()\n'
            ),
            "helper.py": (
                'def do_work():\n'
                '    return 1\n'
            ),
        })
        runner = CliRunner()
        result = invoke_sarif(runner, ["rules"], cwd=proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert len(run["results"]) == 0
