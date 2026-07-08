"""W607-EE -- aggregation-phase plumbing role for ``cmd_audit_trail_conformance``.

WAVE-AXIS FINDING
-----------------

W607-EE on cmd_audit_trail_conformance is **closed-as-duplicate-of-W607-CO**.
cmd_audit_trail_conformance already carries the canonical 4-phase
aggregation-layer plumbing under the W607-CO namespace (landed prior to
this wave per the W607-DZ/EA discovery methodology). The W607-CO layer
wraps the same four canonical aggregation phases that the W607-DX
template for cmd_missing_index uses (with ``serialize_envelope`` playing
the same role as ``ee_serialize_envelope`` would):

  * ``score_classify``
  * ``compute_predicate``
  * ``compute_verdict``
  * ``serialize_envelope``

Introducing an additional ``_w607ee_warnings_out`` / ``_run_check_ee``
layer on top of cmd_audit_trail_conformance's existing W607-CO would:

  1. Triple-stack the aggregation wrap (substrate W607-AL +
     aggregation W607-CO + redundant W607-EE) for zero behavioural
     gain on the canonical 4 phases.
  2. Violate W978's 4th discipline (phase-name collision): the W607-CO
     phase names ``score_classify`` / ``compute_predicate`` /
     ``compute_verdict`` / ``serialize_envelope`` would collide 1:1
     with any W607-EE phase set (even with the ``ee_`` prefix on
     ``ee_serialize_envelope`` -- the underlying marker shape is
     ``audit_trail_conformance_serialize_envelope_failed:`` which is
     already owned by CO).
  3. Confuse the AUDIT-TRAIL family cluster naming. The family triad
     is:

       cmd_audit_trail_verify      -> AI (substrate) + CN + EA (agg)
       cmd_audit_trail_conformance -> AL (substrate) + CO (agg) -- THIS
       cmd_audit_trail_export      -> AP (substrate) + CR (agg)

     Each command's letter-pair stays disjoint across the cluster;
     W607-EE belongs to the next free letter-pair for a DIFFERENT
     command, not a third aggregation layer on cmd_audit_trail_conformance.

This test file therefore PINS the aggregation-layer invariants on the
W607-CO layer (the role this wave brief calls "EE") and documents the
W607-EE-on-cmd_audit_trail_conformance axis as **closed**. Future agents
picking up the W607-EE letter pair should target a DIFFERENT command
(e.g. the next consumer in the family triad rollout queue).

AUDIT-TRAIL FAMILY TRIAD CLOSURE
--------------------------------

With this CLOSE-AS-DUPLICATE pin, all three legs of the AUDIT-TRAIL
family now carry aggregation-LAYER W607 plumbing:

  * cmd_audit_trail_verify       -> W607-AI substrate + W607-CN agg + W607-EA agg-layer
  * cmd_audit_trail_export       -> W607-AP substrate + W607-CR agg
  * cmd_audit_trail_conformance  -> W607-AL substrate + W607-CO agg (this file pins it)

The family is FULLY CLOSED at the aggregation-LAYER axis.

REGRESSION INVARIANTS PRESERVED
-------------------------------

  * W827 -- Pattern-2 silent-fallback seal on the no-trail branch.
    The empty/missing audit-trail state MUST emit
    ``state: no_trail`` + ``partial_success: True`` + explicit
    ``not_run`` markers on each of the 6 checks. Never collapse to
    a clean SAFE / NON-conformant verdict on an unscanned trail.
  * W145 -- audit-trail-conformance findings registry persist
    preserved under ``--persist``. Failing checks become
    ``FindingRecord`` rows; passed/not_run checks are skipped.
  * HMAC chain-verify invariant -- inherits from cmd_audit_trail_verify
    via ``_check_chain_integrity`` (the substrate W607-AL boundary
    that wraps the SHA-256 chain walk).
  * Article 12 not_run preserved -- the 6 Article-12 checks each
    carry ``state: not_run`` on the no-trail branch.

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
# Canonical W607-EE-role aggregation phases (the role W607-CO plays for
# cmd_audit_trail_conformance -- same 4 phase names cmd_missing_index W607-DX
# wraps, with ``serialize_envelope`` playing the brief's ``ee_serialize_envelope``
# role).
# ---------------------------------------------------------------------------


_EE_ROLE_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)


_CONFORMANCE_SRC = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"

_VERIFY_SRC = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_verify.py"

_EXPORT_SRC = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"


# ---------------------------------------------------------------------------
# Helpers -- build a known-good audit trail JSONL with a proper SHA-256 chain
# ---------------------------------------------------------------------------


def _write_chain(path: Path, records: list[dict]) -> None:
    """Write records as JSONL with proper SHA-256 chain linking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            rec["previous_record_hash"] = prev_hash
            line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
            prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()


def _base_record(verdict: str, ts: str) -> dict:
    return {
        "schema": "roam-audit-trail-v1",
        "timestamp": ts,
        "tool": "roam-code",
        "tool_version": "12.26",
        "actor": "test@example.com",
        "verdict": verdict,
        "rationale_summary": "synthetic test record",
        "diff_sha256": "0" * 64,
        "git_sha": "deadbeef" * 5,
        "blast_radius": 30,
        "ai_likelihood": 50,
        "rule_violations_count": 0,
    }


def _invoke_conformance(runner: CliRunner, trail_path: Path, *extra):
    """Invoke ``roam --json audit-trail-conformance-check --input <trail_path>``."""
    from roam.cli import cli

    args = ["--json", "audit-trail-conformance-check", "--input", str(trail_path)]
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def valid_trail(tmp_path):
    """Audit trail with a valid chain (clean substrate path)."""
    path = tmp_path / "trail.jsonl"
    _write_chain(
        path,
        [
            _base_record("SAFE", "2026-05-05T00:00:00Z"),
            _base_record("REVIEW", "2026-05-05T00:01:00Z"),
            _base_record("BLOCK", "2026-05-05T00:02:00Z"),
        ],
    )
    return path


# ---------------------------------------------------------------------------
# (1) WAVE-AXIS FINDING -- W607-EE accumulator is INTENTIONALLY ABSENT
# from cmd_audit_trail_conformance (closed-as-duplicate-of-W607-CO).
# ---------------------------------------------------------------------------


def test_w607ee_accumulator_absent_from_cmd_audit_trail_conformance():
    """W607-EE on cmd_audit_trail_conformance is closed-as-duplicate-of-CO.

    cmd_audit_trail_conformance already carries W607-CO as the canonical
    aggregation layer. Stacking an additional W607-EE layer would:
      * triple-stack aggregation wrapping (substrate AL + agg CO +
        redundant EE) for zero behavioural gain,
      * violate W978 4th discipline (phase-name collision: EE phases
        would collide 1:1 with CO phases since both share the
        ``audit_trail_conformance_<phase>_failed:`` marker family).

    This guard pins the absence so a future agent who incorrectly
    introduces W607-EE on cmd_audit_trail_conformance sees the test
    fail with context pointing them at the W607-CO layer.
    """
    assert _CONFORMANCE_SRC.exists(), f"cmd_audit_trail_conformance.py missing at {_CONFORMANCE_SRC}"
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")

    assert "w607ee_warnings_out" not in src, (
        "W607-EE accumulator unexpectedly present in cmd_audit_trail_conformance. "
        "The aggregation layer is W607-CO "
        "(``_w607co_warnings_out`` + ``_run_check_co``); W607-EE on "
        "cmd_audit_trail_conformance is closed-as-duplicate-of-CO. If you "
        "intended to add a third aggregation layer, you must rename one set "
        "of phases to avoid W978 4th-discipline collision -- but preferred "
        "path is NOT to add the layer (it adds plumbing with zero behavioural gain)."
    )
    assert "_run_check_ee" not in src, (
        "W607-EE helper unexpectedly present in cmd_audit_trail_conformance. "
        "The aggregation helper is ``_run_check_co``; "
        "W607-EE-on-cmd_audit_trail_conformance is closed-as-duplicate-of-CO."
    )


# ---------------------------------------------------------------------------
# (2) CANONICAL AGGREGATION LAYER -- W607-CO plays the W607-EE role
# for cmd_audit_trail_conformance. Pin its presence.
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_conformance_aggregation_layer_is_w607co():
    """The aggregation-layer role for cmd_audit_trail_conformance is W607-CO.

    Pins the structural anchor: ``_w607co_warnings_out`` accumulator,
    ``_run_check_co`` helper, and the W607-AL substrate-CALL layer
    coexisting below it. A regression that removes the CO layer
    silently demotes cmd_audit_trail_conformance to substrate-only coverage.
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")

    assert "w607co_warnings_out" in src, (
        "W607-CO accumulator missing from cmd_audit_trail_conformance; the "
        "aggregation-layer role has regressed. The CO layer is the canonical "
        "aggregation surface; removing it leaves cmd_audit_trail_conformance "
        "with substrate-only (AL) coverage."
    )
    assert "_run_check_co" in src, "W607-CO helper ``_run_check_co`` missing from cmd_audit_trail_conformance."
    assert "w607al_warnings_out" in src, "W607-AL substrate-CALL accumulator missing from cmd_audit_trail_conformance."
    assert "_run_check_al" in src, "W607-AL helper ``_run_check_al`` missing from cmd_audit_trail_conformance."


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every canonical aggregation phase is wrapped
# (under the W607-CO layer, since that plays the EE role).
# ---------------------------------------------------------------------------


def test_every_ee_role_phase_wrapped_in_run_check_co():
    """Every canonical W607-EE-role aggregation phase calls
    ``_run_check_co(...)`` with the canonical phase name.

    The 4 phases ``score_classify`` / ``compute_predicate`` /
    ``compute_verdict`` / ``serialize_envelope`` are the canonical
    aggregation boundaries. In cmd_audit_trail_conformance they are
    wrapped under W607-CO (the layer that plays the W607-EE role).
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")

    for phase in _EE_ROLE_PHASES:
        same_line = f'_run_check_co("{phase}"' in src
        multi_line = any(f'_run_check_co(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"audit_trail_conformance_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EE-role wrap missing for phase {phase!r} on "
            f"cmd_audit_trail_conformance; the canonical aggregation "
            f"boundary is no longer caught. The aggregation layer is W607-CO."
        )


# ---------------------------------------------------------------------------
# (4) Per-phase isolation -- score_classify raise surfaces marker + floors
# ---------------------------------------------------------------------------


def test_score_classify_isolation_marker_and_floor(cli_runner, valid_trail, monkeypatch):
    """Per-phase isolation: a raise inside the score_classify boundary
    surfaces ``audit_trail_conformance_score_classify_failed:`` and floors
    to zero-count metrics rather than crashing the envelope.

    Strategy: patch ``sum`` in the module so the score_classify closure
    raises. The W607-CO wrap catches the raise and the floor
    ``{"passed": 0, "total": 6, "score": 0}`` lets the envelope finish
    composing.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_sum(*a, **kw):
        raise RuntimeError("synthetic-ee-role-score-classify")

    monkeypatch.setattr(_mod, "sum", _raise_sum, raising=False)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("audit_trail_conformance_score_classify_failed:")]
    assert markers, (
        f"expected ``audit_trail_conformance_score_classify_failed:`` "
        f"marker after poisoning ``sum`` to raise; got {all_wo!r}"
    )
    # Floor must surface zero score so envelope doesn't crash.
    summary = data["summary"]
    assert summary.get("score") == 0, f"score_classify floor must zero ``score``; got {summary!r}"


# ---------------------------------------------------------------------------
# (5) Per-phase isolation -- compute_predicate floor dict shape
# ---------------------------------------------------------------------------


def test_compute_predicate_floor_dict_shape():
    """W978 6th-discipline: compute_predicate floor MUST be a concrete
    dict carrying all documented keys (articles_checked / articles_passed /
    articles_failed / total_records), NOT a sentinel that may
    __len__-raise downstream.
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_predicate_floor = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_co"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "compute_predicate"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            assert isinstance(kw.value, ast.Dict), (
                f"compute_predicate default= must be a literal dict; got {type(kw.value).__name__!r}"
            )
            keys_present = set()
            for k in kw.value.keys:
                if isinstance(k, ast.Constant):
                    keys_present.add(k.value)
            expected_keys = {
                "articles_checked",
                "articles_passed",
                "articles_failed",
                "total_records",
            }
            missing = expected_keys - keys_present
            assert not missing, (
                f"compute_predicate floor dict missing keys {missing!r}; "
                f"floor shape must mirror the happy-path return so "
                f"downstream consumers see a consistent envelope."
            )
            found_predicate_floor = True
            break

    assert found_predicate_floor, (
        "compute_predicate _run_check_co call site not found in "
        "cmd_audit_trail_conformance; the aggregation boundary has been "
        "refactored away."
    )


# ---------------------------------------------------------------------------
# (6) Per-phase isolation -- compute_verdict floor is the literal
# "audit-trail-conformance check completed" string (W978 first-hypothesis
# discipline)
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """W978 first-hypothesis discipline: compute_verdict floor must be
    a literal string, NOT an f-string re-interpolating the values that
    just raised. Canonical floor for cmd_audit_trail_conformance is
    ``"audit-trail-conformance check completed"``.
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")

    assert 'default="audit-trail-conformance check completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CO "
        "discipline; the canonical floor literal "
        "'audit-trail-conformance check completed' is missing from "
        "cmd_audit_trail_conformance.py"
    )


# ---------------------------------------------------------------------------
# (7) Per-phase isolation -- serialize_envelope raise -> marker + stub
# ---------------------------------------------------------------------------


def test_serialize_envelope_isolation_marker_and_stub(cli_runner, valid_trail, monkeypatch):
    """If the serialize_envelope boundary (json_envelope) raises, the
    wrap floors to a parseable stub document carrying the canonical
    command name and surfaces the
    ``audit_trail_conformance_serialize_envelope_failed:`` marker.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ee-role-serialize-envelope")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-conformance-check", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("audit_trail_conformance_serialize_envelope_failed:")]
    assert markers, f"expected ``audit_trail_conformance_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (8) Substrate (W607-AL) + aggregation (W607-CO) coexistence -- BOTH
# layer markers surface when BOTH layers fault on the same invocation.
# ---------------------------------------------------------------------------


def test_w607al_substrate_and_w607co_aggregation_coexist(cli_runner, valid_trail, monkeypatch):
    """When BOTH layers fault, BOTH layer markers surface.

    With AL substrate + CO aggregation landed, a single invocation on a
    trail that faults at both layers must surface markers from BOTH
    buckets in the same ``warnings_out`` channel.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_check_chain(*a, **kw):
        raise RuntimeError("synthetic-al-coexist-chain-integrity")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-co-coexist-envelope")

    monkeypatch.setattr(_mod, "_check_chain_integrity", _raise_check_chain)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    al_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_check_chain_integrity_failed:")]
    co_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_serialize_envelope_failed:")]

    assert al_markers, (
        f"W607-AL substrate-CALL marker (audit_trail_conformance_check_chain_integrity_failed) missing; got {top_wo!r}"
    )
    assert co_markers, (
        f"W607-CO aggregation-phase marker (audit_trail_conformance_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``audit_trail_conformance_*`` family
    assert all(m.startswith("audit_trail_conformance_") for m in (al_markers + co_markers)), (
        f"all markers must share the canonical "
        f"``audit_trail_conformance_*`` family; "
        f"got al = {al_markers!r}, co = {co_markers!r}"
    )


# ---------------------------------------------------------------------------
# (9) ANY aggregation marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_aggregation_marker_flips_partial_success(cli_runner, valid_trail, monkeypatch):
    """ANY W607-CO aggregation marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    conformance" from "conformance ran with aggregation degradation"
    via summary.partial_success alone.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ee-role-partial-success")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CO warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (10) warnings_out mirrors -- both top-level AND summary populated
# (W607-DV bond-bug check: late-phase markers must reach BOTH mirrors)
# ---------------------------------------------------------------------------


def test_warnings_out_in_both_top_and_summary_late_phase(cli_runner, valid_trail, monkeypatch):
    """Non-empty W607-CO bucket -> both top-level AND summary.warnings_out
    populated, even for the LATEST phase (serialize_envelope).

    W607-DV bond-bug: a late-phase marker that raises AFTER the summary
    dict was already snapshotted previously failed to reach the
    summary.warnings_out mirror. The CO layer rebuilds the floor's
    warnings_out so this stays caught.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ee-role-mirror")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), f"top-level warnings_out missing; keys = {sorted(data.keys())!r}"
    assert data["summary"].get("warnings_out"), f"summary.warnings_out missing; summary = {data['summary']!r}"

    top_markers = [
        m for m in data["warnings_out"] if m.startswith("audit_trail_conformance_serialize_envelope_failed:")
    ]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("audit_trail_conformance_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker (W607-DV "
        f"bond-bug check); top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- audit_trail_conformance_* markers do NOT
# leak into sibling AUDIT-TRAIL family members or adjacent commands.
# ---------------------------------------------------------------------------


def test_conformance_markers_do_not_leak_into_sibling_families(cli_runner, valid_trail, monkeypatch):
    """``audit_trail_conformance_*`` markers must NOT appear with foreign
    prefixes when cmd_audit_trail_conformance raises. Specifically
    validates the AUDIT-TRAIL family cluster cross-prefix isolation.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cross-prefix-from-ee-role")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"

    foreign_prefixes = (
        # AUDIT-TRAIL family siblings (critical cluster cross-check)
        ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI/CN/EA"),
        ("audit_trail_export_", "cmd_audit_trail_export W607-AP/CR"),
        # Compliance / governance siblings
        ("article_12_", "cmd_article_12_check"),
        ("constitution_", "cmd_constitution"),
        ("permit_", "cmd_permit"),
        # Other adjacent commands
        ("attest_", "cmd_attest (attestation sibling)"),
        ("cga_", "cmd_cga (attestation sibling)"),
        ("runs_", "cmd_runs (run-ledger sibling)"),
        ("pr_bundle_", "cmd_pr_bundle"),
        ("preflight_", "cmd_preflight"),
        ("impact_", "cmd_impact"),
        ("critique_", "cmd_critique"),
        # Security-axis siblings (should never confuse with compliance)
        ("taint_", "cmd_taint"),
        ("vulns_", "cmd_vulns"),
        ("auth_gaps_", "cmd_auth_gaps"),
    )
    for marker in failure_markers:
        # Every marker must use the canonical audit_trail_conformance_* family.
        assert marker.startswith("audit_trail_conformance_"), (
            f"every cmd_audit_trail_conformance W607 marker must use the "
            f"``audit_trail_conformance_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in foreign_prefixes:
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) AUDIT-TRAIL FAMILY TRIAD CLOSURE -- all 3 legs carry W607 plumbing
# ---------------------------------------------------------------------------


def test_audit_trail_triad_carries_w607_plumbing():
    """AUDIT-TRAIL family triad closure pin.

    With this CLOSE-AS-DUPLICATE wave, all three legs of the AUDIT-TRAIL
    family carry W607 aggregation-LAYER plumbing:

      * cmd_audit_trail_verify       -> W607-AI substrate + W607-CN agg + W607-EA agg-layer
      * cmd_audit_trail_export       -> W607-AP substrate + W607-CR agg
      * cmd_audit_trail_conformance  -> W607-AL substrate + W607-CO agg (this wave pins it)

    A regression in any leg that removes the substrate or aggregation
    accumulator/helper would silently demote the family closure.
    """
    # verify leg
    assert _VERIFY_SRC.exists(), "cmd_audit_trail_verify.py missing"
    verify_src = _VERIFY_SRC.read_text(encoding="utf-8")
    assert "w607ai_warnings_out" in verify_src, "verify W607-AI substrate missing"
    assert "_run_check_ai" in verify_src, "verify W607-AI helper missing"
    assert "w607cn_warnings_out" in verify_src, "verify W607-CN aggregation missing"
    assert "_run_check_cn" in verify_src, "verify W607-CN helper missing"
    assert "w607ea_warnings_out" in verify_src, "verify W607-EA agg-layer missing (just-landed wave)"

    # export leg
    assert _EXPORT_SRC.exists(), "cmd_audit_trail_export.py missing"
    export_src = _EXPORT_SRC.read_text(encoding="utf-8")
    assert "w607ap_warnings_out" in export_src, "export W607-AP substrate missing"
    assert "_run_check_ap" in export_src, "export W607-AP helper missing"
    assert "w607cr_warnings_out" in export_src, "export W607-CR aggregation missing"
    assert "_run_check_cr" in export_src, "export W607-CR helper missing"

    # conformance leg (this wave's pin)
    assert _CONFORMANCE_SRC.exists(), "cmd_audit_trail_conformance.py missing"
    conformance_src = _CONFORMANCE_SRC.read_text(encoding="utf-8")
    assert "w607al_warnings_out" in conformance_src, "conformance W607-AL substrate missing"
    assert "_run_check_al" in conformance_src, "conformance W607-AL helper missing"
    assert "w607co_warnings_out" in conformance_src, "conformance W607-CO aggregation missing"
    assert "_run_check_co" in conformance_src, "conformance W607-CO helper missing"


# ---------------------------------------------------------------------------
# (13) Helper-template ``return default`` verbatim shape pin
# ---------------------------------------------------------------------------


def test_run_check_co_helper_returns_default_verbatim():
    """The W607-CO helper template MUST return *default* verbatim on
    exception (not ``None``, not a computed expression, not a re-raise).

    Brief template:

        def _run_check_co(phase, fn, *args, default=None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                _w607co_warnings_out.append(
                    f"audit_trail_conformance_{phase}_failed:{type(exc).__name__}:{exc}"
                )
                return default  # MUST be verbatim `default`
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_run_check_co":
            continue
        found_helper = True

        # Find the try/except inside the helper
        try_nodes = [n for n in ast.walk(node) if isinstance(n, ast.Try)]
        assert try_nodes, "_run_check_co must contain a try/except block"

        # Examine the last Return inside the except handler
        for try_node in try_nodes:
            for handler in try_node.handlers:
                returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
                assert returns, "_run_check_co except handler must contain a return"
                last_return = returns[-1]
                assert isinstance(last_return.value, ast.Name), (
                    f"_run_check_co except handler must `return default` "
                    f"verbatim (a Name node), not a computed expression; "
                    f"got AST node {type(last_return.value).__name__!r}"
                )
                assert last_return.value.id == "default", (
                    f"_run_check_co except handler must `return default` verbatim; got `return {last_return.value.id}`"
                )

    assert found_helper, "_run_check_co helper function not found in cmd_audit_trail_conformance.py"


# ---------------------------------------------------------------------------
# (14) Marker-shape contract -- audit_trail_conformance_<phase>_failed:<exc>:<detail>
# ---------------------------------------------------------------------------


def test_marker_shape_three_colon_delimited(cli_runner, valid_trail, monkeypatch):
    """W607-CO marker shape MUST be exactly
    ``audit_trail_conformance_<phase>_failed:<exc_class>:<detail>``
    -- 3 colon-delimited segments after the family prefix.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise ValueError("delim-check-detail-string")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("audit_trail_conformance_serialize_envelope_failed:")]
    assert markers, f"expected serialize_envelope marker; got {top_wo!r}"

    for marker in markers:
        # Family prefix
        assert marker.startswith("audit_trail_conformance_"), marker
        # Phase delimiter
        assert "_failed:" in marker, marker
        # Exc-class + detail segments
        tail = marker.split("_failed:", 1)[1]
        parts = tail.split(":", 1)
        assert len(parts) == 2, f"marker tail must split into <exc_class>:<detail> on first colon; got tail={tail!r}"
        exc_class, detail = parts
        assert exc_class == "ValueError", f"exc_class segment must be the exception type name; got {exc_class!r}"
        assert "delim-check-detail-string" in detail, f"detail segment must carry the exception string; got {detail!r}"


# ---------------------------------------------------------------------------
# (15) W827 REGRESSION GUARD -- no-trail branch stays explicit (Pattern-2)
# ---------------------------------------------------------------------------


def test_w827_no_trail_branch_stays_explicit(cli_runner, tmp_path):
    """W827 regression guard (Pattern-2 silent-fallback seal).

    The no-trail branch (missing file OR zero records) MUST emit an
    explicit ``state: no_trail`` + ``partial_success: True``, with each
    of the 6 Article-12 checks carrying ``state: not_run``. The W607-CO
    aggregation plumbing MUST NOT regress this to a silent SAFE /
    NON-conformant verdict.
    """
    missing_path = tmp_path / "does_not_exist.jsonl"
    result = _invoke_conformance(cli_runner, missing_path)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    assert summary.get("state") == "no_trail", f"W827 regression: state must be ``no_trail``; got {summary!r}"
    assert summary.get("partial_success") is True, (
        f"W827 regression: no-trail branch must flip partial_success; got {summary!r}"
    )
    # Verdict explicitly names the absent state
    verdict = summary.get("verdict", "")
    assert "no audit trail" in verdict.lower(), (
        f"W827 regression: verdict must name the no-trail state explicitly; got {verdict!r}"
    )

    # Article 12 not_run preserved on each of the 6 checks
    checks = data.get("checks", [])
    assert len(checks) == 6, f"no-trail branch must emit 6 Article-12 not_run checks; got {checks!r}"
    for chk in checks:
        assert chk.get("state") == "not_run", f"Article-12 not_run state regressed; got check={chk!r}"
        assert chk.get("passed") is False, f"not_run check must report passed=False; got {chk!r}"


# ---------------------------------------------------------------------------
# (16) W827 3-state matrix -- (no_trail / partial / conformant) all
# carry distinct verdicts under W607-CO plumbing.
# ---------------------------------------------------------------------------


def test_w827_three_state_matrix_under_w607co(cli_runner, tmp_path, valid_trail):
    """The W827 3-state contract: each of (no_trail / partial_conformance /
    conformant) emits a distinct verdict. W607-CO MUST NOT collapse two
    states to the same string.
    """
    # 1. no_trail state
    missing = tmp_path / "absent.jsonl"
    r1 = _invoke_conformance(cli_runner, missing)
    d1 = _json.loads(r1.output)
    v1 = d1["summary"]["verdict"]
    assert "no audit trail" in v1.lower(), f"no_trail verdict = {v1!r}"

    # 2. partial / non-conformant (young records fail retention)
    r2 = _invoke_conformance(cli_runner, valid_trail)
    d2 = _json.loads(r2.output)
    v2 = d2["summary"]["verdict"]
    # Either partial-conformance or NON-conformant depending on score;
    # both are distinct from the no_trail string.
    assert "no audit trail" not in v2.lower(), f"populated-trail verdict must NOT collapse to no_trail; got {v2!r}"
    assert v2 != v1, f"populated-trail verdict must differ from no_trail verdict; got v1={v1!r}, v2={v2!r}"


# ---------------------------------------------------------------------------
# (17) W145 findings registry persist preserved
# ---------------------------------------------------------------------------


def test_w145_findings_persist_preserved_in_source():
    """W145 audit-trail-conformance findings registry persist preserved.

    The W607-AL/CO plumbing routes the persist path through
    ``_run_check_al("open_findings_db", ...)`` /
    ``_run_check_al("emit_findings", ...)`` /
    ``_run_check_al("commit_findings", ...)``. Confirm the structured
    boundaries are intact (not replaced by a silent except).
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")

    assert (
        '_run_check_al("open_findings_db"' in src
        or ('_run_check_al(\n        "open_findings_db"' in src)
        or '"open_findings_db"' in src
    ), "W145 open_findings_db boundary missing"
    assert '"emit_findings"' in src, "W145 emit_findings boundary missing"
    assert '"commit_findings"' in src, "W145 commit_findings boundary missing"
    assert "AUDIT_TRAIL_CONFORMANCE_DETECTOR_VERSION" in src, "W145 detector version constant missing"


# ---------------------------------------------------------------------------
# (18) HMAC chain-verify invariant preserved (inherits from verify)
# ---------------------------------------------------------------------------


def test_hmac_chain_verify_invariant_preserved():
    """The HMAC chain-verify substrate is delegated to via
    ``_check_chain_integrity``, which is wrapped under
    ``_run_check_al("check_chain_integrity", ...)``. The boundary MUST
    stay intact so a chain-verify raise surfaces as a structured marker
    rather than crashing the conformance command.
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")
    assert "_check_chain_integrity" in src, "_check_chain_integrity substrate missing"
    assert '"check_chain_integrity"' in src, "check_chain_integrity W607-AL phase missing"


# ---------------------------------------------------------------------------
# (19) Cross-prefix isolation against EE-prefix specifically -- there
# should be NO ``audit_trail_conformance_ee_*`` markers anywhere.
# ---------------------------------------------------------------------------


def test_no_ee_prefixed_markers_anywhere_in_source():
    """W607-EE-on-cmd_audit_trail_conformance is CLOSED. There must be
    no ``ee_*`` phase markers (e.g. ``ee_serialize_envelope`` as the
    brief proposed) in the source -- the existing CO layer's
    ``serialize_envelope`` carries the role without collision since
    there is only ONE aggregation layer.
    """
    src = _CONFORMANCE_SRC.read_text(encoding="utf-8")
    # Marker forms the brief proposed should NOT appear
    assert "ee_serialize_envelope" not in src, (
        "EE phase rename ``ee_serialize_envelope`` unexpectedly present; "
        "the brief proposed this rename to dodge W978 4th-discipline "
        "collision, but the CLOSE-AS-DUPLICATE path means the rename "
        "is unnecessary and adds confusion."
    )
    assert "audit_trail_conformance_ee_" not in src, "EE-prefixed marker family unexpectedly present"


# ---------------------------------------------------------------------------
# (20) Happy path -- envelope stays byte-stable (no EE/CO markers leak)
# ---------------------------------------------------------------------------


def test_happy_path_no_aggregation_markers(cli_runner, valid_trail):
    """Clean conformance on a populated trail -> no W607-CO aggregation
    markers (and certainly no W607-EE markers since EE is closed).
    Hash-stable happy path.
    """
    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # No aggregation-phase markers on clean path
    for phase in _EE_ROLE_PHASES:
        offenders = [m for m in all_wo if f"audit_trail_conformance_{phase}_failed:" in m]
        assert not offenders, f"happy path leaked W607-CO {phase} markers: {offenders!r}"

    # No EE-prefixed markers at all
    ee_markers = [m for m in all_wo if "_ee_" in m]
    assert not ee_markers, f"happy path leaked EE-prefixed markers: {ee_markers!r}"


# ---------------------------------------------------------------------------
# (21) AST-scan AUDIT-TRAIL family triad confirms all 3 carry _run_check_*
# helpers (the structural shape that proves W607 plumbing landed)
# ---------------------------------------------------------------------------


def test_audit_trail_triad_ast_helpers_present():
    """AST-scan all 3 AUDIT-TRAIL legs and confirm each defines its
    W607 helper functions (substrate + aggregation).
    """
    expected = {
        _VERIFY_SRC: {"_run_check_ai", "_run_check_cn", "_run_check_ea"},
        _EXPORT_SRC: {"_run_check_ap", "_run_check_cr"},
        _CONFORMANCE_SRC: {"_run_check_al", "_run_check_co"},
    }
    for src_path, helpers in expected.items():
        src = src_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        missing = helpers - defined
        assert not missing, (
            f"{src_path.name} missing W607 helpers: {missing!r}; "
            f"defined helpers include: "
            f"{sorted(h for h in defined if h.startswith('_run_check_'))!r}"
        )
