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
    """Test the roam CLI runner wrapper."""

    def test_inprocess_success(self):
        """In-process path (root='.') parses CliRunner JSON output."""
        from roam.mcp_server import _run_roam
        payload = {"summary": {"health_score": 85}}
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.output = json.dumps(payload)
        mock_result.exception = None
        with patch("click.testing.CliRunner.invoke", return_value=mock_result):
            result = _run_roam(["health"], ".")
            assert result == payload

    def test_inprocess_failure(self):
        """In-process path classifies errors from CliRunner output."""
        from roam.mcp_server import _run_roam
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.output = "Error: No .roam directory found"
        mock_result.exception = None
        with patch("click.testing.CliRunner.invoke", return_value=mock_result):
            result = _run_roam(["health"], ".")
            assert "error" in result
            assert result["error_code"] == "INDEX_NOT_FOUND"
            assert "hint" in result
            assert result["exit_code"] == 1

    def test_inprocess_json_decode_error(self):
        """In-process path handles non-JSON output gracefully."""
        from roam.mcp_server import _run_roam
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.output = "not json {{{"
        mock_result.exception = None
        with patch("click.testing.CliRunner.invoke", return_value=mock_result):
            result = _run_roam(["health"], ".")
            assert "error" in result
            assert "JSON" in result["error"]

    def test_subprocess_fallback_for_remote_root(self):
        """Non-'.' root falls back to subprocess."""
        from roam.mcp_server import _run_roam
        payload = {"summary": {"health_score": 85}}
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
            result = _run_roam(["health"], "/other/project")
            assert result == payload
            mock.assert_called_once()

    def test_subprocess_timeout(self):
        """Subprocess path handles timeout."""
        from roam.mcp_server import _run_roam
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="roam", timeout=60)):
            result = _run_roam(["health"], "/other/project")
            assert "error" in result
            assert "timed out" in result["error"]

    def test_inprocess_exception(self):
        """In-process path handles unexpected exceptions."""
        from roam.mcp_server import _run_roam
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.output = ""
        mock_result.exception = RuntimeError("something broke")
        with patch("click.testing.CliRunner.invoke", return_value=mock_result):
            result = _run_roam(["health"], ".")
            assert "error" in result


# ---------------------------------------------------------------------------
# _tool decorator tests
# ---------------------------------------------------------------------------


class TestToolDecorator:
    """Test the MCP tool registration decorator."""

    def test_default_preset_filters_non_core(self):
        """Non-core tools should be plain functions in core preset (default)."""
        import roam.mcp_server as mod
        # In core preset (default), non-core tool functions are not registered
        # They should still be callable as regular functions
        assert callable(mod.visualize)

    def test_core_tools_set_has_expected_members(self):
        """Core tools set should contain the documented tools."""
        from roam.mcp_server import _CORE_TOOLS
        expected = {
            # compound operations (4)
            "roam_explore", "roam_prepare_change", "roam_review_change",
            "roam_diagnose_issue",
            # comprehension (5)
            "roam_understand", "roam_search_symbol", "roam_context",
            "roam_file_info", "roam_deps",
            # daily workflow (6)
            "roam_preflight", "roam_diff",
            "roam_pr_risk", "roam_affected_tests", "roam_impact",
            "roam_uses",
            # code quality (5)
            "roam_health", "roam_dead_code",
            "roam_complexity_report", "roam_diagnose", "roam_trace",
        }
        assert _CORE_TOOLS == expected

    def test_core_tools_count(self):
        from roam.mcp_server import _CORE_TOOLS
        assert len(_CORE_TOOLS) == 20

    def test_presets_all_defined(self):
        """All 6 presets should be defined."""
        from roam.mcp_server import _PRESETS
        assert set(_PRESETS.keys()) == {"core", "review", "refactor", "debug", "architecture", "full"}

    def test_presets_are_supersets_of_core(self):
        """Named presets (except full) should include all core tools."""
        from roam.mcp_server import _PRESETS, _CORE_TOOLS
        for name, tools in _PRESETS.items():
            if name == "full":
                assert tools == set(), "full preset should be empty set (no filtering)"
            else:
                assert _CORE_TOOLS.issubset(tools), f"{name} preset missing core tools"

    def test_meta_tool_is_callable(self):
        """expand_toolset should be a callable function regardless of FastMCP."""
        from roam.mcp_server import expand_toolset
        assert callable(expand_toolset)

    def test_resolve_preset_default(self):
        """Default preset should be 'core'."""
        from roam.mcp_server import _resolve_preset
        with patch.dict(os.environ, {}, clear=False):
            # Remove both env vars if present
            env = os.environ.copy()
            env.pop("ROAM_MCP_PRESET", None)
            env.pop("ROAM_MCP_LITE", None)
            with patch.dict(os.environ, env, clear=True):
                # With neither env var, default is core
                result = _resolve_preset()
                assert result == "core"

    def test_resolve_preset_explicit(self):
        """Explicit ROAM_MCP_PRESET should override default."""
        from roam.mcp_server import _resolve_preset
        with patch.dict(os.environ, {"ROAM_MCP_PRESET": "review"}):
            assert _resolve_preset() == "review"

    def test_resolve_preset_legacy_lite_off(self):
        """ROAM_MCP_LITE=0 should map to 'full' preset."""
        from roam.mcp_server import _resolve_preset
        with patch.dict(os.environ, {"ROAM_MCP_LITE": "0"}, clear=False):
            env = os.environ.copy()
            env.pop("ROAM_MCP_PRESET", None)
            with patch.dict(os.environ, env, clear=True):
                env["ROAM_MCP_LITE"] = "0"
                with patch.dict(os.environ, env, clear=True):
                    assert _resolve_preset() == "full"


# ---------------------------------------------------------------------------
# expand_toolset meta-tool tests
# ---------------------------------------------------------------------------


class TestExpandToolset:
    """Test the expand_toolset meta-tool."""

    def test_list_all_presets(self):
        from roam.mcp_server import expand_toolset
        result = expand_toolset()
        assert "active_preset" in result
        assert "presets" in result
        assert set(result["presets"].keys()) == {
            "core", "review", "refactor", "debug", "architecture", "full",
        }

    def test_inspect_specific_preset(self):
        from roam.mcp_server import expand_toolset
        result = expand_toolset(preset="review")
        assert result["requested_preset"] == "review"
        assert "tools" in result
        assert isinstance(result["tools"], list)
        assert len(result["tools"]) > 20  # review has more than core
        assert "switch_instructions" in result

    def test_inspect_core_preset(self):
        from roam.mcp_server import expand_toolset
        result = expand_toolset(preset="core")
        assert result["tool_count"] == 20

    def test_invalid_preset(self):
        from roam.mcp_server import expand_toolset
        result = expand_toolset(preset="nonexistent")
        # Falls through to list-all-presets path
        assert "presets" in result


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


# ---------------------------------------------------------------------------
# _compound_envelope tests
# ---------------------------------------------------------------------------


class TestCompoundEnvelope:
    """Test the compound envelope builder."""

    def test_all_sections_succeed(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {"summary": {"verdict": "ok alpha"}, "data": [1, 2]}),
            ("beta", {"summary": {"verdict": "ok beta"}, "extra": "hi"}),
        ])
        assert result["command"] == "test-op"
        assert "alpha" in result
        assert "beta" in result
        assert result["summary"]["sections"] == ["alpha", "beta"]
        assert result["summary"]["errors"] == 0
        assert "alpha: ok alpha" in result["summary"]["verdict"]
        assert "beta: ok beta" in result["summary"]["verdict"]
        assert "_errors" not in result

    def test_one_section_fails(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {"summary": {"verdict": "good"}, "val": 1}),
            ("beta", {"error": "something broke"}),
        ])
        assert result["summary"]["errors"] == 1
        assert "alpha" in result
        assert "beta" not in result  # failed section not in top-level
        assert "_errors" in result
        assert result["_errors"][0]["command"] == "beta"

    def test_all_sections_fail(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {"error": "err1"}),
            ("beta", {"error": "err2"}),
        ])
        assert result["summary"]["errors"] == 2
        assert result["summary"]["sections"] == []
        assert len(result["_errors"]) == 2

    def test_empty_dict_treated_as_error(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {}),
        ])
        assert result["summary"]["errors"] == 1

    def test_meta_kwargs_in_summary(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {"summary": {"verdict": "ok"}}),
        ], target="my_func")
        assert result["summary"]["target"] == "my_func"

    def test_verdict_without_sub_verdicts(self):
        from roam.mcp_server import _compound_envelope
        result = _compound_envelope("test-op", [
            ("alpha", {"data": 1}),  # no summary.verdict
        ])
        assert result["summary"]["verdict"] == "compound operation completed"


# ---------------------------------------------------------------------------
# Compound operation tests
# ---------------------------------------------------------------------------


class TestCompoundOperations:
    """Test compound MCP operations."""

    def test_explore_without_symbol(self):
        from roam.mcp_server import explore
        overview = {"summary": {"verdict": "Python codebase, 85/100"}, "stack": ["python"]}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = overview
            result = explore()
            mock.assert_called_once_with(["understand"], ".")
        assert result["command"] == "explore"
        assert "understand" in result
        assert result["understand"] == overview

    def test_explore_with_symbol(self):
        from roam.mcp_server import explore
        overview = {"summary": {"verdict": "Python codebase"}}
        ctx = {"summary": {"verdict": "open_db context"}, "callers": []}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.side_effect = [overview, ctx]
            result = explore(symbol="open_db")
            assert mock.call_count == 2
            assert mock.call_args_list[0][0][0] == ["understand"]
            assert mock.call_args_list[1][0][0] == ["context", "open_db", "--task", "understand"]
        assert "understand" in result
        assert "context" in result
        assert result["summary"]["target"] == "open_db"

    def test_prepare_change(self):
        from roam.mcp_server import prepare_change
        pf = {"summary": {"verdict": "LOW risk"}, "blast_radius": {}}
        ctx = {"summary": {"verdict": "3 files to read"}, "files": []}
        eff = {"summary": {"verdict": "2 effects"}, "effects": []}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.side_effect = [pf, ctx, eff]
            result = prepare_change(target="my_func")
            assert mock.call_count == 3
            assert mock.call_args_list[0][0][0] == ["preflight", "my_func"]
            assert mock.call_args_list[1][0][0] == ["context", "my_func", "--task", "refactor"]
            assert mock.call_args_list[2][0][0] == ["effects", "my_func"]
        assert "preflight" in result
        assert "context" in result
        assert "effects" in result
        assert result["summary"]["target"] == "my_func"

    def test_prepare_change_staged(self):
        from roam.mcp_server import prepare_change
        pf = {"summary": {"verdict": "ok"}}
        ctx = {"summary": {"verdict": "ok"}}
        eff = {"summary": {"verdict": "ok"}}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.side_effect = [pf, ctx, eff]
            prepare_change(target="func", staged=True)
            # First call should include --staged
            assert "--staged" in mock.call_args_list[0][0][0]

    def test_review_change_default(self):
        from roam.mcp_server import review_change
        risk = {"summary": {"verdict": "LOW 12/100"}}
        brk = {"summary": {"verdict": "0 breaking"}}
        diff = {"summary": {"verdict": "2 files"}}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.side_effect = [risk, brk, diff]
            result = review_change()
            assert mock.call_count == 3
            assert mock.call_args_list[0][0][0] == ["pr-risk"]
            assert mock.call_args_list[1][0][0] == ["breaking"]
            assert mock.call_args_list[2][0][0] == ["pr-diff"]
        assert "pr_risk" in result
        assert "breaking_changes" in result
        assert "pr_diff" in result

    def test_review_change_with_range(self):
        from roam.mcp_server import review_change
        data = {"summary": {"verdict": "ok"}}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = data
            review_change(commit_range="main..HEAD")
            assert mock.call_args_list[1][0][0] == ["breaking", "main..HEAD"]
            assert "--range" in mock.call_args_list[2][0][0]

    def test_review_change_staged(self):
        from roam.mcp_server import review_change
        data = {"summary": {"verdict": "ok"}}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = data
            review_change(staged=True)
            assert "--staged" in mock.call_args_list[0][0][0]
            assert "--staged" in mock.call_args_list[2][0][0]

    def test_diagnose_issue(self):
        from roam.mcp_server import diagnose_issue
        diag = {"summary": {"verdict": "top suspect: parse_input"}, "suspects": []}
        eff = {"summary": {"verdict": "3 effects"}, "effects": []}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.side_effect = [diag, eff]
            result = diagnose_issue(symbol="broken_func")
            assert mock.call_count == 2
            assert mock.call_args_list[0][0][0] == ["diagnose", "broken_func", "--depth", "2"]
            assert mock.call_args_list[1][0][0] == ["effects", "broken_func"]
        assert "diagnose" in result
        assert "effects" in result
        assert result["summary"]["target"] == "broken_func"

    def test_diagnose_issue_custom_depth(self):
        from roam.mcp_server import diagnose_issue
        data = {"summary": {"verdict": "ok"}}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = data
            diagnose_issue(symbol="func", depth=5)
            assert "--depth" in mock.call_args_list[0][0][0]
            assert "5" in mock.call_args_list[0][0][0]

    def test_compound_handles_sub_error(self):
        """Compound operations should include errors without crashing."""
        from roam.mcp_server import explore
        overview = {"error": "No .roam directory found"}
        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = overview
            result = explore()
        assert result["summary"]["errors"] == 1
        assert "_errors" in result

    def test_compound_functions_are_callable(self):
        """All 4 compound functions should be importable and callable."""
        from roam.mcp_server import explore, prepare_change, review_change, diagnose_issue
        assert callable(explore)
        assert callable(prepare_change)
        assert callable(review_change)
        assert callable(diagnose_issue)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    """Test output schema infrastructure."""

    def test_envelope_schema_structure(self):
        from roam.mcp_server import _ENVELOPE_SCHEMA
        assert _ENVELOPE_SCHEMA["type"] == "object"
        props = _ENVELOPE_SCHEMA["properties"]
        assert "command" in props
        assert "summary" in props
        assert "verdict" in props["summary"]["properties"]

    def test_make_schema_basic(self):
        from roam.mcp_server import _make_schema
        schema = _make_schema()
        assert schema["type"] == "object"
        assert "verdict" in schema["properties"]["summary"]["properties"]

    def test_make_schema_with_summary_fields(self):
        from roam.mcp_server import _make_schema
        schema = _make_schema({"score": {"type": "number"}})
        summary_props = schema["properties"]["summary"]["properties"]
        assert "verdict" in summary_props
        assert "score" in summary_props
        assert summary_props["score"]["type"] == "number"

    def test_make_schema_with_payload_fields(self):
        from roam.mcp_server import _make_schema
        schema = _make_schema(results={"type": "array"})
        assert "results" in schema["properties"]
        assert schema["properties"]["results"]["type"] == "array"

    def test_compound_schemas_exist(self):
        from roam.mcp_server import (
            _SCHEMA_EXPLORE, _SCHEMA_PREPARE_CHANGE,
            _SCHEMA_REVIEW_CHANGE, _SCHEMA_DIAGNOSE_ISSUE,
        )
        for schema in [_SCHEMA_EXPLORE, _SCHEMA_PREPARE_CHANGE,
                       _SCHEMA_REVIEW_CHANGE, _SCHEMA_DIAGNOSE_ISSUE]:
            assert schema["type"] == "object"
            assert "summary" in schema["properties"]

    def test_explore_schema_has_sections(self):
        from roam.mcp_server import _SCHEMA_EXPLORE
        props = _SCHEMA_EXPLORE["properties"]
        assert "understand" in props
        assert "context" in props
        assert "sections" in props["summary"]["properties"]

    def test_prepare_change_schema_has_sections(self):
        from roam.mcp_server import _SCHEMA_PREPARE_CHANGE
        props = _SCHEMA_PREPARE_CHANGE["properties"]
        assert "preflight" in props
        assert "context" in props
        assert "effects" in props

    def test_review_change_schema_has_sections(self):
        from roam.mcp_server import _SCHEMA_REVIEW_CHANGE
        props = _SCHEMA_REVIEW_CHANGE["properties"]
        assert "pr_risk" in props
        assert "breaking_changes" in props
        assert "pr_diff" in props

    def test_diagnose_issue_schema_has_sections(self):
        from roam.mcp_server import _SCHEMA_DIAGNOSE_ISSUE
        props = _SCHEMA_DIAGNOSE_ISSUE["properties"]
        assert "diagnose" in props
        assert "effects" in props

    def test_core_tool_schemas_exist(self):
        from roam.mcp_server import (
            _SCHEMA_UNDERSTAND, _SCHEMA_HEALTH, _SCHEMA_SEARCH,
            _SCHEMA_PREFLIGHT, _SCHEMA_CONTEXT, _SCHEMA_IMPACT,
            _SCHEMA_PR_RISK, _SCHEMA_DIFF, _SCHEMA_DIAGNOSE, _SCHEMA_TRACE,
        )
        for schema in [_SCHEMA_UNDERSTAND, _SCHEMA_HEALTH, _SCHEMA_SEARCH,
                       _SCHEMA_PREFLIGHT, _SCHEMA_CONTEXT, _SCHEMA_IMPACT,
                       _SCHEMA_PR_RISK, _SCHEMA_DIFF, _SCHEMA_DIAGNOSE,
                       _SCHEMA_TRACE]:
            assert schema["type"] == "object"
            assert "summary" in schema["properties"]

    def test_health_schema_has_score(self):
        from roam.mcp_server import _SCHEMA_HEALTH
        summary_props = _SCHEMA_HEALTH["properties"]["summary"]["properties"]
        assert "health_score" in summary_props

    def test_search_schema_has_results(self):
        from roam.mcp_server import _SCHEMA_SEARCH
        assert "results" in _SCHEMA_SEARCH["properties"]
        assert _SCHEMA_SEARCH["properties"]["results"]["type"] == "array"
