"""W279 - tests for the ``PolicyDecision`` dataclass and the
``ChangeEvidence.policy_decisions`` normalisation hook.

Covers:

* Closed-enum validation on ``PolicyDecision.decision``.
* ``from_dict`` minimal-shape acceptance.
* ``to_dict`` byte-stable round-trip (2-key and full-key rows).
* ``ChangeEvidence`` accepts both dict rows and ``PolicyDecision``
  instances; the resulting packet's ``content_hash`` matches either
  way.

The golden-hash byte-stability check for the on-disk fixtures lives in
``tests/test_evidence_schema_migration.py`` (31 tests, all still pass
after W279).
"""

from __future__ import annotations

import pytest

from roam.evidence import (
    POLICY_DECISIONS,
    ChangeEvidence,
    PolicyDecision,
)

# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


def test_policy_decision_construction_validates_decision_enum() -> None:
    """Unknown decision literals MUST raise at construction."""
    with pytest.raises(ValueError, match="POLICY_DECISIONS"):
        PolicyDecision(rule_id="r1", decision="not-a-real-decision")


def test_policy_decision_construction_accepts_every_closed_enum_literal() -> None:
    """Every literal in ``POLICY_DECISIONS`` MUST construct cleanly."""
    for verdict in POLICY_DECISIONS:
        # Constructs without raising.
        pd = PolicyDecision(rule_id="r1", decision=verdict)
        assert pd.decision == verdict


def test_policy_decision_rejects_empty_rule_id() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        PolicyDecision(rule_id="", decision="pass")


def test_policy_decision_rejects_non_string_rule_id() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        PolicyDecision(rule_id=123, decision="pass")  # type: ignore[arg-type]


def test_policy_decision_rejects_empty_decision() -> None:
    with pytest.raises(ValueError, match="decision"):
        PolicyDecision(rule_id="r1", decision="")


def test_policy_decision_rejects_empty_subject_kind_string() -> None:
    with pytest.raises(ValueError, match="subject_kind"):
        PolicyDecision(rule_id="r1", decision="pass", subject_kind="")


def test_policy_decision_rejects_empty_evidence_ref_string() -> None:
    with pytest.raises(ValueError, match="evidence_ref"):
        PolicyDecision(rule_id="r1", decision="pass", evidence_ref="")


# ---------------------------------------------------------------------------
# from_dict / minimal-row acceptance
# ---------------------------------------------------------------------------


def test_policy_decision_from_dict_with_minimal_fields() -> None:
    """Two-key minimum row produces None scalars + empty extra."""
    pd = PolicyDecision.from_dict({"rule_id": "r1", "decision": "pass"})
    assert pd.rule_id == "r1"
    assert pd.decision == "pass"
    assert pd.subject is None
    assert pd.subject_kind is None
    assert pd.evidence_ref is None
    assert pd.extra == {}


def test_policy_decision_from_dict_requires_rule_id() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        PolicyDecision.from_dict({"decision": "pass"})


def test_policy_decision_from_dict_requires_decision() -> None:
    with pytest.raises(ValueError, match="decision"):
        PolicyDecision.from_dict({"rule_id": "r1"})


def test_policy_decision_from_dict_stuffs_unknown_keys_into_extra() -> None:
    """Producer rows carry many free-form keys; they end up in ``extra``."""
    pd = PolicyDecision.from_dict(
        {
            "rule_id": "constitution:before_edit",
            "decision": "not_evaluated",
            "evidence_ref": "constitution:before_edit",
            "command_count": 3,
        }
    )
    assert pd.rule_id == "constitution:before_edit"
    assert pd.decision == "not_evaluated"
    assert pd.evidence_ref == "constitution:before_edit"
    assert pd.extra == {"command_count": 3}


# ---------------------------------------------------------------------------
# Byte-stable round-trip (2-key, full-key, producer-shaped rows)
# ---------------------------------------------------------------------------


def test_policy_decision_round_trip_byte_identical_minimal() -> None:
    """Two-key minimum row -> from_dict -> to_dict MUST be the same 2 keys."""
    src = {"rule_id": "r1", "decision": "pass"}
    pd = PolicyDecision.from_dict(src)
    assert pd.to_dict() == src
    # AND no extra null/empty keys leak in:
    assert set(pd.to_dict().keys()) == {"rule_id", "decision"}


def test_policy_decision_round_trip_full_fields() -> None:
    """Six-key row round-trips byte-identically (all five first-class fields
    + one ``extra`` key).
    """
    src = {
        "rule_id": "lease:lease_20260513_1ba1fe",
        "decision": "allow",
        "subject": ["src/auth/login.py", "src/auth/session.py"],
        "subject_kind": "file",
        "evidence_ref": "lease:lease_20260513_1ba1fe",
        "state": "active",
    }
    pd = PolicyDecision.from_dict(src)
    assert pd.to_dict() == src


def test_policy_decision_round_trip_real_producer_constitution() -> None:
    """Matches the cmd_pr_replay constitution gatherer shape verbatim."""
    src = {
        "rule_id": "constitution:before_edit",
        "decision": "not_evaluated",
        "evidence_ref": "constitution:before_edit",
        "command_count": 3,
    }
    assert PolicyDecision.from_dict(src).to_dict() == src


def test_policy_decision_round_trip_real_producer_permit() -> None:
    """Matches the cmd_pr_replay permit gatherer shape verbatim."""
    src = {
        "rule_id": "permit:permit-x",
        "decision": "allow",
        "evidence_ref": "permit:permit-x",
        "expires_at": "2026-12-31T23:59:59Z",
        "scope": "high_blast_radius",
    }
    assert PolicyDecision.from_dict(src).to_dict() == src


def test_policy_decision_round_trip_real_producer_audit_trail() -> None:
    """Matches the audit-trail-verify per-issue policy_decisions shape."""
    src = {
        "rule_id": "audit_trail_chain_integrity",
        "decision": "fail",
        "evidence_ref": "artifact:audit-trail:run_abc",
        "issue_kind": "computed_prev_mismatch",
        "entry_index": 7,
        "expected_prev": "deadbeef",
        "computed_prev": "cafebabe",
        "timestamp": "2026-05-14T12:00:00Z",
    }
    assert PolicyDecision.from_dict(src).to_dict() == src


def test_policy_decision_round_trip_real_producer_rules_with_severity() -> None:
    """Matches the rules-envelope flattener shape verbatim."""
    src = {
        "rule_id": "no_unguarded_io",
        "decision": "fail",
        "evidence_ref": "rule:no_unguarded_io",
        "severity": "high",
        "reason": "io call outside transaction boundary",
        "violation_count": 2,
    }
    assert PolicyDecision.from_dict(src).to_dict() == src


# ---------------------------------------------------------------------------
# ChangeEvidence normalisation hook + content-hash equivalence
# ---------------------------------------------------------------------------


def test_change_evidence_accepts_dict_rows_for_policy_decisions() -> None:
    """Passing dict rows MUST normalise to PolicyDecision internally AND
    the content_hash MUST match a packet built with PolicyDecision
    objects directly.
    """
    rows = [
        {
            "rule_id": "constitution:before_edit",
            "decision": "not_evaluated",
            "evidence_ref": "constitution:before_edit",
            "command_count": 3,
        },
        {
            "rule_id": "permit:permit-x",
            "decision": "allow",
            "evidence_ref": "permit:permit-x",
            "scope": "high_blast_radius",
        },
    ]
    pkt_from_dicts = ChangeEvidence(
        evidence_id="ev_w279_test",
        policy_decisions=tuple(rows),
    )
    pkt_from_objects = ChangeEvidence(
        evidence_id="ev_w279_test",
        policy_decisions=tuple(PolicyDecision.from_dict(r) for r in rows),
    )

    # Internal normalisation kicked in - dict rows became typed
    # PolicyDecisions on the dict-rows packet.
    assert all(isinstance(pd, PolicyDecision) for pd in pkt_from_dicts.policy_decisions)

    # Canonical-JSON bytes match - the content_hash is therefore equal.
    assert pkt_from_dicts.to_canonical_json() == pkt_from_objects.to_canonical_json()
    assert pkt_from_dicts.compute_content_hash() == pkt_from_objects.compute_content_hash()


def test_change_evidence_preserves_non_conforming_rows() -> None:
    """Hand-crafted rows missing ``rule_id`` (or with legacy keys like
    ``rule``) MUST pass through untouched so byte-stability holds for
    pre-W279 golden fixtures.
    """
    legacy_row = {"decision": "allow", "rule": "no_unguarded_io"}
    pkt = ChangeEvidence(
        evidence_id="ev_w279_legacy",
        policy_decisions=(legacy_row,),
    )
    # The row stayed a raw Mapping (no normalisation possible without
    # rule_id), so byte-stability with the v0_full golden is preserved.
    assert pkt.policy_decisions[0] == legacy_row
    assert not isinstance(pkt.policy_decisions[0], PolicyDecision)


def test_change_evidence_canonical_json_contains_flat_keys() -> None:
    """After serialising, policy_decisions entries appear in canonical
    JSON with FLAT top-level keys (no nested ``extra`` envelope).
    """
    import json

    pkt = ChangeEvidence(
        evidence_id="ev_w279_canonical",
        policy_decisions=(
            PolicyDecision.from_dict(
                {
                    "rule_id": "rule_a",
                    "decision": "pass",
                    "evidence_ref": "rule:rule_a",
                    "severity": "low",
                }
            ),
        ),
    )
    payload = json.loads(pkt.to_canonical_json())
    pd_row = payload["policy_decisions"][0]
    # Severity is flat at the top level, NOT nested under ``extra``.
    assert pd_row == {
        "rule_id": "rule_a",
        "decision": "pass",
        "evidence_ref": "rule:rule_a",
        "severity": "low",
    }
    assert "extra" not in pd_row


def test_change_evidence_round_trip_via_canonical_json() -> None:
    """Build packet from dict rows, serialise, reparse, content_hash matches."""
    rows = [
        {
            "rule_id": "lease:lease_xyz",
            "decision": "allow",
            "subject": ["a.py", "b.py"],
            "subject_kind": "file",
            "state": "active",
        },
    ]
    pkt = ChangeEvidence(
        evidence_id="ev_w279_rt",
        policy_decisions=tuple(rows),
    ).with_content_hash()

    # Rebuild from the canonical JSON dict-row shape (simulates a
    # consumer that parsed the packet off the wire).
    import json

    payload = json.loads(pkt.to_canonical_json())
    rebuilt_rows = payload["policy_decisions"]
    rebuilt = ChangeEvidence(
        evidence_id="ev_w279_rt",
        policy_decisions=tuple(rebuilt_rows),
    ).with_content_hash()
    assert rebuilt.content_hash == pkt.content_hash


# ---------------------------------------------------------------------------
# W279b drift-guard tests: invalid `decision` MUST raise when both keys present
# ---------------------------------------------------------------------------


def test_change_evidence_rejects_invalid_decision_when_rule_id_present() -> None:
    """Drift guard: invalid ``decision`` with both ``rule_id`` and
    ``decision`` present MUST raise.

    Pre-W279b, ``ChangeEvidence.__post_init__`` caught any ValueError
    from :meth:`PolicyDecision.from_dict` and silently preserved the
    row as a raw dict, which let producer typos (e.g.
    ``decision="approved"`` instead of ``"allow"``) leak through. The
    fix narrows the catch to rows that are MISSING ``rule_id`` or
    ``decision`` (truly legacy / partial shapes); modern rows that
    carry both keys must satisfy the closed-enum contract.
    """
    with pytest.raises(ValueError, match="POLICY_DECISIONS"):
        ChangeEvidence(
            evidence_id="ev_w279b_drift",
            policy_decisions=(
                # ``approved`` is NOT in POLICY_DECISIONS; the canonical
                # spelling is ``allow``. This is the exact silent-drift
                # bug the guard is designed to surface.
                {"rule_id": "r", "decision": "approved"},
            ),
        )


def test_change_evidence_preserves_legacy_dict_when_decision_missing() -> None:
    """Legacy compat: dict missing ``decision`` should NOT raise; preserved.

    Hand-crafted golden fixtures and pre-W279 producer rows sometimes
    use legacy keys (``rule`` instead of ``rule_id``) or omit
    ``decision`` entirely. The drift guard only fires when BOTH
    ``rule_id`` and ``decision`` are present + non-empty; partial
    shapes pass through untouched so byte-stability with pre-W279
    fixtures holds.
    """
    # Dict without a ``decision`` key at all - partial / legacy shape.
    packet = ChangeEvidence(
        evidence_id="ev_w279b_legacy",
        policy_decisions=(
            {"rule_id": "r"},  # No `decision` field.
        ),
    )
    # Did not raise; the row survived as-is (raw Mapping, not
    # normalised to PolicyDecision).
    assert len(packet.policy_decisions) == 1
    assert packet.policy_decisions[0] == {"rule_id": "r"}

    # Same legacy contract holds for the ``rule`` (no ``rule_id``)
    # shape that the v0_full golden fixture relies on.
    packet2 = ChangeEvidence(
        evidence_id="ev_w279b_legacy2",
        policy_decisions=({"rule": "no_unguarded_io", "decision": "allow"},),
    )
    assert len(packet2.policy_decisions) == 1
    assert packet2.policy_decisions[0] == {
        "rule": "no_unguarded_io",
        "decision": "allow",
    }
