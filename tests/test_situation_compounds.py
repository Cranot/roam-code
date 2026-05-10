"""Tests for the four situation-keyed compound MCP tools (R8.E4):

* ``roam_for_new_feature`` — orientation + search + context + complexity
* ``roam_for_bug_fix`` — diagnose + tests + diff + context
* ``roam_for_refactor`` — preflight + impact + complexity + clones
* ``roam_for_security_review`` — taint + vuln + critique + adversarial

Each test asserts the envelope shape and validates the bundled
sub-commands ran (or failed cleanly via the partial-success path).
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "fastmcp", reason="MCP tool tests require fastmcp; mcp_server module won't import without it."
)

from roam.mcp_server import (
    for_bug_fix,
    for_new_feature,
    for_refactor,
    for_security_review,
)


@pytest.fixture(autouse=True)
def _disable_handle_off(monkeypatch):
    """Disable the R8.E8 large-response handle-off for these tests so
    the assertions can read the full compound summary directly. The
    handle-off behaviour itself is covered by ``test_mcp_handle_off``.
    """
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    yield


# ---------------------------------------------------------------------------
# Common envelope shape — every compound returns the same outer shape
# ---------------------------------------------------------------------------


def _assert_compound_envelope(result: dict, command: str, situation: str) -> None:
    assert isinstance(result, dict), f"expected dict, got {type(result).__name__}"
    assert result.get("command") == command, result.get("command")
    summary = result.get("summary") or {}
    assert isinstance(summary, dict)
    assert "verdict" in summary, summary
    assert summary.get("situation") == situation, summary
    assert isinstance(summary.get("sections"), list)
    # partial_success may be True (some sub-cmds errored) or False;
    # always a bool.
    assert isinstance(summary.get("partial_success"), bool), summary


# ---------------------------------------------------------------------------
# roam_for_new_feature
# ---------------------------------------------------------------------------


def test_for_new_feature_with_area_includes_search_and_context():
    """Passing an ``area`` that resolves to a known symbol should
    surface both ``search`` and ``context`` sections."""
    r = for_new_feature(area="analyze_n1")
    _assert_compound_envelope(r, "for-new-feature", "new_feature")
    sections = r["summary"]["sections"]
    assert "understand" in sections
    assert "search" in sections
    # context is best-effort: only fetched when search returned a match.
    # analyze_n1 is a real symbol in this repo, so context should be there.
    assert "context" in sections, sections


def test_for_new_feature_no_area_returns_orientation_only():
    """Empty area: the tool still runs understand + complexity_report
    but skips search + context."""
    r = for_new_feature(area="")
    _assert_compound_envelope(r, "for-new-feature", "new_feature")
    sections = r["summary"]["sections"]
    assert "understand" in sections
    assert "search" not in sections
    assert "context" not in sections


# ---------------------------------------------------------------------------
# roam_for_bug_fix
# ---------------------------------------------------------------------------


def test_for_bug_fix_requires_symbol():
    r = for_bug_fix(symbol="")
    assert r.get("isError") is True
    assert "USAGE_ERROR" in r.get("error_code", "")


def test_for_bug_fix_returns_four_sections_for_real_symbol():
    r = for_bug_fix(symbol="analyze_n1")
    _assert_compound_envelope(r, "for-bug-fix", "bug_fix")
    sections = r["summary"]["sections"]
    # All four sub-commands SHOULD succeed on this repo's index.
    for expected in ("diagnose", "affected_tests", "diff", "context"):
        assert expected in sections, f"missing {expected!r} in {sections}"


# ---------------------------------------------------------------------------
# roam_for_refactor
# ---------------------------------------------------------------------------


def test_for_refactor_requires_symbol():
    r = for_refactor(symbol="")
    assert r.get("isError") is True
    assert "USAGE_ERROR" in r.get("error_code", "")


def test_for_refactor_envelope_for_real_symbol():
    r = for_refactor(symbol="analyze_n1")
    _assert_compound_envelope(r, "for-refactor", "refactor")
    sections = r["summary"]["sections"]
    # preflight + impact must be present (cheap, deterministic).
    # complexity_report + clones may take a moment but shouldn't error
    # — the whole point of the helper is one-shot fetch.
    assert "preflight" in sections
    assert "impact" in sections


# ---------------------------------------------------------------------------
# roam_for_security_review
# ---------------------------------------------------------------------------


def test_for_security_review_envelope_no_symbol():
    """Broad sweep — no symbol passed."""
    r = for_security_review(symbol="")
    _assert_compound_envelope(r, "for-security-review", "security_review")
    sections = r["summary"]["sections"]
    # taint + vuln + critique + adversarial should all be present
    # (each may individually have an error if the relevant data isn't
    # populated, but they should at least try to run).
    expected = {"taint", "vuln", "critique", "adversarial"}
    actual = set(sections) | {e["command"] for e in r.get("errors", [])}
    missing = expected - actual
    assert not missing, f"missing security sub-cmds: {missing}"


def test_for_security_review_with_symbol_target():
    r = for_security_review(symbol="analyze_n1")
    _assert_compound_envelope(r, "for-security-review", "security_review")
    assert r["summary"]["target"] == "analyze_n1"
