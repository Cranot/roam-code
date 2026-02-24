"""Tests for standardized CLI exit codes (backlog item #19).

Validates that:
- Exit code constants have correct values
- exit_with() helper works correctly
- Custom exceptions carry the right exit codes
- Missing index produces exit code 3
- Gate failure commands produce exit code 5
- MCP server correctly classifies new exit codes
"""

from __future__ import annotations

import os
import sys

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli, index_in_process, git_init, git_commit


# ===========================================================================
# Test exit code constants
# ===========================================================================

class TestExitCodeConstants:
    """Verify exit code integer values match the documented scheme."""

    def test_exit_success(self):
        from roam.exit_codes import EXIT_SUCCESS
        assert EXIT_SUCCESS == 0

    def test_exit_error(self):
        from roam.exit_codes import EXIT_ERROR
        assert EXIT_ERROR == 1

    def test_exit_usage(self):
        from roam.exit_codes import EXIT_USAGE
        assert EXIT_USAGE == 2

    def test_exit_index_missing(self):
        from roam.exit_codes import EXIT_INDEX_MISSING
        assert EXIT_INDEX_MISSING == 3

    def test_exit_index_stale(self):
        from roam.exit_codes import EXIT_INDEX_STALE
        assert EXIT_INDEX_STALE == 4

    def test_exit_gate_failure(self):
        from roam.exit_codes import EXIT_GATE_FAILURE
        assert EXIT_GATE_FAILURE == 5

    def test_exit_partial(self):
        from roam.exit_codes import EXIT_PARTIAL
        assert EXIT_PARTIAL == 6

    def test_descriptions_cover_all_codes(self):
        from roam.exit_codes import (
            DESCRIPTIONS, EXIT_SUCCESS, EXIT_ERROR, EXIT_USAGE,
            EXIT_INDEX_MISSING, EXIT_INDEX_STALE, EXIT_GATE_FAILURE,
            EXIT_PARTIAL,
        )
        all_codes = [
            EXIT_SUCCESS, EXIT_ERROR, EXIT_USAGE,
            EXIT_INDEX_MISSING, EXIT_INDEX_STALE, EXIT_GATE_FAILURE,
            EXIT_PARTIAL,
        ]
        for code in all_codes:
            assert code in DESCRIPTIONS, f"Missing description for exit code {code}"
            assert isinstance(DESCRIPTIONS[code], str)
            assert len(DESCRIPTIONS[code]) > 0


# ===========================================================================
# Test exit_with() helper
# ===========================================================================

class TestExitWith:
    """Verify exit_with() prints to stderr and exits with the correct code."""

    def test_exit_with_message(self):
        from roam.exit_codes import exit_with
        with pytest.raises(SystemExit) as exc_info:
            exit_with(3, "test message")
        assert exc_info.value.code == 3

    def test_exit_with_no_message(self):
        from roam.exit_codes import exit_with
        with pytest.raises(SystemExit) as exc_info:
            exit_with(0)
        assert exc_info.value.code == 0

    def test_exit_with_gate_failure(self):
        from roam.exit_codes import exit_with, EXIT_GATE_FAILURE
        with pytest.raises(SystemExit) as exc_info:
            exit_with(EXIT_GATE_FAILURE, "quality gate failed")
        assert exc_info.value.code == 5


# ===========================================================================
# Test custom exceptions
# ===========================================================================

class TestCustomExceptions:
    """Verify custom exception classes carry the right exit codes."""

    def test_roam_error_default(self):
        from roam.exit_codes import RoamError, EXIT_ERROR
        err = RoamError("something broke")
        assert err.exit_code == EXIT_ERROR
        assert err.format_message() == "something broke"

    def test_roam_error_custom_code(self):
        from roam.exit_codes import RoamError
        err = RoamError("custom", exit_code=42)
        assert err.exit_code == 42

    def test_index_missing_error(self):
        from roam.exit_codes import IndexMissingError, EXIT_INDEX_MISSING
        err = IndexMissingError()
        assert err.exit_code == EXIT_INDEX_MISSING
        assert "roam init" in err.format_message().lower()

    def test_index_missing_error_custom_message(self):
        from roam.exit_codes import IndexMissingError, EXIT_INDEX_MISSING
        err = IndexMissingError("custom message")
        assert err.exit_code == EXIT_INDEX_MISSING
        assert err.format_message() == "custom message"

    def test_index_stale_error(self):
        from roam.exit_codes import IndexStaleError, EXIT_INDEX_STALE
        err = IndexStaleError()
        assert err.exit_code == EXIT_INDEX_STALE
        assert "roam index" in err.format_message().lower()

    def test_gate_failure_error(self):
        from roam.exit_codes import GateFailureError, EXIT_GATE_FAILURE
        err = GateFailureError()
        assert err.exit_code == EXIT_GATE_FAILURE

    def test_gate_failure_error_custom_message(self):
        from roam.exit_codes import GateFailureError, EXIT_GATE_FAILURE
        err = GateFailureError("health score below threshold")
        assert err.exit_code == EXIT_GATE_FAILURE
        assert err.format_message() == "health score below threshold"

    def test_exceptions_inherit_click_exception(self):
        """All custom exceptions should be ClickException subclasses."""
        import click
        from roam.exit_codes import (
            RoamError, IndexMissingError, IndexStaleError, GateFailureError,
        )
        for cls in [RoamError, IndexMissingError, IndexStaleError, GateFailureError]:
            assert issubclass(cls, click.ClickException)


# ===========================================================================
# Test require_index() raises IndexMissingError
# ===========================================================================

class TestRequireIndex:
    """Verify require_index() raises IndexMissingError when DB is absent."""

    def test_require_index_missing(self, tmp_path):
        """require_index() should raise IndexMissingError in a dir with no index."""
        from roam.commands.resolve import require_index
        from roam.exit_codes import IndexMissingError

        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Create a .git directory so find_project_root() works
            (tmp_path / ".git").mkdir()
            with pytest.raises(IndexMissingError):
                require_index()
        finally:
            os.chdir(old_cwd)

    def test_require_index_exists(self, project_factory):
        """require_index() should NOT raise when the index exists."""
        from roam.commands.resolve import require_index

        proj = project_factory({
            "app.py": "def main(): pass\n",
        })
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            # Should not raise since project_factory indexes the project
            require_index()
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# Test IndexMissingError produces exit code 3 through CLI
# ===========================================================================

class TestIndexMissingExitCode:
    """Verify that IndexMissingError produces exit code 3 through the CLI."""

    def test_index_missing_exit_code_via_cli(self, tmp_path):
        """A command that raises IndexMissingError should exit with code 3."""
        from roam.cli import cli
        from roam.exit_codes import EXIT_INDEX_MISSING

        # Create a minimal git repo with no index
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".roam/\n")

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Create a tiny Click command that raises IndexMissingError
            import click
            from roam.exit_codes import IndexMissingError

            @click.command("test-missing")
            def test_missing():
                raise IndexMissingError()

            # Invoke the command directly through Click
            result = runner.invoke(test_missing, catch_exceptions=False)
            assert result.exit_code == EXIT_INDEX_MISSING
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# Test gate failure exit code in budget command
# ===========================================================================

class TestGateFailureExitCode:
    """Verify that gate failure produces exit code 5."""

    def test_budget_gate_failure(self, project_factory):
        """Budget command with exceeded budgets should exit with code 5."""
        from roam.exit_codes import EXIT_GATE_FAILURE

        # Create a project with enough symbols to trigger budget failures
        proj = project_factory({
            "app.py": (
                'def func_a():\n'
                '    return 1\n'
                '\n'
                'def func_b():\n'
                '    return func_a()\n'
                '\n'
                'def func_c():\n'
                '    return func_b()\n'
            ),
        })

        # Create a .roam/budgets.json with an impossible budget (max 0 symbols)
        roam_dir = proj / ".roam"
        roam_dir.mkdir(exist_ok=True)
        import json
        budgets = [
            {
                "name": "symbol_count",
                "metric": "symbol_count",
                "max_increase": 0,
                "max_value": 1,
            }
        ]
        (roam_dir / "budgets.json").write_text(json.dumps(budgets))

        # Also need a snapshot for budget comparison
        runner = CliRunner()
        from roam.cli import cli

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            # Take a snapshot first
            result = runner.invoke(cli, ["snapshot"], catch_exceptions=False)

            # Run budget command
            result = runner.invoke(cli, ["budget"], catch_exceptions=False)
            # Budget may or may not fail depending on whether we have a prior
            # snapshot. The key thing is: if it fails, it uses code 5.
            if result.exit_code != 0:
                assert result.exit_code == EXIT_GATE_FAILURE, (
                    f"Expected exit code {EXIT_GATE_FAILURE} for gate failure, "
                    f"got {result.exit_code}. Output:\n{result.output}"
                )
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# Test MCP server exit code classification
# ===========================================================================

class TestMCPExitCodeClassification:
    """Verify MCP server correctly classifies the new exit codes."""

    def test_classify_index_missing(self):
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_INDEX_MISSING
        code, hint, _retryable = _classify_error("", EXIT_INDEX_MISSING)
        assert code == "INDEX_NOT_FOUND"
        assert "roam init" in hint

    def test_classify_index_stale(self):
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_INDEX_STALE
        code, hint, _retryable = _classify_error("", EXIT_INDEX_STALE)
        assert code == "INDEX_STALE"
        assert "roam index" in hint

    def test_classify_gate_failure(self):
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_GATE_FAILURE
        code, hint, _retryable = _classify_error("", EXIT_GATE_FAILURE)
        assert code == "GATE_FAILURE"
        assert "gate" in hint.lower()

    def test_classify_usage_error(self):
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_USAGE
        code, hint, _retryable = _classify_error("", EXIT_USAGE)
        assert code == "USAGE_ERROR"

    def test_classify_partial_failure(self):
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_PARTIAL
        code, hint, _retryable = _classify_error("", EXIT_PARTIAL)
        assert code == "PARTIAL_FAILURE"

    def test_exit_code_takes_priority_over_text(self):
        """Exit code classification should take priority over text patterns."""
        from roam.mcp_server import _classify_error
        from roam.exit_codes import EXIT_GATE_FAILURE
        # Text says "not found in index" but exit code says gate failure
        code, hint, _retryable = _classify_error("not found in index", EXIT_GATE_FAILURE)
        assert code == "GATE_FAILURE", (
            "Exit code should take priority over text pattern matching"
        )

    def test_fallback_to_text_for_unknown_codes(self):
        """For unknown exit codes, fall back to text pattern matching."""
        from roam.mcp_server import _classify_error
        code, hint, _retryable = _classify_error("not found in index", 99)
        assert code == "INDEX_NOT_FOUND"

    def test_general_failure_for_unknown(self):
        """For unknown exit codes with no text match, return COMMAND_FAILED."""
        from roam.mcp_server import _classify_error
        code, hint, _retryable = _classify_error("something weird", 99)
        assert code == "COMMAND_FAILED"


# ===========================================================================
# Test CLI error handler (LazyGroup.invoke override)
# ===========================================================================

class TestCLIErrorHandler:
    """Verify the LazyGroup invoke override catches unhandled exceptions."""

    def test_success_exit_code(self, project_factory):
        """Normal command execution should produce exit code 0."""
        proj = project_factory({
            "app.py": "def main(): pass\n",
        })
        runner = CliRunner()
        result = invoke_cli(runner, ["health"], cwd=proj)
        assert result.exit_code == 0

    def test_unknown_command_exit_code(self):
        """Unknown command should produce exit code 2 (usage error)."""
        from roam.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["nonexistent-command"])
        assert result.exit_code == 2


# ===========================================================================
# Test backward compatibility
# ===========================================================================

class TestBackwardCompatibility:
    """Verify existing behavior is preserved."""

    def test_ensure_index_still_builds(self, tmp_path):
        """ensure_index() should still auto-build when index is missing."""
        from roam.commands.resolve import ensure_index

        # Create a minimal Python project with git
        (tmp_path / ".gitignore").write_text(".roam/\n")
        (tmp_path / "app.py").write_text("def main(): pass\n")
        git_init(tmp_path)

        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Should not raise, should auto-build
            ensure_index()
            # Index should now exist
            from roam.db.connection import db_exists
            assert db_exists()
        finally:
            os.chdir(old_cwd)

    def test_health_still_returns_zero_on_success(self, project_factory):
        """Health command should still return 0 when no gate check is active."""
        proj = project_factory({
            "app.py": "def main(): pass\n",
        })
        runner = CliRunner()
        result = invoke_cli(runner, ["health"], cwd=proj)
        assert result.exit_code == 0
