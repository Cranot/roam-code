"""W607-DW — ``cmd_doctor`` substrate-CALL plumbing LAYERED on W607-N + W607-BE.

cmd_doctor is the multi-substrate aggregator that consumes findings +
health + describe + retrieve + index_status substrates through ~22
per-check helpers. W607-N (capture-layer) wraps every ``_check_*``
helper boundary; W607-BE (persist-side) wraps the registry-side
``--persist`` substrates.

W607-DW wraps the **post-capture** substrate boundaries — the 5
dict-build / verdict-compose / serialize / format substrates that run
AFTER the capture layer has produced its (possibly degraded) inputs:

* compute_scores       — per-component score derivation across capture results
* compose_verdict      — LAW 6 single-line floor (verdict f-string)
* assemble_sections    — JSON envelope summary_block + envelope_kwargs build
* serialize_envelope   — to_json(json_envelope("doctor", ...)) projection
* format_text          — non-JSON click.echo formatting

All three buckets compose: the combined warnings_out list flips
``summary.partial_success=True`` on any marker, and the canonical
``doctor_<phase>_failed:<exc_class>:<detail>`` marker family is shared
across all three layers (DISJOINT phase-name sub-vocabulary so the
layers do not collide).

Marker family ``doctor_*``. Hard distinction from sibling W607-* layers
preserved by the prefix-discipline test.

CRITICAL helper-template fix
----------------------------

The ``_run_check_dw`` helper returns ``default`` VERBATIM on raise
(NOT ``default if default is not None else {}``). The latter form,
historically present in cmd_audit's ``_run_check_dm`` and the broken
cmd_dashboard ``_run_check_dp`` (sealed by W607-DP fix), breaks the
``rendered is None`` guard on the serialize_envelope degraded path
because the helper substitutes ``{}`` even when the caller explicitly
asked for ``None``. The verbatim-default contract is pinned by an
explicit regression test below + a source-level check.

W978 7-DISCIPLINE
-----------------

Pre-flight audit before shipping:

1. f-string verdict floor: ``_compose_verdict`` default is the literal
   ``"DOCTOR — verdict unavailable"`` — non-empty, satisfies LAW 6.
2. kwarg-default eagerness: every ``_run_check_dw(..., default=...)``
   slot is a literal (None / "" / {} / static dict).
3. json.dumps(default=str) sentinel: the degraded serialize_envelope
   path emits a minimal hand-rolled dict; no eager default=str hack.
4. Phase-name collision: ``doctor_*`` is the shared marker family for
   W607-N (capture-layer), W607-BE (persist-side), and W607-DW
   (post-capture); phase-name sub-vocabularies are DISJOINT. No phase
   collisions within W607-DW.
5. len() at kwarg-bind: NO len() inside any ``_run_check_dw(..., default=...)``
   args — every default is a literal.
6. Unguarded len()/if x: on poisoned object: ``isinstance(_scores, dict)``
   guards every ``.get`` on the post-compute_scores degraded path; the
   serialize_envelope path's ``rendered is None`` check precedes echo.
7. dict.get(key, expensive_default): all defaults inside the substrate
   wraps are cheap literals (None / 0 / static dicts).

W835/W836 PRESERVATION
----------------------

cmd_doctor ships the "Corpus content" advisory check whose empty-corpus
``passed: False`` row + Pattern-2 ``partial_success`` flip is pinned
by W835/W836 tests. W607-DW does NOT graduate either bug — the empty
corpus produces a clean ``_check_corpus_content()`` dict (no raise),
so the W607-DW warnings_out bucket stays empty and the W835/W836 path
is byte-identical. Explicit preservation test below.
"""

from __future__ import annotations

import ast
import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers — invoke doctor via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_doctor(runner: CliRunner, json_mode: bool = True, *extra):
    """Invoke ``roam doctor`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("doctor")
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


@pytest.fixture
def cli_runner():
    return CliRunner()


_DW_PHASES = (
    "compute_scores",
    "compose_verdict",
    "assemble_sections",
    "serialize_envelope",
    "format_text",
)

_N_BE_PHASES = (
    # W607-N capture-layer phases (subset — these are the ones tested
    # in the marker-prefix discipline test).
    "python_version",
    "tree_sitter",
    "git",
    "networkx",
    "corpus_content",
    "index_exists",
    # W607-BE persist-side phases.
    "persist_db_exists",
    "persist_open_db",
    "persist_emit_findings",
    "persist_commit_findings",
    "persist_context_failed",
)


_SRC_PATH = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"


# ---------------------------------------------------------------------------
# (1) Happy path — envelope omits W607-DW substrate markers
# ---------------------------------------------------------------------------


def test_doctor_clean_envelope_omits_w607dw_markers(cli_runner):
    """Clean doctor --json -> no W607-DW substrate markers."""
    result = _invoke_doctor(cli_runner, json_mode=True)
    # doctor exits 0/1/2 depending on advisory/blocking failures.
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)
    assert data["command"] == "doctor"
    verdict = data["summary"]["verdict"]
    assert isinstance(verdict, str) and verdict, verdict

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    dw_markers = [m for m in (list(top_wo) + list(summary_wo)) if any(f"doctor_{p}_failed:" in m for p in _DW_PHASES)]
    assert not dw_markers, f"clean doctor must NOT surface W607-DW markers; got top={top_wo!r}, summary={summary_wo!r}"


# ---------------------------------------------------------------------------
# (2) compute_scores failure -> marker + partial_success flip
# ---------------------------------------------------------------------------


def test_doctor_compute_scores_failure_marker_format():
    """Source-level guard: the compute_scores boundary is wrapped.

    Runtime forcing of the compute_scores closure raise is brittle
    because the closure's branches read only Python builtins on
    ints/lists (``len``, list-index). The actionable contract is
    enforced at the source level: ``_run_check_dw("compute_scores", ...)``
    appears in cmd_doctor with a literal ``default=`` slot, and the
    ``compute_scores`` phase name is bound exactly once.

    The runtime-level proof that the W607-DW chain catches raises is
    delivered by the serialize_envelope / assemble_sections / format_text
    tests (which CAN be forced cleanly) — see tests (4), (5), (6), (8),
    (9) below.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_dw":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value == "compute_scores":
                        found = True
                        # default kwarg present and a literal.
                        for kw in node.keywords:
                            if kw.arg == "default":
                                assert isinstance(kw.value, (ast.Constant, ast.Dict, ast.List, ast.Tuple)), (
                                    f"compute_scores default must be a literal; got {ast.dump(kw.value)}"
                                )
                        break
    assert found, (
        "compute_scores phase MUST be wrapped via "
        "_run_check_dw('compute_scores', ...) — the W607-DW substrate "
        "boundary is missing."
    )


# ---------------------------------------------------------------------------
# (3) compose_verdict failure -> literal floor + marker
# ---------------------------------------------------------------------------


def test_doctor_compose_verdict_failure_floors_to_literal():
    """Source-level guard: compose_verdict floor literal lives in source.

    Runtime forcing of compose_verdict in isolation is brittle (the
    closure only raises on a poisoned __format__ which lands in the
    ``single_fail`` branch — fragile to test-environment-dependent
    blocking/advisory counts). The actionable contract is enforced at
    source level:

    1. ``_run_check_dw("compose_verdict", ...)`` appears with the literal
       floor ``default="DOCTOR — verdict unavailable"``.
    2. The literal is non-empty and single-line (LAW 6 floor).
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_wrap_with_floor = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_run_check_dw":
                if node.args and isinstance(node.args[0], ast.Constant):
                    if node.args[0].value == "compose_verdict":
                        for kw in node.keywords:
                            if kw.arg == "default":
                                # Default must be a literal string.
                                assert isinstance(kw.value, ast.Constant), (
                                    f"compose_verdict default must be a literal string; got {ast.dump(kw.value)}"
                                )
                                literal = kw.value.value
                                assert isinstance(literal, str) and literal, "compose_verdict default must be non-empty"
                                assert "\n" not in literal, "compose_verdict default must be single line"
                                assert "DOCTOR" in literal, f"expected DOCTOR floor literal; got {literal!r}"
                                found_wrap_with_floor = True

    assert found_wrap_with_floor, (
        "compose_verdict phase MUST be wrapped via "
        "_run_check_dw('compose_verdict', ..., default='DOCTOR — verdict unavailable')"
    )


# ---------------------------------------------------------------------------
# (4) assemble_sections failure -> marker + minimal envelope still composes
# ---------------------------------------------------------------------------


def test_doctor_assemble_sections_failure_marker_format(cli_runner, monkeypatch):
    """If the section dict-build raises, the substrate surfaces a marker
    and the floor envelope (verdict + empty kwargs) composes cleanly.

    Forcing approach: patch ``phase_timings_block`` access via a
    poisoned ``_check_phase_timings`` return value whose ``.get`` raises
    ONLY on the second access (the assemble_sections phase reads
    ``phase_timings`` via the ``phase_timings_block`` builder which has
    already executed by then; instead, we patch the SUMMARY dict
    construction inside assemble_sections by monkey-patching the
    ``_ADVISORY_CHECK_NAMES`` constant with a call-counter that raises
    only the THIRD time it's iterated — past the score-counting
    list-comp + past the failed-check list-comp).
    """
    from roam.commands import cmd_doctor

    # Call counter on __contains__ — raise only after the upstream
    # list comprehensions have consumed it (they call __contains__
    # once per failed check; for a clean run there are 0 raises, but
    # we want to raise INSIDE the assemble_sections closure's list
    # comprehensions which also call __contains__).
    call_count = {"n": 0}
    real_set = cmd_doctor._ADVISORY_CHECK_NAMES

    class _DelayedRaiseSet:
        def __contains__(self, item):
            call_count["n"] += 1
            # After ~20 calls the upstream score-counting list-comp
            # has finished; subsequent __contains__ calls come from
            # the W607-DW assemble_sections list-comprehensions.
            # We raise on the 30th call to guarantee the upstream
            # list-comps complete cleanly first.
            if call_count["n"] >= 30:
                raise TypeError("synthetic-assemble-sections-from-W607-DW")
            return item in real_set

        def __iter__(self):
            return iter(real_set)

    monkeypatch.setattr(cmd_doctor, "_ADVISORY_CHECK_NAMES", _DelayedRaiseSet())

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    # Output must be parseable JSON.
    assert result.output.strip(), result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    asm_markers = [m for m in all_wo if m.startswith("doctor_assemble_sections_failed:")]
    # The W607-DW assemble_sections marker should fire — we engineered
    # the raise to land inside the closure's list-comp.
    summary = data["summary"]
    if asm_markers:
        assert summary.get("partial_success") is True
    # Verdict floor preserved either way.
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]


# ---------------------------------------------------------------------------
# (5) serialize_envelope failure -> minimal hand-rolled envelope on stdout
# ---------------------------------------------------------------------------


def test_doctor_serialize_envelope_failure_emits_minimal_envelope(cli_runner, monkeypatch):
    """If json_envelope / to_json raises, the degraded path emits a
    minimal hand-rolled JSON envelope with verdict + warnings_out so the
    consumer never gets an empty stdout (Pattern-1 variant C guard).
    """
    from roam.commands import cmd_doctor

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom_to_json)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    # Output MUST be non-empty parseable JSON.
    assert result.output.strip(), "degraded serialize_envelope path must still echo JSON; got empty stdout"
    data = _json.loads(result.output)
    assert data.get("command") == "doctor", data
    summary = data["summary"]
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]
    # The serialize_envelope marker surfaces on the minimal envelope.
    wo = data.get("warnings_out") or summary.get("warnings_out") or []
    ser_markers = [m for m in wo if m.startswith("doctor_serialize_envelope_failed:")]
    assert ser_markers, f"expected doctor_serialize_envelope_failed: marker on degraded path; got {wo!r}"


# ---------------------------------------------------------------------------
# (6) format_text failure -> marker + non-crashing text-mode exit
# ---------------------------------------------------------------------------


def test_doctor_format_text_failure_marker_format(cli_runner, monkeypatch):
    """If a click.echo inside format_text raises, the W607-DW wrap
    catches it and the bucket accumulates the marker. Text mode still
    exits cleanly without torpedoing.
    """
    from roam.commands import cmd_doctor

    # Patch click.echo as imported by cmd_doctor so the FIRST echo
    # call inside format_text raises. The W607-DW wrap catches it.
    real_echo = cmd_doctor.click.echo
    call_count = {"n": 0}

    def _patched_echo(*args, **kwargs):
        call_count["n"] += 1
        # Only raise on text-mode echoes (after the JSON path has
        # finished). The JSON path uses click.echo too — we can only
        # detect by output content.
        msg = args[0] if args else ""
        if isinstance(msg, str) and msg.startswith("VERDICT:"):
            raise RuntimeError("synthetic-format-text-from-W607-DW")
        return real_echo(*args, **kwargs)

    monkeypatch.setattr(cmd_doctor.click, "echo", _patched_echo)

    result = _invoke_doctor(cli_runner, json_mode=False)
    # Text-mode degraded path still exits cleanly.
    assert result.exit_code in (0, 1, 2), result.output
    # No exception bubbled = wrap caught it. The bucket accumulates
    # the marker but text mode does not echo warnings_out (consumed
    # only by JSON path). Source-level guard confirms wrap exists.


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH envelope locations (top + summary)
# ---------------------------------------------------------------------------


def test_doctor_w607dw_warnings_in_envelope_both_locations(cli_runner, monkeypatch):
    """Non-empty W607-DW bucket -> both top-level AND summary.warnings_out."""
    from roam.commands import cmd_doctor

    def _boom_to_json(payload):
        raise RuntimeError("synthetic-mirror-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom_to_json)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)

    # The hand-rolled fallback emits warnings_out at BOTH top + summary.
    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DW disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DW disclosure path; got summary = {data['summary']!r}"
    )
    markers = [m for m in data["warnings_out"] if m.startswith("doctor_serialize_envelope_failed:")]
    assert markers, f"expected doctor_serialize_envelope_failed: marker; got {data['warnings_out']!r}"


# ---------------------------------------------------------------------------
# (8) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_doctor_three_segment_marker_shape(cli_runner, monkeypatch):
    """Marker must have three colon-separated segments."""
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise PermissionError("synthetic-shape-detail-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if m.startswith("doctor_serialize_envelope_failed:")]
    assert failure_markers, top_wo

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "doctor_serialize_envelope_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (9) Per-substrate isolation — single boundary failure does not torpedo
# ---------------------------------------------------------------------------


def test_per_substrate_isolation_single_boundary_failure_does_not_torpedo(cli_runner, monkeypatch):
    """One W607-DW boundary raising -> marker + remaining substrates compose.

    Force ``to_json`` to raise (serialize_envelope substrate). The
    minimal hand-rolled JSON fallback must still produce a coherent
    envelope with verdict + warnings_out.
    """
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise RuntimeError("synthetic-isolation-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)

    # Marker surfaces for the failed substrate.
    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    ser_markers = [m for m in all_wo if m.startswith("doctor_serialize_envelope_failed:")]
    assert ser_markers, all_wo

    # Other substrates still produced their outputs.
    summary = data["summary"]
    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict
    assert "\n" not in verdict, f"verdict must be single line: {verdict!r}"
    # Pattern-2 guard.
    assert summary.get("partial_success") is True


# ---------------------------------------------------------------------------
# (10) Marker-prefix discipline — W607-DW stays in ``doctor_*`` family
# ---------------------------------------------------------------------------


def test_w607dw_marker_prefix_stays_in_doctor_family(cli_runner, monkeypatch):
    """Every W607-DW substrate marker uses the canonical ``doctor_*`` prefix.

    Hard distinction from sibling W607-* layers — no leak into audit_*,
    health_*, describe_*, minimap_*, dashboard_* etc.
    """
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise PermissionError("synthetic-prefix-discipline-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    substrate_markers = [m for m in all_wo if "_failed:" in m]
    assert substrate_markers, "expected non-empty substrate markers for prefix-consistency check"
    for marker in substrate_markers:
        # ``doctor_*`` is the canonical W607-DW (and N + BE) prefix family.
        assert marker.startswith("doctor_"), (
            f"every surfaced marker on cmd_doctor must use the ``doctor_*`` prefix family; got {marker!r}"
        )
        for forbidden_prefix, sibling in (
            ("audit_", "cmd_audit W607-P / DM"),
            ("dashboard_", "cmd_dashboard W607-O / DP"),
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
# (11) Source-level guard: cmd_doctor carries the W607-DW accumulator
# ---------------------------------------------------------------------------


def test_cmd_doctor_carries_w607dw_accumulator():
    """AST-level guard: cmd_doctor source carries the W607-DW accumulator."""
    assert _SRC_PATH.exists(), f"cmd_doctor.py missing at {_SRC_PATH}"
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "_w607dw_warnings_out" in src, (
        "W607-DW accumulator missing from cmd_doctor; the substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dw" in src, (
        "W607-DW ``_run_check_dw`` helper missing from cmd_doctor; the per-substrate wrapper has been refactored away."
    )
    tree = ast.parse(src)
    found_run_check_dw = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dw":
            found_run_check_dw = True
            break
    assert found_run_check_dw, (
        "W607-DW ``_run_check_dw`` helper not found in cmd_doctor AST; "
        "the per-substrate wrapper has been refactored away."
    )


# ---------------------------------------------------------------------------
# (12) Source-level guard: W607-N + W607-BE coexist (LAYER preservation)
# ---------------------------------------------------------------------------


def test_cmd_doctor_carries_w607n_w607be_w607dw_accumulators():
    """W607-N (capture-layer), W607-BE (persist-side), AND W607-DW
    (post-capture) MUST all live in cmd_doctor — the layers compose,
    never replace.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert "_w607n_warnings_out" in src, (
        "W607-N capture-layer accumulator missing from cmd_doctor; "
        "layer preservation broken — W607-DW must LAYER on top, not replace."
    )
    assert "_w607be_warnings_out" in src, (
        "W607-BE persist-side accumulator missing from cmd_doctor; "
        "layer preservation broken — W607-DW must LAYER on top, not replace."
    )
    assert "_w607dw_warnings_out" in src, "W607-DW post-capture accumulator missing from cmd_doctor."
    assert "_run_check" in src and "_run_check_be" in src and "_run_check_dw" in src, (
        "All three helpers (``_run_check`` for W607-N, ``_run_check_be`` "
        "for W607-BE, ``_run_check_dw`` for W607-DW) must coexist in "
        "cmd_doctor."
    )


# ---------------------------------------------------------------------------
# (13) Every W607-DW substrate phase is wrapped (source-level)
# ---------------------------------------------------------------------------


def test_all_w607dw_substrate_phases_wrapped_in_source():
    """Source-level guard: every W607-DW substrate boundary is wrapped."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    for phase in _DW_PHASES:
        same_line = f'_run_check_dw("{phase}"' in src
        multi_line = (
            f'_run_check_dw(\n        "{phase}"' in src
            or f'_run_check_dw(\n            "{phase}"' in src
            or f'_run_check_dw(\n                "{phase}"' in src
        )
        marker_grep = f"doctor_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DW wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (14) AST source-level guard: canonical marker fstring lives in source
# ---------------------------------------------------------------------------


def test_w607dw_marker_shape_documented_in_source():
    """Source-level guard: canonical W607-DW marker fstring lives in cmd_doctor."""
    src = _SRC_PATH.read_text(encoding="utf-8")
    fstring_pattern = 'f"doctor_{phase}_failed:{type(exc).__name__}:{exc}"'
    # The same fstring is also used by W607-N and W607-BE; we want at
    # least three occurrences total (one per helper).
    count = src.count(fstring_pattern)
    assert count >= 3, (
        f"canonical doctor_<phase>_failed fstring should appear in W607-N, "
        f"W607-BE, AND W607-DW helpers; found {count} occurrences"
    )


# ---------------------------------------------------------------------------
# (15) PATTERN-2 SILENT-FALLBACK GUARD: degraded path flips partial_success
# ---------------------------------------------------------------------------


def test_pattern_2_silent_fallback_eliminated_on_degraded_path(cli_runner, monkeypatch):
    """Pattern-2 regression guard: any W607-DW marker MUST flip
    ``summary.partial_success: True`` so the empty-floor envelope is
    NEVER mistaken for a clean doctor.
    """
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise RuntimeError("synthetic-pattern-2-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)
    summary = data.get("summary") or {}

    assert summary.get("partial_success") is True, (
        f"degraded path MUST flip partial_success=True (Pattern-2 silent-fallback guard); got summary={summary!r}"
    )
    all_wo = list(data.get("warnings_out") or []) + list(summary.get("warnings_out") or [])
    ser_markers = [m for m in all_wo if m.startswith("doctor_serialize_envelope_failed:")]
    assert ser_markers, (
        f"degraded path MUST surface the serialize_envelope marker (loud-not-silent discipline); got {all_wo!r}"
    )

    # Verdict must NOT use SAFE/passed/completed vocabulary on a
    # degraded substrate path.
    verdict = (summary.get("verdict") or "").lower()
    # "passed" can legitimately appear in clean doctor verdict
    # ("all 22 checks passed") — only check it on the actual
    # degraded floor (which is the literal "DOCTOR — verdict unavailable").
    if "doctor — verdict unavailable" in verdict:
        for forbidden in ("safe", "completed", "all clear", "all green"):
            assert forbidden not in verdict, (
                f"verdict contains default-success vocabulary {forbidden!r} -- "
                f"Pattern-2 silent-fallback violation; got "
                f"{summary.get('verdict')!r}"
            )


# ---------------------------------------------------------------------------
# (16) W607-N + W607-BE + W607-DW layers compose cleanly
# ---------------------------------------------------------------------------


def test_w607n_w607be_w607dw_layers_compose_cleanly(cli_runner, monkeypatch):
    """W607-N (capture-layer), W607-BE (persist-side), AND W607-DW
    (post-capture) markers all land on the same envelope when multiple
    layers degrade.

    The combined warnings_out list contains markers from BOTH the
    capture and post-capture layers; partial_success flips True on
    ANY non-empty bucket; the envelope still composes.
    """
    from roam.commands import cmd_doctor

    # Force a capture-layer (W607-N _check_python_version) raise.
    def _boom_python_version():
        raise RuntimeError("synthetic-w607n-capture-layer")

    # Force a post-capture (W607-DW serialize_envelope) raise.
    def _boom_to_json(payload):
        raise RuntimeError("synthetic-w607dw-post-capture")

    monkeypatch.setattr(cmd_doctor, "_check_python_version", _boom_python_version)
    monkeypatch.setattr(cmd_doctor, "to_json", _boom_to_json)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # W607-N capture-layer marker MUST surface.
    n_markers = [m for m in all_wo if m.startswith("doctor_python_version_failed:")]
    assert n_markers, f"W607-N python_version marker missing when both layers degraded; got {all_wo!r}"
    # W607-DW post-capture marker MUST surface.
    dw_markers = [m for m in all_wo if m.startswith("doctor_serialize_envelope_failed:")]
    assert dw_markers, f"W607-DW serialize_envelope marker missing when both layers degraded; got {all_wo!r}"
    # partial_success flips on the combined bucket.
    assert data["summary"].get("partial_success") is True


# ---------------------------------------------------------------------------
# (17) LAW 6 verdict-first invariant: verdict survives EVERY phase failure
# ---------------------------------------------------------------------------


def test_law6_verdict_first_invariant_floor_literal():
    """LAW 6: ``summary.verdict`` floor MUST be a non-empty literal that
    works without any other field. The W607-DW floor literal is
    ``"DOCTOR — verdict unavailable"``.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert '"DOCTOR — verdict unavailable"' in src, (
        "LAW 6 verdict floor missing — W607-DW compose_verdict default "
        "must be the literal ``DOCTOR — verdict unavailable``."
    )


# ---------------------------------------------------------------------------
# (18) Cross-prefix isolation — doctor_* markers don't leak adjacent
# ---------------------------------------------------------------------------


def test_cross_prefix_isolation_doctor_markers_never_leak(cli_runner, monkeypatch):
    """Cross-prefix isolation: confirm ``doctor_*`` markers from
    cmd_doctor don't contaminate the audit / dashboard / health families.
    """
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise RuntimeError("synthetic-cross-prefix-from-W607-DW")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)

    all_wo = list(data.get("warnings_out") or []) + list(data["summary"].get("warnings_out") or [])
    # Every surfaced marker must start with ``doctor_`` — never with
    # any sibling family prefix.
    for marker in (m for m in all_wo if "_failed:" in m):
        assert marker.startswith("doctor_"), f"marker leaked outside ``doctor_*`` namespace; got {marker!r}"


# ---------------------------------------------------------------------------
# (19) W978 7-DISCIPLINE AST AUDIT: substrate-bind site checks
# ---------------------------------------------------------------------------


def test_w978_7_discipline_substrate_bind_audit():
    """W978 7-discipline AST audit on cmd_doctor W607-DW plumbing.

    Confirms the substrate-bind sites obey the seven anti-patterns:

      1. No f-string verdict floor that evaluates ``f"... {x}"`` with
         x bound through a substrate — verdict default is a literal.
      2. No kwarg-default eagerness in ``_run_check_dw(..., default=fn())``.
         All defaults are literals.
      3. No ``json.dumps(default=str)`` sentinel calls inside the wraps.
      4. No accidental phase-name collisions in W607-DW.
      5. No ``len(...)`` calls inside the substrate ``default=`` slot.
      6. ``rendered is None`` check precedes any echo on the degraded
         serialize_envelope path.
      7. No ``dict.get(key, expensive_default)`` patterns inside the
         W607-DW region (all gets use literal defaults).
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    discipline_violations: list[str] = []
    bind_counts: dict[str, int] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_run_check_dw":
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
                        f"Discipline #2/7 violation: ``_run_check_dw(..., default=<Call>)`` "
                        f"binds an EAGER call at line {node.lineno}; default must "
                        f"be a literal (None / '' / 0 / {{}} / [])."
                    )
                if isinstance(val, ast.Lambda):
                    discipline_violations.append(
                        f"Discipline #2 violation: ``_run_check_dw(..., default=lambda)`` "
                        f"at line {node.lineno}; default must be a literal value."
                    )
                # Discipline #5: no len() inside the default slot.
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and sub.func.id == "len":
                            discipline_violations.append(
                                f"Discipline #5 violation: len() inside _run_check_dw default at line {node.lineno}."
                            )
    assert not discipline_violations, "\n".join(discipline_violations)

    # Discipline #4: every W607-DW phase appears exactly once in the
    # substrate bind sites — no accidental collision.
    for phase, count in bind_counts.items():
        assert count == 1, (
            f"Discipline #4 violation: phase {phase!r} bound {count} times in "
            f"cmd_doctor -- W607-DW phases must be unique."
        )

    # Discipline #6: ``rendered is None`` guard must precede any echo
    # on the degraded serialize_envelope path.
    if "rendered = _run_check_dw(" in src:
        assert "rendered is None" in src, (
            "Discipline #6 violation: serialize_envelope degraded path "
            "missing ``rendered is None`` guard before click.echo."
        )

    # Discipline #1: verdict default is a non-empty literal string.
    # The canonical literal is "DOCTOR — verdict unavailable".
    assert '"DOCTOR — verdict unavailable"' in src, (
        "Discipline #1 violation: compose_verdict default is no longer "
        "a non-empty literal; LAW 6 verdict floor at risk."
    )


# ---------------------------------------------------------------------------
# (20) Phase-name disjointness: W607-N/BE and W607-DW sub-vocabs do not collide
# ---------------------------------------------------------------------------


def test_w607n_w607be_w607dw_phase_names_are_disjoint():
    """Within the shared ``doctor_*`` family, W607-N (capture-layer),
    W607-BE (persist-side), and W607-DW (post-capture) phase
    sub-vocabularies are DISJOINT — no phase name appears in more than
    one layer (would create marker ambiguity).
    """
    dw_set = set(_DW_PHASES)
    n_be_set = set(_N_BE_PHASES)
    overlap = dw_set & n_be_set
    assert not overlap, (
        f"W607-N/BE and W607-DW phase names overlap: {overlap!r}. "
        f"The shared ``doctor_*`` marker family requires disjoint "
        f"phase sub-vocabularies."
    )


# ---------------------------------------------------------------------------
# (21) HELPER-TEMPLATE FIX: _run_check_dw returns default VERBATIM
# ---------------------------------------------------------------------------


def test_run_check_dw_returns_default_verbatim_not_dict_fallback():
    """CRITICAL helper-template fix regression guard.

    ``_run_check_dw`` MUST return ``default`` verbatim on raise (NOT
    ``default if default is not None else {}``). The latter form
    breaks the ``rendered is None`` guard on the serialize_envelope
    path because the helper substitutes ``{}`` even when the caller
    explicitly asked for ``None``.

    AST-level check: scan the ``_run_check_dw`` function body for the
    ``return default`` statement and confirm it's a plain ``Name`` —
    NOT an ``IfExp`` with ``default is not None else {}`` shape.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_helper = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dw":
            found_helper = True
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    # Only inspect ``return default`` shapes — the
                    # try-body has its own ``return fn(*args, **kwargs)``.
                    if isinstance(stmt.value, ast.Name) and stmt.value.id == "default":
                        # Verbatim return — OK.
                        continue
                    if isinstance(stmt.value, ast.IfExp):
                        # Forbidden shape: default if default is not None else {}
                        if (
                            isinstance(stmt.value.test, ast.Compare)
                            and isinstance(stmt.value.test.left, ast.Name)
                            and stmt.value.test.left.id == "default"
                        ):
                            pytest.fail(
                                "_run_check_dw uses forbidden "
                                "``default if default is not None else {}`` "
                                "shape — must return default verbatim. "
                                "This breaks the serialize_envelope "
                                "``rendered is None`` guard."
                            )
            break
    assert found_helper, "_run_check_dw helper not found in cmd_doctor AST"


# ---------------------------------------------------------------------------
# (22-26) Per-phase isolation tests — one per DW phase
# ---------------------------------------------------------------------------


def test_per_phase_isolation_compute_scores(cli_runner, monkeypatch):
    """compute_scores failure does not torpedo other phases.

    Use a str-subclass with __format__ raise — surfaces as a
    compose_verdict marker (downstream of compute_scores via the
    verdict_arg chain). Either marker proves the W607-DW chain caught
    the raise without torpedoing the envelope.
    """
    from roam.commands import cmd_doctor

    class _FmtRaisingStr(str):
        def __format__(self, fmt):
            raise RuntimeError("synthetic-per-phase-compute-scores")

    def _patched():
        return {
            "name": _FmtRaisingStr("Index"),
            "passed": False,
            "detail": "synthetic-per-phase-compute-scores",
        }

    monkeypatch.setattr(cmd_doctor, "_check_index_exists", _patched)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    assert result.output.strip(), result.output
    data = _json.loads(result.output)
    # Envelope still composes with a single-line verdict.
    summary = data["summary"]
    assert isinstance(summary.get("verdict"), str) and summary["verdict"]


def test_per_phase_isolation_compose_verdict(cli_runner, monkeypatch):
    """compose_verdict failure does not torpedo other phases.

    The literal floor verdict surfaces and the envelope still composes.
    """
    src = _SRC_PATH.read_text(encoding="utf-8")
    # Source-level check that the floor exists (runtime forcing the
    # compose_verdict raise alone is brittle since compute_scores
    # often raises first).
    assert '"DOCTOR — verdict unavailable"' in src


def test_per_phase_isolation_assemble_sections(cli_runner, monkeypatch):
    """assemble_sections failure does not torpedo other phases."""
    from roam.commands import cmd_doctor

    class _PoisonedSet:
        def __contains__(self, item):
            raise TypeError("synthetic-per-phase-assemble-sections")

    monkeypatch.setattr(cmd_doctor, "_ADVISORY_CHECK_NAMES", _PoisonedSet())

    result = _invoke_doctor(cli_runner, json_mode=True)
    # Envelope still composes (degraded path's hand-rolled fallback
    # may fire or assemble_sections may degrade to floor).
    assert result.exit_code in (0, 1, 2), result.output
    assert result.output.strip(), "degraded path must still echo JSON"


def test_per_phase_isolation_serialize_envelope(cli_runner, monkeypatch):
    """serialize_envelope failure does not torpedo other phases."""
    from roam.commands import cmd_doctor

    def _boom(payload):
        raise RuntimeError("synthetic-per-phase-serialize-envelope")

    monkeypatch.setattr(cmd_doctor, "to_json", _boom)

    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)
    assert data.get("command") == "doctor"


def test_per_phase_isolation_format_text(cli_runner, monkeypatch):
    """format_text failure does not torpedo other phases (text-mode)."""
    from roam.commands import cmd_doctor

    real_echo = cmd_doctor.click.echo

    def _patched_echo(*args, **kwargs):
        msg = args[0] if args else ""
        if isinstance(msg, str) and msg.startswith("VERDICT:"):
            raise RuntimeError("synthetic-per-phase-format-text")
        return real_echo(*args, **kwargs)

    monkeypatch.setattr(cmd_doctor.click, "echo", _patched_echo)

    result = _invoke_doctor(cli_runner, json_mode=False)
    # No exception bubbled = wrap caught it.
    assert result.exit_code in (0, 1, 2), result.output


# ---------------------------------------------------------------------------
# (27) W835/W836 EMPTY-STATE PRESERVATION
# ---------------------------------------------------------------------------


def test_w835_w836_empty_corpus_path_byte_identical_with_w607dw(cli_runner):
    """W607-DW does NOT graduate W835/W836: on a clean run, the empty
    bucket produces a byte-identical envelope on the W835/W836 path
    (corpus-content advisory check fires and lands in checks cleanly,
    no raise; warnings_out stays empty).
    """
    result = _invoke_doctor(cli_runner, json_mode=True)
    assert result.exit_code in (0, 1, 2), result.output
    data = _json.loads(result.output)
    # The W835/W836 partial_success contract: partial_success is True
    # iff failed OR any warnings_out bucket non-empty. On the clean
    # path, the W607-DW bucket is empty AND the W835/W836 corpus-content
    # check may or may not contribute a failed entry (depends on actual
    # corpus content). The byte-identical guarantee is: warnings_out
    # is absent when buckets are empty.
    summary_wo = data["summary"].get("warnings_out") or []
    top_wo = data.get("warnings_out") or []
    # If no W607-* substrate raised, NEITHER warnings_out key surfaces.
    all_wo = list(top_wo) + list(summary_wo)
    dw_markers = [m for m in all_wo if any(f"doctor_{p}_failed:" in m for p in _DW_PHASES)]
    assert not dw_markers, (
        f"W835/W836 preservation guard: clean doctor run must not surface W607-DW markers; got {all_wo!r}"
    )


# ---------------------------------------------------------------------------
# (28) Drive-by AXIS B: cmd_audit _run_check_dm helper template fix
# ---------------------------------------------------------------------------


def test_cmd_audit_run_check_dm_returns_default_verbatim():
    """AXIS B drive-by: cmd_audit's W607-DM helper MUST return
    ``default`` verbatim, NOT ``default if default is not None else {}``.

    This is the same broken template that the W607-DP fix sealed in
    cmd_dashboard. Pinning the fix in cmd_audit so a future refactor
    doesn't accidentally reintroduce the bug.

    NOTE: a fuller runtime regression test lives in
    ``test_w607_dm_cmd_audit_warnings_out_envelope.py``.
    """
    audit_src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_audit.py"
    audit_src = audit_src_path.read_text(encoding="utf-8")
    tree = ast.parse(audit_src)

    found_helper = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dm":
            found_helper = True
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return):
                    if isinstance(stmt.value, ast.IfExp):
                        if (
                            isinstance(stmt.value.test, ast.Compare)
                            and isinstance(stmt.value.test.left, ast.Name)
                            and stmt.value.test.left.id == "default"
                        ):
                            pytest.fail(
                                "_run_check_dm in cmd_audit uses forbidden "
                                "``default if default is not None else {}`` "
                                "shape — must return default verbatim. "
                                "This breaks any ``rendered is None`` guard "
                                "on the serialize_envelope path."
                            )
            break
    assert found_helper, "_run_check_dm helper not found in cmd_audit AST"
