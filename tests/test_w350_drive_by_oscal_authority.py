"""W350 drive-by — ``roam evidence-oscal`` consumes ``authority_refs[]``.

The W350 series closes the producer-wired-but-not-consumed gap for the
authority axis. ``cmd_evidence_doctor`` + ``cmd_pr_replay`` already
surface ``authority_refs[]`` + per-kind counters from the collector
output. This wave extends the OSCAL ``--kind assessment-results``
projection so the same axis lands inside the AR document.

Two consumer surfaces tested here:

1. **OSCAL AR document.** ``build_oscal_assessment_results`` emits one
   observation per distinct ``authority_kind`` into
   ``results[0].observations[]`` (NIST OSCAL ``assessment-results``
   slot). Each observation lists every authority ref of that kind as
   a subject (capped at 10 with truncation disclosure). The flat
   per-ref ``results[0].props[name=authority-ref]`` projection is
   preserved for byte-stable backward-compat.

2. **CLI JSON envelope.** ``--json evidence-oscal --kind
   assessment-results --evidence <path>`` surfaces
   ``summary.authority_refs_count`` + Pattern-2 always-emit
   ``summary.authority_kinds`` dict (all 6 ``AUTHORITY_KINDS`` keys
   present, zero-padded). One ``agent_contract.facts`` entry
   advertises the count with the LAW-4 anchored ``records`` terminal.

Wording discipline (W184): every new string in the AR observation
emission must use ``maps to`` / ``supports evidence for`` / ``axis
observed``. Never ``certifies`` / ``complies`` / ``satisfies control``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.evidence._vocabulary import AUTHORITY_KINDS
from roam.evidence.oscal import ROAM_OSCAL_NS, build_oscal_assessment_results
from tests._helpers.wording_lint import scan_for_overclaims

FIXED_CLOCK = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _packet_with_mixed_authority_refs() -> dict:
    """ChangeEvidence dict with 6 mixed authority_refs across 5 kinds.

    Layout: 1 mode + 2 permits + 1 lease + 1 policy_rule + 1 approval
    + 0 token_scope. Exercises both the multi-ref-per-kind path
    (permits) and the zero-ref-per-kind always-emit path
    (token_scope).
    """
    authority_refs: list[dict] = [
        {
            "authority_id": "mode:safe_edit",
            "authority_kind": "mode",
            "granted_by": "system",
            "source": "mode",
            "extra": {},
        },
        {
            "authority_id": "perm_001",
            "authority_kind": "permit",
            "granted_by": "alice@example.com",
            "source": "permit",
            "extra": {},
        },
        {
            "authority_id": "perm_002",
            "authority_kind": "permit",
            "granted_by": "bob@example.com",
            "source": "permit",
            "extra": {},
        },
        {
            "authority_id": "lease_abc",
            "authority_kind": "lease",
            "granted_by": "agent:claude",
            "source": "inferred_fallback",
            "extra": {},
        },
        {
            "authority_id": "policy:rules/no_destructive_ops",
            "authority_kind": "policy_rule",
            "granted_by": None,
            "source": "rule_config",
            "extra": {},
        },
        {
            "authority_id": "approval:pr_42_review_1",
            "authority_kind": "approval",
            "granted_by": "alice@example.com",
            "source": "human_approval",
            "extra": {},
        },
    ]
    return {
        "evidence_id": "ev_w350_oscal",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "commit_sha": "abc123def",
        "verdict": "REVIEW",
        "authority_refs": authority_refs,
        # Rest of the packet stays minimal — the AR builder tolerates
        # missing optional fields and we want the test to focus on the
        # authority projection.
    }


def _ns_props(props) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in props or []:
        if isinstance(p, dict) and p.get("ns") == ROAM_OSCAL_NS:
            out.setdefault(p["name"], []).append(p["value"])
    return out


def _walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)


# ---------------------------------------------------------------------------
# Builder-level tests — AR observations[]
# ---------------------------------------------------------------------------


def test_ar_authority_observations_one_per_kind():
    """``observations[]`` carries one entry per distinct ``authority_kind``."""
    doc = build_oscal_assessment_results(
        _packet_with_mixed_authority_refs(),
        now=FIXED_CLOCK,
    )
    observations = doc["assessment-results"]["results"][0].get("observations") or []

    # Filter to authority-axis observations: those whose props carry
    # ``authority_kind`` under the roam namespace.
    authority_obs = [
        o for o in observations if "authority_kind" in _ns_props(o.get("props") or [])
    ]
    # Packet has 5 distinct kinds populated (mode/permit/lease/policy_rule/approval).
    assert len(authority_obs) == 5, [
        _ns_props(o.get("props") or []).get("authority_kind") for o in authority_obs
    ]

    kinds_observed = sorted(
        _ns_props(o.get("props") or [])["authority_kind"][0] for o in authority_obs
    )
    assert kinds_observed == [
        "approval",
        "lease",
        "mode",
        "permit",
        "policy_rule",
    ], kinds_observed

    # The permit observation must carry 2 subjects (perm_001 + perm_002)
    # and report total_count=2.
    permit_obs = next(
        o
        for o in authority_obs
        if _ns_props(o.get("props") or [])["authority_kind"][0] == "permit"
    )
    assert len(permit_obs["subjects"]) == 2
    permit_props = _ns_props(permit_obs.get("props") or [])
    assert permit_props["authority_refs_total"] == ["2"]
    # Description discloses the count + commit anchor.
    assert "permit" in permit_obs["description"]
    assert "abc123def" in permit_obs["description"]


def test_ar_authority_observation_subject_props_are_canonical():
    """Each subject carries authority_kind + authority_id + granted_by + source."""
    doc = build_oscal_assessment_results(
        _packet_with_mixed_authority_refs(),
        now=FIXED_CLOCK,
    )
    obs = doc["assessment-results"]["results"][0]["observations"]
    permit_obs = next(
        o for o in obs if _ns_props(o.get("props") or []).get("authority_kind") == ["permit"]
    )
    first = permit_obs["subjects"][0]
    sp = _ns_props(first.get("props") or [])
    assert sp.get("authority_kind") == ["permit"]
    assert sp.get("authority_id") in (["perm_001"], ["perm_002"])
    assert sp.get("granted_by")  # alice / bob set above
    assert sp.get("source") == ["permit"]


def test_ar_authority_observation_truncates_at_cap():
    """Per-kind cap of 10 subjects with ``truncated`` prop + ``total_count``."""
    packet = _packet_with_mixed_authority_refs()
    # Inject 15 permits so the cap fires.
    packet["authority_refs"] = [
        r for r in packet["authority_refs"] if r["authority_kind"] != "permit"
    ]
    for i in range(15):
        packet["authority_refs"].append(
            {
                "authority_id": f"perm_bulk_{i:02d}",
                "authority_kind": "permit",
                "granted_by": "ci",
                "source": "permit",
                "extra": {},
            }
        )
    doc = build_oscal_assessment_results(packet, now=FIXED_CLOCK)
    obs = doc["assessment-results"]["results"][0]["observations"]
    permit_obs = next(
        o for o in obs if _ns_props(o.get("props") or []).get("authority_kind") == ["permit"]
    )
    # Cap is 10 subjects.
    assert len(permit_obs["subjects"]) == 10
    op = _ns_props(permit_obs.get("props") or [])
    # total_count surfaces the uncapped value.
    assert op["authority_refs_total"] == ["15"]
    # Truncation explicit.
    assert op.get("truncated") == ["true"]
    # Remarks mention the truncation event for human reviewers.
    assert "truncated" in permit_obs["remarks"].lower()


def test_ar_authority_observation_wording_is_lint_compliant():
    """Every new string passes the W184 compliance-overclaim lint."""
    doc = build_oscal_assessment_results(
        _packet_with_mixed_authority_refs(),
        now=FIXED_CLOCK,
    )
    observations = doc["assessment-results"]["results"][0].get("observations") or []
    authority_obs = [
        o for o in observations if "authority_kind" in _ns_props(o.get("props") or [])
    ]
    assert authority_obs, "no authority observations were emitted"
    for o in authority_obs:
        for s in _walk_strings(o):
            violations = scan_for_overclaims(s)
            assert not violations, (
                f"compliance overclaim in authority observation string: {violations!r}\nstring={s!r}"
            )


def test_ar_empty_authority_refs_emits_no_authority_observations():
    """Empty ``authority_refs`` → no authority observations (NOT a synthetic success).

    Pattern-2 always-emit applies at the *envelope* counter layer (the
    CLI test below checks that). At the *document* layer the absence of
    the observation is itself the truthful signal: a consumer
    inspecting the AR sees no authority observations and knows no
    authority was bound. The OSCAL document does NOT fabricate empty
    placeholder observations.
    """
    packet = _packet_with_mixed_authority_refs()
    packet["authority_refs"] = []
    doc = build_oscal_assessment_results(packet, now=FIXED_CLOCK)
    observations = doc["assessment-results"]["results"][0].get("observations") or []
    authority_obs = [
        o for o in observations if "authority_kind" in _ns_props(o.get("props") or [])
    ]
    assert authority_obs == []


# ---------------------------------------------------------------------------
# CLI-envelope-level tests — summary counters
# ---------------------------------------------------------------------------


def _invoke_json(packet_path: Path) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "evidence-oscal",
            "--kind",
            "assessment-results",
            "--evidence",
            str(packet_path),
        ],
        catch_exceptions=False,
    )
    return result.exit_code, result.output


def test_ar_envelope_authority_counters_populated(tmp_path: Path) -> None:
    """JSON envelope summary carries the W350 authority counters."""
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(_packet_with_mixed_authority_refs()), encoding="utf-8")

    code, out = _invoke_json(packet_path)
    assert code == 0, out
    env = json.loads(out)
    summary = env["summary"]
    assert summary["authority_refs_count"] == 6, summary

    auth_kinds = summary["authority_kinds"]
    assert set(auth_kinds.keys()) == set(AUTHORITY_KINDS), auth_kinds
    assert auth_kinds["mode"] == 1
    assert auth_kinds["permit"] == 2
    assert auth_kinds["lease"] == 1
    assert auth_kinds["policy_rule"] == 1
    assert auth_kinds["approval"] == 1
    # token_scope is not present in the packet — zero-padded per
    # Pattern-2 always-emit, NOT missing.
    assert auth_kinds["token_scope"] == 0

    # Facts list surfaces the authority count with the LAW-4
    # ``records`` terminal.
    facts = env["agent_contract"]["facts"]
    assert any("authority records" in f for f in facts), facts


def test_ar_envelope_empty_authority_refs_emits_zero_counter(tmp_path: Path) -> None:
    """Pattern-2 always-emit: empty packet → zero counter + zeroed kinds dict."""
    packet = _packet_with_mixed_authority_refs()
    packet["authority_refs"] = []
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    code, out = _invoke_json(packet_path)
    assert code == 0, out
    env = json.loads(out)
    summary = env["summary"]
    # authority_refs_count: 0 — NOT "complete" or missing.
    assert summary["authority_refs_count"] == 0
    # All 6 keys still present per Pattern-2 always-emit.
    assert set(summary["authority_kinds"].keys()) == set(AUTHORITY_KINDS)
    assert sum(summary["authority_kinds"].values()) == 0
    # Facts still surfaces the zero (consumers don't branch on key
    # presence).
    facts = env["agent_contract"]["facts"]
    assert any("0 authority records" in f for f in facts), facts
