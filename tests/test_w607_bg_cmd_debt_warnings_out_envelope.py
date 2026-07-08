"""W607-BG -- ``cmd_debt`` substrate-boundary plumbing.

Forty-fifth-in-batch W607 consumer-layer arc. FRESH plumbing: cmd_debt had
no prior W607 instrumentation. This wave installs the canonical
``_w607bg_warnings_out`` bucket + ``_run_check_bg`` helper inside the
``debt`` click command and wraps the substrate boundaries:

* compute_file_debt         -- per-file score aggregator (loads file_stats
                              + cycles + god + dead + coupling, builds
                              debt_score). Carries the inline
                              ``debt_cycle_detection_failed:`` sub-marker
                              that FIXES IN PLACE the pre-W607-BG bare
                              ``except Exception: pass`` in the cycle
                              detection block (Pattern-2 silent fallback).
* summary_stats             -- aggregate project-level rollup
* improvement_suggestions   -- actionable suggestions block
* estimate_refactoring_roi  -- the ROI prioritization core
                              (developer-hours saved / quarter)
* group_by_directory        -- --by-kind grouping

cmd_debt is the paired surface to cmd_health (W607-M + W607-BA, just
landed) on the DB-substrate family: both consume cycles, god_components,
complexity. Per the wave plan, cmd_debt is the refactoring backlog with
ROI prioritization -- a raise inside ``_estimate_refactoring_roi`` MUST
NOT crash the debt report wholesale; the items still emit in raw
debt_score order (the natural severity fallback when the ROI ranking
layer collapses).

Marker family ``debt_<phase>_failed:<exc_class>:<detail>``. Hard
distinction from sibling W607-* layers preserved by the prefix-discipline
test (cmd_health uses ``health_*``, cmd_debt uses ``debt_*``).

W978 first-hypothesis check
---------------------------

Each W607-BG-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrates are patched
via ``monkeypatch.setattr(cmd_debt, "<helper>", ...)`` on module-level
helpers.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.

LATENT BUG ELIMINATED (not xfailed): the pre-W607-BG ``try/except: pass``
inside ``_compute_file_debt``'s cycle-detection block was a Pattern-2
silent fallback -- a successful debt report would be emitted even when
the cycle scan failed, masking the cycle-penalty contribution from every
file. FIXED IN PLACE via the inline ``warnings_out`` parameter: the
exception still degrades to ``cycle_files = set()`` (the correct floor)
but now surfaces a ``debt_cycle_detection_failed:<exc>:<detail>`` marker
on the bucket so the agent sees that cycle data is missing.
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
def debt_project(project_factory):
    """Small indexed corpus -- enough for the debt pipeline to produce a
    non-empty envelope (file_stats populated by the indexer)."""
    return project_factory(
        {
            "service.py": (
                "def process():\n"
                "    if True:\n"
                "        for i in range(10):\n"
                "            if i % 2 == 0:\n"
                "                return i\n"
                "    return 0\n"
                "\n"
                "def helper():\n"
                "    return process()\n"
            ),
            "api.py": (
                "from service import process\ndef handle():\n    return process()\ndef route():\n    return handle()\n"
            ),
            "lib/util.py": "def util_fn():\n    return 42\n",
        }
    )


def _invoke_debt(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam debt`` against a project root via the top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("debt")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BG substrate markers
# ---------------------------------------------------------------------------


def test_debt_clean_envelope_omits_w607bg_markers(cli_runner, debt_project):
    """Clean debt run -> no W607-BG substrate markers.

    Byte-identical-on-happy-path discipline: an empty W607-BG bucket on
    the success path must NOT introduce new ``debt_<phase>_failed:``
    markers tied to the W607-BG wrap.
    """
    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "debt"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    bg_phases = (
        "compute_file_debt",
        "summary_stats",
        "improvement_suggestions",
        "estimate_refactoring_roi",
        "group_by_directory",
        "cycle_detection",
    )
    bg_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"debt_{p}_failed:" in m for p in bg_phases)]
    assert not bg_markers, (
        f"clean debt must NOT surface W607-BG substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) summary_stats failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_debt_summary_stats_failure_marker_format(cli_runner, debt_project, monkeypatch):
    """If ``_summary_stats`` raises, surface the W607-BG marker with the
    canonical three-segment shape.
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-summary-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_summary_stats", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    stats_markers = [m for m in all_wo if m.startswith("debt_summary_stats_failed:")]
    assert stats_markers, f"expected debt_summary_stats_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in stats_markers), stats_markers
    assert any("synthetic-summary-from-W607-BG" in m for m in stats_markers), stats_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"stats-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # LAW 6: the verdict still appears as a single line (the verdict
    # composer ran AFTER the stats default kicked in).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_debt_w607bg_warnings_in_envelope(cli_runner, debt_project, monkeypatch):
    """Non-empty W607-BG bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BG disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BG disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("debt_improvement_suggestions_failed:")]
    assert markers, f"expected debt_improvement_suggestions_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_debt_three_segment_marker_shape(cli_runner, debt_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("debt_improvement_suggestions_failed:")]
    assert failure_markers, f"expected debt_improvement_suggestions_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "debt_improvement_suggestions_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) ROI DEGRADATION -- items still emit when ROI ranking collapses
# ---------------------------------------------------------------------------


def test_debt_roi_degradation_items_still_emit(cli_runner, debt_project, monkeypatch):
    """ROI-degradation discipline: a raise in ``_estimate_refactoring_roi``
    must NOT crash the debt report wholesale.

    The debt items continue to emit in raw debt_score order (the natural
    severity fallback when the ROI ranking layer collapses). The
    ``debt_estimate_refactoring_roi_failed:`` marker surfaces so the
    agent can see the ranking degradation.
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-roi-collapse-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_estimate_refactoring_roi", _raise)

    # Invoke WITH --roi so the substrate path runs.
    result = _invoke_debt(cli_runner, debt_project, "--roi")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) ROI substrate marker present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    roi_markers = [m for m in all_wo if m.startswith("debt_estimate_refactoring_roi_failed:")]
    assert roi_markers, f"expected debt_estimate_refactoring_roi_failed: marker; got {all_wo!r}"

    # 2) The debt items array STILL emits in raw debt_score order. The
    #    ROI layer collapse must NOT sink the prioritization report.
    items = data.get("items")
    assert isinstance(items, list), f"items must still emit on ROI degradation; got data = {data!r}"
    if len(items) >= 2:
        # debt_score must be monotonically non-increasing (severity
        # fallback when ROI ranking is unavailable).
        scores = [it["debt_score"] for it in items]
        assert scores == sorted(scores, reverse=True), (
            f"items must remain sorted by debt_score on ROI degradation; got {scores!r}"
        )
        # Items must NOT carry a roi sub-payload (ROI collapsed -> no
        # roi entries in the per-path map).
        for it in items:
            assert "roi" not in it, f"items must not carry roi payload after collapse; got {it!r}"

    # 3) summary.partial_success flipped.
    assert data["summary"].get("partial_success") is True, (
        f"ROI degradation must flip partial_success; got summary = {data['summary']!r}"
    )

    # 4) The headline verdict still appears (LAW 6).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (6) PER-SIGNAL DEGRADATION: a raise in ONE phase doesn't sink others
# ---------------------------------------------------------------------------


def test_debt_per_signal_degradation_other_phases_complete(cli_runner, debt_project, monkeypatch):
    """A raise in ``_improvement_suggestions`` must NOT prevent the rest
    of the envelope from being composed (debt_score items, verdict,
    summary stats, etc. all still emit normally).
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-per-signal-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) improvement_suggestions failure marker present.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    sn_markers = [m for m in all_wo if m.startswith("debt_improvement_suggestions_failed:")]
    assert sn_markers, f"expected debt_improvement_suggestions_failed: marker; got {all_wo!r}"

    # 2) The headline items array still appears.
    items = data.get("items")
    assert isinstance(items, list), f"items missing despite suggestions degrade; got data = {data!r}"

    # 3) The verdict still appears and is one line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"

    # 4) Total-files still populated from summary_stats.
    assert "total_files" in data["summary"]

    # 5) summary partial_success flipped.
    assert data["summary"].get("partial_success") is True, (
        f"per-signal failure must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-BG stays in ``debt_*`` family
# ---------------------------------------------------------------------------


def test_w607bg_marker_prefix_stays_in_debt_family(cli_runner, debt_project, monkeypatch):
    """Every W607-BG substrate marker uses the canonical ``debt_*`` prefix.

    Hard distinction from sibling W607-* layers including the paired
    cmd_health surface (W607-M / W607-BA, ``health_*``).
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("debt_"), (
            f"every surfaced W607-BG marker must use the ``debt_*`` prefix family (cmd_debt scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("health_", "cmd_health W607-M / W607-BA"),
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
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("retrieve_", "cmd_retrieve W607-B"),
            ("findings_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D / W607-AV"),
            ("evidence_diff_", "cmd_evidence_diff W607-AX"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (8) Source-level guard: cmd_debt carries the W607-BG accumulator
# ---------------------------------------------------------------------------


def test_cmd_debt_carries_w607bg_accumulator():
    """AST-level guard: cmd_debt source carries the W607-BG accumulator."""
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_debt.py"
    assert src_path.exists(), f"cmd_debt.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607bg_warnings_out" in src, (
        "W607-BG accumulator missing from cmd_debt; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_bg" in src, (
        "W607-BG ``_run_check_bg`` helper missing from cmd_debt; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_bg is defined inside cmd_debt.
    tree = ast.parse(src)
    found_run_check_bg = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_bg":
            found_run_check_bg = True
            break
    assert found_run_check_bg, (
        "W607-BG ``_run_check_bg`` helper not found in cmd_debt AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-BG substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607bg_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BG substrate boundary is wrapped.

    W607-BG substrate inventory (cmd_debt):

    * compute_file_debt         -- per-file score aggregator
    * summary_stats             -- aggregate project-level rollup
    * improvement_suggestions   -- actionable suggestions block
    * estimate_refactoring_roi  -- ROI prioritization core
    * group_by_directory        -- --by-kind grouping

    NOTE: ``cycle_detection`` is wrapped via the inline ``warnings_out``
    parameter inside ``_compute_file_debt`` (NOT ``_run_check_bg``)
    because the cycle scan is a nested sub-substrate. The marker name
    surfaces via direct ``warnings_out.append`` -- pinned by the
    ``debt_cycle_detection_failed`` source-grep below.

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_debt.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "compute_file_debt",
        "summary_stats",
        "improvement_suggestions",
        "estimate_refactoring_roi",
        "group_by_directory",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_bg("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_bg(\n        "{phase}"' in src
            or f'_run_check_bg(\n            "{phase}"' in src
            or f'_run_check_bg(\n                "{phase}"' in src
            or f'_run_check_bg(\n                    "{phase}"' in src
            or f'_run_check_bg(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BG _run_check_bg wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )

    # cycle_detection uses inline append (not _run_check_bg) -- pin
    # that the marker name still exists in source. This is the Pattern-2
    # silent-fallback FIXED IN PLACE: the pre-W607-BG bare
    # ``except Exception: pass`` is gone; the new path surfaces a marker
    # via direct warnings_out.append.
    assert "debt_cycle_detection_failed" in src, (
        "W607-BG debt_cycle_detection_failed marker name missing from "
        "cmd_debt; the inline-wrap discipline for the cycle-detection "
        "sub-substrate has been removed."
    )


# ---------------------------------------------------------------------------
# (10) HEALTH/DEBT PAIRING -- markers can coexist across the pair
# ---------------------------------------------------------------------------


def test_health_debt_marker_families_coexist_on_same_corpus(cli_runner, debt_project, monkeypatch):
    """cmd_health (W607-M + W607-BA, ``health_*``) and cmd_debt (W607-BG,
    ``debt_*``) share substrate boundaries on the same DB shape (cycles,
    god_components, complexity, dead_exports, churn percentile). Verify
    markers from BOTH families surface together when run sequentially
    on the same corpus.

    This is the canonical health/debt pairing bonus -- two consumer
    surfaces of the same substrate layer, distinct prefix families,
    coexist cleanly on the same warnings_out axis.
    """
    from roam.commands import cmd_debt, cmd_health

    def _raise_debt(*args, **kwargs):
        raise RuntimeError("synthetic-debt-from-pairing")

    def _raise_health(*args, **kwargs):
        raise RuntimeError("synthetic-health-from-pairing")

    # Inject a raise into ONE substrate per command.
    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise_debt)
    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise_health)

    # Run debt first.
    debt_result = _invoke_debt(cli_runner, debt_project)
    assert debt_result.exit_code == 0, debt_result.output
    debt_data = _json.loads(debt_result.output)
    debt_top_wo = debt_data.get("warnings_out") or []
    debt_summary_wo = debt_data["summary"].get("warnings_out") or []
    debt_all_wo = list(debt_top_wo) + list(debt_summary_wo)
    debt_markers = [m for m in debt_all_wo if m.startswith("debt_improvement_suggestions_failed:")]
    assert debt_markers, f"expected debt_improvement_suggestions_failed: marker on debt envelope; got {debt_all_wo!r}"

    # Run health on the same corpus.
    from roam.cli import cli as _cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(debt_project))
        health_result = cli_runner.invoke(_cli, ["--json", "health"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    assert health_result.exit_code == 0, health_result.output
    health_data = _json.loads(health_result.output)
    health_top_wo = health_data.get("warnings_out") or []
    health_summary_wo = health_data["summary"].get("warnings_out") or []
    health_all_wo = list(health_top_wo) + list(health_summary_wo)
    health_markers = [m for m in health_all_wo if m.startswith("health_suggest_next_steps_call_failed:")]
    assert health_markers, (
        f"expected health_suggest_next_steps_call_failed: marker on health envelope; got {health_all_wo!r}"
    )

    # Prefix-family isolation: debt envelope must NOT carry health_*
    # markers, and vice versa. The two waves are independent.
    debt_health_leak = [m for m in debt_all_wo if m.startswith("health_")]
    assert not debt_health_leak, f"debt envelope must NOT carry health_* markers; got {debt_health_leak!r}"
    health_debt_leak = [m for m in health_all_wo if m.startswith("debt_")]
    assert not health_debt_leak, f"health envelope must NOT carry debt_* markers; got {health_debt_leak!r}"

    # Both surfaces flipped partial_success.
    assert debt_data["summary"].get("partial_success") is True
    assert health_data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (11) Pattern-2 silent fallback FIXED IN PLACE -- cycle_detection marker
# ---------------------------------------------------------------------------


def test_w607bg_cycle_detection_marker_replaces_silent_fallback(cli_runner, debt_project, monkeypatch):
    """Pattern-2 silent-fallback elimination: the pre-W607-BG bare
    ``try/except: pass`` inside ``_compute_file_debt``'s cycle-detection
    block silently swallowed graph failures. FIXED IN PLACE: the
    exception still degrades to ``cycle_files = set()`` (correct floor)
    but now surfaces a ``debt_cycle_detection_failed:<exc>:<detail>``
    marker on the bucket.
    """
    from roam.graph import builder as _graph_builder

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cycle-detection-from-W607-BG")

    monkeypatch.setattr(_graph_builder, "build_symbol_graph", _raise)

    result = _invoke_debt(cli_runner, debt_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    cycle_markers = [m for m in all_wo if m.startswith("debt_cycle_detection_failed:")]
    assert cycle_markers, f"expected debt_cycle_detection_failed: marker (Pattern-2 fix); got {all_wo!r}"
    assert any("RuntimeError" in m for m in cycle_markers), cycle_markers

    # The envelope still emits (the degraded floor is correct -- cycle
    # data is missing but the debt report is not a wholesale crash).
    assert "items" in data
    # partial_success flips because of the surfaced marker.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (12) --by-kind path also threads W607-BG warnings
# ---------------------------------------------------------------------------


def test_debt_by_kind_threads_w607bg_warnings(cli_runner, debt_project, monkeypatch):
    """The ``--by-kind`` (grouped) JSON branch also threads W607-BG
    markers into both warnings_out fields and flips partial_success.
    """
    from roam.commands import cmd_debt

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-bykind-from-W607-BG")

    monkeypatch.setattr(cmd_debt, "_improvement_suggestions", _raise)

    result = _invoke_debt(cli_runner, debt_project, "--by-kind")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    markers = [m for m in all_wo if m.startswith("debt_improvement_suggestions_failed:")]
    assert markers, f"expected debt_improvement_suggestions_failed: marker on --by-kind path; got {all_wo!r}"
    assert data["summary"].get("partial_success") is True
    # The groups payload still emits (per-signal degradation).
    assert "groups" in data
