"""W280 packet-size budget tests.

The W280 directive adds a soft size budget on the canonical-JSON
serialisation of a ``ChangeEvidence`` packet. The contract:

* Default budget = 256 KiB (262144 bytes) - tunable via
  ``PACKET_SIZE_BUDGET_BYTES``.
* ``_apply_size_budget()`` is pure: within-budget packets return
  ``self``; over-budget packets return a NEW ``ChangeEvidence`` with
  truncation applied in the documented order.
* Truncation order is the W280 contract (see
  ``_BUDGET_TRUNCATION_STEPS``): drop ``artifacts[].content_inline``,
  then ``context_refs[].content_inline``, then
  ``policy_decisions[].extra``, then ``findings[].evidence``, then
  ``actor_refs[].extra``.
* Truncation appends ``"size_limit"`` to ``redactions`` (dedup-safe).
* Truncation runs BEFORE :meth:`with_content_hash` computes the digest,
  so stored hashes reflect the POST-truncation packet.
* Determinism: same input -> same output (no randomness, no field-
  order dependence).
* ``oversized_after_truncation`` is a WARN-equivalent state, not a
  FAIL - the packet is still parseable; just bloated.

These tests are pure dataclass exercises; no DB, no filesystem.
"""

from __future__ import annotations

import dataclasses

from roam.evidence import (
    PACKET_BUDGET_STATES,
    PACKET_SIZE_BUDGET_BYTES,
    ActorRef,
    ChangeEvidence,
    EvidenceArtifact,
    PolicyDecision,
    classify_packet_budget,
    packet_size_bytes,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _small_packet() -> ChangeEvidence:
    """Build a small within-budget packet (well under 256 KiB)."""
    return ChangeEvidence(
        evidence_id="evidence:small-1",
        repo_id="roam-code",
        git_range="HEAD~1..HEAD",
        commit_sha="abc123",
        diff_hash="def456",
        verdict="PASS",
        agent_id="agent:test",
    )


def _oversized_packet_with_inline_artifacts(*, artifact_count: int = 50, inline_size: int = 8 * 1024) -> ChangeEvidence:
    """Build a packet whose ``artifacts[].content_inline`` exceeds budget.

    50 artifacts each carrying 8 KiB inline = ~400 KiB of inline content,
    safely above the 256 KiB budget.
    """
    big_text = "x" * inline_size
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"raw:{i}",
            kind="raw_envelope",
            content_inline=big_text,
        )
        for i in range(artifact_count)
    )
    return ChangeEvidence(
        evidence_id="evidence:oversized-1",
        artifacts=artifacts,
    )


def _oversized_after_truncation_packet() -> ChangeEvidence:
    """Build a packet that's still over budget after every drop step.

    We achieve this by bloating a field that NO truncation step drops:
    ``approvals[]`` is typed ``Mapping[str, Any]`` and not in the drop
    list. Each row carries 8 KiB of free text; 50 rows = ~400 KiB.
    """
    big_text = "y" * (8 * 1024)
    approvals = tuple({"approval_id": f"approval-{i}", "rationale": big_text} for i in range(50))
    return ChangeEvidence(
        evidence_id="evidence:incompressible-1",
        approvals=approvals,
    )


# ---------------------------------------------------------------------------
# Budget constant + state classifier
# ---------------------------------------------------------------------------


def test_packet_size_budget_constant_is_256_kib() -> None:
    """The W280 budget constant is 256 KiB = 262144 bytes."""
    assert PACKET_SIZE_BUDGET_BYTES == 256 * 1024 == 262144


def test_packet_budget_states_closed_tuple() -> None:
    """The closed-enum tuple contains the three documented states."""
    assert PACKET_BUDGET_STATES == (
        "within_budget",
        "truncated",
        "oversized_after_truncation",
    )


def test_classify_packet_budget_at_boundary() -> None:
    """Exactly-at-budget classifies as within_budget; one byte over is
    oversized_after_truncation."""
    assert classify_packet_budget(0) == "within_budget"
    assert classify_packet_budget(PACKET_SIZE_BUDGET_BYTES) == "within_budget"
    assert classify_packet_budget(PACKET_SIZE_BUDGET_BYTES + 1) == "oversized_after_truncation"


# ---------------------------------------------------------------------------
# _apply_size_budget — within budget
# ---------------------------------------------------------------------------


def test_within_budget_packet_unchanged() -> None:
    """A small packet returns SELF from _apply_size_budget (no allocation)."""
    p = _small_packet()
    result = p._apply_size_budget()
    assert result is p  # identity: same object, no copy
    # Sanity: size is well within budget
    assert packet_size_bytes(p) < PACKET_SIZE_BUDGET_BYTES


def test_within_budget_packet_no_size_limit_redaction() -> None:
    """A within-budget packet doesn't get 'size_limit' stamped."""
    p = _small_packet()
    budgeted = p._apply_size_budget()
    assert "size_limit" not in budgeted.redactions


# ---------------------------------------------------------------------------
# _apply_size_budget — truncation
# ---------------------------------------------------------------------------


def test_oversized_packet_drops_artifact_content_inline() -> None:
    """After _apply_size_budget, artifact.content_inline is cleared."""
    p = _oversized_packet_with_inline_artifacts()
    pre_size = packet_size_bytes(p)
    assert pre_size > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()

    # All artifacts now have content_inline = None
    assert all(a.content_inline is None for a in budgeted.artifacts)
    # Resulting packet fits the budget
    post_size = packet_size_bytes(budgeted)
    assert post_size <= PACKET_SIZE_BUDGET_BYTES


def test_oversized_packet_appends_size_limit_redaction() -> None:
    """Truncation stamps 'size_limit' on redactions."""
    p = _oversized_packet_with_inline_artifacts()
    budgeted = p._apply_size_budget()
    assert "size_limit" in budgeted.redactions


def test_size_limit_redaction_not_double_appended() -> None:
    """Re-truncation of an already-stamped packet doesn't duplicate."""
    p = _oversized_packet_with_inline_artifacts()
    once = p._apply_size_budget()
    # Re-run: 'size_limit' is already there, must not duplicate
    twice = once._apply_size_budget()
    size_limit_count = sum(1 for r in twice.redactions if r == "size_limit")
    assert size_limit_count == 1


def test_size_limit_dedup_when_producer_pre_stamps() -> None:
    """A producer that pre-stamps 'size_limit' gets a single entry, not two."""
    # Build an oversized packet with size_limit already in redactions
    big_text = "x" * (8 * 1024)
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"raw:{i}",
            kind="raw_envelope",
            content_inline=big_text,
        )
        for i in range(50)
    )
    p = ChangeEvidence(
        evidence_id="evidence:pre-stamped",
        artifacts=artifacts,
        redactions=("size_limit",),
    )
    budgeted = p._apply_size_budget()
    size_limit_count = sum(1 for r in budgeted.redactions if r == "size_limit")
    assert size_limit_count == 1


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_truncation_is_deterministic() -> None:
    """Truncating the same oversized packet twice yields identical bytes + hash."""
    p = _oversized_packet_with_inline_artifacts()

    a = p._apply_size_budget()
    b = p._apply_size_budget()

    # Byte-identical canonical JSON
    assert a.to_canonical_json() == b.to_canonical_json()
    # Identical content hash (computed independently)
    assert a.compute_content_hash() == b.compute_content_hash()


# ---------------------------------------------------------------------------
# with_content_hash() integration
# ---------------------------------------------------------------------------


def test_with_content_hash_uses_post_truncation_hash() -> None:
    """The stamped hash matches sha256(post-truncation canonical JSON)."""
    p = _oversized_packet_with_inline_artifacts()
    stamped = p.with_content_hash()

    # The stamped hash matches the post-truncation packet's recompute
    # (with content_hash stripped, as compute_content_hash does internally).
    stripped = dataclasses.replace(stamped, content_hash=None)
    expected = stripped.compute_content_hash()
    assert stamped.content_hash == expected

    # The stamped packet is also within budget (since truncation happened)
    assert packet_size_bytes(stamped) <= PACKET_SIZE_BUDGET_BYTES

    # And 'size_limit' is present
    assert "size_limit" in stamped.redactions


def test_with_content_hash_within_budget_packet_byte_stable() -> None:
    """Within-budget packets pass through with_content_hash without truncation.

    Pre-W280 packets and W280 packets that fit the budget must produce
    byte-identical content_hash so stored hashes stay valid.
    """
    p = _small_packet()

    # Compute the hash via with_content_hash (the W280 path: applies
    # budget first) AND via the direct compute_content_hash path (the
    # pre-W280 path: no budget). They MUST agree because no truncation
    # fires.
    via_w280 = p.with_content_hash().content_hash
    direct = p.compute_content_hash()
    assert via_w280 == direct


# ---------------------------------------------------------------------------
# Oversized-after-truncation marker
# ---------------------------------------------------------------------------


def test_oversized_after_truncation_marker() -> None:
    """A packet that's STILL over budget after every drop step is flagged.

    Uses a packet whose bulk lives in ``approvals[]`` (not in any
    drop-target field) so all 5 truncation steps are no-ops and the
    final size still exceeds the budget. The orchestrator still stamps
    ``size_limit`` (per the W280 spec: "stamp the size_limit redaction
    anyway") and classify_packet_budget reports
    oversized_after_truncation.
    """
    p = _oversized_after_truncation_packet()
    assert packet_size_bytes(p) > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()

    # Still over budget (no drop step found anything to drop)
    final_size = packet_size_bytes(budgeted)
    assert final_size > PACKET_SIZE_BUDGET_BYTES

    # State classifier reports oversized_after_truncation
    assert classify_packet_budget(final_size) == "oversized_after_truncation"

    # 'size_limit' is still stamped (the W280 "stamp anyway" rule)
    assert "size_limit" in budgeted.redactions


# ---------------------------------------------------------------------------
# Truncation step coverage
# ---------------------------------------------------------------------------


def test_truncation_drops_context_ref_content_inline() -> None:
    """Step 2: context_refs[].content_inline is cleared when artifacts
    weren't enough to fit the budget.

    We synthesize a packet where artifacts alone fit but context_refs
    push it over the line, then verify the second step fires.
    """
    big_text = "z" * (8 * 1024)
    context_refs = tuple(
        EvidenceArtifact(
            artifact_id=f"ctx:{i}",
            kind="raw_envelope",
            content_inline=big_text,
        )
        for i in range(50)
    )
    p = ChangeEvidence(
        evidence_id="evidence:ctx-oversized",
        context_refs=context_refs,
    )
    assert packet_size_bytes(p) > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()
    assert all(c.content_inline is None for c in budgeted.context_refs)
    assert "size_limit" in budgeted.redactions


def test_truncation_drops_policy_decision_extra() -> None:
    """Step 3: policy_decisions[].extra is cleared on PolicyDecision rows."""
    big_extra = {"detail": "w" * (8 * 1024)}
    pd_rows = tuple(
        PolicyDecision(
            rule_id=f"rule-{i}",
            decision="pass",
            extra=big_extra,
        )
        for i in range(50)
    )
    p = ChangeEvidence(
        evidence_id="evidence:pd-oversized",
        policy_decisions=pd_rows,
    )
    assert packet_size_bytes(p) > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()
    for row in budgeted.policy_decisions:
        assert isinstance(row, PolicyDecision)
        assert row.extra == {}
    assert "size_limit" in budgeted.redactions


def test_truncation_drops_finding_evidence() -> None:
    """Step 4: findings[].evidence sub-dict is cleared if present."""
    big_evidence = {"detail": "v" * (8 * 1024)}
    findings = tuple({"id": f"finding-{i}", "evidence": dict(big_evidence)} for i in range(50))
    p = ChangeEvidence(
        evidence_id="evidence:findings-oversized",
        findings=findings,
    )
    assert packet_size_bytes(p) > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()
    for row in budgeted.findings:
        assert row.get("evidence") == {}
    assert "size_limit" in budgeted.redactions


def test_truncation_drops_actor_ref_extra() -> None:
    """Step 5: actor_refs[].extra is cleared when it's populated."""
    big_extra = {"session_blob": "u" * (8 * 1024)}
    actor_refs = tuple(
        ActorRef(
            actor_kind="agent",
            actor_id=f"agent:test-{i}",
            extra=big_extra,
        )
        for i in range(50)
    )
    p = ChangeEvidence(
        evidence_id="evidence:actor-oversized",
        actor_refs=actor_refs,
    )
    assert packet_size_bytes(p) > PACKET_SIZE_BUDGET_BYTES

    budgeted = p._apply_size_budget()
    for ref in budgeted.actor_refs:
        assert ref.extra == {}
    assert "size_limit" in budgeted.redactions


# ---------------------------------------------------------------------------
# Within-budget protection: redactions tuple is NEVER dropped
# ---------------------------------------------------------------------------


def test_redactions_is_never_dropped() -> None:
    """Pre-existing redactions survive truncation; size_limit is appended."""
    p = ChangeEvidence(
        evidence_id="evidence:keep-redactions",
        artifacts=tuple(
            EvidenceArtifact(
                artifact_id=f"raw:{i}",
                kind="raw_envelope",
                content_inline="x" * (8 * 1024),
            )
            for i in range(50)
        ),
        redactions=("secret", "pii"),
    )
    budgeted = p._apply_size_budget()
    # Original redactions preserved
    assert "secret" in budgeted.redactions
    assert "pii" in budgeted.redactions
    # size_limit added
    assert "size_limit" in budgeted.redactions
