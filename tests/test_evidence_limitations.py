"""W284 — unit tests for the packet-derived evidence-limitations section.

The renderer in ``roam.commands.cmd_pr_replay._render_evidence_limitations``
delegates the actual bullet generation to ``_derive_limitations`` so the
projection logic can be unit-tested without touching the Markdown
rendering surface. These tests pin the three-source derivation contract:

1. **Per-Q gaps** from ``ChangeEvidence.evidence_completeness()``.
2. **Redaction reasons** from ``ChangeEvidence.redactions``.
3. **Trust-tier warnings** from ``ChangeEvidence.actor_refs`` whose
   ``trust_tier`` is ``self_reported_agent`` or ``unknown``.

Pure unit tests — no CLI invocation, no filesystem. The renderer-level
test (synthetic packet -> Markdown -> assert bullets) lives in
``tests/test_evidence_pr_replay.py``.
"""

from __future__ import annotations

import dataclasses


def _make_packet(**overrides):
    """Build a minimal ChangeEvidence packet with optional overrides.

    Defaults match the "empty packet" used elsewhere in the evidence
    test suite — content_hash is invalidated by ``dataclasses.replace``
    when callers override packet contents.
    """
    from roam.evidence import ChangeEvidence

    base = ChangeEvidence(
        evidence_id="test:w284",
        git_range="HEAD~1..HEAD",
        verdict="clean",
        risk_level="low",
    )
    if overrides:
        return dataclasses.replace(base, **overrides)
    return base


# ---------------------------------------------------------------------------
# Strong / pristine packet — no bullets derived from any of the 3 sources.
# ---------------------------------------------------------------------------


def test_limitations_empty_when_packet_is_strong_and_pristine():
    """STRONG packet with no redactions and verified_ci actors -> ()."""
    from roam.commands.cmd_pr_replay import _derive_limitations
    from roam.evidence import EvidenceSubject
    from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="ci_runner",
                actor_id="ci_runner:github/actions/runs/123",
                trust_tier="verified_ci",
            ),
        ),
        authority_refs=(
            AuthorityRef(
                authority_kind="mode",
                authority_id="mode:safe_edit",
            ),
        ),
        environment_refs=(
            EnvironmentRef(
                env_kind="ci_job",
                env_id="ci_job:gh/owner/repo/123",
            ),
        ),
        context_refs=(
            EvidenceSubject(kind="file", qualified_name="src/x.py"),
        ),
        changed_subjects=(
            EvidenceSubject(kind="symbol", qualified_name="x.fn"),
        ),
        policy_decisions=({"rule": "no_secrets", "passed": True},),
        tests_run=({"name": "t1", "outcome": "passed"},),
        approvals=({"actor": "alice", "ts": "2026-05-14T00:00:00Z"},),
    )

    assert _derive_limitations(packet) == ()


# ---------------------------------------------------------------------------
# Source 1: per-Q gap bullets (missing / partial).
# ---------------------------------------------------------------------------


def test_limitations_includes_missing_q_bullets():
    """Packet with Q3 + Q6 missing emits 2 bullets naming each Q.

    An empty packet scores Q1..Q8 mostly missing; we exercise this by
    asserting the SHAPE of the Q3 and Q6 bullets specifically.
    """
    from roam.commands.cmd_pr_replay import _derive_limitations

    bullets = _derive_limitations(_make_packet())

    q3 = [b for b in bullets if "Q3 (context_read): MISSING" in b]
    q6 = [b for b in bullets if "Q6 (policy): MISSING" in b]
    assert len(q3) == 1
    assert len(q6) == 1
    # Q3 should reference context_refs in the explanation.
    assert "context_refs" in q3[0]


def test_limitations_includes_partial_q_bullets():
    """Q8 partial with ``producer_not_available`` redaction -> PARTIAL bullet."""
    from roam.commands.cmd_pr_replay import _derive_limitations

    packet = _make_packet(redactions=("producer_not_available",))
    bullets = _derive_limitations(packet)

    q8_partials = [
        b for b in bullets if "Q8 (accept): PARTIAL" in b
    ]
    assert len(q8_partials) == 1
    # The W261 partial path mentions the redaction marker by name.
    assert "producer_not_available" in q8_partials[0]


def test_limitations_q_bullets_sorted_q1_to_q8():
    """Q-gap bullets are emitted in Q1..Q8 order.

    Deterministic ordering is a W284 contract: same packet -> same
    bullet list. We assert the Q-bullets appear in numeric order by
    finding their positions in the bullet tuple.
    """
    from roam.commands.cmd_pr_replay import _derive_limitations

    bullets = _derive_limitations(_make_packet())
    q_positions = []
    for i, b in enumerate(bullets):
        for q_num in range(1, 9):
            if f"Q{q_num} " in b:  # space narrows to "Q3 (context...)"
                q_positions.append((q_num, i))
                break
    # Each Q appears at most once, and positions are strictly increasing.
    seen_q = [q for q, _ in q_positions]
    assert seen_q == sorted(seen_q)


# ---------------------------------------------------------------------------
# Source 2: redaction bullets.
# ---------------------------------------------------------------------------


def test_limitations_includes_redaction_bullets():
    """``redactions=("secret", "size_limit")`` -> 2 redaction bullets."""
    from roam.commands.cmd_pr_replay import _derive_limitations

    packet = _make_packet(redactions=("secret", "size_limit"))
    bullets = _derive_limitations(packet)

    secret = [b for b in bullets if "Redacted content: `secret`" in b]
    size = [b for b in bullets if "Redacted content: `size_limit`" in b]
    assert len(secret) == 1
    assert len(size) == 1
    # Reviewer-facing explanations come from the documented vocabulary.
    assert "secrets scrubbed" in secret[0]
    assert "256 KiB" in size[0]


def test_limitations_redaction_bullets_preserve_tuple_order():
    """Redaction bullets follow the tuple iteration order, not alphabetic.

    W284 contract: ``packet.redactions`` is canonical (W210/W232) and
    its order is part of the content hash. The bullets MUST preserve
    that order to keep the rendered section deterministic.
    """
    from roam.commands.cmd_pr_replay import _derive_limitations

    packet = _make_packet(redactions=("size_limit", "secret"))
    bullets = _derive_limitations(packet)

    # Find the indices of the size_limit + secret bullets and assert
    # size_limit appears FIRST (matches the tuple order, not alphabetic).
    redaction_lines = [
        b for b in bullets if b.startswith("- **Redacted content: ")
    ]
    assert len(redaction_lines) == 2
    assert "`size_limit`" in redaction_lines[0]
    assert "`secret`" in redaction_lines[1]


# ---------------------------------------------------------------------------
# Source 3: trust-tier warning bullets.
# ---------------------------------------------------------------------------


def test_limitations_includes_trust_warning_bullets():
    """One ``self_reported_agent`` ActorRef -> 1 trust-warning bullet."""
    from roam.commands.cmd_pr_replay import _derive_limitations
    from roam.evidence.refs import ActorRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:claude-opus-4.7",
                trust_tier="self_reported_agent",
            ),
        ),
    )
    bullets = _derive_limitations(packet)

    warnings = [b for b in bullets if "Actor identity unverified" in b]
    assert len(warnings) == 1
    assert "agent:claude-opus-4.7" in warnings[0]
    assert "self_reported_agent" in warnings[0]


def test_limitations_skips_verified_ci_actor():
    """An ActorRef with ``verified_ci`` trust tier emits no warning."""
    from roam.commands.cmd_pr_replay import _derive_limitations
    from roam.evidence.refs import ActorRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="ci_runner",
                actor_id="ci_runner:github/actions/runs/123",
                trust_tier="verified_ci",
            ),
        ),
    )
    bullets = _derive_limitations(packet)
    warnings = [b for b in bullets if "Actor identity unverified" in b]
    assert warnings == []


def test_limitations_unknown_actor_tier_emits_warning():
    """The default ``unknown`` trust tier also surfaces a warning bullet."""
    from roam.commands.cmd_pr_replay import _derive_limitations
    from roam.evidence.refs import ActorRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="human",
                actor_id="human:alice@example.com",
                # Default trust_tier="unknown"
            ),
        ),
    )
    bullets = _derive_limitations(packet)
    warnings = [b for b in bullets if "Actor identity unverified" in b]
    assert len(warnings) == 1
    assert "human:alice@example.com" in warnings[0]
    assert "`unknown`" in warnings[0]


# ---------------------------------------------------------------------------
# All-three-sources combined test.
# ---------------------------------------------------------------------------


def test_limitations_combines_all_three_sources():
    """A packet with Q gap + redaction + trust warning -> bullets in order.

    Three-source ordering is a W284 contract:
    Q-gaps -> redactions -> trust warnings.
    """
    from roam.commands.cmd_pr_replay import _derive_limitations
    from roam.evidence.refs import ActorRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:test",
                trust_tier="self_reported_agent",
            ),
        ),
        redactions=("secret",),
    )
    bullets = _derive_limitations(packet)

    # Find one bullet from each source and assert positional ordering.
    q_idx = next(
        (i for i, b in enumerate(bullets) if "Q3 (context_read): MISSING" in b),
        None,
    )
    redaction_idx = next(
        (i for i, b in enumerate(bullets) if "Redacted content:" in b),
        None,
    )
    trust_idx = next(
        (i for i, b in enumerate(bullets) if "Actor identity unverified" in b),
        None,
    )
    assert q_idx is not None
    assert redaction_idx is not None
    assert trust_idx is not None
    assert q_idx < redaction_idx < trust_idx


# ---------------------------------------------------------------------------
# Renderer-surface contract.
# ---------------------------------------------------------------------------


def test_renderer_emits_sentinel_when_no_limitations():
    """A pristine STRONG packet renders the no-limitations sentinel.

    The renderer wraps the derived bullets and ALWAYS appends the
    non-certification statement. When derivation yields ``()``, the
    renderer emits an explicit italic "no limitations detected"
    sentinel rather than a list that contains only the non-cert
    bullet — that latter shape would read as "the only limitation we
    can find is that we don't certify compliance," which is unhelpful.
    """
    from roam.commands.cmd_pr_replay import _render_evidence_limitations
    from roam.evidence import EvidenceSubject
    from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef

    packet = _make_packet(
        actor_refs=(
            ActorRef(
                actor_kind="ci_runner",
                actor_id="ci_runner:github/actions/runs/123",
                trust_tier="verified_ci",
            ),
        ),
        authority_refs=(
            AuthorityRef(
                authority_kind="mode",
                authority_id="mode:safe_edit",
            ),
        ),
        environment_refs=(
            EnvironmentRef(
                env_kind="ci_job",
                env_id="ci_job:gh/owner/repo/123",
            ),
        ),
        context_refs=(
            EvidenceSubject(kind="file", qualified_name="src/x.py"),
        ),
        changed_subjects=(
            EvidenceSubject(kind="symbol", qualified_name="x.fn"),
        ),
        policy_decisions=({"rule": "no_secrets", "passed": True},),
        tests_run=({"name": "t1", "outcome": "passed"},),
        approvals=({"actor": "alice", "ts": "2026-05-14T00:00:00Z"},),
    )

    out = _render_evidence_limitations(packet)
    assert "_No evidence limitations detected._" in out
    # Non-cert bullet is still appended.
    assert "**Non-certification**" in out


def test_renderer_is_deterministic_for_same_packet():
    """Same packet input -> byte-identical output across calls.

    W284 determinism contract: a deterministic packet produces a
    deterministic bullet list. Two calls on the same packet must
    return the same string.
    """
    from roam.commands.cmd_pr_replay import _render_evidence_limitations

    packet = _make_packet(redactions=("secret", "size_limit"))
    out1 = _render_evidence_limitations(packet)
    out2 = _render_evidence_limitations(packet)
    assert out1 == out2
