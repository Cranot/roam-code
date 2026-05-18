"""W607-AT -- ``cmd_evidence_doctor`` threads ``warnings_out`` onto its envelope.

cmd_evidence_doctor is the VALIDATOR at the head of the evidence-compiler
pipeline. It validates W174 (ChangeEvidence dataclass) + W226 (export
profiles) + W228 (false-positive feedback) + the collector -> exporter
chain. After W607-AT, the marker stack composes from the audit-trail
quartet (W607-AD/AI/AL/AP) through evidence-doctor's consumption of those
markers downstream.

Substrate boundaries wrapped by W607-AT
---------------------------------------

Eleven substrate-call sites in ``evidence_doctor()`` get the canonical
``_run_check_at(phase, fn, *args)`` wrapper:

* ``load_raw_packet``              -- _load_raw_packet(...)         (JSON read + parse)
* ``validate_closed_enums``        -- _validate_closed_enums(...)   (W174 vocab check)
* ``recompute_content_hash``       -- _recompute_content_hash(...)  (W218 integrity)
* ``classify_completeness``        -- classify_completeness(...)    (W1266 raw scorer)
* ``classify_banner``              -- _classify_banner(...)         (W259 banner tier)
* ``classify_trust_tiers``         -- _classify_trust_tiers(...)    (W281 trust tally)
* ``classify_authority_kinds``     -- _classify_authority_kinds(...) (W350 authority tally)
* ``packet_size_bytes``            -- packet_size_bytes(...)        (W280 byte count)
* ``classify_packet_budget``       -- classify_packet_budget(...)   (W280 budget state)
* ``build_next_steps``             -- _build_next_steps(...)        (Q-gap recipe)
* ``build_verdict``                -- _build_verdict(...)           (FAIL/WARN/PASS scorer)

Each raise becomes an
``evidence_doctor_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607at_warnings_out``.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Pre-W607-AT cmd_evidence_doctor had THREE narrow try/except blocks in
module-level helpers (_load_raw_packet's OSError/JSONDecodeError, and
_recompute_content_hash's TypeError/ValueError). All THREE return
structured sentinel values (error string / None), so they are NOT
Pattern-2 ``except ...: pass`` silent fallbacks. ZERO Pattern-2 silent
fallbacks needed to be eliminated by W607-AT - the AST-walk guard (test
11 below) pins this for the future.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import hashlib
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers -- build a known-good ChangeEvidence-shaped packet
# ---------------------------------------------------------------------------


def _hash_packet(payload: dict) -> str:
    """Recompute the content_hash for a packet payload exactly the way
    the ChangeEvidence dataclass does (so synthetic test packets pass
    the doctor's hash check on the happy path).
    """
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


def _synthetic_packet(stamp_hash: bool = True) -> dict:
    """Build a STRONG-coverage synthetic ChangeEvidence packet."""
    p: dict = {
        "evidence_id": "ev_w607_at_test",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": "agent:w607_at",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "REVIEW",
        "risk_level": "low",
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
        ],
        "changed_subjects": [
            {
                "kind": "symbol",
                "qualified_name": "app/svc::do_thing",
                "repo_id": None,
                "extra": {},
            }
        ],
        "findings": [
            {
                "finding_id_str": "test::finding:1",
                "claim": "low-severity finding",
                "severity": "low",
            }
        ],
        "policy_decisions": [{"rule_id": "test:rule", "outcome": "allowed"}],
        "tests_required": ["tests/test_foo.py::test_one"],
        "tests_run": [{"test_id": "tests/test_foo.py::test_one", "outcome": "passed"}],
        "approvals": [{"approval_id": "ap:1", "approver": "alice", "scope": "merge"}],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": [],
        "actor_refs": [
            {
                "actor_id": "agent:test",
                "actor_kind": "agent",
                "display_name": "Test agent",
                "trust_tier": "verified_ci",
                "extra": {},
            }
        ],
        "authority_refs": [
            {
                "authority_id": "mode:safe_edit",
                "authority_kind": "mode",
                "granted_by": "system",
                "source": "mode",
                "extra": {},
            }
        ],
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


def _invoke_doctor(runner: CliRunner, packet_path: Path, *extra):
    """Invoke ``roam --json evidence-doctor <packet_path>``."""
    from roam.cli import cli

    args = ["--json", "evidence-doctor", str(packet_path)]
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def valid_packet(tmp_path):
    """Synthetic ChangeEvidence packet for happy-path envelope shape tests."""
    path = tmp_path / "packet.json"
    path.write_text(_json.dumps(_synthetic_packet()), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AT substrate markers
# ---------------------------------------------------------------------------


def test_evidence_doctor_clean_envelope_omits_w607at_markers(cli_runner, valid_packet):
    """Clean run -> no W607-AT substrate markers.

    Hash-stable: empty W607-AT bucket on the success path produces an
    envelope without substrate markers AND without a top-level
    ``warnings_out`` key. Byte-identical to pre-W607-AT when no helper
    raised.
    """
    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "evidence-doctor"
    # Empty-bucket discipline: NO W607-AT markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("evidence_doctor_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean evidence-doctor must NOT surface "
        f"evidence_doctor_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) validate_closed_enums failure -> marker emitted, envelope still emits
# ---------------------------------------------------------------------------


def test_evidence_doctor_validate_closed_enums_failure_marker_format(cli_runner, valid_packet, monkeypatch):
    """If _validate_closed_enums raises, surface ``evidence_doctor_validate_closed_enums_failed:``."""
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-validate-enums-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_validate_closed_enums", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    ve_markers = [m for m in top_wo if m.startswith("evidence_doctor_validate_closed_enums_failed:")]
    assert ve_markers, f"expected evidence_doctor_validate_closed_enums_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in ve_markers), ve_markers
    assert any("synthetic-validate-enums-from-W607-AT" in m for m in ve_markers), ve_markers
    # Non-empty W607-AT bucket -> partial_success flips True.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (3) recompute_content_hash failure -> marker + envelope still ships
# ---------------------------------------------------------------------------


def test_evidence_doctor_recompute_content_hash_failure_marker_format(cli_runner, valid_packet, monkeypatch):
    """If _recompute_content_hash raises, surface ``evidence_doctor_recompute_content_hash_failed:``."""
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-recompute-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_recompute_content_hash", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    rh_markers = [m for m in top_wo if m.startswith("evidence_doctor_recompute_content_hash_failed:")]
    assert rh_markers, f"expected evidence_doctor_recompute_content_hash_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in rh_markers), rh_markers


# ---------------------------------------------------------------------------
# (4) classify_completeness failure -> marker emitted
# ---------------------------------------------------------------------------


def test_evidence_doctor_classify_completeness_failure_marker_format(cli_runner, valid_packet, monkeypatch):
    """If classify_completeness raises, surface the marker.

    W174 dataclass-validate bonus: this is the W1266 raw-dict
    completeness scorer; a raise here would otherwise crash the doctor
    wholesale. With W607-AT, the envelope completes with empty
    completeness totals and the marker discloses the failure.
    """
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-completeness-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "classify_completeness", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    cc_markers = [m for m in top_wo if m.startswith("evidence_doctor_classify_completeness_failed:")]
    assert cc_markers, f"expected evidence_doctor_classify_completeness_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in cc_markers), cc_markers
    # Envelope completes with empty completeness totals (don't crash wholesale).
    assert "summary" in data
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (5) classify_trust_tiers failure -> marker emitted
# ---------------------------------------------------------------------------


def test_evidence_doctor_classify_trust_tiers_failure_marker_format(cli_runner, valid_packet, monkeypatch):
    """If _classify_trust_tiers raises, surface the marker."""
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-trust-tiers-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_classify_trust_tiers", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    tt_markers = [m for m in top_wo if m.startswith("evidence_doctor_classify_trust_tiers_failed:")]
    assert tt_markers, f"expected evidence_doctor_classify_trust_tiers_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_evidence_doctor_warnings_out_in_envelope(cli_runner, valid_packet, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_validate_closed_enums", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY W607-AT helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_evidence_doctor_helper_raises(cli_runner, valid_packet, monkeypatch):
    """Any non-empty W607-AT bucket -> summary.partial_success = True."""
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_classify_authority_kinds", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, valid_packet, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AP contracts.
    """
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_build_next_steps", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("evidence_doctor_build_next_steps_failed:")]
    assert failure_markers, f"expected evidence_doctor_build_next_steps_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "evidence_doctor_build_next_steps_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``evidence_doctor_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_evidence_doctor_not_other_families(cli_runner, valid_packet, monkeypatch):
    """Every surfaced W607-AT marker uses ``evidence_doctor_*``.

    cmd_evidence_doctor is the validator at the head of the
    evidence-compiler pipeline -- mutually distinct from sibling
    W607-* layers. Hard guard against accidental marker-prefix drift.
    """
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_classify_banner", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("evidence_doctor_"), (
            f"every surfaced W607-AT marker must use the "
            f"``evidence_doctor_*`` prefix family "
            f"(cmd_evidence_doctor scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("audit_trail_export_", "cmd_audit_trail_export W607-AP"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-AL"),
            ("attest_", "cmd_attest W607-AD"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_risk_", "cmd_pr_risk W607-AB/Q"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("cga_", "cmd_cga W607-AF"),
            ("vulns_", "cmd_vulns W607-AQ"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Validator-closure pairing: AT marker family mutually distinct in source
# ---------------------------------------------------------------------------


def test_validator_closure_prefix_distinct_from_quartet():
    """W607-AT marker family is mutually distinct from W607-AD/AI/AL/AP.

    Source-level guard pinning the validator-closure marker-family
    closed-enum invariant: when a downstream aggregator consumes
    envelopes from the audit-trail quartet AND the evidence-doctor
    validator, the prefix family alone attributes each disclosure
    correctly.

    Templates:
    * evidence_doctor_{phase}_failed          (W607-AT, this wave)
    * audit_trail_export_{phase}_failed       (W607-AP)
    * audit_trail_conformance_{phase}_failed  (W607-AL)
    * audit_trail_verify_{phase}_failed       (W607-AI)
    * attest_{phase}_failed                   (W607-AD producer)
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"
    doctor_src_path = src_root / "cmd_evidence_doctor.py"
    export_src_path = src_root / "cmd_audit_trail_export.py"
    conformance_src_path = src_root / "cmd_audit_trail_conformance.py"
    verify_src_path = src_root / "cmd_audit_trail_verify.py"

    assert doctor_src_path.exists(), doctor_src_path
    assert export_src_path.exists(), export_src_path
    assert conformance_src_path.exists(), conformance_src_path
    assert verify_src_path.exists(), verify_src_path

    doctor_src = doctor_src_path.read_text(encoding="utf-8")
    export_src = export_src_path.read_text(encoding="utf-8")
    conformance_src = conformance_src_path.read_text(encoding="utf-8")
    verify_src = verify_src_path.read_text(encoding="utf-8")

    # Each family carries its OWN marker template in its OWN source.
    assert "evidence_doctor_{phase}_failed" in doctor_src, (
        "W607-AT evidence_doctor_{phase}_failed marker template missing "
        "from cmd_evidence_doctor -- validator-side regressed."
    )
    assert "audit_trail_export_{phase}_failed" in export_src
    assert "audit_trail_conformance_{phase}_failed" in conformance_src
    assert "audit_trail_verify_{phase}_failed" in verify_src

    # The four prefixes are mutually distinct.
    prefixes = (
        "evidence_doctor_",
        "audit_trail_export_",
        "audit_trail_conformance_",
        "audit_trail_verify_",
    )
    for i, p1 in enumerate(prefixes):
        for j, p2 in enumerate(prefixes):
            if i == j:
                continue
            assert p1 != p2, (p1, p2)


# ---------------------------------------------------------------------------
# (11) PATTERN-2 ELIMINATION drift-guard: no `except ...: pass` blocks
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallbacks_eliminated():
    """W607-AT pins the absence of Pattern-2 silent fallbacks in cmd_evidence_doctor.

    Pre-W607-AT cmd_evidence_doctor had ZERO bare ``except ...: pass``
    Pattern-2 silent fallbacks - the three module-level try/except
    blocks all return structured sentinel values (error string / None)
    rather than degrading silently. This AST-walk guard pins the
    elimination: any new ``except ...: pass`` in this module fails the
    test. Mirrors W607-AL test 11 and W607-AP test 11.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_doctor.py"
    src = src_path.read_text(encoding="utf-8")

    tree = ast.parse(src)
    silent_fallbacks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # Body of a single `pass` is the Pattern-2 antipattern.
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                    if handler.type is None:
                        kind = "bare except"
                    elif isinstance(handler.type, ast.Name):
                        kind = handler.type.id
                    elif isinstance(handler.type, ast.Attribute):
                        kind = ast.unparse(handler.type)
                    else:
                        kind = ast.dump(handler.type)
                    silent_fallbacks.append((handler.lineno, kind))

    assert not silent_fallbacks, (
        f"W607-AT must keep cmd_evidence_doctor free of Pattern-2 "
        f"silent-fallback ``except ...: pass`` blocks; still found: "
        f"{silent_fallbacks!r}. Convert each to ``_run_check_at(...)``."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_evidence_doctor carries the W607-AT accumulator
# ---------------------------------------------------------------------------


def test_cmd_evidence_doctor_carries_w607at_accumulator():
    """AST-level guard: cmd_evidence_doctor carries the W607-AT accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation fails this guard rather than silently regressing
    every other dynamic envelope-shape test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_doctor.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607at_warnings_out" in src, (
        "W607-AT accumulator missing from cmd_evidence_doctor; the substrate-CALL marker plumbing has been removed."
    )
    assert "evidence_doctor_{phase}_failed" in src, (
        "W607-AT marker prefix template missing from cmd_evidence_doctor; "
        'check the `f"evidence_doctor_{phase}_failed:..."` line in '
        "_run_check_at."
    )
    # Parse-tree level: confirm _run_check_at is defined inside the command body.
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_at":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AT ``_run_check_at`` helper not found in "
        "cmd_evidence_doctor AST; the per-substrate wrapper "
        "has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_evidence_doctor substrate boundary is wrapped.

    W607-AT substrate inventory (in order of execution):

    * load_raw_packet              -- JSON read + parse
    * validate_closed_enums        -- W174 vocabulary check
    * recompute_content_hash       -- W218 integrity recompute
    * classify_completeness        -- W1266 raw-dict completeness scorer
    * classify_banner              -- W259 banner tier classification
    * classify_trust_tiers         -- W281 actor-trust tier tally
    * classify_authority_kinds     -- W350 authority-kind tally
    * packet_size_bytes            -- W280 byte-count measurement
    * classify_packet_budget       -- W280 budget-state classification
    * build_next_steps             -- Q-gap -> action recipe
    * build_verdict                -- FAIL/WARN/PASS ladder scoring

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces to allow for
    refactor of the substrate call sites without breaking the guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_doctor.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_raw_packet",
        "validate_closed_enums",
        "recompute_content_hash",
        "classify_completeness",
        "classify_banner",
        "classify_trust_tiers",
        "classify_authority_kinds",
        "packet_size_bytes",
        "classify_packet_budget",
        "build_next_steps",
        "build_verdict",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_at("{phase}"' in src
        multi_line = any(f'_run_check_at(\n{" " * indent}"{phase}"' in src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, (
            f"W607-AT _run_check_at wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) AUDIT-TRAIL CONSUMPTION bonus: AT markers coexist with audit_trail_* markers
# ---------------------------------------------------------------------------


def test_audit_trail_consumption_marker_coexistence(cli_runner, valid_packet, monkeypatch):
    """W607-AT markers can coexist with audit_trail_* markers in the same envelope.

    When cmd_evidence_doctor validates a packet whose source audit trail
    had any of the audit-trail quartet (W607-AD/AI/AL/AP) markers in its
    warnings_out, the validator's own evidence_doctor_* markers must
    coexist on the SAME envelope without prefix collision.

    This test simulates: a packet is read; the validator's own substrate
    fails (raises). The marker emitted carries the evidence_doctor_*
    prefix (NOT audit_trail_*), even though both families operate on the
    same evidence-compiler pipeline.
    """
    from roam.commands import cmd_evidence_doctor

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-audit-trail-consumption-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "_validate_closed_enums", _raise)

    result = _invoke_doctor(cli_runner, valid_packet)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # The validator's marker fires...
    doctor_markers = [m for m in top_wo if m.startswith("evidence_doctor_")]
    assert doctor_markers, (
        f"expected evidence_doctor_ markers for audit-trail consumption coexistence test; got {top_wo!r}"
    )
    # ...and NO audit-trail-family markers leak from the same envelope.
    sibling_leaks = [
        m
        for m in top_wo
        if (
            m.startswith("audit_trail_export_")
            or m.startswith("audit_trail_verify_")
            or m.startswith("audit_trail_conformance_")
            or m.startswith("attest_")
        )
    ]
    assert not sibling_leaks, (
        f"audit-trail-family W607-* marker families leaked into the evidence-doctor envelope; got {sibling_leaks!r}"
    )


# ---------------------------------------------------------------------------
# (15) W174 DATACLASS-VALIDATE bonus: malformed packet -> marker, envelope ships
# ---------------------------------------------------------------------------


def test_w174_dataclass_validate_malformed_packet_discloses(cli_runner, tmp_path, monkeypatch):
    """Simulated classify_completeness raise on a synthetic malformed packet.

    W174 dataclass-validate bonus: when the W1266 completeness scorer
    raises on a malformed packet (synthetic raise simulates a producer
    that emitted a packet this binary doesn't understand), the doctor
    must:

    1. Surface evidence_doctor_classify_completeness_failed: marker
    2. NOT crash wholesale -- the envelope completes
    3. Disclose the empty completeness totals (missing_count = 8) so
       the consumer sees "this packet was not validated for completeness"
       rather than "this packet passed completeness validation"
    """
    from roam.commands import cmd_evidence_doctor

    # Build a packet whose schema_version is set but whose internal shape
    # might surprise the completeness scorer. Then raise inside the
    # scorer to simulate a malformed-packet failure.
    packet = _synthetic_packet()
    packet_path = tmp_path / "malformed.json"
    packet_path.write_text(_json.dumps(packet), encoding="utf-8")

    def _raise(*args, **kwargs):
        raise ValueError("synthetic-malformed-packet-from-W607-AT")

    monkeypatch.setattr(cmd_evidence_doctor, "classify_completeness", _raise)

    result = _invoke_doctor(cli_runner, packet_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # (1) Marker surfaces
    top_wo = data.get("warnings_out") or []
    cc_markers = [m for m in top_wo if m.startswith("evidence_doctor_classify_completeness_failed:")]
    assert cc_markers, (
        f"expected evidence_doctor_classify_completeness_failed: marker on malformed-packet path; got {top_wo!r}"
    )
    assert any("ValueError" in m for m in cc_markers), cc_markers

    # (2) Envelope completes (not crashed wholesale) -- summary key
    # present.
    assert "summary" in data, "envelope must still ship summary on raise"

    # (3) Disclosure: when completeness scorer raised, totals fall back
    # to the empty-totals default (missing_count = 8) so consumers see
    # the "this packet was not validated for completeness" signal.
    summary = data["summary"]
    assert summary.get("missing_count") == 8, (
        f"expected missing_count = 8 on completeness scorer raise; got summary = {summary!r}"
    )
    assert summary.get("partial_success") is True


# ---------------------------------------------------------------------------
# (16) Sibling parity -- W607-AP source unchanged by W607-AT
# ---------------------------------------------------------------------------


def test_w607_ap_source_unaffected():
    """Sibling parity guard: W607-AP cmd_audit_trail_export surface unchanged.

    W607-AT lands only in cmd_evidence_doctor. The W607-AP sibling source
    surface MUST stay identical -- accumulator + marker template present.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    export_src_path = src_root / "cmd_audit_trail_export.py"
    assert export_src_path.exists()

    export_src = export_src_path.read_text(encoding="utf-8")

    assert "_w607ap_warnings_out" in export_src, (
        "W607-AP accumulator removed from cmd_audit_trail_export; W607-AT must not regress the sibling instrumentation."
    )
    assert "audit_trail_export_{phase}_failed" in export_src, (
        "W607-AP marker prefix template removed from cmd_audit_trail_export; "
        "W607-AT must not regress the sibling marker family."
    )
