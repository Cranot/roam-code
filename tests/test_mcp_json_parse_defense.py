"""Tests for Fix A (SYNTHESIS Pattern 1) — JSON-parse-on-empty-input.

When the CLI emits empty stdout on a success path (e.g. ``roam diff``
on a clean tree, ``roam file`` on a path with no symbols), the MCP
wrapper used to feed it to ``json.loads()`` and crash. The fix
intercepts empty-stdout-on-success and returns a structured
``state=no_data`` envelope.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Mirror tests/test_mcp_server.py's isolation fixture."""
    from roam.mcp_server import _ROAM_RESULT_CACHE, _reset_error_storm

    _ROAM_RESULT_CACHE.clear()
    _reset_error_storm()
    yield
    _ROAM_RESULT_CACHE.clear()
    _reset_error_storm()


# ---------------------------------------------------------------------------
# In-process path — empty + corrupted stdout
# ---------------------------------------------------------------------------


def test_empty_stdout_on_success_returns_no_data_envelope():
    """A successful exit_code with empty stdout no longer crashes; the
    wrapper emits a clean ``state=no_data`` envelope."""
    from roam.mcp_server import _run_roam

    mock_result = MagicMock()
    mock_result.exit_code = 0
    mock_result.output = ""
    mock_result.exception = None
    with patch("click.testing.CliRunner.invoke", return_value=mock_result):
        result = _run_roam(["impact", "some_symbol"], ".")

    summary = result.get("summary") or {}
    assert summary.get("state") == "no_data"
    assert summary.get("partial_success") is False
    assert summary.get("verdict") == "no data"
    assert result.get("data") == []


def test_empty_stdout_on_diff_command_returns_no_changes_verdict():
    """The ``diff`` family gets the ``no changes`` verdict instead of
    the generic ``no data`` — this is what for_bug_fix's diff
    subcommand consumes."""
    from roam.mcp_server import _run_roam

    mock_result = MagicMock()
    mock_result.exit_code = 0
    mock_result.output = ""
    mock_result.exception = None
    with patch("click.testing.CliRunner.invoke", return_value=mock_result):
        result = _run_roam(["diff"], ".")

    summary = result.get("summary") or {}
    assert summary.get("state") == "no_data"
    assert summary.get("verdict") == "no changes"


def test_corrupted_stdout_returns_invalid_output_envelope():
    """Non-empty stdout that isn't valid JSON returns an
    ``INVALID_JSON`` envelope with a 500-char preview so the agent
    sees what came back."""
    from roam.mcp_server import _run_roam

    mock_result = MagicMock()
    mock_result.exit_code = 0
    mock_result.output = "not json {{{ garbage"
    mock_result.exception = None
    with patch("click.testing.CliRunner.invoke", return_value=mock_result):
        result = _run_roam(["health"], ".")

    summary = result.get("summary") or {}
    assert summary.get("state") == "invalid_output"
    assert summary.get("partial_success") is True
    assert result.get("error_code") == "INVALID_JSON"
    # Preview is included so the agent can debug.
    assert "raw_stdout_preview" in result
    assert "garbage" in result["raw_stdout_preview"]


# ---------------------------------------------------------------------------
# Subprocess path — empty + corrupted stdout
# ---------------------------------------------------------------------------


def test_subprocess_empty_stdout_returns_no_data_envelope():
    """Same defence on the subprocess fallback path."""
    from roam.mcp_server import _run_roam

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        result = _run_roam(["uses", "ensure_index"], "/other/project")

    summary = result.get("summary") or {}
    assert summary.get("state") == "no_data"
    assert summary.get("partial_success") is False


def test_subprocess_corrupted_stdout_returns_invalid_output_envelope():
    """Same INVALID_JSON envelope from the subprocess path."""
    from roam.mcp_server import _run_roam

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not-json {{{",
            stderr="",
        )
        result = _run_roam(["health"], "/other/project")

    summary = result.get("summary") or {}
    assert summary.get("state") == "invalid_output"
    assert result.get("error_code") == "INVALID_JSON"
    assert "raw_stdout_preview" in result


# ---------------------------------------------------------------------------
# file_info — pre-validates path before invoking CLI
# ---------------------------------------------------------------------------


def test_file_info_on_missing_path_returns_no_data_envelope():
    """``roam_file_info`` pre-validates that the path exists and emits
    a clean envelope rather than letting the CLI emit empty stdout."""
    from roam.mcp_server import file_info

    out = file_info("definitely/does/not/exist.py", ".")
    summary = out.get("summary") or {}
    assert summary.get("state") == "no_data"
    assert summary.get("partial_success") is False
    assert out.get("data") == []
    # The verdict should be the clean 'no data' string, not a CLI error.
    assert summary.get("verdict") == "no data"


def test_file_info_on_empty_path_returns_no_data_envelope():
    """Empty / whitespace path is treated the same as a missing path."""
    from roam.mcp_server import file_info

    for path in ["", "   ", "\t"]:
        out = file_info(path, ".")
        summary = out.get("summary") or {}
        assert summary.get("state") == "no_data", f"empty path {path!r} did not return no_data"
        assert summary.get("partial_success") is False


def test_file_info_on_valid_path_passes_through():
    """Sanity check: a path that EXISTS in the repo still works — the
    defence only kicks in for missing paths."""
    from roam.mcp_server import file_info

    # Mock the CLI invocation so we don't depend on a real index.
    payload = {"summary": {"verdict": "ok"}, "symbols": [{"name": "foo"}]}
    with patch("roam.mcp_server._run_roam", return_value=payload):
        out = file_info("src/roam/__init__.py", ".")

    # We got the payload back, not the no_data envelope.
    assert out is payload or out.get("symbols") == [{"name": "foo"}]


# ---------------------------------------------------------------------------
# session_metrics + validate_plan integration tests
# ---------------------------------------------------------------------------


def test_session_metrics_exposes_partial_success_count_field():
    """Fix E — ``session_metrics`` must surface the new
    ``partial_success_count`` field even when zero."""
    from roam.mcp_server import _reset_session_partial_success_count, roam_session_metrics

    _reset_session_partial_success_count()
    out = roam_session_metrics(".")
    summary = out.get("summary") or {}
    assert "partial_success_count" in summary
    assert "command_error_count" in summary
    # Legacy alias preserved for back-compat.
    assert "error_count" in summary


def test_session_metrics_counts_partial_success_envelopes():
    """When envelopes flow through with ``summary.partial_success: true``
    they must be counted in ``partial_success_count``."""
    from roam.mcp_server import (
        _note_partial_success,
        _reset_session_partial_success_count,
        roam_session_metrics,
    )

    _reset_session_partial_success_count()
    _note_partial_success({"summary": {"partial_success": True}})
    _note_partial_success({"summary": {"partial_success": True}})
    _note_partial_success({"summary": {"partial_success": False}})

    out = roam_session_metrics(".")
    summary = out.get("summary") or {}
    assert summary["partial_success_count"] == 2


def test_validate_plan_unknown_kind_enumerates_expected_fields():
    """Fix E — the UNKNOWN_KIND blocker must include ``expected_fields``
    so the agent doesn't have to guess what shape each kind takes."""
    from roam.mcp_server import validate_plan

    out = validate_plan(operations=[{"kind": "teleport", "symbol": "x"}], root=".")
    ops = out.get("operations") or []
    assert ops, "expected at least one operation result"
    blockers = ops[0].get("blockers") or []
    unknown = [b for b in blockers if b.get("code") == "UNKNOWN_KIND"]
    assert unknown, f"expected an UNKNOWN_KIND blocker, got {blockers!r}"
    blocker = unknown[0]
    # supported_kinds + expected_fields must be enumerated.
    assert "supported_kinds" in blocker
    assert set(blocker["supported_kinds"]) == {"rename", "move", "remove", "modify", "add"}
    assert "expected_fields" in blocker
    assert "rename" in blocker["expected_fields"]
    assert "new_name" in blocker["expected_fields"]["rename"]
