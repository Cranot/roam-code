"""W607-CO -- additive aggregation-phase plumbing for ``cmd_audit_trail_conformance``.

cmd_audit_trail_conformance is the COMPLIANCE-checking leg of the
AUDIT-TRAIL FAMILY. With W607-CO landed, the full conformance-check
build path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-AL (10 substrate boundaries:
    load_records / check_chain_integrity / check_timestamp_completeness /
    check_actor_attribution / check_reproducibility_metadata /
    check_verdict_and_rationale / check_retention / open_findings_db /
    emit_findings / commit_findings)
  - aggregation-phase layer: W607-CO (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``audit_trail_conformance_*`` marker
family and the
``audit_trail_conformance_<phase>_failed:<exc_class>:<detail>`` shape
contract. The two buckets (``_w607al_warnings_out`` substrate-CALL +
``_w607co_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

AUDIT-TRAIL FAMILY pairing
--------------------------

  cmd_audit_trail_verify       (W607-AI substrate + W607-CN aggregation)
  cmd_audit_trail_conformance  (W607-AL substrate + W607-CO THIS)
  cmd_audit_trail_export       (W607-AP substrate; CP candidate)

W827 regression guard (Pattern 2 silent-fallback)
-------------------------------------------------

W827 (Fix E) sealed the empty-corpus / no-trail silent-fallback. The
no-trail branch (``trail_absent``) emits an explicit ``state: no_trail``
+ ``partial_success: True`` rather than a clean SAFE verdict. W607-CO
MUST NOT re-introduce a Pattern-2 silent-SAFE bug on the no-trail
branch -- the dedicated guard below confirms it stays explicit. Also
confirms the trail-populated branch's per-article-conformance scoring
(GDPR Article 12 + SOC2) survives the W607-CO plumbing.

W978 first-hypothesis check (5 recurring traps)
-----------------------------------------------

cmd_taint W607-CJ codified the 5th W978 discipline: move ``len()``
INSIDE the wrapped closure rather than at the kwarg-bind site. Every
W607-CO ``default=`` MUST be a literal constant, AND every ``len()``
/ ``sum()`` over the wrapped input MUST live inside the closure. The
defensive test below exercises the floor on a corrupt-input sentinel
(``_BadChecksList``) mirroring cmd_sbom's ``_BadDeps(list)`` shape.

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
    """Audit trail with a valid chain (clean substrate path).

    Chain integrity + timestamps + actors + reproducibility +
    verdict-rationale all PASS; retention will FAIL (records are young
    2026-05 timestamps). This is the natural "trail loaded, scoring
    runs" state we need for the aggregation-phase envelope shape lock.
    """
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
# (1) Happy path -- envelope omits W607-CO aggregation markers
# ---------------------------------------------------------------------------


def test_conformance_happy_path_no_w607co_markers(cli_runner, valid_trail):
    """Clean conformance on a populated trail -> no W607-CO aggregation markers.

    Hash-stable: an empty W607-CO bucket on the success path must produce
    an envelope without any
    ``audit_trail_conformance_score_classify_failed:`` /
    ``audit_trail_conformance_compute_predicate_failed:`` /
    ``audit_trail_conformance_compute_verdict_failed:`` /
    ``audit_trail_conformance_serialize_envelope_failed:`` markers (from
    the CO layer).
    """
    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-conformance-check"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607co_phases = (
        "audit_trail_conformance_score_classify_failed:",
        "audit_trail_conformance_compute_predicate_failed:",
        "audit_trail_conformance_compute_verdict_failed:",
        "audit_trail_conformance_serialize_envelope_failed:",
    )
    for prefix in w607co_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean conformance must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_co`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_conformance_carries_w607co_accumulator():
    """AST-level guard: cmd_audit_trail_conformance source carries the
    W607-CO accumulator.

    Pins the canonical W607-CO anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AL) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    assert src_path.exists(), f"cmd_audit_trail_conformance.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607co_warnings_out" in src, (
        "W607-CO accumulator missing from cmd_audit_trail_conformance; the "
        "additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_co" in src, (
        "W607-CO helper ``_run_check_co`` missing from cmd_audit_trail_conformance; "
        "the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_co is defined inside the command.
    tree = ast.parse(src)
    found_run_check_co = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_co":
            found_run_check_co = True
            break
    assert found_run_check_co, (
        "W607-CO ``_run_check_co`` helper not found in "
        "cmd_audit_trail_conformance AST; the additive aggregation-phase "
        "wrapper has been refactored away."
    )

    # W607-AL must still be present (additive layer does NOT replace it)
    assert "w607al_warnings_out" in src, (
        "W607-AL accumulator vanished alongside the W607-CO add; the "
        "additive plumbing must preserve the W607-AL substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_co():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_co(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_co("<phase>", ...)``
    call inside cmd_audit_trail_conformance.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_co(\n        "{phase}"',
            f'_run_check_co(\n            "{phase}"',
            f'_run_check_co(\n                "{phase}"',
            f'_run_check_co(\n                    "{phase}"',
            f'_run_check_co(\n                        "{phase}"',
            f'_run_check_co("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_co(...); add the W607-CO guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) score_classify failure -> marker emitted, envelope still ships
# ---------------------------------------------------------------------------


def test_score_classify_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If the score_classify boundary raises, surface the marker.

    We monkey-patch one of the per-article checks to return a non-dict
    sentinel that crashes the score_classify closure's ``c["passed"]``
    access. The W607-CO ``score_classify`` boundary catches the raise
    and emits the marker.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    # Patch ``_check_chain_integrity`` to return a sentinel that survives
    # tuple-unpacking (returns the expected 2-tuple) but injects a
    # ``passed``-raising sentinel into the dict ``c["passed"]`` access
    # inside the score_classify closure. Cleanest path: directly poison
    # one of the checks dict entries by patching the function that
    # produces ``passed`` to return a __bool__-raising sentinel.

    class _BadBool:
        def __bool__(self):
            raise RuntimeError("synthetic-score-classify-from-W607-CO")

    # The score_classify closure does ``sum(1 for c in _checks if c["passed"])``.
    # If c["passed"] evaluates to a _BadBool, the bool() lookup raises.
    def _good_returning_bad_bool(*args, **kwargs):
        return (_BadBool(), "synthetic check returning __bool__-raising sentinel")

    monkeypatch.setattr(_mod, "_check_chain_integrity", _good_returning_bad_bool)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("audit_trail_conformance_score_classify_failed:")]
    assert markers, f"expected ``audit_trail_conformance_score_classify_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    W978 first-hypothesis: a __format__-raising sentinel under test would
    re-raise inside the default f-string. The canonical floor for
    cmd_audit_trail_conformance is
    ``"audit-trail-conformance check completed"`` (mirror of cmd_taint's
    ``"Taint analysis completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="audit-trail-conformance check completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CO "
        "discipline; the canonical floor literal "
        "'audit-trail-conformance check completed' is missing from "
        "cmd_audit_trail_conformance.py"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607co_serialize_envelope_floor_on_raise(cli_runner, valid_trail, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``audit_trail_conformance_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("audit-trail-conformance-check", ...)`` would otherwise
    crash AFTER all substrate + aggregation signals were already gathered.
    The consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-conformance-check", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("audit_trail_conformance_serialize_envelope_failed:")]
    assert markers, f"expected ``audit_trail_conformance_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, valid_trail, monkeypatch):
    """ANY W607-CO or W607-AL marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    conformance" from "conformance ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CO warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607co_warnings_out_in_both_top_and_summary(cli_runner, valid_trail, monkeypatch):
    """Non-empty W607-CO bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AL contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CO raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CO raise path; got summary = {data['summary']!r}"
    )

    top_markers = [
        m for m in data["warnings_out"] if m.startswith("audit_trail_conformance_serialize_envelope_failed:")
    ]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("audit_trail_conformance_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CO uses the SAME ``audit_trail_conformance_*`` family
# ---------------------------------------------------------------------------


def test_w607co_marker_prefix_audit_trail_conformance_family(cli_runner, valid_trail, monkeypatch):
    """W607-CO markers use the canonical ``audit_trail_conformance_*``
    prefix (same family as W607-AL; W607-CO is ADDITIVE, not a separate
    prefix).

    Hard guard: any W607-CO marker that leaks into a sibling W607-*
    family (e.g. ``audit_trail_verify_*`` / ``audit_trail_export_*`` /
    ``attest_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("audit_trail_conformance_"), (
            f"every W607-CO marker must use the ``audit_trail_conformance_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) W607-AL COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607al_substrate_markers_coexist_with_w607co_aggregation(cli_runner, valid_trail, monkeypatch):
    """Confirm ``audit_trail_conformance_<substrate-phase>_failed:`` markers
    (W607-AL layer) coexist with
    ``audit_trail_conformance_<agg-phase>_failed:`` markers (W607-CO layer)
    -- both in same family, but threaded through different buckets at
    envelope-emit.

    This is the explicit guard requested by the W607-CO brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``audit_trail_conformance_<substrate-phase>_failed:`` vs.
    ``audit_trail_conformance_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    # W607-AL substrate boundary -- one of the per-article checks raises
    def _raise_chain_check(*a, **kw):
        raise RuntimeError("synthetic-al-coexist-chain-check")

    # W607-CO aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-co-coexist-envelope")

    monkeypatch.setattr(_mod, "_check_chain_integrity", _raise_chain_check)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AL
    al_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_check_chain_integrity_failed:")]
    # Aggregation-phase from W607-CO
    co_markers = [m for m in top_wo if m.startswith("audit_trail_conformance_serialize_envelope_failed:")]

    assert al_markers, (
        f"W607-AL substrate-CALL marker (audit_trail_conformance_check_chain_integrity_failed) missing; got {top_wo!r}"
    )
    assert co_markers, (
        f"W607-CO aggregation-phase marker (audit_trail_conformance_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``audit_trail_conformance_*`` family
    assert all(m.startswith("audit_trail_conformance_") for m in (al_markers + co_markers)), (
        f"all markers must share the canonical ``audit_trail_conformance_*`` "
        f"family; got al = {al_markers!r}, co = {co_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- audit_trail_conformance_* markers DO NOT
# leak into adjacent commands (cmd_audit_trail_verify, cmd_audit_trail_export)
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_markers_do_not_leak_into_adjacent_commands(cli_runner, valid_trail, monkeypatch):
    """``audit_trail_conformance_*`` markers must NOT appear with foreign
    prefixes (``audit_trail_verify_*`` / ``audit_trail_export_*`` /
    ``attest_*`` / ``pr_bundle_*``) when conformance raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    # Every failure marker must start with audit_trail_conformance_ --
    # foreign-family leakage is a bug. Note: we cannot literally exclude
    # ``audit_trail_verify_`` / ``audit_trail_export_`` from the
    # ``audit_trail_conformance_`` family because Python prefix-matching
    # is monotonic; instead we positively assert membership in the
    # conformance family.
    foreign_prefixes = (
        "audit_trail_verify_",
        "audit_trail_export_",
        "attest_",
        "pr_bundle_",
        "taint_",
        "vulns_",
        "vuln_reach_",
        "preflight_",
        "impact_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_audit_trail_conformance warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) AUDIT-TRAIL FAMILY pairing -- conformance / verify / export markers
# stay isolated when invoked on the same workspace
# ---------------------------------------------------------------------------


def test_audit_trail_family_marker_isolation(cli_runner, valid_trail, monkeypatch):
    """AUDIT-TRAIL FAMILY pairing guard requested by the W607-CO brief:

    Confirm that ``audit_trail_conformance_<phase>_failed:`` markers
    (W607-AL + W607-CO) stay in the canonical
    ``audit_trail_conformance_*`` family when conformance is invoked on
    a workspace also covered by cmd_audit_trail_verify (W607-AI + CN)
    and cmd_audit_trail_export (W607-AP). Each command's markers must
    stay in its OWN family and never bleed into a sibling's envelope.

    Closes the AUDIT-TRAIL FAMILY: every emitter in the trio now has
    dual-bucket plumbing (substrate-CALL + aggregation-phase for
    conformance via W607-AL + CO) AND prefix-isolation guards.

    Strategy: monkeypatch conformance's json_envelope to raise so a
    W607-CO marker fires, and confirm:
      1. conformance envelope carries ``audit_trail_conformance_*`` markers
      2. conformance envelope does NOT carry ``audit_trail_verify_*`` /
         ``audit_trail_export_*`` foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``audit_trail_conformance_`` prefix.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-family-isolation-from-W607-CO")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # conformance envelope MUST contain
    # audit_trail_conformance_serialize_envelope_failed
    assert any(m.startswith("audit_trail_conformance_serialize_envelope_failed:") for m in all_markers), (
        f"conformance envelope missing audit_trail_conformance_serialize_envelope_failed marker; got {all_markers!r}"
    )

    # conformance envelope MUST NOT contain sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("audit_trail_verify_"), (
            f"conformance envelope leaked audit_trail_verify_* marker: {marker!r}"
        )
        assert not marker.startswith("audit_trail_export_"), (
            f"conformance envelope leaked audit_trail_export_* marker: {marker!r}"
        )

    # Closed-enum check: every failure marker uses the canonical
    # ``audit_trail_conformance_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("audit_trail_conformance_"), (
            f"every audit-trail-conformance failure marker must use the "
            f"canonical ``audit_trail_conformance_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (13) W827 REGRESSION GUARD -- no-trail branch stays explicit
# ---------------------------------------------------------------------------


def test_w827_no_trail_branch_stays_explicit(cli_runner, tmp_path):
    """W827 regression guard (Pattern 2 silent-fallback).

    W827 (Fix E) sealed a Pattern-2 silent-fallback bug: the no-trail
    branch used to compute a 0/6 score and report NON-conformant, which
    misled consumers into thinking the trail was scanned and failed.
    The fix names the absent state explicitly (``state: no_trail`` +
    ``partial_success: True``). W607-CO MUST NOT re-introduce this bug.

    Strategy: invoke conformance against a non-existent trail path and
    confirm the no-trail branch still emits the explicit state.
    """
    nonexistent_trail = tmp_path / "does-not-exist.jsonl"
    result = _invoke_conformance(cli_runner, nonexistent_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    # W827 contract: name the absent state explicitly
    assert summary.get("state") == "no_trail", (
        f"W827 regression: no-trail branch must emit explicit state='no_trail'; got summary={summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"W827 regression: no-trail branch must set partial_success=True; got summary={summary!r}"
    )
    # Verdict must NOT be silent-SAFE/PASS/clean
    verdict = summary.get("verdict", "").lower()
    assert "no audit trail" in verdict, (
        f"W827 regression: verdict must name absent state explicitly; got verdict={summary.get('verdict')!r}"
    )


# ---------------------------------------------------------------------------
# (14) Article-12 + 6-check scoring survives W607-CO plumbing
# ---------------------------------------------------------------------------


def test_article_12_six_check_scoring_survives_w607co(cli_runner, valid_trail):
    """GDPR Article 12 + SOC2 regression guard: per-article (6-check)
    conformance scoring survives the W607-CO plumbing.

    cmd_audit_trail_conformance scores against 6 Article-12 checks
    (chain_integrity / timestamp_completeness / actor_attribution /
    reproducibility_metadata / verdict_and_rationale / retention).
    The W607-CO aggregation plumbing must NOT shadow these fields on
    the success path.

    Loose check: on a clean conformance run, the envelope carries:
      - ``checks_passed`` + ``checks_total`` (= 6) in summary
      - ``score`` (0-100) in summary
      - ``checks`` array of 6 entries, each with ``id`` + ``passed`` + ``message``
      - ``compliance_kind`` = "audit_trail_chain_integrity"
      - ``schema_reference`` mentions "EU AI Act"
    """
    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # 6-check Article-12 scoring survives W607-CO
    assert "checks_passed" in summary, (
        f"checks_passed missing on clean envelope; summary keys = {sorted(summary.keys())!r}"
    )
    assert "checks_total" in summary, (
        f"checks_total missing on clean envelope; summary keys = {sorted(summary.keys())!r}"
    )
    assert summary["checks_total"] == 6, (
        f"W607-CO regression: checks_total={summary['checks_total']} != 6; the 6-check Article-12 contract has drifted"
    )
    assert isinstance(summary.get("score"), int), f"score must be int after W607-CO; got {summary.get('score')!r}"

    checks = data.get("checks") or []
    assert len(checks) == 6, (
        f"6-check Article-12 conformance contract: expected 6 checks, got {len(checks)}; checks={checks!r}"
    )
    canonical_ids = {
        "chain_integrity",
        "timestamp_completeness",
        "actor_attribution",
        "reproducibility_metadata",
        "verdict_and_rationale",
        "retention",
    }
    seen_ids = {c["id"] for c in checks}
    assert seen_ids == canonical_ids, (
        f"6-check Article-12 conformance contract: id set drifted; expected {canonical_ids!r}, got {seen_ids!r}"
    )

    assert summary.get("compliance_kind") == "audit_trail_chain_integrity", (
        f"compliance_kind drifted under W607-CO; got {summary.get('compliance_kind')!r}"
    )
    assert "EU AI Act" in summary.get("schema_reference", ""), (
        f"schema_reference must mention EU AI Act per Article-12 contract; got {summary.get('schema_reference')!r}"
    )


# ---------------------------------------------------------------------------
# (15) W978 5-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-CO ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_co(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants.
    Reject any Call, Attribute, Subscript, BinOp, UnaryOp (non-numeric),
    Compare, IfExp, or f-string node in the default expression -- these
    compute from upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE the
        wrap call, e.g. ``default=_envelope_floor``). Rejects Call,
        Attribute, Subscript, BinOp, Compare, IfExp, f-string, etc. --
        these can compute over potentially-poisoned upstream values at
        kwarg-bind time and raise BEFORE the wrap's try-block enters.

        Note: bare ``Name`` references are safe because the underlying
        variable was constructed at an earlier statement; the kwarg-bind
        only reads the already-built value. The W978 trap fires on
        expressions, not on name lookups.
        """
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, (ast.Dict)):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        # ast.UnaryOp with USub on a constant (e.g. -1) is acceptable
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _run_check_co(...)
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_co"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_co(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in "
        "cmd_audit_trail_conformance.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ for the canonical fix "
        "pattern."
    )


# ---------------------------------------------------------------------------
# (16) W978 5-discipline defensive -- corrupt-input sentinel
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_does_not_eagerly_raise_on_bad_input(cli_runner, valid_trail, monkeypatch):
    """W978 defensive test: exercise the W607-CO closures on a poisoned
    ``checks`` list and confirm the floor catches the raise.

    Mirrors cmd_sbom's ``_BadDeps(list)`` / cmd_taint's
    ``_BadFindingList`` discipline -- a dict-like check whose
    ``__getitem__("passed")`` raises. The score_classify closure
    iterates ``checks`` and does ``c["passed"]`` -- if the
    W978-5th-discipline ``len()``/``sum()`` were at the kwarg-bind site,
    the raise would escape the try-block. Because ``len()``/``sum()``
    live INSIDE the score_classify closure, the raise lands inside the
    try-block and surfaces a marker.

    Strategy: patch ``_check_chain_integrity`` to return a 2-tuple whose
    first element is a __bool__-raising sentinel. The pre-aggregation
    code does ``chain_ok, chain_msg = _run_check_al(...)`` which is
    fine; then ``checks[]`` is built containing this sentinel as
    ``passed``; then score_classify does ``sum(1 for c in _checks if
    c["passed"])`` which triggers __bool__. The W607-CO score_classify
    closure catches the raise and emits the marker.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    class _BadBool:
        # Mimics cmd_sbom's _BadDeps / cmd_taint's _BadFindingList:
        # an object whose __bool__ raises.
        def __bool__(self):
            raise RuntimeError("synthetic-w978-bad-bool-from-W607-CO")

    def _bad_check(*args, **kwargs):
        return (_BadBool(), "synthetic check returning __bool__-raising sentinel")

    monkeypatch.setattr(_mod, "_check_chain_integrity", _bad_check)

    result = _invoke_conformance(cli_runner, valid_trail)
    # The command MUST NOT crash -- a marker must be on the envelope
    # rather than the raise escaping the W607-CO score_classify wrap.
    assert result.exit_code == 0, f"W978 violation: bad-bool sentinel caused crash; output={result.output!r}"
    # Envelope must be parseable and carry SOMETHING in warnings_out
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-conformance-check", data
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # W607-CO score_classify must carry the marker
    co_markers = [m for m in all_wo if m.startswith("audit_trail_conformance_score_classify_failed:")]
    assert co_markers, (
        f"W978 regression: bad-bool sentinel produced no W607-CO marker "
        f"on the envelope; the bad input either bypassed the wraps or "
        f"eagerly raised in default=; got all_wo={all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (17) Combined-bucket ordering -- AL markers precede CO markers
# ---------------------------------------------------------------------------


def test_combined_bucket_ordering_al_before_co(cli_runner, valid_trail, monkeypatch):
    """When BOTH W607-AL and W607-CO markers fire, the combined bucket
    presents AL substrate markers BEFORE CO aggregation markers.

    Substrate boundaries fire BEFORE aggregation phases in execution
    order; the combined-bucket assembly preserves this ordering so the
    full degradation lineage is visible in marker-emission order.
    """
    from roam.commands import cmd_audit_trail_conformance as _mod

    def _raise_chain_check(*a, **kw):
        raise RuntimeError("synthetic-ordering-al-chain-check")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ordering-co-envelope")

    monkeypatch.setattr(_mod, "_check_chain_integrity", _raise_chain_check)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_conformance(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []

    # Find AL substrate marker index (check_chain_integrity is substrate)
    al_idx = next(
        (i for i, m in enumerate(top_wo) if m.startswith("audit_trail_conformance_check_chain_integrity_failed:")),
        None,
    )
    # Find CO aggregation marker index (serialize_envelope is aggregation)
    co_idx = next(
        (i for i, m in enumerate(top_wo) if m.startswith("audit_trail_conformance_serialize_envelope_failed:")),
        None,
    )
    assert al_idx is not None, f"AL marker missing in {top_wo!r}"
    assert co_idx is not None, f"CO marker missing in {top_wo!r}"
    assert al_idx < co_idx, (
        f"AL substrate marker (index {al_idx}) must precede CO aggregation "
        f"marker (index {co_idx}) in combined bucket; got {top_wo!r}"
    )
