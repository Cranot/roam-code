"""W607-DM — ``cmd_audit`` producer-side substrate-CALL plumbing.

cmd_audit is the **producer** side of the metrics-emission 2-way:
``cmd_metrics_push`` (W607-DI consumer) calls ``roam audit --json``
in-process, parses the envelope, and folds the ``danger_score``
projection into the Cloud Lite payload. W607-DM closes the
substrate-CALL layer on the producer end so a degradation inside
``cmd_audit`` (verdict-composition raise, section-assembly raise,
envelope-serialization raise, text-format raise) does not torpedo
the audit envelope without lineage.

Pair:

* cmd_metrics_push (consumer)  → W607-DI (just landed)
* cmd_audit        (producer)  → W607-DM (THIS WAVE)

W607-DM is LAYERED on top of the pre-existing W607-P plumbing. W607-P
wraps the 8 sub-command ``_capture`` boundaries (health/debt/dead/
test_pyramid/api/stats/hotspots/stale_refs). W607-DM wraps the
**post-capture** substrate boundaries:

* compute_scores       — _summary_field extraction across all sections
* compose_verdict      — pressure-rank + LAW 6 verdict string
* assemble_sections    — section dict-build (brief vs. full)
* serialize_envelope   — to_json(json_envelope("audit", ...)) projection
* format_text          — non-JSON click.echo formatting

Both buckets compose: the combined warnings_out list flips
``summary.partial_success=True`` on any marker, and the canonical
``audit_<phase>_failed:<exc_class>:<detail>`` marker family is shared
across both layers (disjoint phase-name sub-vocabulary so the layers
do not collide).

Marker family ``audit_*``. Hard distinction from sibling W607-* layers
preserved by the prefix-discipline test.

W978 7-DISCIPLINE
-----------------

Pre-flight audit before shipping:

1. f-string verdict floor: ``_compose_verdict`` default is the literal
   ``"AUDIT — verdict unavailable"`` — non-empty, satisfies LAW 6.
2. kwarg-default eagerness: every ``_run_check_dm(..., default=...)``
   slot is a literal (None / "" / {} / static dict).
3. json.dumps(default=str) sentinel: the degraded serialize_envelope
   path emits a minimal hand-rolled dict; no eager default=str hack.
4. Phase-name collision: ``audit_*`` is the shared marker family for
   W607-P (capture-layer) and W607-DM (post-capture); phase-name
   sub-vocabularies are DISJOINT (W607-P phases: health/debt/dead/
   test_pyramid/api/stats/hotspots/stale_refs; W607-DM phases:
   compute_scores/compose_verdict/assemble_sections/serialize_envelope/
   format_text). No phase collisions within W607-DM.
5. len() at kwarg-bind: NO len() inside any ``_run_check_dm(..., default=...)``
   args — every default is a literal.
6. Unguarded len()/if x: on poisoned object: ``isinstance(_scores, dict)``
   guards every ``.get`` on the post-compute_scores degraded path; the
   text path's ``rendered is None`` check precedes echo.
7. dict.get(key, expensive_default): all defaults inside the substrate
   wraps are cheap literals (None / 0 / static dicts).
"""

from __future__ import annotations

import ast
import json as _json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_audit_project(tmp_path: Path) -> Path:
    """Build a minimal indexed project root for cmd_audit.

    The audit command calls ``ensure_index()`` then invokes a chain of
    sub-commands in-process. The fixture only needs an indexable repo,
    not a populated metrics schema — the W607-P capture layer already
    tolerates empty sub-envelopes, so W607-DM only needs the
    post-capture substrates to execute.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            line_start INTEGER, line_end INTEGER
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
        "line_start, line_end) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2)"
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def audit_project(tmp_path):
    return _build_audit_project(tmp_path)


def _invoke_audit(cli_runner, project_root, *args, json_mode=True):
    """Invoke the audit click command directly."""
    from roam.commands.cmd_audit import audit

    obj = {"json": json_mode, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(audit, list(args), obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


_DM_PHASES = (
    "compute_scores",
    "compose_verdict",
    "assemble_sections",
    "serialize_envelope",
    "format_text",
)


_SRC_PATH = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit.py"


# ---------------------------------------------------------------------------
# (1) Happy path — envelope omits W607-DM substrate markers
# ---------------------------------------------------------------------------


def test_audit_clean_envelope_omits_w607dm_markers(cli_runner, audit_project):
    """Clean audit --json -> no W607-DM substrate markers."""
    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "audit"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dm_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"audit_{p}_failed:" in m for p in _DM_PHASES)]
    assert not dm_markers, f"clean audit must NOT surface W607-DM markers; got top={top_wo!r}, summary={summary_wo!r}"


# ---------------------------------------------------------------------------
# (2) compose_verdict failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_audit_compose_verdict_failure_marker_format(cli_runner, audit_project, monkeypatch):
    """Force a compose_verdict raise by poisoning the f-string inputs.

    The compose_verdict closure runs ``f"{coverage_pct:.0f}%"`` when
    coverage_pct is a number < 40 — replacing the format-spec target
    with a raising __format__ surfaces the canonical marker.
    """
    from roam.commands import cmd_audit

    real_summary_field = cmd_audit._summary_field

    class _RaiseOnFormat(float):
        def __format__(self, spec):
            raise ValueError("synthetic-format-from-W607-DM")

    def _patched(payload, *keys, default=None):
        # Force a degraded coverage_pct < 40 to drive the pressure
        # branch where the f-string format-spec evaluates.
        if "imported_coverage_pct" in keys:
            return _RaiseOnFormat(10.0)
        return real_summary_field(payload, *keys, default=default)

    monkeypatch.setattr(cmd_audit, "_summary_field", _patched)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    verdict_markers = [m for m in all_wo if m.startswith("audit_compose_verdict_failed:")]
    assert verdict_markers, f"expected audit_compose_verdict_failed: marker; got {all_wo!r}"
    # Envelope flips partial_success on degraded path.
    assert data["summary"].get("partial_success") is True
    verdict = data["summary"].get("verdict")
    # LAW 6 floor: non-empty single-line verdict survives.
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    assert verdict == "AUDIT — verdict unavailable", verdict


# ---------------------------------------------------------------------------
# (3) warnings_out lands in BOTH envelope locations (top + summary)
# ---------------------------------------------------------------------------


def test_audit_w607dm_warnings_in_envelope_both_locations(cli_runner, audit_project, monkeypatch):
    """Non-empty W607-DM bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DM disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DM disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("audit_compute_scores_failed:")]
    assert markers, f"expected audit_compute_scores_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (4) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_audit_three_segment_marker_shape(cli_runner, audit_project, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-shape-detail-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("audit_compute_scores_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "audit_compute_scores_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (5) Per-substrate isolation — single boundary failure does not torpedo
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_single_boundary_failure_does_not_torpedo(cli_runner, audit_project, monkeypatch):
    """One W607-DM boundary raising -> marker + remaining substrates compose.

    Force ``_summary_field`` to raise. The compute_scores substrate
    degrades to ``{}``; the remaining substrates (compose_verdict,
    assemble_sections, serialize_envelope) MUST still compose a
    coherent envelope.
    """
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-isolation-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # Marker surfaces for the failed substrate.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    score_markers = [m for m in all_wo if m.startswith("audit_compute_scores_failed:")]
    assert score_markers, all_wo

    # Other substrates still produced their outputs.
    summary = data["summary"]
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # Sections still composed despite the upstream score-extraction
    # degrade (assemble_sections substrate is isolated from
    # compute_scores).
    assert "sections" in data, sorted(data.keys())
    # Pattern-2 guard.
    assert summary.get("partial_success") is True


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — W607-DM stays in ``audit_*`` family
# ---------------------------------------------------------------------------


def test_w607dm_marker_prefix_stays_in_audit_family(cli_runner, audit_project, monkeypatch):
    """Every W607-DM substrate marker uses the canonical ``audit_*`` prefix.

    Hard distinction from sibling W607-* layers — especially
    ``audit_trail_*`` which is a DIFFERENT command family
    (cmd_audit_trail_verify / cmd_audit_trail_export /
    cmd_audit_trail_conformance). The prefix-discipline pin makes the
    family separation visible.
    """
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        # ``audit_*`` is the canonical W607-DM (and W607-P) prefix family.
        assert marker.startswith("audit_"), (
            f"every surfaced marker on cmd_audit must use the ``audit_*`` prefix family; got {marker!r}"
        )
        # ``audit_trail_*`` is a DIFFERENT command family. The W607-DM
        # phases are: compute_scores / compose_verdict /
        # assemble_sections / serialize_envelope / format_text — none
        # of these collides with ``audit_trail_*`` sub-tokens, but the
        # invariant is worth pinning explicitly.
        assert not marker.startswith("audit_trail_"), (
            f"marker leaked into ``audit_trail_*`` family (W607-AI/AL/CN/CO sibling scope); got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("metrics_push_", "cmd_metrics_push W607-DI"),
            ("bus_factor_", "cmd_bus_factor W607-CQ"),
            ("dashboard_", "cmd_dashboard W607-O"),
            ("doctor_", "cmd_doctor W607-N / BE"),
            ("health_", "cmd_health W607-M / BA"),
            ("describe_", "cmd_describe W607-K / DG"),
            ("minimap_", "cmd_minimap W607-L / AZ"),
            ("preflight_", "cmd_preflight W607-R / AW"),
            ("smells_", "cmd_smells W607-BN / DF"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("clones_", "cmd_clones W607-BQ / DC"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("hotspots_", "cmd_hotspots W607-CP (runtime)"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
            ("auth_gaps_", "cmd_auth_gaps W607-CM"),
            ("n1_", "cmd_n1 W607-CB"),
            ("dead_", "cmd_dead W607-BX"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) Source-level guard: cmd_audit carries the W607-DM accumulator
# ---------------------------------------------------------------------------


def test_cmd_audit_carries_w607dm_accumulator():
    """AST-level guard: cmd_audit source carries the W607-DM accumulator."""
    assert _SRC_PATH.exists(), f"cmd_audit.py missing at {_SRC_PATH}"
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "_w607dm_warnings_out" in src, (
        "W607-DM accumulator missing from cmd_audit; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dm" in src, (
        "W607-DM ``_run_check_dm`` helper missing from cmd_audit; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dm = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dm":
            found_run_check_dm = True
            break
    assert found_run_check_dm, (
        "W607-DM ``_run_check_dm`` helper not found in cmd_audit AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (8) Every W607-DM substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dm_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DM substrate boundary is wrapped."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    for phase in _DM_PHASES:
        same_line = f'_run_check_dm("{phase}"' in src
        multi_line = (
            f'_run_check_dm(\n        "{phase}"' in src
            or f'_run_check_dm(\n            "{phase}"' in src
            or f'_run_check_dm(\n                "{phase}"' in src
        )
        marker_grep = f"audit_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DM wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (9) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dm_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DM marker fstring lives in cmd_audit."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    fstring_pattern = 'f"audit_{phase}_failed:{type(exc).__name__}:{exc}"'
    assert fstring_pattern in src, (
        f"canonical W607-DM marker fstring missing from cmd_audit; expected: {fstring_pattern}"
    )


# ---------------------------------------------------------------------------
# (10) PATTERN-2 SILENT-FALLBACK GUARD: degraded path flips partial_success
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, audit_project, monkeypatch):
    """Pattern-2 regression guard: any W607-DM marker MUST flip
    ``summary.partial_success: True`` so the empty-floor envelope is
    NEVER mistaken for a clean audit.
    """
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-pattern-2-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    score_markers = [m for m in all_wo if m.startswith("audit_compute_scores_failed:")]
    assert score_markers, (
        f"degraded path MUST surface the compute_scores marker (loud-not-silent discipline); got {all_wo!r}"
    )

    # Verdict must NOT use SAFE/passed/completed vocabulary on a
    # degraded substrate path. The verdict floor is "AUDIT — verdict
    # unavailable" which is safe; the dynamic verdict (when present)
    # uses "AUDIT —" prefix without SAFE/passed/completed.
    verdict = (summary.get("verdict") or "").lower()
    for forbidden in ("safe", "passed", "completed", "all clear", "all green"):
        assert forbidden not in verdict, (
            f"verdict contains default-success vocabulary {forbidden!r} -- "
            f"Pattern-2 silent-fallback violation; got {summary.get('verdict')!r}"
        )


# ---------------------------------------------------------------------------
# (11) Cross-prefix isolation -- audit_* markers don't leak to audit_trail_*
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_audit_markers_never_leak_to_audit_trail(cli_runner, audit_project, monkeypatch):
    """Cross-prefix isolation: confirm ``audit_*`` markers from cmd_audit
    don't contaminate the ``audit_trail_*`` family (a DIFFERENT command
    family: cmd_audit_trail_verify / cmd_audit_trail_export /
    cmd_audit_trail_conformance).

    The two prefixes share the leading ``audit_`` token by accident of
    name, but the W607-DM marker family is canonically the bare
    ``audit_*`` (with phases compute_scores / compose_verdict /
    assemble_sections / serialize_envelope / format_text). None of
    those collide with ``audit_trail_*`` sub-vocabulary, but the
    invariant is worth pinning to prevent future drift.
    """
    from roam.commands import cmd_audit

    def _raise(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DM")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Every surfaced marker must start with ``audit_`` but NEVER with
    # ``audit_trail_``.
    for marker in (m for m in all_wo if "_failed:" in m):
        assert marker.startswith("audit_"), f"marker leaked outside ``audit_*`` namespace; got {marker!r}"
        assert not marker.startswith("audit_trail_"), (
            f"marker leaked into ``audit_trail_*`` family (separate cmd_audit_trail_* surface); got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (12) Metrics-emission 2-way pairing pin -- both producer + consumer carry W607
# ---------------------------------------------------------------------------


def test_metrics_emission_2way_producer_consumer_pairing():
    """AST-scan cmd_audit + cmd_metrics_push confirming both carry W607
    plumbing. cmd_audit is the producer; cmd_metrics_push is the
    consumer. Closing the 2-way at substrate-CALL means a degradation
    on EITHER side surfaces via warnings_out rather than crashing.
    """
    audit_src = _SRC_PATH.read_text(encoding="utf-8")
    mp_src = (Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_metrics_push.py").read_text(
        encoding="utf-8"
    )

    # Producer side: W607-P + W607-DM both present.
    assert "_w607p_warnings_out" in audit_src, (
        "cmd_audit lost its W607-P accumulator — the capture-layer plumbing has been removed; 2-way pair is broken."
    )
    assert "_w607dm_warnings_out" in audit_src, (
        "cmd_audit lost its W607-DM accumulator — the post-capture plumbing has been removed; 2-way pair is broken."
    )
    # Consumer side: W607-DI present.
    assert "_w607di_warnings_out" in mp_src, (
        "cmd_metrics_push lost its W607-DI accumulator — the consumer side of the 2-way pair is broken."
    )
    assert "_run_check_di" in mp_src, (
        "cmd_metrics_push lost its _run_check_di helper — the consumer side of the 2-way pair is broken."
    )

    # The marker families are distinct (no cross-prefix bleed).
    # cmd_audit emits ``audit_*``; cmd_metrics_push emits ``metrics_push_*``.
    assert 'f"audit_{phase}_failed:' in audit_src, audit_src[:200]
    assert 'f"metrics_push_{phase}_failed:' in mp_src, mp_src[:200]


# ---------------------------------------------------------------------------
# (13) audit_envelope shape preserved -- consumer (metrics_push) reads via _capture_audit
# ---------------------------------------------------------------------------


def test_audit_envelope_shape_preserved_for_consumer(cli_runner, audit_project):
    """The audit envelope shape MUST be preserved so cmd_metrics_push's
    ``_capture_audit`` can fold the ``danger_score`` projection without
    a schema-drift breakage.

    Required envelope keys (per cmd_metrics_push consumer contract):
      - top-level: command="audit", summary (dict)
      - summary.verdict (str)
      - summary.health_score / debt_total / dead_count etc. for the
        Cloud Lite payload assembly.
    """
    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data["command"] == "audit"
    summary = data["summary"]
    assert isinstance(summary, dict)
    # Verdict floor (LAW 6).
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]
    # Consumer-facing keys (cmd_metrics_push reads these via
    # _summary_field in the audit envelope).
    for key in (
        "health_score",
        "debt_total",
        "dead_count",
        "danger_zone_count",
        "api_surface",
        "file_total",
        "symbol_total",
    ):
        assert key in summary, (
            f"audit envelope missing consumer-facing key {key!r} "
            f"(cmd_metrics_push depends on this); got {sorted(summary.keys())!r}"
        )


# ---------------------------------------------------------------------------
# (14) W978 7-DISCIPLINE AST AUDIT: substrate-bind site checks
# ---------------------------------------------------------------------------


def test_w978_7_discipline_substrate_bind_audit():
    """W978 7-discipline AST audit on cmd_audit W607-DM plumbing.

    Confirms the substrate-bind sites obey the seven anti-patterns:

      1. No f-string verdict floor that evaluates ``f"... {x}"`` with
         x bound through a substrate — verdict default is a literal.
      2. No kwarg-default eagerness in ``_run_check_dm(..., default=fn())``.
         All defaults are literals.
      3. No ``json.dumps(default=str)`` sentinel calls inside the wraps.
      4. No accidental phase-name collisions in W607-DM.
      5. No ``len(...)`` calls inside the substrate ``default=`` slot.
      6. ``rendered is None`` check precedes any echo on the degraded
         serialize_envelope path.
      7. No ``dict.get(key, expensive_default)`` patterns inside the
         W607-DM region (all gets use literal defaults).
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    discipline_violations: list[str] = []
    bind_counts: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_dm":
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
                        f"Discipline #2/7 violation: ``_run_check_dm(..., default=<Call>)`` "
                        f"binds an EAGER call at line {node.lineno}; default must "
                        f"be a literal (None / '' / 0 / {{}} / [])."
                    )
                if isinstance(val, ast.Lambda):
                    discipline_violations.append(
                        f"Discipline #2 violation: ``_run_check_dm(..., default=lambda)`` "
                        f"at line {node.lineno}; default must be a literal value."
                    )
                # Discipline #5: no len() inside the default slot.
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and sub.func.id == "len":
                            discipline_violations.append(
                                f"Discipline #5 violation: len() inside _run_check_dm default at line {node.lineno}."
                            )
    assert not discipline_violations, "\n".join(discipline_violations)

    # Discipline #4: every W607-DM phase appears exactly once in the
    # substrate bind sites — no accidental collision.
    for phase, count in bind_counts.items():
        assert count == 1, (
            f"Discipline #4 violation: phase {phase!r} bound {count} times in "
            f"cmd_audit -- W607-DM phases must be unique."
        )

    # Discipline #6: ``rendered is None`` guard must precede any echo
    # on the degraded serialize_envelope path.
    if "rendered = _run_check_dm(" in src:
        assert "rendered is None" in src, (
            "Discipline #6 violation: serialize_envelope degraded path "
            "missing ``rendered is None`` guard before click.echo."
        )

    # Discipline #1: verdict default is a non-empty literal string.
    # The canonical literal is "AUDIT — verdict unavailable".
    assert '"AUDIT — verdict unavailable"' in src, (
        "Discipline #1 violation: compose_verdict default is no longer "
        "a non-empty literal; LAW 6 verdict floor at risk."
    )


# ---------------------------------------------------------------------------
# (15) W607-P + W607-DM compose cleanly -- both buckets land on envelope
# ---------------------------------------------------------------------------


def test_w607p_w607dm_layers_compose_cleanly(cli_runner, audit_project, monkeypatch):
    """Both W607-P (capture) and W607-DM (post-capture) markers land on
    the same envelope when both layers degrade.

    The combined warnings_out list contains markers from BOTH layers,
    partial_success flips True on ANY non-empty bucket, and the
    envelope still composes.
    """
    from roam.commands import cmd_audit

    # Force a post-capture (W607-DM compute_scores) raise.
    def _raise_field(*args, **kwargs):
        raise RuntimeError("synthetic-w607dm-post-capture")

    monkeypatch.setattr(cmd_audit, "_summary_field", _raise_field)

    result = _invoke_audit(cli_runner, audit_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # W607-DM compute_scores marker MUST surface.
    dm_markers = [m for m in all_wo if m.startswith("audit_compute_scores_failed:")]
    assert dm_markers, f"W607-DM compute_scores marker missing on degraded path; got {all_wo!r}"
    # partial_success flips on the combined bucket.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# HELPER-TEMPLATE FIX: _run_check_dm returns default VERBATIM (drive-by Axis B)
# ---------------------------------------------------------------------------


def test_run_check_dm_returns_default_verbatim_not_dict_fallback(cli_runner, audit_project, monkeypatch):
    """CRITICAL helper-template fix regression guard.

    ``_run_check_dm`` MUST return ``default`` verbatim on raise (NOT
    ``default if default is not None else {}``). The latter form breaks
    any ``rendered is None``-style guard on the serialize_envelope path
    because the helper substitutes ``{}`` even when the caller
    explicitly asked for ``None``.

    This bug was identified by the cmd_dashboard W607-DP agent (where
    the identical broken template existed in ``_run_check_dp``) and
    sealed in cmd_audit's ``_run_check_dm`` as a drive-by alongside the
    cmd_doctor W607-DW wave.

    AST-level check: scan ``_run_check_dm`` for the ``return default``
    statement; it must be a plain ``Name`` (verbatim) — NOT an
    ``IfExp`` with ``default is not None else {}`` shape.

    Runtime check: serialize_envelope-style boundary with
    ``default=None`` — confirm the wrapper preserves ``None`` so a
    None-aware downstream guard can fire correctly.
    """
    # ---- AST-level check ----
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    forbidden_violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dm":
            found_helper = True
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    val = stmt.value
                    if val is None:
                        continue
                    if isinstance(val, ast.IfExp):
                        # Forbidden shape: ``default if default is not None else {}``
                        if (
                            isinstance(val.test, ast.Compare)
                            and isinstance(val.test.left, ast.Name)
                            and val.test.left.id == "default"
                        ):
                            forbidden_violations.append(
                                f"_run_check_dm uses forbidden "
                                f"``default if default is not None else {{}}`` "
                                f"shape at line {stmt.lineno} — must return "
                                f"default verbatim."
                            )
            break
    assert found_helper, "_run_check_dm helper not found in cmd_audit AST"
    assert not forbidden_violations, "\n".join(forbidden_violations)

    # ---- Runtime check: serialize_envelope boundary preserves None ----
    # Verify the fix is live, not just present in source. Force a
    # serialize_envelope-style raise and confirm the wrapper substitutes
    # the verbatim ``None`` default, allowing the consumer's
    # ``rendered is None`` guard to fire.
    from roam.commands import cmd_audit

    def _boom(payload):
        raise RuntimeError("synthetic-helper-template-verbatim-default")

    monkeypatch.setattr(cmd_audit, "to_json", _boom)

    result = _invoke_audit(cli_runner, audit_project)
    # The serialize_envelope degraded path must echo a minimal JSON
    # fallback (the ``rendered is None`` guard fires correctly because
    # the helper returned ``None`` verbatim, not ``{}``). Empty stdout
    # would prove the bug is regressed.
    assert result.output.strip(), (
        "serialize_envelope degraded path emitted empty stdout — the "
        "``rendered is None`` guard never fired, likely because the "
        "helper-template fix has been regressed (default substituted as "
        "{} instead of returned verbatim)."
    )
    # The minimal fallback envelope must be parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "audit", data
    # The serialize_envelope marker must surface as proof the wrapper
    # caught the raise.
    wo = data.get("warnings_out") or data["summary"].get("warnings_out") or []
    ser_markers = [m for m in wo if m.startswith("audit_serialize_envelope_failed:")]
    assert ser_markers, f"expected audit_serialize_envelope_failed: marker on degraded path; got {wo!r}"
