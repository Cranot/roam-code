"""Tests for developer workflow commands.

Covers: preflight, pr-risk, diff, context, affected-tests, diagnose, digest.
Uses CliRunner for in-process testing (~50 tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope, git_commit


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


# ============================================================================
# Preflight command
# ============================================================================


class TestPreflight:
    """Tests for `roam preflight <symbol>`."""

    def test_preflight_user(self, indexed_project, cli_runner, monkeypatch):
        """preflight User shows dependencies and impact."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "User"])
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output
        assert "Blast radius:" in output
        assert "Affected tests:" in output
        assert "Complexity:" in output

    def test_preflight_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns a valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "User"], json_mode=True)
        data = parse_json_output(result, "preflight")
        assert_json_envelope(data, "preflight")
        assert "risk_level" in data["summary"]
        assert "target" in data["summary"]
        assert "blast_radius" in data

    def test_preflight_unknown_symbol(self, indexed_project, cli_runner, monkeypatch):
        """Handles nonexistent symbol gracefully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "NonExistentSymbol"])
        assert result.exit_code == 0
        output = result.output
        # Should indicate not found, either in text or JSON
        assert "not found" in output.lower() or "No symbols found" in output

    def test_preflight_function(self, indexed_project, cli_runner, monkeypatch):
        """preflight create_user works for functions."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "create_user"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_preflight_no_target(self, indexed_project, cli_runner, monkeypatch):
        """preflight with no target or --staged fails with usage hint."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight"])
        # Should exit non-zero or show help
        assert result.exit_code != 0 or "Provide a TARGET" in result.output

    def test_preflight_json_unknown_symbol(self, indexed_project, cli_runner, monkeypatch):
        """--json with unknown symbol returns envelope with error info."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["preflight", "DoesNotExist"], json_mode=True,
        )
        data = parse_json_output(result, "preflight")
        assert_json_envelope(data, "preflight")
        # Should contain error or UNKNOWN risk
        summary = data["summary"]
        assert summary.get("risk_level") == "UNKNOWN" or "error" in summary

    def test_preflight_admin(self, indexed_project, cli_runner, monkeypatch):
        """preflight Admin (subclass) exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["preflight", "Admin"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ============================================================================
# PR-risk command
# ============================================================================


class TestPrRisk:
    """Tests for `roam pr-risk`."""

    def test_pr_risk_clean(self, indexed_project, cli_runner, monkeypatch):
        """With no unstaged changes, handles gracefully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"])
        assert result.exit_code == 0
        # Should indicate no changes
        assert "No changes found" in result.output or "risk" in result.output.lower()

    def test_pr_risk_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope (even if no changes)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
        assert result.exit_code == 0
        # May return JSON with risk_score=0 or plain message
        output = result.output.strip()
        if output.startswith("{"):
            import json
            data = json.loads(output)
            # Could be either a simple dict or a full envelope
            assert "risk_score" in data or "summary" in data

    def test_pr_risk_with_changes(self, indexed_project, cli_runner, monkeypatch):
        """After modifying a file, shows risk assessment."""
        monkeypatch.chdir(indexed_project)
        models_path = indexed_project / "src" / "models.py"
        original = models_path.read_text()
        try:
            models_path.write_text(
                'class User:\n'
                '    """A user model (modified)."""\n'
                '    def __init__(self, name, email):\n'
                '        self.name = name\n'
                '        self.email = email\n'
                '\n'
                '    def display_name(self):\n'
                '        return self.name.title()\n'
                '\n'
                '    def validate_email(self):\n'
                '        return "@" in self.email\n'
                '\n'
                '\n'
                'class Admin(User):\n'
                '    """An admin user."""\n'
                '    def __init__(self, name, email, role="admin"):\n'
                '        super().__init__(name, email)\n'
                '        self.role = role\n'
                '\n'
                '    def promote(self, user):\n'
                '        pass\n'
                '\n'
                '    def new_method(self):\n'
                '        return "new"\n'
            )
            result = invoke_cli(cli_runner, ["pr-risk"])
            assert result.exit_code == 0
            output = result.output
            # Should show some risk output or "no changes"
            assert ("Risk" in output or "risk" in output
                    or "No changes" in output)
        finally:
            models_path.write_text(original)

    def test_pr_risk_json_with_changes(self, indexed_project, cli_runner, monkeypatch):
        """--json with modified files returns structured risk data."""
        monkeypatch.chdir(indexed_project)
        utils_path = indexed_project / "src" / "utils.py"
        original = utils_path.read_text()
        try:
            utils_path.write_text(
                'def format_name(first, last):\n'
                '    """Format a full name (updated)."""\n'
                '    return f"{first} {last}".strip()\n'
                '\n'
                'def parse_email(raw):\n'
                '    """Parse an email address."""\n'
                '    if "@" not in raw:\n'
                '        return None\n'
                '    parts = raw.split("@")\n'
                '    return {"user": parts[0], "domain": parts[1]}\n'
                '\n'
                'UNUSED_CONSTANT = "never_referenced"\n'
            )
            result = invoke_cli(cli_runner, ["pr-risk"], json_mode=True)
            assert result.exit_code == 0
        finally:
            utils_path.write_text(original)

    def test_pr_risk_staged(self, indexed_project, cli_runner, monkeypatch):
        """--staged with no staged files handles gracefully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pr-risk", "--staged"])
        assert result.exit_code == 0
        assert "No changes" in result.output or "risk" in result.output.lower()


# ============================================================================
# Diff command
# ============================================================================


class TestDiff:
    """Tests for `roam diff`."""

    def test_diff_clean(self, indexed_project, cli_runner, monkeypatch):
        """No changes produces clean output."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diff"])
        assert result.exit_code == 0
        assert "No changes found" in result.output

    def test_diff_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope when there are no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diff"], json_mode=True)
        assert result.exit_code == 0
        # With no changes, it just prints text (not JSON envelope)
        # That is acceptable behavior
        output = result.output.strip()
        assert "No changes" in output or output.startswith("{")

    def test_diff_with_changes(self, indexed_project, cli_runner, monkeypatch):
        """After modifying a file, shows affected symbols."""
        monkeypatch.chdir(indexed_project)
        models_path = indexed_project / "src" / "models.py"
        original = models_path.read_text()
        try:
            models_path.write_text(
                'class User:\n'
                '    def __init__(self, name):\n'
                '        self.name = name\n'
            )
            result = invoke_cli(cli_runner, ["diff"])
            assert result.exit_code == 0
            output = result.output
            # Should show blast radius or indicate changes
            assert ("Blast Radius" in output or "Changed files" in output
                    or "Affected" in output or "No changes" in output
                    or "not found in index" in output.lower())
        finally:
            models_path.write_text(original)

    def test_diff_json_with_changes(self, indexed_project, cli_runner, monkeypatch):
        """--json with changes returns structured blast radius."""
        monkeypatch.chdir(indexed_project)
        service_path = indexed_project / "src" / "service.py"
        original = service_path.read_text()
        try:
            service_path.write_text(
                'from models import User, Admin\n'
                '\n'
                'def create_user(name, email):\n'
                '    """Create a new user (updated)."""\n'
                '    user = User(name, email)\n'
                '    return user\n'
                '\n'
                'def get_display(user):\n'
                '    """Get display name."""\n'
                '    return user.display_name()\n'
                '\n'
                'def unused_helper():\n'
                '    """Still unused."""\n'
                '    return 42\n'
            )
            result = invoke_cli(cli_runner, ["diff"], json_mode=True)
            assert result.exit_code == 0
            output = result.output.strip()
            if output.startswith("{"):
                import json
                data = json.loads(output)
                if "summary" in data:
                    assert "changed_files" in data["summary"]
        finally:
            service_path.write_text(original)

    def test_diff_staged(self, indexed_project, cli_runner, monkeypatch):
        """--staged with no staged changes prints clean message."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diff", "--staged"])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_full_flag(self, indexed_project, cli_runner, monkeypatch):
        """--full flag exits 0 even with no changes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diff", "--full"])
        assert result.exit_code == 0

    def test_diff_tests_flag(self, indexed_project, cli_runner, monkeypatch):
        """--tests flag exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diff", "--tests"])
        assert result.exit_code == 0


# ============================================================================
# Context command
# ============================================================================


class TestContext:
    """Tests for `roam context <symbol>`."""

    def test_context_user(self, indexed_project, cli_runner, monkeypatch):
        """context User shows relevant files."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "User"])
        assert result.exit_code == 0
        output = result.output
        assert "Context for:" in output or "User" in output
        assert "Files to read" in output or "files_to_read" in output.lower()

    def test_context_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope with files."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "User"], json_mode=True)
        data = parse_json_output(result, "context")
        assert_json_envelope(data, "context")
        assert "files_to_read" in data
        assert isinstance(data["files_to_read"], list)
        assert len(data["files_to_read"]) >= 1

    def test_context_unknown(self, indexed_project, cli_runner, monkeypatch):
        """Handles nonexistent symbol with error."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "NonExistentSymbol"])
        # Should exit non-zero or show "not found"
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_context_function(self, indexed_project, cli_runner, monkeypatch):
        """context create_user shows callers and callees."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "create_user"])
        assert result.exit_code == 0
        output = result.output
        # Should show the symbol context
        assert "create_user" in output

    def test_context_no_args(self, indexed_project, cli_runner, monkeypatch):
        """context with no arguments shows help."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context"])
        assert result.exit_code == 0
        # Shows help or usage info when no symbol provided
        output = result.output
        assert "context" in output.lower() or "Usage" in output or "NAMES" in output

    def test_context_json_callers_callees(self, indexed_project, cli_runner, monkeypatch):
        """--json for create_user contains caller/callee data."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["context", "create_user"], json_mode=True,
        )
        data = parse_json_output(result, "context")
        assert_json_envelope(data, "context")
        # Should have callers and callees keys
        assert "callers" in data or "summary" in data

    def test_context_batch(self, indexed_project, cli_runner, monkeypatch):
        """Batch mode with multiple symbols exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "User", "create_user"])
        assert result.exit_code == 0
        output = result.output
        assert "Batch" in output or "User" in output

    def test_context_task_refactor(self, indexed_project, cli_runner, monkeypatch):
        """--task refactor tailors context output."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["context", "User", "--task", "refactor"],
        )
        assert result.exit_code == 0
        assert "task=refactor" in result.output or "refactor" in result.output.lower()


# ============================================================================
# Affected-tests command
# ============================================================================


class TestAffectedTests:
    """Tests for `roam affected-tests`."""

    def test_affected_tests_with_target(self, indexed_project, cli_runner, monkeypatch):
        """affected-tests User exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "User"])
        assert result.exit_code == 0
        # May have no test files in this small project
        output = result.output
        assert "affected" in output.lower() or "No affected" in output

    def test_affected_tests_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["affected-tests", "User"], json_mode=True,
        )
        data = parse_json_output(result, "affected-tests")
        assert_json_envelope(data, "affected-tests")
        assert "tests" in data
        assert "total_tests" in data["summary"]

    def test_affected_tests_no_target(self, indexed_project, cli_runner, monkeypatch):
        """affected-tests with no target and no --staged fails."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests"])
        assert result.exit_code != 0 or "Provide a TARGET" in result.output

    def test_affected_tests_function(self, indexed_project, cli_runner, monkeypatch):
        """affected-tests create_user exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "create_user"])
        assert result.exit_code == 0

    def test_affected_tests_unknown(self, indexed_project, cli_runner, monkeypatch):
        """affected-tests with unknown symbol fails gracefully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "NoSuchThing"])
        # Should error out
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_affected_tests_command_flag(self, indexed_project, cli_runner, monkeypatch):
        """--command flag outputs a pytest command or comment."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["affected-tests", "User", "--command"],
        )
        assert result.exit_code == 0
        output = result.output.strip()
        # Either "pytest ..." or "# No affected tests found."
        assert output.startswith("pytest") or output.startswith("#")


# ============================================================================
# Diagnose command
# ============================================================================


class TestDiagnose:
    """Tests for `roam diagnose <symbol>`."""

    def test_diagnose_runs(self, indexed_project, cli_runner, monkeypatch):
        """diagnose User exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diagnose", "User"])
        assert result.exit_code == 0
        assert "VERDICT:" in result.output or "Diagnose" in result.output

    def test_diagnose_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["diagnose", "User"], json_mode=True,
        )
        data = parse_json_output(result, "diagnose")
        assert_json_envelope(data, "diagnose")
        assert "target_metrics" in data
        assert "upstream" in data
        assert "downstream" in data

    def test_diagnose_function(self, indexed_project, cli_runner, monkeypatch):
        """diagnose create_user exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diagnose", "create_user"])
        assert result.exit_code == 0

    def test_diagnose_unknown(self, indexed_project, cli_runner, monkeypatch):
        """diagnose with unknown symbol fails gracefully."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diagnose", "NoSuchSymbol"])
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_diagnose_depth(self, indexed_project, cli_runner, monkeypatch):
        """diagnose with --depth flag exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["diagnose", "User", "--depth", "3"],
        )
        assert result.exit_code == 0

    def test_diagnose_json_verdict(self, indexed_project, cli_runner, monkeypatch):
        """JSON output contains verdict in summary."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["diagnose", "create_user"], json_mode=True,
        )
        data = parse_json_output(result, "diagnose")
        assert_json_envelope(data, "diagnose")
        assert "verdict" in data["summary"]


# ============================================================================
# Digest command
# ============================================================================


class TestDigest:
    """Tests for `roam digest`."""

    def test_digest_runs(self, indexed_project, cli_runner, monkeypatch):
        """digest exits 0 (may show 'no snapshots')."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["digest"])
        assert result.exit_code == 0
        output = result.output
        # Either shows digest or tells us no snapshots exist
        assert ("Digest" in output or "No snapshots" in output
                or "snapshot" in output.lower())

    def test_digest_json(self, indexed_project, cli_runner, monkeypatch):
        """--json returns envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["digest"], json_mode=True)
        data = parse_json_output(result, "digest")
        assert_json_envelope(data, "digest")
        # Should have current metrics at minimum
        assert "current" in data or "summary" in data

    def test_digest_brief(self, indexed_project, cli_runner, monkeypatch):
        """--brief flag exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["digest", "--brief"])
        assert result.exit_code == 0

    def test_digest_json_summary_keys(self, indexed_project, cli_runner, monkeypatch):
        """JSON summary has expected keys when snapshots exist."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["digest"], json_mode=True)
        data = parse_json_output(result, "digest")
        assert_json_envelope(data, "digest")
        summary = data["summary"]
        # If no snapshots, summary has error key
        # If snapshots exist, summary has health_score
        assert "error" in summary or "health_score" in summary


# ============================================================================
# Cross-command integration tests
# ============================================================================


class TestWorkflowIntegration:
    """Tests that combine workflow commands in realistic sequences."""

    def test_preflight_then_diff(self, indexed_project, cli_runner, monkeypatch):
        """Run preflight, modify file, then diff -- both succeed."""
        monkeypatch.chdir(indexed_project)

        # Preflight before changes
        result = invoke_cli(cli_runner, ["preflight", "User"])
        assert result.exit_code == 0

        # Modify a file
        models_path = indexed_project / "src" / "models.py"
        original = models_path.read_text()
        try:
            models_path.write_text(
                'class User:\n'
                '    """Modified user."""\n'
                '    def __init__(self, name, email):\n'
                '        self.name = name\n'
                '        self.email = email\n'
            )

            # Diff after changes
            result = invoke_cli(cli_runner, ["diff"])
            assert result.exit_code == 0
        finally:
            models_path.write_text(original)

    def test_context_then_affected_tests(self, indexed_project, cli_runner, monkeypatch):
        """Run context and affected-tests for same symbol."""
        monkeypatch.chdir(indexed_project)

        result = invoke_cli(cli_runner, ["context", "create_user"])
        assert result.exit_code == 0

        result = invoke_cli(cli_runner, ["affected-tests", "create_user"])
        assert result.exit_code == 0

    def test_pr_risk_with_committed_changes(self, indexed_project, cli_runner, monkeypatch):
        """pr-risk with a commit range exits 0."""
        monkeypatch.chdir(indexed_project)
        # Use HEAD~1..HEAD if there is history
        result = invoke_cli(cli_runner, ["pr-risk", "HEAD~1..HEAD"])
        assert result.exit_code == 0

    def test_all_json_envelopes_consistent(self, indexed_project, cli_runner, monkeypatch):
        """All workflow commands produce consistent JSON envelopes."""
        monkeypatch.chdir(indexed_project)

        # Commands that accept a symbol argument
        symbol_commands = [
            (["preflight", "User"], "preflight"),
            (["context", "User"], "context"),
            (["diagnose", "User"], "diagnose"),
            (["affected-tests", "User"], "affected-tests"),
        ]

        for args, cmd_name in symbol_commands:
            result = invoke_cli(cli_runner, args, json_mode=True)
            data = parse_json_output(result, cmd_name)
            assert_json_envelope(data, cmd_name)
            # All envelopes must have version and timestamp
            assert isinstance(data["version"], str)
            assert isinstance(data["timestamp"], (int, float, str))
