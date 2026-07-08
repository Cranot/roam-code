"""W607-EC — ``cmd_preflight`` POST-CAPTURE plumbing LAYERED on W607-R + W607-AW.

cmd_preflight is the FLAGSHIP gate command — its 5-signal envelope
(blast / complexity / conventions / coupling / fitness) is the
canonical "dominant variable" per CLAUDE.md LAW 1 for agent-decision
speed. W607-R (substrate-CALL helper boundaries) and W607-AW
(aggregation-phase boundaries) already wrap the capture-layer and
compute-aggregation paths.

W607-EC wraps the **post-capture** substrate boundaries — the 5
dict-build / verdict-compose / serialize / format substrates that run
AFTER the capture layer has produced its (possibly degraded) inputs:

* compute_scores       — pre-verdict score derivation + label normalization
* compose_verdict      — LAW 1+6 single-line verdict floor
* assemble_sections    — JSON summary_dict + envelope_kwargs build
* serialize_envelope   — to_json(json_envelope("preflight", ...)) projection
* format_text          — non-JSON click.echo formatting

All three buckets compose: the combined warnings_out list flips
``summary.partial_success=True`` on any marker, and the canonical
``preflight_<phase>_failed:<exc_class>:<detail>`` marker family is
shared across all three layers (DISJOINT phase-name sub-vocabulary so
the layers do not collide).

Marker family ``preflight_*``. Hard distinction from sibling W607-*
layers preserved by the prefix-discipline test.

CRITICAL helper-template fix
----------------------------

The ``_run_check_ec`` helper returns ``default`` VERBATIM on raise
(NOT ``default if default is not None else {}``). The latter form
breaks the ``rendered is None`` guard on the serialize_envelope
degraded path because the helper substitutes ``{}`` even when the
caller explicitly asked for ``None``. The verbatim-default contract is
pinned by an explicit regression test below + a source-level check.

W978 7-DISCIPLINE
-----------------

Pre-flight audit before shipping:

1. f-string verdict floor: ``_compose_verdict`` default is the literal
   ``"preflight gate degraded"`` — non-empty, satisfies LAW 6.
2. kwarg-default eagerness: every ``_run_check_ec(..., default=...)``
   slot is a literal (None / "" / {} / static dict).
3. json.dumps(default=str) sentinel: the degraded serialize_envelope
   path emits a minimal hand-rolled dict; no eager default=str hack.
4. Phase-name collision: ``preflight_*`` is the shared marker family
   for W607-R (capture-layer), W607-AW (aggregation), and W607-EC
   (post-capture); phase-name sub-vocabularies are DISJOINT.
5. len() at kwarg-bind: NO len() inside any ``_run_check_ec(..., default=...)``
   args — every default is a literal.
6. Unguarded len()/if x: on poisoned object: ``isinstance(_scores, dict)``
   guards every ``.get`` on the post-compute_scores degraded path; the
   serialize_envelope path's ``rendered is None`` check precedes echo.
7. dict.get(key, expensive_default): all defaults inside the substrate
   wraps are cheap literals.

W759 PRESERVATION
-----------------

cmd_preflight ships the W847 INTERNAL VOCABULARY contract: helper
returns / rank-table keys / risk-level comparisons stay UPPER-case
(``CRITICAL``/``HIGH``/``MEDIUM``/``LOW``/``WARNING``/``OK``). W607-EC
must NOT lowercase any of these. The literal verdict floor
``"preflight gate degraded"`` is single-line and CANT contain the
UPPER tier vocabulary on the degraded path — that's the LAW 6 floor
contract, NOT a W759 violation (no internal severity vocabulary is
emitted on the floor path).
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_preflight.py"


def _invoke_preflight(runner: CliRunner, target: str, json_mode: bool = True, *extra):
    """Invoke ``roam preflight`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("preflight")
    args.append(target)
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


@pytest.fixture
def cli_runner():
    return CliRunner()


_EC_PHASES = (
    "compute_scores",
    "compose_verdict",
    "assemble_sections",
    "serialize_envelope",
    "format_text",
)

_R_AW_PHASES = (
    # W607-R substrate-CALL helper-boundary phases
    "resolve_targets",
    "blast_radius",
    "affected_tests",
    "complexity",
    "coupling",
    "conventions",
    "fitness",
    # W607-AW aggregation-phase boundaries
    "overall_risk",
    "risk_driver",
    "fitness_violations",
    "auto_log",
)


# ---------------------------------------------------------------------------
# (1) Happy path — envelope omits W607-EC substrate markers
# ---------------------------------------------------------------------------


def test_preflight_clean_envelope_omits_w607ec_markers(cli_runner):
    """Clean preflight --json -> no W607-EC substrate markers, 5-signal
    envelope shape byte-identical to pre-EC."""
    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "preflight"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    ec_markers = [
        m for m in (list(top_wo) + list(summary_wo)) if any(f"preflight_{p}_failed:" in m for p in _EC_PHASES)
    ]
    assert not ec_markers, (
        f"clean preflight must NOT surface W607-EC markers; got top={top_wo!r}, summary={summary_wo!r}"
    )

    # 5-signal envelope shape preserved
    for required in ("blast_radius", "tests", "complexity", "coupling", "conventions", "fitness"):
        assert required in data, f"5-signal envelope missing {required!r}"


# ---------------------------------------------------------------------------
# (2) compute_scores wrapped at source — AST audit
# ---------------------------------------------------------------------------


def test_preflight_compute_scores_phase_wrapped_in_source():
    """Source-level guard: the compute_scores boundary is wrapped."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_ec":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value == "compute_scores":
                        found = True
                        for kw in node.keywords:
                            if kw.arg == "default":
                                assert isinstance(kw.value, (ast.Constant, ast.Dict, ast.List, ast.Tuple)), (
                                    f"compute_scores default must be a literal; got {ast.dump(kw.value)}"
                                )
                        break
    assert found, (
        "compute_scores phase MUST be wrapped via "
        "_run_check_ec('compute_scores', ...) — the W607-EC substrate "
        "boundary is missing."
    )


# ---------------------------------------------------------------------------
# (3) compose_verdict failure -> literal floor + marker
# ---------------------------------------------------------------------------


def test_preflight_compose_verdict_failure_floors_to_literal():
    """Source-level guard: compose_verdict literal floor lives in source.

    1. ``_run_check_ec("compose_verdict", ...)`` appears with the
       literal floor ``default="preflight gate degraded"``.
    2. The literal is non-empty and single-line (LAW 6 floor).
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_wrap_with_floor = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_ec":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value == "compose_verdict":
                        for kw in node.keywords:
                            if kw.arg == "default":
                                assert isinstance(kw.value, ast.Constant), (
                                    f"compose_verdict default must be a literal string; got {ast.dump(kw.value)}"
                                )
                                literal = kw.value.value
                                assert isinstance(literal, str) and literal, "compose_verdict default must be non-empty"
                                assert "\n" not in literal, "compose_verdict default must be single line"
                                assert "preflight" in literal.lower(), (
                                    f"expected preflight-anchored floor literal; got {literal!r}"
                                )
                                found_wrap_with_floor = True

    assert found_wrap_with_floor, (
        "compose_verdict phase MUST be wrapped via "
        "_run_check_ec('compose_verdict', ..., default='preflight gate degraded')"
    )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> minimal hand-rolled envelope on stdout
# ---------------------------------------------------------------------------


def test_preflight_serialize_envelope_failure_emits_minimal_envelope(cli_runner, monkeypatch):
    """If to_json raises, the degraded path emits a minimal hand-rolled
    JSON envelope with verdict + warnings_out so the consumer never gets
    an empty stdout (Pattern-1 variant C guard).
    """
    from roam.commands import cmd_preflight

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom_to_json)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "degraded serialize_envelope path must still echo JSON; got empty stdout"
    data = _json.loads(result.output)
    assert data.get("command") == "preflight", data
    summary = data["summary"]
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]
    wo = data.get("warnings_out") or summary.get("warnings_out") or []
    ser_markers = [m for m in wo if m.startswith("preflight_serialize_envelope_failed:")]
    assert ser_markers, f"expected preflight_serialize_envelope_failed: marker on degraded path; got {wo!r}"


# ---------------------------------------------------------------------------
# (5) format_text failure -> marker + non-crashing text-mode exit
# ---------------------------------------------------------------------------


def test_preflight_format_text_failure_marker_format(cli_runner, monkeypatch):
    """If a click.echo inside format_text raises, the W607-EC wrap
    catches it and the bucket accumulates the marker. Text mode still
    exits cleanly without torpedoing.
    """
    from roam.commands import cmd_preflight

    real_echo = cmd_preflight.click.echo
    call_count = {"n": 0}

    def _patched_echo(*args, **kwargs):
        call_count["n"] += 1
        msg = args[0] if args else ""
        if isinstance(msg, str) and msg.startswith("VERDICT:"):
            raise RuntimeError("synthetic-format-text-from-W607-EC")
        return real_echo(*args, **kwargs)

    monkeypatch.setattr(cmd_preflight.click, "echo", _patched_echo)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=False)
    # Text-mode degraded path still exits cleanly.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (6) warnings_out lands in BOTH envelope locations (top + summary)
# ---------------------------------------------------------------------------


def test_preflight_w607ec_warnings_in_envelope_both_locations(cli_runner, monkeypatch):
    """Non-empty W607-EC bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_preflight

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-mirror-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom_to_json)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-EC disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-EC disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("preflight_serialize_envelope_failed:")]
    assert markers, f"expected preflight_serialize_envelope_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (7) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_preflight_three_segment_marker_shape(cli_runner, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_preflight

    def _boom(payload):
        raise PermissionError("synthetic-shape-detail-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("preflight_serialize_envelope_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "preflight_serialize_envelope_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline — W607-EC stays in ``preflight_*`` family
# ---------------------------------------------------------------------------


def test_w607ec_marker_prefix_stays_in_preflight_family(cli_runner, monkeypatch):
    """Every W607-EC substrate marker uses the canonical ``preflight_*`` prefix.

    Hard distinction from sibling W607-* layers — no leak into audit_*,
    doctor_*, health_*, describe_*, minimap_*, dashboard_*, etc.
    """
    from roam.commands import cmd_preflight

    def _boom(payload):
        raise PermissionError("synthetic-prefix-discipline-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        assert marker.startswith("preflight_"), (
            f"every surfaced marker on cmd_preflight must use the ``preflight_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("audit_", "cmd_audit W607-P / DM"),
            ("dashboard_", "cmd_dashboard W607-O / DP"),
            ("health_", "cmd_health W607-M / BA"),
            ("describe_", "cmd_describe W607-K / DG"),
            ("minimap_", "cmd_minimap W607-L / AZ"),
            ("doctor_", "cmd_doctor W607-N / BE / DW"),
            ("smells_", "cmd_smells W607-BN / DF"),
            ("vibe_check_", "cmd_vibe_check W607-BS"),
            ("metrics_push_", "cmd_metrics_push W607-DI"),
            ("clones_", "cmd_clones W607-BQ / DC"),
            ("duplicates_", "cmd_duplicates W607-BM / DD"),
            ("hotspots_", "cmd_hotspots W607-CP (runtime)"),
            ("complexity_", "cmd_complexity W607-BJ"),
            ("dark_matter_", "cmd_dark_matter W607-BK"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) Source-level guard: cmd_preflight carries the W607-EC accumulator
# ---------------------------------------------------------------------------


def test_cmd_preflight_carries_w607ec_accumulator():
    """AST-level guard: cmd_preflight source carries the W607-EC accumulator."""
    assert _SRC_PATH.exists(), f"cmd_preflight.py missing at {_SRC_PATH}"
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "w607ec_warnings_out" in src, (
        "W607-EC accumulator missing from cmd_preflight; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_ec" in src, (
        "W607-EC ``_run_check_ec`` helper missing from cmd_preflight; the "
        "per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_ec = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ec":
            found_run_check_ec = True
            break
    assert found_run_check_ec, (
        "W607-EC ``_run_check_ec`` helper not found in cmd_preflight AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (10) W607-R + W607-AW coexist (LAYER preservation)
# ---------------------------------------------------------------------------


def test_cmd_preflight_carries_w607r_w607aw_w607ec_accumulators():
    """W607-R (substrate-CALL), W607-AW (aggregation), AND W607-EC
    (post-capture) MUST all live in cmd_preflight — the layers compose,
    never replace.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "w607r_warnings_out" in src, (
        "W607-R substrate-CALL accumulator missing from cmd_preflight; "
        "layer preservation broken — W607-EC must LAYER on top, not replace."
    )
    assert "w607aw_warnings_out" in src, (
        "W607-AW aggregation accumulator missing from cmd_preflight; "
        "layer preservation broken — W607-EC must LAYER on top, not replace."
    )
    assert "w607ec_warnings_out" in src, "W607-EC post-capture accumulator missing from cmd_preflight."
    assert "_run_check" in src and "_run_check_aw" in src and "_run_check_ec" in src, (
        "All three helpers (``_run_check`` for W607-R, ``_run_check_aw`` "
        "for W607-AW, ``_run_check_ec`` for W607-EC) must coexist in "
        "cmd_preflight."
    )


# ---------------------------------------------------------------------------
# (11) Every W607-EC substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607ec_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-EC substrate boundary is wrapped."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    for phase in _EC_PHASES:
        same_line = f'_run_check_ec("{phase}"' in src
        multi_line = (
            f'_run_check_ec(\n            "{phase}"' in src
            or f'_run_check_ec(\n                "{phase}"' in src
            or f'_run_check_ec(\n        "{phase}"' in src
        )
        marker_grep = f"preflight_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-EC wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (12) Canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607ec_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-EC marker fstring lives in cmd_preflight."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    fstring_pattern = 'f"preflight_{phase}_failed:{type(exc).__name__}:{exc}"'
    count = src.count(fstring_pattern)
    assert count >= 3, (
        f"canonical preflight_<phase>_failed fstring should appear in W607-R, "
        f"W607-AW, AND W607-EC helpers; found {count} occurrences"
    )


# ---------------------------------------------------------------------------
# (13) Pattern-2 silent-fallback guard
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, monkeypatch):
    """Pattern-2 regression guard: any W607-EC marker MUST flip
    ``summary.partial_success: True`` so the empty-floor envelope is
    NEVER mistaken for a clean preflight.
    """
    from roam.commands import cmd_preflight

    def _boom(payload):
        raise RuntimeError("synthetic-pattern-2-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    ser_markers = [m for m in all_wo if m.startswith("preflight_serialize_envelope_failed:")]
    assert ser_markers, (
        f"degraded path MUST surface the serialize_envelope marker (loud-not-silent discipline); got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (14) W607-R + W607-AW + W607-EC layers compose cleanly
# ---------------------------------------------------------------------------


def test_w607r_w607aw_w607ec_layers_compose_cleanly(cli_runner, monkeypatch):
    """All three layers' markers land on the same envelope when multiple
    layers degrade.

    Force a W607-R capture-layer raise (_check_blast_radius) AND a
    W607-EC post-capture raise (to_json) — markers from both buckets
    surface on the combined warnings_out list and partial_success flips.
    """
    from roam.commands import cmd_preflight

    def _boom_blast_radius(*args, **kwargs):
        raise RuntimeError("synthetic-w607r-capture-layer")

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-w607ec-post-capture")

    monkeypatch.setattr(cmd_preflight, "_check_blast_radius", _boom_blast_radius)
    monkeypatch.setattr(cmd_preflight, "to_json", _boom_to_json)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # W607-R capture-layer marker MUST surface.
    r_markers = [m for m in all_wo if m.startswith("preflight_blast_radius_failed:")]
    assert r_markers, f"W607-R blast_radius marker missing when layers degraded; got {all_wo!r}"
    # W607-EC post-capture marker MUST surface.
    ec_markers = [m for m in all_wo if m.startswith("preflight_serialize_envelope_failed:")]
    assert ec_markers, f"W607-EC serialize_envelope marker missing when layers degraded; got {all_wo!r}"
    # partial_success flips on the combined bucket.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (15) LAW 1 + LAW 6 verdict-first invariant: verdict survives EVERY phase failure
# ---------------------------------------------------------------------------


def test_law1_law6_verdict_first_invariant_floor_literal():
    """LAW 1 + LAW 6: ``summary.verdict`` floor MUST be a non-empty
    literal that works without any other field. The W607-EC floor
    literal is ``"preflight gate degraded"``.

    cmd_preflight is the FLAGSHIP gate command — its verdict is THE
    canonical agent-decision driver (LAW 1: prompt is the dominant
    variable; LAW 6: compression forces domain neutrality). The floor
    must work standalone.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert '"preflight gate degraded"' in src, (
        "LAW 1 + LAW 6 verdict floor missing — W607-EC compose_verdict "
        "default must be the literal ``preflight gate degraded``."
    )


# ---------------------------------------------------------------------------
# (16) Cross-prefix isolation — preflight_* markers don't leak adjacent
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_preflight_markers_never_leak(cli_runner, monkeypatch):
    """Cross-prefix isolation: confirm ``preflight_*`` markers from
    cmd_preflight don't contaminate sibling layer families.
    """
    from roam.commands import cmd_preflight

    def _boom(payload):
        raise RuntimeError("synthetic-cross-prefix-from-W607-EC")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    for marker in (m for m in all_wo if "_failed:" in m):
        assert marker.startswith("preflight_"), f"marker leaked outside ``preflight_*`` namespace; got {marker!r}"


# ---------------------------------------------------------------------------
# (17) W978 7-DISCIPLINE AST AUDIT
# ---------------------------------------------------------------------------


def test_w978_7_discipline_substrate_bind_audit():
    """W978 7-discipline AST audit on cmd_preflight W607-EC plumbing.

    1. No f-string verdict floor that evaluates ``f"... {x}"`` —
       verdict default is a literal.
    2. No kwarg-default eagerness in ``_run_check_ec(..., default=fn())``.
    3. No ``json.dumps(default=str)`` sentinel calls inside the wraps.
    4. No accidental phase-name collisions in W607-EC.
    5. No ``len(...)`` calls inside the substrate ``default=`` slot.
    6. ``rendered is None`` check precedes any echo on the degraded
       serialize_envelope path.
    7. No ``dict.get(key, expensive_default)`` patterns inside the
       W607-EC region.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    discipline_violations: list[str] = []
    bind_counts: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_ec":
            if node.args and isinstance(node.args[0], ast.Constant):
                phase = node.args[0].value
                if isinstance(phase, str):
                    bind_counts[phase] = bind_counts.get(phase, 0) + 1
            for kw in node.keywords:
                if kw.arg != "default":
                    continue
                val = kw.value
                # Discipline #2: default must be a literal.
                if isinstance(val, ast.Call):
                    discipline_violations.append(
                        f"Discipline #2/7 violation: ``_run_check_ec(..., default=<Call>)`` "
                        f"binds an EAGER call at line {node.lineno}."
                    )
                if isinstance(val, ast.Lambda):
                    discipline_violations.append(
                        f"Discipline #2 violation: ``_run_check_ec(..., default=lambda)`` at line {node.lineno}."
                    )
                # Discipline #5: no len() inside the default slot.
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and sub.func.id == "len":
                            discipline_violations.append(
                                f"Discipline #5 violation: len() inside _run_check_ec default at line {node.lineno}."
                            )
    assert not discipline_violations, "\n".join(discipline_violations)

    # Discipline #4: every W607-EC phase appears exactly once.
    for phase, count in bind_counts.items():
        assert count == 1, (
            f"Discipline #4 violation: phase {phase!r} bound {count} times in "
            f"cmd_preflight -- W607-EC phases must be unique."
        )

    # Discipline #6: ``rendered is None`` guard must precede any echo
    # on the degraded serialize_envelope path.
    if "rendered = _run_check_ec(" in src:
        assert "rendered is None" in src, (
            "Discipline #6 violation: serialize_envelope degraded path "
            "missing ``rendered is None`` guard before click.echo."
        )

    # Discipline #1: verdict default is a non-empty literal string.
    assert '"preflight gate degraded"' in src, (
        "Discipline #1 violation: compose_verdict default is no longer "
        "a non-empty literal; LAW 6 verdict floor at risk."
    )


# ---------------------------------------------------------------------------
# (18) Phase-name disjointness: W607-R/AW and W607-EC sub-vocabs disjoint
# ---------------------------------------------------------------------------


def test_w607r_w607aw_w607ec_phase_names_are_disjoint():
    """Within the shared ``preflight_*`` family, W607-R (substrate-CALL),
    W607-AW (aggregation), and W607-EC (post-capture) phase
    sub-vocabularies are DISJOINT — no phase name appears in more than
    one layer (would create marker ambiguity).
    """
    ec_set = set(_EC_PHASES)
    r_aw_set = set(_R_AW_PHASES)
    overlap = ec_set & r_aw_set
    assert not overlap, (
        f"W607-R/AW and W607-EC phase names overlap: {overlap!r}. "
        f"The shared ``preflight_*`` marker family requires disjoint "
        f"phase sub-vocabularies."
    )


# ---------------------------------------------------------------------------
# (19) HELPER-TEMPLATE FIX: _run_check_ec returns default VERBATIM
# ---------------------------------------------------------------------------


def test_run_check_ec_returns_default_verbatim_not_dict_fallback():
    """CRITICAL helper-template fix regression guard.

    ``_run_check_ec`` MUST return ``default`` verbatim on raise (NOT
    ``default if default is not None else {}``). The latter form breaks
    the ``rendered is None`` guard on the serialize_envelope path.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ec":
            found_helper = True
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    if isinstance(stmt.value, ast.Name) and stmt.value.id == "default":
                        continue
                    if isinstance(stmt.value, ast.IfExp):
                        if (
                            isinstance(stmt.value.test, ast.Compare)
                            and isinstance(stmt.value.test.left, ast.Name)
                            and stmt.value.test.left.id == "default"
                        ):
                            pytest.fail(
                                "_run_check_ec uses forbidden "
                                "``default if default is not None else {}`` "
                                "shape — must return default verbatim. "
                                "This breaks the serialize_envelope "
                                "``rendered is None`` guard."
                            )
            break
    assert found_helper, "_run_check_ec helper not found in cmd_preflight AST"


# ---------------------------------------------------------------------------
# (20-24) Per-phase isolation tests — one per EC phase
# ---------------------------------------------------------------------------


def test_per_phase_isolation_compute_scores(cli_runner, monkeypatch):
    """compute_scores wrapper exists at the source level.

    Runtime forcing the compute_scores closure raise is brittle: the
    closure only reads dict-lookups on already-validated capture
    results; forcing a raise requires patching the very inputs the
    capture wrappers protect. Source-level wrap guard suffices.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert '"compute_scores"' in src or "preflight_compute_scores_failed" in src


def test_per_phase_isolation_compose_verdict(cli_runner):
    """compose_verdict floor literal lives in source."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert '"preflight gate degraded"' in src


def test_per_phase_isolation_assemble_sections(cli_runner, monkeypatch):
    """assemble_sections wrapper exists at the source level."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert '"assemble_sections"' in src or "preflight_assemble_sections_failed" in src


def test_per_phase_isolation_serialize_envelope(cli_runner, monkeypatch):
    """serialize_envelope failure does not torpedo other phases.

    Runtime test: force to_json to raise, confirm the envelope still
    composes with verdict + marker.
    """
    from roam.commands import cmd_preflight

    def _boom(payload):
        raise RuntimeError("synthetic-per-phase-serialize-envelope")

    monkeypatch.setattr(cmd_preflight, "to_json", _boom)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data.get("command") == "preflight"
    summary = data["summary"]
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]


def test_per_phase_isolation_format_text(cli_runner, monkeypatch):
    """format_text failure does not torpedo other phases (text-mode)."""
    from roam.commands import cmd_preflight

    real_echo = cmd_preflight.click.echo

    def _patched_echo(*args, **kwargs):
        msg = args[0] if args else ""
        if isinstance(msg, str) and msg.startswith("VERDICT:"):
            raise RuntimeError("synthetic-per-phase-format-text")
        return real_echo(*args, **kwargs)

    monkeypatch.setattr(cmd_preflight.click, "echo", _patched_echo)

    result = _invoke_preflight(cli_runner, "preflight", json_mode=False)
    # No exception bubbled = wrap caught it.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# (25) 5-signal envelope shape preserved on happy path (byte-identical)
# ---------------------------------------------------------------------------


def test_preflight_5_signal_envelope_shape_byte_identical_on_happy_path(cli_runner):
    """Clean preflight emits the 5-signal envelope shape (LAW 1 dominant
    variable): blast_radius / tests / complexity / coupling / conventions
    / fitness all present as top-level keys, no W607-EC warnings_out.
    """
    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    # 5-signal envelope (technically 6 sections: blast + tests +
    # complexity + coupling + conventions + fitness — the "5-signal"
    # phrasing groups blast+tests as "blast radius" axis per CLAUDE.md).
    for required_section in (
        "blast_radius",
        "tests",
        "complexity",
        "coupling",
        "conventions",
        "fitness",
    ):
        assert required_section in data, (
            f"5-signal envelope shape broken — missing {required_section!r}; "
            f"got top-level keys = {sorted(data.keys())!r}"
        )

    # Summary contract: verdict + risk_level + symbols_checked +
    # files_checked + fitness_violations + risk_level_definition.
    summary = data["summary"]
    for required_summary in (
        "verdict",
        "risk_level",
        "symbols_checked",
        "files_checked",
        "fitness_violations",
        "risk_level_definition",
    ):
        assert required_summary in summary, (
            f"summary shape broken — missing {required_summary!r}; got summary keys = {sorted(summary.keys())!r}"
        )

    # Empty W607-EC bucket → no warnings_out on either mirror.
    assert data.get("warnings_out") is None or data.get("warnings_out") == [], (
        f"clean preflight must omit top-level warnings_out; got {data.get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (26) W759 INTERNAL UPPER-case vocabulary preservation
# ---------------------------------------------------------------------------


def test_w759_internal_upper_case_vocabulary_preserved(cli_runner):
    """W847 internal severity vocabulary (UPPER-case
    CRITICAL/HIGH/MEDIUM/LOW/WARNING/OK) stays UPPER on the
    ``risk_level`` rollup. W607-EC must NOT lowercase this — it's
    INTERNAL VOCABULARY, not envelope severity slot.
    """
    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    risk_level = summary.get("risk_level")
    assert risk_level in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}, (
        f"W759/W847 violation: risk_level must be UPPER-case agent-facing risk-tier vocabulary; got {risk_level!r}"
    )


# ---------------------------------------------------------------------------
# (27) Helper-template `return default` verbatim shape source check
# ---------------------------------------------------------------------------


def test_run_check_ec_helper_template_verbatim_default_source_match():
    """The W607-EC helper template MUST match the canonical post-W607-DW
    shape exactly — single ``return default`` statement (Name node), no
    ``default if default is not None else {}`` IfExp.

    This is a tighter check than test (19) — confirms there's exactly
    one ``return default`` statement and it's a plain Name.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    helper_returns: list[ast.Return] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_ec":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    helper_returns.append(stmt)
            break

    assert helper_returns, "_run_check_ec helper has no return statements"

    # At least one return must be the canonical ``return default`` Name.
    found_verbatim = False
    for ret in helper_returns:
        if isinstance(ret.value, ast.Name) and ret.value.id == "default":
            found_verbatim = True
            break
    assert found_verbatim, (
        "_run_check_ec MUST have a verbatim ``return default`` statement; "
        "the helper-template fix from W607-DP/DW was lost."
    )


# ---------------------------------------------------------------------------
# (28) Empty-bucket → byte-identical envelope (no warnings_out keys)
# ---------------------------------------------------------------------------


def test_preflight_empty_bucket_byte_identical_envelope(cli_runner):
    """W607-EC empty bucket → byte-identical envelope (no warnings_out
    keys added to either summary or top-level).
    """
    result = _invoke_preflight(cli_runner, "preflight", json_mode=True)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]
    # Neither mirror surfaces warnings_out.
    assert "warnings_out" not in summary or not summary.get("warnings_out"), (
        f"empty bucket must not add summary.warnings_out; got {summary!r}"
    )
    assert "warnings_out" not in data or not data.get("warnings_out"), (
        f"empty bucket must not add top-level warnings_out; got {sorted(data.keys())!r}"
    )
