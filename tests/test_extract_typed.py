"""W1038 — tests for the ``extract_typed`` config-shape helper.

Covers the recurring W1019/W1019b/W1019c/W1019d/W1019e/W1036/W1051/W1052
micro-pattern: a top-level key is fetched from a parsed config mapping,
its type is checked, and on mismatch a structured warning is appended
and the default is returned. The helper consolidates 8+ inline sites
across cmd_check_rules / cmd_alerts / cmd_budget / cmd_fitness /
cmd_health into one shape.

Mandate: silent-empty behaviour when ``warnings_out`` is ``None`` stays
explicit (pre-Pattern-2 callers depend on it). When the accumulator is
supplied, every shape-mismatch surfaces an actionable warning naming
the key, the actual type, the expected shape, and the resolution
(default value).
"""

from __future__ import annotations

from roam.commands._yaml_loader import extract_typed

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_type_match_returns_value_no_warning() -> None:
    """Value is the expected type — return it verbatim, never warn."""
    cfg = {"rules": [{"id": "r1"}, {"id": "r2"}]}
    warnings_out: list[str] = []
    result = extract_typed(cfg, "rules", list, [], warnings_out=warnings_out)
    assert result == [{"id": "r1"}, {"id": "r2"}]
    assert warnings_out == []


def test_happy_path_dict_type_match() -> None:
    """Same as above for a dict-typed extract — return the parsed mapping."""
    cfg = {"health": {"health_min": 60, "cycle_max": 0}}
    warnings_out: list[str] = []
    result = extract_typed(cfg, "health", dict, {}, warnings_out=warnings_out)
    assert result == {"health_min": 60, "cycle_max": 0}
    assert warnings_out == []


# ---------------------------------------------------------------------------
# Type-mismatch — with accumulator
# ---------------------------------------------------------------------------


def test_type_mismatch_with_accumulator_returns_default_appends_warning() -> None:
    """Wrong shape + accumulator set: default returned, warning appended."""
    cfg = {"rules": "not-a-list"}
    warnings_out: list[str] = []
    result = extract_typed(cfg, "rules", list, [], warnings_out=warnings_out)
    assert result == []
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    # The warning carries: key, actual type, expected shape, default.
    assert "`rules`" in msg
    assert "'str'" in msg  # actual type
    assert "expected list" in msg  # bare expected_type.__name__
    assert "Treating as default []" in msg


def test_type_mismatch_dict_expected_mapping_wording() -> None:
    """Default warning for a dict-typed extract uses the class name."""
    cfg = {"health": [1, 2, 3]}
    warnings_out: list[str] = []
    result = extract_typed(cfg, "health", dict, {}, warnings_out=warnings_out)
    assert result == {}
    assert len(warnings_out) == 1
    assert "`health`" in warnings_out[0]
    assert "'list'" in warnings_out[0]
    assert "expected dict" in warnings_out[0]


# ---------------------------------------------------------------------------
# Type-mismatch — with warnings_out=None
# ---------------------------------------------------------------------------


def test_type_mismatch_with_none_accumulator_returns_default_silently() -> None:
    """Pre-W1038 silent-empty path: no warning emitted when ``warnings_out`` is ``None``."""
    cfg = {"rules": 42}
    # Default sentinel — no accumulator supplied at all.
    result = extract_typed(cfg, "rules", list, [])
    assert result == []
    # An explicit ``warnings_out=None`` matches the same silent path.
    result2 = extract_typed(cfg, "rules", list, [], warnings_out=None)
    assert result2 == []


# ---------------------------------------------------------------------------
# Key absent
# ---------------------------------------------------------------------------


def test_key_absent_returns_default_no_warning() -> None:
    """Absent key is the default state — return default, never warn.

    The helper only warns on SHAPE mismatch; key-presence checks are
    intentionally left to callers (they often carry the missing-key
    warning with a richer, recipe-specific vocabulary — see cmd_fitness
    / cmd_budget / cmd_health "no `<key>:` key" wording).
    """
    cfg: dict = {}
    warnings_out: list[str] = []
    result = extract_typed(cfg, "rules", list, [], warnings_out=warnings_out)
    assert result == []
    assert warnings_out == []


# ---------------------------------------------------------------------------
# ``context`` kwarg
# ---------------------------------------------------------------------------


def test_context_kwarg_prepended_to_warning() -> None:
    """Non-empty ``context`` is prepended with a ``: `` separator."""
    cfg = {"rules": "broken"}
    warnings_out: list[str] = []
    extract_typed(
        cfg,
        "rules",
        list,
        [],
        warnings_out=warnings_out,
        context="fitness: 'path/to/.roam/fitness.yaml'",
    )
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert msg.startswith("fitness: 'path/to/.roam/fitness.yaml': ")
    assert "`rules`" in msg


def test_empty_context_no_prefix() -> None:
    """Empty ``context`` omits the prefix entirely (no leading separator)."""
    cfg = {"rules": "broken"}
    warnings_out: list[str] = []
    extract_typed(cfg, "rules", list, [], warnings_out=warnings_out)
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    # Without context, the warning must NOT start with a stray ': '.
    assert msg.startswith("`rules`")


# ---------------------------------------------------------------------------
# ``expected_shape`` kwarg
# ---------------------------------------------------------------------------


def test_expected_shape_kwarg_overrides_bare_class_name() -> None:
    """Custom shape clause replaces the bare ``__name__`` in the warning."""
    cfg = {"rules": 7}
    warnings_out: list[str] = []
    extract_typed(
        cfg,
        "rules",
        list,
        [],
        warnings_out=warnings_out,
        expected_shape="a list of `{id, threshold?, severity?}` entries",
    )
    msg = warnings_out[0]
    # Shape clause replaces "expected list" with the richer phrasing.
    assert "expected a list of" in msg
    assert "{id, threshold?, severity?}" in msg
    # Bare class name is NOT used when expected_shape is supplied.
    assert "expected list." not in msg


def test_tuple_expected_type_default_shape_joins_names() -> None:
    """When ``expected_type`` is a tuple, the default warning joins names with ' or '."""
    cfg = {"value": "string"}
    warnings_out: list[str] = []
    extract_typed(
        cfg,
        "value",
        (int, float),
        0,
        warnings_out=warnings_out,
    )
    msg = warnings_out[0]
    assert "expected int or float" in msg


# ---------------------------------------------------------------------------
# ``validator`` kwarg (W1038-followup)
# ---------------------------------------------------------------------------


def test_validator_passes_returns_value_no_warning() -> None:
    """Validator returns True — value returned verbatim, no warning."""
    cfg = {"profile": "strict-security"}
    warnings_out: list[str] = []
    result = extract_typed(
        cfg,
        "profile",
        str,
        "",
        warnings_out=warnings_out,
        validator=lambda v: bool(v.strip()),
        expected_shape="non-empty string",
    )
    assert result == "strict-security"
    assert warnings_out == []


def test_validator_fails_returns_default_appends_warning_with_expected_shape() -> None:
    """Validator returns False — default returned, warning names ``expected_shape``.

    Captures the W1038-followup non-empty-string sub-pattern: type passes
    (``isinstance("   ", str)`` is True) but the validator rejects it
    semantically, so the helper warns and falls back to the default.
    """
    cfg = {"profile": "   "}  # whitespace-only — type passes, validator fails.
    warnings_out: list[str] = []
    result = extract_typed(
        cfg,
        "profile",
        str,
        "",
        warnings_out=warnings_out,
        validator=lambda v: bool(v.strip()),
        expected_shape="non-empty string",
    )
    assert result == ""
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    assert "`profile`" in msg
    # When the validator fails on a type-match, the warning shows the
    # offending value (repr) rather than its type — the type was right;
    # the value content was wrong.
    assert "'   '" in msg
    assert "expected non-empty string" in msg
    assert "Treating as default ''" in msg


def test_validator_and_type_mismatch_type_check_wins() -> None:
    """Type-mismatch short-circuits BEFORE the validator runs.

    Guarantee: when the value is the wrong type, the warning names the type
    failure (``"`key` is 'int', expected ..."``), NOT the validator failure.
    The isinstance() check fires first; the validator only sees values that
    already passed the type gate. This keeps warning attribution honest.
    """
    cfg = {"profile": 42}  # wrong type — int, not str.
    warnings_out: list[str] = []
    calls: list[object] = []

    def tracking_validator(v: str) -> bool:
        calls.append(v)
        return True  # would pass if invoked — but it must NOT be invoked.

    result = extract_typed(
        cfg,
        "profile",
        str,
        "",
        warnings_out=warnings_out,
        validator=tracking_validator,
        expected_shape="non-empty string",
    )
    assert result == ""
    # Validator never ran — type-mismatch short-circuits first.
    assert calls == []
    assert len(warnings_out) == 1
    msg = warnings_out[0]
    # Warning attribution: names the actual TYPE, not the value's content.
    assert "is 'int'" in msg
    assert "expected non-empty string" in msg
