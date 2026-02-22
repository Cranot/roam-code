"""Tests for the MCP server module.

Tests cover:
- _classify_error() error pattern matching
- _ensure_fresh_index() with mocked subprocess
- _run_roam() structured error responses
- _tool() decorator lite-mode filtering
- mcp_cmd CLI command
- Tool wrapper argument construction
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# _classify_error tests
# ---------------------------------------------------------------------------


class TestClassifyError:
    """Test error classification returns correct codes and hints."""

    def _classify(self, stderr, exit_code=1):
        from roam.mcp_server import _classify_error
        return _classify_error(stderr, exit_code)

    def test_index_not_found_no_roam(self):
        code, hint = self._classify("Error: No .roam directory found")
        assert code == "INDEX_NOT_FOUND"
        assert "roam init" in hint

    def test_index_not_found_in_index(self):
        code, hint = self._classify("symbol 'foo' not found in index")
        assert code == "INDEX_NOT_FOUND"

    def test_index_not_found_db(self):
        code, hint = self._classify("cannot open index.db")
        assert code == "INDEX_NOT_FOUND"

    def test_index_stale(self):
        code, hint = self._classify("warning: index is stale, run roam index")
        assert code == "INDEX_STALE"
        assert "roam index" in hint

    def test_not_git_repo(self):
        code, hint = self._classify("fatal: not a git repository")
        assert code == "NOT_GIT_REPO"
        assert "git init" in hint

    def test_db_locked(self):
        code, hint = self._classify("sqlite3.OperationalError: database is locked")
        assert code == "DB_LOCKED"

    def test_permission_denied(self):
        code, hint = self._classify("OSError: Permission denied: '/foo/bar'")
        assert code == "PERMISSION_DENIED"

    def test_no_results_symbol(self):
        code, hint = self._classify("symbol not found: 'bazqux'")
        assert code == "NO_RESULTS"
        assert "search term" in hint

    def test_no_matches(self):
        code, hint = self._classify("no matches for pattern 'xyz'")
        assert code == "NO_RESULTS"

    def test_generic_failure(self):
        code, hint = self._classify("something went wrong", exit_code=1)
        assert code == "COMMAND_FAILED"

    def test_unknown_success(self):
        code, hint = self._classify("", exit_code=0)
        assert code == "UNKNOWN"

    def test_patterns_ordered_specific_first(self):
        # "not found in index" should match INDEX_NOT_FOUND, not a more generic pattern
        code, _ = self._classify("Error: symbol 'x' not found in index database")
        assert code == "INDEX_NOT_FOUND"

    def test_permission_on_index_gets_permission_denied(self):
        # "permission denied" is more specific than generic index errors
        code, _ = self._classify("index.db: Permission denied")
        assert code == "PERMISSION_DENIED"

    def test_case_insensitive(self):
        code, _ = self._classify("PERMISSION DENIED for path /etc/shadow")
        assert code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# _ensure_fresh_index tests
# ---------------------------------------------------------------------------


class TestEnsureFreshIndex:
    """Test index freshness checking."""

    def test_success(self):
        from roam.mcp_server import _ensure_fresh_index
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"summary": {"files": 10}}
            result = _ensure_fresh_index(".")
            assert result is None
            mock.assert_called_once_with(["index"], ".")

    def test_failure(self):
        from roam.mcp_server import _ensure_fresh_index
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"error": "permission denied"}
            result = _ensure_fresh_index(".")
            assert result is not None
            assert "error" in result
            assert "permission denied" in result["error"]


# ---------------------------------------------------------------------------
# _run_roam tests
# ---------------------------------------------------------------------------


class TestRunRoam:
    """Test the roam CLI subprocess wrapper."""

    def test_success(self):
        from roam.mcp_server import _run_roam
        payload = {"summary": {"health_score": 85}}
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
            result = _run_roam(["health"], ".")
            assert result == payload

    def test_failure_with_structured_error(self):
        from roam.mcp_server import _run_roam
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: No .roam directory found",
            )
            result = _run_roam(["health"], ".")
            assert "error" in result
            assert result["error_code"] == "INDEX_NOT_FOUND"
            assert "hint" in result
            assert result["exit_code"] == 1
            assert "command" in result

    def test_timeout(self):
        from roam.mcp_server import _run_roam
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="roam", timeout=60)):
            result = _run_roam(["health"], ".")
            assert "error" in result
            assert "timed out" in result["error"]

    def test_json_decode_error(self):
        from roam.mcp_server import _run_roam
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout="not json {{{",
                stderr="",
            )
            result = _run_roam(["health"], ".")
            assert "error" in result
            assert "JSON" in result["error"]


# ---------------------------------------------------------------------------
# _tool decorator tests
# ---------------------------------------------------------------------------


class TestToolDecorator:
    """Test the MCP tool registration decorator."""

    def test_lite_mode_filters_non_core(self):
        """Non-core tools should be plain functions in lite mode."""
        import roam.mcp_server as mod
        # In lite mode (default), non-core tool functions are not registered
        # They should still be callable as regular functions
        assert callable(mod.visualize)

    def test_core_tools_set_has_expected_members(self):
        """Core tools set should contain the documented tools."""
        from roam.mcp_server import _CORE_TOOLS
        expected = {
            "roam_understand", "roam_search_symbol", "roam_context",
            "roam_file_info", "roam_deps", "roam_preflight", "roam_diff",
            "roam_pr_risk", "roam_affected_tests", "roam_impact",
            "roam_uses", "roam_health", "roam_dead_code",
            "roam_complexity_report", "roam_diagnose", "roam_trace",
        }
        assert _CORE_TOOLS == expected

    def test_core_tools_count(self):
        from roam.mcp_server import _CORE_TOOLS
        assert len(_CORE_TOOLS) == 16


# ---------------------------------------------------------------------------
# mcp_cmd CLI tests
# ---------------------------------------------------------------------------


class TestMcpCmd:
    """Test the roam mcp CLI command."""

    def test_help(self):
        from roam.mcp_server import mcp_cmd
        runner = CliRunner()
        result = runner.invoke(mcp_cmd, ["--help"])
        assert result.exit_code == 0
        assert "roam mcp" in result.output
        assert "--transport" in result.output
        assert "--no-auto-index" in result.output

    def test_missing_fastmcp(self):
        """When fastmcp isn't installed, should fail with clear message."""
        from roam.mcp_server import mcp_cmd
        runner = CliRunner()
        with patch("roam.mcp_server.mcp", None):
            result = runner.invoke(mcp_cmd, ["--no-auto-index"])
            assert result.exit_code == 1
            assert "roam-code[mcp]" in result.output

    def test_list_tools_flag(self):
        """--list-tools should print registered tools without starting server."""
        from roam.mcp_server import mcp_cmd
        runner = CliRunner()
        # Even without fastmcp, --list-tools should fail gracefully
        # (it checks mcp is None first)
        with patch("roam.mcp_server.mcp", None):
            result = runner.invoke(mcp_cmd, ["--list-tools"])
            assert result.exit_code == 1  # mcp is None check fires first


# ---------------------------------------------------------------------------
# Tool wrapper argument construction tests
# ---------------------------------------------------------------------------


class TestToolWrappers:
    """Test that tool wrappers construct correct CLI arguments."""

    def _check_args(self, fn, kwargs, expected_args):
        """Call a tool function with mocked _run_roam and verify args."""
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            fn(**kwargs)
            mock.assert_called_once()
            actual_args = mock.call_args[0][0]
            assert actual_args == expected_args

    def test_roam_diff_default(self):
        from roam.mcp_server import roam_diff
        self._check_args(roam_diff, {}, ["diff"])

    def test_roam_diff_with_range(self):
        from roam.mcp_server import roam_diff
        self._check_args(
            roam_diff,
            {"commit_range": "HEAD~3..HEAD", "staged": False},
            ["diff", "HEAD~3..HEAD"],
        )

    def test_roam_diff_staged(self):
        from roam.mcp_server import roam_diff
        self._check_args(roam_diff, {"staged": True}, ["diff", "--staged"])

    def test_roam_symbol(self):
        from roam.mcp_server import roam_symbol
        self._check_args(roam_symbol, {"name": "foo"}, ["symbol", "foo"])

    def test_roam_symbol_full(self):
        from roam.mcp_server import roam_symbol
        self._check_args(
            roam_symbol, {"name": "foo", "full": True},
            ["symbol", "foo", "--full"],
        )

    def test_roam_deps(self):
        from roam.mcp_server import roam_deps
        self._check_args(roam_deps, {"path": "src/cli.py"}, ["deps", "src/cli.py"])

    def test_roam_uses(self):
        from roam.mcp_server import roam_uses
        self._check_args(roam_uses, {"name": "open_db"}, ["uses", "open_db"])

    def test_roam_weather(self):
        from roam.mcp_server import roam_weather
        self._check_args(roam_weather, {"count": 10}, ["weather", "-n", "10"])

    def test_roam_debt(self):
        from roam.mcp_server import roam_debt
        self._check_args(roam_debt, {}, ["debt", "-n", "20"])

    def test_roam_debt_full(self):
        from roam.mcp_server import roam_debt
        self._check_args(
            roam_debt,
            {"limit": 5, "by_kind": True, "threshold": 10.0},
            ["debt", "-n", "5", "--by-kind", "--threshold", "10.0"],
        )

    def test_roam_n1(self):
        from roam.mcp_server import roam_n1
        self._check_args(roam_n1, {}, ["n1"])

    def test_roam_n1_with_options(self):
        from roam.mcp_server import roam_n1
        self._check_args(
            roam_n1,
            {"confidence": "high", "verbose": True},
            ["n1", "--confidence", "high", "--verbose"],
        )

    def test_roam_auth_gaps(self):
        from roam.mcp_server import roam_auth_gaps
        self._check_args(roam_auth_gaps, {}, ["auth-gaps"])

    def test_roam_auth_gaps_routes_only(self):
        from roam.mcp_server import roam_auth_gaps
        self._check_args(
            roam_auth_gaps,
            {"routes_only": True},
            ["auth-gaps", "--routes-only"],
        )

    def test_roam_over_fetch(self):
        from roam.mcp_server import roam_over_fetch
        self._check_args(roam_over_fetch, {}, ["over-fetch", "--threshold", "10"])

    def test_roam_missing_index(self):
        from roam.mcp_server import roam_missing_index
        self._check_args(roam_missing_index, {}, ["missing-index"])

    def test_roam_orphan_routes(self):
        from roam.mcp_server import roam_orphan_routes
        self._check_args(roam_orphan_routes, {}, ["orphan-routes", "-n", "50"])

    def test_roam_migration_safety(self):
        from roam.mcp_server import roam_migration_safety
        self._check_args(roam_migration_safety, {}, ["migration-safety", "-n", "50"])

    def test_roam_api_drift(self):
        from roam.mcp_server import roam_api_drift
        self._check_args(roam_api_drift, {}, ["api-drift"])

    def test_roam_api_drift_with_model(self):
        from roam.mcp_server import roam_api_drift
        self._check_args(
            roam_api_drift,
            {"model": "User", "confidence": "high"},
            ["api-drift", "--model", "User", "--confidence", "high"],
        )


# ---------------------------------------------------------------------------
# Error pattern table tests
# ---------------------------------------------------------------------------


class TestErrorPatterns:
    """Test the _ERROR_PATTERNS table structure."""

    def test_patterns_are_lowercase(self):
        from roam.mcp_server import _ERROR_PATTERNS
        for pattern, code, hint in _ERROR_PATTERNS:
            assert pattern == pattern.lower(), f"pattern '{pattern}' should be lowercase"

    def test_codes_are_uppercase(self):
        from roam.mcp_server import _ERROR_PATTERNS
        for pattern, code, hint in _ERROR_PATTERNS:
            assert code == code.upper(), f"code '{code}' should be uppercase"

    def test_hints_end_with_period(self):
        from roam.mcp_server import _ERROR_PATTERNS
        for pattern, code, hint in _ERROR_PATTERNS:
            assert hint.endswith("."), f"hint for {code} should end with period"

    def test_no_duplicate_patterns(self):
        from roam.mcp_server import _ERROR_PATTERNS
        patterns = [p for p, _, _ in _ERROR_PATTERNS]
        assert len(patterns) == len(set(patterns)), "duplicate patterns found"
