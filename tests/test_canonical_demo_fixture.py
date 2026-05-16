"""W216 — canonical demo fixture round-trip + content-hash invariants.

These tests guard ``templates/demos/canonical-evidence.json`` — the
single source-of-truth fixture for the buyer-readable PR Replay demo
(`canonical-pr-replay.md`) and its setup narrative
(`canonical-pr-context.md`).

The fixture exists so that any agent (or human) who points a fresh
``ChangeEvidence`` consumer at the file gets a byte-identical round
trip and a content_hash that verifies. The tests below pin both
properties.

Per the W216 directive, tests 3 + 4 exercise the W210 helpers
(``assurance_floor`` and ``evidence_completeness``) when they land
on ``ChangeEvidence``. As of W216, those helpers are NOT yet
implemented as methods on the dataclass, so the tests defer with
``pytest.skip`` until W210 lands.
"""

from __future__ import annotations

import json as _json
import pathlib

import pytest

from roam.evidence import ChangeEvidence

# Path to the fixture, anchored relative to this test file so the
# test runs identically from any cwd.
_FIXTURE_PATH = pathlib.Path(__file__).resolve().parent.parent / "templates" / "demos" / "canonical-evidence.json"


def _load_fixture_text() -> str:
    """Read the canonical fixture as raw UTF-8 text."""
    return _FIXTURE_PATH.read_text(encoding="utf-8")


def _build_packet_from_dict(payload: dict) -> ChangeEvidence:
    """Reconstruct a ``ChangeEvidence`` packet from the on-disk dict.

    The fixture is canonical JSON (sorted keys, no whitespace), so we
    rebuild the dataclass instance by feeding the dict fields back
    into the constructor. Tuple fields stay as lists in the JSON
    payload; the constructor coerces them via ``__post_init__``.

    Nested dataclasses (``EvidenceSubject``, ``EvidenceArtifact``,
    ``ActorRef`` / ``AuthorityRef`` / ``EnvironmentRef``) are
    reconstructed from their dict form. We import them here lazily
    to keep the module-level import graph minimal.
    """
    from roam.evidence import (
        ActorRef,
        AuthorityRef,
        EnvironmentRef,
        EvidenceArtifact,
        EvidenceSubject,
    )

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

    return ChangeEvidence(
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
        context_refs=tuple(_artifact(a) for a in payload.get("context_refs", ())),
        changed_subjects=tuple(_subject(s) for s in payload.get("changed_subjects", ())),
        findings=tuple(payload.get("findings", ())),
        policy_decisions=tuple(payload.get("policy_decisions", ())),
        tests_required=tuple(payload.get("tests_required", ())),
        tests_run=tuple(payload.get("tests_run", ())),
        approvals=tuple(payload.get("approvals", ())),
        accepted_risks=tuple(payload.get("accepted_risks", ())),
        artifacts=tuple(_artifact(a) for a in payload.get("artifacts", ())),
        actor_refs=tuple(_actor(a) for a in payload.get("actor_refs", ())),
        authority_refs=tuple(_authority(a) for a in payload.get("authority_refs", ())),
        environment_refs=tuple(_env(e) for e in payload.get("environment_refs", ())),
        redactions=tuple(payload.get("redactions", ())),
        content_hash=payload.get("content_hash"),
        signature_ref=payload.get("signature_ref"),
    )


# ---------------------------------------------------------------------------
# 1. Round trip — parse → serialize gives byte-identical output
# ---------------------------------------------------------------------------


def test_canonical_evidence_json_round_trips():
    """Parsing the fixture and re-serialising must produce the same bytes.

    The fixture is canonical JSON (sorted keys, ``(",", ":")``
    separators). Loading it via ``json.loads`` and then dumping with
    the same canonical params reproduces the on-disk string byte-for-
    byte. This pins the determinism contract on the producer side.
    """
    raw = _load_fixture_text()
    parsed = _json.loads(raw)
    redumped = _json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert redumped == raw, (
        "Fixture is not canonical JSON. Re-serializing with sort_keys + "
        "(',',':') separators produced a different byte string. The "
        "generator script must be re-run to regenerate the fixture."
    )


# ---------------------------------------------------------------------------
# 2. Content hash matches the declared field
# ---------------------------------------------------------------------------


def test_canonical_evidence_content_hash_matches_declared():
    """The fixture's ``content_hash`` matches a fresh ``compute_content_hash``.

    Loads the fixture, reconstructs the ``ChangeEvidence`` packet
    from the dict, calls ``compute_content_hash()`` (which zeroes
    the hash field and sha256s the canonical JSON), and compares
    against the value the fixture declares. A mismatch means the
    fixture has drifted from its declared hash and downstream
    consumers will no longer be able to verify it.
    """
    raw = _load_fixture_text()
    payload = _json.loads(raw)
    declared = payload.get("content_hash")
    assert isinstance(declared, str) and len(declared) == 64, "Fixture is missing a 64-hex-char content_hash field."

    packet = _build_packet_from_dict(payload)
    fresh = packet.compute_content_hash()
    assert fresh == declared, (
        f"content_hash drift: fixture declares {declared} but "
        f"compute_content_hash() returned {fresh}. The fixture must "
        f"be regenerated."
    )


# ---------------------------------------------------------------------------
# 3. + 4. W210 helpers (deferred until they land on the dataclass)
# ---------------------------------------------------------------------------


def test_canonical_evidence_assurance_floor_passes():
    """The packet should clear the W210 ``assurance_floor`` once it lands.

    The W216 directive specifies this test for after W210 has shipped
    the ``assurance_floor()`` helper on ``ChangeEvidence``. Today, no
    such method exists; the demo packet is built to clear that bar
    (every assurance slot is populated), so the assertion will hold
    on the day W210 lands.
    """
    if not hasattr(ChangeEvidence, "assurance_floor"):
        pytest.skip("waits for W210 (assurance_floor helper on ChangeEvidence)")
    packet = _build_packet_from_dict(_json.loads(_load_fixture_text()))
    result = packet.assurance_floor()  # type: ignore[attr-defined]
    # Accept either a bool or a {"passes": bool, ...} contract.
    if isinstance(result, dict):
        assert result.get("passes") is True, f"assurance_floor on the canonical demo packet did not pass: {result!r}"
    else:
        assert bool(result) is True, "assurance_floor on the canonical demo packet returned falsy"


def test_canonical_evidence_completeness_is_8_of_8():
    """The packet should fill all 8 evidence questions per W210.

    Like ``test_canonical_evidence_assurance_floor_passes``, this
    pins the IDEAL-case 8/8 coverage the demo is built to embody.
    Until W210 ships the helper, the test defers.
    """
    if not hasattr(ChangeEvidence, "evidence_completeness"):
        pytest.skip("waits for W210 (evidence_completeness helper on ChangeEvidence)")
    packet = _build_packet_from_dict(_json.loads(_load_fixture_text()))
    result = packet.evidence_completeness()  # type: ignore[attr-defined]
    # Accept a couple of plausible shapes the helper might return.
    if isinstance(result, dict):
        complete = result.get("complete") or result.get("answered") or 0
        missing = result.get("missing") or 0
        assert complete == 8, (
            f"evidence_completeness reported {complete} complete answers; the canonical demo packet is built for 8/8."
        )
        assert missing == 0, (
            f"evidence_completeness reported {missing} missing answers; "
            f"the canonical demo packet is built for 0 missing."
        )
    elif isinstance(result, tuple) and len(result) == 2:
        complete, missing = result
        assert complete == 8 and missing == 0
    else:
        # Fallback: treat as a scalar count.
        assert result == 8
