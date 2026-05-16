"""W274 — tests for ``roam evidence-doctor``.

These tests exercise the CLI command end-to-end via ``CliRunner``
against synthetic / canonical ``ChangeEvidence`` packets written to
``tmp_path``.

Test inventory:

1. ``test_doctor_passes_on_canonical_packet`` — load
   ``templates/demos/canonical-evidence.json`` (W216) and assert
   verdict starts with "PASS".
2. ``test_doctor_warns_on_partial_packet`` — synthesize a
   STRONG-but-partial packet and assert verdict starts with "WARN" and
   banner_tier is "partial".
3. ``test_doctor_fails_on_content_hash_mismatch`` — load packet, mutate
   one field, assert verdict starts with "FAIL".
4. ``test_doctor_emits_next_steps_for_partial`` — synthesize a packet
   missing Q8 and assert next_steps mentions Q8.
5. ``test_doctor_validates_closed_enums`` — synthesize a packet with
   ``subject_kind="nonsense"``, assert verdict starts with "FAIL".
6. ``test_doctor_json_envelope_carries_verdict`` — assert JSON output
   has a non-empty ``summary.verdict``.
7. ``test_doctor_handles_missing_content_hash`` — packet with no
   ``content_hash`` should not FAIL on hash (it's a WARN at most).
8. ``test_doctor_handles_malformed_json`` — packet that's not JSON
   should FAIL gracefully (exit code 2, no traceback).
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    """Invoke ``roam evidence-doctor`` and return ``(exit_code, output)``."""
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["evidence-doctor", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


def _write_packet(path: Path, payload: dict) -> Path:
    """Serialise a packet to disk and return the path."""
    path.write_text(_json.dumps(payload), encoding="utf-8")
    return path


def _hash_packet(payload: dict) -> str:
    """Recompute the content_hash for a packet payload exactly the way
    the dataclass does (so synthetic test packets pass the doctor's
    hash check)."""
    import hashlib

    from roam.evidence.change_evidence import (
        _W182_OMIT_WHEN_EMPTY_FIELDS,
        _W210_OMIT_WHEN_DEFAULT_FIELDS,
    )

    stripped = dict(payload)
    stripped["content_hash"] = None
    for k in _W182_OMIT_WHEN_EMPTY_FIELDS:
        if stripped.get(k) == []:
            stripped.pop(k, None)
    for k, default in _W210_OMIT_WHEN_DEFAULT_FIELDS.items():
        if k in stripped and stripped[k] == default:
            stripped.pop(k, None)
    canonical = _json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _synthetic_packet(
    *,
    include_actor_refs: bool = True,
    include_authority_refs: bool = True,
    include_context_refs: bool = True,
    include_changed_subjects: bool = True,
    include_risk_level: bool = True,
    include_policy_decisions: bool = True,
    include_tests_run: bool = True,
    include_approvals: bool = True,
    include_redactions: bool = False,
    subject_kind: str = "symbol",
    stamp_hash: bool = True,
) -> dict:
    """Build a synthetic ``ChangeEvidence``-shaped dict.

    Each ``include_*`` flag toggles whether the corresponding evidence
    question is satisfied (so callers can build STRONG / PARTIAL /
    INSUFFICIENT packets cleanly).
    """
    p: dict = {
        "evidence_id": "ev_test_doctor",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:test",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "REVIEW",
        "risk_level": "low" if include_risk_level else None,
        "context_refs": [
            {
                "artifact_id": "raw_envelope:preflight",
                "kind": "raw_envelope",
                "path": ".roam/runs/test/preflight.json",
                "content_hash": "c" * 64,
                "content_inline": None,
                "extra": {},
                "redactions": [],
            }
        ]
        if include_context_refs
        else [],
        "changed_subjects": [
            {
                "kind": subject_kind,
                "qualified_name": "app/svc::do_thing",
                "repo_id": None,
                "extra": {},
            }
        ]
        if include_changed_subjects
        else [],
        "findings": [
            {
                "finding_id_str": "test::finding:1",
                "claim": "low-severity finding",
                "severity": "low",
            }
        ],
        "policy_decisions": [{"rule_id": "test:rule", "outcome": "allowed"}] if include_policy_decisions else [],
        "tests_required": ["tests/test_foo.py::test_one"],
        "tests_run": [{"test_id": "tests/test_foo.py::test_one", "outcome": "passed"}] if include_tests_run else [],
        "approvals": [{"approval_id": "ap:1", "approver": "alice", "scope": "merge"}] if include_approvals else [],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": ["policy"] if include_redactions else [],
        "actor_refs": [
            {
                "actor_id": "agent:test",
                "actor_kind": "agent",
                "display_name": "Test agent",
                "trust_tier": "self_reported_agent",
                "extra": {},
            }
        ]
        if include_actor_refs
        else [],
        "authority_refs": [
            {
                "authority_id": "mode:safe_edit",
                "authority_kind": "mode",
                "granted_by": "system",
                "source": "mode",
                "extra": {},
            }
        ]
        if include_authority_refs
        else [],
        "environment_refs": [
            {
                "env_id": "local",
                "env_kind": "local_run",
                "extra": {},
            }
        ],
        "signature_ref": None,
        "content_hash": None,
    }
    if stamp_hash:
        p["content_hash"] = _hash_packet(p)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_doctor_passes_on_canonical_packet() -> None:
    """The W216 canonical demo packet represents the IDEAL 8/8 state.

    W281 added trust-tier inspection, which initially downgraded the
    canonical packet to WARN because it shipped with a
    ``self_reported_agent`` actor_ref. W286 repaired the fixture so the
    ideal demo actually PASSes: actor_refs now carry ``git_author`` and
    ``local_env`` tiers (no ``self_reported_agent``, no ``unknown``),
    matching what a CI-mediated agent run with an HMAC-signed run
    ledger would produce.
    """
    path = "templates/demos/canonical-evidence.json"
    code, out = _invoke(path)
    assert code == 0, out
    first_line = out.splitlines()[0]
    # W286: repaired fixture should PASS, not WARN.
    assert first_line.startswith("VERDICT: PASS"), first_line
    assert "STRONG coverage" in first_line, first_line


def test_doctor_warns_on_partial_packet(tmp_path: Path) -> None:
    """A packet missing context (Q3) and approvals (Q8) is partial."""
    p = _synthetic_packet(
        include_context_refs=False,
        include_approvals=False,
        include_redactions=False,
    )
    path = _write_packet(tmp_path / "partial.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    assert payload["summary"]["verdict"].startswith("WARN"), payload["summary"]
    assert payload["summary"]["banner_tier"] in ("partial", "insufficient")


def test_doctor_fails_on_content_hash_mismatch(tmp_path: Path) -> None:
    """Mutating a field after stamping the hash trips the FAIL branch."""
    p = _synthetic_packet()
    # Mutate verdict AFTER stamping the hash → recompute disagrees.
    p["verdict"] = "MUTATED"
    path = _write_packet(tmp_path / "broken.json", p)
    code, out = _invoke(str(path), json_mode=True)
    payload = _json.loads(out)
    assert payload["summary"]["verdict"].startswith("FAIL"), payload["summary"]
    assert payload["summary"]["hash_ok"] is False
    assert payload["content_hash"]["state"] == "mismatch"


def test_doctor_emits_next_steps_for_partial(tmp_path: Path) -> None:
    """A packet missing Q8 surfaces a next-step mentioning Q8."""
    p = _synthetic_packet(
        include_approvals=False,
        include_redactions=False,
    )
    path = _write_packet(tmp_path / "no_q8.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    next_steps = payload.get("next_steps", [])
    # Q8 should appear among the named steps.
    q_names = {s.get("q") for s in next_steps}
    assert "Q8" in q_names, next_steps


def test_doctor_validates_closed_enums(tmp_path: Path) -> None:
    """``subject_kind="nonsense"`` should trigger a closed-enum FAIL."""
    p = _synthetic_packet(subject_kind="nonsense", stamp_hash=False)
    # Stamp a hash matching the malformed payload so the doctor reaches
    # the enum check rather than failing on hash first.
    p["content_hash"] = _hash_packet(p)
    path = _write_packet(tmp_path / "bad_enum.json", p)
    code, out = _invoke(str(path), json_mode=True)
    payload = _json.loads(out)
    assert payload["summary"]["verdict"].startswith("FAIL"), payload["summary"]
    assert payload["summary"]["enum_violations"] >= 1
    # The violation should name the changed_subjects field.
    violations = payload.get("enum_violations", [])
    assert any("changed_subjects" in v["field"] for v in violations), violations


def test_doctor_json_envelope_carries_verdict(tmp_path: Path) -> None:
    """JSON envelope must have a non-empty ``summary.verdict``."""
    p = _synthetic_packet()
    path = _write_packet(tmp_path / "ok.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    verdict = payload["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict
    # Envelope shape sanity
    assert payload["command"] == "evidence-doctor"
    assert "agent_contract" in payload
    assert isinstance(payload["agent_contract"]["facts"], list)
    assert payload["agent_contract"]["facts"], "facts should be non-empty"


def test_doctor_handles_missing_content_hash(tmp_path: Path) -> None:
    """A packet with no ``content_hash`` stamped is not a FAIL on hash."""
    p = _synthetic_packet(stamp_hash=False)
    p.pop("content_hash", None)
    path = _write_packet(tmp_path / "unstamped.json", p)
    code, out = _invoke(str(path), json_mode=True)
    payload = _json.loads(out)
    # Not a FAIL: hash is "not_stamped", not "mismatch".
    assert payload["content_hash"]["state"] == "not_stamped"
    # Verdict can be PASS or WARN depending on banner — but never FAIL
    # purely because the packet lacks a stamped hash.
    assert not payload["summary"]["verdict"].startswith("FAIL"), payload["summary"]


def test_doctor_handles_malformed_json(tmp_path: Path) -> None:
    """A non-JSON packet should FAIL gracefully (exit 2, no traceback)."""
    bad = tmp_path / "garbage.json"
    bad.write_text("this is not json at all {", encoding="utf-8")
    code, out = _invoke(str(bad), json_mode=True)
    # Exit code 2 means hard load failure; envelope still parses.
    assert code == 2, out
    payload = _json.loads(out)
    assert payload["summary"]["verdict"].startswith("FAIL"), payload["summary"]
    assert payload["summary"]["schema_ok"] is False


# ---------------------------------------------------------------------------
# W281 — actor-trust-tier surface tests
# ---------------------------------------------------------------------------


def _packet_with_actor_refs(
    refs: list[dict],
    *,
    stamp_hash: bool = True,
) -> dict:
    """Build a STRONG-coverage packet with caller-controlled actor_refs.

    All other evidence questions are filled so the banner classifies as
    STRONG — that way the trust-tier WARN signal is the ONLY downgrade
    knob, and the test isolates the W281 logic from the banner ladder.
    """
    p = _synthetic_packet(stamp_hash=False)
    p["actor_refs"] = refs
    if stamp_hash:
        p["content_hash"] = _hash_packet(p)
    return p


def test_doctor_reports_trust_tier_counts(tmp_path: Path) -> None:
    """All 5 trust-tier keys present in JSON envelope with correct counts.

    Pattern-2 always-emit: zero-count tiers still appear in the dict.
    """
    refs = [
        {
            "actor_id": "ci:1",
            "actor_kind": "ci_runner",
            "trust_tier": "verified_ci",
            "extra": {},
        },
        {
            "actor_id": "human:alice",
            "actor_kind": "human",
            "trust_tier": "git_author",
            "extra": {},
        },
        {
            "actor_id": "agent:claude",
            "actor_kind": "agent",
            "trust_tier": "self_reported_agent",
            "extra": {},
        },
        {
            "actor_id": "agent:other",
            "actor_kind": "agent",
            "trust_tier": "self_reported_agent",
            "extra": {},
        },
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "mixed.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    tiers = payload["trust_tiers"]
    # All 5 keys present (Pattern-2 always-emit).
    assert set(tiers.keys()) == {
        "verified_ci",
        "git_author",
        "local_env",
        "self_reported_agent",
        "unknown",
    }, tiers
    assert tiers["verified_ci"] == 1
    assert tiers["git_author"] == 1
    assert tiers["local_env"] == 0
    assert tiers["self_reported_agent"] == 2
    assert tiers["unknown"] == 0


def test_doctor_warns_on_self_reported_agent(tmp_path: Path) -> None:
    """STRONG-coverage packet with a self_reported_agent ref downgrades to WARN."""
    refs = [
        {
            "actor_id": "agent:claude",
            "actor_kind": "agent",
            "trust_tier": "self_reported_agent",
            "extra": {},
        }
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "self_reported.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    verdict = payload["summary"]["verdict"]
    assert verdict.startswith("WARN"), verdict
    # Verdict line must name the gap inline.
    assert "STRONG coverage but actor identity unverified" in verdict, verdict
    assert "self_reported_agent" in verdict, verdict
    # Banner itself stays STRONG — only the verdict reflects the trust gap.
    assert payload["summary"]["banner_tier"] == "strong"
    assert payload["summary"]["trust_warnings_count"] == 1


def test_doctor_warns_on_unknown_tier(tmp_path: Path) -> None:
    """STRONG-coverage packet with an unknown-tier ref also yields WARN."""
    refs = [
        {
            "actor_id": "external:?",
            "actor_kind": "external",
            "trust_tier": "unknown",
            "extra": {},
        }
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "unknown_tier.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    verdict = payload["summary"]["verdict"]
    assert verdict.startswith("WARN"), verdict
    assert "unknown" in verdict, verdict
    assert payload["summary"]["banner_tier"] == "strong"
    assert payload["summary"]["trust_warnings_count"] == 1


def test_doctor_passes_when_all_actors_verified_ci(tmp_path: Path) -> None:
    """STRONG-coverage packet with only verified_ci refs scores PASS."""
    refs = [
        {
            "actor_id": "ci:github/run/1",
            "actor_kind": "ci_runner",
            "trust_tier": "verified_ci",
            "extra": {},
        },
        {
            "actor_id": "human:alice",
            "actor_kind": "human",
            "trust_tier": "verified_ci",
            "extra": {},
        },
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "all_verified.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    verdict = payload["summary"]["verdict"]
    assert verdict.startswith("PASS"), verdict
    assert payload["summary"]["banner_tier"] == "strong"
    assert payload["summary"]["trust_warnings_count"] == 0
    # Trust-tier counts must still surface with all 5 keys.
    tiers = payload["trust_tiers"]
    assert tiers["verified_ci"] == 2
    assert tiers["self_reported_agent"] == 0
    assert tiers["unknown"] == 0


def test_doctor_fails_on_invalid_trust_tier(tmp_path: Path) -> None:
    """An out-of-vocabulary trust_tier must trigger a closed-enum FAIL."""
    refs = [
        {
            "actor_id": "agent:fake",
            "actor_kind": "agent",
            "trust_tier": "totally-fake",
            "extra": {},
        }
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "bad_tier.json", p)
    code, out = _invoke(str(path), json_mode=True)
    payload = _json.loads(out)
    assert payload["summary"]["verdict"].startswith("FAIL"), payload["summary"]
    assert payload["summary"]["enum_violations"] >= 1
    violations = payload.get("enum_violations", [])
    assert any("trust_tier" in v["field"] and v["value"] == "totally-fake" for v in violations), violations


def test_doctor_trust_warnings_array_carries_actor_id(tmp_path: Path) -> None:
    """``trust_warnings[]`` entries carry actor_ref_index + actor_id + tier + rationale."""
    refs = [
        {
            "actor_id": "ci:run/42",
            "actor_kind": "ci_runner",
            "trust_tier": "verified_ci",
            "extra": {},
        },
        {
            "actor_id": "agent:claude-test",
            "actor_kind": "agent",
            "trust_tier": "self_reported_agent",
            "extra": {},
        },
    ]
    p = _packet_with_actor_refs(refs)
    path = _write_packet(tmp_path / "warn_array.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    warnings = payload["trust_warnings"]
    # Only the self_reported_agent ref triggers a warning entry.
    assert len(warnings) == 1, warnings
    w = warnings[0]
    assert set(w.keys()) >= {
        "actor_ref_index",
        "actor_id",
        "trust_tier",
        "rationale",
    }, w
    assert w["actor_ref_index"] == 1  # second ref (zero-indexed)
    assert w["actor_id"] == "agent:claude-test"
    assert w["trust_tier"] == "self_reported_agent"
    assert "self-reported" in w["rationale"].lower(), w["rationale"]


# ---------------------------------------------------------------------------
# W280 - packet-size budget surface tests
# ---------------------------------------------------------------------------


def _packet_with_inline_artifacts(*, count: int, inline_size: int, stamp_hash: bool = True) -> dict:
    """Build a packet with N inline artifacts each of size ``inline_size``.

    Used for the oversized-after-truncation test: the doctor reads a
    raw dict (not a dataclass), so the packet on disk doesn't get the
    in-process ``_apply_size_budget`` treatment. The doctor reports
    ``packet_size_bytes`` directly from the canonical-JSON byte count
    of what it loaded.
    """
    big = "x" * inline_size
    p = _synthetic_packet(stamp_hash=False)
    p["artifacts"] = [
        {
            "artifact_id": f"raw:{i}",
            "kind": "raw_envelope",
            "path": None,
            "content_hash": None,
            "content_inline": big,
            "redactions": [],
            "extra": {},
        }
        for i in range(count)
    ]
    if stamp_hash:
        p["content_hash"] = _hash_packet(p)
    return p


def test_doctor_reports_packet_size_bytes(tmp_path: Path) -> None:
    """JSON envelope carries a top-level ``packet_size`` block + summary keys."""
    from roam.evidence import PACKET_SIZE_BUDGET_BYTES

    p = _synthetic_packet()
    path = _write_packet(tmp_path / "ok.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)

    # Top-level packet_size block
    assert "packet_size" in payload, payload.keys()
    pkt = payload["packet_size"]
    assert set(pkt.keys()) == {"bytes", "budget_bytes", "budget_state"}, pkt
    assert isinstance(pkt["bytes"], int) and pkt["bytes"] > 0
    assert pkt["budget_bytes"] == PACKET_SIZE_BUDGET_BYTES
    assert pkt["budget_state"] in ("within_budget", "oversized_after_truncation")

    # Summary mirrors the size keys for one-glance consumers.
    assert payload["summary"]["packet_size_bytes"] == pkt["bytes"]
    assert payload["summary"]["budget_state"] == pkt["budget_state"]


def test_doctor_reports_within_budget_state(tmp_path: Path) -> None:
    """A small synthetic packet reports budget_state == 'within_budget'."""
    p = _synthetic_packet()
    path = _write_packet(tmp_path / "small.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    assert payload["packet_size"]["budget_state"] == "within_budget"
    assert payload["summary"]["budget_state"] == "within_budget"


def test_doctor_warns_on_oversized_after_truncation(tmp_path: Path) -> None:
    """A synthetic packet whose canonical JSON exceeds 256 KiB downgrades to WARN.

    The doctor reads the on-disk packet as-is and reports the canonical
    JSON size; it does NOT run the in-process ``_apply_size_budget``
    (that's a producer-side step). When a stored packet exceeds the
    budget, ``budget_state`` is ``oversized_after_truncation`` and the
    verdict line names the bloat inline.

    The synthetic packet starts with a ``self_reported_agent`` actor_ref
    (W281 default); we override it to ``verified_ci`` so the trust-tier
    WARN doesn't preempt the size-WARN in the verdict ladder. Both
    signals can coexist (FAIL > trust WARN > size WARN > banner WARN >
    PASS); this test isolates the size signal.
    """
    # 40 artifacts * 8 KiB inline = ~320 KiB, comfortably over 256 KiB.
    p = _packet_with_inline_artifacts(count=40, inline_size=8 * 1024, stamp_hash=False)
    # Upgrade the actor_ref's tier so size is the only WARN signal.
    p["actor_refs"] = [
        {
            "actor_id": "ci:run/oversized",
            "actor_kind": "ci_runner",
            "trust_tier": "verified_ci",
            "extra": {},
        }
    ]
    p["content_hash"] = _hash_packet(p)
    path = _write_packet(tmp_path / "oversized.json", p)
    code, out = _invoke(str(path), json_mode=True)
    assert code == 0, out
    payload = _json.loads(out)
    # Size is over budget
    assert payload["packet_size"]["budget_state"] == "oversized_after_truncation"
    assert payload["packet_size"]["bytes"] > payload["packet_size"]["budget_bytes"]
    # Verdict is WARN (oversized contributes WARN, not FAIL).
    verdict = payload["summary"]["verdict"]
    assert verdict.startswith("WARN"), verdict
    # Verdict line names the bloat inline.
    assert "oversized" in verdict.lower() or "bytes" in verdict.lower(), verdict


def test_doctor_packet_size_block_emitted_on_load_failure(tmp_path: Path) -> None:
    """The hard-load-failure envelope still emits packet_size keys.

    Pattern-2 always-emit: consumers can rely on the keys existing
    regardless of whether the load succeeded.
    """
    from roam.evidence import PACKET_SIZE_BUDGET_BYTES

    bad = tmp_path / "garbage.json"
    bad.write_text("{not json", encoding="utf-8")
    code, out = _invoke(str(bad), json_mode=True)
    assert code == 2, out
    payload = _json.loads(out)
    # packet_size block still present on a hard load failure.
    assert "packet_size" in payload, payload.keys()
    assert payload["packet_size"]["budget_bytes"] == PACKET_SIZE_BUDGET_BYTES
    assert payload["packet_size"]["budget_state"] == "within_budget"
    assert payload["packet_size"]["bytes"] == 0
    assert payload["summary"]["packet_size_bytes"] == 0
    assert payload["summary"]["budget_state"] == "within_budget"
