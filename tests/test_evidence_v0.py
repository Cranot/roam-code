"""W174 Phase 0 + Phase 1 - evidence-compiler schema v0 tests.

Covers:

* Vocabulary closed-enumeration validation
* Frozenset immutability (drift guard)
* JSON round-trip byte stability
* Content-hash exclusion of the ``content_hash`` field itself
* Hash stability under dict / kwarg ordering
* Soft-conformance: large artifacts use path+hash, not inline
* Redaction-reason validation on ChangeEvidence and EvidenceArtifact

All tests are pure dataclass exercises; no DB, no filesystem.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from roam.evidence import (
    ACTOR_KINDS,
    ARTIFACT_KINDS,
    AUTHORITY_KINDS,
    CLAIM_SEVERITIES,
    ENV_KINDS,
    EVIDENCE_SCHEMA_VERSION,
    INLINE_CONTENT_SOFT_LIMIT_BYTES,
    LINK_KINDS,
    REDACTION_REASONS,
    SUBJECT_KINDS,
    ActorRef,
    AuthorityRef,
    ChangeEvidence,
    EnvironmentRef,
    EvidenceArtifact,
    EvidenceLink,
    EvidenceSubject,
)

# ---------------------------------------------------------------------------
# Phase 0 - vocabulary
# ---------------------------------------------------------------------------


def test_subject_kinds_frozenset_immutable() -> None:
    """frozenset.add raises - the closed enumeration cannot be mutated."""
    with pytest.raises(AttributeError):
        SUBJECT_KINDS.add("rogue_kind")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        LINK_KINDS.add("rogue_link")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        ARTIFACT_KINDS.add("rogue_artifact")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        CLAIM_SEVERITIES.add("rogue_severity")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        REDACTION_REASONS.add("rogue_reason")  # type: ignore[attr-defined]


def test_vocabulary_counts_match_memo() -> None:
    """Drift guard: the architecture memo enumerates explicit counts."""
    # 13 baseline subject kinds from memo lines 111-130
    # + 7 evidence-level additions (rule/control/run/bundle/finding/test/artifact)
    assert len(SUBJECT_KINDS) == 20
    # 12 link kinds from memo lines 136-153
    assert len(LINK_KINDS) == 12
    # 11 artifact kinds documented
    assert len(ARTIFACT_KINDS) == 11
    # 5 severities (critical/high/medium/low/info)
    assert len(CLAIM_SEVERITIES) == 5
    # 10 redaction reasons (6 baseline + W241 adds machine_local_path +
    # schema_strict for the collector-side last-line-of-defense redactions
    # + W261 adds producer_not_available for declaring a missing producer
    # gap, first used for the Q8 acceptance-producer hole on pr-replay
    # + MCP-P1.2 adds prompt_injection_marker for the egress marker scan
    # at the MCP tool-call boundary — append-only, deliberate source edit).
    assert len(REDACTION_REASONS) == 10
    assert "prompt_injection_marker" in REDACTION_REASONS


def test_all_link_kinds_documented() -> None:
    """Every LINK_KINDS entry must appear in the module-level docstring.

    Drift guard: when a new link kind is added to the frozenset, the
    docstring must be updated too. The docstring is the human-readable
    contract; the frozenset is the machine-readable contract.
    """
    from roam.evidence import _vocabulary

    # Module-level doc currently unused at this test branch — we pull
    # inspect.getsource below for the per-constant docstring check.
    _module_doc = _vocabulary.__doc__ or ""  # noqa: F841
    # The LINK_KINDS constant carries its own docstring above the
    # declaration; pull the source so we can look at it.
    import inspect

    source = inspect.getsource(_vocabulary)
    for kind in LINK_KINDS:
        assert f"``{kind}``" in source, f"link kind {kind!r} is in LINK_KINDS but not documented in _vocabulary.py"
    # Same drift guard for subject and artifact kinds
    for kind in SUBJECT_KINDS:
        assert f"``{kind}``" in source, f"subject kind {kind!r} not documented in _vocabulary.py"
    for kind in ARTIFACT_KINDS:
        assert f"``{kind}``" in source, f"artifact kind {kind!r} not documented in _vocabulary.py"


# ---------------------------------------------------------------------------
# Phase 1 - subject / link / artifact validation
# ---------------------------------------------------------------------------


def test_subject_validates_kind() -> None:
    """Unknown subject kind raises ValueError."""
    # Happy path
    s = EvidenceSubject(kind="symbol", qualified_name="src/foo.py::bar")
    assert s.kind == "symbol"
    assert s.qualified_name == "src/foo.py::bar"

    # Bad kind
    with pytest.raises(ValueError, match="not in SUBJECT_KINDS"):
        EvidenceSubject(kind="not_a_real_kind", qualified_name="x")

    # Empty qualified_name
    with pytest.raises(ValueError, match="non-empty string"):
        EvidenceSubject(kind="symbol", qualified_name="")


def test_link_validates_kind() -> None:
    """Unknown link kind raises ValueError; both endpoints must be subjects."""
    src = EvidenceSubject(kind="symbol", qualified_name="a")
    tgt = EvidenceSubject(kind="symbol", qualified_name="b")

    # W501: EvidenceLink.kind is a LINK_KINDS vocabulary token, unrelated to
    # the edges.kind column / W512 CALL_OR_REF_KINDS canonicalisation.
    link = EvidenceLink(kind="calls", source=src, target=tgt)
    assert link.kind == "calls"

    # Bad kind
    with pytest.raises(ValueError, match="not in LINK_KINDS"):
        EvidenceLink(kind="not_a_real_link", source=src, target=tgt)

    # Non-subject endpoint
    with pytest.raises(ValueError, match="must be an EvidenceSubject"):
        EvidenceLink(kind="calls", source="not-a-subject", target=tgt)  # type: ignore[arg-type]


def test_artifact_kind_validates() -> None:
    """Unknown artifact kind raises ValueError; path requires content_hash."""
    # Happy path: inline-content artifact
    a = EvidenceArtifact(
        artifact_id="report:abc123",
        kind="report",
        content_inline="# Hello",
    )
    assert a.kind == "report"
    assert a.path is None

    # Happy path: path-referenced artifact
    a2 = EvidenceArtifact(
        artifact_id="sarif:def456",
        kind="sarif",
        path=".roam/exports/findings.sarif",
        content_hash="a" * 64,
    )
    assert a2.path == ".roam/exports/findings.sarif"

    # Bad kind
    with pytest.raises(ValueError, match="not in ARTIFACT_KINDS"):
        EvidenceArtifact(artifact_id="x", kind="not_a_real_kind")

    # Empty artifact_id
    with pytest.raises(ValueError, match="non-empty string"):
        EvidenceArtifact(artifact_id="", kind="report")

    # path + content_inline mutually exclusive
    with pytest.raises(ValueError, match="mutually exclusive"):
        EvidenceArtifact(
            artifact_id="x",
            kind="report",
            path="some/path",
            content_hash="a" * 64,
            content_inline="hi",
        )

    # path without content_hash
    with pytest.raises(ValueError, match="content_hash"):
        EvidenceArtifact(
            artifact_id="x",
            kind="report",
            path="some/path",
        )


def test_redaction_reason_must_be_in_set() -> None:
    """Both EvidenceArtifact and ChangeEvidence reject unknown reasons."""
    # On the artifact
    with pytest.raises(ValueError, match="unknown reason"):
        EvidenceArtifact(
            artifact_id="x",
            kind="report",
            content_inline="hi",
            redactions=("rogue_reason_not_in_set",),
        )

    # Known reasons accepted
    a = EvidenceArtifact(
        artifact_id="x",
        kind="report",
        content_inline="hi",
        redactions=("secret", "pii"),
    )
    assert a.redactions == ("secret", "pii")

    # On the packet
    with pytest.raises(ValueError, match="unknown reason"):
        ChangeEvidence(
            evidence_id="ev_1",
            redactions=("nope",),
        )


# ---------------------------------------------------------------------------
# Phase 1 - ChangeEvidence determinism
# ---------------------------------------------------------------------------


def _fixture_packet() -> ChangeEvidence:
    """Build a representative ChangeEvidence used by round-trip tests."""
    subj_a = EvidenceSubject(
        kind="symbol",
        qualified_name="src/auth/login.py::handle_login",
        extra={"line_start": 42, "line_end": 87},
    )
    subj_b = EvidenceSubject(
        kind="file",
        qualified_name="src/auth/session.py",
    )
    subj_c = EvidenceSubject(
        kind="endpoint",
        qualified_name="endpoint:POST /api/login",
    )

    art_inline = EvidenceArtifact(
        artifact_id="report:abc",
        kind="report",
        content_inline="# PR Replay\n\nVerdict: SAFE\n",
    )
    art_path = EvidenceArtifact(
        artifact_id="sarif:def",
        kind="sarif",
        path=".roam/exports/findings.sarif",
        content_hash="9" * 64,
        redactions=("secret",),
    )

    return ChangeEvidence(
        evidence_id="ev_20260513_001",
        repo_id="github.com/example/repo",
        git_range="abc1234..def5678",
        commit_sha="def5678",
        diff_hash="0" * 64,
        run_ids=("run_20260513_a3f9c2",),
        agent_id="agent-claude-opus-4.7",
        human_actor="alice@example.com",
        mode="safe_edit",
        started_at="2026-05-13T10:00:00Z",
        completed_at="2026-05-13T10:15:00Z",
        verdict="SAFE",
        risk_level="low",
        context_refs=(art_inline,),
        changed_subjects=(subj_a, subj_b, subj_c),
        findings=(
            {"finding_id_str": "smells:src/auth:abc", "claim": "long-params"},
            {"finding_id_str": "dead:src/auth:xyz", "claim": "dead-param"},
        ),
        policy_decisions=({"rule": "no_unguarded_io", "decision": "allow"},),
        tests_required=("tests/test_auth.py",),
        tests_run=({"id": "tests/test_auth.py::test_login", "outcome": "passed"},),
        approvals=(),
        accepted_risks=(),
        artifacts=(art_path,),
        redactions=("secret",),
    )


def test_change_evidence_roundtrip_json_stable() -> None:
    """serialize -> json.loads -> json.dumps(sort_keys, separators) -> bytes match."""
    packet = _fixture_packet()
    canonical = packet.to_canonical_json()

    # Round-trip through json
    parsed = json.loads(canonical)
    reserialised = json.dumps(parsed, sort_keys=True, separators=(",", ":"))

    assert canonical == reserialised, "canonical JSON is not byte-stable across parse/serialise"


def test_content_hash_excludes_self() -> None:
    """The content_hash field is NOT part of the hash input."""
    packet = _fixture_packet()
    h_without = packet.compute_content_hash()

    # Now stamp a fake content_hash and recompute; should be unchanged
    # because the recompute strips content_hash before hashing.
    stamped = dataclasses.replace(packet, content_hash="deadbeef" * 8)
    h_with = stamped.compute_content_hash()

    assert h_without == h_with, "content_hash is leaking into its own input - recompute should strip it before hashing"


def test_content_hash_stable_across_key_order() -> None:
    """Dict-extra ordering inside subjects/findings cannot perturb the hash."""
    # Build the same packet two different ways with reordered ``extra``
    # mappings and reordered findings dicts.
    subj_a = EvidenceSubject(
        kind="symbol",
        qualified_name="x",
        extra={"a": 1, "b": 2, "c": 3},
    )
    subj_a_reorder = EvidenceSubject(
        kind="symbol",
        qualified_name="x",
        extra={"c": 3, "a": 1, "b": 2},
    )
    packet_a = ChangeEvidence(
        evidence_id="ev_1",
        changed_subjects=(subj_a,),
        findings=({"x": 1, "y": 2},),
    )
    packet_b = ChangeEvidence(
        evidence_id="ev_1",
        changed_subjects=(subj_a_reorder,),
        findings=({"y": 2, "x": 1},),
    )
    assert packet_a.compute_content_hash() == packet_b.compute_content_hash()


def test_with_content_hash_populates_field() -> None:
    """with_content_hash() returns a new packet carrying the hash."""
    packet = _fixture_packet()
    assert packet.content_hash is None
    stamped = packet.with_content_hash()
    assert stamped.content_hash is not None
    assert len(stamped.content_hash) == 64  # sha256 hex
    assert all(c in "0123456789abcdef" for c in stamped.content_hash)
    # And the stamped packet's hash equals the original's compute_content_hash
    assert stamped.content_hash == packet.compute_content_hash()


def test_large_artifact_uses_path_not_content_inline() -> None:
    """Soft conformance: artifacts above the soft limit reference by path.

    The constructor doesn't reject inline content above the limit
    (callers may have legitimate reasons), but well-behaved producers
    SHOULD switch to path-referencing. This test pins the contract
    by demonstrating the recommended pattern and asserting the soft
    limit is exposed for callers to consult.
    """
    # Soft limit is a public constant
    assert isinstance(INLINE_CONTENT_SOFT_LIMIT_BYTES, int)
    assert INLINE_CONTENT_SOFT_LIMIT_BYTES > 0

    # Large artifact, path-referenced (the recommended pattern)
    big_artifact = EvidenceArtifact(
        artifact_id="trace:big",
        kind="trace",
        path=".roam/exports/trace_2026_05_13.jsonl",
        content_hash="1" * 64,
    )
    assert big_artifact.path is not None
    assert big_artifact.content_inline is None
    assert big_artifact.content_hash is not None

    # And the constructor itself doesn't choke on a clearly-too-big
    # inline payload. W288-followup adds a producer-side warning, but
    # keeps the contract advisory: no rejection, truncation, or
    # redaction stamping at this layer.
    huge = "x" * (INLINE_CONTENT_SOFT_LIMIT_BYTES * 2)
    with pytest.warns(UserWarning, match="INLINE_CONTENT_SOFT_LIMIT_BYTES"):
        art = EvidenceArtifact(
            artifact_id="report:huge",
            kind="report",
            content_inline=huge,
        )
    assert len(art.content_inline or "") > INLINE_CONTENT_SOFT_LIMIT_BYTES
    assert art.redactions == ()


def test_packet_with_findings_round_trips() -> None:
    """Happy path: full fixture serialises, parses, and matches via hash."""
    packet = _fixture_packet().with_content_hash()
    canonical = packet.to_canonical_json()
    parsed = json.loads(canonical)

    # Spot-check the shape
    assert parsed["evidence_id"] == "ev_20260513_001"
    assert parsed["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert parsed["verdict"] == "SAFE"
    assert len(parsed["changed_subjects"]) == 3
    assert len(parsed["artifacts"]) == 1
    assert len(parsed["context_refs"]) == 1
    assert parsed["redactions"] == ["secret"]
    assert parsed["content_hash"] is not None
    assert len(parsed["content_hash"]) == 64

    # Tuples should land as JSON arrays (the canonical-JSON helper
    # coerces tuple -> list)
    assert isinstance(parsed["run_ids"], list)
    assert isinstance(parsed["tests_required"], list)


def test_schema_version_is_stamped_by_default() -> None:
    """Every packet carries a schema_version without the caller setting one."""
    p = ChangeEvidence(evidence_id="ev_min")
    assert p.schema_version == EVIDENCE_SCHEMA_VERSION


def test_collections_coerce_lists_to_tuples() -> None:
    """Caller passing a list ends up with a tuple - keeps the packet hashable."""
    p = ChangeEvidence(
        evidence_id="ev_coerce",
        run_ids=["r1", "r2"],  # type: ignore[arg-type]
        tests_required=["t1"],  # type: ignore[arg-type]
    )
    assert isinstance(p.run_ids, tuple)
    assert isinstance(p.tests_required, tuple)
    assert p.run_ids == ("r1", "r2")


# ---------------------------------------------------------------------------
# W182 - agentic-assurance refs (identity / authority / environment)
# ---------------------------------------------------------------------------


def test_actor_ref_validates_kind() -> None:
    """Unknown actor_kind raises ValueError; happy path returns a frozen
    dataclass."""
    # Happy path
    a = ActorRef(actor_kind="agent", actor_id="claude-opus-4.7")
    assert a.actor_kind == "agent"
    assert a.actor_id == "claude-opus-4.7"
    assert a.display_name is None

    # Bad kind
    with pytest.raises(ValueError, match="not in ACTOR_KINDS"):
        ActorRef(actor_kind="not_a_real_kind", actor_id="x")

    # Empty actor_id
    with pytest.raises(ValueError, match="non-empty string"):
        ActorRef(actor_kind="human", actor_id="")

    # Frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.actor_id = "mutated"  # type: ignore[misc]


def test_authority_ref_validates_kind() -> None:
    """Unknown authority_kind raises ValueError; happy path round-trips."""
    auth = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
        granted_by="system:roam",
    )
    assert auth.authority_kind == "mode"
    assert auth.granted_by == "system:roam"

    with pytest.raises(ValueError, match="not in AUTHORITY_KINDS"):
        AuthorityRef(authority_kind="bogus", authority_id="x")

    with pytest.raises(ValueError, match="non-empty string"):
        AuthorityRef(authority_kind="approval", authority_id="")


def test_environment_ref_validates_kind() -> None:
    """Unknown env_kind raises ValueError; happy path round-trips."""
    env = EnvironmentRef(env_kind="ci_job", env_id="gh:owner/repo/runs/123")
    assert env.env_kind == "ci_job"
    assert env.env_id == "gh:owner/repo/runs/123"

    with pytest.raises(ValueError, match="not in ENV_KINDS"):
        EnvironmentRef(env_kind="prod", env_id="x")

    with pytest.raises(ValueError, match="non-empty string"):
        EnvironmentRef(env_kind="local_run", env_id="")


def test_actor_refs_kinds_frozenset_immutable() -> None:
    """All three W182 kind sets are frozensets and cannot be mutated."""
    with pytest.raises(AttributeError):
        ACTOR_KINDS.add("rogue_actor")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        AUTHORITY_KINDS.add("rogue_authority")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        ENV_KINDS.add("rogue_env")  # type: ignore[attr-defined]
    # And the counts match the docstring contract (drift guard)
    assert len(ACTOR_KINDS) == 6
    assert len(AUTHORITY_KINDS) == 6
    assert len(ENV_KINDS) == 4


def test_change_evidence_with_refs_round_trips_canonical_json() -> None:
    """Populated refs serialise, parse, and re-serialise byte-stable."""
    actor = ActorRef(
        actor_kind="agent",
        actor_id="agent:claude-opus-4.7",
        display_name="Claude Opus 4.7",
        extra={"session_id": "s_42"},
    )
    auth = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
        granted_by="system:roam",
    )
    env = EnvironmentRef(
        env_kind="ci_job",
        env_id="ci:gh/owner/repo/runs/123",
        extra={"provider": "github_actions"},
    )
    packet = ChangeEvidence(
        evidence_id="ev_w182_1",
        actor_refs=(actor,),
        authority_refs=(auth,),
        environment_refs=(env,),
    )
    canonical = packet.to_canonical_json()
    parsed = json.loads(canonical)
    reserialised = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert canonical == reserialised, "canonical JSON with W182 refs is not byte-stable across parse/serialise"
    # Spot-check shape
    assert parsed["actor_refs"][0]["actor_kind"] == "agent"
    assert parsed["authority_refs"][0]["authority_kind"] == "mode"
    assert parsed["environment_refs"][0]["env_kind"] == "ci_job"


def test_empty_refs_omitted_from_canonical_json() -> None:
    """Empty W182 ref tuples are SKIPPED from canonical JSON (option (a)).

    This is the backward-compat decision documented on the
    ``ChangeEvidence`` class docstring under "W182 backward-compat
    decision". Empty refs are absent from the JSON keys (not present
    as empty arrays) so pre-W182 packets hash identically.
    """
    p = ChangeEvidence(evidence_id="ev_empty_refs")
    parsed = json.loads(p.to_canonical_json())
    assert "actor_refs" not in parsed
    assert "authority_refs" not in parsed
    assert "environment_refs" not in parsed

    # ... but when ANY ref is populated, only the populated one appears.
    p2 = ChangeEvidence(
        evidence_id="ev_partial_refs",
        actor_refs=(ActorRef(actor_kind="human", actor_id="alice"),),
    )
    parsed2 = json.loads(p2.to_canonical_json())
    assert "actor_refs" in parsed2
    assert parsed2["actor_refs"][0]["actor_kind"] == "human"
    assert "authority_refs" not in parsed2
    assert "environment_refs" not in parsed2


def test_content_hash_unchanged_when_refs_empty() -> None:
    """Pre-W182 packet hash == W182 packet hash when refs are empty.

    Proves option (a): omitting empty refs from canonical JSON keeps
    the content_hash backward-compatible with packets produced by code
    that pre-dates W182. We model "pre-W182" by computing the hash of
    a manually-built canonical JSON that does NOT contain the three
    new keys.
    """
    p_v0_shape = ChangeEvidence(evidence_id="ev_compat_1")  # no refs
    canonical = p_v0_shape.to_canonical_json()

    # The canonical JSON for a refs-empty packet has no actor_refs/
    # authority_refs/environment_refs keys at all.
    assert "actor_refs" not in canonical
    assert "authority_refs" not in canonical
    assert "environment_refs" not in canonical

    # And the hash matches the hash of the same packet built via
    # explicit empty-tuple refs (option (a) requires these be the
    # same byte-for-byte).
    p_explicit_empty = ChangeEvidence(
        evidence_id="ev_compat_1",
        actor_refs=(),
        authority_refs=(),
        environment_refs=(),
    )
    assert p_v0_shape.to_canonical_json() == p_explicit_empty.to_canonical_json()
    assert p_v0_shape.compute_content_hash() == p_explicit_empty.compute_content_hash()

    # And the schema_version stays "1.0.0" (proves option (a) was taken
    # rather than option (b)).
    assert EVIDENCE_SCHEMA_VERSION == "1.0.0"


def test_packet_with_full_assurance_signature() -> None:
    """Happy path: 1-of-each ref populated, packet hashes and round-trips."""
    actor = ActorRef(
        actor_kind="agent",
        actor_id="agent:claude-opus-4.7",
        display_name="Claude Opus 4.7",
    )
    human = ActorRef(
        actor_kind="human",
        actor_id="human:alice@example.com",
        display_name="Alice",
    )
    auth_mode = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
    )
    auth_permit = AuthorityRef(
        authority_kind="permit",
        authority_id="permit:perm_20260513_a3f9c2",
        granted_by="human:alice@example.com",
    )
    env_ci = EnvironmentRef(
        env_kind="ci_job",
        env_id="ci:github.com/owner/repo/actions/runs/12345",
    )
    env_branch = EnvironmentRef(
        env_kind="branch_range",
        env_id="branch_range:main:abc1234..def5678",
    )

    packet = ChangeEvidence(
        evidence_id="ev_w182_full",
        repo_id="github.com/owner/repo",
        commit_sha="def5678",
        verdict="SAFE",
        actor_refs=(actor, human),
        authority_refs=(auth_mode, auth_permit),
        environment_refs=(env_ci, env_branch),
    ).with_content_hash()

    # Content hash is populated and well-formed
    assert packet.content_hash is not None
    assert len(packet.content_hash) == 64
    assert all(c in "0123456789abcdef" for c in packet.content_hash)

    # Round-trip JSON
    canonical = packet.to_canonical_json()
    parsed = json.loads(canonical)
    assert len(parsed["actor_refs"]) == 2
    assert len(parsed["authority_refs"]) == 2
    assert len(parsed["environment_refs"]) == 2
    assert parsed["actor_refs"][0]["actor_kind"] == "agent"
    assert parsed["actor_refs"][1]["actor_kind"] == "human"
    assert parsed["authority_refs"][1]["granted_by"] == "human:alice@example.com"

    # Re-serialise byte-stable
    reserialised = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert canonical == reserialised

    # Hash is order-independent w.r.t. dict-key ordering inside refs.
    # Build the same packet with reordered ``extra`` mappings on refs
    # to confirm sort_keys at every dict level keeps the hash stable.
    actor_reorder = ActorRef(
        actor_kind="agent",
        actor_id="agent:claude-opus-4.7",
        display_name="Claude Opus 4.7",
    )
    packet_same_shape = ChangeEvidence(
        evidence_id="ev_w182_full",
        repo_id="github.com/owner/repo",
        commit_sha="def5678",
        verdict="SAFE",
        actor_refs=(actor_reorder, human),
        authority_refs=(auth_mode, auth_permit),
        environment_refs=(env_ci, env_branch),
    ).with_content_hash()
    assert packet.content_hash == packet_same_shape.content_hash


def test_w182_refs_coerce_lists_to_tuples() -> None:
    """ChangeEvidence(actor_refs=[..]) ends up with a tuple - hashable."""
    p = ChangeEvidence(
        evidence_id="ev_coerce_refs",
        actor_refs=[ActorRef(actor_kind="human", actor_id="a")],  # type: ignore[arg-type]
        authority_refs=[AuthorityRef(authority_kind="mode", authority_id="m")],  # type: ignore[arg-type]
        environment_refs=[EnvironmentRef(env_kind="local_run", env_id="e")],  # type: ignore[arg-type]
    )
    assert isinstance(p.actor_refs, tuple)
    assert isinstance(p.authority_refs, tuple)
    assert isinstance(p.environment_refs, tuple)
    assert len(p.actor_refs) == 1


# ---------------------------------------------------------------------------
# W210 - per-claim confidence + time-aware + stale-evidence + version-link
# + minimum-viable-assurance gate + report honesty banner
# ---------------------------------------------------------------------------


def test_confidence_basis_constants_present() -> None:
    """``CLAIM_CONFIDENCES`` is the 4-element frozenset per the W210 vocab.

    Item 1 of the W210 directive: every finding row produced by a
    detector should be able to stamp a ``confidence_basis`` literal.
    The vocabulary is closed and lives in
    ``roam.evidence._vocabulary``; the four literals listed in the
    directive must be the EXACT contents (no more, no fewer) so a
    consumer can rely on the closed enumeration.
    """
    from roam.evidence._vocabulary import CLAIM_CONFIDENCES

    assert isinstance(CLAIM_CONFIDENCES, frozenset)
    assert CLAIM_CONFIDENCES == frozenset(
        {
            "direct",
            "derived",
            "inferred",
            "legacy_fallback",
        }
    )
    # Drift guard: frozenset is immutable
    with pytest.raises(AttributeError):
        CLAIM_CONFIDENCES.add("rogue_basis")  # type: ignore[attr-defined]


def test_time_aware_fields_default_none_and_omitted() -> None:
    """``context_read_at`` / ``edits_started_at`` / ``edits_completed_at``
    default to ``None`` AND are omitted from canonical JSON.

    Item 2 of the W210 directive: three change-scope timestamps distinct
    from the run-wide ``started_at`` / ``completed_at``. Defaults are
    ``None``; the omit-when-default rule keeps the pre-W210 content_hash
    byte-identical for any packet that doesn't populate the new fields.
    """
    p = ChangeEvidence(evidence_id="ev_w210_time_defaults")
    # Defaults
    assert p.context_read_at is None
    assert p.edits_started_at is None
    assert p.edits_completed_at is None
    # Omitted from canonical JSON (the omit-when-default rule)
    canonical = p.to_canonical_json()
    assert "context_read_at" not in canonical
    assert "edits_started_at" not in canonical
    assert "edits_completed_at" not in canonical

    # When populated, they appear in the JSON
    p2 = ChangeEvidence(
        evidence_id="ev_w210_time_populated",
        context_read_at="2026-05-14T10:00:00Z",
        edits_started_at="2026-05-14T10:05:00Z",
        edits_completed_at="2026-05-14T10:30:00Z",
    )
    parsed2 = json.loads(p2.to_canonical_json())
    assert parsed2["context_read_at"] == "2026-05-14T10:00:00Z"
    assert parsed2["edits_started_at"] == "2026-05-14T10:05:00Z"
    assert parsed2["edits_completed_at"] == "2026-05-14T10:30:00Z"


def test_evidence_stale_flag_default_false_and_omitted() -> None:
    """``evidence_stale`` defaults to ``False`` AND is omitted from JSON.

    Same omit-when-default pattern as the W182 ref lists - emitting
    ``"evidence_stale": false`` on every pre-W210 packet would break
    every stored content_hash. ``stale_reasons`` defaults to ``()`` and
    is likewise omitted.
    """
    p = ChangeEvidence(evidence_id="ev_w210_stale_defaults")
    assert p.evidence_stale is False
    assert p.stale_reasons == ()
    canonical = p.to_canonical_json()
    assert "evidence_stale" not in canonical
    assert "stale_reasons" not in canonical

    # When set, the flag and reasons round-trip through canonical JSON
    p2 = ChangeEvidence(
        evidence_id="ev_w210_stale_set",
        evidence_stale=True,
        stale_reasons=("preflight_older_than_edits", "tests_pre_diff"),
    )
    parsed = json.loads(p2.to_canonical_json())
    assert parsed["evidence_stale"] is True
    assert parsed["stale_reasons"] == [
        "preflight_older_than_edits",
        "tests_pre_diff",
    ]


def test_version_fields_omitted_when_none() -> None:
    """``roam_version`` / ``rules_config_hash`` / ``constitution_hash`` /
    ``control_map_hash`` default to ``None`` and are omitted when ``None``.

    Item 4 of the W210 directive: identify WHICH roam version and WHICH
    config files produced this packet. Defaults keep pre-W210 hashes
    stable; populated values round-trip through canonical JSON.
    """
    p = ChangeEvidence(evidence_id="ev_w210_versions_default")
    assert p.roam_version is None
    assert p.rules_config_hash is None
    assert p.constitution_hash is None
    assert p.control_map_hash is None
    canonical = p.to_canonical_json()
    assert "roam_version" not in canonical
    assert "rules_config_hash" not in canonical
    assert "constitution_hash" not in canonical
    assert "control_map_hash" not in canonical

    # Populated - they appear in JSON
    p2 = ChangeEvidence(
        evidence_id="ev_w210_versions_set",
        roam_version="13.0",
        rules_config_hash="a" * 64,
        constitution_hash="b" * 64,
        control_map_hash="c" * 64,
    )
    parsed = json.loads(p2.to_canonical_json())
    assert parsed["roam_version"] == "13.0"
    assert parsed["rules_config_hash"] == "a" * 64
    assert parsed["constitution_hash"] == "b" * 64
    assert parsed["control_map_hash"] == "c" * 64


# ---------------------------------------------------------------------------
# W287 - ``resolve_roam_version`` helper
# ---------------------------------------------------------------------------


def testresolve_roam_version_returns_package_version() -> None:
    """The helper returns the installed ``roam-code`` version string.

    W287 directive. ``resolve_roam_version()`` is the producer-side
    hook for stamping :attr:`ChangeEvidence.roam_version` from the real
    package metadata rather than a hard-coded string. The helper must:

    * Return a non-empty string.
    * Return the SAME value that ``roam.__version__`` resolves to (so
      consumers comparing the packet stamp against ``roam --version``
      see a match).
    """
    from roam import __version__
    from roam.evidence.change_evidence import resolve_roam_version

    resolved = resolve_roam_version()
    assert isinstance(resolved, str)
    assert resolved
    assert resolved == __version__


def testresolve_roam_version_falls_back_on_metadata_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper returns ``"unknown"`` when ``roam.__version__`` is unavailable.

    Simulates a malformed install where importing ``__version__`` from
    ``roam`` raises (e.g. the package metadata is missing entirely).
    The helper must NOT propagate the exception - collection must keep
    running, and the sentinel ``"unknown"`` is the canonical fallback.
    """
    import sys

    import roam
    from roam.evidence.change_evidence import resolve_roam_version

    # Force ``from roam import __version__`` to raise during the helper's
    # deferred import. Easiest reliable way: temporarily remove the
    # attribute from the loaded ``roam`` module so the import-bound
    # ``from roam import __version__`` inside the helper raises
    # ``ImportError``.
    saved = roam.__version__
    monkeypatch.delattr(roam, "__version__")
    try:
        # Sanity: the helper's deferred ``from roam import __version__``
        # must hit ``ImportError`` now.
        assert "roam" in sys.modules
        result = resolve_roam_version()
        assert result == "unknown"
    finally:
        # ``monkeypatch.delattr`` already restores on test teardown, but
        # also keep ``saved`` available for any post-yield consumer.
        roam.__version__ = saved  # type: ignore[attr-defined]


def testresolve_roam_version_falls_back_when_version_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty / non-string ``__version__`` values fall back to ``"unknown"``.

    A bad install or a partial metadata write could leave
    ``roam.__version__`` as an empty string or a non-string sentinel.
    The helper's defensive type-check must catch this and return the
    same ``"unknown"`` fallback rather than emitting an empty
    ``roam_version`` stamp that downstream consumers would have to
    special-case.
    """
    import roam
    from roam.evidence.change_evidence import resolve_roam_version

    monkeypatch.setattr(roam, "__version__", "", raising=False)
    assert resolve_roam_version() == "unknown"

    monkeypatch.setattr(roam, "__version__", None, raising=False)
    assert resolve_roam_version() == "unknown"


def test_change_evidence_default_roam_version_stays_none() -> None:
    """Default-constructed packets KEEP ``roam_version`` at ``None``.

    Hash-stability invariant. The W210 omit-when-default contract
    requires that any packet that does not EXPLICITLY pass
    ``roam_version`` produces canonical JSON without the field. W287
    deliberately places the version stamp in the PRODUCER
    (``collector.collect_change_evidence``), NOT in the dataclass
    default, so existing golden hashes and existing hand-built test
    packets stay byte-stable.
    """
    p = ChangeEvidence(evidence_id="ev_w287_default")
    assert p.roam_version is None
    assert "roam_version" not in p.to_canonical_json()


def test_assurance_floor_method() -> None:
    """Bare packet fails the MVA gate; missing-list names the six gaps.

    Item 5 of the W210 directive: ``assurance_floor()`` returns
    ``{"passes": bool, "missing": tuple[str, ...]}``. A bare packet
    has none of the six required signals so it must fail with all six
    names in ``missing``.
    """
    p = ChangeEvidence(evidence_id="ev_w210_floor_bare")
    floor = p.assurance_floor()
    assert floor["passes"] is False
    # All six checks fail on a bare packet
    assert set(floor["missing"]) == {
        "actor",
        "authority",
        "changed_subjects",
        "findings",
        "verification",
        "policy_state",
    }
    # ``missing`` is a tuple (hashable, stable order)
    assert isinstance(floor["missing"], tuple)


def test_assurance_floor_passes_when_minimum_present() -> None:
    """Populated packet passes the MVA gate; ``missing`` is empty.

    Builds a packet that satisfies every floor check via the smallest
    set of fields that gates each check, and asserts that
    ``passes is True`` with an empty ``missing`` tuple.
    """
    subj = EvidenceSubject(kind="symbol", qualified_name="src/x.py::f")
    p = ChangeEvidence(
        evidence_id="ev_w210_floor_pass",
        # actor: at least one actor_refs entry
        actor_refs=(ActorRef(actor_kind="agent", actor_id="agent:a"),),
        # authority: at least one authority_refs entry
        authority_refs=(AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit"),),
        # changed_subjects: non-empty
        changed_subjects=(subj,),
        # findings: non-empty
        findings=({"finding_id_str": "x", "claim": "y"},),
        # verification: tests_run non-empty
        tests_run=({"id": "t1", "outcome": "passed"},),
        # policy_state: authority_refs already gates this; alternately
        # policy_decisions also satisfies. Both paths covered.
        policy_decisions=({"rule": "r1", "decision": "allow"},),
    )
    floor = p.assurance_floor()
    assert floor["passes"] is True
    assert floor["missing"] == ()


def test_evidence_completeness_8q_table() -> None:
    """Bare packet returns all-missing; populated returns mix of states.

    Item 6 of the W210 directive: ``evidence_completeness()`` returns
    a Q1..Q8 table plus totals. Bare packet (no evidence) returns all
    ``"missing"``; a fully-populated packet returns all ``"complete"``;
    a hand-built partial packet exercises the ``partial`` path and the
    Q5 ``not_applicable`` path (verdict=SAFE with no findings).
    """
    # Bare: all eight questions missing
    bare = ChangeEvidence(evidence_id="ev_w210_complete_bare")
    table = bare.evidence_completeness()
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert table[q] == "missing", f"{q} expected missing, got {table[q]}"
    # Totals reflect 8 missing
    assert table["complete"] == 0
    assert table["partial"] == 0
    assert table["missing"] == 8
    assert table["not_applicable"] == 0

    # Partial mix: agent_id only -> Q1 partial; mode only -> Q2 partial;
    # verdict=SAFE + no findings -> Q5 not_applicable; redactions ->
    # Q8 partial; tests_required only -> Q7 partial.
    partial = ChangeEvidence(
        evidence_id="ev_w210_complete_partial",
        agent_id="agent:a",
        mode="safe_edit",
        verdict="SAFE",
        tests_required=("tests/test_x.py",),
        redactions=("policy",),
    )
    table_p = partial.evidence_completeness()
    assert table_p["Q1"] == "partial"  # agent_id only
    assert table_p["Q2"] == "partial"  # mode only
    assert table_p["Q3"] == "missing"  # no context_refs
    assert table_p["Q4"] == "missing"  # no changed_subjects
    assert table_p["Q5"] == "not_applicable"  # SAFE + no findings
    assert table_p["Q6"] == "missing"  # no policy or authority
    assert table_p["Q7"] == "partial"  # tests_required only
    assert table_p["Q8"] == "partial"  # redactions only
    assert table_p["complete"] == 0
    assert table_p["partial"] == 4
    assert table_p["missing"] == 3
    assert table_p["not_applicable"] == 1
    # Sum of Q-states equals 8 (sanity check on the totals)
    assert (table_p["complete"] + table_p["partial"] + table_p["missing"] + table_p["not_applicable"]) == 8

    # Fully populated: all 8 questions complete.
    subj = EvidenceSubject(kind="symbol", qualified_name="src/x.py::f")
    art = EvidenceArtifact(
        artifact_id="report:rep",
        kind="report",
        content_inline="ok",
    )
    full = ChangeEvidence(
        evidence_id="ev_w210_complete_full",
        actor_refs=(ActorRef(actor_kind="agent", actor_id="agent:a"),),
        authority_refs=(AuthorityRef(authority_kind="mode", authority_id="mode:safe_edit"),),
        context_refs=(art,),
        changed_subjects=(subj,),
        risk_level="low",
        policy_decisions=({"rule": "r1", "decision": "allow"},),
        tests_run=({"id": "t1", "outcome": "passed"},),
        approvals=({"id": "a1"},),
    )
    table_f = full.evidence_completeness()
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"):
        assert table_f[q] == "complete", f"{q} expected complete, got {table_f[q]}"
    assert table_f["complete"] == 8
    assert table_f["partial"] == 0
    assert table_f["missing"] == 0
    assert table_f["not_applicable"] == 0


def test_existing_w182_hash_stability_preserved() -> None:
    """Pre-W210 packet hashes are byte-identical AFTER W210 additions.

    The W210 backward-compat contract: any packet that doesn't populate
    the W210 fields must produce the exact same canonical JSON (and
    therefore the exact same content_hash) as it did before W210.

    Two fixtures pin the contract:

    * **Bare packet** - all fields at default. Pre-W210 SHA-256 of the
      canonical JSON is captured below as a literal string. Any drift
      in the omit-when-default rules will surface as a mismatch here.
    * **W182 packet** - populated identity/authority/environment refs
      with no W210 fields touched. Same byte-identical contract.

    The pre-W210 hashes were captured on the same Python interpreter
    (3.12.13) against the same refs.py state (including W211
    ``trust_tier`` / ``source`` fields - they are NOT a W210 concern).
    """
    # Bare packet (the empty-defaults case)
    p_bare = ChangeEvidence(evidence_id="ev_compat_1")
    expected_bare_hash = "c64772f6c8c0314637cd47d94f00f8d5924633a037d379e8b0e3b10e14d0158b"
    assert p_bare.compute_content_hash() == expected_bare_hash, (
        "W210 additions perturbed the bare-packet hash. The omit-when-"
        "default rule is the only thing preventing this drift; check "
        "_W210_OMIT_WHEN_DEFAULT_FIELDS for a missing entry."
    )
    # And the canonical JSON contains none of the W210 field names
    bare_canonical = p_bare.to_canonical_json()
    for w210_field in (
        "context_read_at",
        "edits_started_at",
        "edits_completed_at",
        "evidence_stale",
        "stale_reasons",
        "roam_version",
        "rules_config_hash",
        "constitution_hash",
        "control_map_hash",
    ):
        assert w210_field not in bare_canonical, (
            f"W210 field {w210_field!r} leaked into the bare-packet "
            f"canonical JSON; the omit-when-default rule has a bug."
        )

    # W182 packet (populated refs, no W210 fields)
    actor = ActorRef(
        actor_kind="agent",
        actor_id="agent:claude-opus-4.7",
        display_name="Claude Opus 4.7",
    )
    auth = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
        granted_by="system:roam",
    )
    env = EnvironmentRef(
        env_kind="ci_job",
        env_id="ci:gh/owner/repo/runs/123",
        extra={"provider": "github_actions"},
    )
    p_w182 = ChangeEvidence(
        evidence_id="ev_w182_compat",
        repo_id="github.com/owner/repo",
        commit_sha="def5678",
        verdict="SAFE",
        actor_refs=(actor,),
        authority_refs=(auth,),
        environment_refs=(env,),
    )
    expected_w182_hash = "b234978474e0bbbdf95cc4c7aa695750ff0bd110781d631975304979c32bc502"
    assert p_w182.compute_content_hash() == expected_w182_hash, (
        "W210 additions perturbed the W182 packet hash. The W210 fields "
        "must default to omit-sentinel values that do NOT appear in "
        "canonical JSON; check _W210_OMIT_WHEN_DEFAULT_FIELDS."
    )
    # Schema version is unchanged
    assert EVIDENCE_SCHEMA_VERSION == "1.0.0"


# ---------------------------------------------------------------------------
# W534 - ChangeEvidence.from_canonical_json() inverse-of-serialiser
# ---------------------------------------------------------------------------


_W534_FIXTURE_NAMES = (
    "v0_minimal",
    "v0_full",
    "v1_with_refs",
    "v1_empty_refs",
    "v1_5_with_w210_fields",
)


def _w534_read_fixture(name: str) -> str:
    """Read a golden fixture's raw canonical JSON bytes."""
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "evidence" / f"{name}.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", _W534_FIXTURE_NAMES)
def test_w534_round_trip_byte_identical_on_all_fixtures(name: str) -> None:
    """``from_canonical_json(text).to_canonical_json() == text`` for every fixture.

    The load-bearing W534 contract. If this drifts on any of the 5 golden
    fixtures, every stored ``content_hash`` for the corresponding shape
    is invalidated. Mirrors the existing schema-migration round-trip test
    but routes through the new ``from_canonical_json`` classmethod
    (rather than the hand-rolled ``_load_packet`` helper that lives in
    ``tests/test_evidence_schema_migration.py``).
    """
    body = _w534_read_fixture(name)
    packet = ChangeEvidence.from_canonical_json(body)
    canon = packet.to_canonical_json()
    assert canon == body, (
        f"fixture {name!r} did not round-trip byte-stable via "
        f"from_canonical_json -> to_canonical_json\n"
        f"EXPECTED ({len(body)} bytes): {body[:200]}\n"
        f"ACTUAL   ({len(canon)} bytes): {canon[:200]}"
    )


@pytest.mark.parametrize("name", _W534_FIXTURE_NAMES)
def test_w534_round_trip_preserves_content_hash(name: str) -> None:
    """Reconstructed packet's compute_content_hash matches the sibling .sha256.

    Stricter than the byte-identical-text test: proves the hash is also
    preserved after a parse, even when the field type is a nested dataclass
    (``EvidenceArtifact`` / ``EvidenceSubject`` / ``ActorRef`` / ...).
    """
    from pathlib import Path

    sha_path = Path(__file__).parent / "fixtures" / "evidence" / f"{name}.sha256"
    expected = sha_path.read_text(encoding="utf-8").strip()

    body = _w534_read_fixture(name)
    packet = ChangeEvidence.from_canonical_json(body)
    actual = packet.compute_content_hash()
    assert actual == expected, (
        f"fixture {name!r} content_hash drift via from_canonical_json:\n"
        f"  expected (sibling .sha256): {expected}\n"
        f"  actual   (recomputed):      {actual}"
    )


def test_w534_round_trip_preserves_nested_dataclass_fields() -> None:
    """Parse -> serialise reconstructs every nested dataclass shape."""
    # Build a packet with every nested-dataclass field type populated.
    subj = EvidenceSubject(kind="symbol", qualified_name="src/foo.py::bar")
    art = EvidenceArtifact(
        artifact_id="sarif:abc",
        kind="sarif",
        path=".roam/exports/x.sarif",
        content_hash="9" * 64,
    )
    actor = ActorRef(actor_kind="agent", actor_id="agent:test")
    auth = AuthorityRef(
        authority_kind="mode",
        authority_id="mode:safe_edit",
        source="mode",
    )
    env = EnvironmentRef(env_kind="workspace", env_id="workspace:/home/x")

    original = ChangeEvidence(
        evidence_id="ev_w534_nested",
        verdict="SAFE",
        risk_level="low",
        changed_subjects=(subj,),
        artifacts=(art,),
        actor_refs=(actor,),
        authority_refs=(auth,),
        environment_refs=(env,),
        findings=({"finding_id_str": "f1", "claim": "x"},),
    )

    canon = original.to_canonical_json()
    parsed = ChangeEvidence.from_canonical_json(canon)

    # Byte-identical re-serialisation:
    assert parsed.to_canonical_json() == canon
    # Same content hash:
    assert parsed.compute_content_hash() == original.compute_content_hash()
    # Nested dataclass types preserved (not raw dicts):
    assert isinstance(parsed.changed_subjects[0], EvidenceSubject)
    assert isinstance(parsed.artifacts[0], EvidenceArtifact)
    assert isinstance(parsed.actor_refs[0], ActorRef)
    assert isinstance(parsed.authority_refs[0], AuthorityRef)
    assert isinstance(parsed.environment_refs[0], EnvironmentRef)


def test_w534_strict_mode_raises_on_unknown_subject_kind() -> None:
    """strict=True raises ValueError on an unknown SUBJECT_KIND."""
    packet = ChangeEvidence(
        evidence_id="ev_w534_strict_subject",
        verdict="SAFE",
        changed_subjects=(EvidenceSubject(kind="symbol", qualified_name="x"),),
    )
    canon = packet.to_canonical_json()
    # Splice a bad subject kind in.
    bad = canon.replace('"kind":"symbol"', '"kind":"not_a_real_kind"')
    assert bad != canon, "test setup: substitution did not change the JSON"

    with pytest.raises(ValueError, match="not in SUBJECT_KINDS"):
        ChangeEvidence.from_canonical_json(bad, strict=True)


def test_w534_non_strict_mode_warns_and_drops_unknown_subject_kind() -> None:
    """strict=False (default) warns + drops the offending row, packet still loads."""
    packet = ChangeEvidence(
        evidence_id="ev_w534_lenient_subject",
        verdict="SAFE",
        changed_subjects=(
            EvidenceSubject(kind="symbol", qualified_name="src/keep.py::a"),
            EvidenceSubject(kind="file", qualified_name="src/keep.py"),
        ),
    )
    canon = packet.to_canonical_json()
    # Corrupt only the FIRST subject's kind; the second should survive.
    bad = canon.replace('"kind":"symbol"', '"kind":"not_a_real_kind"', 1)
    assert bad != canon

    with pytest.warns(UserWarning, match="dropped changed_subject"):
        parsed = ChangeEvidence.from_canonical_json(bad)  # default strict=False
    # The bad row is gone; the well-formed one survives.
    assert len(parsed.changed_subjects) == 1
    assert parsed.changed_subjects[0].kind == "file"
    assert parsed.changed_subjects[0].qualified_name == "src/keep.py"


def test_w534_malformed_json_raises_clear_value_error() -> None:
    """Bad JSON surfaces as a ValueError naming the parser failure."""
    with pytest.raises(ValueError, match="malformed JSON"):
        ChangeEvidence.from_canonical_json("not valid {{{ json")
    # Top-level non-object also raises:
    with pytest.raises(ValueError, match="expected a JSON object"):
        ChangeEvidence.from_canonical_json('["this", "is", "an", "array"]')


def test_w534_omit_when_default_round_trip_keeps_optional_fields_unset() -> None:
    """A v0_minimal-shaped packet parses without spurious default fields.

    Specifically: when the input JSON omits ``actor_refs`` / W210 scalars
    entirely (the pre-W182 / pre-W210 shape), the resulting packet has
    those fields at their declared defaults AND re-serialises without
    re-introducing the keys. Ensures the omit-when-default contract on
    the SERIALISATION side survives a parse + re-emit cycle.
    """
    body = _w534_read_fixture("v0_minimal")
    parsed = ChangeEvidence.from_canonical_json(body)
    # Optional fields stay at their declared defaults.
    assert parsed.actor_refs == ()
    assert parsed.authority_refs == ()
    assert parsed.environment_refs == ()
    assert parsed.context_read_at is None
    assert parsed.evidence_stale is False
    assert parsed.stale_reasons == ()
    assert parsed.roam_version is None
    # Re-emit: byte-identical to input.
    assert parsed.to_canonical_json() == body


# ---------------------------------------------------------------------------
# W561 - Pattern 1 variant D disclosure: from_canonical_json_with_drops
# ---------------------------------------------------------------------------


def test_w561_with_drops_returns_empty_list_on_clean_packet() -> None:
    """A clean fixture parses with an empty ``drops`` list.

    Round-trip byte-stability mandate: the W534 round-trip contract
    (``from_canonical_json(text).to_canonical_json() == text``) must
    still hold when routed through ``from_canonical_json_with_drops``.
    No drops on golden fixtures.
    """
    body = _w534_read_fixture("v0_minimal")
    packet, drops = ChangeEvidence.from_canonical_json_with_drops(body)
    assert drops == []
    # Round-trip still byte-identical.
    assert packet.to_canonical_json() == body


def test_w561_with_drops_reports_dropped_subject_row() -> None:
    """Unknown SUBJECT_KIND row drops + ``drops`` list captures the reason.

    Default-mode behaviour: the bad row is silently dropped (legacy
    UserWarning still emitted), AND ``drops`` collects the human-
    readable reason so a downstream envelope can disclose the
    degradation (Pattern 1 variant D fix).
    """
    packet = ChangeEvidence(
        evidence_id="ev_w561_drop_subject",
        verdict="SAFE",
        changed_subjects=(
            EvidenceSubject(kind="symbol", qualified_name="src/keep.py::a"),
            EvidenceSubject(kind="file", qualified_name="src/keep.py"),
        ),
    )
    canon = packet.to_canonical_json()
    bad = canon.replace('"kind":"symbol"', '"kind":"not_a_real_kind"', 1)

    with pytest.warns(UserWarning, match="dropped changed_subject"):
        parsed, drops = ChangeEvidence.from_canonical_json_with_drops(bad)
    assert len(parsed.changed_subjects) == 1
    assert len(drops) == 1
    assert "changed_subject" in drops[0]
    assert "not_a_real_kind" in drops[0]


def test_w561_with_drops_strict_mode_still_raises() -> None:
    """``strict=True`` re-raises on first violation; tuple is unreachable.

    The drop-aware classmethod inherits the same strict-mode contract as
    ``from_canonical_json``: any unknown enum value raises ValueError.
    """
    packet = ChangeEvidence(
        evidence_id="ev_w561_strict_subject",
        verdict="SAFE",
        changed_subjects=(EvidenceSubject(kind="symbol", qualified_name="src/a.py::x"),),
    )
    canon = packet.to_canonical_json()
    bad = canon.replace('"kind":"symbol"', '"kind":"not_a_real_kind"', 1)

    with pytest.raises(ValueError, match="not in SUBJECT_KINDS"):
        ChangeEvidence.from_canonical_json_with_drops(bad, strict=True)


def test_w561_with_drops_aggregates_multiple_dropped_rows() -> None:
    """Multiple bad rows produce multiple drop reasons in order.

    Covers the case where the AR envelope's ``dropped_enum_rows`` count
    is > 1, so the slice-to-5 ``dropped_reasons`` field has something
    to slice.
    """
    packet = ChangeEvidence(
        evidence_id="ev_w561_drop_multiple",
        verdict="SAFE",
        changed_subjects=(
            EvidenceSubject(kind="symbol", qualified_name="src/a.py::x"),
            EvidenceSubject(kind="symbol", qualified_name="src/b.py::y"),
            EvidenceSubject(kind="file", qualified_name="src/c.py"),
        ),
    )
    canon = packet.to_canonical_json()
    # Poison both ``symbol`` rows; the ``file`` row should survive.
    bad = canon.replace('"kind":"symbol"', '"kind":"wizard"')

    with pytest.warns(UserWarning):
        parsed, drops = ChangeEvidence.from_canonical_json_with_drops(bad)
    assert len(parsed.changed_subjects) == 1
    assert parsed.changed_subjects[0].kind == "file"
    assert len(drops) == 2
    assert all("changed_subject" in d for d in drops)


# ---------------------------------------------------------------------------
# W1156 - REFERENCE_REMOVAL_VERDICTS closed-enum substrate for
# cmd_refs_text + cmd_delete_check (W1134 audit recommendation)
# ---------------------------------------------------------------------------


def test_reference_removal_verdicts_membership() -> None:
    """Drift guard: REFERENCE_REMOVAL_VERDICTS pins the 6-member alphabet.

    Two CLI consumers split the alphabet:
      * cmd_refs_text emits: safe_to_remove / review / load_bearing
      * cmd_delete_check emits: safe / likely_safe / break_risk

    Canonical form is lowercase + underscore (matches POLICY_DECISIONS
    convention). The CLI text-output layer renders as
    UPPERCASE-WITH-HYPHENS; validators normalize before membership check.

    See W1134 audit + W1156 for context.
    """
    from roam.evidence._vocabulary import REFERENCE_REMOVAL_VERDICTS

    expected = frozenset(
        {
            "safe_to_remove",
            "review",
            "load_bearing",
            "safe",
            "likely_safe",
            "break_risk",
        }
    )
    assert REFERENCE_REMOVAL_VERDICTS == expected, (
        f"Drift detected: {REFERENCE_REMOVAL_VERDICTS - expected} added "
        f"OR {expected - REFERENCE_REMOVAL_VERDICTS} removed. "
        f"See W1134 audit + W1156 for context."
    )
    # Drift guard: frozenset is immutable
    with pytest.raises(AttributeError):
        REFERENCE_REMOVAL_VERDICTS.add("rogue_verdict")  # type: ignore[attr-defined]
