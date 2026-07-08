"""W607-AY -- ``cmd_taint`` substrate-boundary plumbing.

cmd_taint is the dataflow-reach leg of the security-reachability triad
(cmd_vuln_reach W607-AU is the call-graph reachability sibling,
cmd_supply_chain W607-AK is the supply-chain projection sibling). It
walks source -> sink graph traversal patterns via BFS over the indexed
edges, sharing entry-enumeration + reachability-classify substrate
shape with vuln_reach. Prior to W607-AY a raise inside any substrate
helper (rule load, corpus probe, BFS propagation, flow-shape
classifier, registry write, confidence classifier, SARIF projection,
or JSON serialize) crashed the whole taint invocation.

W607-AY is FRESH plumbing: cmd_taint had NO pre-existing warnings_out
channel and NO ``_run_check`` / substrate-CALL marker wiring. The
accumulator-based markers become the canonical
``summary.warnings_out`` field outright with marker prefix
``taint_<phase>_failed:<exc_class>:<detail>``.

W978 first-hypothesis check
---------------------------

Each W607-AY-wrapped substrate has a documented empty-floor default
matching its happy-path return shape so a raise degrades cleanly.
Dominant raise axes: rule-load refusal (corrupt YAML in rules pack),
BFS refusal (cycle pathologies / missing edges), classifier refusal
(unexpected finding shape), and serializer refusal (circular ref or
non-JSON-encodable field).

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrate helpers are
imported at module top in cmd_taint, so tests patch them via
``monkeypatch.setattr`` on the ``roam.commands.cmd_taint`` module
(re-bound names) OR on the upstream source module
(``roam.security.taint_engine`` etc.) depending on the boundary.

W493 / W499 / W512 edge-kind discipline
---------------------------------------

cmd_taint's classifier walks adjacent path-pair edges via
``call_or_ref_in_clause()`` -- the W512 consolidation that replaced
the historical ``kind = 'calls'`` typo audited in W493/W499. The
guard test below pins this so a regression toward bare ``calls``
would surface.

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
def taint_project(project_factory):
    """Small Python project with a source -> sink reach pattern.

    Mirrors the dogfood SQLi shape: an HTTP-style input bound to a
    cursor.execute() call. The bundled python-sqli rule pack will
    detect this path.
    """
    return project_factory(
        {
            "app.py": (
                "import flask\n"
                "from db import run_query\n"
                "def handler():\n"
                "    user = flask.request.args.get('x')\n"
                "    return run_query(user)\n"
            ),
            "db.py": (
                "import sqlite3\n"
                "def run_query(q):\n"
                "    conn = sqlite3.connect('x.db')\n"
                "    return conn.execute(q).fetchall()\n"
            ),
        }
    )


def _invoke_taint(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam taint`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("taint")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-AY substrate-CALL markers
# ---------------------------------------------------------------------------


def test_taint_clean_envelope_omits_w607ay_markers(cli_runner, taint_project):
    """Clean taint -> no W607-AY substrate markers.

    Byte-identical-on-happy-path: an empty W607-AY bucket on the success
    path must NOT introduce ``taint_<phase>_failed:`` markers on the
    envelope. cmd_taint has no pre-existing warnings_out channel, so the
    field is absent entirely on the clean path.
    """
    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "taint"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ay_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("taint_")]
    assert not ay_markers, (
        f"clean taint must NOT surface W607-AY substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) run_taint failure -> structured marker + degraded envelope
# ---------------------------------------------------------------------------


def test_taint_run_taint_failure_marker(cli_runner, taint_project, monkeypatch):
    """If ``run_taint`` raises, surface the W607-AY marker.

    The BFS source->sink propagation is the critical correctness
    boundary (W493/W499/W512 audit history) -- a raise here previously
    crashed the whole taint invocation. W607-AY surfaces it as a
    structured ``taint_run_taint_failed:<exc>:<detail>`` marker and
    emits a degraded envelope (zero findings) rather than crashing.
    """
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-run-taint-from-W607-AY")

    # cmd_taint re-binds ``run_taint`` at module import (line ~40), so
    # patch on the cmd_taint module -- the click handler looks up the
    # re-bound name at call-time.
    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("taint_run_taint_failed:")]
    assert markers, f"expected taint_run_taint_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-run-taint-from-W607-AY" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"run_taint-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_taint_w607ay_warnings_in_envelope(cli_runner, taint_project, monkeypatch):
    """Non-empty W607-AY bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-AY")

    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-AY disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-AY disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("taint_run_taint_failed:")]
    assert markers, f"expected taint_run_taint_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) partial_success flips when W607-AY substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607ay_helper_raises(cli_runner, taint_project, monkeypatch):
    """Any non-empty W607-AY bucket -> summary.partial_success = True."""
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-AY")

    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-AY warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, taint_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AU contracts.
    """
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-AY")

    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("taint_run_taint_failed:")]
    assert failure_markers, f"expected taint_run_taint_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "taint_run_taint_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) capture_qualified_only_lint failure -> structured marker
# ---------------------------------------------------------------------------


def test_taint_capture_qualified_only_lint_failure_marker(cli_runner, taint_project, monkeypatch):
    """If ``capture_qualified_only_lint`` raises, surface a marker.

    Rule load is the upstream substrate -- a raise here used to crash
    every taint invocation before any of the dataflow plumbing could
    run. The W607-AY wrap surfaces it via the no-rules envelope branch.
    """
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-rules-load-from-W607-AY")

    # cmd_taint aliases capture_qualified_only_lint to
    # _w489_a_capture_qualified_only_lint -- patch that.
    monkeypatch.setattr(_mod, "_w489_a_capture_qualified_only_lint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("taint_capture_qualified_only_lint_failed:")]
    assert markers, f"expected taint_capture_qualified_only_lint_failed: marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-AY stays in ``taint_*`` family
# ---------------------------------------------------------------------------


def test_w607ay_marker_prefix_stays_in_taint_family(cli_runner, taint_project, monkeypatch):
    """Every W607-AY substrate marker uses the canonical ``taint_*`` prefix.

    cmd_taint is the dataflow-reach substrate -- distinct from sibling
    W607-* layers. Marker prefix MUST stay ``taint_*`` and MUST NOT
    leak into other family prefixes.
    """
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-AY")

    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("taint_"), (
            f"every surfaced W607-AY marker must use the ``taint_*`` prefix family (cmd_taint scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers. Note: ``taint_``
        # is unique to cmd_taint -- vuln_reach_*, vulns_*, sbom_*,
        # supply_chain_*, etc. must NOT appear.
        for forbidden_prefix, sibling in (
            ("vuln_reach_", "cmd_vuln_reach W607-AU"),
            ("vulns_", "cmd_vulns W607-AQ"),
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
# (8) Source-level guard: cmd_taint carries the W607-AY accumulator
# ---------------------------------------------------------------------------


def test_cmd_taint_carries_w607ay_accumulator():
    """AST-level guard: cmd_taint source carries the W607-AY accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-AY instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    assert src_path.exists(), f"cmd_taint.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ay_warnings_out" in src, (
        "W607-AY accumulator missing from cmd_taint; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ay" in src, (
        "W607-AY ``_run_check_ay`` helper missing from cmd_taint; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_ay is defined inside cmd_taint.
    tree = ast.parse(src)
    found_run_check_ay = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ay":
            found_run_check_ay = True
            break
    assert found_run_check_ay, (
        "W607-AY ``_run_check_ay`` helper not found in cmd_taint AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-AY substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ay_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-AY substrate boundary is wrapped.

    W607-AY substrate inventory (cmd_taint):

    * capture_qualified_only_lint -- rule load + W454/W479 lint capture
    * query_symbol_count          -- corpus-empty probe
    * run_taint                   -- BFS source->sink propagation
                                     (W493/W499/W512 critical)
    * build_emit_entries          -- flow-shape classifier
                                     (forward_bfs vs co_call)
    * emit_findings               -- registry write
    * wrap_findings               -- confidence classifier
    * taint_to_sarif              -- SARIF projection
    * write_sarif                 -- SARIF text render
    * serialize_envelope          -- on-text JSON serialization

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts
    multiple indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "capture_qualified_only_lint",
        "query_symbol_count",
        "run_taint",
        "build_emit_entries",
        "emit_findings",
        "wrap_findings",
        "taint_to_sarif",
        "write_sarif",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ay("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_ay(\n        "{phase}"' in src
            or f'_run_check_ay(\n            "{phase}"' in src
            or f'_run_check_ay(\n                "{phase}"' in src
            or f'_run_check_ay(\n                    "{phase}"' in src
            or f'_run_check_ay(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-AY _run_check_ay wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) Dataflow-classification disclosure: run_taint raise surfaces marker
#      AND emits a verdict-bearing envelope (Pattern-1 variant-D check)
# ---------------------------------------------------------------------------


def test_w607ay_dataflow_compute_raise_disclosure(cli_runner, taint_project, monkeypatch):
    """Dataflow classification disclosure -- W607-AY bonus check.

    When ``run_taint`` raises, the envelope MUST still emit a
    verdict-bearing summary with classified counts at zero (no silent
    crash). This is the W805 Pattern-1 variant-D check: degraded
    dataflow-classify outcome surfaces explicitly via the warnings_out
    marker, NOT a silent SAFE verdict.
    """
    from roam.commands import cmd_taint as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-dataflow-from-W607-AY")

    monkeypatch.setattr(_mod, "run_taint", _raise)

    result = _invoke_taint(cli_runner, taint_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Envelope still emits a verdict-bearing summary.
    assert "summary" in data, data
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, data["summary"]

    # Classified counts are zero on the degraded path (default fallback).
    assert data["summary"].get("findings") == 0, data["summary"]
    assert data["summary"].get("errors") == 0, data["summary"]
    assert data["summary"].get("warnings") == 0, data["summary"]

    # Marker is present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("taint_run_taint_failed:")]
    assert markers, f"expected taint_run_taint_failed: marker on dataflow-classify raise; got {all_wo!r}"

    # partial_success flips so consumers can branch on degradation.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (11) SECURITY-REACHABILITY TRIAD pairing: W607-AY coexists with
#      W607-AU (vuln_reach) + W607-AQ (vulns) on the same corpus
# ---------------------------------------------------------------------------


def test_w607ay_and_w607au_and_w607aq_markers_coexist(cli_runner, taint_project, tmp_path, monkeypatch):
    """SECURITY-REACHABILITY TRIAD closure bonus.

    cmd_taint, cmd_vuln_reach, and cmd_vulns all run on the same
    corpus and surface their respective markers without prefix
    collision. The marker families stay distinct:
    cmd_taint -> ``taint_*``, cmd_vuln_reach -> ``vuln_reach_*``,
    cmd_vulns -> ``vulns_*``. No mixing.
    """
    # 1) cmd_taint -> taint_* family
    from roam.commands import cmd_taint as _taint_mod

    def _raise_taint(*args, **kwargs):
        raise RuntimeError("synthetic-triad-taint-from-W607-AY")

    monkeypatch.setattr(_taint_mod, "run_taint", _raise_taint)

    taint_result = _invoke_taint(cli_runner, taint_project)
    assert taint_result.exit_code == 0, taint_result.output
    taint_data = _json.loads(taint_result.output)
    taint_wo = list(taint_data.get("warnings_out") or []) + list(taint_data["summary"].get("warnings_out") or [])
    taint_markers = [m for m in taint_wo if m.startswith("taint_")]
    assert taint_markers, f"expected taint_* marker; got {taint_wo!r}"
    # taint_* must NOT leak into sibling families.
    assert not any(m.startswith("vuln_reach_") for m in taint_wo), (
        f"taint output must NOT leak vuln_reach_* prefix; got {taint_wo!r}"
    )
    assert not any(m.startswith("vulns_") for m in taint_wo), (
        f"taint output must NOT leak vulns_* prefix; got {taint_wo!r}"
    )

    # Restore so cmd_vuln_reach can run cleanly, then raise inside it.
    monkeypatch.undo()

    # 2) cmd_vuln_reach -> vuln_reach_* family (raise in analyze_reachability)
    import roam.security.vuln_reach as _vr_mod

    # First seed a vuln row so vuln-reach doesn't short-circuit
    report = [
        {
            "cve": "CVE-2099-0001",
            "package": "run_query",
            "severity": "high",
            "title": "test reach vuln",
        }
    ]
    report_path = tmp_path / "report.json"
    report_path.write_text(_json.dumps(report), encoding="utf-8")

    from roam.cli import cli as _cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(taint_project))
        # Seed via vuln-map first so vuln-reach has rows to analyze.
        cli_runner.invoke(
            _cli,
            ["vuln-map", "--generic", str(report_path)],
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)

    def _raise_vr(*args, **kwargs):
        raise RuntimeError("synthetic-triad-vr-from-W607-AY")

    monkeypatch.setattr(_vr_mod, "analyze_reachability", _raise_vr)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(taint_project))
        vr_result = cli_runner.invoke(_cli, ["--json", "vuln-reach"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert vr_result.exit_code == 0, vr_result.output
    vr_data = _json.loads(vr_result.output)
    vr_wo = list(vr_data.get("warnings_out") or []) + list(vr_data["summary"].get("warnings_out") or [])
    vr_markers = [m for m in vr_wo if m.startswith("vuln_reach_")]
    assert vr_markers, f"expected vuln_reach_* marker; got {vr_wo!r}"
    # No leakage of taint_ or vulns_ in vuln-reach output.
    assert not any(m.startswith("taint_") for m in vr_wo), (
        f"vuln-reach output must NOT leak taint_* prefix; got {vr_wo!r}"
    )

    # 3) cmd_vulns -> vulns_* family (raise inside _query_vulns)
    monkeypatch.undo()
    from roam.commands import cmd_vulns as _cmd_vulns_mod

    def _raise_vulns(*args, **kwargs):
        raise RuntimeError("synthetic-triad-vulns-from-W607-AY")

    monkeypatch.setattr(_cmd_vulns_mod, "_query_vulns", _raise_vulns)

    # Re-seed vulns so we get past the empty-corpus short-circuit
    old_cwd = os.getcwd()
    try:
        os.chdir(str(taint_project))
        vulns_result = cli_runner.invoke(_cli, ["--json", "vulns"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert vulns_result.exit_code == 0, vulns_result.output
    vulns_data = _json.loads(vulns_result.output)
    vulns_wo = list(vulns_data.get("warnings_out") or []) + list(vulns_data["summary"].get("warnings_out") or [])
    vulns_markers = [m for m in vulns_wo if m.startswith("vulns_")]
    assert vulns_markers, f"expected vulns_* marker; got {vulns_wo!r}"
    # No leakage of taint_ / vuln_reach_ in vulns output.
    assert not any(m.startswith("taint_") for m in vulns_wo), (
        f"vulns output must NOT leak taint_* prefix; got {vulns_wo!r}"
    )


# ---------------------------------------------------------------------------
# (12) W493 KIND='CALLS' AUDIT bonus: classifier uses call_or_ref_in_clause
# ---------------------------------------------------------------------------


def test_w493_classifier_uses_call_or_ref_kinds_not_calls_typo():
    """Regression guard for W493/W499/W512 edge-kind consolidation.

    cmd_taint's ``_classify_flow_shape`` walks adjacent path-pair edges
    via the SQL fragment built by ``call_or_ref_in_clause()`` (W512
    consolidation). The W493 audit history shows that bare
    ``kind = 'calls'`` (with a trailing s) was a historical typo --
    the canonical edge-kind vocabulary defined in
    ``roam.db.edge_kinds.CALL_OR_REF_KINDS`` covers ``call_or_ref``,
    ``call``, ``ref``, etc.

    This guard pins the consolidation: cmd_taint MUST go through
    ``call_or_ref_in_clause()`` and MUST NOT regress to bare
    ``kind = 'calls'`` literal strings.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_taint.py"
    src = src_path.read_text(encoding="utf-8")

    # Positive assertion: the W512 consolidation helper IS imported and
    # used in the classifier.
    assert "from roam.db.edge_kinds import call_or_ref_in_clause" in src, (
        "cmd_taint must import ``call_or_ref_in_clause`` from "
        "``roam.db.edge_kinds`` -- the W512 consolidation. A regression "
        "to ad-hoc ``kind = 'calls'`` SQL fragments would re-introduce "
        "the W493 typo."
    )
    assert "call_or_ref_in_clause()" in src, (
        "cmd_taint must invoke ``call_or_ref_in_clause()`` -- the W512 "
        "consolidation. A regression to inline ``kind = 'calls'`` would "
        "re-introduce the W493 typo and miss every non-``calls`` edge "
        "kind in the canonical vocabulary."
    )

    # Negative assertion: bare ``kind = 'calls'`` literals (the W493
    # typo) must NOT appear in cmd_taint. We check for the exact SQL
    # idiom rather than the bare string 'calls' to avoid false
    # positives on comments / docstrings.
    forbidden_idioms = [
        "kind = 'calls'",
        'kind = "calls"',
        "kind='calls'",
        'kind="calls"',
    ]
    for idiom in forbidden_idioms:
        assert idiom not in src, (
            f"cmd_taint contains forbidden W493 typo idiom {idiom!r}; "
            f"use ``call_or_ref_in_clause()`` for the W512-canonical "
            f"edge-kind vocabulary."
        )
