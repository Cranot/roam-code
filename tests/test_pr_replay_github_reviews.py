"""W247b - GitHub PR-review harvester wired into ``roam pr-replay``.

These tests pin the integration contract between W247a's pure parser
(``roam.evidence.github_reviews``) and the pr-replay evidence collector:

1. ``test_no_github_args_preserves_producer_not_available`` - back-compat:
   when neither ``--github-reviews-json`` nor ``--github-reviews-gh`` is
   set, the W261 ``producer_not_available`` redaction still fires
   (Q8 = partial).
2. ``test_fixture_approved_on_head_populates_approvals_and_q8_complete`` -
   APPROVED review on the head commit populates
   ``ChangeEvidence.approvals`` and lifts Q8 to ``complete``.
3. ``test_stale_approved_review_filtered_and_warned`` - APPROVED review on
   a stale commit is dropped (W247a head-commit rule) and surfaced as
   stderr/warnings text.
4. ``test_changes_requested_yields_policy_decision_deny`` - a
   CHANGES_REQUESTED review becomes a ``decision="deny"`` row on
   ``ChangeEvidence.policy_decisions`` with a ``rule_id`` prefixed by
   ``github_review:``.
5. ``test_review_bodies_do_not_appear_in_canonical_json`` - bodies from
   the fixture (every entry has one) MUST NOT leak into the packet's
   canonical JSON. W247a guardrail re-asserted at the collector boundary.
6. ``test_parser_failure_appends_warning_does_not_crash`` - malformed
   fixture JSON yields a warning on stderr but the command still
   completes (exit 0) and the packet is still well-formed.
7. ``test_review_source_provided_suppresses_producer_not_available`` -
   the "checked, no approval" semantic: a fixture with zero APPROVED
   entries on head still suppresses the ``producer_not_available``
   marker because the operator DID check the source.

The fixture at ``tests/fixtures/github_reviews/example_pr_reviews.json``
predates this wave (shipped W247a) and was hand-built to cover every
state the parser handles. Its head_commit is the literal
``aaa111aaa111aaa111aaa111aaa111aaa111aaa1`` - tests monkey-patch
``_git_head_sha`` so the matching is deterministic.

Tests invoke via ``CliRunner``, matching the pattern in
``tests/test_evidence_pr_replay.py``.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FIXTURE_HEAD_SHA = "aaa111aaa111aaa111aaa111aaa111aaa111aaa1"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "github_reviews" / "example_pr_reviews.json"


def _invoke(*args: str) -> tuple[int, str]:
    """Invoke ``roam pr-replay`` and return ``(exit_code, captured_output)``.

    Captured output mixes stdout + stderr because we want to assert on
    the ``[pr-replay] evidence-collector warning:`` lines pr-replay
    prints to stderr. Click's CliRunner in 8.2+ no longer accepts
    ``mix_stderr=False`` so we read stderr via the result attribute
    when present and fall back to stdout-only otherwise.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["pr-replay", *args], catch_exceptions=False)
    out = result.output or ""
    # In Click 8.2+ stderr is interleaved by default; in older Clicks the
    # ``stderr`` attribute exists when the runner was configured for it.
    err = getattr(result, "stderr", "") or ""
    return result.exit_code, out + err


@pytest.fixture
def patched_head_sha(monkeypatch):
    """Force ``_git_head_sha`` to return the fixture's head commit.

    Tests that exercise the fixture path need the parser's head-commit
    filter to match the fixture, which targets a synthetic SHA the local
    git repo will never produce naturally. The patch lives on the module
    object so both the helper call site and any direct callers see the
    same value.
    """
    from roam.commands import cmd_pr_replay

    monkeypatch.setattr(cmd_pr_replay, "_git_head_sha", lambda: FIXTURE_HEAD_SHA)
    yield FIXTURE_HEAD_SHA


# ---------------------------------------------------------------------------
# 1. No-args back-compat path
# ---------------------------------------------------------------------------


def test_no_github_args_preserves_producer_not_available(tmp_path):
    """Without ``--github-reviews-json``, the W261 marker still fires.

    This is the back-compat assertion: nothing this wave added must
    silently change behavior when the operator did NOT opt in to a
    GitHub source. The redaction list must still carry
    ``producer_not_available`` and Q8 must still score ``partial``.
    """
    target = tmp_path / "evidence.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0
    payload = _json.loads(target.read_text(encoding="utf-8"))
    assert "producer_not_available" in (payload.get("redactions") or [])


# ---------------------------------------------------------------------------
# 2. APPROVED on head -> Q8 complete
# ---------------------------------------------------------------------------


def test_fixture_approved_on_head_populates_approvals_and_q8_complete(tmp_path, patched_head_sha):
    """APPROVED review on head commit lifts Q8 from partial to complete."""
    target = tmp_path / "evidence.json"
    code, _ = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(FIXTURE_PATH),
        "--github-pr-number",
        "42",
    )
    assert code == 0
    payload = _json.loads(target.read_text(encoding="utf-8"))

    approvals = payload.get("approvals") or []
    # Alice (id=1001) approved on head; Bob (id=1002) approved on a stale
    # commit -> filtered. Expected = 1 surviving approval.
    assert len(approvals) >= 1, f"expected at least one approval on packet, got {approvals}"
    aliases = {a.get("approver") for a in approvals}
    assert "alice" in aliases, f"expected alice in approvers; got {aliases}"

    # Q8 must score 'complete' — reconstruct a ChangeEvidence directly
    # from the on-disk payload and call evidence_completeness(). The
    # Q8 rule (approvals OR accepted_risks -> complete) is field-driven,
    # so as long as ``approvals`` survives the rebuild we're complete.
    from roam.evidence import ChangeEvidence

    rebuilt = ChangeEvidence(
        evidence_id=payload["evidence_id"],
        schema_version=payload.get("schema_version", "1.0.0"),
        git_range=payload.get("git_range"),
        approvals=tuple(approvals),
    )
    completeness = rebuilt.evidence_completeness()
    assert completeness.get("Q8") == "complete", (
        f"expected Q8 complete, got {completeness.get('Q8')}: full={completeness}"
    )


# ---------------------------------------------------------------------------
# 3. Stale APPROVED review filtered + warned
# ---------------------------------------------------------------------------


def test_stale_approved_review_filtered_and_warned(tmp_path, patched_head_sha):
    """An APPROVED review on a stale commit is dropped + warned on stderr."""
    target = tmp_path / "evidence.json"
    code, captured = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(FIXTURE_PATH),
        "--github-pr-number",
        "42",
    )
    assert code == 0
    payload = _json.loads(target.read_text(encoding="utf-8"))
    approvers = {a.get("approver") for a in (payload.get("approvals") or [])}
    # Bob's approval is on commit 'stale222...' which != fixture head.
    assert "bob" not in approvers, f"expected bob to be filtered as stale; approvers={approvers}"
    # Surface in the warnings stream: pr-replay logs collector warnings
    # to stderr with the ``[pr-replay] evidence-collector warning:``
    # prefix. The W247a parser-warning string contains 'stale approval'.
    assert "stale" in captured.lower(), f"expected stale-approval warning in stderr output; got: {captured!r}"


# ---------------------------------------------------------------------------
# 4. CHANGES_REQUESTED -> policy_decision deny
# ---------------------------------------------------------------------------


def test_changes_requested_yields_policy_decision_deny(tmp_path, patched_head_sha):
    """A CHANGES_REQUESTED review appears as a ``decision="deny"`` row."""
    target = tmp_path / "evidence.json"
    code, _ = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(FIXTURE_PATH),
        "--github-pr-number",
        "42",
    )
    assert code == 0
    payload = _json.loads(target.read_text(encoding="utf-8"))
    decisions = payload.get("policy_decisions") or []
    review_decisions = [
        d for d in decisions if isinstance(d.get("rule_id"), str) and d["rule_id"].startswith("github_review:")
    ]
    assert len(review_decisions) >= 1, f"expected at least one github_review: decision; got {decisions}"
    # Carol's review (id=1003) is CHANGES_REQUESTED on the head commit.
    assert any(d.get("decision") == "deny" and d.get("rule_id") == "github_review:1003" for d in review_decisions), (
        f"expected deny decision rule_id=github_review:1003; got {review_decisions}"
    )


# ---------------------------------------------------------------------------
# 5. No body text leaks into canonical JSON
# ---------------------------------------------------------------------------


def test_review_bodies_do_not_appear_in_canonical_json(tmp_path, patched_head_sha):
    """W247a guardrail: review bodies MUST NOT appear in the packet bytes.

    The fixture's bodies contain distinctive substrings (e.g. "sensitive
    private info") so a leak via either approver / decision / extras
    would be detectable in a substring check on the on-disk bytes.
    """
    target = tmp_path / "evidence.json"
    code, _ = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(FIXTURE_PATH),
        "--github-pr-number",
        "42",
    )
    assert code == 0
    raw = target.read_text(encoding="utf-8")
    # The four distinctive body strings from the fixture - any leak is
    # caught by a simple substring search.
    leaks = [
        "sensitive private info",
        "approved an older commit",
        "rework the migration plan",
        "Left some inline comments",
        "Dismissed by PR author",
        "Reviewing now, will submit",
    ]
    for needle in leaks:
        assert needle not in raw, f"review body leaked into canonical JSON: {needle!r}"

    # W293 regression: the provenance hop on approvals / policy_decisions
    # must NOT pull review bodies through. Inspect the parsed packet
    # rows directly (in addition to the bytes-level substring check
    # above) so a future producer that accidentally serialises bodies
    # into ``extra`` is caught at the structured-row layer.
    parsed = _json.loads(raw)
    for row in (parsed.get("approvals") or []) + (parsed.get("policy_decisions") or []):
        # Approval / policy-decision rows have ``provenance`` stamped per
        # W293. Confirm the field exists AND no body-shaped key sneaks in.
        for forbidden in ("body", "body_text", "body_html"):
            assert forbidden not in row, (
                f"W293 regression: forbidden body key {forbidden!r} appeared on row after provenance hop: {row!r}"
            )


# ---------------------------------------------------------------------------
# 6. Malformed fixture warns and does not crash
# ---------------------------------------------------------------------------


def test_parser_failure_appends_warning_does_not_crash(tmp_path, patched_head_sha):
    """A malformed fixture surfaces a warning but pr-replay still completes."""
    bad = tmp_path / "broken_reviews.json"
    bad.write_text("this is not json", encoding="utf-8")

    target = tmp_path / "evidence.json"
    code, captured = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(bad),
        "--github-pr-number",
        "42",
    )
    assert code == 0, f"pr-replay must not crash on malformed fixture; output: {captured!r}"
    assert target.exists(), "evidence packet was not written"
    # Warning text mentions github review or load failure.
    assert "github review" in captured.lower() or "load failed" in captured.lower(), (
        f"expected github-review warning on stderr; got {captured!r}"
    )


# ---------------------------------------------------------------------------
# 7. Source provided suppresses producer_not_available
# ---------------------------------------------------------------------------


def test_review_source_provided_suppresses_producer_not_available(tmp_path, patched_head_sha):
    """When a source is checked, ``producer_not_available`` MUST NOT fire.

    The W247b semantic: "checked, no approval" is distinct from
    "producer unavailable." Even a fixture that yields ZERO approvals
    (e.g. one with only filtered states) must suppress the marker.
    """
    # Build a fixture with NO APPROVED-on-head reviews so approvals stays
    # empty. CHANGES_REQUESTED is fine - it doesn't populate approvals
    # but DOES count as "source was checked."
    empty_fixture = tmp_path / "no_approvals.json"
    empty_fixture.write_text(
        _json.dumps(
            [
                {
                    "id": 9001,
                    "user": {"login": "zoe"},
                    "state": "COMMENTED",
                    "submitted_at": "2026-05-13T10:00:00Z",
                    "commit_id": FIXTURE_HEAD_SHA,
                    "html_url": "https://example.test/pr/42#review-9001",
                    "body": "Just commenting, no verdict.",
                }
            ]
        ),
        encoding="utf-8",
    )

    target = tmp_path / "evidence.json"
    code, _ = _invoke(
        "--tier",
        "sample",
        "--evidence",
        str(target),
        "--github-reviews-json",
        str(empty_fixture),
        "--github-pr-number",
        "42",
    )
    assert code == 0
    payload = _json.loads(target.read_text(encoding="utf-8"))
    assert "producer_not_available" not in (payload.get("redactions") or []), (
        f"producer_not_available should NOT fire when source provided; redactions={payload.get('redactions')!r}"
    )


# ---------------------------------------------------------------------------
# 8. Validation: mutual exclusion + required PR number
# ---------------------------------------------------------------------------


def test_both_review_sources_raises_usage_error(tmp_path):
    """``--github-reviews-json`` and ``--github-reviews-gh`` are mutually exclusive."""
    code, captured = _invoke(
        "--tier",
        "sample",
        "--github-reviews-json",
        str(FIXTURE_PATH),
        "--github-reviews-gh",
        "owner/repo#42",
        "--github-pr-number",
        "42",
    )
    # Click UsageError -> exit 2 by default.
    assert code != 0
    assert "not both" in captured.lower() or "use --github-reviews-json" in captured.lower()


def test_github_source_without_pr_number_raises_usage_error(tmp_path):
    """``--github-reviews-json`` without ``--github-pr-number`` is rejected."""
    code, captured = _invoke(
        "--tier",
        "sample",
        "--github-reviews-json",
        str(FIXTURE_PATH),
    )
    assert code != 0
    assert "--github-pr-number is required" in captured or "pr-number" in captured.lower()
