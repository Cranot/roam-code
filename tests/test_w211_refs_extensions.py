"""W211 ref-extension tests.

Covers the five W211 directives layered onto ``src/roam/evidence/``:

1. ``ActorRef.trust_tier`` + ``ACTOR_TRUST_TIERS`` frozenset.
2. ``AuthorityRef.source`` + ``AUTHORITY_SOURCES`` frozenset and the
   W198 facade auto-stamp.
3. :class:`roam.evidence.approval.ApprovalRecord` first-class dataclass.
4. :func:`roam.evidence.stale_accepted_risks` helper.
5. ``NON-GOALS:`` docstring markers at every evidence model file.
"""

from __future__ import annotations

import pytest

from roam.evidence import (
    ACTOR_TRUST_TIERS,
    AUTHORITY_SOURCES,
    ActorRef,
    ApprovalRecord,
    AuthorityRef,
    ChangeEvidence,
    stale_accepted_risks,
)
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# 1. ActorRef trust tier
# ---------------------------------------------------------------------------


def test_actor_trust_tiers_frozenset() -> None:
    """ACTOR_TRUST_TIERS is the 5-member closed enumeration + immutable."""
    expected = {
        "verified_ci",
        "git_author",
        "local_env",
        "self_reported_agent",
        "unknown",
    }
    assert set(ACTOR_TRUST_TIERS) == expected
    assert isinstance(ACTOR_TRUST_TIERS, frozenset)
    with pytest.raises(AttributeError):
        ACTOR_TRUST_TIERS.add("rogue")  # type: ignore[attr-defined]


def test_actor_ref_default_trust_tier_is_unknown() -> None:
    """The most-conservative default - unset paths stay honest."""
    ref = ActorRef(actor_kind="agent", actor_id="agent:claude-opus-4.7")
    assert ref.trust_tier == "unknown"


def test_actor_ref_validates_trust_tier() -> None:
    """Unknown trust_tier raises ValueError naming the rejected literal."""
    with pytest.raises(ValueError, match="trust_tier"):
        ActorRef(
            actor_kind="agent",
            actor_id="agent:claude-opus-4.7",
            trust_tier="cryptographically_certain",
        )


def test_actor_ref_accepts_every_tier() -> None:
    """Every tier in ACTOR_TRUST_TIERS round-trips."""
    for tier in ACTOR_TRUST_TIERS:
        ref = ActorRef(
            actor_kind="agent",
            actor_id="agent:x",
            trust_tier=tier,
        )
        assert ref.trust_tier == tier


# ---------------------------------------------------------------------------
# 2. AuthorityRef source
# ---------------------------------------------------------------------------


def test_authority_sources_frozenset() -> None:
    """AUTHORITY_SOURCES is the 6-member closed enumeration + immutable."""
    expected = {
        "mode",
        "permit",
        "rule_config",
        "ci_policy",
        "human_approval",
        "inferred_fallback",
    }
    assert set(AUTHORITY_SOURCES) == expected
    assert isinstance(AUTHORITY_SOURCES, frozenset)
    with pytest.raises(AttributeError):
        AUTHORITY_SOURCES.add("rogue")  # type: ignore[attr-defined]


def test_authority_ref_default_source_inferred_fallback() -> None:
    """Default source is ``inferred_fallback`` per the W211 directive."""
    ref = AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit")
    assert ref.source == "inferred_fallback"


def test_authority_ref_validates_source() -> None:
    """Unknown source raises ValueError naming the rejected literal."""
    with pytest.raises(ValueError, match="source"):
        AuthorityRef(
            authority_kind="mode",
            authority_id="mode:safe_edit",
            source="vibes",
        )


def test_authority_ref_facade_extra_flag_for_permit_without_id() -> None:
    """source='permit' with no permit_id stamps extra['facade'] = True.

    Encodes the W198 fact: ``roam permit`` is currently verdict-only and
    does not persist a permit_id. The auto-stamp makes the facade
    nature explicit on the wire.
    """
    ref = AuthorityRef(
        authority_kind="permit",
        authority_id="permit:facade",
        source="permit",
    )
    assert ref.extra.get("facade") is True


def test_authority_ref_permit_with_persisted_id_no_facade_flag() -> None:
    """source='permit' WITH a permit_id does NOT stamp the facade flag."""
    ref = AuthorityRef(
        authority_kind="permit",
        authority_id="permit:perm_20260514_abc123",
        source="permit",
        extra={"permit_id": "perm_20260514_abc123"},
    )
    assert "facade" not in ref.extra


def test_authority_ref_non_permit_source_no_facade_flag() -> None:
    """source != 'permit' never auto-stamps the facade flag."""
    ref = AuthorityRef(
        authority_kind="approval",
        authority_id="approval:pr_42",
        source="human_approval",
    )
    assert "facade" not in ref.extra


# ---------------------------------------------------------------------------
# 3. ApprovalRecord first-class dataclass
# ---------------------------------------------------------------------------


def test_approval_record_round_trips() -> None:
    """ApprovalRecord constructs, validates, and round-trips through asdict."""
    import dataclasses

    rec = ApprovalRecord(
        approver="human:alice@example.com",
        scope="high_blast_radius",
        timestamp="2026-05-14T12:00:00Z",
        reason="reviewed call graph manually",
        expiry="2026-06-14T12:00:00Z",
        risk_accepted="r_n_plus_one_in_checkout",
        extra={"pr": 42},
    )
    payload = dataclasses.asdict(rec)
    assert payload["approver"] == "human:alice@example.com"
    assert payload["scope"] == "high_blast_radius"
    assert payload["timestamp"] == "2026-05-14T12:00:00Z"
    assert payload["expiry"] == "2026-06-14T12:00:00Z"
    assert payload["risk_accepted"] == "r_n_plus_one_in_checkout"
    assert payload["extra"] == {"pr": 42}


def test_approval_record_requires_non_empty_fields() -> None:
    """Empty approver/scope/timestamp raise ValueError."""
    with pytest.raises(ValueError, match="approver"):
        ApprovalRecord(approver="", scope="x", timestamp="2026-05-14T12:00:00Z")
    with pytest.raises(ValueError, match="scope"):
        ApprovalRecord(approver="x", scope="", timestamp="2026-05-14T12:00:00Z")
    with pytest.raises(ValueError, match="timestamp"):
        ApprovalRecord(approver="x", scope="y", timestamp="")


def test_approval_record_validates_timestamp_iso() -> None:
    """Unparseable timestamps raise ValueError."""
    with pytest.raises(ValueError, match="timestamp"):
        ApprovalRecord(
            approver="human:alice",
            scope="high_blast_radius",
            timestamp="not-a-date",
        )


def test_approval_record_validates_expiry_iso() -> None:
    """Unparseable expiry strings raise ValueError."""
    with pytest.raises(ValueError, match="expiry"):
        ApprovalRecord(
            approver="human:alice",
            scope="high_blast_radius",
            timestamp="2026-05-14T12:00:00Z",
            expiry="next tuesday",
        )


def test_approval_record_is_expired() -> None:
    """is_expired() handles past/future/none correctly."""
    # Past expiry: stale.
    past = ApprovalRecord(
        approver="human:alice",
        scope="x",
        timestamp="2026-05-01T00:00:00Z",
        expiry="2026-05-10T00:00:00Z",
    )
    assert past.is_expired(now_iso="2026-05-14T00:00:00Z") is True

    # Future expiry: fresh.
    fresh = ApprovalRecord(
        approver="human:alice",
        scope="x",
        timestamp="2026-05-01T00:00:00Z",
        expiry="2027-05-10T00:00:00Z",
    )
    assert fresh.is_expired(now_iso="2026-05-14T00:00:00Z") is False

    # No expiry: never stale.
    never = ApprovalRecord(
        approver="human:alice",
        scope="x",
        timestamp="2026-05-01T00:00:00Z",
    )
    assert never.is_expired(now_iso="2026-05-14T00:00:00Z") is False
    # Even "now" with no expiry is not stale.
    assert never.is_expired() is False


def test_approval_record_now_default_uses_utc_now() -> None:
    """is_expired() without now_iso uses current UTC time."""
    # An expiry far in the past must register as expired against any
    # plausible "now".
    rec = ApprovalRecord(
        approver="human:alice",
        scope="x",
        timestamp="2000-01-01T00:00:00Z",
        expiry="2000-01-02T00:00:00Z",
    )
    assert rec.is_expired() is True
    # And one far in the future must register as fresh.
    future_rec = ApprovalRecord(
        approver="human:alice",
        scope="x",
        timestamp="2026-05-14T00:00:00Z",
        expiry="2999-01-02T00:00:00Z",
    )
    assert future_rec.is_expired() is False


# ---------------------------------------------------------------------------
# 4. stale_accepted_risks helper
# ---------------------------------------------------------------------------


def test_stale_accepted_risks_filter() -> None:
    """Stale entries filter: known-stale + known-fresh + no-expiry mix."""
    packet = ChangeEvidence(
        evidence_id="ev_w211",
        accepted_risks=(
            # Stale (expiry in the past).
            {"id": "r_stale", "expiry": "2026-04-01T00:00:00Z"},
            # Fresh (expiry in the future).
            {"id": "r_fresh", "expiry": "2027-04-01T00:00:00Z"},
            # No expiry - never stale.
            {"id": "r_perpetual"},
            # Explicit None expiry - same as missing.
            {"id": "r_none", "expiry": None},
        ),
    )
    stale = stale_accepted_risks(packet, now_iso="2026-05-14T00:00:00Z")
    stale_ids = {entry["id"] for entry in stale}
    assert stale_ids == {"r_stale"}


def test_stale_accepted_risks_malformed_expiry_treated_as_stale() -> None:
    """Unparseable expiry surfaces as stale so producer notices."""
    packet = ChangeEvidence(
        evidence_id="ev_w211_bad",
        accepted_risks=({"id": "r_bad", "expiry": "definitely-not-iso"},),
    )
    stale = stale_accepted_risks(packet, now_iso="2026-05-14T00:00:00Z")
    assert len(stale) == 1
    assert stale[0]["id"] == "r_bad"


def test_stale_accepted_risks_empty_packet() -> None:
    """No accepted_risks means an empty result tuple, not None."""
    packet = ChangeEvidence(evidence_id="ev_w211_empty")
    stale = stale_accepted_risks(packet)
    assert stale == ()


def test_stale_accepted_risks_default_now_uses_utc() -> None:
    """When now_iso is omitted, the helper uses current UTC time."""
    far_past_iso = "2000-01-01T00:00:00Z"
    far_future_iso = "2999-01-01T00:00:00Z"
    packet = ChangeEvidence(
        evidence_id="ev_w211_now",
        accepted_risks=(
            {"id": "r_old", "expiry": far_past_iso},
            {"id": "r_new", "expiry": far_future_iso},
        ),
    )
    stale = stale_accepted_risks(packet)
    stale_ids = {entry["id"] for entry in stale}
    assert stale_ids == {"r_old"}


# ---------------------------------------------------------------------------
# 5. NON-GOALS docstring marker at every model file
# ---------------------------------------------------------------------------


def test_non_goals_docstrings_present_at_every_model() -> None:
    """Every model file declares its NON-GOALS at module level.

    Soft-conformance scan: each evidence-model file in
    ``src/roam/evidence/`` must include the literal marker
    ``NON-GOALS:`` in its module docstring (or another module-level
    comment block). The marker is a deliberate textual anchor so
    reviewers and future authors can grep for it.
    """
    root = repo_root() / "src" / "roam" / "evidence"
    targets = {
        "change_evidence.py",
        "refs.py",
        "artifact.py",
        "mcp_receipt.py",
        "approval.py",
    }
    missing: list[str] = []
    for name in sorted(targets):
        path = root / name
        text = path.read_text(encoding="utf-8")
        if "NON-GOALS:" not in text:
            missing.append(name)
    assert not missing, f"NON-GOALS marker missing in: {missing}"
