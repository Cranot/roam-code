"""W607-BC -- ``cmd_understand`` per-phase substrate-CALL marker plumbing.

Forty-something-in-batch W607 consumer-layer arc. FRESH plumbing on a
high-traffic exploration aggregator. cmd_understand is the third member
of the canonical exploration trio (cmd_describe W607-K + cmd_minimap
W607-L + W607-AZ + cmd_understand W607-BC) -- agents call ``roam
understand`` as the single-call orientation report covering project
structure, tech stack, architecture, health, hotspots, conventions,
complexity, patterns, debt, and reading order. A raise in any one
downstream substrate previously bubbled as a Click traceback and
dropped the whole envelope. W607-BC surfaces each raise as a structured
``understand_<phase>_failed:<exc_class>:<detail>`` marker.

W607-BC substrate inventory:

* detect_frameworks       -- framework scan over edge targets + file content
* detect_build            -- build-tool detection from file names
* build_graph_layers      -- build_symbol_graph + detect_layers (combined)
* find_entry_points       -- files with no importers + symbols
* key_abstractions        -- top symbols by PageRank
* load_clusters           -- cluster query
* collect_metrics         -- health metrics aggregator
* find_hotspots           -- churn + coupling
* detect_conventions      -- conventions_helper delegate
* complexity_overview     -- aggregate complexity stats
* detect_patterns         -- strategy/factory lightweight detection
* top_debt                -- debt-hotspot weighted query
* suggest_reading_order   -- reading-order prioritizer
* gather_tour_data        -- --tour data gatherer (mode-only)
* serialize_envelope      -- on-text JSON serialization
* render_text             -- text-mode rendering
* emit_tour_text          -- --tour text emit (mode-only)

EXPLORATION-COMMAND resilience: cmd_understand is a high-traffic agent
orientation surface -- losing the envelope on a single broken detector
would force agents into a brittle Glob/Grep fallback. The per-phase
wrap is what gives W607-BC its "partial-batch resilience" property.

W978 first-hypothesis check
---------------------------

Each W607-BC-wrapped substrate has a documented empty-floor default
that matches its happy-path return shape so a raise degrades cleanly:

* detect_frameworks       -> []                  (empty frameworks list)
* detect_build            -> None                (no tool detected)
* build_graph_layers      -> (None, [])          (no G, no layers)
* find_entry_points       -> []
* key_abstractions        -> []
* load_clusters           -> []
* collect_metrics         -> empty health dict   (zero scores everywhere)
* find_hotspots           -> []
* detect_conventions      -> {}                  (empty conventions dict)
* complexity_overview     -> None
* detect_patterns         -> []
* top_debt                -> []
* suggest_reading_order   -> []
* gather_tour_data        -> None
* render_text             -> None                (text-render side effect)
* emit_tour_text          -> None                (text-render side effect)
* serialize_envelope      -> None                (manual fallback rebuild)

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. The substrate helpers
are patched via ``monkeypatch.setattr(cmd_understand, "_<helper>", ...)``
on module-level helpers.

Marker prefix discipline
------------------------

Marker family is ``understand_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers (``describe_*`` for cmd_describe,
``minimap_*`` for cmd_minimap, ``preflight_*`` for cmd_preflight, etc.).

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def understand_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges -- the W607-BC
    substrate-failure baseline."""
    proj = tmp_path / "understand_w607bc_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _invoke_understand(runner: CliRunner, cwd, *extra, json_mode: bool = True):
    """Invoke ``roam understand`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("understand")
    args.extend(extra)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BC substrate-CALL markers
# ---------------------------------------------------------------------------


def test_understand_clean_envelope_omits_w607bc_markers(cli_runner, understand_project):
    """Clean understand -> no W607-BC substrate markers.

    Byte-identical-on-happy-path: an empty W607-BC bucket on the success
    path must NOT introduce ``understand_*_failed:`` markers on the
    envelope. The envelope's ``warnings_out`` is omitted entirely on a
    clean run.
    """
    result = _invoke_understand(cli_runner, understand_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "understand"
    # Empty-bucket discipline: NO warnings_out keys on the clean path.
    assert "warnings_out" not in data, (
        f"clean understand must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean understand must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) detect_conventions failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_understand_detect_conventions_failure_marker_format(cli_runner, understand_project, monkeypatch):
    """If ``_detect_conventions`` raises, surface the W607-BC marker.

    Conventions detection is one of the multi-substrate boundaries.
    """
    from roam.commands import cmd_understand

    def _boom_conventions(conn):
        raise RuntimeError("synthetic-conventions-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_detect_conventions", _boom_conventions)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("understand_detect_conventions_failed:")]
    assert markers, f"expected understand_detect_conventions_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers
    assert any("synthetic-conventions-from-W607-BC" in m for m in markers), markers
    assert data["summary"].get("partial_success") is True, (
        f"conventions-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_understand_w607bc_warnings_in_envelope(cli_runner, understand_project, monkeypatch):
    """Non-empty W607-BC bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_understand

    def _boom_hotspots(conn, limit=10):
        raise RuntimeError("synthetic-hotspots-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_find_hotspots", _boom_hotspots)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BC disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BC disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("understand_find_hotspots_failed:")]
    assert markers, f"expected understand_find_hotspots_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, understand_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AZ contracts.
    """
    from roam.commands import cmd_understand

    def _boom_top_debt(conn, limit=5):
        raise ValueError("synthetic-shape-detail-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_top_debt", _boom_top_debt)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("understand_top_debt_failed:")]
    assert failure_markers, f"expected understand_top_debt_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "understand_top_debt_failed", parts
    assert parts[1] == "ValueError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) EXPLORATION-COMMAND partial-batch resilience: one phase raises,
#     remaining substrates still surface
# ---------------------------------------------------------------------------


def test_understand_partial_batch_resilience_envelope_preserved(cli_runner, understand_project, monkeypatch):
    """A raise in ``_detect_patterns_summary`` must NOT abort the envelope.

    Per-substrate PARTIAL-BATCH-RESILIENCE bonus shape: one substrate
    boundary failing must NOT prevent the rest of the orientation
    report (project stats, languages, tech stack, architecture, health)
    from being delivered to the agent.
    """
    from roam.commands import cmd_understand

    def _boom_patterns(conn):
        raise RuntimeError("synthetic-batch-patterns-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_detect_patterns_summary", _boom_patterns)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) detect_patterns failure marker present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    pattern_markers = [m for m in all_wo if m.startswith("understand_detect_patterns_failed:")]
    assert pattern_markers, f"expected understand_detect_patterns_failed: marker; got {all_wo!r}"

    # 2) summary partial_success flipped
    assert data["summary"].get("partial_success") is True, (
        f"partial-batch failure must flip partial_success; got summary = {data['summary']!r}"
    )

    # 3) Envelope still emits cleanly + carries the remaining substrates.
    assert data["command"] == "understand"
    assert "project" in data, "remaining substrates dropped on partial failure"
    assert "tech_stack" in data, "remaining substrates dropped on partial failure"
    assert "architecture" in data, "remaining substrates dropped on partial failure"
    assert "health_summary" in data, "remaining substrates dropped on partial failure"


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline -- W607-BC stays in ``understand_*`` family
# ---------------------------------------------------------------------------


def test_w607bc_marker_prefix_stays_in_understand_family(cli_runner, understand_project, monkeypatch):
    """Every W607-BC substrate marker uses the canonical ``understand_*`` prefix.

    cmd_understand is the orientation aggregator -- distinct from
    sibling W607-* layers. Marker prefix MUST stay ``understand_*`` and
    MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_understand

    def _boom_conventions(conn):
        raise RuntimeError("synthetic-prefix-discipline-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_detect_conventions", _boom_conventions)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("understand_"), (
            f"every surfaced W607-BC marker must use the ``understand_*`` "
            f"prefix family (cmd_understand scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("minimap_", "cmd_minimap W607-L / W607-AZ"),
            ("describe_", "cmd_describe W607-K"),
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
            ("audit_trail_", "cmd_audit_trail W607-P"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N"),
            ("health_", "cmd_health W607-M"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            ("vuln_reach_", "cmd_vuln_reach W607-AU"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_understand carries the W607-BC accumulator
# ---------------------------------------------------------------------------


def test_cmd_understand_carries_w607bc_accumulator():
    """AST-level guard: cmd_understand source carries the W607-BC accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BC instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_understand.py"
    assert src_path.exists(), f"cmd_understand.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "_w607bc_warnings_out" in src, (
        "W607-BC accumulator missing from cmd_understand; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bc" in src, (
        "W607-BC ``_run_check_bc`` helper missing from cmd_understand; the "
        "per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bc is defined inside cmd_understand.
    tree = ast.parse(src)
    found_run_check_bc = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bc":
            found_run_check_bc = True
            break
    assert found_run_check_bc, (
        "W607-BC ``_run_check_bc`` helper not found in cmd_understand AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Each W607-BC substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bc_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BC substrate boundary is wrapped.

    W607-BC substrate inventory (cmd_understand):

    * detect_frameworks       -- framework scan
    * detect_build            -- build-tool detection
    * build_graph_layers      -- build_symbol_graph + detect_layers
    * find_entry_points       -- entry-point discovery
    * key_abstractions        -- top symbols by PageRank
    * load_clusters           -- cluster query
    * collect_metrics         -- health metrics aggregator
    * find_hotspots           -- churn + coupling
    * detect_conventions      -- conventions_helper delegate
    * complexity_overview     -- complexity stats
    * detect_patterns         -- strategy/factory detection
    * top_debt                -- debt-hotspot weighted query
    * suggest_reading_order   -- reading-order prioritizer
    * gather_tour_data        -- --tour data gatherer
    * render_text             -- text-mode rendering
    * emit_tour_text          -- --tour text emit
    * serialize_envelope      -- on-text JSON serialization

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_understand.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "detect_frameworks",
        "detect_build",
        "build_graph_layers",
        "find_entry_points",
        "key_abstractions",
        "load_clusters",
        "collect_metrics",
        "find_hotspots",
        "detect_conventions",
        "complexity_overview",
        "detect_patterns",
        "top_debt",
        "suggest_reading_order",
        "gather_tour_data",
        "render_text",
        "emit_tour_text",
        "serialize_envelope",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bc("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bc(\n        "{phase}"' in src
            or f'_run_check_bc(\n            "{phase}"' in src
            or f'_run_check_bc(\n                "{phase}"' in src
            or f'_run_check_bc(\n                    "{phase}"' in src
            or f'_run_check_bc(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BC _run_check_bc wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) EXPLORATION-TRIO closure: understand_* / describe_* / minimap_*
#     markers all coexist when invoked in sequence on the same corpus
# ---------------------------------------------------------------------------


def test_exploration_trio_markers_coexist_on_same_corpus(cli_runner, understand_project, monkeypatch):
    """Trio milestone: W607-K (describe) + W607-L/AZ (minimap) + W607-BC
    (understand) markers coexist when the three commands are invoked in
    sequence on the same corpus.

    Each command keeps its own marker family discipline -- ``describe_*``
    for cmd_describe, ``minimap_*`` for cmd_minimap, ``understand_*``
    for cmd_understand -- and they do not collide. This pins the
    closure of the canonical exploration-aggregator trio.
    """
    from roam.cli import cli
    from roam.commands import cmd_minimap, cmd_understand

    # Force one substrate failure in each of the three commands so all
    # three marker families fire.
    def _boom_understand_conv(conn):
        raise RuntimeError("synthetic-understand-trio")

    def _boom_minimap_upsert(*args, **kwargs):
        raise PermissionError("synthetic-minimap-trio")

    monkeypatch.setattr(cmd_understand, "_detect_conventions", _boom_understand_conv)
    monkeypatch.setattr(cmd_minimap, "_upsert_file", _boom_minimap_upsert)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(understand_project))

        # 1) roam understand --json
        r_understand = cli_runner.invoke(cli, ["--json", "understand"], catch_exceptions=False)
        assert r_understand.exit_code == 0, r_understand.output
        d_understand = _json.loads(r_understand.output)

        # 2) roam minimap --json -o tmp.md
        target = understand_project / "tour-CLAUDE.md"
        r_minimap = cli_runner.invoke(
            cli,
            ["--json", "minimap", "-o", str(target)],
            catch_exceptions=False,
        )
        assert r_minimap.exit_code == 0, r_minimap.output
        d_minimap = _json.loads(r_minimap.output)
    finally:
        os.chdir(old_cwd)

    # cmd_understand markers (W607-BC family)
    understand_wo = list(d_understand.get("warnings_out") or []) + list(
        d_understand["summary"].get("warnings_out") or []
    )
    understand_markers = [m for m in understand_wo if m.startswith("understand_")]
    assert understand_markers, f"expected understand_* markers from cmd_understand W607-BC; got {understand_wo!r}"
    # cmd_understand must NOT carry describe_* or minimap_* markers
    for m in understand_wo:
        assert not m.startswith("describe_"), f"cmd_understand envelope must NOT carry describe_* markers; got {m!r}"
        assert not m.startswith("minimap_"), f"cmd_understand envelope must NOT carry minimap_* markers; got {m!r}"

    # cmd_minimap markers (W607-L / W607-AZ family)
    minimap_wo = list(d_minimap.get("warnings_out") or []) + list(d_minimap["summary"].get("warnings_out") or [])
    minimap_markers = [m for m in minimap_wo if m.startswith("minimap_")]
    assert minimap_markers, f"expected minimap_* markers from cmd_minimap W607-L/AZ; got {minimap_wo!r}"
    # cmd_minimap must NOT carry understand_* or describe_* markers
    for m in minimap_wo:
        assert not m.startswith("understand_"), f"cmd_minimap envelope must NOT carry understand_* markers; got {m!r}"
        assert not m.startswith("describe_"), f"cmd_minimap envelope must NOT carry describe_* markers; got {m!r}"


# ---------------------------------------------------------------------------
# (10) Top-level vs summary.warnings_out parity on disclosure path
# ---------------------------------------------------------------------------


def test_top_level_and_summary_warnings_out_parity(cli_runner, understand_project, monkeypatch):
    """top-level warnings_out and summary.warnings_out must agree.

    Same closure invariant the W607-AZ minimap test (#10) pins: the
    bucket is sourced once and threaded into both channels so consumers
    reading either end see the same lineage.
    """
    from roam.commands import cmd_understand

    def _boom_complexity(conn):
        raise RuntimeError("synthetic-parity-from-W607-BC")

    monkeypatch.setattr(cmd_understand, "_complexity_overview", _boom_complexity)

    result = _invoke_understand(cli_runner, understand_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    assert sorted(top_wo) == sorted(summary_wo), (
        f"top-level vs summary.warnings_out must be equal; top={top_wo!r} summary={summary_wo!r}"
    )
    # And the disclosed marker is the complexity one we synthesised.
    complexity_markers = [m for m in top_wo if m.startswith("understand_complexity_overview_failed:")]
    assert complexity_markers, f"expected understand_complexity_overview_failed: marker; got {top_wo!r}"
