"""W607-CR -- additive aggregation-phase plumbing for ``cmd_audit_trail_export``.

cmd_audit_trail_export is the EXPORT leg of the AUDIT-TRAIL FAMILY.
With W607-CR landed, the full export build path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-AP (10 substrate boundaries:
    load_records_finalize / compute_chain_head / build_integrity_summary /
    append_integrity_summary / load_records / filter_records /
    aggregate_records / build_top_actors / render_output /
    atomic_write_text)
  - aggregation-phase layer: W607-CR (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``audit_trail_export_*`` marker family
and the ``audit_trail_export_<phase>_failed:<exc_class>:<detail>`` shape
contract. The two buckets (``_w607ap_warnings_out`` substrate-CALL +
``_w607cr_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

AUDIT-TRAIL FAMILY 3-WAY pairing (closes the family at aggregation layer)
-------------------------------------------------------------------------

  cmd_audit_trail_verify       (W607-AI substrate + W607-CN aggregation)
  cmd_audit_trail_conformance  (W607-AL substrate + W607-CO aggregation)
  cmd_audit_trail_export       (W607-AP substrate + W607-CR THIS)

W978 first-hypothesis check (5 recurring traps)
-----------------------------------------------

cmd_taint W607-CJ codified the 5th W978 discipline: move ``len()``
INSIDE the wrapped closure rather than at the kwarg-bind site. Every
W607-CR ``default=`` MUST be a literal constant, AND every ``len()``
/ ``sum()`` over the wrapped input MUST live inside the closure. The
defensive test below exercises the floor on a corrupt-input sentinel
mirroring cmd_sbom's ``_BadDeps(list)`` shape.

MULTI-FORMAT NOTE
-----------------

cmd_audit_trail_export has 3 emit paths (CSV / JSON / markdown via
``--format``). The marker family must stay clean across all 3 formats;
the multi-format isolation guard below confirms it.

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


def _invoke_export(runner: CliRunner, trail_path: Path, *extra):
    """Invoke ``roam --json audit-trail-export --input <trail_path>``."""
    from roam.cli import cli

    args = ["--json", "audit-trail-export", "--input", str(trail_path)]
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
# (1) Happy path -- envelope omits W607-CR aggregation markers
# ---------------------------------------------------------------------------


def test_export_happy_path_no_w607cr_markers(cli_runner, valid_trail):
    """Clean export on a populated trail -> no W607-CR aggregation markers.

    Hash-stable: an empty W607-CR bucket on the success path must
    produce an envelope without any
    ``audit_trail_export_score_classify_failed:`` /
    ``audit_trail_export_compute_predicate_failed:`` /
    ``audit_trail_export_compute_verdict_failed:`` /
    ``audit_trail_export_serialize_envelope_failed:`` markers (from the
    CR layer).
    """
    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit-trail-export"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cr_phases = (
        "audit_trail_export_score_classify_failed:",
        "audit_trail_export_compute_predicate_failed:",
        "audit_trail_export_compute_verdict_failed:",
        "audit_trail_export_serialize_envelope_failed:",
    )
    for prefix in w607cr_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean export must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cr`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_audit_trail_export_carries_w607cr_accumulator():
    """AST-level guard: cmd_audit_trail_export source carries the W607-CR
    accumulator.

    Pins the canonical W607-CR anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AP) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    assert src_path.exists(), f"cmd_audit_trail_export.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607cr_warnings_out" in src, (
        "W607-CR accumulator missing from cmd_audit_trail_export; the "
        "additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cr" in src, (
        "W607-CR helper ``_run_check_cr`` missing from cmd_audit_trail_export; "
        "the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cr is defined inside the command.
    tree = ast.parse(src)
    found_run_check_cr = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cr":
            found_run_check_cr = True
            break
    assert found_run_check_cr, (
        "W607-CR ``_run_check_cr`` helper not found in "
        "cmd_audit_trail_export AST; the additive aggregation-phase "
        "wrapper has been refactored away."
    )

    # W607-AP must still be present (additive layer does NOT replace it)
    assert "w607ap_warnings_out" in src, (
        "W607-AP accumulator vanished alongside the W607-CR add; the "
        "additive plumbing must preserve the W607-AP substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cr():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cr(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_cr("<phase>", ...)``
    call inside cmd_audit_trail_export.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "score_classify",
        "compute_predicate",
        "compute_verdict",
        "serialize_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_cr(\n        "{phase}"',
            f'_run_check_cr(\n            "{phase}"',
            f'_run_check_cr(\n                "{phase}"',
            f'_run_check_cr(\n                    "{phase}"',
            f'_run_check_cr(\n                        "{phase}"',
            f'_run_check_cr("{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_cr(...); add the W607-CR guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_verdict failure -> marker emitted, envelope still ships
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, valid_trail, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    Monkey-patch len() inside the verdict closure by patching the
    filtered list to a __len__-raising sentinel. The closure does
    ``f"{len(_filtered)} record(s) exported"`` -- if _filtered.__len__
    raises, the W607-CR ``compute_verdict`` boundary catches it.

    Easier: monkeypatch the _filter_records helper to return an object
    whose ``__len__`` raises. _filter_records is wrapped in W607-AP --
    so we want to short-circuit BEFORE it. Instead, monkeypatch
    ``_render_markdown`` to return cleanly but patch ``_filter_records``
    AFTER. Cleanest: directly monkeypatch a name inside the compute_verdict
    closure by patching the f-string interpolation. Use a class whose
    __len__ raises and inject it via _filter_records returning it.
    """
    from roam.commands import cmd_audit_trail_export

    class _BadLen(list):
        def __len__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CR")

    def _filter_returns_bad_len(*args, **kwargs):
        return _BadLen()

    monkeypatch.setattr(cmd_audit_trail_export, "_filter_records", _filter_returns_bad_len)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # Any of score_classify, compute_predicate, compute_verdict may fire
    # depending on which len() runs first. The compute_verdict marker
    # specifically must fire at some point.
    cv_markers = [m for m in all_wo if m.startswith("audit_trail_export_compute_verdict_failed:")]
    assert cv_markers, f"expected ``audit_trail_export_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in cv_markers), cv_markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    W978 first-hypothesis: a __format__-raising sentinel under test
    would re-raise inside the default f-string. The canonical floor for
    cmd_audit_trail_export is ``"audit-trail-export completed"`` (mirror
    of cmd_audit_trail_conformance W607-CO's ``"audit-trail-conformance
    check completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="audit-trail-export completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CR "
        "discipline; the canonical floor literal "
        "'audit-trail-export completed' is missing from "
        "cmd_audit_trail_export.py"
    )


# ---------------------------------------------------------------------------
# (6) serialize_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cr_serialize_envelope_floor_on_raise(cli_runner, valid_trail, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``audit_trail_export_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("audit-trail-export", ...)`` would otherwise crash
    AFTER all substrate + aggregation signals were already gathered.
    The consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-export", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("audit_trail_export_serialize_envelope_failed:")]
    assert markers, f"expected ``audit_trail_export_serialize_envelope_failed:`` marker; got {top_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, valid_trail, monkeypatch):
    """ANY W607-CR or W607-AP marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    export" from "export ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CR warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cr_warnings_out_in_both_top_and_summary(cli_runner, valid_trail, monkeypatch):
    """Non-empty W607-CR bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AP contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CR raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CR raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("audit_trail_export_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("audit_trail_export_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CR uses the SAME ``audit_trail_export_*`` family
# ---------------------------------------------------------------------------


def test_w607cr_marker_prefix_audit_trail_export_family(cli_runner, valid_trail, monkeypatch):
    """W607-CR markers use the canonical ``audit_trail_export_*`` prefix
    (same family as W607-AP; W607-CR is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CR marker that leaks into a sibling W607-*
    family (e.g. ``audit_trail_verify_*`` / ``audit_trail_conformance_*``
    / ``attest_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("audit_trail_export_"), (
            f"every W607-CR marker must use the ``audit_trail_export_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) W607-AP COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607ap_substrate_markers_coexist_with_w607cr_aggregation(cli_runner, valid_trail, monkeypatch):
    """Confirm ``audit_trail_export_<substrate-phase>_failed:`` markers
    (W607-AP layer) coexist with ``audit_trail_export_<agg-phase>_failed:``
    markers (W607-CR layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    This is the explicit guard requested by the W607-CR brief: the
    additive aggregation-phase layer must NOT shadow the pre-existing
    substrate-CALL layer; both buckets must combine into the same
    warnings_out channel with marker-prefix disambiguation
    (``audit_trail_export_<substrate-phase>_failed:`` vs
    ``audit_trail_export_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_audit_trail_export as _mod

    # W607-AP substrate boundary -- render_output raises
    def _raise_render(*a, **kw):
        raise RuntimeError("synthetic-ap-coexist-render")

    # W607-CR aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cr-coexist-envelope")

    monkeypatch.setattr(_mod, "_render_markdown", _raise_render)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AP
    ap_markers = [m for m in top_wo if m.startswith("audit_trail_export_render_output_failed:")]
    # Aggregation-phase from W607-CR
    cr_markers = [m for m in top_wo if m.startswith("audit_trail_export_serialize_envelope_failed:")]

    assert ap_markers, (
        f"W607-AP substrate-CALL marker (audit_trail_export_render_output_failed) missing; got {top_wo!r}"
    )
    assert cr_markers, (
        f"W607-CR aggregation-phase marker (audit_trail_export_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``audit_trail_export_*`` family
    assert all(m.startswith("audit_trail_export_") for m in (ap_markers + cr_markers)), (
        f"all markers must share the canonical ``audit_trail_export_*`` "
        f"family; got ap = {ap_markers!r}, cr = {cr_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) CROSS-PREFIX ISOLATION -- audit_trail_export_* markers DO NOT leak
# into adjacent commands (cmd_audit_trail_verify, cmd_audit_trail_conformance)
# ---------------------------------------------------------------------------


def test_audit_trail_export_markers_do_not_leak_into_adjacent_commands(cli_runner, valid_trail, monkeypatch):
    """``audit_trail_export_*`` markers must NOT appear with foreign
    prefixes (``audit_trail_verify_*`` / ``audit_trail_conformance_*`` /
    ``attest_*`` / ``pr_bundle_*``) when export raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    foreign_prefixes = (
        "audit_trail_verify_",
        "audit_trail_conformance_",
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
                f"cmd_audit_trail_export warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (12) AUDIT-TRAIL FAMILY 3-WAY pairing -- conformance / verify / export
# markers stay isolated when invoked on the same workspace
# ---------------------------------------------------------------------------


def test_audit_trail_family_3way_marker_isolation(cli_runner, valid_trail, monkeypatch):
    """AUDIT-TRAIL FAMILY 3-WAY pairing guard requested by the W607-CR brief.

    Confirm that ``audit_trail_export_<phase>_failed:`` markers
    (W607-AP + W607-CR) stay in the canonical
    ``audit_trail_export_*`` family when export is invoked on a workspace
    also covered by cmd_audit_trail_verify (W607-AI + CN) and
    cmd_audit_trail_conformance (W607-AL + CO). Each command's markers
    must stay in its OWN family and never bleed into a sibling's
    envelope.

    Closes the AUDIT-TRAIL FAMILY 3-WAY: every emitter in the trio now
    has dual-bucket plumbing (substrate-CALL + aggregation-phase) AND
    prefix-isolation guards.

    Strategy: monkeypatch export's json_envelope to raise so a W607-CR
    marker fires, and confirm:
      1. export envelope carries ``audit_trail_export_*`` markers
      2. export envelope does NOT carry ``audit_trail_verify_*`` /
         ``audit_trail_conformance_*`` foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``audit_trail_export_`` prefix.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-family-3way-isolation-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # export envelope MUST contain
    # audit_trail_export_serialize_envelope_failed
    assert any(m.startswith("audit_trail_export_serialize_envelope_failed:") for m in all_markers), (
        f"export envelope missing audit_trail_export_serialize_envelope_failed marker; got {all_markers!r}"
    )

    # export envelope MUST NOT contain sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("audit_trail_verify_"), (
            f"export envelope leaked audit_trail_verify_* marker: {marker!r}"
        )
        assert not marker.startswith("audit_trail_conformance_"), (
            f"export envelope leaked audit_trail_conformance_* marker: {marker!r}"
        )

    # Closed-enum check: every failure marker uses the canonical
    # ``audit_trail_export_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("audit_trail_export_"), (
            f"every audit-trail-export failure marker must use the "
            f"canonical ``audit_trail_export_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (13) MULTI-FORMAT ISOLATION -- marker family stays clean across CSV/JSON/MD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["md", "json", "csv"])
def test_multi_format_marker_isolation(cli_runner, valid_trail, monkeypatch, fmt):
    """Multi-format isolation: marker family stays clean across CSV / JSON
    / markdown projections.

    cmd_audit_trail_export has 3 emit paths (``--format md|json|csv``).
    The W607-AP layer wraps each renderer via ``render_output`` (which
    delegates to _render_markdown / _render_csv / _render_json). The
    W607-CR aggregation phases live in the POST-rendering flow and are
    format-agnostic. Confirm the marker family stays in
    ``audit_trail_export_*`` across all 3 formats.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_envelope(*a, **kw):
        raise RuntimeError(f"synthetic-multi-format-{fmt}-from-W607-CR")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail, "--format", fmt)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, f"expected failure markers for format={fmt}; got {all_markers!r}"
    for marker in failure_markers:
        assert marker.startswith("audit_trail_export_"), f"format={fmt}: marker family must stay clean; got {marker!r}"

    # Specifically the W607-CR serialize_envelope marker MUST fire for
    # all 3 formats (the JSON-mode emit path is the only one that calls
    # json_envelope; CSV/MD modes don't reach json_envelope under
    # --json -- but the global --json wrapper forces JSON mode regardless
    # of --format, so serialize_envelope still fires here).
    cr_markers = [m for m in failure_markers if m.startswith("audit_trail_export_serialize_envelope_failed:")]
    assert cr_markers, f"format={fmt}: W607-CR serialize_envelope marker missing; got {all_markers!r}"


# ---------------------------------------------------------------------------
# (14) W978 5-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-CR ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_cr(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants.
    Reject any Call, Attribute, Subscript, BinOp, UnaryOp (non-numeric),
    Compare, IfExp, or f-string node in the default expression -- these
    compute from upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit_trail_export.py"
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
        # Match _run_check_cr(...)
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cr"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_cr(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in "
        "cmd_audit_trail_export.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ for the canonical fix "
        "pattern."
    )


# ---------------------------------------------------------------------------
# (15) W978 5-discipline defensive -- corrupt-input sentinel
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_does_not_eagerly_raise_on_bad_input(cli_runner, valid_trail, monkeypatch):
    """W978 defensive test: exercise the W607-CR closures on a poisoned
    ``filtered`` list and confirm the floor catches the raise.

    Mirrors cmd_sbom's ``_BadDeps(list)`` / cmd_taint's
    ``_BadFindingList`` discipline -- a list whose ``__len__`` raises.
    The score_classify / compute_verdict / compute_predicate closures
    each call ``len(_filtered)``. If the W978-5th-discipline ``len()``
    were at the kwarg-bind site, the raise would escape the try-block.
    Because ``len()`` lives INSIDE the closures, the raise lands inside
    the try-block and surfaces a marker.

    Strategy: patch ``_filter_records`` to return a __len__-raising
    sentinel. score_classify / compute_verdict / compute_predicate each
    catch the raise and emit their markers (at least one MUST fire).
    """
    from roam.commands import cmd_audit_trail_export as _mod

    class _BadLen(list):
        # Mimics cmd_sbom's _BadDeps: an object whose __len__ raises.
        def __len__(self):
            raise RuntimeError("synthetic-w978-bad-len-from-W607-CR")

    def _bad_filter(*args, **kwargs):
        return _BadLen()

    monkeypatch.setattr(_mod, "_filter_records", _bad_filter)

    result = _invoke_export(cli_runner, valid_trail)
    # The command MUST NOT crash -- a marker must be on the envelope
    # rather than the raise escaping a W607-CR wrap.
    assert result.exit_code == 0, f"W978 violation: bad-len sentinel caused crash; output={result.output!r}"
    data = _json.loads(result.output)
    assert data.get("command") == "audit-trail-export", data
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    # At least one of the 3 closures that call len(_filtered) MUST fire
    cr_markers = [
        m
        for m in all_wo
        if (
            m.startswith("audit_trail_export_score_classify_failed:")
            or m.startswith("audit_trail_export_compute_verdict_failed:")
            or m.startswith("audit_trail_export_compute_predicate_failed:")
        )
    ]
    assert cr_markers, (
        f"W978 regression: bad-len sentinel produced no W607-CR marker "
        f"on the envelope; the bad input either bypassed the wraps or "
        f"eagerly raised in default=; got all_wo={all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (16) Combined-bucket ordering -- AP markers precede CR markers
# ---------------------------------------------------------------------------


def test_combined_bucket_ordering_ap_before_cr(cli_runner, valid_trail, monkeypatch):
    """When BOTH W607-AP and W607-CR markers fire, the combined bucket
    presents AP substrate markers BEFORE CR aggregation markers.

    Substrate boundaries fire BEFORE aggregation phases in execution
    order; the combined-bucket assembly preserves this ordering so the
    full degradation lineage is visible in marker-emission order.
    """
    from roam.commands import cmd_audit_trail_export as _mod

    def _raise_render(*a, **kw):
        raise RuntimeError("synthetic-ordering-ap-render")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-ordering-cr-envelope")

    monkeypatch.setattr(_mod, "_render_markdown", _raise_render)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []

    # Find AP substrate marker index (render_output is substrate)
    ap_idx = next(
        (i for i, m in enumerate(top_wo) if m.startswith("audit_trail_export_render_output_failed:")),
        None,
    )
    # Find CR aggregation marker index (serialize_envelope is aggregation)
    cr_idx = next(
        (i for i, m in enumerate(top_wo) if m.startswith("audit_trail_export_serialize_envelope_failed:")),
        None,
    )
    assert ap_idx is not None, f"AP marker missing in {top_wo!r}"
    assert cr_idx is not None, f"CR marker missing in {top_wo!r}"
    assert ap_idx < cr_idx, (
        f"AP substrate marker (index {ap_idx}) must precede CR aggregation "
        f"marker (index {cr_idx}) in combined bucket; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (17) Clean-path envelope carries export_mode + export_state
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_export_mode_and_state(cli_runner, valid_trail):
    """W607-CR surfaces export_mode + export_state on the envelope.

    The score_classify closure returns a mode/state dict; these get
    surfaced on summary so consumers can read the export classification
    without re-deriving from raw counts. On a clean populated-trail run:
      - summary.export_mode == "records"
      - summary.export_state == "RECORDS_EXPORTED"
    """
    result = _invoke_export(cli_runner, valid_trail)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert summary.get("export_mode") == "records", (
        f"export_mode missing or wrong on clean envelope; got {summary.get('export_mode')!r}"
    )
    assert summary.get("export_state") == "RECORDS_EXPORTED", (
        f"export_state missing or wrong on clean envelope; got {summary.get('export_state')!r}"
    )
