"""W276 - insufficient-tier demo fixture round-trip + banner invariants.

Companion to ``tests/test_canonical_demo_fixture.py`` (W216). Where
that file pins the STRONG-tier canonical packet, this file pins the
INSUFFICIENT-tier packet that ships at
``templates/demos/insufficient-evidence.json``.

The insufficient packet exists so a buyer can see what the W259
honest-coverage banner produces for a thin third-party export - a
PR-replay-shaped artifact that covers Q4 (changed_subjects) only and
declares Q8 (accept) as ``producer_not_available`` per W261. The
banner threshold table classifies it as INSUFFICIENT.

These tests pin five invariants:

1. The fixture is canonical JSON (parse -> reserialise byte-stable).
2. The declared ``content_hash`` matches ``compute_content_hash()``.
3. The packet parses as a valid v0 ``ChangeEvidence``.
4. ``evidence_completeness()`` returns ``complete=1 / partial=1 /
   missing=6`` - the exact (1, 1, 6) coverage the W259 banner tests
   use as the INSUFFICIENT-tier fixture.
5. ``classify_evidence_coverage`` returns the INSUFFICIENT tier and
   the rationale string that warns against publishing.
"""

from __future__ import annotations

import json as _json
import pathlib

from roam.evidence import ChangeEvidence, EvidenceSubject
from roam.evidence.banner import (
    TIER_INSUFFICIENT,
    classify_evidence_coverage,
)


# Path to the fixture, anchored relative to this test file so the
# test runs identically from any cwd.
_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "templates"
    / "demos"
    / "insufficient-evidence.json"
)


def _load_fixture_text() -> str:
    """Read the insufficient-tier fixture as raw UTF-8 text."""
    return _FIXTURE_PATH.read_text(encoding="utf-8")


def _build_packet_from_dict(payload: dict) -> ChangeEvidence:
    """Reconstruct a ``ChangeEvidence`` packet from the on-disk dict.

    The insufficient fixture only populates ``changed_subjects`` and
    ``redactions``; the other tuple fields are empty arrays in the
    JSON payload. We still pass them through the constructor so the
    coercion path is exercised.
    """

    def _subject(d: dict) -> EvidenceSubject:
        return EvidenceSubject(
            kind=d["kind"],
            qualified_name=d["qualified_name"],
            repo_id=d.get("repo_id"),
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
        context_refs=(),
        changed_subjects=tuple(
            _subject(s) for s in payload.get("changed_subjects", ())
        ),
        findings=tuple(payload.get("findings", ())),
        policy_decisions=tuple(payload.get("policy_decisions", ())),
        tests_required=tuple(payload.get("tests_required", ())),
        tests_run=tuple(payload.get("tests_run", ())),
        approvals=tuple(payload.get("approvals", ())),
        accepted_risks=tuple(payload.get("accepted_risks", ())),
        artifacts=(),
        actor_refs=(),
        authority_refs=(),
        environment_refs=(),
        redactions=tuple(payload.get("redactions", ())),
        content_hash=payload.get("content_hash"),
        signature_ref=payload.get("signature_ref"),
    )


# ---------------------------------------------------------------------------
# 1. Round trip - parse -> serialize gives byte-identical output
# ---------------------------------------------------------------------------


def test_insufficient_demo_packet_is_canonical_json() -> None:
    """Parsing the fixture and re-serialising must produce the same bytes.

    Mirrors the W216 round-trip test. If this fails, the fixture has
    drifted from the canonical JSON form and the generator script (or
    a manual edit) must be re-applied.
    """
    raw = _load_fixture_text()
    parsed = _json.loads(raw)
    redumped = _json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert redumped == raw, (
        "Insufficient fixture is not canonical JSON. Re-serializing with "
        "sort_keys + (',',':') separators produced a different byte string."
    )


# ---------------------------------------------------------------------------
# 2. Parses as a v0 ChangeEvidence
# ---------------------------------------------------------------------------


def test_insufficient_demo_packet_parses_as_v0_change_evidence() -> None:
    """The fixture loads into a valid v0 ``ChangeEvidence`` packet.

    Schema version is ``"1.0.0"`` (the same as the canonical
    fixture; W210 additions are omitted when at default values so
    the version stays stable).
    """
    raw = _load_fixture_text()
    payload = _json.loads(raw)
    packet = _build_packet_from_dict(payload)
    assert packet.schema_version == "1.0.0"
    assert packet.evidence_id == "evidence:third-party-ci-export-20260514"
    # The packet's identity surface is intentionally empty - those
    # are the gaps the banner exists to flag.
    assert packet.actor_refs == ()
    assert packet.agent_id is None
    assert packet.human_actor is None


# ---------------------------------------------------------------------------
# 3. Content hash matches the declared field
# ---------------------------------------------------------------------------


def test_insufficient_demo_packet_content_hash_valid() -> None:
    """The fixture's declared ``content_hash`` matches a fresh recompute.

    Mirrors the W216 hash-match test. A drift here means downstream
    consumers can no longer verify the packet - the fixture is broken.
    """
    raw = _load_fixture_text()
    payload = _json.loads(raw)
    declared = payload.get("content_hash")
    assert isinstance(declared, str) and len(declared) == 64, (
        "Insufficient fixture is missing a 64-hex-char content_hash field."
    )

    packet = _build_packet_from_dict(payload)
    fresh = packet.compute_content_hash()
    assert fresh == declared, (
        f"content_hash drift on insufficient fixture: declared {declared}, "
        f"compute_content_hash() returned {fresh}. Regenerate the fixture."
    )


# ---------------------------------------------------------------------------
# 4. evidence_completeness() returns the (1, 1, 6) INSUFFICIENT tuple
# ---------------------------------------------------------------------------


def test_insufficient_demo_packet_evidence_completeness_is_1_1_6() -> None:
    """The packet covers exactly Q4 (complete) and Q8 (partial via redaction).

    The W259 banner test (``tests/test_evidence_banner.py
    ::test_banner_insufficient_tier``) uses the (1, 1, 6) shape as its
    INSUFFICIENT-tier fixture. This test asserts the demo packet on
    disk produces the same counts so the fixture and the banner are
    in lock-step.
    """
    raw = _load_fixture_text()
    payload = _json.loads(raw)
    packet = _build_packet_from_dict(payload)
    scores = packet.evidence_completeness()

    # Per-question scoreboard - Q4 is the only complete answer, Q8 is
    # partial because of the W261 producer_not_available redaction.
    assert scores["Q1"] == "missing"
    assert scores["Q2"] == "missing"
    assert scores["Q3"] == "missing"
    assert scores["Q4"] == "complete"
    assert scores["Q5"] == "missing"
    assert scores["Q6"] == "missing"
    assert scores["Q7"] == "missing"
    assert scores["Q8"] == "partial"

    # Totals - must hit (1, 1, 6, 0) so the banner classifies this as
    # INSUFFICIENT (complete < 7 AND missing > 3).
    assert scores["complete"] == 1
    assert scores["partial"] == 1
    assert scores["missing"] == 6
    assert scores["not_applicable"] == 0


# ---------------------------------------------------------------------------
# 5. Banner classifies as INSUFFICIENT
# ---------------------------------------------------------------------------


def test_insufficient_demo_packet_scores_insufficient_tier() -> None:
    """``classify_evidence_coverage`` returns the INSUFFICIENT tier.

    The rationale string must include the "1 of 8" count and the
    "do not publish" warning so a reviewer scanning the banner sees
    the gap on its own line without consulting any other section.
    """
    raw = _load_fixture_text()
    payload = _json.loads(raw)
    packet = _build_packet_from_dict(payload)

    tier_id, label, rationale = classify_evidence_coverage(packet)
    assert tier_id == TIER_INSUFFICIENT
    assert label == "Insufficient evidence"
    assert "1 of 8" in rationale
    assert "do not publish" in rationale.lower()
