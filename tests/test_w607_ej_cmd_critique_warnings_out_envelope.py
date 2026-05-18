"""W607-EJ -- aggregation-phase plumbing role for ``cmd_critique``.

WAVE-AXIS FINDING
-----------------

W607-EJ on cmd_critique is **closed-as-duplicate-of-W607-Y-plus-W607-BL**.
cmd_critique already carries BOTH layers of the canonical W607 plumbing:

  * substrate-CALL layer: W607-Y (eight substrate boundaries wrapped
    via ``_run_check`` / ``_w607y_warnings_out`` -- parse_diff /
    find_changed_symbols / run_checks / aggregate / emit_findings /
    load_overrides / bench_relevance_hint / compute_risk_level).
  * aggregation-phase layer: W607-BL (five aggregation boundaries
    wrapped via ``_run_check_bl`` / ``_w607bl_warnings_out`` --
    severity_classify / severity_normalize / compute_verdict /
    auto_log / serialize_envelope).

Plus the older W641-followup-B ``_critique_warnings_out`` bucket
tracking unknown-severity drops. All three buckets share the canonical
``critique_*`` marker family and feed a single combined
``warnings_out`` channel mirrored on BOTH summary AND top-level of the
envelope.

Introducing an additional ``_w607ej_warnings_out`` / ``_run_check_ej``
layer would:

  1. Quadruple-stack the wrap (unknown-severity + substrate-CALL Y +
     aggregation-phase BL + redundant EJ) for zero behavioural gain on
     the canonical phases.
  2. Violate W978's 4th discipline (phase-name collision): the EJ
     phase set would collide 1:1 with the W607-Y substrate phases AND
     the W607-BL aggregation phases. An agent reading
     ``critique_compute_verdict_failed:`` could not tell which layer
     raised.
  3. Confuse the agent-OS edit-loop cluster naming. cmd_critique's
     letter pair is Y + BL; the W607-EJ letter pair belongs to the
     next free consumer (a DIFFERENT command), not a third layer on
     cmd_critique.

This test file PINS the dual-layer invariants on the W607-Y +
W607-BL plumbing and documents the W607-EJ-on-cmd_critique axis as
**closed**. Future agents picking up the W607-EJ letter pair should
target a DIFFERENT command from the consumer queue.

ROLE-MAP TABLE
--------------

For the agent-OS edit-loop cluster (pre-edit triangle + post-edit
gate), the substrate-CALL + aggregation-phase letter-pair roles are:

  * cmd_preflight    -> R (substrate) + AW (aggregation) + EC (5-phase post-capture LAYER 3)
  * cmd_impact       -> T (substrate) + BB (aggregation)
  * cmd_diagnose     -> S (substrate) + BH (aggregation)
  * cmd_critique     -> Y (substrate) + BL (aggregation)  <-- this command

W607-EJ on cmd_critique -> closed-as-duplicate-of-Y-plus-BL.

REGRESSION INVARIANTS PRESERVED
-------------------------------

  * W153 -- critique mirrors aggregated findings into the central
    findings registry via ``_emit_critique_findings`` on ``--persist``.
    Detector version pinned at ``CRITIQUE_DETECTOR_VERSION``. The
    ``emit_findings`` phase is wrapped by W607-Y.
  * W832 -- deferred per-check status disclosure on the envelope
    summary. ``check_status`` keys differentiate
    ``all_checks_ran`` from ``partial_critique`` so a 0-concerns
    verdict cannot mask a no-check no-op.
  * W256 / W263 -- critique-contract drift-guard: ``critique`` is the
    envelope command name; the W607-BL serialize_envelope floor stub
    preserves ``"command": "critique"`` even on json_envelope raise.
  * W831 -- EMPTY_INPUT structured usage error: empty diff input
    raises ``structured_usage_error(EMPTY_INPUT, ...)`` before any
    W607 plumbing runs.
  * Exit code 5 on HIGH severity preserved at the terminal
    ``ctx.exit(5)`` gate (after envelope emission), independent of
    any W607 plumbing.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical W607-EJ-role phases (overlapping with both the W607-Y substrate
# layer and the W607-BL aggregation layer cmd_critique already wraps).
# ---------------------------------------------------------------------------


_EJ_ROLE_PHASES = (
    "parse_diff",
    "resolve_diff_symbols",
    "check_clones_not_edited",
    "check_blast_radius",
    "check_critique_rules",
    "aggregate_findings",
    "score_classify",
    "compute_predicate",
    "compose_verdict",
    "serialize_envelope",
)


# ---------------------------------------------------------------------------
# Phases ACTUALLY wrapped under W607-Y (substrate-CALL layer).
# ---------------------------------------------------------------------------


_Y_SUBSTRATE_PHASES = (
    "parse_diff",
    "find_changed_symbols",
    "run_checks",
    "aggregate",
    "emit_findings",
    "load_overrides",
    "bench_relevance_hint",
    "compute_risk_level",
)


# ---------------------------------------------------------------------------
# Phases ACTUALLY wrapped under W607-BL (aggregation-phase layer).
# ---------------------------------------------------------------------------


_BL_AGGREGATION_PHASES = (
    "severity_classify",
    "severity_normalize",
    "compute_verdict",
    "auto_log",
    "serialize_envelope",
)


_CRITIQUE_SRC = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_critique.py"


def _read_src() -> str:
    assert _CRITIQUE_SRC.exists(), f"cmd_critique.py missing at {_CRITIQUE_SRC}"
    return _CRITIQUE_SRC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (1) WAVE-AXIS FINDING -- W607-EJ accumulator is INTENTIONALLY ABSENT
# from cmd_critique (closed-as-duplicate-of-Y-plus-BL).
# ---------------------------------------------------------------------------


def test_w607ej_accumulator_absent_from_cmd_critique():
    """W607-EJ on cmd_critique is closed-as-duplicate-of-Y-plus-BL.

    cmd_critique already carries both the W607-Y substrate-CALL layer
    AND the W607-BL aggregation-phase layer. Stacking an additional
    W607-EJ layer would quadruple-stack the wrap (W641-followup-B
    unknown-severity + Y substrate + BL aggregation + redundant EJ)
    for zero behavioural gain.

    This guard pins the absence so a future agent who incorrectly
    introduces W607-EJ on cmd_critique sees the test fail with context
    pointing them at the Y + BL layers.
    """
    src = _read_src()
    assert "_w607ej_warnings_out" not in src, (
        "W607-EJ accumulator unexpectedly present in cmd_critique. "
        "cmd_critique's layers are W607-Y (``_w607y_warnings_out`` + "
        "``_run_check``) AND W607-BL (``_w607bl_warnings_out`` + "
        "``_run_check_bl``); W607-EJ on cmd_critique is "
        "closed-as-duplicate-of-Y-plus-BL. If you intended to add a "
        "third W607 layer on cmd_critique, you must rename one set of "
        "phases to avoid W978 4th-discipline collision -- but the "
        "preferred path is NOT to add the layer (zero behavioural "
        "gain over the existing dual-layer plumbing)."
    )


def test_w607ej_helper_absent_from_cmd_critique():
    """The W607-EJ helper ``_run_check_ej`` must NOT appear in cmd_critique."""
    src = _read_src()
    assert "_run_check_ej" not in src, (
        "W607-EJ helper unexpectedly present in cmd_critique. "
        "cmd_critique's helpers are ``_run_check`` (W607-Y) and "
        "``_run_check_bl`` (W607-BL); W607-EJ-on-cmd_critique is "
        "closed-as-duplicate-of-Y-plus-BL."
    )


# ---------------------------------------------------------------------------
# (2) CANONICAL DUAL LAYERS -- W607-Y substrate-CALL + W607-BL aggregation
# play the W607-EJ role for cmd_critique. Pin their presence.
# ---------------------------------------------------------------------------


def test_cmd_critique_substrate_layer_is_w607y():
    """The substrate-CALL layer role for cmd_critique is W607-Y.

    Pins the structural anchor: ``_w607y_warnings_out`` accumulator
    AND ``_run_check`` helper. A regression that removes the Y layer
    silently demotes cmd_critique to aggregation-only coverage.
    """
    src = _read_src()
    assert "_w607y_warnings_out" in src, (
        "W607-Y accumulator missing from cmd_critique; the substrate-"
        "CALL layer for cmd_critique has regressed. Removing it leaves "
        "cmd_critique with aggregation-only (BL) coverage."
    )
    assert "def _run_check(phase" in src, "W607-Y helper ``_run_check(phase, ...)`` missing from cmd_critique."


def test_cmd_critique_aggregation_layer_is_w607bl():
    """The aggregation-phase layer role for cmd_critique is W607-BL.

    Pins the structural anchor: ``_w607bl_warnings_out`` accumulator
    AND ``_run_check_bl`` helper. A regression that removes the BL
    layer silently demotes cmd_critique to substrate-only coverage.
    """
    src = _read_src()
    assert "_w607bl_warnings_out" in src, (
        "W607-BL accumulator missing from cmd_critique; the "
        "aggregation-phase layer for cmd_critique has regressed. "
        "Removing it leaves cmd_critique with substrate-only (Y) "
        "coverage."
    )
    assert "def _run_check_bl(phase" in src, "W607-BL helper ``_run_check_bl(phase, ...)`` missing from cmd_critique."


# ---------------------------------------------------------------------------
# (3) Older bucket -- W641-followup-B unknown-severity tracking coexists.
# ---------------------------------------------------------------------------


def test_unknown_severity_bucket_coexists():
    """W641-followup-B ``_critique_warnings_out`` unknown-severity bucket.

    Tracks the data-shape axis (a finding's severity label couldn't be
    mapped to W631 risk-level vocabulary). MUST coexist with W607-Y +
    W607-BL on the helper-raise axis; combined-warnings emission is
    where the three buckets converge.
    """
    src = _read_src()
    assert "_critique_warnings_out" in src, (
        "W641-followup-B unknown-severity bucket missing from "
        "cmd_critique; the data-shape disclosure surface has "
        "regressed."
    )


# ---------------------------------------------------------------------------
# (4) Per-phase wrap discipline -- W607-Y substrate phases.
# ---------------------------------------------------------------------------


def test_every_y_substrate_phase_wrapped_in_run_check():
    """Every canonical W607-Y substrate phase calls ``_run_check(...)``
    with the canonical phase name.

    The 8 phases ``parse_diff`` / ``find_changed_symbols`` /
    ``run_checks`` / ``aggregate`` / ``emit_findings`` /
    ``load_overrides`` / ``bench_relevance_hint`` /
    ``compute_risk_level`` are the canonical substrate boundaries
    wrapped by W607-Y.
    """
    src = _read_src()
    for phase in _Y_SUBSTRATE_PHASES:
        same_line = f'_run_check("{phase}"' in src
        multi_line = any(f'_run_check(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"critique_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-Y substrate wrap missing for phase {phase!r} on "
            f"cmd_critique; the canonical substrate boundary is no "
            f"longer caught."
        )


# ---------------------------------------------------------------------------
# (5) Per-phase wrap discipline -- W607-BL aggregation phases.
# ---------------------------------------------------------------------------


def test_every_bl_aggregation_phase_wrapped_in_run_check_bl():
    """Every canonical W607-BL aggregation phase calls
    ``_run_check_bl(...)`` with the canonical phase name.

    The 5 phases ``severity_classify`` / ``severity_normalize`` /
    ``compute_verdict`` / ``auto_log`` / ``serialize_envelope`` are
    the canonical aggregation boundaries wrapped by W607-BL.
    """
    src = _read_src()
    for phase in _BL_AGGREGATION_PHASES:
        same_line = "_run_check_bl(\n" in src and f'"{phase}"' in src
        compact = f'_run_check_bl("{phase}"' in src
        marker_grep = f"critique_{phase}_failed" in src
        assert same_line or compact or marker_grep, (
            f"W607-BL aggregation wrap missing for phase {phase!r} on "
            f"cmd_critique; the canonical aggregation boundary is no "
            f"longer caught."
        )


# ---------------------------------------------------------------------------
# (6) Marker family discipline -- closed-enum prefix.
# ---------------------------------------------------------------------------


def test_marker_family_is_critique_prefix():
    """Both Y and BL emit ``critique_<phase>_failed:<exc>:<detail>``.

    Marker family is closed-enum ``critique_*`` -- NOT ``preflight_*``
    (W607-R/AW), NOT ``impact_*`` (W607-T/BB), NOT ``diagnose_*``
    (W607-S/BH). The marker-prefix discipline pins this distinction so
    cross-prefix leakage stays impossible.
    """
    src = _read_src()
    # Y layer
    assert 'f"critique_{phase}_failed:{type(exc).__name__}:{exc}"' in src, (
        "W607-Y marker shape regressed; should be f'critique_{phase}_failed:{type(exc).__name__}:{exc}'."
    )


def test_cross_prefix_isolation_no_preflight_or_impact_or_diagnose_leak():
    """W607 prefixes for sibling commands MUST NOT appear in cmd_critique.

    cmd_critique emits the ``critique_*`` family ONLY. A
    ``preflight_*_failed`` or ``impact_*_failed`` or
    ``diagnose_*_failed`` marker string in cmd_critique would mean a
    sibling-command prefix leaked into the wrong consumer.
    """
    src = _read_src()
    for foreign_prefix in ("preflight_", "impact_", "diagnose_", "relate_", "taint_"):
        # Filter to f-strings that look like the marker shape, not arbitrary
        # mentions of the word "preflight" in comments / next_commands.
        candidate_marker = f'f"{foreign_prefix}{{phase}}_failed'
        assert candidate_marker not in src, (
            f"Cross-prefix leak: {foreign_prefix!r} marker shape found "
            f"in cmd_critique. Each command's W607 plumbing must emit "
            f"its OWN ``<cmd>_*`` family."
        )


# ---------------------------------------------------------------------------
# (7) Helper template shape -- ``return default`` verbatim, no transform.
# ---------------------------------------------------------------------------


def _find_function_def(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_run_check_helper_returns_default_verbatim():
    """``_run_check`` (W607-Y) returns the *default* sentinel on raise.

    The helper template MUST be:
        try: return fn(*args, **kwargs)
        except Exception as exc:
            <accumulator>.append(...)
            return default

    Any transform on the default (e.g. ``return default or []``) would
    let a falsy raise floor silently collide with a clean call's empty
    return, conflating the two states.
    """
    src = _read_src()
    tree = ast.parse(src)
    fn = _find_function_def(tree, "_run_check")
    assert fn is not None, "_run_check helper not defined in cmd_critique"
    # Last statement in the except handler must be ``return default``.
    handler_returns = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    handler_returns.append(sub)
    assert handler_returns, "_run_check has no return inside except handler"
    last_ret = handler_returns[-1]
    assert isinstance(last_ret.value, ast.Name) and last_ret.value.id == "default", (
        f"_run_check must ``return default`` verbatim, not transform it; got {ast.dump(last_ret)}"
    )


def test_run_check_bl_helper_returns_default_verbatim():
    """``_run_check_bl`` (W607-BL) returns the *default* sentinel on raise.

    Mirror of ``_run_check`` shape -- same ``return default`` verbatim
    discipline so a future contributor cannot silently collapse the
    floor sentinel.
    """
    src = _read_src()
    tree = ast.parse(src)
    fn = _find_function_def(tree, "_run_check_bl")
    assert fn is not None, "_run_check_bl helper not defined in cmd_critique"
    handler_returns = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return):
                    handler_returns.append(sub)
    assert handler_returns, "_run_check_bl has no return inside except handler"
    last_ret = handler_returns[-1]
    assert isinstance(last_ret.value, ast.Name) and last_ret.value.id == "default", (
        f"_run_check_bl must ``return default`` verbatim, not transform it; got {ast.dump(last_ret)}"
    )


# ---------------------------------------------------------------------------
# (8) Helper template shape -- accumulator appended with canonical marker.
# ---------------------------------------------------------------------------


def test_run_check_appends_canonical_marker():
    """W607-Y helper appends ``critique_<phase>_failed:<exc>:<detail>``."""
    src = _read_src()
    tree = ast.parse(src)
    fn = _find_function_def(tree, "_run_check")
    assert fn is not None
    fn_src = ast.get_source_segment(src, fn) or ""
    assert "_w607y_warnings_out.append(" in fn_src, "_run_check must append onto _w607y_warnings_out (W607-Y bucket)."
    assert "critique_{phase}_failed:" in fn_src, "_run_check marker shape regressed."


def test_run_check_bl_appends_canonical_marker():
    """W607-BL helper appends ``critique_<phase>_failed:<exc>:<detail>``."""
    src = _read_src()
    tree = ast.parse(src)
    fn = _find_function_def(tree, "_run_check_bl")
    assert fn is not None
    fn_src = ast.get_source_segment(src, fn) or ""
    assert "_w607bl_warnings_out.append(" in fn_src, (
        "_run_check_bl must append onto _w607bl_warnings_out (W607-BL bucket)."
    )
    assert "critique_{phase}_failed:" in fn_src, "_run_check_bl marker shape regressed."


# ---------------------------------------------------------------------------
# (9) Combined-channel emission -- three buckets merge into one warnings_out.
# ---------------------------------------------------------------------------


def test_combined_warnings_out_merges_three_buckets():
    """Envelope emission combines the three buckets into one list.

    ``_critique_warnings_out`` (W641-followup-B unknown-severity) +
    ``_w607y_warnings_out`` (W607-Y substrate-CALL) +
    ``_w607bl_warnings_out`` (W607-BL aggregation-phase). All three
    share the ``critique_*`` family and combine at emission.
    """
    src = _read_src()
    assert "_combined_warnings_out" in src, (
        "Combined warnings_out list missing -- the three buckets must merge at emission time."
    )
    # The combine expression should reference all three buckets.
    for bucket in (
        "_critique_warnings_out",
        "_w607y_warnings_out",
        "_w607bl_warnings_out",
    ):
        assert f"list({bucket})" in src, (
            f"Combined-warnings list does not include {bucket!r}; the three-bucket merge has regressed."
        )


# ---------------------------------------------------------------------------
# (10) Bond-bug check -- markers reach BOTH top-level + summary.warnings_out.
# ---------------------------------------------------------------------------


def test_markers_mirror_to_top_level_envelope():
    """W607-Y / W607-BL markers reach the top-level envelope.

    Mirror parity check (W978 7-discipline + bond-bug check): the
    combined channel writes to BOTH summary.warnings_out AND the
    top-level envelope.warnings_out. A consumer reading the top-level
    envelope directly (without descending into summary) MUST see the
    markers.
    """
    src = _read_src()
    assert 'summary["warnings_out"]' in src, (
        "summary.warnings_out write site missing; markers cannot reach the summary mirror."
    )
    assert '_envelope_kwargs["warnings_out"]' in src, (
        "Top-level envelope.warnings_out write site missing; markers "
        "do not reach the top-level mirror. Bond-bug: a consumer that "
        "reads top-level envelope.warnings_out alone misses W607 "
        "signal."
    )


def test_partial_success_flips_on_any_bucket_nonempty():
    """``partial_success`` flips when ANY of the three buckets is non-empty.

    Pattern-2 silent-fallback discipline: a downstream consumer reading
    ``partial_success`` alone should see ``True`` regardless of which
    bucket emitted the marker.
    """
    src = _read_src()
    # Look for the canonical "if _combined_warnings_out: ... partial_success = True"
    assert "if _combined_warnings_out:" in src, "Combined-warnings flip-on-nonempty guard missing."
    assert 'summary["partial_success"] = True' in src, "partial_success flip on summary missing."


# ---------------------------------------------------------------------------
# (11) Late-phase markers reach the envelope (auto_log + serialize_envelope).
# ---------------------------------------------------------------------------


def test_late_phase_serialize_envelope_floor_carries_combined_warnings():
    """W607-BL serialize_envelope floor stub carries the combined list.

    A late-phase raise on ``json_envelope("critique", ...)`` AFTER all
    substrate + aggregation signals were already gathered must still
    surface the markers via the floor stub.
    """
    src = _read_src()
    assert "_envelope_floor" in src, "W607-BL serialize_envelope floor stub missing."
    # Floor stub must include warnings_out via the combined list.
    assert "list(_combined_warnings_out)" in src, (
        "Combined warnings_out list not propagated into the floor "
        "stub; late-phase markers will not reach the envelope on "
        "json_envelope raise."
    )


def test_late_phase_auto_log_marker_rebuilds_envelope():
    """W607-BL auto_log boundary: a raise rebuilds the envelope so the
    marker reaches the JSON output.

    Mirror parity check: an exception raised on the auto_log call (HMAC
    chain mishape / filesystem failure) appends to ``_w607bl_warnings_out``
    AFTER the envelope was already built. The rebuild branch re-runs
    the combined-warnings merge so the late-phase marker reaches the
    top-level + summary mirrors.
    """
    src = _read_src()
    assert "critique_auto_log_failed:" in src, "auto_log marker shape regressed."
    # The rebuild branch should re-merge the combined list AND re-run
    # the serialize_envelope wrap.
    assert "if _w607bl_warnings_out and not any(" in src, (
        "Late-phase auto_log rebuild branch missing; a raise after envelope emission will not reach the JSON output."
    )


# ---------------------------------------------------------------------------
# (12) W153 preservation -- critique findings-registry persistence.
# ---------------------------------------------------------------------------


def test_w153_critique_findings_registry_persistence_preserved():
    """W153: critique mirrors aggregated findings into the central
    findings registry on ``--persist`` via ``_emit_critique_findings``.

    The emit_findings phase is wrapped by W607-Y so a raise on the
    registry write becomes a marker instead of crashing the envelope.
    """
    src = _read_src()
    assert "_emit_critique_findings" in src, (
        "W153 critique findings-registry mirror has regressed; _emit_critique_findings is gone."
    )
    assert "CRITIQUE_DETECTOR_VERSION" in src, (
        "W153 detector version pin missing; the registry-rows version stamp has regressed."
    )
    # Emit must be W607-Y-wrapped.
    assert '"emit_findings", _emit_critique_findings' in src or "critique_emit_findings_failed" in src, (
        "W153 _emit_critique_findings call is not wrapped under W607-Y "
        "emit_findings phase; a raise on registry write will crash the "
        "envelope."
    )


# ---------------------------------------------------------------------------
# (13) W831 preservation -- EMPTY_INPUT structured usage error.
# ---------------------------------------------------------------------------


def test_w831_empty_input_structured_usage_error_preserved():
    """W831: empty diff input raises
    ``structured_usage_error(EMPTY_INPUT, ...)`` BEFORE any W607
    plumbing runs.

    Pattern-1 variant-D discipline: an empty-input no-op cannot emit a
    success verdict. The structured usage error is the canonical
    pre-W607 gate.
    """
    src = _read_src()
    assert "EMPTY_INPUT" in src, (
        "W831 EMPTY_INPUT structured usage error missing; an empty "
        "diff input will reach W607 plumbing without a pre-gate."
    )
    assert "structured_usage_error(EMPTY_INPUT" in src, (
        "EMPTY_INPUT must be raised via structured_usage_error (not click.UsageError or plain exit)."
    )


# ---------------------------------------------------------------------------
# (14) Exit code 5 preservation -- HIGH severity gate.
# ---------------------------------------------------------------------------


def test_exit_code_5_on_high_severity_preserved():
    """Exit code 5 on HIGH severity preserved at the terminal gate.

    The ``ctx.exit(5)`` call after envelope emission is independent of
    any W607 plumbing -- a high-severity finding remains CI-failing
    regardless of which marker layer emitted.
    """
    src = _read_src()
    assert "ctx.exit(5)" in src, "Exit code 5 gate missing; HIGH-severity findings will not fail CI."
    # Gate must be conditional on high severity.
    assert 'result["severity_breakdown"].get("high", 0)' in src or 'severity_breakdown.get("high"' in src, (
        "Exit-5 gate is not conditional on HIGH severity count; the CI-failing semantics have regressed."
    )


# ---------------------------------------------------------------------------
# (15) Envelope command-name preservation -- critique-contract drift guard.
# ---------------------------------------------------------------------------


def test_envelope_command_name_is_critique():
    """W256 / W263: envelope command name must be ``"critique"``.

    Both the canonical ``json_envelope("critique", ...)`` path AND the
    W607-BL serialize_envelope floor stub must carry the same command
    name so a downstream consumer's schema validator does not see
    drift.
    """
    src = _read_src()
    assert 'json_envelope(\n        "critique"' in src or 'json_envelope("critique"' in src, (
        'Canonical envelope command name has drifted from ``"critique"``.'
    )
    # Floor stub must also carry "command": "critique".
    assert '"command": "critique"' in src, (
        'W607-BL serialize_envelope floor stub command name has drifted from ``"critique"``.'
    )


# ---------------------------------------------------------------------------
# (16) Severity-classify degradation discipline -- W607-BL sentinel.
# ---------------------------------------------------------------------------


def test_severity_classification_sentinel_exposed():
    """W607-BL surfaces ``severity_classification: "unknown"`` when the
    inner severity-classify boundary raises.

    Closed-enum sentinel: ``classified`` (clean) or ``unknown``
    (degraded). Mirrors cmd_impact's ``risk_classification`` /
    cmd_diagnose's ``severity_classification`` sentinel.
    """
    src = _read_src()
    assert '"severity_classification"' in src, (
        "W607-BL severity_classification sentinel missing from the "
        "summary dict; severity_classify degradation is not "
        "disclosed."
    )
    assert '"unknown"' in src and '"classified"' in src, (
        "severity_classification sentinel must use the closed-enum values ``unknown`` / ``classified``."
    )


# ---------------------------------------------------------------------------
# (17) Coexistence -- existing W607 layers are NOT replaced by W607-EJ.
# ---------------------------------------------------------------------------


def test_pre_existing_w607_layers_intact():
    """Coexistence check: W607-Y + W607-BL substrings all present.

    A future agent who refactors cmd_critique to "consolidate W607
    layers" would unintentionally remove proven coverage. This guard
    pins the full set of layer markers in source so a consolidation
    refactor fails the test before merge.
    """
    src = _read_src()
    layer_markers = (
        "_w607y_warnings_out",  # W607-Y accumulator
        "_w607bl_warnings_out",  # W607-BL accumulator
        "_critique_warnings_out",  # W641-followup-B unknown-severity
        "def _run_check(phase",  # W607-Y helper
        "def _run_check_bl(phase",  # W607-BL helper
    )
    for marker in layer_markers:
        assert marker in src, (
            f"Pre-existing layer marker {marker!r} missing from cmd_critique; a W607 layer has regressed."
        )


# ---------------------------------------------------------------------------
# (18) Closed-enum guard -- W607-EJ phase names absent under any helper.
# ---------------------------------------------------------------------------


def test_w607ej_phase_names_not_introduced_under_y_or_bl():
    """The W607-EJ-role phase names that diverge from Y/BL are absent.

    Some W607-EJ-role phases overlap with W607-Y or W607-BL (e.g.
    ``parse_diff`` / ``serialize_envelope`` / ``compute_verdict``).
    The DIVERGENT phase names (resolve_diff_symbols /
    check_clones_not_edited / check_blast_radius / check_critique_rules
    / aggregate_findings / score_classify / compute_predicate /
    compose_verdict) must NOT appear as new phase-string literals --
    introducing them under any helper would create a third W607 layer.
    """
    src = _read_src()
    divergent = (
        "resolve_diff_symbols",
        "check_clones_not_edited",
        "check_blast_radius",
        "check_critique_rules",
        "aggregate_findings",
        "score_classify",
        "compute_predicate",
        "compose_verdict",
    )
    for phase in divergent:
        # The marker form ``critique_<phase>_failed`` would only appear
        # if the phase was being emitted. We tolerate the bare word
        # appearing in comments (e.g. the docstring may reference it)
        # but the marker f-string-formatted form must be absent.
        marker_form = f"critique_{phase}_failed"
        assert marker_form not in src, (
            f"W607-EJ-divergent phase {phase!r} unexpectedly emits a "
            f"marker in cmd_critique; this suggests a third W607 layer "
            f"is being added under an unexpected helper."
        )


# ---------------------------------------------------------------------------
# (19) W832 deferred verdict-review preservation.
# ---------------------------------------------------------------------------


def test_w832_check_status_disclosure_preserved():
    """W832: per-check status disclosure on the envelope summary.

    ``check_status`` keys differentiate ``all_checks_ran`` from
    ``partial_critique`` so a 0-concerns verdict cannot mask a no-check
    no-op. W607-BL state assignment respects this.
    """
    src = _read_src()
    assert '"check_status"' in src, "W832 check_status disclosure missing from cmd_critique."
    assert '"all_checks_ran"' in src and '"partial_critique"' in src, (
        "W832 closed-enum state values missing -- the deferred verdict-review semantics have regressed."
    )


# ---------------------------------------------------------------------------
# (20) Helper count -- exactly two W607 helpers, not three.
# ---------------------------------------------------------------------------


def test_exactly_two_w607_helpers_defined():
    """cmd_critique defines exactly TWO W607 helpers: ``_run_check``
    (W607-Y) and ``_run_check_bl`` (W607-BL).

    Adding a third helper (``_run_check_ej`` or similar) would
    triple-stack the W607 plumbing. The count guard pins the
    closed-set ``{Y, BL}`` so a future contributor sees the failing
    test before introducing a redundant third layer.
    """
    src = _read_src()
    tree = ast.parse(src)
    # W607 helpers are named ``_run_check`` (the W607-Y bare form) and
    # ``_run_check_<letterpair>`` (the W607-BL aggregation form). The
    # unrelated ``_run_checks_with_status`` (W832 deferred check-status
    # helper, plural ``checks``) is intentionally excluded by the
    # ``_run_check_`` underscore-suffix filter -- it has a different
    # responsibility (per-check status aggregation, NOT exception-wrap).
    w607_helpers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and (node.name == "_run_check" or node.name.startswith("_run_check_"))
    ]
    # Note: helpers are defined INSIDE the critique() click command, so
    # they're FunctionDef nodes nested in the outer function.
    assert "_run_check" in w607_helpers, "_run_check (W607-Y) helper missing"
    assert "_run_check_bl" in w607_helpers, "_run_check_bl (W607-BL) helper missing"
    # No other ``_run_check_*`` helper should exist (after the
    # plural-form filter above excludes _run_checks_with_status).
    unexpected = [h for h in w607_helpers if h not in ("_run_check", "_run_check_bl")]
    assert not unexpected, (
        f"Unexpected W607 helper(s) defined in cmd_critique: "
        f"{unexpected!r}. The closed-set of W607 helpers for "
        f"cmd_critique is {{_run_check (Y), _run_check_bl (BL)}}; any "
        f"additional helper triple-stacks the plumbing for zero "
        f"behavioural gain."
    )


# ---------------------------------------------------------------------------
# (21) AST audit -- exception handler bodies are simple (no swallow-and-success).
# ---------------------------------------------------------------------------


def test_run_check_exception_handler_only_appends_and_returns():
    """W978 discipline: the except handler in ``_run_check`` does
    EXACTLY two things: append a marker, return *default*.

    A handler that silently swallows the exception (``return None``,
    ``pass``, or computing a non-default fallback) would conflate a
    clean run with a degraded one. The simple-shape audit pins the
    minimal contract.
    """
    src = _read_src()
    tree = ast.parse(src)
    fn = _find_function_def(tree, "_run_check")
    assert fn is not None
    handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    assert len(handlers) == 1, f"_run_check must have exactly ONE except handler; got {len(handlers)}."
    handler_body = handlers[0].body
    # Body must be: [Expr(call to append), Return(Name('default'))]
    assert len(handler_body) == 2, (
        f"_run_check except-handler body must be 2 statements (append + return default); got {len(handler_body)}."
    )
    # First statement: append call.
    first = handler_body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call), (
        "First statement in except handler must be the append() Call."
    )
    # Second statement: return default.
    second = handler_body[1]
    assert isinstance(second, ast.Return), "Second statement in except handler must be a return."
    assert isinstance(second.value, ast.Name) and second.value.id == "default", (
        "Return value in except handler must be the bare ``default`` name, not a transform."
    )


# ---------------------------------------------------------------------------
# (22) Documentation invariant -- W607-EJ closure rationale documented.
# ---------------------------------------------------------------------------


def test_w607ej_closure_rationale_documented_in_this_file():
    """The WAVE-AXIS FINDING comment in this file documents the
    closed-as-duplicate decision.

    Future agents who land on W607-EJ should read the module docstring
    of this test file FIRST before attempting to add a third W607
    layer. This guard checks the rationale text is present.
    """
    this_file = Path(__file__).read_text(encoding="utf-8")
    assert "closed-as-duplicate-of-W607-Y-plus-W607-BL" in this_file, (
        "W607-EJ closure rationale text missing from this test file's module docstring."
    )
    # The wave letter pair Y + BL must be named so future agents see
    # the existing layers in plain text.
    assert "W607-Y" in this_file and "W607-BL" in this_file, (
        "W607-Y / W607-BL layer names missing from module docstring."
    )
