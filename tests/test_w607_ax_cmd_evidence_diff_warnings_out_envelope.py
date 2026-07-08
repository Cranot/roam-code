"""W607-AX -- ``cmd_evidence_diff`` threads ``warnings_out`` onto its envelope.

cmd_evidence_diff is the SIBLING validator to cmd_evidence_doctor (W607-AT).
Both validators consume the W1266 raw-dict completeness scorer per the
docstring at ``evidence/completeness_compat.py``: "Both
``cmd_evidence_doctor`` and ``cmd_evidence_diff`` recompute the W210
``evidence_completeness()`` projection locally." Plumbing both closes the
raw-dict-completeness boundary for the validator family.

Substrate boundaries wrapped by W607-AX
---------------------------------------

Twelve substrate-call sites in ``evidence_diff()`` get the canonical
``_run_check_ax(phase, fn, *args)`` wrapper:

* ``load_packet_old``        -- _load_packet(old_path)
* ``load_packet_new``        -- _load_packet(new_path)
* ``diff_refs_actor``        -- _diff_refs(... actor_refs ...)
* ``diff_refs_authority``    -- _diff_refs(... authority_refs ...)
* ``diff_refs_environment``  -- _diff_refs(... environment_refs ...)
* ``diff_scalar_verdicts``   -- _diff_scalar_fields(... verdict/risk_level ...)
* ``diff_findings``          -- _diff_findings(old, new)
* ``diff_artifacts``         -- _diff_artifacts(old, new)
* ``diff_completeness``      -- _diff_completeness(old, new)  (W1266 boundary)
* ``diff_scalar_timing``     -- _diff_scalar_fields(... timing ...)
* ``extract_stale_old``      -- _extract_stale(old)
* ``extract_stale_new``      -- _extract_stale(new)
* ``build_verdict``          -- _build_verdict(...)

Each raise becomes an
``evidence_diff_<phase>_failed:<exc_class>:<detail>`` marker via
``_w607ax_warnings_out``.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Pre-W607-AX cmd_evidence_diff has ZERO bare ``except ...: pass``
Pattern-2 silent fallbacks. The defensive ``isinstance(... , list)``
checks in _diff_refs / _diff_findings / _diff_artifacts return
structured empties (not silent passes), so there is no Pattern-2
antipattern to eliminate. The AST-walk guard (test 11 below) pins this
for the future.

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
    """Recompute the content_hash for a packet payload the same way the
    ChangeEvidence dataclass does (so synthetic test packets pass any
    downstream hash check on the happy path).
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


def _synthetic_packet(seed: str = "ax", stamp_hash: bool = True) -> dict:
    """Build a synthetic ChangeEvidence-shaped packet for diff tests.

    Two packets built with the same seed should hash-equal, so a clean
    diff (seed -> seed) produces "no drift". Different seeds produce a
    hash drift but no completeness regression.
    """
    p: dict = {
        "evidence_id": f"ev_w607_ax_{seed}",
        "schema_version": "1.0.0",
        "repo_id": "test/repo",
        "git_range": "abc..def",
        "commit_sha": "d" * 40,
        "diff_hash": "h" * 64,
        "run_ids": ["run_1"],
        "agent_id": f"agent:w607_ax_{seed}",
        "human_actor": None,
        "mode": "safe_edit",
        "started_at": "2026-05-14T10:00:00Z",
        "completed_at": "2026-05-14T10:05:00Z",
        "verdict": "REVIEW",
        "risk_level": "low",
        "context_refs": [],
        "changed_subjects": [
            {
                "kind": "symbol",
                "qualified_name": "app/svc::do_thing",
                "repo_id": None,
                "extra": {},
            }
        ],
        "findings": [],
        "policy_decisions": [{"rule_id": "test:rule", "outcome": "allowed"}],
        "tests_required": [],
        "tests_run": [],
        "approvals": [],
        "accepted_risks": [],
        "artifacts": [],
        "redactions": [],
        "actor_refs": [
            {
                "actor_id": f"agent:{seed}",
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


def _invoke_diff(runner: CliRunner, old_path: Path, new_path: Path, *extra):
    """Invoke ``roam --json evidence-diff <old> <new>``."""
    from roam.cli import cli

    args = ["--json", "evidence-diff", str(old_path), str(new_path)]
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def packet_pair(tmp_path):
    """Two byte-identical synthetic ChangeEvidence packets.

    Diffing seed=='ax' against itself produces a clean "(no drift)"
    envelope -- the happy-path baseline for W607-AX shape tests.
    """
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    payload = _synthetic_packet(seed="ax")
    raw = _json.dumps(payload)
    old_p.write_text(raw, encoding="utf-8")
    new_p.write_text(raw, encoding="utf-8")
    return old_p, new_p


# ---------------------------------------------------------------------------
# (1) Happy path -- clean envelope omits W607-AX substrate markers
# ---------------------------------------------------------------------------


def test_evidence_diff_clean_envelope_omits_w607ax_markers(cli_runner, packet_pair):
    """Clean run -> no W607-AX substrate markers.

    Hash-stable: empty W607-AX bucket on the success path produces an
    envelope without substrate markers AND without a top-level
    ``warnings_out`` key. Byte-identical to pre-W607-AX when no helper
    raised.
    """
    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "evidence-diff"
    # Empty-bucket discipline: NO W607-AX markers on the clean envelope.
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    substrate_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if m.startswith("evidence_diff_") and "_failed:" in m
    ]
    assert not substrate_markers, (
        f"clean evidence-diff must NOT surface "
        f"evidence_diff_<phase>_failed: markers; "
        f"got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) diff_completeness failure -> marker emitted, envelope still emits
# ---------------------------------------------------------------------------


def test_evidence_diff_completeness_failure_marker_format(cli_runner, packet_pair, monkeypatch):
    """If _diff_completeness raises, surface ``evidence_diff_diff_completeness_failed:``.

    W1266 BOUNDARY bonus: the diff_completeness phase is the shared
    boundary with cmd_evidence_doctor (W607-AT). A raise here would
    otherwise crash the diff wholesale. With W607-AX, the envelope
    completes with empty completeness triples and the marker discloses
    the failure.
    """
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-completeness-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_completeness", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    dc_markers = [m for m in top_wo if m.startswith("evidence_diff_diff_completeness_failed:")]
    assert dc_markers, f"expected evidence_diff_diff_completeness_failed: marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in dc_markers), dc_markers
    assert any("synthetic-completeness-from-W607-AX" in m for m in dc_markers), dc_markers
    # Non-empty W607-AX bucket -> partial_success flips True.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (3) load_packet failure -> marker emitted, envelope still emits
# ---------------------------------------------------------------------------


def test_evidence_diff_load_packet_failure_marker_format(cli_runner, packet_pair, monkeypatch):
    """If _load_packet raises, surface ``evidence_diff_load_packet_*_failed:``."""
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-load-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_load_packet", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    load_markers = [m for m in top_wo if m.startswith("evidence_diff_load_packet_")]
    assert load_markers, f"expected evidence_diff_load_packet_*_failed: marker; got {top_wo!r}"
    # Both load_packet_old and load_packet_new should fail.
    assert any(m.startswith("evidence_diff_load_packet_old_failed:") for m in load_markers), load_markers
    assert any(m.startswith("evidence_diff_load_packet_new_failed:") for m in load_markers), load_markers


# ---------------------------------------------------------------------------
# (4) diff_refs failure -> marker emitted
# ---------------------------------------------------------------------------


def test_evidence_diff_refs_failure_marker_format(cli_runner, packet_pair, monkeypatch):
    """If _diff_refs raises, surface ``evidence_diff_diff_refs_*_failed:``.

    The _diff_refs helper is called three times (actor / authority /
    environment) -- a raise hits all three. We accept any prefix match.
    """
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-refs-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_refs", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    refs_markers = [m for m in top_wo if m.startswith("evidence_diff_diff_refs_")]
    assert refs_markers, f"expected evidence_diff_diff_refs_*_failed: markers; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (5) build_verdict failure -> marker emitted
# ---------------------------------------------------------------------------


def test_evidence_diff_build_verdict_failure_marker_format(cli_runner, packet_pair, monkeypatch):
    """If _build_verdict raises, surface ``evidence_diff_build_verdict_failed:``."""
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-verdict-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_build_verdict", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    bv_markers = [m for m in top_wo if m.startswith("evidence_diff_build_verdict_failed:")]
    assert bv_markers, f"expected evidence_diff_build_verdict_failed: marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (6) warnings_out lands in BOTH summary AND top-level envelope
# ---------------------------------------------------------------------------


def test_evidence_diff_warnings_out_in_envelope(cli_runner, packet_pair, monkeypatch):
    """Non-empty bucket -> BOTH top-level AND summary.warnings_out populated."""
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_completeness", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) partial_success flips when ANY W607-AX helper raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_evidence_diff_helper_raises(cli_runner, packet_pair, monkeypatch):
    """Any non-empty W607-AX bucket -> summary.partial_success = True."""
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_findings", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, packet_pair, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AT contracts.
    """
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_artifacts", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("evidence_diff_diff_artifacts_failed:")]
    assert failure_markers, f"expected evidence_diff_diff_artifacts_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "evidence_diff_diff_artifacts_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- ``evidence_diff_*`` only
# ---------------------------------------------------------------------------


def test_marker_prefix_evidence_diff_not_other_families(cli_runner, packet_pair, monkeypatch):
    """Every surfaced W607-AX marker uses ``evidence_diff_*``.

    cmd_evidence_diff is the SIBLING validator to cmd_evidence_doctor --
    they share the W1266 completeness boundary but their marker prefix
    families MUST stay mutually distinct so downstream aggregators can
    attribute disclosures correctly.
    """
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_completeness", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    substrate_markers = [m for m in top_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("evidence_diff_"), (
            f"every surfaced W607-AX marker must use the "
            f"``evidence_diff_*`` prefix family "
            f"(cmd_evidence_diff scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("evidence_doctor_", "cmd_evidence_doctor W607-AT (sibling validator)"),
            ("audit_trail_export_", "cmd_audit_trail_export W607-AP"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-AL"),
            ("attest_", "cmd_attest W607-AD"),
            ("pr_bundle_", "cmd_pr_bundle W607-AE"),
            ("pr_analyze_", "cmd_pr_analyze W607-AA"),
            ("pr_risk_", "cmd_pr_risk W607-AB/Q"),
            ("diff_", "cmd_diff W607-Z (top-level diff, not evidence_diff_)"),
            ("critique_", "cmd_critique W607-Y"),
            ("cga_", "cmd_cga W607-AF"),
            ("vulns_", "cmd_vulns W607-AQ"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (10) Validator-family pairing: AT and AX mutually distinct in source
# ---------------------------------------------------------------------------


def test_validator_family_prefix_distinct_from_doctor():
    """W607-AX marker family is mutually distinct from W607-AT.

    Source-level guard pinning the validator-family marker closed-enum
    invariant: cmd_evidence_doctor and cmd_evidence_diff are siblings on
    the SAME shared W1266 completeness boundary. They MUST emit markers
    with mutually distinct prefix families so a downstream aggregator
    consuming envelopes from both validators can attribute each
    disclosure correctly.

    Templates:
    * evidence_doctor_{phase}_failed  (W607-AT)
    * evidence_diff_{phase}_failed    (W607-AX, this wave)
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"
    doctor_src_path = src_root / "cmd_evidence_doctor.py"
    diff_src_path = src_root / "cmd_evidence_diff.py"

    assert doctor_src_path.exists(), doctor_src_path
    assert diff_src_path.exists(), diff_src_path

    doctor_src = doctor_src_path.read_text(encoding="utf-8")
    diff_src = diff_src_path.read_text(encoding="utf-8")

    # Each family carries its OWN marker template in its OWN source.
    assert "evidence_doctor_{phase}_failed" in doctor_src, (
        "W607-AT evidence_doctor_{phase}_failed marker template missing "
        "from cmd_evidence_doctor -- sibling validator regressed."
    )
    assert "evidence_diff_{phase}_failed" in diff_src, (
        "W607-AX evidence_diff_{phase}_failed marker template missing "
        "from cmd_evidence_diff -- this-wave validator regressed."
    )

    # The two prefixes are mutually distinct.
    assert "evidence_diff_" != "evidence_doctor_"
    # And the diff source does NOT define the doctor template.
    assert "evidence_doctor_{phase}_failed" not in diff_src, (
        "cmd_evidence_diff must not carry the W607-AT doctor template; marker family contamination."
    )
    # The doctor source does NOT define the diff template.
    assert "evidence_diff_{phase}_failed" not in doctor_src, (
        "cmd_evidence_doctor must not carry the W607-AX diff template; marker family contamination."
    )


# ---------------------------------------------------------------------------
# (11) PATTERN-2 ELIMINATION drift-guard: no `except ...: pass` blocks
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallbacks_eliminated():
    """W607-AX pins the absence of Pattern-2 silent fallbacks in cmd_evidence_diff.

    Pre-W607-AX cmd_evidence_diff had ZERO bare ``except ...: pass``
    Pattern-2 silent fallbacks -- the defensive ``isinstance(..., list)``
    checks in _diff_refs / _diff_findings / _diff_artifacts return
    structured empties (not silent passes). This AST-walk guard pins
    the elimination: any new ``except ...: pass`` in this module fails
    the test. Mirrors W607-AT test 11.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_diff.py"
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
        f"W607-AX must keep cmd_evidence_diff free of Pattern-2 "
        f"silent-fallback ``except ...: pass`` blocks; still found: "
        f"{silent_fallbacks!r}. Convert each to ``_run_check_ax(...)``."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: cmd_evidence_diff carries the W607-AX accumulator
# ---------------------------------------------------------------------------


def test_cmd_evidence_diff_carries_w607ax_accumulator():
    """AST-level guard: cmd_evidence_diff carries the W607-AX accumulator.

    Pins the canonical anchors so a future refactor that removes the
    instrumentation fails this guard rather than silently regressing
    every other dynamic envelope-shape test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_diff.py"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ax_warnings_out" in src, (
        "W607-AX accumulator missing from cmd_evidence_diff; the substrate-CALL marker plumbing has been removed."
    )
    assert "evidence_diff_{phase}_failed" in src, (
        "W607-AX marker prefix template missing from cmd_evidence_diff; "
        'check the `f"evidence_diff_{phase}_failed:..."` line in '
        "_run_check_ax."
    )
    # Parse-tree level: confirm _run_check_ax is defined inside the command body.
    tree = ast.parse(src)
    found_run_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ax":
            found_run_check = True
            break
    assert found_run_check, (
        "W607-AX ``_run_check_ax`` helper not found in "
        "cmd_evidence_diff AST; the per-substrate wrapper "
        "has been refactored away."
    )


# ---------------------------------------------------------------------------
# (13) Each substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_substrate_phases_wrapped_in_source():
    """Source-level guard: every cmd_evidence_diff substrate boundary is wrapped.

    W607-AX substrate inventory (in order of execution):

    * load_packet_old           -- _load_packet(old_path)
    * load_packet_new           -- _load_packet(new_path)
    * diff_refs_actor           -- _diff_refs(... actor_refs ...)
    * diff_refs_authority       -- _diff_refs(... authority_refs ...)
    * diff_refs_environment     -- _diff_refs(... environment_refs ...)
    * diff_scalar_verdicts      -- _diff_scalar_fields(... verdict/risk_level)
    * diff_findings             -- _diff_findings(old, new)
    * diff_artifacts            -- _diff_artifacts(old, new)
    * diff_completeness         -- _diff_completeness(old, new)  (W1266)
    * diff_scalar_timing        -- _diff_scalar_fields(... timing ...)
    * extract_stale_old         -- _extract_stale(old)
    * extract_stale_new         -- _extract_stale(new)
    * build_verdict             -- _build_verdict(...)

    Accepts indentation depths of 8, 12, 16, 20, 24 spaces to allow for
    refactor of the substrate call sites without breaking the guard.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_evidence_diff.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "load_packet_old",
        "load_packet_new",
        "diff_refs_actor",
        "diff_refs_authority",
        "diff_refs_environment",
        "diff_scalar_verdicts",
        "diff_findings",
        "diff_artifacts",
        "diff_completeness",
        "diff_scalar_timing",
        "extract_stale_old",
        "extract_stale_new",
        "build_verdict",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ax("{phase}"' in src
        multi_line = any(f'_run_check_ax(\n{" " * indent}"{phase}"' in src for indent in (8, 12, 16, 20, 24))
        assert same_line or multi_line, (
            f"W607-AX _run_check_ax wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) DOCTOR/DIFF PAIRING bonus: AT and AX markers coexist in source
# ---------------------------------------------------------------------------


def test_doctor_diff_pairing_markers_coexist(cli_runner, packet_pair, monkeypatch):
    """W607-AX markers carry the evidence_diff_ prefix (not evidence_doctor_).

    When cmd_evidence_diff and cmd_evidence_doctor both run on the same
    packet pair (downstream aggregator scenario), their warnings_out
    buckets MUST stay distinguishable by prefix family. The marker
    emitted by the diff carries evidence_diff_ (NOT evidence_doctor_),
    even though both validators share the W1266 raw-dict completeness
    boundary.
    """
    from roam.commands import cmd_evidence_diff

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-doctor-diff-pairing-from-W607-AX")

    monkeypatch.setattr(cmd_evidence_diff, "_diff_completeness", _raise)

    old_p, new_p = packet_pair
    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    # The validator's marker fires...
    diff_markers = [m for m in top_wo if m.startswith("evidence_diff_")]
    assert diff_markers, f"expected evidence_diff_ markers for doctor/diff pairing coexistence test; got {top_wo!r}"
    # ...and NO evidence_doctor_ markers leak into the diff envelope.
    doctor_leaks = [m for m in top_wo if m.startswith("evidence_doctor_")]
    assert not doctor_leaks, (
        f"evidence_doctor_ marker family leaked into the evidence-diff envelope; got {doctor_leaks!r}"
    )


# ---------------------------------------------------------------------------
# (15) W1266 BOUNDARY bonus: per-packet failure, other packet's signal preserved
# ---------------------------------------------------------------------------


def test_w1266_load_per_packet_marker_partial_batch_resilience(cli_runner, tmp_path, monkeypatch):
    """Simulated per-packet load failure: marker fires + diff still ships.

    W1266 BOUNDARY bonus: when one packet load raises (simulating a
    malformed producer artifact), the OTHER packet's signal must still
    be processed and the envelope must still complete. This is the
    partial-batch-resilience contract: a raise on one boundary does not
    crash the entire diff.

    We raise on load_packet ONLY when called with the first argument
    (old_path) -- the call for new_path returns a real packet so the
    diff continues with empty-vs-real semantics.
    """
    from roam.commands import cmd_evidence_diff

    # Two real packets on disk
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_packet = _synthetic_packet(seed="old")
    new_packet = _synthetic_packet(seed="new")
    old_p.write_text(_json.dumps(old_packet), encoding="utf-8")
    new_p.write_text(_json.dumps(new_packet), encoding="utf-8")

    # Monkey-patch _load_packet to raise on the OLD path, succeed on NEW.
    real_load = cmd_evidence_diff._load_packet
    call_count = {"n": 0}

    def _selective_raise(path):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic-old-only-load-from-W607-AX")
        return real_load(path)

    monkeypatch.setattr(cmd_evidence_diff, "_load_packet", _selective_raise)

    result = _invoke_diff(cli_runner, old_p, new_p)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []

    # (a) The old-only load failure surfaces a marker.
    old_load_markers = [m for m in top_wo if m.startswith("evidence_diff_load_packet_old_failed:")]
    assert old_load_markers, (
        f"expected evidence_diff_load_packet_old_failed: marker on per-packet load failure; got {top_wo!r}"
    )
    # (b) The new-side load did NOT raise -> no new-side marker.
    new_load_markers = [m for m in top_wo if m.startswith("evidence_diff_load_packet_new_failed:")]
    assert not new_load_markers, f"new-side load should not have raised; got {new_load_markers!r}"

    # (c) Envelope still completes -- partial_success flips, but the
    # summary block carries the full set of diff fields.
    assert "summary" in data
    assert data["summary"].get("partial_success") is True
    # The diff between {} (failed-old) and a real new packet should
    # surface refs added on the new side.
    assert data["summary"].get("added_refs_total", 0) >= 1, (
        f"expected new-side refs to land as added in partial-batch path; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (16) Sibling parity -- W607-AT source unchanged by W607-AX
# ---------------------------------------------------------------------------


def test_w607_at_source_unaffected():
    """Sibling parity guard: W607-AT cmd_evidence_doctor surface unchanged.

    W607-AX lands only in cmd_evidence_diff. The W607-AT sibling source
    surface MUST stay identical -- accumulator + marker template present.
    """
    src_root = Path(__file__).parent.parent / "src" / "roam" / "commands"

    doctor_src_path = src_root / "cmd_evidence_doctor.py"
    assert doctor_src_path.exists()

    doctor_src = doctor_src_path.read_text(encoding="utf-8")

    assert "w607at_warnings_out" in doctor_src, (
        "W607-AT accumulator removed from cmd_evidence_doctor; W607-AX must not regress the sibling instrumentation."
    )
    assert "evidence_doctor_{phase}_failed" in doctor_src, (
        "W607-AT marker prefix template removed from cmd_evidence_doctor; "
        "W607-AX must not regress the sibling marker family."
    )
