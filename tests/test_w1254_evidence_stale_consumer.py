"""W1254 - consumer wire-up for the W210 ``evidence_stale`` flag.

W210 added ``evidence_stale: bool`` and ``stale_reasons: tuple[str, ...]``
fields to :class:`ChangeEvidence` plus the :meth:`assurance_floor` /
:meth:`evidence_completeness` methods. W1234 shipped the producer wire-up
that flips ``evidence_stale`` when the run-ledger event stream shows
context-reads post-dating edit starts. Until W1254, the two consumer
methods did NOT read ``evidence_stale`` at all - the producer signal was
populated-but-unused.

W1254 lands the consumer side:

* :meth:`assurance_floor` - **additive** augmentation: adds ``stale`` +
  ``stale_reasons`` keys to the return dict. ``passes`` is UNCHANGED by
  staleness (MVA-floor coverage and staleness are distinct quality
  axes); consumers that care MUST gate on both. This keeps every
  downstream reader (esp. :mod:`roam.attest.vsa`) byte-stable on the
  ``passes`` / ``missing`` contract.

* :meth:`evidence_completeness` - **integrated** penalty: when
  ``evidence_stale=True``, every ``"complete"`` Q is demoted to
  ``"partial"`` so the totals honestly reflect that the structured data,
  while present, is no-longer-trustworthy as a "complete" signal. The
  eight-Q invariant ``complete + partial + missing + not_applicable ==
  8`` is preserved; ``stale`` + ``stale_reasons`` are exposed alongside
  the totals so a consumer that reads only the dict can detect the
  demotion.

Hash-stability invariant: both methods are READ-ONLY computed properties
that do NOT modify packet fields and do NOT touch canonical-JSON / the
content-hash. W1254 cannot perturb stored hashes - this test pins that
contract too.
"""

from __future__ import annotations

import dataclasses

from roam.evidence import (
    ActorRef,
    AuthorityRef,
    ChangeEvidence,
    EvidenceArtifact,
    EvidenceSubject,
)


def _fresh_full_packet() -> ChangeEvidence:
    """Build a fully-populated, non-stale packet (the W1254 baseline).

    Mirrors the ``full`` packet in the W210 ``test_evidence_v0`` drift
    guard: all 8 evidence questions are answered (Q1..Q8 = complete) and
    ``evidence_stale`` defaults to ``False``.
    """
    subj = EvidenceSubject(kind="symbol", qualified_name="src/x.py::f")
    art = EvidenceArtifact(
        artifact_id="report:rep",
        kind="report",
        content_inline="ok",
    )
    return ChangeEvidence(
        evidence_id="ev_w1254_full",
        actor_refs=(ActorRef(actor_kind="agent", actor_id="agent:a"),),
        authority_refs=(AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit"),),
        context_refs=(art,),
        changed_subjects=(subj,),
        risk_level="low",
        policy_decisions=({"rule": "r1", "decision": "allow"},),
        tests_run=({"id": "t1", "outcome": "passed"},),
        approvals=({"id": "a1"},),
        findings=({"finding_id_str": "f1", "claim": "x"},),
    )


# ---------------------------------------------------------------------------
# assurance_floor - additive ``stale`` augmentation
# ---------------------------------------------------------------------------


def test_assurance_floor_fresh_packet_stale_false() -> None:
    """Non-stale packet: ``stale=False`` and ``stale_reasons==()``.

    The additive augmentation MUST not change the existing ``passes``
    / ``missing`` contract on a non-stale packet.
    """
    packet = _fresh_full_packet()
    floor = packet.assurance_floor()
    # Pre-W1254 contract preserved.
    assert floor["passes"] is True
    assert floor["missing"] == ()
    # W1254 additions.
    assert floor["stale"] is False
    assert floor["stale_reasons"] == ()


def test_assurance_floor_stale_packet_passes_independent_of_stale() -> None:
    """Stale-but-MVA-complete packet: ``passes=True`` AND ``stale=True``.

    Principled separation of quality axes: staleness is not a coverage
    gap, so it does NOT pull ``passes`` down. Consumers that care about
    both axes (e.g. ``roam.attest.vsa._verification_result``) gate on
    both signals.
    """
    fresh = _fresh_full_packet()
    stale = dataclasses.replace(
        fresh,
        evidence_stale=True,
        stale_reasons=("context_read_at (2026-05-16T10:00:00Z) >= edits_started_at (2026-05-16T09:30:00Z)",),
    )
    floor = stale.assurance_floor()
    # MVA-floor coverage is independent of staleness.
    assert floor["passes"] is True
    assert floor["missing"] == ()
    # Stale signal surfaces additively.
    assert floor["stale"] is True
    assert floor["stale_reasons"] == (
        "context_read_at (2026-05-16T10:00:00Z) >= edits_started_at (2026-05-16T09:30:00Z)",
    )


def test_assurance_floor_stale_bare_packet_still_fails_floor() -> None:
    """Stale + bare packet: ``passes=False`` AND ``stale=True``.

    Sanity check on the additive design: making a below-floor packet
    stale does NOT magically lift it past the floor (and vice-versa).
    """
    bare = ChangeEvidence(
        evidence_id="ev_w1254_stale_bare",
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits",),
    )
    floor = bare.assurance_floor()
    assert floor["passes"] is False
    # All six axes are missing on a bare packet.
    assert set(floor["missing"]) == {
        "actor",
        "authority",
        "changed_subjects",
        "findings",
        "verification",
        "policy_state",
    }
    assert floor["stale"] is True
    assert floor["stale_reasons"] == ("preflight_older_than_edits",)


# ---------------------------------------------------------------------------
# evidence_completeness - integrated demotion penalty
# ---------------------------------------------------------------------------


def test_evidence_completeness_fresh_packet_unchanged() -> None:
    """Non-stale packet: completeness table identical to pre-W1254 shape.

    Drift guard: the W1254 staleness penalty path MUST not fire on a
    fresh packet. Existing consumers (``banner.py``,
    ``cmd_evidence_doctor``, ``cmd_evidence_diff``) keep the same
    counts they would have read pre-W1254.
    """
    packet = _fresh_full_packet()
    table = packet.evidence_completeness()
    # All 8 questions complete (the W210 ``test_evidence_completeness_8q_table``
    # contract).
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert table[q] == "complete", f"{q} expected complete, got {table[q]}"
    assert table["complete"] == 8
    assert table["partial"] == 0
    assert table["missing"] == 0
    assert table["not_applicable"] == 0
    # W1254 additions reflect non-stale.
    assert table["stale"] is False
    assert table["stale_reasons"] == ()


def test_evidence_completeness_stale_complete_packet_demoted() -> None:
    """Stale + otherwise-complete packet: 8 complete demote to 8 partial.

    The single most important W1254 test. A packet that would have
    classified as STRONG coverage (``complete >= 7`` per
    ``banner.classify_evidence_coverage``) when fresh MUST instead
    classify as PARTIAL when stale, with the demotion visible on the
    per-Q table. Eight-Q invariant preserved.
    """
    fresh = _fresh_full_packet()
    stale = dataclasses.replace(
        fresh,
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits", "tests_pre_diff"),
    )
    table = stale.evidence_completeness()
    # Every Q that was complete is now partial.
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert table[q] == "partial", f"{q} expected partial (stale demotion), got {table[q]}"
    assert table["complete"] == 0
    assert table["partial"] == 8
    assert table["missing"] == 0
    assert table["not_applicable"] == 0
    # Eight-Q invariant preserved.
    assert (table["complete"] + table["partial"] + table["missing"] + table["not_applicable"]) == 8
    # Staleness signal surfaces alongside the table.
    assert table["stale"] is True
    assert table["stale_reasons"] == (
        "preflight_older_than_edits",
        "tests_pre_diff",
    )


def test_evidence_completeness_stale_partial_packet_no_double_demote() -> None:
    """Stale packet with mixed Q-states: only ``complete`` Qs demote.

    Tests the surgical scope of the W1254 demotion: ``partial``,
    ``missing``, and ``not_applicable`` Qs are NOT touched by the
    staleness penalty (they were never claiming "complete" trust).
    """
    # Build a partial-mix packet: Q1 partial (agent_id only), Q2
    # partial (mode only), Q3 missing, Q4 missing, Q5 not_applicable
    # (SAFE + no findings), Q6 missing, Q7 partial (tests_required
    # only), Q8 partial (redactions only).
    partial = ChangeEvidence(
        evidence_id="ev_w1254_stale_partial",
        agent_id="agent:a",
        mode="safe_edit",
        verdict="SAFE",
        tests_required=("tests/test_x.py",),
        redactions=("policy",),
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits",),
    )
    table = partial.evidence_completeness()
    # The same per-Q states as the W210 partial-mix test: no Q was
    # ``complete`` to begin with, so no demotion fires.
    assert table["Q1"] == "partial"
    assert table["Q2"] == "partial"
    assert table["Q3"] == "missing"
    assert table["Q4"] == "missing"
    assert table["Q5"] == "not_applicable"
    assert table["Q6"] == "missing"
    assert table["Q7"] == "partial"
    assert table["Q8"] == "partial"
    assert table["complete"] == 0
    assert table["partial"] == 4
    assert table["missing"] == 3
    assert table["not_applicable"] == 1
    # Staleness signal still surfaces.
    assert table["stale"] is True
    assert table["stale_reasons"] == ("preflight_older_than_edits",)


# ---------------------------------------------------------------------------
# Hash-stability invariant: consumer methods are pure computed reads.
# ---------------------------------------------------------------------------


def test_w1254_consumer_methods_do_not_perturb_wire_format() -> None:
    """``assurance_floor()`` and ``evidence_completeness()`` are pure reads.

    Both methods are computed properties; calling them MUST NOT modify
    the packet (frozen dataclass would raise) and MUST NOT change the
    canonical JSON or content hash. This is the load-bearing hash-
    stability contract for W1254: the consumer wire-up does NOT cross
    the wire-format boundary.
    """
    # Fresh packet: pin canonical bytes + hash, then call consumer methods.
    fresh = _fresh_full_packet().with_content_hash()
    canon_before = fresh.to_canonical_json()
    hash_before = fresh.content_hash
    # Call both consumer methods - they MUST not mutate anything.
    fresh.assurance_floor()
    fresh.evidence_completeness()
    # Canonical JSON + hash unchanged.
    assert fresh.to_canonical_json() == canon_before
    assert fresh.content_hash == hash_before

    # Stale packet: same contract.
    stale = dataclasses.replace(
        _fresh_full_packet(),
        evidence_stale=True,
        stale_reasons=("context_read_at >= edits_started_at",),
    ).with_content_hash()
    canon_stale_before = stale.to_canonical_json()
    hash_stale_before = stale.content_hash
    stale.assurance_floor()
    stale.evidence_completeness()
    assert stale.to_canonical_json() == canon_stale_before
    assert stale.content_hash == hash_stale_before
