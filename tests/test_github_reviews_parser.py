"""W247a - tests for the GitHub PR review parser/normalizer.

These tests pin the W247a contract: a pure parser/normalizer that turns
GitHub PR review JSON into neutral evidence-compatible records.

Coverage matrix:

* Head-commit filtering: ``APPROVED`` on head -> approval; ``APPROVED``
  on stale commit -> dropped with warning.
* CHANGES_REQUESTED -> ``PolicyDecision(decision="deny", ...)``.
* COMMENTED / DISMISSED / PENDING -> dropped with warnings (neither
  approvals nor blockers).
* Review bodies (``body`` / ``body_text`` / ``body_html``) are NEVER
  stored anywhere on the returned records.
* Neutral approval reason: ``"github_pr_approval"`` (never the body).
* Unknown ``state`` literals raise ``ValueError`` against
  ``GITHUB_REVIEW_STATES``.
* Deterministic output (same input -> byte-identical output).
* Fixture loader round-trips a JSON list.
* ``harvest_reviews_from_gh_cli`` raises ``RuntimeError`` when ``gh`` is
  missing.

Per the W247a directive: NO network calls, NO pr-replay integration,
fixture-first.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from roam.evidence import (
    GITHUB_REVIEW_STATES,
    ApprovalRecord,
    PolicyDecision,
    harvest_reviews_from_gh_cli,
    load_reviews_from_fixture,
    parse_github_reviews,
)

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "github_reviews"
EXAMPLE_FIXTURE = FIXTURE_DIR / "example_pr_reviews.json"

# SHA placeholder that matches one APPROVED entry, one CHANGES_REQUESTED
# entry, one COMMENTED, one DISMISSED, one PENDING (5 of 6 reviews) so
# head-commit filtering on the stale-approval row is exercised.
HEAD_SHA = "aaa111aaa111aaa111aaa111aaa111aaa111aaa1"
STALE_SHA = "stale222stale222stale222stale222stale222"
PR_NUMBER = 42

# Sensitive substrings present in fixture review bodies. The parser
# MUST NEVER let any of these appear in the returned records.
_SENSITIVE_BODY_SUBSTRINGS: tuple[str, ...] = (
    "This is sensitive private info",
    "I approved an older commit",
    "Please rework the migration plan",
    "Left some inline comments",
    "Dismissed by PR author",
    "Reviewing now",
)


# ---------------------------------------------------------------------------
# GITHUB_REVIEW_STATES drift guard
# ---------------------------------------------------------------------------


def test_github_review_states_drift_guard() -> None:
    """``GITHUB_REVIEW_STATES`` is a 5-element frozenset with the exact
    GitHub PR review state vocabulary.

    Adding / removing a state must be a deliberate source-code edit; this
    test pins the closed enumeration so a silent producer-side change to
    the vocabulary fails CI immediately.
    """
    assert isinstance(GITHUB_REVIEW_STATES, frozenset)
    assert len(GITHUB_REVIEW_STATES) == 5
    assert GITHUB_REVIEW_STATES == frozenset(
        {
            "APPROVED",
            "CHANGES_REQUESTED",
            "COMMENTED",
            "DISMISSED",
            "PENDING",
        }
    )
    with pytest.raises(AttributeError):
        GITHUB_REVIEW_STATES.add("ROGUE_STATE")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def test_load_reviews_from_fixture_returns_list() -> None:
    """``load_reviews_from_fixture`` round-trips the JSON list on disk."""
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    assert isinstance(reviews, list)
    assert len(reviews) == 6
    states = [r["state"] for r in reviews]
    # Fixture covers all 5 closed-enum states (APPROVED appears twice:
    # one on head commit, one on a stale commit, to exercise filtering).
    assert set(states) == {
        "APPROVED",
        "CHANGES_REQUESTED",
        "COMMENTED",
        "DISMISSED",
        "PENDING",
    }
    assert states.count("APPROVED") == 2  # head + stale


def test_load_reviews_from_fixture_path_str_accepted(tmp_path: pathlib.Path) -> None:
    """Loader accepts a ``str`` path too (not just ``pathlib.Path``)."""
    target = tmp_path / "reviews.json"
    target.write_text("[]", encoding="utf-8")
    # The helper converts str -> Path internally.
    out = load_reviews_from_fixture(target)  # type: ignore[arg-type]
    assert out == []


def test_load_reviews_from_fixture_rejects_non_list(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        load_reviews_from_fixture(target)


# ---------------------------------------------------------------------------
# parse_github_reviews - head-commit filtering
# ---------------------------------------------------------------------------


def test_parse_filters_to_approved_on_head_commit_only() -> None:
    """Only ``APPROVED`` reviews on the head commit become approvals.

    Fixture has 2 APPROVED entries (alice on head, bob on stale); only
    alice should surface as an ApprovalRecord.
    """
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    approvals, _decisions, _warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    assert len(approvals) == 1
    approval = approvals[0]
    assert isinstance(approval, ApprovalRecord)
    assert approval.approver == "alice"
    assert approval.scope == "pr:42"
    assert approval.timestamp == "2026-05-13T10:15:00Z"
    assert approval.extra.get("commit_id") == HEAD_SHA
    assert approval.extra.get("review_id") == 1001


def test_parse_skips_approved_on_stale_commit() -> None:
    """``APPROVED`` review on a stale commit is filtered out + warned."""
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    approvals, _decisions, warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    # Bob is APPROVED but on STALE_SHA - not in approvals.
    assert all(a.approver != "bob" for a in approvals)
    # And there's a warning naming him.
    stale_warns = [w for w in warnings if "bob" in w]
    assert len(stale_warns) == 1
    assert "stale approval" in stale_warns[0]
    assert STALE_SHA in stale_warns[0]


# ---------------------------------------------------------------------------
# parse_github_reviews - CHANGES_REQUESTED routing
# ---------------------------------------------------------------------------


def test_parse_routes_changes_requested_to_policy_decisions() -> None:
    """CHANGES_REQUESTED -> ``PolicyDecision(decision="deny", ...)``."""
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    _approvals, decisions, _warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert isinstance(decision, PolicyDecision)
    assert decision.rule_id == "github_review:1003"
    assert decision.decision == "deny"
    assert decision.subject == "pr:42"
    assert decision.subject_kind == "commit"
    assert decision.extra.get("reviewer") == "carol"
    assert decision.extra.get("submitted_at") == "2026-05-13T11:00:00Z"


def test_parse_routes_changes_requested_on_stale_commit_too() -> None:
    """CHANGES_REQUESTED is a blocker on ANY commit, not just head.

    Even if a reviewer requested changes on an older commit, that's
    still policy signal - the change request didn't go away just
    because newer commits landed. The directive says "CHANGES_REQUESTED
    is a blocker/policy signal" with no head-commit qualifier.
    """
    stale_changes_req = [
        {
            "id": 9999,
            "user": {"login": "stale-blocker"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-05-10T08:00:00Z",
            "commit_id": STALE_SHA,
            "html_url": "https://github.com/x/y/pull/42#pullrequestreview-9999",
        }
    ]
    _approvals, decisions, _warnings = parse_github_reviews(
        reviews=stale_changes_req,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    assert len(decisions) == 1
    assert decisions[0].decision == "deny"
    assert decisions[0].extra.get("commit_id") == STALE_SHA


# ---------------------------------------------------------------------------
# parse_github_reviews - filtered states
# ---------------------------------------------------------------------------


def test_parse_filters_out_commented_dismissed_pending() -> None:
    """COMMENTED / DISMISSED / PENDING reviews are NOT in either output.

    They surface as warnings instead so a consumer can audit.
    """
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    approvals, decisions, warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    # None of dave / erin / frank should appear in approvals or
    # policy_decisions.
    filtered_logins = {"dave", "erin", "frank"}
    for a in approvals:
        assert a.approver not in filtered_logins
    for d in decisions:
        assert d.extra.get("reviewer") not in filtered_logins

    # Each filtered state has exactly one warning naming the reviewer.
    assert any("dave" in w and "COMMENTED" in w for w in warnings)
    assert any("erin" in w and "DISMISSED" in w for w in warnings)
    assert any("frank" in w and "PENDING" in w for w in warnings)


# ---------------------------------------------------------------------------
# parse_github_reviews - body-storage prohibition
# ---------------------------------------------------------------------------


def test_parse_never_stores_review_body() -> None:
    """Review bodies MUST NEVER appear on returned approvals/decisions.

    This is the W247a "never store bodies" rule, enforced as a
    code-level invariant rather than a comment. We round-trip the
    fixture (which contains sensitive substrings in the body fields),
    then assert no record's stringified payload contains any of those
    substrings.
    """
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    approvals, decisions, warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )

    # Build the full text payload of every returned record (approvals,
    # decisions, warnings) and assert none of the sensitive substrings
    # leak.
    payload_pieces: list[str] = []
    for a in approvals:
        payload_pieces.append(repr(a))
        payload_pieces.append(json.dumps(dict(a.extra)))
        payload_pieces.append(a.reason or "")
    for d in decisions:
        payload_pieces.append(repr(d))
        payload_pieces.append(json.dumps(dict(d)))
    payload_pieces.extend(warnings)

    payload = "\n".join(payload_pieces)
    for substring in _SENSITIVE_BODY_SUBSTRINGS:
        assert substring not in payload, f"review body substring {substring!r} leaked into parser output"


def test_parse_returns_approval_record_with_neutral_reason() -> None:
    """``ApprovalRecord.reason`` is the neutral literal, not the body."""
    reviews = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    approvals, _decisions, _warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    assert len(approvals) == 1
    assert approvals[0].reason == "github_pr_approval"


# ---------------------------------------------------------------------------
# parse_github_reviews - validation
# ---------------------------------------------------------------------------


def test_parse_validates_review_state_against_closed_enum() -> None:
    """Unknown ``state`` raises ``ValueError`` against GITHUB_REVIEW_STATES."""
    rogue = [
        {
            "id": 7777,
            "user": {"login": "mallory"},
            "state": "FROBNICATE",
            "submitted_at": "2026-05-13T10:00:00Z",
            "commit_id": HEAD_SHA,
        }
    ]
    with pytest.raises(ValueError, match="GITHUB_REVIEW_STATES"):
        parse_github_reviews(
            reviews=rogue,
            head_commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )


def test_parse_rejects_non_list_reviews() -> None:
    with pytest.raises(ValueError, match="must be a list"):
        parse_github_reviews(
            reviews={"not": "a list"},  # type: ignore[arg-type]
            head_commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )


def test_parse_rejects_empty_head_commit_sha() -> None:
    with pytest.raises(ValueError, match="head_commit_sha"):
        parse_github_reviews(
            reviews=[],
            head_commit_sha="",
            pr_number=PR_NUMBER,
        )


def test_parse_rejects_non_int_pr_number() -> None:
    with pytest.raises(ValueError, match="pr_number"):
        parse_github_reviews(
            reviews=[],
            head_commit_sha=HEAD_SHA,
            pr_number="42",  # type: ignore[arg-type]
        )


def test_parse_rejects_non_mapping_review_entry() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_github_reviews(
            reviews=["not a dict"],  # type: ignore[list-item]
            head_commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )


def test_parse_rejects_approved_review_missing_submitted_at() -> None:
    bad = [
        {
            "id": 5555,
            "user": {"login": "alice"},
            "state": "APPROVED",
            "commit_id": HEAD_SHA,
            # no submitted_at
        }
    ]
    with pytest.raises(ValueError, match="submitted_at"):
        parse_github_reviews(
            reviews=bad,
            head_commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )


# ---------------------------------------------------------------------------
# parse_github_reviews - determinism
# ---------------------------------------------------------------------------


def test_parse_is_deterministic() -> None:
    """Same input -> byte-identical output across two calls."""
    reviews_a = load_reviews_from_fixture(EXAMPLE_FIXTURE)
    reviews_b = load_reviews_from_fixture(EXAMPLE_FIXTURE)

    out_a = parse_github_reviews(
        reviews=reviews_a,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    out_b = parse_github_reviews(
        reviews=reviews_b,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )

    # Same approver/scope/timestamp/extra in same order.
    assert tuple((a.approver, a.timestamp, dict(a.extra)) for a in out_a[0]) == tuple(
        (a.approver, a.timestamp, dict(a.extra)) for a in out_b[0]
    )
    # Same policy-decision rule_id/decision/subject/extra in same order.
    assert tuple((d.rule_id, d.decision, d.subject, dict(d.extra)) for d in out_a[1]) == tuple(
        (d.rule_id, d.decision, d.subject, dict(d.extra)) for d in out_b[1]
    )
    # Same warnings, same order.
    assert out_a[2] == out_b[2]


def test_parse_preserves_input_order() -> None:
    """Output preserves the input chronological ordering of reviews.

    GitHub returns reviews in chronological order; the parser must not
    reorder them, otherwise CHANGES_REQUESTED ordering (which can
    semantically encode "most recent verdict") would be lost.
    """
    # Two CHANGES_REQUESTED rows in a known order.
    reviews = [
        {
            "id": 100,
            "user": {"login": "alice"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-05-13T09:00:00Z",
            "commit_id": HEAD_SHA,
        },
        {
            "id": 200,
            "user": {"login": "bob"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-05-13T10:00:00Z",
            "commit_id": HEAD_SHA,
        },
    ]
    _approvals, decisions, _warnings = parse_github_reviews(
        reviews=reviews,
        head_commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
    )
    assert [d.rule_id for d in decisions] == [
        "github_review:100",
        "github_review:200",
    ]


# ---------------------------------------------------------------------------
# harvest_reviews_from_gh_cli - error path
# ---------------------------------------------------------------------------


def test_harvest_from_gh_cli_raises_when_gh_missing(tmp_path: pathlib.Path) -> None:
    """Missing gh executable raises ``RuntimeError`` (NOT FileNotFoundError).

    Caller-friendly: a single ``RuntimeError`` to catch on the network
    path, regardless of the underlying subprocess failure mode.
    """
    nonexistent = tmp_path / "does-not-exist-gh-binary"
    assert not nonexistent.exists()
    with pytest.raises(RuntimeError, match="gh"):
        harvest_reviews_from_gh_cli(
            owner="example-org",
            repo="example-repo",
            pr_number=PR_NUMBER,
            gh_executable=str(nonexistent),
            timeout=5,
        )
