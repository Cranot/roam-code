"""W226 - export-profile redaction tests.

Covers the four profile presets shipped in
``src/roam/evidence/profiles.py``:

* ``internal``  - true pass-through
* ``customer``  - drops internal IDs, artifacts by reference only
* ``audit``     - same as customer but preserves actor identities
* ``public``    - anonymises humans, drops artifact paths AND inline

The tests pin both the visible-redaction behaviour (what fields change)
and the masking-trail behaviour (the appended ``profile:<name>:...``
entries in the packet's ``redactions`` tuple).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from roam.evidence import (
    EXPORT_PROFILES,
    ActorRef,
    AuthorityRef,
    ChangeEvidence,
    EnvironmentRef,
    EvidenceArtifact,
    EvidenceSubject,
    ExportProfile,
    apply_profile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _packet_with_everything() -> ChangeEvidence:
    """Build a ``ChangeEvidence`` populated enough to exercise every
    profile field. Mirrors the W216 canonical fixture in spirit while
    staying small and explicit.
    """
    subj = EvidenceSubject(
        kind="symbol",
        qualified_name="src/auth/login.py::handle_login",
        extra={
            "internal_id": "internal-symbol-7f3a",
            "raw_text": "def handle_login(): ...",
            "line_start": 42,
        },
    )
    art_inline = EvidenceArtifact(
        artifact_id="report:abc",
        kind="report",
        content_inline="# PR Replay\n\nVerdict: SAFE\n",
        extra={"internal_id": "internal-report-9", "kind_tag": "weekly"},
    )
    art_path = EvidenceArtifact(
        artifact_id="sarif:def",
        kind="sarif",
        path=".roam/exports/findings.sarif",
        content_hash="9" * 64,
        extra={"sarif_version": "2.1.0"},
    )
    actor_human = ActorRef(
        actor_kind="human",
        actor_id="human:alice@example.com",
        display_name="Alice",
        trust_tier="git_author",
        extra={
            "email": "alice@example.com",
            "username": "alice",
            "department": "platform",
        },
    )
    actor_agent = ActorRef(
        actor_kind="agent",
        actor_id="agent:claude-opus-4.7",
        display_name="Claude Code 4.7",
        trust_tier="self_reported_agent",
        extra={"model": "claude-opus-4.7"},
    )
    authority = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
        source="mode",
    )
    env = EnvironmentRef(
        env_kind="workspace",
        env_id="workspace:/srv/example",
    )
    return ChangeEvidence(
        evidence_id="ev_test_w226_001",
        repo_id="github.com/example/repo",
        git_range="abc1234..def5678",
        commit_sha="def5678",
        diff_hash="0" * 64,
        run_ids=("run_20260514_test_w226",),
        agent_id="agent:claude-opus-4.7",
        human_actor="alice@example.com",
        mode="safe_edit",
        started_at="2026-05-14T10:00:00Z",
        completed_at="2026-05-14T10:15:00Z",
        verdict="SAFE",
        risk_level="low",
        context_refs=(art_inline,),
        changed_subjects=(subj,),
        findings=(
            {
                "kind": "long-params",
                "severity": "low",
                "internal_id": "internal-finding-1",
                "raw_text": "def f(a, b, c, d, e, f, g): ...",
            },
        ),
        policy_decisions=(
            {"rule": "no_unguarded_io", "outcome": "allow"},
        ),
        tests_required=("tests/test_auth.py::test_login",),
        tests_run=(
            {"id": "tests/test_auth.py::test_login", "outcome": "passed"},
        ),
        approvals=(),
        accepted_risks=(),
        artifacts=(art_path,),
        actor_refs=(actor_human, actor_agent),
        authority_refs=(authority,),
        environment_refs=(env,),
        redactions=("secret",),  # producer-attached pre-existing entry
    )


def _packet_with_hash() -> ChangeEvidence:
    """Same packet but stamped with its own content hash."""
    return _packet_with_everything().with_content_hash()


# ---------------------------------------------------------------------------
# 1. internal profile is a true pass-through
# ---------------------------------------------------------------------------


def test_internal_profile_is_passthrough() -> None:
    """``apply_profile(packet, 'internal')`` returns the same packet.

    The internal profile applies no redactions and preserves identity
    (``is``-equality) so callers that compare by reference behave
    predictably.
    """
    packet = _packet_with_hash()
    redacted, warnings = apply_profile(packet, "internal")
    assert redacted is packet
    assert warnings == []
    # Sanity: the redactions tuple is unchanged.
    assert redacted.redactions == ("secret",)


# ---------------------------------------------------------------------------
# 2. customer profile drops artifact content_inline
# ---------------------------------------------------------------------------


def test_customer_profile_drops_artifact_content_inline() -> None:
    """Every artifact with ``content_inline`` gets it cleared.

    The customer profile lists ``content_inline`` in
    ``redact_artifact_fields``, so the inline-form report artifact
    must come out with ``content_inline=None``.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "customer")
    assert all(a.content_inline is None for a in redacted.context_refs)
    assert all(a.content_inline is None for a in redacted.artifacts)


# ---------------------------------------------------------------------------
# 3. customer profile keeps paths with hashes
# ---------------------------------------------------------------------------


def test_customer_profile_keeps_paths_with_hashes() -> None:
    """Path-referenced artifacts keep their ``path`` + ``content_hash``.

    The customer profile does NOT list ``path`` in
    ``redact_artifact_fields``; auditors / customers can verify the
    bytes via the path + sha256 hash. Only the public profile drops
    paths entirely.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "customer")
    sarif = next(a for a in redacted.artifacts if a.kind == "sarif")
    assert sarif.path == ".roam/exports/findings.sarif"
    assert sarif.content_hash == "9" * 64


# ---------------------------------------------------------------------------
# 4. audit profile preserves actor identities
# ---------------------------------------------------------------------------


def test_audit_profile_preserves_actor_identities() -> None:
    """Auditors need to know WHO acted; ``human_actor`` survives.

    The audit profile sets ``redact_actor_fields=()`` deliberately - the
    audit trail must name humans for accountability. The
    ``display_name`` on each ``ActorRef`` also survives.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "audit")
    assert redacted.human_actor == "alice@example.com"
    human_ref = next(
        r for r in redacted.actor_refs if r.actor_kind == "human"
    )
    assert human_ref.display_name == "Alice"


# ---------------------------------------------------------------------------
# 5. audit profile drops artifact inline
# ---------------------------------------------------------------------------


def test_audit_profile_drops_artifact_inline() -> None:
    """Auditors get path + hash, not raw artifact bodies.

    The audit profile mirrors the customer profile on the artifact axis:
    inline content is dropped but path + content_hash survive so the
    auditor can demand the on-disk bytes through a side channel.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "audit")
    assert all(a.content_inline is None for a in redacted.context_refs)
    assert all(a.content_inline is None for a in redacted.artifacts)
    # Path survives:
    sarif = next(a for a in redacted.artifacts if a.kind == "sarif")
    assert sarif.path == ".roam/exports/findings.sarif"


# ---------------------------------------------------------------------------
# 6. public profile redacts human_actor
# ---------------------------------------------------------------------------


def test_public_profile_redacts_human_actor() -> None:
    """``human_actor`` is cleared on the packet and on every human ref.

    The public profile anonymises humans entirely. The agent ref is
    untouched because it's a system identity, not PII.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "public")
    assert redacted.human_actor is None
    human_ref = next(
        r for r in redacted.actor_refs if r.actor_kind == "human"
    )
    assert human_ref.display_name is None
    agent_ref = next(
        r for r in redacted.actor_refs if r.actor_kind == "agent"
    )
    # Agent display name is NOT redacted - it's a system identity.
    assert agent_ref.display_name == "Claude Code 4.7"


# ---------------------------------------------------------------------------
# 7. public profile drops artifact paths but keeps hashes
# ---------------------------------------------------------------------------


def test_public_profile_drops_artifact_paths_keeps_hashes() -> None:
    """Path + inline are both cleared; ``content_hash`` survives.

    The public profile is the strictest: artifact bytes are referenced
    only by their sha256 hash. Anyone with the hash can verify a copy
    they hold, but the packet itself reveals neither the bytes nor the
    on-disk path.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "public")
    sarif = next(a for a in redacted.artifacts if a.kind == "sarif")
    assert sarif.path is None
    assert sarif.content_inline is None
    assert sarif.content_hash == "9" * 64


# ---------------------------------------------------------------------------
# 8. profile appends to redactions list
# ---------------------------------------------------------------------------


def test_profile_appends_to_redactions_list() -> None:
    """The packet's ``redactions`` tuple grows with profile-tag entries.

    Existing producer-attached redactions (e.g. ``"secret"``) stay at
    the head; new profile-tagged entries (e.g.
    ``"profile:customer:artifact_inline"``) append in deterministic
    order. The entries are deduplicated so a profile that touches
    three artifacts doesn't append the same tag three times.
    """
    packet = _packet_with_hash()
    redacted, _ = apply_profile(packet, "customer")
    # Existing entry survives at the head:
    assert redacted.redactions[0] == "secret"
    # Profile-tagged entries follow:
    rest = set(redacted.redactions[1:])
    assert any(r.startswith("profile:customer:") for r in rest)
    # Specific expected tags for the customer profile on this fixture:
    assert "profile:customer:artifact_inline" in rest
    assert "profile:customer:artifact_extra" in rest
    assert "profile:customer:subject_extra" in rest
    assert "profile:customer:findings_extra" in rest
    # Dedup check: no entry appears more than once.
    assert len(redacted.redactions) == len(set(redacted.redactions))


# ---------------------------------------------------------------------------
# 9. unknown profile name handled gracefully
# ---------------------------------------------------------------------------


def test_unknown_profile_name_raises_or_warns() -> None:
    """Unknown profile names degrade to ``internal`` and emit a warning.

    The directive asks for graceful handling - a render-time transform
    must never crash the renderer. The fallback is the safest possible
    behaviour (pass-through) plus a clear warning string.
    """
    packet = _packet_with_hash()
    redacted, warnings = apply_profile(packet, "not_a_profile")
    # Pass-through behaviour - same instance:
    assert redacted is packet
    # Warning string names the bogus profile:
    assert any("not_a_profile" in w for w in warnings)


# ---------------------------------------------------------------------------
# 10. content_hash field is not changed by profile application
# ---------------------------------------------------------------------------


def test_profile_does_not_change_content_hash_field() -> None:
    """``apply_profile`` MUST preserve the recorded ``content_hash``.

    The hash represents the AUTHORITATIVE (internal) form of the
    packet. Consumers verify against THAT hash, not against a fresh
    hash of the redacted form. ``apply_profile`` therefore leaves
    ``content_hash`` untouched.
    """
    packet = _packet_with_hash()
    recorded = packet.content_hash
    assert recorded is not None and len(recorded) == 64

    for profile_name in ("customer", "audit", "public"):
        redacted, _ = apply_profile(packet, profile_name)
        assert redacted.content_hash == recorded, (
            f"profile {profile_name} mutated content_hash: "
            f"{recorded} -> {redacted.content_hash}"
        )


# ---------------------------------------------------------------------------
# Round-trip: the redacted packet must still serialise to canonical JSON
# ---------------------------------------------------------------------------


def test_redacted_packet_round_trips_through_canonical_json() -> None:
    """The redacted packet must still be a valid ``ChangeEvidence``.

    Per the W226 constraints: "The redacted packet must round-trip
    through canonical_json AFTER redaction (proves it's still a valid
    ChangeEvidence)." We exercise all four profiles and assert the
    resulting JSON is parseable AND byte-stable.
    """
    packet = _packet_with_hash()
    for profile_name in EXPORT_PROFILES:
        redacted, _ = apply_profile(packet, profile_name)
        canonical = redacted.to_canonical_json()
        # Parseable JSON:
        parsed = json.loads(canonical)
        # Round-trip is byte-stable:
        reserialised = json.dumps(
            parsed, sort_keys=True, separators=(",", ":")
        )
        assert canonical == reserialised, (
            f"canonical JSON not byte-stable for profile {profile_name!r}"
        )


# ---------------------------------------------------------------------------
# Sample sanity: profile model shape + EXPORT_PROFILES coverage
# ---------------------------------------------------------------------------


def test_export_profiles_covers_four_names() -> None:
    """``EXPORT_PROFILES`` ships exactly the four named presets."""
    assert set(EXPORT_PROFILES) == {"internal", "customer", "audit", "public"}
    for name, profile in EXPORT_PROFILES.items():
        assert isinstance(profile, ExportProfile)
        assert profile.name == name
        # Inline budget is positive integer:
        assert profile.inline_artifact_size_limit > 0


# ---------------------------------------------------------------------------
# Bonus: apply_profile on the W216 canonical fixture (smoke)
# ---------------------------------------------------------------------------


_W216_FIXTURE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "templates"
    / "demos"
    / "canonical-evidence.json"
)


@pytest.mark.skipif(
    not _W216_FIXTURE.exists(),
    reason="W216 canonical fixture not present in this checkout",
)
def test_apply_profile_on_w216_fixture_public() -> None:
    """End-to-end smoke on the canonical demo packet.

    Rebuilds the W216 fixture as a ``ChangeEvidence`` (reusing the same
    helper logic as ``test_canonical_demo_fixture.py``) and applies the
    ``public`` profile. Asserts the human-axis redactions land and
    the canonical JSON still parses.
    """
    # Reuse the helper from the W216 test module via a minimal copy
    # rather than importing - keeps test modules independent.
    payload = json.loads(_W216_FIXTURE.read_text(encoding="utf-8"))

    def _subject(d: dict) -> EvidenceSubject:
        return EvidenceSubject(
            kind=d["kind"],
            qualified_name=d["qualified_name"],
            repo_id=d.get("repo_id"),
            extra=d.get("extra", {}),
        )

    def _artifact(d: dict) -> EvidenceArtifact:
        return EvidenceArtifact(
            artifact_id=d["artifact_id"],
            kind=d["kind"],
            path=d.get("path"),
            content_hash=d.get("content_hash"),
            content_inline=d.get("content_inline"),
            redactions=tuple(d.get("redactions", ())),
            extra=d.get("extra", {}),
        )

    def _actor(d: dict) -> ActorRef:
        return ActorRef(
            actor_kind=d["actor_kind"],
            actor_id=d["actor_id"],
            display_name=d.get("display_name"),
            trust_tier=d.get("trust_tier", "unknown"),
            extra=d.get("extra", {}),
        )

    def _authority(d: dict) -> AuthorityRef:
        return AuthorityRef(
            authority_kind=d["authority_kind"],
            authority_id=d["authority_id"],
            granted_by=d.get("granted_by"),
            source=d.get("source", "inferred_fallback"),
            extra=d.get("extra", {}),
        )

    def _env(d: dict) -> EnvironmentRef:
        return EnvironmentRef(
            env_kind=d["env_kind"],
            env_id=d["env_id"],
            extra=d.get("extra", {}),
        )

    packet = ChangeEvidence(
        evidence_id=payload["evidence_id"],
        schema_version=payload["schema_version"],
        repo_id=payload.get("repo_id"),
        git_range=payload.get("git_range"),
        commit_sha=payload.get("commit_sha"),
        diff_hash=payload.get("diff_hash"),
        run_ids=tuple(payload.get("run_ids", ())),
        agent_id=payload.get("agent_id"),
        human_actor=payload.get("human_actor"),
        mode=payload.get("mode"),
        started_at=payload.get("started_at"),
        completed_at=payload.get("completed_at"),
        verdict=payload.get("verdict"),
        risk_level=payload.get("risk_level"),
        context_refs=tuple(
            _artifact(a) for a in payload.get("context_refs", ())
        ),
        changed_subjects=tuple(
            _subject(s) for s in payload.get("changed_subjects", ())
        ),
        findings=tuple(payload.get("findings", ())),
        policy_decisions=tuple(payload.get("policy_decisions", ())),
        tests_required=tuple(payload.get("tests_required", ())),
        tests_run=tuple(payload.get("tests_run", ())),
        approvals=tuple(payload.get("approvals", ())),
        accepted_risks=tuple(payload.get("accepted_risks", ())),
        artifacts=tuple(_artifact(a) for a in payload.get("artifacts", ())),
        actor_refs=tuple(_actor(a) for a in payload.get("actor_refs", ())),
        authority_refs=tuple(
            _authority(a) for a in payload.get("authority_refs", ())
        ),
        environment_refs=tuple(
            _env(e) for e in payload.get("environment_refs", ())
        ),
        redactions=tuple(payload.get("redactions", ())),
        content_hash=payload.get("content_hash"),
        signature_ref=payload.get("signature_ref"),
    )

    redacted, _ = apply_profile(packet, "public")

    # Human-axis redaction:
    assert redacted.human_actor is None
    human_refs = [r for r in redacted.actor_refs if r.actor_kind == "human"]
    assert human_refs and all(r.display_name is None for r in human_refs)
    # Agent identity survives (system actor, not PII):
    agent_refs = [r for r in redacted.actor_refs if r.actor_kind == "agent"]
    assert agent_refs and all(r.display_name is not None for r in agent_refs)
    # content_hash preserved:
    assert redacted.content_hash == packet.content_hash
    # JSON still parses:
    json.loads(redacted.to_canonical_json())


# ---------------------------------------------------------------------------
# W279b - typed PolicyDecision rows must redact like legacy dict rows
# ---------------------------------------------------------------------------


def test_apply_profile_redacts_typed_policy_decision_rows() -> None:
    """Typed ``PolicyDecision`` rows MUST be Mapping-compatible so
    ``apply_profile`` redacts their extra-style keys identically to
    legacy dict rows.

    Pre-W279b, ``_redact_mapping_tuple`` did
    ``isinstance(row, Mapping)`` before scrubbing keys; a typed
    ``PolicyDecision`` that didn't subclass ``Mapping`` would skip
    the check and silently retain redacted keys (``internal_id``,
    ``raw_text``) on the rendered packet. The fix subclasses the
    dataclass on ``collections.abc.Mapping`` + implements ``__iter__``
    / ``__len__`` so the existing redaction path sees the row exactly
    as it sees a dict.
    """
    from roam.evidence import PolicyDecision

    typed_pd = PolicyDecision.from_dict({
        "rule_id": "rule_a",
        "decision": "allow",
        "evidence_ref": "rule:rule_a",
        "internal_id": "internal-policy-77",  # redacted by customer
        "raw_text": "raw clause body",       # redacted by customer
        "severity": "low",                    # survives
    })
    legacy_dict = {
        "rule_id": "rule_b",
        "decision": "allow",
        "evidence_ref": "rule:rule_b",
        "internal_id": "internal-policy-88",
        "raw_text": "raw clause body 2",
        "severity": "high",
    }

    # Sanity: PolicyDecision is observable as a Mapping (the contract
    # _redact_mapping_tuple relies on).
    from collections.abc import Mapping as MappingABC
    assert isinstance(typed_pd, MappingABC)
    # Iterating yields the canonical-view keys (Mapping ABC contract).
    assert set(iter(typed_pd)) == set(typed_pd.to_dict().keys())
    assert len(typed_pd) == len(typed_pd.to_dict())

    packet = ChangeEvidence(
        evidence_id="ev_w279b_profile",
        policy_decisions=(typed_pd, legacy_dict),
    )

    redacted, _ = apply_profile(packet, "customer")

    # Both rows had ``internal_id`` + ``raw_text`` scrubbed; both kept
    # the surviving fields (``rule_id``, ``decision``, ``evidence_ref``,
    # ``severity``). Compare the dict-view of each so the typed and
    # legacy rows are checked through the same lens.
    def _view(row):
        if hasattr(row, "to_dict"):
            return row.to_dict()
        return dict(row)

    for row in redacted.policy_decisions:
        v = _view(row)
        assert "internal_id" not in v
        assert "raw_text" not in v
        assert v["rule_id"] in {"rule_a", "rule_b"}
        assert v["decision"] == "allow"
        assert "severity" in v

    # The masking trail records the policy_decisions scrub explicitly.
    assert any(
        r == "profile:customer:policy_decisions_extra"
        for r in redacted.redactions
    )
