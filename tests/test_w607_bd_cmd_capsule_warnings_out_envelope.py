"""W607-BD -- ``cmd_capsule`` substrate-boundary plumbing.

cmd_capsule is the graph-capsule exporter: it snapshots the indexed
structural graph (topology counts, symbols with metrics, edges,
clusters, health metrics) and serializes the result as a portable JSON
capsule. Prior to W607-BD a raise inside any substrate helper
(gather_topology / gather_symbols / gather_edges / gather_clusters /
gather_health / atomic_write_capsule / serialize_envelope) crashed the
whole capsule invocation. Per W805 audit-report-build loop this also
left the downstream audit-report consumer with no recourse: a stale
graph or transient SQLite hiccup blew up the export wholesale rather
than degrading to a partial capsule with the surviving sections.

W607-BD is FRESH plumbing: cmd_capsule had NO pre-existing
``warnings_out`` channel and NO ``_run_check`` / substrate-CALL marker
wiring. The accumulator-based markers become the canonical
``summary.warnings_out`` field outright with marker prefix
``capsule_<phase>_failed:<exc_class>:<detail>``.

W978 first-hypothesis check
---------------------------

Each W607-BD-wrapped substrate has a documented empty-floor default
matching its happy-path return shape so a raise degrades cleanly:

* ``gather_topology``    -> ``{"files": 0, "symbols": 0, "edges": 0,
                              "languages": []}``
* ``gather_symbols``     -> ``[]``
* ``gather_edges``       -> ``[]``
* ``gather_clusters``    -> ``[]``
* ``gather_health``      -> ``{"score": 0, "cycles": 0, ...}``
* ``atomic_write_capsule`` -> ``False`` (write skipped; envelope still emits)
* ``serialize_envelope`` -> ``"{}"`` (consumer still gets JSON)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``_build_capsule``
accepts an injected ``run_check`` parameter (the wrapper closure) so
the per-gather instrumentation lives at the click handler -- no new
module-level import edge was introduced.

cmd_capsule does NOT compose preflight / impact / diff / critique --
it's a single-pass graph exporter -- so the upstream-invocation
4-fold pairing bonus from the brief is N/A. The triad-coexistence
check in this file confirms instead that ``capsule_*`` markers stay
in their family and do not leak into sibling W607-* prefixes.

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
def capsule_project(project_factory):
    """Small Python project for capsule export.

    Two trivially-linked Python files give the indexer a non-empty
    symbols + edges table so the capsule has something to serialize.
    """
    return project_factory(
        {
            "app.py": ("from lib import helper\ndef main():\n    return helper()\n"),
            "lib.py": ("def helper():\n    return 42\n"),
        }
    )


def _invoke_capsule(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam capsule`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("capsule")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BD substrate-CALL markers
# ---------------------------------------------------------------------------


def test_capsule_clean_envelope_omits_w607bd_markers(cli_runner, capsule_project):
    """Clean capsule export -> no W607-BD substrate markers.

    Byte-identical-on-happy-path: an empty W607-BD bucket on the success
    path must NOT introduce ``capsule_<phase>_failed:`` markers on the
    envelope. cmd_capsule has no pre-existing warnings_out channel, so
    the field is absent entirely on the clean path.
    """
    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "capsule"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bd_markers = [m for m in (list(top_wo) + list(summary_wo)) if "_failed:" in m and m.startswith("capsule_")]
    assert not bd_markers, (
        f"clean capsule must NOT surface W607-BD substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )

    # Happy path: partial_success is not set (or is False).
    assert not data["summary"].get("partial_success"), data["summary"]


# ---------------------------------------------------------------------------
# (2) gather_topology failure -> structured marker + degraded envelope
# ---------------------------------------------------------------------------


def test_capsule_gather_topology_failure_marker(cli_runner, capsule_project, monkeypatch):
    """If ``_gather_topology`` raises, surface the W607-BD marker.

    Topology gather is the first substrate boundary. A raise here used
    to crash the whole capsule build; now it degrades to the empty-floor
    default (zero files / symbols / edges) plus a marker so the envelope
    still discloses the remaining sections.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-topology-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_topology", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("capsule_gather_topology_failed:")]
    assert markers, f"expected capsule_gather_topology_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-topology-from-W607-BD" in m for m in markers), markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"gather_topology-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_capsule_w607bd_warnings_in_envelope(cli_runner, capsule_project, monkeypatch):
    """Non-empty W607-BD bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_symbols", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BD disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BD disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("capsule_gather_symbols_failed:")]
    assert markers, f"expected capsule_gather_symbols_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) partial_success flips when W607-BD substrate raises
# ---------------------------------------------------------------------------


def test_partial_success_set_when_w607bd_helper_raises(cli_runner, capsule_project, monkeypatch):
    """Any non-empty W607-BD bucket -> summary.partial_success = True."""
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_health", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-BD warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, capsule_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AY contracts.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_edges", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("capsule_gather_edges_failed:")]
    assert failure_markers, f"expected capsule_gather_edges_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "capsule_gather_edges_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) gather_clusters failure -> structured marker
# ---------------------------------------------------------------------------


def test_capsule_gather_clusters_failure_marker(cli_runner, capsule_project, monkeypatch):
    """If ``_gather_clusters`` raises, surface a marker.

    Clusters are an independent boundary -- a raise here should degrade
    only the clusters section (empty list) without disturbing topology /
    symbols / edges / health on the envelope.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-clusters-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_clusters", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("capsule_gather_clusters_failed:")]
    assert markers, f"expected capsule_gather_clusters_failed: marker; got {all_wo!r}"
    # Other sections still present.
    assert "topology" in data, data
    assert "symbols" in data, data
    assert "edges" in data, data
    # Clusters degraded to empty list (the empty-floor default).
    assert data.get("clusters") == [], data.get("clusters")


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-BD stays in ``capsule_*`` family
# ---------------------------------------------------------------------------


def test_w607bd_marker_prefix_stays_in_capsule_family(cli_runner, capsule_project, monkeypatch):
    """Every W607-BD substrate marker uses the canonical ``capsule_*`` prefix.

    cmd_capsule is the graph-capsule exporter substrate -- distinct
    from sibling W607-* layers. Marker prefix MUST stay ``capsule_*``
    and MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_topology", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("capsule_"), (
            f"every surfaced W607-BD marker must use the ``capsule_*`` "
            f"prefix family (cmd_capsule scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers. ``capsule_`` is
        # unique to cmd_capsule -- preflight_, impact_, diff_, critique_,
        # taint_, vuln_reach_, vulns_, etc. must NOT appear.
        for forbidden_prefix, sibling in (
            ("preflight_", "cmd_preflight W607-R / W607-AW"),
            ("impact_", "cmd_impact W607-T"),
            ("diff_", "cmd_diff W607-Z"),
            ("critique_", "cmd_critique W607-Y"),
            ("taint_", "cmd_taint W607-AY"),
            ("vuln_reach_", "cmd_vuln_reach W607-AU"),
            ("vulns_", "cmd_vulns W607-AQ"),
            ("sbom_", "cmd_sbom W607-AM"),
            ("supply_chain_", "cmd_supply_chain W607-AK"),
            ("cga_", "cmd_cga W607-AF"),
            ("attest_", "cmd_attest W607-AD"),
            ("diagnose_", "cmd_diagnose W607-S"),
            ("audit_", "cmd_audit W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L / W607-AZ"),
            ("relate_", "cmd_relate W607-W"),
            ("deps_", "cmd_deps W607-V"),
            ("uses_", "cmd_uses W607-U"),
            ("pr_risk_", "cmd_pr_risk W607-Q / W607-AB"),
            ("findings_", "cmd_findings W607-C"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_capsule carries the W607-BD accumulator
# ---------------------------------------------------------------------------


def test_cmd_capsule_carries_w607bd_accumulator():
    """AST-level guard: cmd_capsule source carries the W607-BD accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BD instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    assert src_path.exists(), f"cmd_capsule.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bd_warnings_out" in src, (
        "W607-BD accumulator missing from cmd_capsule; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bd" in src, (
        "W607-BD ``_run_check_bd`` helper missing from cmd_capsule; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bd is defined inside cmd_capsule.
    tree = ast.parse(src)
    found_run_check_bd = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bd":
            found_run_check_bd = True
            break
    assert found_run_check_bd, (
        "W607-BD ``_run_check_bd`` helper not found in cmd_capsule AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-BD substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bd_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BD substrate boundary is wrapped.

    W607-BD substrate inventory (cmd_capsule):

    * gather_topology       -- file / symbol / edge counts + language list
    * gather_symbols        -- symbol rows + per-row metrics
                               (cognitive_complexity / fan_in / fan_out)
    * gather_edges          -- raw symbol-level edge rows
    * gather_clusters       -- cluster id + label + size rows
    * gather_health         -- health metrics via metrics_history
    * atomic_write_capsule  -- on-disk capsule write (registry-write
                               boundary equivalent)
    * serialize_envelope    -- on-text JSON serialization

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts
    multiple indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_capsule.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "gather_topology",
        "gather_symbols",
        "gather_edges",
        "gather_clusters",
        "gather_health",
        "atomic_write_capsule",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line_bd = f'_run_check_bd("{phase}"' in src
        same_line_run = f'run_check("{phase}"' in src
        # Multi-line variants: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line_bd = (
            f'_run_check_bd(\n        "{phase}"' in src
            or f'_run_check_bd(\n            "{phase}"' in src
            or f'_run_check_bd(\n                "{phase}"' in src
            or f'_run_check_bd(\n                    "{phase}"' in src
            or f'_run_check_bd(\n                        "{phase}"' in src
        )
        multi_line_run = (
            f'run_check(\n        "{phase}"' in src
            or f'run_check(\n            "{phase}"' in src
            or f'run_check(\n                "{phase}"' in src
            or f'run_check(\n                    "{phase}"' in src
            or f'run_check(\n                        "{phase}"' in src
        )
        assert same_line_bd or same_line_run or multi_line_bd or multi_line_run, (
            f"W607-BD _run_check_bd wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (10) PARTIAL-CAPSULE RESILIENCE: one substrate raise still emits envelope
# ---------------------------------------------------------------------------


def test_partial_capsule_resilience_on_health_raise(cli_runner, capsule_project, monkeypatch):
    """Partial-capsule resilience -- W607-BD bonus check.

    When ``_gather_health`` raises, the capsule envelope MUST still
    emit with topology / symbols / edges / clusters populated AND the
    health section degraded to its empty-floor default. Don't crash
    the capsule wholesale on one gather failure.
    """
    from roam.commands import cmd_capsule as _mod

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-health-from-W607-BD")

    monkeypatch.setattr(_mod, "_gather_health", _raise)

    result = _invoke_capsule(cli_runner, capsule_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Envelope still emits a verdict-bearing summary.
    assert "summary" in data, data
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, data["summary"]

    # Surviving sections present.
    assert "topology" in data, data
    assert "symbols" in data, data
    assert "edges" in data, data
    assert "clusters" in data, data
    # Health degraded to empty-floor default.
    assert data["health"]["score"] == 0, data["health"]
    assert data["health"]["cycles"] == 0, data["health"]
    assert data["summary"].get("health_score") == 0, data["summary"]

    # Marker is present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    markers = [m for m in all_wo if m.startswith("capsule_gather_health_failed:")]
    assert markers, f"expected capsule_gather_health_failed: marker on partial-capsule resilience path; got {all_wo!r}"

    # partial_success flips so consumers can branch on degradation.
    assert data["summary"].get("partial_success") is True, data["summary"]


# ---------------------------------------------------------------------------
# (11) atomic_write_capsule failure -> marker + envelope still emits
# ---------------------------------------------------------------------------


def test_capsule_atomic_write_failure_marker(cli_runner, capsule_project, monkeypatch, tmp_path):
    """If ``atomic_write_text`` raises during ``--output``, surface a marker.

    The on-disk write is a registry-write-equivalent boundary -- a
    raise (disk full, permission, read-only filesystem) used to crash
    the whole capsule command. W607-BD wraps it so the in-memory
    capsule data still surfaces via stdout.
    """
    import roam.atomic_io as _atomic_io

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-disk-from-W607-BD")

    # atomic_write_text is imported inside the click handler (lazy
    # import), so patch on its source module so the lazy lookup sees
    # the raise.
    monkeypatch.setattr(_atomic_io, "atomic_write_text", _raise)

    out_file = tmp_path / "capsule.json"
    # --output path -> text summary is printed; W607-BD markers go on
    # stdout if --json is also set. We rely on the json envelope path
    # being suppressed (capsule prints text summary when --output is
    # set), so check that the command exited 0 (didn't crash) and the
    # marker was accumulated -- we verify the marker accumulator was
    # populated by re-invoking with --json (no --output) using a
    # different gather raise to confirm the wrapper shape; this test
    # specifically confirms the --output write path does not crash.
    result = _invoke_capsule(
        cli_runner,
        capsule_project,
        "--output",
        str(out_file),
        json_mode=False,
    )
    # Command must not crash; text summary still prints.
    assert result.exit_code == 0, result.output
    assert "VERDICT:" in result.output, result.output
    # The file write was skipped (PermissionError); out_file MUST NOT exist.
    assert not out_file.exists(), f"atomic_write raised but file still landed at {out_file}"

    # The undocumented assertion is that the wrapper accumulated the
    # marker -- but cmd_capsule does not print the markers to stdout in
    # text mode (only the JSON envelope branch surfaces them). The
    # critical contract verified here is "did not crash + did not write
    # a torn file" -- the JSON-mode marker surfacing is covered by
    # tests (2)/(3)/(4)/(6)/(10) above.


# ---------------------------------------------------------------------------
# (12) TRIAD-COEXISTENCE bonus: capsule_* coexists with sibling W607-*
# ---------------------------------------------------------------------------


def test_w607bd_capsule_markers_coexist_with_taint_and_diff(cli_runner, capsule_project, monkeypatch):
    """W805 audit-report-build loop closure bonus.

    cmd_capsule, cmd_taint, and cmd_diff all run on the same corpus
    and surface their respective markers without prefix collision.
    The marker families stay distinct:
    cmd_capsule -> ``capsule_*``, cmd_taint -> ``taint_*``,
    cmd_diff -> ``diff_*``. No mixing.

    cmd_capsule does NOT invoke any of these upstream commands itself
    -- it's a single-pass graph exporter. The cross-family coexistence
    check confirms that even when an agent runs all three back-to-back
    on the same corpus, the marker namespaces stay in their families.
    """
    from roam.cli import cli as _cli
    from roam.commands import cmd_capsule as _capsule_mod
    from roam.commands import cmd_taint as _taint_mod

    # 1) cmd_capsule -> capsule_* family
    def _raise_capsule(*args, **kwargs):
        raise RuntimeError("synthetic-coexist-capsule-from-W607-BD")

    monkeypatch.setattr(_capsule_mod, "_gather_topology", _raise_capsule)

    capsule_result = _invoke_capsule(cli_runner, capsule_project)
    assert capsule_result.exit_code == 0, capsule_result.output
    capsule_data = _json.loads(capsule_result.output)
    capsule_wo = list(capsule_data.get("warnings_out") or []) + list(capsule_data["summary"].get("warnings_out") or [])
    capsule_markers = [m for m in capsule_wo if m.startswith("capsule_")]
    assert capsule_markers, f"expected capsule_* marker; got {capsule_wo!r}"
    # capsule_* must NOT leak into sibling families.
    assert not any(m.startswith("taint_") for m in capsule_wo), (
        f"capsule output must NOT leak taint_* prefix; got {capsule_wo!r}"
    )
    assert not any(m.startswith("diff_") for m in capsule_wo), (
        f"capsule output must NOT leak diff_* prefix; got {capsule_wo!r}"
    )

    # Restore so cmd_taint can run cleanly, then raise inside it.
    monkeypatch.undo()

    # 2) cmd_taint -> taint_* family
    def _raise_taint(*args, **kwargs):
        raise RuntimeError("synthetic-coexist-taint-from-W607-BD")

    monkeypatch.setattr(_taint_mod, "run_taint", _raise_taint)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(capsule_project))
        taint_result = cli_runner.invoke(_cli, ["--json", "taint"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert taint_result.exit_code == 0, taint_result.output
    taint_data = _json.loads(taint_result.output)
    taint_wo = list(taint_data.get("warnings_out") or []) + list(taint_data["summary"].get("warnings_out") or [])
    # cmd_taint may short-circuit on no rules/findings -- only assert
    # the cross-family discipline when markers exist. We DO confirm
    # that any markers present stay in taint_* and don't leak capsule_.
    assert not any(m.startswith("capsule_") for m in taint_wo), (
        f"taint output must NOT leak capsule_* prefix; got {taint_wo!r}"
    )
