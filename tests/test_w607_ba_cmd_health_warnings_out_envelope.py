"""W607-BA -- ``cmd_health`` substrate-boundary plumbing.

Forty-fourth-in-batch W607 consumer-layer arc. ADDITIVE plumbing: cmd_health
already carries the W607-M ``_w607m_warnings_out`` channel with inline
try/except markers for the DB-shape graph substrates (graph_build / cycles /
god_components / bottlenecks / layers / tangle / propagation_cost /
algebraic_connectivity / file_health / imported_coverage). W607-BA layers a
SECOND bucket (``_w607ba_warnings_out``) + helper (``_run_check_ba``) on top,
wrapping the substrate boundaries W607-M did NOT cover:

* gate_config_load          -- ``_load_gate_config_with_status`` in gate branch
* gate_complexity_query     -- complexity_max SELECT (replaces a pre-W607-BA
                               bare ``except Exception: pass`` that swallowed
                               every marker entirely)
* compute_health_score      -- the geometric-mean 0-100 composition
* compose_verdict           -- the "Healthy 32/100 with 12 cycles" derivation
                               (CLAUDE.md LAW 6 canonical example)
* health_findings_emit      -- ``_emit_health_findings`` registry write
* suggest_next_steps_call   -- the agent-contract next_steps composition
* baseline_diff_emit        -- ``_emit_baseline_diff`` for --baseline mode
* sarif_emit                -- the SARIF projection branch
* gate_sarif_loader         -- ``_load_gate_config`` inside SARIF mode

CLAUDE.md LAW 6 critical axis -- cmd_health's "Healthy 32/100 with 12 cycles"
verdict is the canonical example for a verdict that must work without any
other field. A silent failure in any sub-score boundary defeats the CI gate
downstream consumers depend on. The W607-BA additive bucket surfaces a
marker even when the failure happens AFTER the W607-M-wrapped DB phases
succeed -- partial-batch resilience for the headline 0-100 score.

The marker prefix stays in the ``health_*`` family (same as W607-M). Both
waves share the marker family AND the warnings_out axis -- the per-wave
bucket is merged into a single ``warnings_out`` list before serialization.

Per-signal degradation: a raise in compose_verdict must NOT prevent
compute_health_score, gate_complexity_query, or any other phase from still
running. The per-phase wrap is what gives W607-BA its partial-batch
resilience property -- a broken sub-score shouldn't sink the rest of the
envelope.

W978 first-hypothesis check
---------------------------

Each W607-BA-wrapped substrate has a documented empty-floor default that
matches its happy-path return shape so a raise degrades cleanly.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. Substrates are patched
via ``monkeypatch.setattr(cmd_health, "<helper>", ...)`` on module-level
helpers.

Marker prefix discipline
------------------------

Marker family is ``health_<phase>_failed:<exc_class>:<detail>``.
Hard distinction from sibling W607-* layers preserved by the prefix-
discipline test.

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
def health_project(project_factory):
    """Small indexed corpus -- enough for the health pipeline to produce a
    non-empty envelope (avoids the empty-corpus carve-out at W834 that
    short-circuits before the W607-BA-wrapped phases)."""
    return project_factory(
        {
            "service.py": "def process():\n    return 1\n\ndef helper():\n    return process()\n",
            "api.py": (
                "from service import process\ndef handle():\n    return process()\ndef route():\n    return handle()\n"
            ),
            "lib/util.py": "def util_fn():\n    return 42\n",
        }
    )


def _invoke_health(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam health`` against a project root via the top-level CLI.

    Using the top-level CLI rather than the click command directly so the
    ``--json`` flag wires into ``ctx.obj`` the same way the production
    invocation does.
    """
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("health")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-BA substrate-CALL markers
# ---------------------------------------------------------------------------


def test_health_clean_envelope_omits_w607ba_markers(cli_runner, health_project):
    """Clean health -> no W607-BA substrate markers.

    Byte-identical-on-happy-path: an empty W607-BA bucket on the success
    path must NOT introduce new ``health_<phase>_failed:`` markers tied
    to the W607-BA wrap. The envelope's ``warnings_out`` may still be
    omitted entirely on a clean run (W607-M parity discipline).
    """
    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "health"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    # The W607-BA-specific phases must not surface on a clean run.
    ba_phases = (
        "gate_config_load",
        "gate_complexity_query",
        "compute_health_score",
        "compose_verdict",
        "health_findings_emit",
        "suggest_next_steps_call",
        "baseline_diff_emit",
        "sarif_emit",
        "gate_sarif_loader",
    )
    ba_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"health_{p}_failed:" in m for p in ba_phases)]
    assert not ba_markers, (
        f"clean health must NOT surface W607-BA substrate markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compose_verdict failure -> structured marker + partial_success flip
# ---------------------------------------------------------------------------


def test_health_compute_health_score_failure_marker_format(cli_runner, health_project, monkeypatch):
    """If the geometric-mean health-score scorer raises, surface the
    W607-BA marker with the canonical three-segment shape.

    health_score composition is one of W607-BA's score-composition
    substrate boundaries. A raise here previously would have propagated
    and crashed the gate. W607-BA surfaces it as a structured
    ``health_compute_health_score_failed:<exc>:<detail>`` marker and
    falls back to a default of 0 -- the verdict scorer then composes
    a degraded "Unhealthy 0/100" verdict that still satisfies LAW 6.

    W978 first-hypothesis discipline: patch the LAST math.exp call (the
    one inside ``_compute_health_score`` -- it's invoked only once on
    the final ``100 * math.exp(log_score)`` expression, AFTER all the
    ``_health_factor`` invocations have completed). Patch via a counter
    so the per-factor sigmoid calls still go through normally.
    """
    from roam.commands import cmd_health

    # ``_compute_health_score`` uses ``math.log`` ONLY at the
    # geometric-mean sum (one call per health factor inside the sum's
    # generator expression). The per-factor sigmoid uses ``math.exp``,
    # not ``math.log``. So patching math.log raises only inside the
    # score-compute hot path -- never inside _health_factor.
    def _raise_log(x):
        raise RuntimeError("synthetic-score-from-W607-BA")

    monkeypatch.setattr(cmd_health.math, "log", _raise_log)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    score_markers = [m for m in all_wo if m.startswith("health_compute_health_score_failed:")]
    assert score_markers, f"expected health_compute_health_score_failed: marker; got {all_wo!r}"
    assert any("RuntimeError" in m for m in score_markers), score_markers
    assert any("synthetic-score-from-W607-BA" in m for m in score_markers), score_markers
    # Envelope flips partial_success on the degraded path.
    assert data["summary"].get("partial_success") is True, (
        f"score-failed degraded envelope must flip partial_success; got summary = {data['summary']!r}"
    )
    # The verdict scorer ran AFTER the score failed, so the verdict
    # should still appear as a single line (LAW 6 invariant).
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) warnings_out lands in envelope (top-level AND summary mirror)
# ---------------------------------------------------------------------------


def test_health_w607ba_warnings_in_envelope(cli_runner, health_project, monkeypatch):
    """Non-empty W607-BA bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_health

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-BA")

    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-BA disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-BA disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("health_suggest_next_steps_call_failed:")]
    assert markers, f"expected health_suggest_next_steps_call_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape -- prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, health_project, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A..AZ contracts.
    """
    from roam.commands import cmd_health

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-BA")

    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("health_suggest_next_steps_call_failed:")]
    assert failure_markers, f"expected health_suggest_next_steps_call_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "health_suggest_next_steps_call_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) PER-SIGNAL DEGRADATION: a raise in ONE phase doesn't sink others
# ---------------------------------------------------------------------------


def test_health_per_signal_degradation_other_phases_complete(cli_runner, health_project, monkeypatch):
    """CLAUDE.md LAW 6 critical-axis bonus for cmd_health: a raise in
    suggest_next_steps must NOT prevent the rest of the envelope from
    being composed.

    Simulates: ``suggest_next_steps`` raises, but health_score,
    verdict, severity, etc. all still emit normally. This is the
    partial-batch resilience property that makes per-phase wrapping
    more valuable than an outer-guard -- the outer-guard would
    short-circuit after the first raise, losing the headline 0-100
    score.
    """
    from roam.commands import cmd_health

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-per-signal-from-W607-BA")

    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 1) suggest_next_steps_call failure marker present
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    sn_markers = [m for m in all_wo if m.startswith("health_suggest_next_steps_call_failed:")]
    assert sn_markers, f"expected health_suggest_next_steps_call_failed: marker; got {all_wo!r}"

    # 2) The headline health_score still appears (the geometric mean
    #    ran BEFORE the next_steps raise, so it must survive).
    assert "health_score" in data["summary"], (
        f"health_score missing despite next_steps degrade; got summary = {data['summary']!r}"
    )
    assert isinstance(data["summary"]["health_score"], int)
    assert 0 <= data["summary"]["health_score"] <= 100

    # 3) The verdict still appears and is one line.
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"

    # 4) Severity counts still populated.
    assert "severity" in data["summary"]

    # 5) summary partial_success flipped
    assert data["summary"].get("partial_success") is True, (
        f"per-signal failure must flip partial_success; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (6) CI-GATE bonus: marker presence downgrades the gate verdict
# ---------------------------------------------------------------------------


def test_health_marker_flips_partial_success_on_main_envelope(cli_runner, health_project, monkeypatch):
    """When ANY W607-BA marker is present, partial_success MUST be True
    on the main envelope.

    CLAUDE.md LAW 6 / W531 fail-loud: the headline 0-100 score CI-gate
    must NOT silently pass green when any sub-score boundary failed.
    The marker on warnings_out is the agent-visible signal; the
    partial_success flag is the structured signal CI checks key off
    of.
    """
    from roam.commands import cmd_health

    def _raise(*args, **kwargs):
        raise ValueError("synthetic-ci-gate-from-W607-BA")

    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Invariant: marker present <=> partial_success True
    has_marker = bool(data.get("warnings_out") or data["summary"].get("warnings_out"))
    assert has_marker, (
        f"expected at least one W607-BA marker; "
        f"got top={data.get('warnings_out')!r}, "
        f"summary={data['summary'].get('warnings_out')!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"marker present must imply partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) Marker-prefix discipline -- W607-BA stays in ``health_*`` family
# ---------------------------------------------------------------------------


def test_w607ba_marker_prefix_stays_in_health_family(cli_runner, health_project, monkeypatch):
    """Every W607-BA substrate marker uses the canonical ``health_*`` prefix.

    cmd_health is the flagship CI-gate aggregator -- distinct from sibling
    W607-* layers. Marker prefix MUST stay ``health_*`` (shared with the
    W607-M wave) and MUST NOT leak into other family prefixes.
    """
    from roam.commands import cmd_health

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-BA")

    monkeypatch.setattr(cmd_health, "suggest_next_steps", _raise)

    result = _invoke_health(cli_runner, health_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("health_"), (
            f"every surfaced W607-BA marker must use the ``health_*`` prefix family (cmd_health scope); got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
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
# (8) Source-level guard: cmd_health carries the W607-BA accumulator
# ---------------------------------------------------------------------------


def test_cmd_health_carries_w607ba_accumulator():
    """AST-level guard: cmd_health source carries the W607-BA accumulator.

    Pins the canonical anchors so a future refactor that removes the
    W607-BA instrumentation fails this guard rather than silently
    regressing every other test on dynamic envelope shape.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_health.py"
    assert src_path.exists(), f"cmd_health.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    assert "w607ba_warnings_out" in src, (
        "W607-BA accumulator missing from cmd_health; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ba" in src, (
        "W607-BA ``_run_check_ba`` helper missing from cmd_health; the per-substrate wrapper has been refactored away."
    )
    # Parse-tree level: confirm _run_check_ba is defined inside cmd_health.
    tree = ast.parse(src)
    found_run_check_ba = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ba":
            found_run_check_ba = True
            break
    assert found_run_check_ba, (
        "W607-BA ``_run_check_ba`` helper not found in cmd_health AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (9) Each W607-BA substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ba_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-BA substrate boundary is wrapped.

    W607-BA substrate inventory (cmd_health additive over W607-M):

    * gate_config_load          -- ``_load_gate_config_with_status`` in gate
    * gate_complexity_query     -- complexity_max SELECT MAX(complexity)
    * compute_health_score      -- geometric-mean 0-100 composition
    * compose_verdict           -- "Healthy 32/100 with N cycles" derivation
    * suggest_next_steps_call   -- agent-contract next_steps composition
    * baseline_diff_emit        -- ``_emit_baseline_diff`` --baseline branch
    * sarif_emit                -- ``health_to_sarif`` projection
    * gate_sarif_loader         -- ``_load_gate_config`` inside SARIF mode

    NOTE: ``health_findings_emit`` is intentionally wrapped in an
    inline try/except (NOT _run_check_ba) because the pre-W89 schema
    case is a legitimate silent degrade (sqlite3.OperationalError ->
    pass) while everything else surfaces a marker. The marker emission
    happens through ``_w607ba_warnings_out.append`` directly, not
    through ``_run_check_ba``.

    If a future wave introduces a new substrate boundary, this guard
    needs to know about it -- add the phase name here. Accepts multiple
    indent depths because the call sites span branch blocks
    (8/12/16/20/24 spaces).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_health.py"
    src = src_path.read_text(encoding="utf-8")
    expected_phases = [
        "gate_config_load",
        "gate_complexity_query",
        "compute_health_score",
        "compose_verdict",
        "suggest_next_steps_call",
        "baseline_diff_emit",
        "sarif_emit",
        "gate_sarif_loader",
    ]
    for phase in expected_phases:
        same_line = f'_run_check_ba("{phase}"' in src
        # Multi-line variant: phase string on the next line, indented at
        # 8/12/16/20/24 spaces depending on nesting depth.
        multi_line = (
            f'_run_check_ba(\n        "{phase}"' in src
            or f'_run_check_ba(\n            "{phase}"' in src
            or f'_run_check_ba(\n                "{phase}"' in src
            or f'_run_check_ba(\n                    "{phase}"' in src
            or f'_run_check_ba(\n                        "{phase}"' in src
        )
        assert same_line or multi_line, (
            f"W607-BA _run_check_ba wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )

    # health_findings_emit uses inline append (not _run_check_ba) -- pin
    # that the marker name still exists in source.
    assert "health_findings_emit_failed" in src, (
        "W607-BA health_findings_emit marker name missing from cmd_health; "
        "the inline-wrap discipline for the pre-W89 schema case has been "
        "removed."
    )


# ---------------------------------------------------------------------------
# (10) W607-M outer plumbing coexists with W607-BA additive plumbing
# ---------------------------------------------------------------------------


def test_w607m_and_w607ba_coexist_in_cmd_health():
    """cmd_health carries BOTH the W607-M DB-shape plumbing AND the
    W607-BA additive per-substrate plumbing.

    W607-BA is an ADDITIVE extension to W607-M's pre-existing inline
    try/except plumbing. Both must remain in place: W607-M catches the
    DB-shape graph substrates (graph_build / cycles / god_components /
    bottlenecks / layers / tangle / propagation_cost /
    algebraic_connectivity / file_health / imported_coverage), and
    W607-BA catches the score-composition / verdict / gate-config /
    SARIF / suggest_next_steps boundaries layered on top.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_health.py"
    src = src_path.read_text(encoding="utf-8")
    # W607-M bucket name
    assert "w607m_warnings_out" in src, (
        "W607-M ``_w607m_warnings_out`` bucket missing from cmd_health; the W607-M plumbing has been removed."
    )
    # W607-BA bucket name
    assert "w607ba_warnings_out" in src, (
        "W607-BA ``_w607ba_warnings_out`` bucket missing from cmd_health; the W607-BA plumbing has been removed."
    )
    # W607-M per-phase markers (e.g. graph_build, cycles, propagation_cost)
    assert "health_graph_build_failed" in src, (
        "W607-M ``health_graph_build_failed`` marker family missing from "
        "cmd_health; the W607-M plumbing has been removed."
    )
    # W607-BA per-phase markers (use the helper format)
    assert "_run_check_ba" in src, (
        "W607-BA ``_run_check_ba`` per-phase helper missing from cmd_health; the W607-BA plumbing has been removed."
    )


# ---------------------------------------------------------------------------
# (11) W834 empty-corpus silent-Healthy disclosure coexists with W607-BA
# ---------------------------------------------------------------------------


def test_w834_empty_corpus_carve_out_coexists_with_w607ba(cli_runner, project_factory):
    """The W834 empty-corpus carve-out (Pattern 2 silent-fallback fix)
    fires BEFORE the W607-BA-instrumented phases. Verify both states
    coexist: the empty-corpus envelope still emits cleanly with
    ``state="empty_corpus"`` + ``partial_success=True`` AND does NOT
    introduce any W607-BA markers (since the carve-out short-circuits
    before any W607-BA substrate runs).
    """
    # Empty project -- index it but no actual symbols.
    proj = project_factory({"README.md": "# empty\n"})

    result = _invoke_health(cli_runner, proj)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # W834 contract: state=empty_corpus, partial_success=True
    assert data["summary"].get("state") == "empty_corpus", (
        f"W834 empty-corpus carve-out broken; got state = {data['summary'].get('state')!r}"
    )
    assert data["summary"].get("partial_success") is True, (
        f"W834 empty-corpus envelope must flip partial_success; got summary = {data['summary']!r}"
    )

    # W607-BA invariant: no W607-BA markers (the carve-out short-circuits
    # before any W607-BA substrate boundary runs).
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    ba_phases = (
        "gate_config_load",
        "gate_complexity_query",
        "compute_health_score",
        "compose_verdict",
        "suggest_next_steps_call",
        "baseline_diff_emit",
        "sarif_emit",
        "gate_sarif_loader",
        "health_findings_emit",
    )
    ba_markers = [m for m in all_wo if any(f"health_{p}_failed:" in m for p in ba_phases)]
    assert not ba_markers, f"W834 empty-corpus path must NOT trigger W607-BA markers; got {ba_markers!r}"
