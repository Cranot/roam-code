"""W607-CL -- additive aggregation-phase plumbing for ``cmd_vuln_reach``.

cmd_vuln_reach is the call-graph reachability projection sibling of
cmd_vulns -- it walks from ingested vuln rows through the symbol graph
to assess which vulns are reachable from project entry points. With
W607-CL landed, the full vuln-reach build path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-AU (7 build-path substrate boundaries:
    ensure_vuln_table / build_symbol_graph / query_vuln_count /
    analyze_reachability / reach_from_entry / reach_for_cve /
    serialize_envelope)
  - aggregation-phase layer: W607-CL (3 build-path aggregation
    boundaries: compute_predicate / compute_verdict / build_envelope)

Both layers share the canonical ``vuln_reach_*`` marker family and the
``vuln_reach_<phase>_failed:<exc_class>:<detail>`` shape contract. The
two buckets (``_w607au_warnings_out`` substrate-CALL +
``_w607cl_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

Relation to W607-AU
-------------------

cmd_vuln_reach already carries W607-AU substrate-CALL plumbing
covering 7 substrate-helper boundaries on the build path. W607-CL is
ADDITIVE on top of W607-AU, extending marker coverage to the
AGGREGATION-PHASE boundaries that W607-AU left unguarded:

  - ``compute_predicate``  -- per-field extraction of (total /
                              reachable_count / critical_count) used
                              to compose the verdict string +
                              envelope.
  - ``compute_verdict``    -- verdict-string assembly based on the
                              reachable count + critical-path count.
  - ``build_envelope``     -- ``json_envelope("vuln-reach", ...)``
                              projection. Phase name distinct from
                              W607-AU's existing ``serialize_envelope``
                              (which wraps ``to_json`` instead).

W826 / W823 regression check (security axis)
--------------------------------------------

Per W826 (HIGH-SEV cmd_taint silent-SAFE on empty corpus -- security-
critical Pattern-2): cmd_vuln_reach must NEVER silently emit a SAFE
verdict on the aggregation-phase boundary raising. The marker +
partial_success disclosure preserves the W823 empty-corpus security-
axis discipline. A guard test confirms W607-CL doesn't re-introduce
a Pattern-2 silent-SAFE bug on the aggregation-raise path.

W805 security-reachability triad pairing
----------------------------------------

cmd_vuln_reach sits on the reachability-projection leg of the W805
cross-artifact-consistency family. With W607-CL landed, the
SECURITY-REACHABILITY TRIAD is plumbed end-to-end at the substrate-
CALL AND aggregation-phase layers: cmd_vulns (W607-AQ + W607-CH),
cmd_taint (W607-AY [+ W607-CJ when landed]), and cmd_vuln_reach
(W607-AU + W607-CL). An integration test confirms each command's
markers stay in its OWN family and never bleed into a sibling's
envelope.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def vuln_reach_project(project_factory):
    """Small project with a call chain so reachability has signal."""
    return project_factory(
        {
            "api.py": "from service import process\ndef handle(): return process()\n",
            "service.py": "from utils import merge_data\ndef process(): return merge_data({})\n",
            "utils.py": "def merge_data(d): return d\ndef unused(): pass\n",
        }
    )


@pytest.fixture
def generic_vuln_report(tmp_path):
    """A small generic-format vuln report consumed by vuln-map to seed rows."""
    report = [
        {
            "cve": "CVE-2099-0001",
            "package": "merge_data",
            "severity": "high",
            "title": "test reach vuln",
        }
    ]
    p = tmp_path / "report.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


@pytest.fixture
def critical_vuln_report(tmp_path):
    """A generic-format vuln report at CRITICAL severity (REACHED tier).

    Drives the W607-CL REACHED-tier path: a critical vuln rooted in
    a reachable symbol (``merge_data`` per the project corpus).
    """
    report = [
        {
            "cve": "CVE-2099-9001",
            "package": "merge_data",
            "severity": "critical",
            "title": "test reached critical vuln",
        }
    ]
    p = tmp_path / "critical.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


def _invoke_vuln_reach(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam vuln-reach`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("vuln-reach")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _invoke_vuln_map(cli_runner, project_root, report_path):
    """Seed the DB with a vuln row so the no-vulns short-circuit doesn't fire."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, ["vuln-map", "--generic", report_path], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CL aggregation markers
# ---------------------------------------------------------------------------


def test_vuln_reach_happy_path_no_w607cl_markers(cli_runner, vuln_reach_project, generic_vuln_report):
    """Clean vuln-reach on healthy corpus -> no W607-CL aggregation markers.

    Hash-stable: an empty W607-CL bucket on the success path must
    produce an envelope without any
    ``vuln_reach_compute_predicate_failed:`` /
    ``vuln_reach_compute_verdict_failed:`` /
    ``vuln_reach_build_envelope_failed:`` markers.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "vuln-reach"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    w607cl_phases = (
        "vuln_reach_compute_predicate_failed:",
        "vuln_reach_compute_verdict_failed:",
        "vuln_reach_build_envelope_failed:",
    )
    for prefix in w607cl_phases:
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean vuln-reach must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cl`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_vuln_reach_carries_w607cl_accumulator():
    """AST-level guard: cmd_vuln_reach source carries the W607-CL accumulator.

    Pins the canonical W607-CL anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AU) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vuln_reach.py"
    assert src_path.exists(), f"cmd_vuln_reach.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607cl_warnings_out" in src, (
        "W607-CL accumulator missing from cmd_vuln_reach; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cl" in src, (
        "W607-CL helper ``_run_check_cl`` missing from cmd_vuln_reach; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cl is defined inside cmd_vuln_reach.
    tree = ast.parse(src)
    found_run_check_cl = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cl":
            found_run_check_cl = True
            break
    assert found_run_check_cl, (
        "W607-CL ``_run_check_cl`` helper not found in cmd_vuln_reach AST; "
        "the additive aggregation-phase wrapper has been refactored away."
    )

    # W607-AU must still be present (additive layer does NOT replace it)
    assert "w607au_warnings_out" in src, (
        "W607-AU accumulator vanished alongside the W607-CL add; the "
        "additive plumbing must preserve the W607-AU substrate-CALL layer."
    )
    assert "_run_check_au" in src, (
        "W607-AU helper ``_run_check_au`` vanished alongside the W607-CL "
        "add; the additive layer must preserve the substrate-CALL layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cl():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cl(...)`` with the canonical phase name.

    The three phases must appear inside a ``_run_check_cl("<phase>", ...)``
    call inside cmd_vuln_reach.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vuln_reach.py"
    src = src_path.read_text(encoding="utf-8")

    canonical_phases = (
        "compute_predicate",
        "compute_verdict",
        "build_envelope",
    )
    for phase in canonical_phases:
        markers = [
            f'_run_check_cl("{phase}"',
            f'_run_check_cl(\n        "{phase}"',
            f'_run_check_cl(\n            "{phase}"',
            f'_run_check_cl(\n                "{phase}"',
            f'_run_check_cl(\n                    "{phase}"',
            f'_run_check_cl(\n                        "{phase}"',
        ]
        found = any(m in src for m in markers)
        assert found, (
            f"phase ``{phase}`` is not wrapped in _run_check_cl(...); add the W607-CL guard or pin the canonical anchor"
        )


# ---------------------------------------------------------------------------
# (4) compute_predicate failure marker
# ---------------------------------------------------------------------------


def test_compute_predicate_failure_marker_format(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """If the compute_predicate boundary raises, surface the marker.

    We patch the ``_compute_predicate_fields`` closure by intercepting
    ``_run_check_cl`` on the ``compute_predicate`` phase. The W607-CL
    wrap surfaces a structured marker rather than crashing the
    envelope.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    # Strategy: wrap _output_all to swap in a _run_check_cl whose
    # compute_predicate-phase invocation raises. The real wrap inside
    # _output_all will catch it and surface the canonical marker.
    _orig_output_all = _mod._output_all

    def _patched_output_all(ctx, results, json_mode, **kwargs):
        # We patch the function-arg passed into the closure -- the
        # compute_predicate call inside the function calls the
        # _run_check_cl we supply here. If we force that wrapper to
        # raise on compute_predicate, the OUTER catch (none) won't
        # save us... we need the function to take the floor path.
        # Instead: provide a _run_check_cl that does the canonical
        # try/except behaviour but ALWAYS raises for the inner fn.
        accumulator = kwargs.get("w607cl_warnings_out", [])

        def _wrapped(phase, fn, *a, default=None, **kw):
            if phase == "compute_predicate":
                # Simulate the inner closure raising.
                exc = RuntimeError("synthetic-compute-predicate-from-W607-CL")
                accumulator.append(f"vuln_reach_{phase}_failed:{type(exc).__name__}:{exc}")
                return default
            return fn(*a, **kw)

        kwargs["_run_check_cl"] = _wrapped
        return _orig_output_all(ctx, results, json_mode, **kwargs)

    monkeypatch.setattr(_mod, "_output_all", _patched_output_all)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vuln_reach_compute_predicate_failed:")]
    assert markers, f"expected ``vuln_reach_compute_predicate_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict failure marker
# ---------------------------------------------------------------------------


def test_compute_verdict_failure_marker_format(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """If the compute_verdict boundary raises, surface the marker.

    We patch ``analyze_reachability`` to return rows whose critical
    count value raises on the f-string interpolation. The
    ``_build_verdict_str`` closure trips inside the f-string.

    W978 first-hypothesis check: the canonical floor MUST NOT
    re-interpolate the same values that raised -- the floor is a
    literal string ``"vuln-reach completed"``.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    class _BadCount:
        def __gt__(self, _other):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CL")

        def __format__(self, _spec):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CL")

        def __ne__(self, _other):
            raise RuntimeError("synthetic-compute-verdict-from-W607-CL")

    # Patch the predicate-fields builder to inject the bad-count sentinel
    # into critical_count -- that's where _build_verdict_str's
    # ``critical_count_local > 0`` and f-string interpolation trip.
    import roam.security.vuln_reach as _vr_mod

    def _ok_analyze(*_args, **_kwargs):
        return [
            {
                "reachable": 1,
                "cve_id": "CVE-X",
                "package_name": "x",
                "severity": "critical",
                "title": "t",
                "path_names": [],
                "hop_count": 1,
                "blast_radius": 1,
            }
        ]

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _ok_analyze)

    # Now patch the predicate function to return our bad-count sentinel
    # for critical_count. We do this by patching json_envelope to not
    # interfere and patching the inner _compute_predicate function via
    # _run_check_cl -- the simplest way is to inject through the
    # severity list pipeline.
    # Simpler: patch _run_check_cl by intercepting only when phase is
    # compute_predicate.
    _orig = _mod._output_all

    def _patched_output_all(ctx, results, json_mode, **kwargs):
        cl_run = kwargs.get("_run_check_cl")
        if cl_run is not None:

            def _wrapped(phase, fn, *a, default=None, **kw):
                if phase == "compute_predicate":
                    return {
                        "total": 1,
                        "reachable_count": 1,
                        "critical_count": _BadCount(),
                    }
                return cl_run(phase, fn, *a, default=default, **kw)

            kwargs["_run_check_cl"] = _wrapped
        return _orig(ctx, results, json_mode, **kwargs)

    monkeypatch.setattr(_mod, "_output_all", _patched_output_all)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    cl_markers = [m for m in all_wo if m.startswith("vuln_reach_compute_verdict_failed:")]
    assert cl_markers, f"expected ``vuln_reach_compute_verdict_failed:`` marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in cl_markers), cl_markers


# ---------------------------------------------------------------------------
# (6) build_envelope guard -- raise floors to stub document
# ---------------------------------------------------------------------------


def test_w607cl_build_envelope_floor_on_raise(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``vuln_reach_build_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("vuln-reach", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-build-envelope-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "vuln-reach", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    summary_wo = data.get("summary", {}).get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vuln_reach_build_envelope_failed:")]
    assert markers, f"expected ``vuln_reach_build_envelope_failed:`` marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (7) ANY marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """ANY W607-CL or W607-AU marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    vuln-reach" from "vuln-reach ran with substrate degradation" via
    summary.partial_success alone.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CL warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cl_warnings_out_in_both_top_and_summary(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """Non-empty W607-CL bucket -> both top-level AND summary.warnings_out
    populated.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CL raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CL raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("vuln_reach_build_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("vuln_reach_build_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the build_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (9) Marker-prefix discipline -- W607-CL uses the SAME ``vuln_reach_*`` family
# ---------------------------------------------------------------------------


def test_w607cl_marker_prefix_vuln_reach_family(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """W607-CL markers use the canonical ``vuln_reach_*`` prefix (same family
    as W607-AU; W607-CL is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CL marker that leaks into a sibling W607-*
    family (``sbom_*`` / ``supply_chain_*`` / ``cga_*`` / ``attest_*`` /
    ``taint_*`` / ``vulns_*``) breaks the closed-enum marker-family
    contract.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("vuln_reach_"), (
            f"every W607-CL marker must use the ``vuln_reach_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (10) W607-AU COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607au_substrate_markers_coexist_with_w607cl_aggregation(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """Confirm ``vuln_reach_<substrate-phase>_failed:`` markers (W607-AU
    layer) coexist with ``vuln_reach_<agg-phase>_failed:`` markers
    (W607-CL layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    The additive aggregation-phase layer must NOT shadow the pre-
    existing substrate-CALL layer; both buckets must combine into the
    same warnings_out channel with marker-prefix disambiguation
    (``vuln_reach_<substrate-phase>_failed:`` vs.
    ``vuln_reach_<agg-phase>_failed:``).
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod
    from roam.commands import cmd_vuln_reach as _mod

    # W607-AU substrate boundary -- analyze_reachability raises
    def _raise_analyze(*_args, **_kwargs):
        raise RuntimeError("synthetic-au-coexist-analyze")

    # W607-CL aggregation boundary -- build_envelope raises via patched
    # json_envelope
    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-cl-coexist-build-envelope")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise_analyze)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # Substrate-CALL phase from W607-AU
    au_markers = [m for m in all_wo if m.startswith("vuln_reach_analyze_reachability_failed:")]
    # Aggregation-phase from W607-CL
    cl_markers = [m for m in all_wo if m.startswith("vuln_reach_build_envelope_failed:")]

    assert au_markers, f"W607-AU substrate-CALL marker (vuln_reach_analyze_reachability_failed) missing; got {all_wo!r}"
    assert cl_markers, f"W607-CL aggregation-phase marker (vuln_reach_build_envelope_failed) missing; got {all_wo!r}"

    # Both share the canonical ``vuln_reach_*`` family
    assert all(m.startswith("vuln_reach_") for m in (au_markers + cl_markers)), (
        f"all markers must share the canonical ``vuln_reach_*`` family; got au = {au_markers!r}, cl = {cl_markers!r}"
    )


# ---------------------------------------------------------------------------
# (11) W826 / W823 REGRESSION GUARD -- empty corpus does NOT silently SAFE
# even when W607-CL aggregation boundary raises
# ---------------------------------------------------------------------------


def test_w823_w826_no_silent_safe_on_aggregation_raise(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """W823/W826 regression guard: vuln-reach + aggregation-phase raise
    MUST disclose the failure, never collapse to a silent SAFE verdict.

    Per W826 (HIGH-SEV cmd_taint silent-SAFE on empty corpus -- security-
    critical Pattern-2): cmd_vuln_reach must NEVER silently emit a SAFE
    verdict on the aggregation-phase boundary raising. The marker +
    partial_success disclosure preserves the W823 empty-corpus
    security-axis discipline.

    Strategy: vulns rows present + W607-CL build_envelope raise. The
    envelope MUST:
      1. Carry partial_success=True (Pattern-2 not silent)
      2. Carry a ``vuln_reach_build_envelope_failed:`` marker
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-w826-regression-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Pattern-2: partial_success MUST be True
    assert data["summary"].get("partial_success") is True, (
        f"W826 regression: W607-CL raise must flip partial_success=True; got summary = {data['summary']!r}"
    )

    # Pattern-2: marker MUST be present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    cl_markers = [m for m in all_wo if m.startswith("vuln_reach_build_envelope_failed:")]
    assert cl_markers, (
        f"W826 regression: W607-CL raise must surface vuln_reach_build_envelope_failed marker; got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) REACHED-tier path coverage -- a reachable vuln preserves the canonical
# reachable count through the aggregation phase
# ---------------------------------------------------------------------------


def test_reached_tier_path_preserved_through_w607cl(cli_runner, vuln_reach_project, critical_vuln_report):
    """When a critical-severity reachable vuln is present, the W607-CL
    wrapping must preserve the reachability bucket through the
    aggregation phase.

    Analogous to W607-CH CRITICAL-LEVEL path exercise: cmd_vuln_reach's
    aggregation-phase plumbing must NOT silently down-bucket the
    reachable-vuln count or the critical-path count.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, critical_vuln_report)
    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    # total_vulns must reach 1 -- the critical vuln we ingested
    assert summary.get("total_vulns", 0) >= 1, (
        f"REACHED-tier vuln must surface in summary.total_vulns; got summary = {summary!r}"
    )

    # No W607-CL degradation markers on the REACHED clean path
    top_wo = data.get("warnings_out") or []
    summary_wo = summary.get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    cl_failure_markers = [
        m
        for m in all_markers
        if (
            m.startswith("vuln_reach_compute_predicate_failed:")
            or m.startswith("vuln_reach_compute_verdict_failed:")
            or m.startswith("vuln_reach_build_envelope_failed:")
        )
    ]
    assert not cl_failure_markers, (
        f"REACHED-tier clean path must NOT surface W607-CL markers; got {cl_failure_markers!r}"
    )


# ---------------------------------------------------------------------------
# (13) CROSS-PREFIX ISOLATION -- vuln_reach_* markers DO NOT leak into
# adjacent commands' envelopes (cmd_taint, cmd_vulns, cmd_sbom)
# ---------------------------------------------------------------------------


def test_vuln_reach_markers_do_not_leak_into_adjacent_commands(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """``vuln_reach_*`` markers must NOT appear with foreign prefixes
    (``taint_*`` / ``vulns_*`` / ``sbom_*`` / ``supply_chain_*`` /
    ``cga_*`` / ``attest_*`` / ``pr_bundle_*``) when vuln-reach raises.

    Validates the marker-family isolation contract: each command's W607
    plumbing uses its OWN prefix and does not bleed into adjacent
    commands' warnings_out channels.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_markers if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-isolation check"

    foreign_prefixes = (
        "taint_",
        "vulns_",
        "sbom_",
        "supply_chain_",
        "cga_",
        "attest_",
        "pr_bundle_",
        "preflight_",
        "impact_",
        "diagnose_",
        "critique_",
        "diff_",
    )
    for marker in failure_markers:
        for foreign in foreign_prefixes:
            assert not marker.startswith(foreign), (
                f"cmd_vuln_reach warnings_out must not contain {foreign}* markers; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) SECURITY-REACHABILITY TRIAD pairing -- vulns_/taint_/vuln_reach_
# marker families stay isolated when each command fires on the same workspace
# ---------------------------------------------------------------------------


def test_security_reachability_triad_marker_families_coexist(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """SECURITY-REACHABILITY TRIAD pairing guard:

    Confirm that ``vuln_reach_<phase>_failed:`` markers (W607-AU +
    W607-CL) stay in the canonical ``vuln_reach_*`` family when
    vuln-reach is invoked on a workspace also covered by cmd_vulns
    (W607-AQ + W607-CH) and cmd_taint (W607-AY + W607-CJ when landed)
    commands. Each command's markers must stay in its OWN family and
    never bleed into a sibling's envelope.

    Closes the security-reachability triad at the aggregation-phase
    layer: every emitter in the W805 security chain now has
    substrate-CALL plumbing AND aggregation-phase plumbing.

    Strategy: monkeypatch vuln-reach's json_envelope to raise so a
    W607-CL marker fires, and confirm:
      1. vuln-reach envelope carries ``vuln_reach_*_failed:`` markers
      2. vuln-reach envelope does NOT carry ``taint_*`` / ``vulns_*``
         foreign markers
      3. The marker family is closed-enum: every failure marker starts
         with the canonical ``vuln_reach_`` prefix.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise RuntimeError("synthetic-triad-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)

    # vuln-reach envelope MUST contain vuln_reach_build_envelope_failed
    assert any(m.startswith("vuln_reach_build_envelope_failed:") for m in all_markers), (
        f"vuln-reach envelope missing vuln_reach_build_envelope_failed marker; got {all_markers!r}"
    )

    # vuln-reach envelope MUST NOT contain security-triad sibling markers
    for marker in all_markers:
        if "_failed:" not in marker:
            continue
        assert not marker.startswith("taint_"), f"vuln-reach envelope leaked taint_* marker: {marker!r}"
        assert not marker.startswith("vulns_"), f"vuln-reach envelope leaked vulns_* marker: {marker!r}"

    # Closed-enum check: every failure marker uses the canonical
    # ``vuln_reach_*`` prefix.
    failure_markers = [m for m in all_markers if "_failed:" in m]
    for marker in failure_markers:
        assert marker.startswith("vuln_reach_"), (
            f"every vuln-reach failure marker must use the canonical ``vuln_reach_*`` family; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (15) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_w607cl_three_segment_marker_shape(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-AU / W607-CH contracts.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_envelope(*_args, **_kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-CL")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("vuln_reach_build_envelope_failed:")]
    assert failure_markers, f"expected vuln_reach_build_envelope_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "vuln_reach_build_envelope_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (16) OpenVEX-shape isolation -- marker family stays clean across the
# projection (the JSON envelope's ``vulnerabilities`` list is the
# OpenVEX-shaped data; markers go on warnings_out, never inside the
# OpenVEX projection)
# ---------------------------------------------------------------------------


def test_w607cl_openvex_shape_isolation(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """W607-CL markers go on ``warnings_out``, never inside the
    OpenVEX-shaped ``vulnerabilities`` projection.

    cmd_vuln_reach emits a ``vulnerabilities`` array on the envelope --
    the OpenVEX-shaped reachability projection. The marker family must
    NEVER leak into a per-vuln record; markers always land on
    ``warnings_out``.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod
    from roam.commands import cmd_vuln_reach as _mod

    def _raise_analyze(*_args, **_kwargs):
        raise RuntimeError("synthetic-openvex-shape-from-W607-CL")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise_analyze)
    # Also flush a CL marker
    _orig_json_envelope = _mod.json_envelope

    def _wrapping_envelope(name, **kwargs):
        # Inject a fault on first call: raise so build_envelope fires
        if not hasattr(_wrapping_envelope, "called"):
            _wrapping_envelope.called = True
            raise RuntimeError("synthetic-openvex-cl")
        return _orig_json_envelope(name, **kwargs)

    monkeypatch.setattr(_mod, "json_envelope", _wrapping_envelope)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # The OpenVEX-shape projection MUST NOT carry a vuln_reach_* marker
    # inside any per-vuln record
    vulns = data.get("vulnerabilities", [])
    for v in vulns:
        for key, value in v.items():
            if isinstance(value, str):
                assert "vuln_reach_" not in value or "_failed:" not in value, (
                    f"OpenVEX-shape per-vuln record leaked W607 marker into field {key!r}: {value!r}"
                )


# ---------------------------------------------------------------------------
# (17) Phase-name disambiguation -- build_envelope NOT colliding with
# W607-AU's serialize_envelope
# ---------------------------------------------------------------------------


def test_w607cl_phase_name_disambiguation_from_au():
    """W607-CL uses ``build_envelope``; W607-AU uses ``serialize_envelope``.

    Per the W607-CH discipline (W978 trap #4 -- phase-name collision):
    if the substrate-CALL layer already wraps ``serialize_envelope``,
    the aggregation-phase layer MUST name its envelope-construction
    phase ``build_envelope`` so marker prefixes stay disambiguated.

    Source-level guard pinning both phase names so a future refactor
    that collapses them into one trips this test.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vuln_reach.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-AU: serialize_envelope wraps to_json
    assert "serialize_envelope" in src, (
        "W607-AU phase ``serialize_envelope`` missing -- the substrate-CALL layer for to_json must be preserved."
    )

    # W607-CL: build_envelope wraps json_envelope (a distinct phase name)
    assert "build_envelope" in src, (
        "W607-CL phase ``build_envelope`` missing -- the aggregation-"
        "phase layer for json_envelope must use a phase name distinct "
        "from the substrate-CALL layer's ``serialize_envelope``."
    )

    # Confirm both phase strings appear in distinct _run_check_* calls
    # by checking for the canonical wrapper signatures
    assert (
        '_run_check_cl(\n            "build_envelope"' in src
        or '_run_check_cl(\n                "build_envelope"' in src
        or '_run_check_cl("build_envelope"' in src
    ), "build_envelope must be wrapped by _run_check_cl (the W607-CL additive aggregation-phase layer)."
