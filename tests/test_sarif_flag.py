"""Tests for the --sarif global CLI flag.

Validates that `roam --sarif <command>` produces valid SARIF 2.1.0 JSON
for dead, health, complexity, and rules commands.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli

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
    assert result.exit_code == 0, f"Command failed (exit {result.exit_code}):\n{result.output}"
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid SARIF JSON: {e}\nOutput was:\n{result.output[:500]}")


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
    return project_factory(
        {
            "src/models.py": (
                "class User:\n"
                '    """A user model."""\n'
                "    def __init__(self, name, email):\n"
                "        self.name = name\n"
                "        self.email = email\n"
                "\n"
                "    def display_name(self):\n"
                "        return self.name.title()\n"
            ),
            "src/service.py": (
                "from models import User\n"
                "\n"
                "def create_user(name, email):\n"
                "    user = User(name, email)\n"
                "    return user\n"
                "\n"
                "def unused_helper():\n"
                '    """This function is never called."""\n'
                "    return 42\n"
            ),
            "src/utils.py": (
                'def format_name(first, last):\n    return f"{first} {last}"\n\nUNUSED_CONSTANT = "never_referenced"\n'
            ),
        }
    )


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
        proj = project_factory(
            {
                "main.py": ("from helper import do_work\n\ndef main():\n    do_work()\n\nmain()\n"),
                "helper.py": ("def do_work():\n    return 1\n"),
            }
        )
        runner = CliRunner()
        result = invoke_sarif(runner, ["rules"], cwd=proj)
        data = parse_sarif(result)
        assert_valid_sarif(data)
        run = data["runs"][0]
        assert len(run["results"]) == 0


class TestSarifSeverityMapping:
    """W531: ``severity: error`` taint rules MUST emit SARIF level=error.

    Before W531 ``_LEVEL_MAP`` had no entry for ``"ERROR"`` so every taint
    finding (every shipped SQLi / SSTI / deserialization rule ships
    ``severity: error``) silently downgraded to SARIF ``"note"``. GitHub
    Code Scanning + Defender + every CI gate keyed off ``level=error`` was
    broken. The fix is the closed mapping in ``_LEVEL_MAP``; this test
    locks it.
    """

    def test_to_level_maps_error_to_sarif_error(self):
        from roam.output.sarif import _to_level

        # Lowercase and uppercase both resolve to the SARIF "error" level.
        assert _to_level("error") == "error"
        assert _to_level("ERROR") == "error"

    def test_to_level_full_severity_table(self):
        from roam.output.sarif import _to_level

        # Closed mapping — every shipped severity tier resolves correctly.
        assert _to_level("CRITICAL") == "error"
        assert _to_level("critical") == "error"
        assert _to_level("ERROR") == "error"
        assert _to_level("error") == "error"
        assert _to_level("HIGH") == "warning"
        assert _to_level("WARNING") == "warning"
        assert _to_level("warning") == "warning"
        assert _to_level("MEDIUM") == "note"
        assert _to_level("LOW") == "note"
        assert _to_level("INFO") == "note"
        # Unknown labels default to "note" — never accidentally gates CI.
        assert _to_level("UNKNOWN") == "note"

    def test_taint_severity_error_emits_sarif_level_error(self):
        """A taint finding produced from a ``severity: error`` rule must
        emit a SARIF result with ``level: "error"`` AND the rule's
        ``defaultConfiguration.level`` must also be ``"error"``."""
        from roam.output.sarif import taint_to_sarif

        findings = [
            {
                "rule_id": "java-sqli",
                "severity": "error",
                "cwe": "CWE-89",
                "owasp_top10": "A03:2021_Injection",
                "source": {"name": "getParameter", "file": "S.java", "line": 1},
                "sink": {"name": "executeQuery", "file": "D.java", "line": 9},
                "path_length": 2,
                "path": [],
                "sanitizer_in_path": False,
                "vex_justification": None,
            }
        ]
        doc = taint_to_sarif(findings)
        result = doc["runs"][0]["results"][0]
        assert result["level"] == "error", (
            "severity:error MUST surface as SARIF level=error so CI gates "
            "keyed off level=error fire; pre-W531 it downgraded to note."
        )
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["defaultConfiguration"]["level"] == "error"

    def test_taint_severity_warning_emits_sarif_level_warning(self):
        """``severity: warning`` resolves to SARIF ``level: "warning"``."""
        from roam.output.sarif import taint_to_sarif

        findings = [
            {
                "rule_id": "js-ssrf",
                "severity": "warning",
                "cwe": "CWE-918",
                "owasp_top10": "",
                "source": {"name": "req.query", "file": "x.js", "line": 1},
                "sink": {"name": "fetch", "file": "x.js", "line": 5},
                "path_length": 2,
                "path": [],
                "sanitizer_in_path": False,
                "vex_justification": None,
            }
        ]
        doc = taint_to_sarif(findings)
        assert doc["runs"][0]["results"][0]["level"] == "warning"
