"""W607-AU -- ``cmd_vuln_reach`` substrate-boundary plumbing.

Thirty-fifth-in-batch W607 consumer-layer arc. Fresh-plumbing wave:
cmd_vuln_reach had NO pre-existing ``warnings_out`` channel and NO
``_run_check`` / substrate-CALL marker plumbing -- so the canonical
fresh template applies (one accumulator + one ``_run_check_au``
helper) with the marker prefix ``vuln_reach_*`` outright on a
brand-new ``summary.warnings_out`` field.

cmd_vuln_reach is the call-graph reachability projection sibling of
cmd_vulns. It walks from vuln rows through the symbol graph to assess
which vulns are runtime-reachable from project entry points. The
substrate boundaries it touches are: ``ensure_vuln_table`` (DB schema
bootstrap), ``build_symbol_graph`` (graph build for traversal),
``query_vuln_count`` (inventory probe), ``analyze_reachability`` (the
core reachability compute), ``reach_from_entry`` (entry-anchored reach
projection), ``reach_for_cve`` (CVE-anchored reach projection), and
``serialize_envelope`` (on-text JSON serialization). Prior to W607-AU
a raise in any of these crashed the whole vuln-reach invocation
wholesale.

W805 cross-artifact consistency family
--------------------------------------

cmd_vuln_reach sits on the reachability-projection leg of the W805
cross-artifact-consistency family. With W607-AK (supply_chain) +
W607-AM (sbom) + W607-AQ (vulns) + W607-AU (vuln_reach), the
supply-chain substrate is W607-plumbed end-to-end across {ingest,
normalize, reach, sbom-build, vex-build, sign, write, verify, project}.

W978 first-hypothesis check
---------------------------

Each W607-AU-wrapped substrate has a documented empty-floor default
matching its happy-path return shape so a raise degrades cleanly.
Dominant raise axes are: graph build refusal
(``build_symbol_graph`` on corrupt index), DB read refusal
(``query_vuln_count`` on stale schema), and traversal refusal
(``analyze_reachability`` / ``reach_from_entry`` / ``reach_for_cve``
on cycle pathologies or missing nodes).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate
helpers are imported inside ``vuln_reach`` at the call-site, so
patches go via ``monkeypatch.setattr`` on the
``roam.commands.cmd_vuln_reach`` module after the import-time
re-bind (when the test calls the click command, the closure resolves
the patched names because they're looked up at call-time on the
imported module).

Marker prefix discipline
------------------------

Marker family is ``vuln_reach_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers (``vulns_*``, ``sbom_*``,
``supply_chain_*``, ``cga_*``, ``attest_*``, etc.) preserved by the
prefix-discipline test.

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
    """Small project with a call chain so the reachability graph has signal."""
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
# (1) Happy path -- envelope omits W607-AU substrate-CALL markers
# ---------------------------------------------------------------------------


def test_vuln_reach_clean_envelope_omits_w607au_markers(cli_runner, vuln_reach_project, generic_vuln_report):
    """Clean vuln-reach -> no W607-AU substrate markers.

    Byte-identical-on-happy-path: an empty W607-AU bucket on the success
    path must NOT introduce ``vuln_reach_<phase>_failed:`` markers on
    the envelope. cmd_vuln_reach has no pre-existing warnings_out
    channel, so the field is absent entirely on the clean path.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "vuln-reach"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    au_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("vuln_reach_")]
    assert not au_markers, (
        f"clean vuln-reach must NOT surface W607-AU substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) analyze_reachability failure -> structured marker + degraded envelope
# ---------------------------------------------------------------------------


def test_vuln_reach_analyze_reachability_failure_marker(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """If ``analyze_reachability`` raises, surface the W607-AU marker.

    The traversal is the core reachability-compute substrate -- a raise
    here previously crashed the whole vuln-reach invocation. W607-AU
    surfaces it as a structured
    ``vuln_reach_analyze_reachability_failed:<exc>:<detail>`` marker
    and emits a structured degraded envelope (zero vulns) rather than
    crashing.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod
    from roam.commands import cmd_vuln_reach as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-analyze-from-W607-AU")

    # Patch on the source module -- the cmd_vuln_reach helper imports the
    # names inside the click handler, so we patch the upstream module.
    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)
    monkeypatch.setattr(_mod, "vuln_reach", _mod.vuln_reach)  # defensive no-op

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("vuln_reach_analyze_reachability_failed:")]
    assert markers, f"expected vuln_reach_analyze_reachability_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-analyze-from-W607-AU" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"analyze-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_vuln_reach_w607au_warnings_in_envelope(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """Non-empty W607-AU bucket -> both top-level AND summary.warnings_out."""
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AU disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AU disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("vuln_reach_analyze_reachability_failed:")]
    assert markers, f"expected vuln_reach_analyze_reachability_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) partial_success flips when W607-AU substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607au_helper_raises(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """Any non-empty W607-AU bucket -> summary.partial_success = True."""
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AU warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AQ contracts.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("vuln_reach_analyze_reachability_failed:")]
    assert failure_markers, f"expected vuln_reach_analyze_reachability_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "vuln_reach_analyze_reachability_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) build_symbol_graph failure -> structured marker
# ---------------------------------------------------------------------------


def test_vuln_reach_build_symbol_graph_failure_marker(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """If ``build_symbol_graph`` raises, surface a marker.

    Graph build is the upstream substrate for every reachability
    branch. Ensures the W607-AU disclosure covers the traversal
    bootstrap leg.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.graph.builder as _builder_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-graph-from-W607-AU")

    monkeypatch.setattr(_builder_mod, "build_symbol_graph", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("vuln_reach_build_symbol_graph_failed:")]
    assert markers, f"expected vuln_reach_build_symbol_graph_failed: marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-AU stays in ``vuln_reach_*`` family
# ---------------------------------------------------------------------------


def test_w607au_marker_prefix_stays_in_vuln_reach_family(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """Every W607-AU substrate marker uses the canonical ``vuln_reach_*`` prefix.

    cmd_vuln_reach is the call-graph reachability projection substrate
    -- distinct from sibling W607-* layers. Marker prefix MUST stay
    ``vuln_reach_*`` and MUST NOT leak into other family prefixes
    (``vulns_*``, ``sbom_*``, ``supply_chain_*``, ``cga_*``, ``attest_*``,
    etc.).
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("vuln_reach_"), (
            f"every surfaced W607-AU marker must use the ``vuln_reach_*`` "
            f"prefix family (cmd_vuln_reach scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers. Note: ``vulns_``
        # is intentionally NOT in this list because ``vuln_reach_`` is
        # a substring-superset prefix; the startswith("vuln_reach_")
        # check above already pins the family.
        for forbidden_prefix, sibling in (
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("impact_", "cmd_impact W607-T"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("preflight_", "cmd_preflight W607-R"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_vuln_reach carries the W607-AU accumulator
# ---------------------------------------------------------------------------


def test_cmd_vuln_reach_carries_w607au_accumulator():
    """AST-level guard: cmd_vuln_reach source carries the W607-AU accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AU instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vuln_reach.py"
    assert src_path.exists(), f"cmd_vuln_reach.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607au_warnings_out" in src, (
        "W607-AU accumulator missing from cmd_vuln_reach; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_au" in src, (
        "W607-AU ``_run_check_au`` helper missing from cmd_vuln_reach; the "
        "per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_au is defined inside cmd_vuln_reach.
    tree = ast.parse(src)
    found_run_check_au = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_au":
            found_run_check_au = True
            break
    assert found_run_check_au, (
        "W607-AU ``_run_check_au`` helper not found in cmd_vuln_reach AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-AU substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607au_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AU substrate boundary is wrapped.

    W607-AU substrate inventory (cmd_vuln_reach):

    * ensure_vuln_table          -- DB schema bootstrap
    * build_symbol_graph         -- graph build for reachability traversal
    * query_vuln_count           -- DB inventory probe
    * analyze_reachability       -- all-vulns reachability compute
    * reach_from_entry           -- entry-anchored reach projection
    * reach_for_cve              -- CVE-anchored reach projection
    * serialize_envelope         -- on-text JSON serialization

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts
    multiple indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_vuln_reach.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "ensure_vuln_table",
        "build_symbol_graph",
        "query_vuln_count",
        "analyze_reachability",
        "reach_from_entry",
        "reach_for_cve",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_au("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_au(\n        "{phase}"' in src
            or f'_run_check_au(\n            "{phase}"' in src
            or f'_run_check_au(\n                "{phase}"' in src
            or f'_run_check_au(\n                    "{phase}"' in src
            or f'_run_check_au(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AU _run_check_au wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) Reachability-classification disclosure: analyze_reachability raise
#      surfaces the marker AND emits a verdict-bearing envelope
# ---------------------------------------------------------------------------


def test_w607au_reachability_compute_raise_disclosure(cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch):
    """Reachability classification disclosure -- W607-AU bonus check.

    When ``analyze_reachability`` raises, the envelope MUST still emit
    a verdict-bearing summary with classified counts at zero (no
    silent crash). This is the W805 Pattern-1 variant-D check:
    degraded reach-classify outcome surfaces explicitly via the
    warnings_out marker, NOT a silent SAFE verdict.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)
    import roam.security.vuln_reach as _vr_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-classify-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise)

    result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Envelope still emits a verdict-bearing summary.
    assert "summary" in data, data
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, data["summary"]

    # Classified counts are zero on the degraded path (default fallback).
    assert data["summary"].get("reachable_count") == 0, data["summary"]
    assert data["summary"].get("critical_count") == 0, data["summary"]
    assert data["summary"].get("total_vulns") == 0, data["summary"]

    # Marker is present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("vuln_reach_analyze_reachability_failed:")]
    assert markers, f"expected vuln_reach_analyze_reachability_failed: marker on reach-classify raise; got {all_wo!r}"

    # partial_success flips so consumers can branch on degradation.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (11) SBOM / VEX / supply-chain / reach quad pairing -- W607-AU coexists
#      with W607-AQ (vulns), W607-AM (sbom), and W607-AK (supply_chain)
# ---------------------------------------------------------------------------


def test_w607au_and_w607aq_and_w607am_and_w607ak_markers_coexist(
    cli_runner, vuln_reach_project, generic_vuln_report, monkeypatch
):
    """SBOM/VEX/supply-chain/reach QUAD closure bonus.

    cmd_vuln_reach, cmd_vulns, cmd_sbom, and cmd_supply_chain all run on
    the same corpus and surface their respective markers without prefix
    collision. The marker families stay distinct:
    cmd_vuln_reach -> ``vuln_reach_*``, cmd_vulns -> ``vulns_*``,
    cmd_sbom -> ``sbom_*``, cmd_supply_chain -> ``supply_chain_*``.
    No mixing.
    """
    _invoke_vuln_map(cli_runner, vuln_reach_project, generic_vuln_report)

    from unittest import mock

    import roam.security.vuln_reach as _vr_mod
    from roam.commands import cmd_vulns as _cmd_vulns_mod
    from roam.commands.cmd_sbom import sbom
    from roam.commands.cmd_supply_chain import supply_chain

    # 1) cmd_vuln_reach -> vuln_reach_* family
    def _raise_reach(*args, **kwargs):
        raise RuntimeError("synthetic-quad-reach-from-W607-AU")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise_reach)

    reach_result = _invoke_vuln_reach(cli_runner, vuln_reach_project)
    assert reach_result.exit_code == 0, reach_result.output
    reach_data = _json.loads(reach_result.output)
    reach_wo = list(reach_data.get("warnings_out") or []) + list(reach_data["summary"].get("warnings_out") or [])
    reach_markers = [m for m in reach_wo if m.startswith("vuln_reach_")]
    assert reach_markers, f"expected vuln_reach_* marker; got {reach_wo!r}"
    # vuln_reach_* must NOT leak into sibling families.
    assert not any(m.startswith("sbom_") for m in reach_wo), (
        f"vuln-reach output must NOT leak sbom_* prefix; got {reach_wo!r}"
    )
    assert not any(m.startswith("supply_chain_") for m in reach_wo), (
        f"vuln-reach output must NOT leak supply_chain_* prefix; got {reach_wo!r}"
    )

    # Restore so cmd_vulns can run cleanly, then raise inside _query_vulns.
    monkeypatch.undo()

    # 2) cmd_vulns -> vulns_* family (raise inside _query_vulns)
    def _raise_vulns(*args, **kwargs):
        raise RuntimeError("synthetic-quad-vulns-from-W607-AU")

    monkeypatch.setattr(_cmd_vulns_mod, "_query_vulns", _raise_vulns)

    from roam.cli import cli as _cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(vuln_reach_project))
        vulns_result = cli_runner.invoke(_cli, ["--json", "vulns"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert vulns_result.exit_code == 0, vulns_result.output
    vulns_data = _json.loads(vulns_result.output)
    vulns_wo = list(vulns_data.get("warnings_out") or []) + list(vulns_data["summary"].get("warnings_out") or [])
    vulns_markers = [m for m in vulns_wo if m.startswith("vulns_")]
    assert vulns_markers, f"expected vulns_* marker; got {vulns_wo!r}"
    # No leakage of vuln_reach_ / sbom_ / supply_chain_ in vulns output.
    assert not any(m.startswith("vuln_reach_") for m in vulns_wo), (
        f"vulns output must NOT leak vuln_reach_* prefix; got {vulns_wo!r}"
    )

    # 3) cmd_sbom -> sbom_* family (raise inside discover_and_parse)
    monkeypatch.undo()

    def _raise_sbom(*args, **kwargs):
        raise RuntimeError("synthetic-quad-sbom-from-W607-AU")

    monkeypatch.setattr("roam.commands.cmd_supply_chain.discover_and_parse", _raise_sbom)

    sbom_obj = {"json": True, "sarif": False, "budget": 0}
    with mock.patch(
        "roam.commands.cmd_sbom.find_project_root",
        return_value=Path(str(vuln_reach_project)),
    ):
        sbom_result = cli_runner.invoke(sbom, ["--no-reachability"], obj=sbom_obj)
    assert sbom_result.exit_code == 0, sbom_result.output
    sbom_data = _json.loads(sbom_result.output)
    sbom_wo = list(sbom_data.get("warnings_out") or []) + list(sbom_data["summary"].get("warnings_out") or [])
    sbom_markers = [m for m in sbom_wo if m.startswith("sbom_")]
    assert sbom_markers, f"expected sbom_* marker; got {sbom_wo!r}"
    assert not any(m.startswith("vuln_reach_") for m in sbom_wo), (
        f"sbom output must NOT leak vuln_reach_* prefix; got {sbom_wo!r}"
    )

    # 4) cmd_supply_chain -> supply_chain_* family
    sc_obj = {"json": True, "sarif": False, "budget": 0}
    with mock.patch(
        "roam.commands.cmd_supply_chain.find_project_root",
        return_value=Path(str(vuln_reach_project)),
    ):
        sc_result = cli_runner.invoke(supply_chain, [], obj=sc_obj)
    assert sc_result.exit_code == 0, sc_result.output
    sc_data = _json.loads(sc_result.output)
    sc_wo = list(sc_data.get("warnings_out") or []) + list(sc_data["summary"].get("warnings_out") or [])
    sc_markers = [m for m in sc_wo if m.startswith("supply_chain_")]
    assert sc_markers, f"expected supply_chain_* marker; got {sc_wo!r}"
    assert not any(m.startswith("vuln_reach_") for m in sc_wo), (
        f"supply_chain output must NOT leak vuln_reach_* prefix; got {sc_wo!r}"
    )
    assert not any(m.startswith("sbom_") for m in sc_wo), (
        f"supply_chain output must NOT leak sbom_* prefix; got {sc_wo!r}"
    )
