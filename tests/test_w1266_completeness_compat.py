"""W1266 - shared raw-dict completeness compat helper.

Tests for ``src/roam/evidence/completeness_compat.py``:

* The shared helper produces identical output across both consumers
  (cmd_evidence_doctor + cmd_evidence_diff) for the same input.
* A stale packet sees every ``"complete"`` Q demoted to ``"partial"``
  (W1254 stale-demotion penalty), while ``"not_applicable"`` is
  preserved.
* A pre-W210 packet (no ``evidence_stale`` field) sees no demotion -
  the algorithm reads the field defensively via ``.get(...)``.
* The shared helper agrees with the canonical
  ``ChangeEvidence.evidence_completeness()`` for non-stale packets
  built from the dataclass.
"""

from __future__ import annotations

from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.completeness_compat import (
    classify_completeness,
    compute_completeness,
)
from roam.evidence.refs import ActorRef, AuthorityRef
from roam.evidence.subject import EvidenceSubject


def _strong_packet_dict() -> dict:
    """A raw-dict shape that scores 7+ Qs complete (STRONG banner).

    Mirrors what a producer-emitted packet looks like before
    ``ChangeEvidence`` reconstruction.
    """
    return {
        "schema_version": "1.0.0",
        "evidence_id": "ev_w1266_strong",
        "actor_refs": [
            {"actor_kind": "human", "actor_id": "human:alice"},
            {"actor_kind": "agent", "actor_id": "agent:claude"},
        ],
        "authority_refs": [
            {"authority_kind": "mode", "authority_id": "mode:safe_edit"},
        ],
        "context_refs": [
            {"kind": "report", "artifact_id": "report:abc"},
        ],
        "changed_subjects": [
            {"kind": "symbol", "qualified_name": "src/auth.py::login"},
        ],
        "risk_level": "low",
        "policy_decisions": [
            {"rule": "no_unguarded_io", "decision": "allow"},
        ],
        "tests_run": [
            {"id": "tests/test_auth.py::test_login", "outcome": "passed"},
        ],
        "approvals": [
            {"approver": "bob@example.com"},
        ],
    }


# ---------------------------------------------------------------------------
# (1) The shared helper produces identical output across both consumers
# ---------------------------------------------------------------------------


def test_shared_helper_used_by_doctor_and_diff_produces_identical_q_dict():
    """cmd_evidence_doctor and cmd_evidence_diff import the SAME helper -
    they cannot drift from each other anymore. Calling the helper from
    both import paths returns the same dict for the same input."""
    # Both consumers import from completeness_compat; the doctor takes
    # classify_completeness, the diff takes compute_completeness. Both
    # share the same underlying algorithm. We assert: the per-question
    # dict produced by both is the same for an identical input.
    from roam.commands.cmd_evidence_diff import (
        compute_completeness as diff_compute,
    )
    from roam.commands.cmd_evidence_doctor import (
        classify_completeness as doctor_classify,
    )

    pkt = _strong_packet_dict()

    doctor_q, _doctor_totals = doctor_classify(pkt)
    diff_q = diff_compute(pkt)

    assert doctor_q == diff_q
    # Sanity: the strong-shape packet scores Q1..Q4 + Q6 + Q7 + Q8
    # complete; Q5 is "complete" because risk_level is set. So all 8
    # questions are complete.
    assert doctor_q["Q1"] == "complete"
    assert doctor_q["Q2"] == "complete"
    assert doctor_q["Q5"] == "complete"
    assert doctor_q["Q8"] == "complete"


# ---------------------------------------------------------------------------
# (2) Stale packet -> complete demoted to partial; not_applicable preserved
# ---------------------------------------------------------------------------


def test_stale_packet_demotes_complete_to_partial():
    """When ``evidence_stale=True`` the W1254 penalty demotes every
    ``complete`` Q to ``partial``. The eight-Q invariant
    (complete + partial + missing + not_applicable == 8) holds."""
    fresh = _strong_packet_dict()
    fresh_q, fresh_totals = classify_completeness(fresh)
    assert fresh_totals["complete"] == 8
    assert fresh_totals["partial"] == 0

    stale = dict(fresh)
    stale["evidence_stale"] = True
    stale["stale_reasons"] = ["context_read_at older than 24h"]
    stale_q, stale_totals = classify_completeness(stale)

    # Every Q that was complete is now partial. None remain complete.
    assert stale_totals["complete"] == 0
    assert stale_totals["partial"] == 8
    assert stale_totals["missing"] == 0
    assert stale_totals["not_applicable"] == 0
    # Invariant: 8 Qs total.
    assert sum(stale_totals.values()) == 8
    # Per-question check: every Q dropped from "complete" to "partial".
    for q_key in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert fresh_q[q_key] == "complete"
        assert stale_q[q_key] == "partial"


def test_stale_packet_preserves_not_applicable():
    """``not_applicable`` is NOT demoted - a Q that does not apply
    remains inapplicable regardless of staleness. Q5 is the canonical
    case: SAFE verdict + no findings + no risk_level -> N/A."""
    # Build a packet whose Q5 is N/A: verdict=SAFE, no findings, no
    # risk_level. Other Qs stay missing - only Q5's classification
    # matters for this test.
    pkt = {
        "schema_version": "1.0.0",
        "evidence_id": "ev_w1266_na",
        "verdict": "SAFE",
        "findings": [],
    }
    fresh_q = compute_completeness(pkt)
    assert fresh_q["Q5"] == "not_applicable"

    stale = dict(pkt)
    stale["evidence_stale"] = True
    stale_q = compute_completeness(stale)
    # Q5 stays N/A even under staleness demotion.
    assert stale_q["Q5"] == "not_applicable"


def test_report_artifact_is_partial_verification_but_attestation_is_complete():
    """Raw-dict scoring mirrors Q7's artifact-kind distinction."""
    report_only = {
        "artifacts": [
            {"artifact_id": "report:one", "kind": "report"},
        ],
    }
    assert compute_completeness(report_only)["Q7"] == "partial"

    attested = {
        "artifacts": [
            {"artifact_id": "attestation:one", "kind": "attestation"},
        ],
    }
    assert compute_completeness(attested)["Q7"] == "complete"


# ---------------------------------------------------------------------------
# (3) Pre-W210 packets (no evidence_stale field) -> no demotion
# ---------------------------------------------------------------------------


def test_pre_w210_packet_no_demotion():
    """A pre-W210 packet (no ``evidence_stale`` key at all) reads as
    fresh - no demotion applies. The helper defensively reads the field
    via .get() so the absence of the key is treated as "not stale"."""
    pre_w210 = _strong_packet_dict()
    # Sanity: confirm the test fixture doesn't accidentally carry the
    # W210 field.
    assert "evidence_stale" not in pre_w210

    q, totals = classify_completeness(pre_w210)
    # All 8 Qs remain complete.
    assert totals["complete"] == 8
    assert totals["partial"] == 0


def test_evidence_stale_false_no_demotion():
    """Explicit ``evidence_stale=False`` also produces no demotion -
    same as the absent-key path."""
    pkt = _strong_packet_dict()
    pkt["evidence_stale"] = False
    q, totals = classify_completeness(pkt)
    assert totals["complete"] == 8
    assert totals["partial"] == 0


# ---------------------------------------------------------------------------
# (4) Shared helper agrees with ChangeEvidence.evidence_completeness()
# ---------------------------------------------------------------------------


def test_helper_agrees_with_change_evidence_method_on_fresh_packet():
    """The shared raw-dict helper and
    ``ChangeEvidence.evidence_completeness()`` produce the SAME per-Q
    table when given the same input. This pins the canonical mandate:
    the helper exists to operate on raw dicts (older / newer schemas)
    but its algorithm is the same as the dataclass method."""
    ev = ChangeEvidence(
        evidence_id="ev_w1266_dc",
        actor_refs=[
            ActorRef(actor_kind="human", actor_id="human:alice"),
        ],
        authority_refs=[
            AuthorityRef(
                authority_kind="mode",
                authority_id="mode:safe_edit",
                source="mode",
            ),
        ],
        changed_subjects=[
            EvidenceSubject(
                kind="symbol",
                qualified_name="src/auth.py::login",
            ),
        ],
        risk_level="low",
    )
    # Method-bound projection (dataclass path).
    method_q = ev.evidence_completeness()

    # Raw-dict projection. ``ChangeEvidence`` doesn't expose a
    # canonical-JSON-via-dict helper used by the doctor / diff, so we
    # build the equivalent shape by hand for the fields the helper
    # consults.
    pkt = {
        "actor_refs": [{"actor_kind": "human", "actor_id": "human:alice"}],
        "authority_refs": [
            {
                "authority_kind": "mode",
                "authority_id": "mode:safe_edit",
                "source": "mode",
            },
        ],
        "changed_subjects": [
            {"kind": "symbol", "qualified_name": "src/auth.py::login"},
        ],
        "risk_level": "low",
    }
    helper_q = compute_completeness(pkt)

    # Compare Q1..Q8 only - the method also includes "stale" /
    # "stale_reasons" / totals keys that the helper doesn't emit.
    for q_key in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert method_q[q_key] == helper_q[q_key], f"{q_key}: method={method_q[q_key]!r} vs helper={helper_q[q_key]!r}"


def test_helper_agrees_with_change_evidence_method_on_stale_packet():
    """Same as above but with ``evidence_stale=True``. Both paths must
    apply the W1254 demotion identically; this test was the load-bearing
    regression before W1266 (the helper omitted the demotion entirely)."""
    ev = ChangeEvidence(
        evidence_id="ev_w1266_dc_stale",
        actor_refs=[
            ActorRef(actor_kind="human", actor_id="human:alice"),
        ],
        risk_level="low",
        evidence_stale=True,
        stale_reasons=("context_read_at older than 24h",),
    )
    method_q = ev.evidence_completeness()

    pkt = {
        "actor_refs": [{"actor_kind": "human", "actor_id": "human:alice"}],
        "risk_level": "low",
        "evidence_stale": True,
        "stale_reasons": ["context_read_at older than 24h"],
    }
    helper_q = compute_completeness(pkt)

    # Q1 + Q5 were complete on the dataclass; both must demote on both
    # paths.
    for q_key in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert method_q[q_key] == helper_q[q_key], f"{q_key}: method={method_q[q_key]!r} vs helper={helper_q[q_key]!r}"
    # Sanity: at least one Q dropped from complete to partial via
    # demotion.
    assert helper_q["Q1"] == "partial"
    assert helper_q["Q5"] == "partial"
