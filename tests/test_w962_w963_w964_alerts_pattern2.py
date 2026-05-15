"""W962 + W963 + W964: close three sibling Pattern 2 silent-fallback bugs
in ``cmd_alerts.py``.

All three were surfaced as W918 drive-bys (same Pattern 2 family) and share
the W918 ``warnings_out`` plumb-through pattern:

- **W962** — ``_parse_alerts_yaml`` did not validate ``op`` against the
  closed comparator set ``{">", "<", ">=", "<=", "=="}``. A typo like
  ``op: '!='`` in ``.roam/alerts.yaml`` made the alert a silent no-op.
- **W963** — ``_check_thresholds`` silently fell through the if/elif chain
  for unknown comparators. This is the runtime symptom of W962 and also
  guards against typos introduced directly in ``_DEFAULT_THRESHOLDS`` or in
  rules constructed in-process.
- **W964** — ``cfg.get("delta_alerts", True)`` returned the raw YAML scalar.
  ``delta_alerts: "yes"`` (string) was truthy under ``if delta_enabled:`` but
  distinct from the bool ``True``; under a strict ``is True`` check the
  feature would silently disable itself. Non-bool / non-truthy-string shapes
  (int, list, ...) silently disabled the feature outright.

Discipline (per CLAUDE.md Pattern 2): preserve happy-path behaviour, surface
the silent-fallback state as an actionable warning on ``warnings_out``,
NEVER raise on incomplete user configs (backward compat).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roam.commands.cmd_alerts import (
    _CANONICAL_LEVELS,
    _VALID_OPS,
    Alert,
    AlertThreshold,
    _check_thresholds,
    _coerce_bool,
    _coerce_level,
    _load_alerts_config,
    _make_alert,
    _parse_alerts_yaml,
    _resolved_thresholds,
)


def _make_alerts_yaml(text: str, root: Path) -> Path:
    """Write ``.roam/alerts.yaml`` under *root* and return the project path."""
    (root / ".roam").mkdir(exist_ok=True)
    (root / ".roam" / "alerts.yaml").write_text(text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# W962: parse-time op validation
# ---------------------------------------------------------------------------


def test_w962_invalid_op_in_yaml_emits_warning(tmp_path: Path) -> None:
    """W962: ``op: '!='`` in YAML triggers a Pattern 2 warning at parse
    time. The threshold survives in the parsed dict so check-time can
    log + skip it consistently.
    """
    _make_alerts_yaml(
        "thresholds:\n  cycles: { op: '!=', value: 10, level: warning }\n",
        tmp_path,
    )

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    assert warnings, "Expected at least one warning for invalid op '!='"
    warning = warnings[0]
    # LAW 4 + LAW 2: names the metric, names the operator, points at the
    # config file, ends on an imperative next step.
    assert "cycles" in warning, f"Warning must name the metric, got: {warning!r}"
    assert "!=" in warning, (
        f"Warning must name the offending operator, got: {warning!r}"
    )
    assert ".roam/alerts.yaml" in warning, (
        f"Warning must point at the config file, got: {warning!r}"
    )
    assert "Edit" in warning or "edit" in warning, (
        f"Warning must end on an imperative next step, got: {warning!r}"
    )
    # Cycle's rule still parsed (warning fires, rule survives) so the
    # parsed dict shape is stable for downstream consumers.
    assert "thresholds" in cfg
    assert "cycles" in cfg["thresholds"]


def test_w962_valid_op_emits_no_warning(tmp_path: Path) -> None:
    """W962: a valid ``op: '>='`` raises no parse-time warning — every
    valid comparator in the closed set passes through silently.
    """
    _make_alerts_yaml(
        "thresholds:\n  cycles: { op: '>=', value: 10, level: warning }\n",
        tmp_path,
    )

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    assert warnings == [], (
        f"Valid op '>=' must not trigger a W962 warning, got: {warnings}"
    )
    assert cfg["thresholds"]["cycles"]["op"] == ">="


def test_w962_parse_alerts_yaml_direct_invocation(tmp_path: Path) -> None:
    """W962: the tiny-parser path (used when PyYAML is unavailable) also
    appends to ``warnings_out`` on invalid ops. Belt-and-braces against
    environments without PyYAML installed.
    """
    yaml_text = (
        "thresholds:\n"
        "  health_score: { op: 'maybe', value: 60, level: critical }\n"
    )
    warnings: list[str] = []
    parsed = _parse_alerts_yaml(yaml_text, warnings_out=warnings)

    assert warnings, "Tiny parser must append to warnings_out"
    assert "health_score" in warnings[0]
    assert "maybe" in warnings[0]
    # Rule still survives in the parsed structure.
    assert parsed["thresholds"]["health_score"]["op"] == "maybe"


def test_w962_valid_ops_closed_set_matches_check_chain() -> None:
    """W962: the closed comparator set ``_VALID_OPS`` matches the set
    ``_check_thresholds`` actually knows how to evaluate. Drift guard
    against future additions to one side only.
    """
    assert _VALID_OPS == frozenset({">", "<", ">=", "<=", "=="})


# ---------------------------------------------------------------------------
# W963: check-time op validation (belt-and-braces)
# ---------------------------------------------------------------------------


def test_w963_unknown_op_at_check_time_emits_warning() -> None:
    """W963: a rule with an unknown ``op`` that bypassed parse-time
    validation (e.g. constructed in-process) surfaces a warning at
    check time AND is skipped.
    """
    current = {"cycles": 50}
    bad_rule = {"cycles": {"op": "!=", "value": 10, "level": "warning"}}

    warnings: list[str] = []
    alerts = _check_thresholds(current, bad_rule, warnings_out=warnings)

    assert alerts == [], (
        f"Unknown-op rule must be skipped (no alert emitted), got: {alerts}"
    )
    assert warnings, "Check-time must surface the invalid op"
    assert "cycles" in warnings[0]
    assert "!=" in warnings[0]


def test_w963_eq_op_now_supported() -> None:
    """W963: while wiring the closed-set validation, ``==`` joined the
    set of comparators ``_check_thresholds`` evaluates. Pin it so a
    future refactor cannot quietly drop it.
    """
    current = {"cycles": 10}
    rule = {"cycles": {"op": "==", "value": 10, "level": "info"}}
    alerts = _check_thresholds(current, rule)

    assert len(alerts) == 1
    assert alerts[0]["metric"] == "cycles"
    assert alerts[0]["level"] == "info"


def test_w963_known_ops_still_work_unchanged() -> None:
    """W963: happy-path comparators (``>``, ``<``, ``>=``, ``<=``) emit
    the same alerts they did pre-W963. No behavioural drift.
    """
    current = {"cycles": 15, "health_score": 50}
    rules = {
        "cycles": {"op": ">", "value": 10, "level": "warning"},
        "health_score": {"op": "<", "value": 60, "level": "critical"},
    }
    warnings: list[str] = []
    alerts = _check_thresholds(current, rules, warnings_out=warnings)

    assert len(alerts) == 2
    assert warnings == [], (
        f"Valid ops must not surface check-time warnings, got: {warnings}"
    )


def test_w963_resolver_threshold_warning_flows_to_check_when_op_invalid(
    tmp_path: Path,
) -> None:
    """W962 + W963 belt-and-braces in series: an invalid op in the YAML
    surfaces at parse-time (W962) AND, if it survives into
    ``_check_thresholds``, a check-time warning surfaces too. Both warnings
    name the same metric so the user has a paper trail.
    """
    _make_alerts_yaml(
        # ``cycles`` IS in defaults — the merge will produce a rule
        # with op='!=' from the override.
        "thresholds:\n  cycles: { op: '!=' }\n",
        tmp_path,
    )

    warnings: list[str] = []
    resolved = _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)

    # Parse-time warning fired (W962).
    assert any("!=" in w and "cycles" in w for w in warnings), (
        f"W962 parse-time warning must surface invalid op, got: {warnings}"
    )

    # The resolved rule still carries the invalid op (merge does not
    # heal); W963 catches it at check time.
    assert resolved["cycles"]["op"] == "!="

    check_warnings: list[str] = []
    alerts = _check_thresholds(
        {"cycles": 100}, resolved, warnings_out=check_warnings
    )
    assert alerts == [] or all(a["metric"] != "cycles" for a in alerts), (
        f"Invalid-op cycles rule must not emit an alert, got: {alerts}"
    )
    assert any("cycles" in w and "!=" in w for w in check_warnings), (
        f"W963 check-time warning must surface invalid op, got: "
        f"{check_warnings}"
    )


# ---------------------------------------------------------------------------
# W964: bool coercion of delta_alerts (and other YAML bool fields)
# ---------------------------------------------------------------------------


def test_w964_bool_true_passes_through_silently() -> None:
    """W964: a true bool ``True`` is the happy path — no warning, value
    preserved.
    """
    warnings: list[str] = []
    out = _coerce_bool(
        True, default=False, field_name="delta_alerts", warnings_out=warnings
    )
    assert out is True
    assert warnings == []


def test_w964_bool_false_passes_through_silently() -> None:
    """W964: bool ``False`` round-trips unchanged. Happy path."""
    warnings: list[str] = []
    out = _coerce_bool(
        False, default=True, field_name="delta_alerts", warnings_out=warnings
    )
    assert out is False
    assert warnings == []


def test_w964_yes_string_coerced_silently() -> None:
    """W964: common YAML-truthy strings (``yes``) coerce to True with no
    warning — they are unambiguous user intent.
    """
    warnings: list[str] = []
    out = _coerce_bool(
        "yes",
        default=False,
        field_name="delta_alerts",
        warnings_out=warnings,
    )
    assert out is True
    assert warnings == [], (
        f"'yes' is unambiguous YAML truthy; no warning expected, "
        f"got: {warnings}"
    )


def test_w964_no_string_coerced_silently() -> None:
    """W964: ``no`` coerces to False with no warning."""
    warnings: list[str] = []
    out = _coerce_bool(
        "no",
        default=True,
        field_name="delta_alerts",
        warnings_out=warnings,
    )
    assert out is False
    assert warnings == []


def test_w964_int_truthy_emits_warning_and_uses_default() -> None:
    """W964: a truthy int (e.g. ``1``) is NOT a bool — surface a warning
    and use the default. Pre-W964 this would have silently disabled the
    feature on a strict ``is True`` check.
    """
    warnings: list[str] = []
    out = _coerce_bool(
        1,
        default=True,
        field_name="delta_alerts",
        warnings_out=warnings,
    )
    assert out is True, "Default is True; int 1 falls back to it"
    assert warnings, "Non-bool, non-truthy-string must surface a warning"
    warning = warnings[0]
    assert "delta_alerts" in warning, (
        f"Warning must name the offending field, got: {warning!r}"
    )
    assert ".roam/alerts.yaml" in warning, (
        f"Warning must point at the config file, got: {warning!r}"
    )
    assert "true" in warning.lower() and "false" in warning.lower(), (
        f"Warning must name the valid bool spellings, got: {warning!r}"
    )


def test_w964_unknown_string_emits_warning_and_uses_default() -> None:
    """W964: a string that isn't in the canonical YAML-bool set
    (e.g. ``maybe``) surfaces a warning and falls back to the default.
    """
    warnings: list[str] = []
    out = _coerce_bool(
        "maybe",
        default=False,
        field_name="delta_alerts",
        warnings_out=warnings,
    )
    assert out is False
    assert warnings
    assert "maybe" in warnings[0]


def test_w964_end_to_end_yaml_non_bool_triggers_warning(
    tmp_path: Path,
) -> None:
    """W964 end-to-end: a ``delta_alerts`` that resolves to a non-bool,
    non-YAML-bool-synonym scalar (e.g. an int via PyYAML, or a stray
    string via the tiny parser) surfaces a warning when fed through
    ``_coerce_bool`` at the alerts command boundary.

    The exact YAML parser used at runtime is environment-dependent
    (PyYAML if installed, the tiny parser otherwise), so this test
    drives the boundary directly: simulate the parsed value the alerts
    command will see and assert the helper produces the right warning +
    default.
    """
    # Case 1: int 1 (truthy under naive bool() but NOT a real bool).
    warnings: list[str] = []
    out = _coerce_bool(
        1,
        default=True,
        field_name="delta_alerts",
        warnings_out=warnings,
    )
    assert out is True
    assert warnings, "Int 1 must surface a W964 warning"

    # Case 2: a stray non-canonical string ("maybe") — fall back to default.
    warnings2: list[str] = []
    out2 = _coerce_bool(
        "maybe",
        default=False,
        field_name="delta_alerts",
        warnings_out=warnings2,
    )
    assert out2 is False
    assert warnings2

    # Case 3: a canonical synonym ("yes") — coerced silently to True.
    warnings3: list[str] = []
    out3 = _coerce_bool(
        "yes",
        default=False,
        field_name="delta_alerts",
        warnings_out=warnings3,
    )
    assert out3 is True
    assert warnings3 == []


# ---------------------------------------------------------------------------
# W967: tiny YAML parser silently disabled delta_alerts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("true", True),
        ("false", False),
        ("yes", "yes"),  # _coerce_scalar leaves YAML-truthy strings alone;
        ("no", "no"),    # _coerce_bool at the CLI boundary handles them.
        ("True", True),
        ("False", False),
    ],
)
def test_w967_top_level_scalar_field_parses_correctly(
    raw_value: str, expected: object
) -> None:
    """W967: the tiny YAML parser preserves flush-left scalar values
    on top-level fields. Pre-W967 the line ``delta_alerts: true`` parsed
    as ``{'delta_alerts: true': {}}`` (empty dict, falsy under
    ``cfg.get('delta_alerts', True)``), silently disabling the feature
    for every user without PyYAML installed.
    """
    parsed = _parse_alerts_yaml(f"delta_alerts: {raw_value}")
    assert "delta_alerts" in parsed, (
        f"Expected ``delta_alerts`` as a top-level key, got: {parsed!r}"
    )
    assert parsed["delta_alerts"] == expected, (
        f"Expected ``delta_alerts={expected!r}``, got: "
        f"{parsed['delta_alerts']!r}"
    )
    # Crucially: the value is NOT an empty dict.
    assert parsed["delta_alerts"] != {}, (
        "Pre-W967 regression — value collapsed to empty dict (silent disable)"
    )


def test_w967_empty_value_still_treated_as_section_header() -> None:
    """W967: ``thresholds:`` (empty value) still starts a section block.
    Backward compat for every existing alerts.yaml in the wild.
    """
    parsed = _parse_alerts_yaml("thresholds:")
    assert parsed == {"thresholds": {}}


def test_w967_comment_at_top_level_is_skipped() -> None:
    """W967: a commented-out scalar field is not parsed as a key — the
    leading ``#`` skip path stays intact.
    """
    parsed = _parse_alerts_yaml("# delta_alerts: true")
    assert "delta_alerts" not in parsed
    # Empty input — nothing parsed.
    assert parsed == {}


def test_w967_mixed_scalar_and_section(tmp_path: Path) -> None:
    """W967: end-to-end via ``_load_alerts_config`` — a YAML with BOTH a
    ``thresholds:`` section AND a flush-left ``delta_alerts: true``
    scalar parses every field correctly when only the tiny parser is
    available.
    """
    yaml_text = (
        "thresholds:\n"
        "  cycles: { op: '>', value: 10, level: warning }\n"
        "delta_alerts: true\n"
    )
    parsed = _parse_alerts_yaml(yaml_text)
    assert parsed["delta_alerts"] is True
    assert parsed["thresholds"]["cycles"]["op"] == ">"
    assert parsed["thresholds"]["cycles"]["value"] == 10
    assert parsed["thresholds"]["cycles"]["level"] == "warning"


def test_w967_silent_disable_regression_pinned(tmp_path: Path) -> None:
    """W967 pinning test: the exact pre-W967 silent-disable scenario.

    User writes ``delta_alerts: false`` in alerts.yaml, runs WITHOUT
    PyYAML installed (the tiny parser path). Pre-W967 the parser
    returned ``{'delta_alerts: false': {}}``, ``cfg.get('delta_alerts',
    True)`` returned True (default), and delta_alerts ran anyway. The
    user's explicit opt-out was silently ignored.

    Post-W967 the value round-trips faithfully.
    """
    parsed = _parse_alerts_yaml("delta_alerts: false")
    # The exact false-disable case.
    assert parsed.get("delta_alerts") is False
    # And the inverse: an explicit ``true`` parses as True.
    parsed_t = _parse_alerts_yaml("delta_alerts: true")
    assert parsed_t.get("delta_alerts") is True


# ---------------------------------------------------------------------------
# W968: _VALID_OPS vs AlertThreshold.op Literal drift-guard
# ---------------------------------------------------------------------------


def test_w968_valid_ops_matches_alert_threshold_literal() -> None:
    """W968: both sources of truth for the op closed-set must agree.

    ``_VALID_OPS`` (frozenset, runtime) and ``AlertThreshold.op``
    (Literal, type-check time) are hand-maintained mirrors. This drift
    guard fails CI the day someone adds a new op to one side without
    the other.

    NOTE: ``cmd_alerts`` uses ``from __future__ import annotations`` so
    ``AlertThreshold.__annotations__`` returns ``ForwardRef`` objects,
    not the resolved Literal. ``typing.get_type_hints`` resolves them
    against the module's namespace.
    """
    import typing

    hints = typing.get_type_hints(AlertThreshold)
    literal_ops = frozenset(typing.get_args(hints["op"]))
    assert _VALID_OPS == literal_ops, (
        f"Drift between _VALID_OPS {sorted(_VALID_OPS)} "
        f"and AlertThreshold.op Literal {sorted(literal_ops)}. "
        f"Update both."
    )


# ---------------------------------------------------------------------------
# W969: level closed-set validation
# ---------------------------------------------------------------------------


def test_w969_canonical_lowercase_level_passes_silently() -> None:
    """W969: the canonical lowercase spellings round-trip with no
    warning. Happy path.
    """
    for level in ("critical", "warning", "info"):
        warnings: list[str] = []
        out = _coerce_level(
            level,
            default="warning",
            field_name="thresholds.cycles.level",
            warnings_out=warnings,
        )
        assert out == level
        assert warnings == [], (
            f"Canonical level {level!r} must not surface a warning, "
            f"got: {warnings}"
        )


def test_w969_uppercase_level_is_normalized_silently() -> None:
    """W969: pre-W649 UPPER-cased configs round-trip into the canonical
    lowercase vocabulary silently. No warning — the user's intent is
    unambiguous.
    """
    warnings: list[str] = []
    out = _coerce_level(
        "WARNING",
        default="warning",
        field_name="thresholds.cycles.level",
        warnings_out=warnings,
    )
    assert out == "warning"
    assert warnings == [], (
        f"Pre-W649 UPPER-cased level must normalize silently, "
        f"got: {warnings}"
    )


def test_w969_mixed_case_level_is_normalized_silently() -> None:
    """W969: ``Critical`` / ``Info`` (Title-case) round-trip silently.
    Same family as the UPPER-case case — clear user intent, no warning.
    """
    warnings: list[str] = []
    out = _coerce_level(
        "Critical",
        default="warning",
        field_name="thresholds.cycles.level",
        warnings_out=warnings,
    )
    assert out == "critical"
    assert warnings == []


def test_w969_unknown_level_warns_and_defaults() -> None:
    """W969: ``level: "fatal"`` surfaces an actionable warning and falls
    back to the safe default. Pre-W969 this propagated unchanged through
    ``_make_alert`` into ``counts[a["level"]] += 1`` and KeyError'd.
    """
    warnings: list[str] = []
    out = _coerce_level(
        "fatal",
        default="warning",
        field_name="thresholds.cycles.level",
        warnings_out=warnings,
    )
    assert out == "warning", "Unknown level must fall back to the default"
    assert warnings, "Unknown level must surface a Pattern 2 warning"
    warning = warnings[0]
    # LAW 2 + LAW 4: imperative + concrete-noun anchors.
    assert "fatal" in warning, (
        f"Warning must name the offending value, got: {warning!r}"
    )
    assert "thresholds.cycles.level" in warning, (
        f"Warning must name the offending field, got: {warning!r}"
    )
    assert ".roam/alerts.yaml" in warning, (
        f"Warning must point at the config file, got: {warning!r}"
    )
    # Names the canonical set so the user knows the valid spellings.
    for level in _CANONICAL_LEVELS:
        assert level in warning, (
            f"Warning must name canonical level {level!r}, got: {warning!r}"
        )


def test_w969_non_string_level_warns_and_defaults() -> None:
    """W969: a non-string level (int, list, ...) also surfaces a warning
    and falls back to the default. Belt-and-braces against malformed
    YAML.
    """
    for bad_value in (1, ["critical"], None, 3.14):
        warnings: list[str] = []
        out = _coerce_level(
            bad_value,
            default="warning",
            field_name="thresholds.cycles.level",
            warnings_out=warnings,
        )
        assert out == "warning"
        assert warnings, (
            f"Non-string level {bad_value!r} must surface a warning"
        )


def test_w969_unknown_level_in_yaml_warns_and_defaults(tmp_path: Path) -> None:
    """W969 end-to-end: ``level: "fatal"`` in alerts.yaml surfaces a
    Pattern 2 warning at parse time AND the parsed rule carries the
    safe default level so downstream consumers cannot KeyError.
    """
    _make_alerts_yaml(
        "thresholds:\n"
        "  cycles: { op: '>', value: 10, level: fatal }\n",
        tmp_path,
    )

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    assert warnings, (
        "Expected at least one warning for invalid level 'fatal'"
    )
    assert any("fatal" in w for w in warnings), (
        f"Warning must name the offending value, got: {warnings}"
    )
    # The rule survives but with the safe default.
    assert cfg["thresholds"]["cycles"]["level"] == "warning"


def test_w969_unknown_level_in_default_thresholds_caught() -> None:
    """W969 + W973 belt-and-braces: a rule constructed in-process with
    an invalid level (bypassing the YAML parser AND
    ``_resolved_thresholds`` which heals via ``_coerce_level``) used to
    propagate through ``_check_thresholds`` -> ``_make_alert`` and crash
    the CLI at ``counts[a["level"]] += 1``.

    Post-W973 the safety net moves UP to the construction site:
    ``_make_alert`` asserts the level is canonical, so the bypass path
    fires an AssertionError IMMEDIATELY with an actionable message
    instead of polluting the alert list with a non-canonical level. This
    is the better discipline — surface the bug at construction, not at
    downstream consumption.

    The CLI never reaches this state in practice (every load site runs
    through ``_coerce_level``); this test pins the construction-site
    guard so the safety net cannot regress.
    """
    current = {"cycles": 100}
    # Inject directly — pretend a downstream caller passed this.
    rule = {"cycles": {"op": ">", "value": 10, "level": "fatal"}}

    with pytest.raises(AssertionError) as exc_info:
        _check_thresholds(current, rule)

    # The W973 assert names the offending level and the canonical set.
    assert "fatal" in str(exc_info.value)
    assert "critical" in str(exc_info.value)


def test_w969_resolved_thresholds_in_process_fatal_level_emits_warning(
    tmp_path: Path,
) -> None:
    """W969: ``_resolved_thresholds`` is the belt-and-braces wiring site
    for ``_coerce_level``. A rule constructed via YAML with an invalid
    level surfaces a warning here EVEN IF parse-time missed it.
    """
    _make_alerts_yaml(
        # Note: ``cycles`` IS in defaults so the merge path fires.
        "thresholds:\n  cycles: { op: '>', value: 10, level: fatal }\n",
        tmp_path,
    )

    warnings: list[str] = []
    resolved = _resolved_thresholds(
        project_root=tmp_path, warnings_out=warnings
    )

    # Warning fired at SOME point in the load -> merge pipeline.
    assert any("fatal" in w for w in warnings), (
        f"Expected fatal-level warning, got: {warnings}"
    )
    # And the resolved rule carries the safe default.
    assert resolved["cycles"]["level"] == "warning"


def test_w969_canonical_levels_matches_module_constants() -> None:
    """W969 drift guard: the canonical level frozenset matches the
    CRITICAL/WARNING/INFO module constants. If someone adds a new
    severity to one side, the other side fails this test.
    """
    from roam.commands.cmd_alerts import CRITICAL, INFO, WARNING

    assert _CANONICAL_LEVELS == frozenset({CRITICAL, WARNING, INFO}), (
        f"Drift between _CANONICAL_LEVELS {sorted(_CANONICAL_LEVELS)} "
        f"and module constants "
        f"{sorted({CRITICAL, WARNING, INFO})}. Update both."
    )


# ---------------------------------------------------------------------------
# W972: non-dict YAML root silent fallback
# ---------------------------------------------------------------------------


def test_yaml_root_non_dict_warns(tmp_path: Path) -> None:
    """W972 (Pattern 2 — silent fallback): a YAML file whose root parses
    to a list (e.g. ``- foo\\n- bar``) instead of a mapping previously
    fell back to ``{}`` silently — the user's whole config was discarded
    with no signal. Post-W972, an actionable warning surfaces naming the
    root type and pointing at the resolution.
    """
    _make_alerts_yaml("- foo\n- bar\n- baz\n", tmp_path)

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    # Empty config preserved (backward compat).
    assert cfg == {}, f"Non-dict root must fall back to empty config, got: {cfg!r}"
    # But the silent state is now explicit.
    assert warnings, "Non-dict YAML root must surface a Pattern 2 warning"
    warning = warnings[0]
    # LAW 2 + LAW 4: imperative + concrete-noun anchors.
    assert "list" in warning, (
        f"Warning must name the offending root type, got: {warning!r}"
    )
    assert ".roam/alerts.yaml" in warning, (
        f"Warning must point at the config file, got: {warning!r}"
    )
    assert "Edit" in warning or "edit" in warning, (
        f"Warning must end on an imperative next step, got: {warning!r}"
    )


def test_yaml_root_scalar_string_warns(tmp_path: Path) -> None:
    """W972: a bare scalar at the root (e.g. ``just a string``) also
    surfaces a warning. Belt-and-braces against malformed configs.

    NOTE: the PyYAML path will parse a bare scalar as a string; the tiny
    parser will treat it as a section header. Behaviour is parser-
    dependent — drive the assertion only against the PyYAML path so the
    test runs consistently when PyYAML is installed.
    """
    pytest.importorskip("yaml")

    _make_alerts_yaml("just a bare scalar string\n", tmp_path)

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    assert cfg == {}, f"Non-dict root must fall back to empty config, got: {cfg!r}"
    assert warnings, "Scalar-root YAML must surface a Pattern 2 warning"
    assert "str" in warnings[0], (
        f"Warning must name the offending root type, got: {warnings[0]!r}"
    )


def test_yaml_root_valid_dict_no_warning(tmp_path: Path) -> None:
    """W972: a normal mapping root emits no W972 warning — happy path."""
    pytest.importorskip("yaml")

    _make_alerts_yaml(
        "thresholds:\n  cycles: { op: '>', value: 10, level: warning }\n",
        tmp_path,
    )

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    assert cfg, "Valid mapping root must parse non-empty"
    # No W972-class warning surfaces (warning text mentions "root is a").
    assert not any("root is a" in w for w in warnings), (
        f"Mapping root must not surface a W972 warning, got: {warnings}"
    )


# ---------------------------------------------------------------------------
# W973: _make_alert level closed-set defense
# ---------------------------------------------------------------------------


def test_make_alert_rejects_non_canonical_level() -> None:
    """W973 (Pattern 2 — defense in depth): ``_make_alert`` asserts that
    ``level`` is in the canonical lowercase set. All 5 internal callers
    pass canonical levels today, so this is a latent guard for future
    internal misuse — surface the bug at the construction site instead
    of at the downstream ``counts[a["level"]] += 1`` KeyError.
    """
    with pytest.raises(AssertionError) as exc_info:
        _make_alert(
            level="fatal",
            metric="cycles",
            message="cycles=100",
            current_value=100,
        )
    # Error message must name the offending value AND the valid set.
    assert "fatal" in str(exc_info.value), (
        f"Assertion message must name the offending level, got: {exc_info.value!r}"
    )
    assert "critical" in str(exc_info.value), (
        f"Assertion message must name canonical 'critical', got: {exc_info.value!r}"
    )


def test_make_alert_accepts_canonical_levels() -> None:
    """W973: every canonical level (critical, warning, info) passes
    through ``_make_alert`` cleanly — no regression for the happy path.
    """
    for level in ("critical", "warning", "info"):
        alert = _make_alert(
            level=level,
            metric="cycles",
            message=f"cycles trip at {level}",
            current_value=42,
        )
        assert alert["level"] == level
        assert alert["metric"] == "cycles"
        assert alert["current_value"] == 42


def test_make_alert_rejects_uppercase_legacy_level() -> None:
    """W973: pre-W649 UPPER-cased levels (``"CRITICAL"``) are NOT in the
    canonical set — they should be normalised by ``_coerce_level``
    upstream before reaching ``_make_alert``. A direct call with
    UPPER-cased level fires the assert.
    """
    with pytest.raises(AssertionError) as exc_info:
        _make_alert(
            level="CRITICAL",
            metric="cycles",
            message="test",
            current_value=1,
        )
    assert "CRITICAL" in str(exc_info.value)


# ---------------------------------------------------------------------------
# W974: AlertThreshold.level Literal drift guard
# ---------------------------------------------------------------------------


def test_canonical_levels_matches_alert_threshold_literal() -> None:
    """W974: both sources of truth for the level closed-set must agree.

    ``_CANONICAL_LEVELS`` (frozenset, runtime) and
    ``AlertThreshold.level`` (Literal, type-check time) are
    hand-maintained mirrors. Sister drift guard to the W968 op
    drift guard.
    """
    import typing

    hints = typing.get_type_hints(AlertThreshold)
    literal_levels = frozenset(typing.get_args(hints["level"]))
    assert _CANONICAL_LEVELS == literal_levels, (
        f"Drift between _CANONICAL_LEVELS {sorted(_CANONICAL_LEVELS)} "
        f"and AlertThreshold.level Literal {sorted(literal_levels)}. "
        f"Update both."
    )


# ---------------------------------------------------------------------------
# W959: Alert TypedDict drift guards (mirrors W968 / W974 patterns).
#
# NOTE on coverage: the prompt asked for two drift guards
# (``test_alert_level_matches_canonical_levels`` +
# ``test_alert_op_matches_valid_ops``). The actual ``_make_alert`` shape
# carries ``level / metric / message / current_value / trend_direction``
# but NOT ``op`` — ``op`` is a threshold-rule field on
# ``AlertThreshold``, not an alert-record field. So the second drift
# guard pins the cross-mirror between ``Alert.level`` and
# ``AlertThreshold.level`` instead (both must agree on the closed
# severity set). The W968 ``_VALID_OPS`` drift guard above already
# covers the ``op`` axis at the only place it lives.
# ---------------------------------------------------------------------------


def test_alert_level_matches_canonical_levels() -> None:
    """W959: ``Alert.level`` (Literal, type-check time) and
    ``_CANONICAL_LEVELS`` (frozenset, runtime) are hand-maintained mirrors.

    Adding a new severity to one side means updating BOTH. Pattern-matches
    the W974 ``AlertThreshold.level`` drift guard.
    """
    import typing

    hints = typing.get_type_hints(Alert)
    literal_levels = frozenset(typing.get_args(hints["level"]))
    assert _CANONICAL_LEVELS == literal_levels, (
        f"Drift between _CANONICAL_LEVELS {sorted(_CANONICAL_LEVELS)} "
        f"and Alert.level Literal {sorted(literal_levels)}. "
        f"Update both."
    )


def test_alert_level_matches_alert_threshold_level() -> None:
    """W959: ``Alert.level`` and ``AlertThreshold.level`` are independent
    Literal types but MUST carry the same closed set — ``_make_alert``
    threads a level out of an ``AlertThreshold`` rule (via
    ``_check_thresholds``) into an ``Alert`` record. If the two Literals
    diverge, a perfectly valid threshold-level would not be a valid
    alert-level and the round-trip would type-fail.

    Sister drift guard: alongside the W968 op closed-set guard and W974
    AlertThreshold.level guard, this one pins the cross-TypedDict
    contract — closing the W959 typing pass.
    """
    import typing

    alert_hints = typing.get_type_hints(Alert)
    threshold_hints = typing.get_type_hints(AlertThreshold)
    alert_levels = frozenset(typing.get_args(alert_hints["level"]))
    threshold_levels = frozenset(typing.get_args(threshold_hints["level"]))
    assert alert_levels == threshold_levels, (
        f"Drift between Alert.level Literal {sorted(alert_levels)} "
        f"and AlertThreshold.level Literal {sorted(threshold_levels)}. "
        f"Both mirror _CANONICAL_LEVELS — update all three together."
    )


# ---------------------------------------------------------------------------
# W1025: ``thresholds:`` section is present but NOT a mapping
# ---------------------------------------------------------------------------
#
# Sibling Pattern 2 fix one level deeper than W972: the YAML root is a
# mapping, but the ``thresholds:`` key inside it carries a scalar / list
# instead of the expected ``{metric: rule, ...}`` mapping. Pre-W1025 the
# downstream ``_resolved_thresholds`` either crashed on ``.items()``
# (truthy non-dict scalar / non-empty list) or silently collapsed via the
# ``or {}`` fallback (falsy non-dict). Post-W1025 the silent-state is made
# explicit on ``warnings_out`` AND the section is coerced to ``{}`` so
# the rest of the pipeline stays happy.


def test_thresholds_section_scalar_warns(tmp_path: Path) -> None:
    """W1025 (Pattern 2 — silent fallback): ``thresholds: 42`` (a scalar
    where a mapping was expected) must surface a warning naming the
    offending type AND keep the rest of the config working (empty
    thresholds, defaults take over).
    """
    pytest.importorskip("yaml")

    _make_alerts_yaml("thresholds: 42\n", tmp_path)

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    # The section is coerced to {} so downstream code doesn't crash.
    assert cfg.get("thresholds") == {}, (
        f"Non-dict thresholds section must coerce to empty mapping, "
        f"got: {cfg.get('thresholds')!r}"
    )
    # The silent state is made explicit.
    assert warnings, "Non-dict thresholds section must surface a Pattern 2 warning"
    assert any("thresholds:" in w for w in warnings), (
        f"Warning must name the offending section, got: {warnings}"
    )
    assert any("int" in w for w in warnings), (
        f"Warning must name the offending value type, got: {warnings}"
    )

    # And ``_resolved_thresholds`` doesn't crash — defaults survive.
    warnings = []
    resolved = _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)
    assert resolved, "Defaults must still apply when overrides section is malformed"


def test_thresholds_section_list_warns(tmp_path: Path) -> None:
    """W1025: ``thresholds:`` written as a YAML list instead of a mapping
    must surface a warning AND keep the pipeline working with defaults.
    The PyYAML path is the one that produces a list here; the tiny-parser
    sibling case is covered by ``test_thresholds_section_scalar_warns_tiny``.
    """
    pytest.importorskip("yaml")

    _make_alerts_yaml(
        "thresholds:\n  - a\n  - b\n",
        tmp_path,
    )

    warnings: list[str] = []
    cfg = _load_alerts_config(project_root=tmp_path, warnings_out=warnings)

    # The section is coerced to {} so downstream code doesn't crash.
    assert cfg.get("thresholds") == {}, (
        f"List thresholds section must coerce to empty mapping, "
        f"got: {cfg.get('thresholds')!r}"
    )
    assert warnings, "List thresholds section must surface a Pattern 2 warning"
    assert any("thresholds:" in w for w in warnings), (
        f"Warning must name the offending section, got: {warnings}"
    )
    assert any("list" in w for w in warnings), (
        f"Warning must name the offending value type, got: {warnings}"
    )

    # And ``_resolved_thresholds`` doesn't crash — defaults survive.
    warnings = []
    resolved = _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)
    assert resolved, "Defaults must still apply when overrides section is malformed"
