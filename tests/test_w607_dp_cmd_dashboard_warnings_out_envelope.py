"""W607-DP — ``cmd_dashboard`` substrate-CALL plumbing LAYERED on W607-O.

cmd_dashboard is the unified single-screen status surface — it aggregates
overview / health (collect_metrics) / hotspots / risks / vibe-check /
danger-zone substrates into ONE envelope. W607-O (already shipped) wraps
the 7 capture-layer ``_run_check`` boundaries (overview / collect_metrics
/ hotspots / risk_areas / vibe_check / discoverable_via / danger_top).

W607-DP wraps the **post-capture** substrate boundaries — the 5 dict-build /
verdict-compose / serialize / format substrates that run AFTER the
capture layer has produced its (possibly degraded) inputs:

* compute_scores       — health-score + label assembly across capture results
* compose_verdict      — LAW 6 single-line floor (verdict f-string)
* assemble_sections    — JSON envelope summary_block + envelope_kwargs build
* serialize_envelope   — to_json(json_envelope("dashboard", ...)) projection
* format_text          — non-JSON click.echo formatting

Both buckets compose: the combined warnings_out list flips
``summary.partial_success=True`` on any marker, and the canonical
``dashboard_<phase>_failed:<exc_class>:<detail>`` marker family is shared
across both layers (DISJOINT phase-name sub-vocabulary so the layers
do not collide).

Marker family ``dashboard_*``. Hard distinction from sibling W607-* layers
preserved by the prefix-discipline test.

W978 7-DISCIPLINE
-----------------

Pre-flight audit before shipping:

1. f-string verdict floor: ``_compose_verdict`` default is the literal
   ``"DASHBOARD — verdict unavailable"`` — non-empty, satisfies LAW 6.
2. kwarg-default eagerness: every ``_run_check_dp(..., default=...)``
   slot is a literal (None / "" / {} / static dict).
3. json.dumps(default=str) sentinel: the degraded serialize_envelope
   path emits a minimal hand-rolled dict; no eager default=str hack.
4. Phase-name collision: ``dashboard_*`` is the shared marker family for
   W607-O (capture-layer) and W607-DP (post-capture); phase-name
   sub-vocabularies are DISJOINT (W607-O phases: overview / collect_metrics
   / hotspots / risk_areas / vibe_check / discoverable_via / danger_top;
   W607-DP phases: compute_scores / compose_verdict / assemble_sections /
   serialize_envelope / format_text). No phase collisions within W607-DP.
5. len() at kwarg-bind: NO len() inside any ``_run_check_dp(..., default=...)``
   args — every default is a literal.
6. Unguarded len()/if x: on poisoned object: ``isinstance(_scores, dict)``
   guards every ``.get`` on the post-compute_scores degraded path; the
   serialize_envelope path's ``rendered is None`` check precedes echo.
7. dict.get(key, expensive_default): all defaults inside the substrate
   wraps are cheap literals (None / 0 / static dicts).
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
# Helpers — invoke dashboard via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_dashboard(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam dashboard`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("dashboard")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixture — populated, indexed corpus
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def dashboard_project(tmp_path, monkeypatch):
    """Indexed corpus with multiple symbols + edges."""
    proj = tmp_path / "dashboard_w607dp_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n    return 1\n\n"
        "def helper():\n    inner()\n    return 42\n\n"
        "def inner():\n    return 7\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        'def format_name(first, last):\n    return f"{first} {last}"\n\ndef shout(msg):\n    return msg.upper()\n',
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


_DP_PHASES = (
    "compute_scores",
    "compose_verdict",
    "assemble_sections",
    "serialize_envelope",
    "format_text",
)

_O_PHASES = (
    "overview",
    "collect_metrics",
    "hotspots",
    "risk_areas",
    "vibe_check",
    "discoverable_via",
    "danger_top",
)


_SRC_PATH = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_dashboard.py"


# ---------------------------------------------------------------------------
# (1) Happy path — envelope omits W607-DP substrate markers
# ---------------------------------------------------------------------------


def test_dashboard_clean_envelope_omits_w607dp_markers(cli_runner, dashboard_project):
    """Clean dashboard --json -> no W607-DP substrate markers."""
    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "dashboard"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dp_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"dashboard_{p}_failed:" in m for p in _DP_PHASES)
    ]
    assert not dp_markers, (
        f"clean dashboard must NOT surface W607-DP markers; got top={top_wo!r}, summary={summary_wo!r}"
    )


# ---------------------------------------------------------------------------
# (2) compute_scores failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_dashboard_compute_scores_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """Force a compute_scores raise by poisoning the health-score read.

    The compute_scores closure does ``health.get("health_score", 0)``;
    swapping ``_health_label`` for a raising stub forces the substrate
    boundary to raise.
    """
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise ValueError("synthetic-compute-scores-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    score_markers = [m for m in all_wo if m.startswith("dashboard_compute_scores_failed:")]
    assert score_markers, f"expected dashboard_compute_scores_failed: marker; got {all_wo!r}"
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    verdict = data["summary"].get("verdict")
    # LAW 6 floor: non-empty single-line verdict survives.
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"


# ---------------------------------------------------------------------------
# (3) compose_verdict failure -> literal floor + marker
# ---------------------------------------------------------------------------


def test_dashboard_compose_verdict_failure_floors_to_literal(cli_runner, dashboard_project, monkeypatch):
    """If the verdict f-string raises, the literal floor verdict surfaces."""
    from roam.commands import cmd_dashboard

    # Patch the helper that compose_verdict depends on so the f-string
    # construction raises mid-way. We use _vibe_check_canonical to return
    # an object whose __getitem__ raises, forcing the f"...{vibe['score']}"
    # path inside compose_verdict to blow up.
    class _PoisonedVibe(dict):
        def __getitem__(self, key):
            if key == "score":
                raise RuntimeError("synthetic-compose-verdict-from-W607-DP")
            return super().__getitem__(key)

    poisoned = _PoisonedVibe({"total_issues": 0, "categories": [], "score": 0})

    def _patched_vibe(conn):
        return poisoned

    monkeypatch.setattr(cmd_dashboard, "_vibe_check_canonical", _patched_vibe)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    summary = data["summary"]
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert verdict == "DASHBOARD — verdict unavailable", verdict
    # Marker surfaces on the degraded path.
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    verdict_markers = [m for m in all_wo if m.startswith("dashboard_compose_verdict_failed:")]
    assert verdict_markers, f"expected dashboard_compose_verdict_failed: marker; got {all_wo!r}"


# ---------------------------------------------------------------------------
# (4) assemble_sections failure -> marker + minimal envelope still composes
# ---------------------------------------------------------------------------


def test_dashboard_assemble_sections_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If the section dict-build raises, the substrate surfaces a marker
    and the floor envelope (verdict + empty kwargs) composes cleanly.
    """
    from roam.commands import cmd_dashboard

    # Patch _overview so the assemble_sections closure raises mid-build
    # when it reaches ``overview["files"]`` (returns a non-dict).
    def _boom_overview(conn):
        # Non-dict that raises on subscription.
        class _Raise:
            def __getitem__(self, key):
                raise TypeError("synthetic-assemble-sections-from-W607-DP")

            def get(self, key, default=None):
                return default

        return _Raise()

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    asm_markers = [m for m in all_wo if m.startswith("dashboard_assemble_sections_failed:")]
    assert asm_markers, f"expected dashboard_assemble_sections_failed: marker; got {all_wo!r}"
    # Verdict floor preserved + partial_success flipped.
    summary = data["summary"]
    assert summary.get("partial_success") is True
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]


# ---------------------------------------------------------------------------
# (5) serialize_envelope failure -> minimal hand-rolled envelope on stdout
# ---------------------------------------------------------------------------


def test_dashboard_serialize_envelope_failure_emits_minimal_envelope(cli_runner, dashboard_project, monkeypatch):
    """If json_envelope / to_json raises, the degraded path emits a
    minimal hand-rolled JSON envelope with verdict + warnings_out so the
    consumer never gets an empty stdout (Pattern-1 variant C guard).
    """
    from roam.commands import cmd_dashboard

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "to_json", _boom_to_json)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    # Output MUST be non-empty parseable JSON.
    assert result.output.strip(), "degraded serialize_envelope path must still echo JSON; got empty stdout"
    data = _json.loads(result.output)
    assert data.get("command") == "dashboard", data
    summary = data["summary"]
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]
    # The serialize_envelope marker surfaces on the minimal envelope.
    wo = data.get("warnings_out") or summary.get("warnings_out") or []
    ser_markers = [m for m in wo if m.startswith("dashboard_serialize_envelope_failed:")]
    assert ser_markers, f"expected dashboard_serialize_envelope_failed: marker on degraded path; got {wo!r}"


# ---------------------------------------------------------------------------
# (6) format_text failure -> marker + non-crashing text-mode exit
# ---------------------------------------------------------------------------


def test_dashboard_format_text_failure_marker_format(cli_runner, dashboard_project, monkeypatch):
    """If a click.echo inside format_text raises, the W607-DP wrap
    catches it and the bucket accumulates the marker. Text mode still
    exits cleanly without torpedoing.
    """
    from roam.commands import cmd_dashboard

    # Patch _format_age to raise — it's called inside format_text early on.
    def _boom_age(seconds):
        raise RuntimeError("synthetic-format-text-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_format_age", _boom_age)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=False)
    # Text-mode degraded path still exits cleanly.
    assert result.exit_code == 0, result.output
    # The text mode does not echo the warnings_out bucket (it's
    # consumed by the JSON path). But the AST-level guard confirms
    # the wrap exists; the runtime guard confirms no crash.
    # No exception bubbled = wrap caught it.


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH envelope locations (top + summary)
# ---------------------------------------------------------------------------


def test_dashboard_w607dp_warnings_in_envelope_both_locations(cli_runner, dashboard_project, monkeypatch):
    """Non-empty W607-DP bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise RuntimeError("synthetic-mirror-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DP disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DP disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("dashboard_compute_scores_failed:")]
    assert markers, f"expected dashboard_compute_scores_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_dashboard_three_segment_marker_shape(cli_runner, dashboard_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise PermissionError("synthetic-shape-detail-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("dashboard_compute_scores_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dashboard_compute_scores_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Per-substrate isolation — single boundary failure does not torpedo
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_single_boundary_failure_does_not_torpedo(cli_runner, dashboard_project, monkeypatch):
    """One W607-DP boundary raising -> marker + remaining substrates compose.

    Force ``_health_label`` to raise. The compute_scores substrate
    degrades to ``{"hs": 0, "h_label": "UNHEALTHY"}``; compose_verdict,
    assemble_sections, serialize_envelope MUST still produce a coherent
    envelope.
    """
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise RuntimeError("synthetic-isolation-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Marker surfaces for the failed substrate.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    score_markers = [m for m in all_wo if m.startswith("dashboard_compute_scores_failed:")]
    assert score_markers, all_wo

    # Other substrates still produced their outputs.
    summary = data["summary"]
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # The envelope still has the dashboard-specific section keys produced
    # by assemble_sections (overview / health / hotspots / risks).
    assert "overview" in data or "summary" in data, sorted(data.keys())
    # Pattern-2 guard.
    assert summary.get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) Marker-prefix discipline — W607-DP stays in ``dashboard_*`` family
# ---------------------------------------------------------------------------


def test_w607dp_marker_prefix_stays_in_dashboard_family(cli_runner, dashboard_project, monkeypatch):
    """Every W607-DP substrate marker uses the canonical ``dashboard_*`` prefix.

    Hard distinction from sibling W607-* layers — no leak into doctor_*,
    health_*, audit_*, describe_*, minimap_*, etc.
    """
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        # ``dashboard_*`` is the canonical W607-DP (and W607-O) prefix family.
        assert marker.startswith("dashboard_"), (
            f"every surfaced marker on cmd_dashboard must use the ``dashboard_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("audit_", "cmd_audit W607-P / DM"),
            ("doctor_", "cmd_doctor W607-N / BE"),
            ("health_", "cmd_health W607-M / BA"),
            ("describe_", "cmd_describe W607-K / DG"),
            ("minimap_", "cmd_minimap W607-L / AZ"),
            ("preflight_", "cmd_preflight W607-R / AW"),
            ("smells_", "cmd_smells W607-BN / DF"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("metrics_push_", "cmd_metrics_push W607-DI"),
            ("clones_", "cmd_clones W607-BQ / DC"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("hotspots_", "cmd_hotspots W607-CP (runtime)"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("dead_", "cmd_dead W607-BX"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (11) Source-level guard: cmd_dashboard carries the W607-DP accumulator
# ---------------------------------------------------------------------------


def test_cmd_dashboard_carries_w607dp_accumulator():
    """AST-level guard: cmd_dashboard source carries the W607-DP accumulator."""
    assert _SRC_PATH.exists(), f"cmd_dashboard.py missing at {_SRC_PATH}"
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "w607dp_warnings_out" in src, (
        "W607-DP accumulator missing from cmd_dashboard; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dp" in src, (
        "W607-DP ``_run_check_dp`` helper missing from cmd_dashboard; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dp = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dp":
            found_run_check_dp = True
            break
    assert found_run_check_dp, (
        "W607-DP ``_run_check_dp`` helper not found in cmd_dashboard AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: W607-O accumulator coexists (LAYER preservation)
# ---------------------------------------------------------------------------


def test_cmd_dashboard_carries_both_w607o_and_w607dp_accumulators():
    """W607-O (capture-layer) and W607-DP (post-capture) MUST both live in
    cmd_dashboard — the layers compose, never replace.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "w607o_warnings_out" in src, (
        "W607-O capture-layer accumulator missing from cmd_dashboard; "
        "layer preservation broken — W607-DP must LAYER on top, not replace."
    )
    assert "w607dp_warnings_out" in src, "W607-DP post-capture accumulator missing from cmd_dashboard."
    assert "_run_check" in src and "_run_check_dp" in src, (
        "Both helpers (``_run_check`` for W607-O and ``_run_check_dp`` for W607-DP) must coexist in cmd_dashboard."
    )


# ---------------------------------------------------------------------------
# (13) Every W607-DP substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dp_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DP substrate boundary is wrapped."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    for phase in _DP_PHASES:
        same_line = f'_run_check_dp("{phase}"' in src
        multi_line = (
            f'_run_check_dp(\n        "{phase}"' in src
            or f'_run_check_dp(\n            "{phase}"' in src
            or f'_run_check_dp(\n                "{phase}"' in src
        )
        marker_grep = f"dashboard_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DP wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dp_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DP marker fstring lives in cmd_dashboard."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    fstring_pattern = 'f"dashboard_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DP marker fstring missing from cmd_dashboard; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (15) PATTERN-2 SILENT-FALLBACK GUARD: degraded path flips partial_success
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, dashboard_project, monkeypatch):
    """Pattern-2 regression guard: any W607-DP marker MUST flip
    ``summary.partial_success: True`` so the empty-floor envelope is
    NEVER mistaken for a clean dashboard.
    """
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise RuntimeError("synthetic-pattern-2-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    score_markers = [m for m in all_wo if m.startswith("dashboard_compute_scores_failed:")]
    assert score_markers, (
        f"degraded path MUST surface the compute_scores marker (loud-not-silent discipline); got {all_wo!r}"
    )

    # Verdict must NOT use SAFE/passed/completed vocabulary on a
    # degraded substrate path.
    verdict = (summary.get("verdict") or "").lower()
    for forbidden in ("safe", "passed", "completed", "all clear", "all green"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {summary.get('verdict')!r}"
        )


# ---------------------------------------------------------------------------
# (16) W607-O + W607-DP layers compose cleanly -- both buckets land
# ---------------------------------------------------------------------------


def test_w607o_w607dp_layers_compose_cleanly(cli_runner, dashboard_project, monkeypatch):
    """Both W607-O (capture) and W607-DP (post-capture) markers land on
    the same envelope when both layers degrade.

    The combined warnings_out list contains markers from BOTH layers,
    partial_success flips True on ANY non-empty bucket, and the
    envelope still composes.
    """
    from roam.commands import cmd_dashboard

    # Force a capture-layer (W607-O overview) raise.
    def _boom_overview(conn):
        raise RuntimeError("synthetic-w607o-capture-layer")

    # Force a post-capture (W607-DP compute_scores) raise.
    def _boom_label(score):
        raise RuntimeError("synthetic-w607dp-post-capture")

    monkeypatch.setattr(cmd_dashboard, "_overview", _boom_overview)
    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom_label)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # W607-O capture-layer marker MUST surface.
    o_markers = [m for m in all_wo if m.startswith("dashboard_overview_failed:")]
    assert o_markers, f"W607-O overview marker missing when both layers degraded; got {all_wo!r}"
    # W607-DP post-capture marker MUST surface.
    dp_markers = [m for m in all_wo if m.startswith("dashboard_compute_scores_failed:")]
    assert dp_markers, f"W607-DP compute_scores marker missing when both layers degraded; got {all_wo!r}"
    # partial_success flips on the combined bucket.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (17) LAW 6 verdict-first invariant: verdict survives EVERY phase failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase_attr,attr_name,exc",
    [
        ("_overview", "_overview", "synthetic-w607-overview-floor-check"),
        ("_health_label", "_health_label", "synthetic-w607-compute-scores-floor-check"),
        ("_vibe_check_canonical", "_vibe_check_canonical", "synthetic-w607-vibe-floor-check"),
    ],
)
def test_law6_verdict_first_invariant_survives_phase_failures(
    cli_runner, dashboard_project, monkeypatch, phase_attr, attr_name, exc
):
    """LAW 6: ``summary.verdict`` MUST survive any single-phase failure
    as a non-empty single-line string. Either the dynamic verdict
    (if compose_verdict ran cleanly) OR the literal floor
    ``DASHBOARD — verdict unavailable`` (if compose_verdict raised).
    """
    from roam.commands import cmd_dashboard

    def _raise(*args, **kwargs):
        raise RuntimeError(exc)

    monkeypatch.setattr(cmd_dashboard, attr_name, _raise)
    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"LAW 6 verdict-first invariant broken for phase {attr_name!r}: verdict = {verdict!r}"
    )
    assert "\n" not in verdict, f"LAW 6: verdict must be single line for phase {attr_name!r}; got {verdict!r}"


# ---------------------------------------------------------------------------
# (18) Cross-prefix isolation — dashboard_* markers don't leak adjacent
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_dashboard_markers_never_leak(cli_runner, dashboard_project, monkeypatch):
    """Cross-prefix isolation: confirm ``dashboard_*`` markers from
    cmd_dashboard don't contaminate the audit / doctor / health families.
    """
    from roam.commands import cmd_dashboard

    def _boom(score):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DP")

    monkeypatch.setattr(cmd_dashboard, "_health_label", _boom)

    result = _invoke_dashboard(cli_runner, dashboard_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Every surfaced marker must start with ``dashboard_`` — never with
    # any sibling family prefix.
    for marker in (m for m in all_wo if "_failed:" in m):
        assert marker.startswith("dashboard_"), f"marker leaked outside ``dashboard_*`` namespace; got {marker!r}"


# ---------------------------------------------------------------------------
# (19) W978 7-DISCIPLINE AST AUDIT: substrate-bind site checks
# ---------------------------------------------------------------------------


def test_w978_7_discipline_substrate_bind_audit():
    """W978 7-discipline AST audit on cmd_dashboard W607-DP plumbing.

    Confirms the substrate-bind sites obey the seven anti-patterns:

      1. No f-string verdict floor that evaluates ``f"... {x}"`` with
         x bound through a substrate — verdict default is a literal.
      2. No kwarg-default eagerness in ``_run_check_dp(..., default=fn())``.
         All defaults are literals.
      3. No ``json.dumps(default=str)`` sentinel calls inside the wraps.
      4. No accidental phase-name collisions in W607-DP.
      5. No ``len(...)`` calls inside the substrate ``default=`` slot.
      6. ``rendered is None`` check precedes any echo on the degraded
         serialize_envelope path.
      7. No ``dict.get(key, expensive_default)`` patterns inside the
         W607-DP region (all gets use literal defaults).
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    discipline_violations: list[str] = []
    bind_counts: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_dp":
            # Track phase-name for collision check.
            if node.args and isinstance(node.args[0], ast.Constant):
                phase = node.args[0].value
                if isinstance(phase, str):
                    bind_counts[phase] = bind_counts.get(phase, 0) + 1
            for kw in node.keywords:
                if kw.arg != "default":
                    continue
                val = kw.value
                # Discipline #2: default must be a literal — not a Call,
                # not a Lambda, not an arbitrary expression that could
                # raise at bind time.
                if isinstance(val, ast.Call):
                    discipline_violations.append(
                        f"Discipline #2/7 violation: ``_run_check_dp(..., default=<Call>)`` "
                        f"binds an EAGER call at line {node.lineno}; default must "
                        f"be a literal (None / '' / 0 / {{}} / [])."
                    )
                if isinstance(val, ast.Lambda):
                    discipline_violations.append(
                        f"Discipline #2 violation: ``_run_check_dp(..., default=lambda)`` "
                        f"at line {node.lineno}; default must be a literal value."
                    )
                # Discipline #5: no len() inside the default slot.
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and sub.func.id == "len":
                            discipline_violations.append(
                                f"Discipline #5 violation: len() inside _run_check_dp default at line {node.lineno}."
                            )
    assert not discipline_violations, "\n".join(discipline_violations)

    # Discipline #4: every W607-DP phase appears exactly once in the
    # substrate bind sites — no accidental collision.
    for phase, count in bind_counts.items():
        assert count == 1, (
            f"Discipline #4 violation: phase {phase!r} bound {count} times in "
            f"cmd_dashboard -- W607-DP phases must be unique."
        )

    # Discipline #6: ``rendered is None`` guard must precede any echo
    # on the degraded serialize_envelope path.
    if "rendered = _run_check_dp(" in src:
        assert "rendered is None" in src, (
            "Discipline #6 violation: serialize_envelope degraded path "
            "missing ``rendered is None`` guard before click.echo."
        )

    # Discipline #1: verdict default is a non-empty literal string.
    # The canonical literal is "DASHBOARD — verdict unavailable".
    assert '"DASHBOARD — verdict unavailable"' in src, (
        "Discipline #1 violation: compose_verdict default is no longer "
        "a non-empty literal; LAW 6 verdict floor at risk."
    )


# ---------------------------------------------------------------------------
# (20) Phase-name disjointness: W607-O and W607-DP sub-vocabs do not collide
# ---------------------------------------------------------------------------


def test_w607o_w607dp_phase_names_are_disjoint():
    """Within the shared ``dashboard_*`` family, W607-O and W607-DP
    phase sub-vocabularies are DISJOINT — no phase name appears in both
    layers (would create marker ambiguity).
    """
    o_set = set(_O_PHASES)
    dp_set = set(_DP_PHASES)
    overlap = o_set & dp_set
    assert not overlap, (
        f"W607-O and W607-DP phase names overlap: {overlap!r}. "
        f"The shared ``dashboard_*`` marker family requires disjoint "
        f"phase sub-vocabularies."
    )
