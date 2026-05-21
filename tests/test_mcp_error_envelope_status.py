"""Drift guard — MCP error envelopes carry the Pattern-1 canonical ``status``.

CLAUDE.md "Pattern-1 canonical failure envelope" mandates that every
wrapper that cannot complete normally produces an envelope pairing
``isError: true`` with a closed-enum ``status``. Before this guard,
the MCP error-envelope surface carried ``isError`` + ``error_code`` but
NOT the canonical ``status`` field.

This test pins the conformance fix:

* ``_structured_error`` stamps a canonical ``status`` for every
  ``error_code`` it can receive (via ``_ERROR_CODE_TO_STATUS``, with a
  ``hard_failure`` fallback for unknown codes).
* ``busy_envelope`` carries TOP-LEVEL ``error_code`` + ``isError`` +
  ``status == "rate_limited"`` (not nested under ``summary``).
* ``cold_start_envelope`` carries ``isError: true`` alongside its
  existing ``status: "index_not_built"``.
* ``_build_invalid_json_envelope`` carries ``isError`` + ``status``.
* ``_compound_envelope`` all-failed path carries ``isError`` + ``status``.

No centralized 7-value status enum exists in the codebase as of
2026-05-21, so the canonical set is hardcoded here with a CLAUDE.md
citation. If a canonical constant lands later, import it instead.
"""

from __future__ import annotations

import pytest

# CLAUDE.md "Pattern-1 canonical failure envelope" — the closed
# enumeration of ``status`` values. Source of truth: the ``status``
# field description in CLAUDE.md ("closed enum: index_not_built |
# advisory_warnings | partial_failure | hard_failure | usage_error |
# rate_limited | stale_index"). Hardcoded because no single module-level
# constant exposes the full 7-value set yet.
_CANONICAL_STATUS_VALUES = frozenset(
    {
        "index_not_built",
        "advisory_warnings",
        "partial_failure",
        "hard_failure",
        "usage_error",
        "rate_limited",
        "stale_index",
    }
)


# Representative error codes — one per status class — exercised through
# ``_structured_error``. The expected ``status`` mirrors
# ``_ERROR_CODE_TO_STATUS`` in ``mcp_server.py``.
_CODE_TO_EXPECTED_STATUS = [
    ("USAGE_ERROR", "usage_error"),
    ("EMPTY_INPUT", "usage_error"),
    ("INVALID_DIFF", "usage_error"),
    ("ELICITATION_REQUIRED", "usage_error"),
    ("INDEX_NOT_FOUND", "index_not_built"),
    ("INDEX_STALE", "stale_index"),
    ("STALE_DB_DIR", "stale_index"),
    ("RATE_LIMITED", "rate_limited"),
    ("PARTIAL_FAILURE", "partial_failure"),
    ("INVALID_JSON", "partial_failure"),
    ("JSON_DECODE", "partial_failure"),
    ("COMMAND_FAILED", "hard_failure"),
    ("RUN_FAILED", "hard_failure"),
    ("NOT_GIT_REPO", "hard_failure"),
    ("DB_LOCKED", "hard_failure"),
    ("PERMISSION_DENIED", "hard_failure"),
    ("GATE_FAILURE", "hard_failure"),
    ("NO_RESULTS", "hard_failure"),
    ("FILE_NOT_FOUND", "hard_failure"),
    ("DIRTY_TREE", "hard_failure"),
    ("APPLY_FAILED", "hard_failure"),
    ("MODE_BLOCKED", "hard_failure"),
    ("UNKNOWN", "hard_failure"),
]


@pytest.mark.parametrize("code,expected_status", _CODE_TO_EXPECTED_STATUS)
def test_structured_error_stamps_canonical_status(code, expected_status):
    """Every error code routed through ``_structured_error`` gets a canonical status."""
    from roam.mcp_server import _reset_error_storm, _structured_error

    # Reset the storm coalescer so this call gets the full (untrimmed)
    # envelope — the trimmed shape is exercised separately below.
    _reset_error_storm()
    result = _structured_error(
        {
            "error": f"synthetic {code} failure",
            "error_code": code,
            "hint": "synthetic hint",
        }
    )
    assert result["isError"] is True
    assert result["status"] == expected_status
    assert result["status"] in _CANONICAL_STATUS_VALUES


def test_structured_error_unknown_code_falls_back_to_hard_failure():
    """An error code absent from the map falls through to ``hard_failure``."""
    from roam.mcp_server import _reset_error_storm, _structured_error

    _reset_error_storm()
    result = _structured_error(
        {
            "error": "a code that is not in the map",
            "error_code": "SOME_BRAND_NEW_CODE",
            "hint": "synthetic hint",
        }
    )
    assert result["isError"] is True
    assert result["status"] == "hard_failure"
    assert result["status"] in _CANONICAL_STATUS_VALUES


def test_structured_error_explicit_status_wins_over_code_default():
    """``setdefault`` semantics — an explicit caller status is preserved."""
    from roam.mcp_server import _reset_error_storm, _structured_error

    _reset_error_storm()
    # COMMAND_FAILED defaults to hard_failure, but a caller that knows
    # the command completed partially can pass partial_failure explicitly.
    result = _structured_error(
        {
            "error": "command failed partially",
            "error_code": "COMMAND_FAILED",
            "status": "partial_failure",
            "hint": "synthetic hint",
        }
    )
    assert result["isError"] is True
    assert result["status"] == "partial_failure"


def test_structured_error_trimmed_envelope_keeps_status():
    """The storm-trimmed envelope still carries the canonical status."""
    from roam.mcp_server import _reset_error_storm, _structured_error

    _reset_error_storm()
    last = None
    # Fire the same code past the storm threshold (3) so the trimmed
    # shape is returned.
    for _ in range(5):
        last = _structured_error(
            {
                "error": "db is locked",
                "error_code": "DB_LOCKED",
                "hint": "wait and retry",
            }
        )
    assert last is not None
    assert last.get("trimmed") is True
    assert last["isError"] is True
    assert last["status"] == "hard_failure"
    assert last["status"] in _CANONICAL_STATUS_VALUES
    _reset_error_storm()


def test_busy_envelope_has_top_level_error_fields_and_status():
    """``busy_envelope`` carries TOP-LEVEL error fields + isError + rate_limited status."""
    from roam.mcp_extras.concurrency import busy_envelope

    env = busy_envelope("roam_retrieve")
    # Pattern-1 canonical shape — error fields at the top level, not
    # nested under summary.
    assert env["error_code"] == "RATE_LIMITED"
    assert env["isError"] is True
    assert env["status"] == "rate_limited"
    assert env["status"] in _CANONICAL_STATUS_VALUES
    assert env["retryable"] is True
    assert "error" in env
    assert "hint" in env
    # summary keeps a single-line verdict (LAW 6) but no longer carries
    # the error fields.
    assert "verdict" in env["summary"]
    assert "error_code" not in env["summary"]


def test_cold_start_envelope_has_iserror():
    """``cold_start_envelope`` pairs its status with ``isError: true``."""
    from roam.mcp_extras.preflight import cold_start_envelope

    env = cold_start_envelope("roam_understand")
    assert env["isError"] is True
    assert env["status"] == "index_not_built"
    assert env["status"] in _CANONICAL_STATUS_VALUES


def test_invalid_json_envelope_has_iserror_and_status():
    """``_build_invalid_json_envelope`` carries isError + canonical status."""
    from roam.mcp_server import _build_invalid_json_envelope

    env = _build_invalid_json_envelope(
        ["health"],
        "Failed to parse JSON output: synthetic",
        "not json at all",
    )
    assert env["isError"] is True
    assert env["status"] == "partial_failure"
    assert env["status"] in _CANONICAL_STATUS_VALUES
    assert env["error_code"] == "INVALID_JSON"


def test_compound_envelope_all_failed_has_iserror_and_status():
    """The ``_compound_envelope`` all-failed path is a conformant failure envelope."""
    from roam.mcp_server import _compound_envelope

    result = _compound_envelope(
        "test-op",
        [
            ("alpha", {"error": "err1"}),
            ("beta", {"error": "err2"}),
        ],
    )
    assert result["isError"] is True
    assert result["status"] == "partial_failure"
    assert result["status"] in _CANONICAL_STATUS_VALUES


def test_compound_envelope_partial_success_not_marked_iserror():
    """A compound with at least one surviving section is NOT a failure envelope."""
    from roam.mcp_server import _compound_envelope

    result = _compound_envelope(
        "test-op",
        [
            ("alpha", {"summary": {"verdict": "good"}, "val": 1}),
            ("beta", {"error": "something broke"}),
        ],
    )
    # Partial (not all-failed) — isError/status are NOT stamped; the
    # partial_success flag in the summary carries the degraded signal.
    assert "isError" not in result
    assert "status" not in result
    assert result["summary"]["partial_success"] is True


def test_error_code_to_status_map_targets_only_canonical_values():
    """Every value in ``_ERROR_CODE_TO_STATUS`` is in the canonical 7-value set."""
    from roam.mcp_server import _ERROR_CODE_TO_STATUS

    for code, status in _ERROR_CODE_TO_STATUS.items():
        assert status in _CANONICAL_STATUS_VALUES, f"{code} -> {status} is not canonical"
