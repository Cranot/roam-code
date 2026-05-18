"""Tests for Task 2 (IMPLEMENTATION-2026-05-12) — first_error_message
preservation across error-storm trim. The fix snapshots the FIRST
human-readable error message for a given error_code so that trimmed
storm envelopes (fire >= 3) still carry the actionable text. Without
this fix the agent loop loses the remediation hint after the third
fire and has to break the storm to recover it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_storm_state():
    """Reset both the storm counter and the first-error-message cache
    between tests so one test's poison doesn't leak into the next."""
    from roam.mcp_server import _reset_error_storm

    _reset_error_storm()
    yield
    _reset_error_storm()


def _make_error(error_code: str, message: str) -> dict:
    """Build the minimal error_dict shape that ``_structured_error``
    consumes (hint/severity get filled in by the function)."""
    return {
        "error": message,
        "error_code": error_code,
        "hint": "see docs",
    }


def test_first_occurrence_captured_in_cache():
    """First fire of a given error_code populates _first_error_message
    so a subsequent trimmed fire can replay the text."""
    from roam.mcp_server import _first_error_message, _structured_error

    out = _structured_error(_make_error("DB_LOCKED", "lock held by pid 9001"))

    # First fire returns the full verbose envelope (not trimmed).
    assert out.get("trimmed") is not True
    assert _first_error_message.get("DB_LOCKED") == "lock held by pid 9001"


def test_trimmed_envelope_preserves_first_error_message():
    """After 3 fires of the same code the envelope is trimmed — but it
    still carries ``first_error_message`` so the agent can read the
    original stderr text without breaking the storm."""
    from roam.mcp_server import _structured_error

    # Fire 1: full envelope, captures the first message.
    _structured_error(_make_error("INDEX_STALE", "schema bumped: 12.51 -> 12.60"))
    # Fire 2: full envelope still.
    _structured_error(_make_error("INDEX_STALE", "schema bumped: 12.51 -> 12.60"))
    # Fire 3: trimmed envelope.
    trimmed = _structured_error(_make_error("INDEX_STALE", "schema bumped: 12.51 -> 12.60"))

    assert trimmed.get("trimmed") is True
    assert trimmed.get("error_code") == "INDEX_STALE"
    # The whole point of the fix — must surface the original message.
    assert trimmed.get("first_error_message") == "schema bumped: 12.51 -> 12.60"


def test_error_code_change_resets_cache_for_previous_code():
    """When error_code A is replaced by code B mid-session, the cache
    entry for A is dropped so we never leak A's stderr into B's
    envelope."""
    from roam.mcp_server import _first_error_message, _structured_error

    _structured_error(_make_error("DB_LOCKED", "lock held by pid 9001"))
    assert _first_error_message.get("DB_LOCKED") == "lock held by pid 9001"

    # Different code fires — DB_LOCKED's cached entry should be cleared.
    _structured_error(_make_error("INDEX_STALE", "schema bumped"))
    assert "DB_LOCKED" not in _first_error_message
    assert _first_error_message.get("INDEX_STALE") == "schema bumped"


def test_reset_error_storm_clears_first_error_message_cache():
    """``_reset_error_storm`` MUST also drop ``_first_error_message``
    so test-isolation works the way the docstring claims."""
    from roam.mcp_server import _first_error_message, _reset_error_storm, _structured_error

    _structured_error(_make_error("USAGE_ERROR", "missing --symbol flag"))
    assert _first_error_message.get("USAGE_ERROR") == "missing --symbol flag"

    _reset_error_storm()
    assert _first_error_message == {}


def test_no_leak_across_error_codes_in_trimmed_envelope():
    """A trimmed envelope for code B must NOT carry the first message
    of code A — even though both were observed in the session. This is
    a regression guard against an off-by-one where the cache used a
    single-slot instead of dict-keyed-by-code design."""
    from roam.mcp_server import _structured_error

    _structured_error(_make_error("DB_LOCKED", "DB_LOCKED message"))
    # Trigger storm for INDEX_STALE.
    _structured_error(_make_error("INDEX_STALE", "INDEX_STALE message"))
    _structured_error(_make_error("INDEX_STALE", "INDEX_STALE message"))
    trimmed = _structured_error(_make_error("INDEX_STALE", "INDEX_STALE message"))

    assert trimmed.get("first_error_message") == "INDEX_STALE message"
    # Must not mix codes — the DB_LOCKED text should NOT appear in the
    # INDEX_STALE trimmed envelope.
    assert "DB_LOCKED message" not in trimmed.get("first_error_message", "")


def test_trimmed_envelope_keeps_command_without_cross_command_message_leak():
    """Repeated same-code usage errors from different tools must keep the
    current command identity and avoid replaying another command's text."""
    from roam.mcp_server import _structured_error

    _structured_error(
        {
            "command": "roam_for_refactor",
            "error": "symbol is required for roam_for_refactor",
            "error_code": "USAGE_ERROR",
            "hint": "pass a symbol",
        }
    )
    _structured_error(
        {
            "command": "roam_for_security_review",
            "error": "vulns list rejected extra argument",
            "error_code": "USAGE_ERROR",
            "hint": "call vulns without list",
        }
    )
    trimmed = _structured_error(
        {
            "command": "roam_for_bug_fix",
            "error": "symbol is required for roam_for_bug_fix",
            "error_code": "USAGE_ERROR",
            "hint": "pass a symbol",
        }
    )

    assert trimmed.get("trimmed") is True
    assert trimmed.get("command") == "roam_for_bug_fix"
    assert trimmed.get("first_error_message") == "symbol is required for roam_for_bug_fix"


def test_r9_preserved_fields_still_present_in_trimmed_envelope():
    """Regression guard: the R9 security recheck fields (``retryable``,
    ``doc_link``) must stay alongside the new ``first_error_message``
    field. Otherwise the new fix would re-introduce the field-stripping
    bug R9 already closed."""
    from roam.mcp_server import _structured_error

    for _ in range(3):
        trimmed = _structured_error(_make_error("DB_LOCKED", "lock held by pid 9001"))

    # Trimmed (third fire).
    assert trimmed.get("trimmed") is True
    # R9 fields must still be in the envelope.
    assert "retryable" in trimmed
    assert "doc_link" in trimmed
    assert "severity" in trimmed
    assert "repeat_count" in trimmed
    # The new field also lives alongside them.
    assert "first_error_message" in trimmed


def test_stale_db_dir_envelope_carries_first_error_message_after_trim():
    """Task 2a integration: StaleDbDirError surfaces as STALE_DB_DIR with
    the configured-path + remediation hint in ``error``. After 3 fires
    the trimmed envelope still carries that text via the new
    ``first_error_message`` field — the dogfood corpus's canonical
    use-case for the fix."""
    from roam.mcp_server import _structured_error

    msg = (
        "db_dir 'D:\\\\external\\\\.roam' (configured in .roam/config.json db_dir) "
        "is not usable: [WinError 53] The network path was not found"
    )
    for _ in range(3):
        trimmed = _structured_error(
            {
                "error": msg,
                "error_code": "STALE_DB_DIR",
                "state": "stale_db_dir",
                "partial_success": True,
                "hint": "edit the config or run `roam config db-dir --reset`",
            }
        )

    assert trimmed.get("trimmed") is True
    assert trimmed.get("error_code") == "STALE_DB_DIR"
    # The fix replays the original stderr in the trimmed envelope.
    assert trimmed.get("first_error_message") == msg
