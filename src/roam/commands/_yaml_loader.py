"""Shared YAML config loader with structured warnings (Pattern-2 substrate).

Replaces five-to-seven bespoke ``_load_<thing>`` functions across the
commands package, each of which implements the same Pattern-2 fix shape
(W706, W918+, W994+W995, W1009). The helper owns the I/O + parser
fallback + canonical warning-string format; per-callsite schema is
plugged in via the ``schema_validator`` and ``tiny_parser`` callbacks.

See ``(internal memo)`` for the survey + rationale
(W1016). This module is Phase 1 (W1018): the helper ships UNUSED. Phase 2
(W1019) migrates the five clean-win callsites.

Mandate: callers never see exceptions, always see a structured
``warnings_out`` accumulator on malformed input. When ``warnings_out`` is
None, behaviour stays byte-identical to the pre-Pattern-2 silent-empty
shape (the legacy callers depend on that for happy-path envelopes).

Canonical warning shape (formatted by :func:`append_warning`):

    ``{config_label}: {path!s}: {body}``

The ``body`` clause MUST name (a) the offending shape, (b) the resolution
the helper applied, and (c) an imperative fix step. See the W706 reference
warnings in ``finding_suppress.py::_load_ignore_findings_file`` for the
vocabulary the validator strings should mirror.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, TypeVar, overload

from roam.output.formatter import WarningsOut

__all__ = [
    "LOAD_STATUSES",
    "WarningsOut",
    "append_warning",
    "extract_typed",
    "load_yaml_with_warnings",
    "parse_rule_list",
]

T = TypeVar("T")

# W1030 — closed-enum disambiguation for the load_yaml_with_warnings status
# kwarg. Surfaces what the helper actually saw on disk so callers can tell
# "missing file" / "intentional empty config" / "truncated/malformed file"
# apart instead of conflating all three on the empty-container return path.
#
# - "ok"               — file existed, parsed cleanly, root type + schema OK.
# - "missing"          — path did not exist (file-not-present is not an error).
# - "empty_file"       — file existed but its on-disk bytes are zero-length
#                        (or whitespace-only). Distinct from "all comments,
#                        zero documented entries" which is "empty_yaml".
# - "empty_yaml"       — file had content but the YAML parser returned None
#                        (e.g. "# only comments\n"). Documented shape is
#                        intentionally empty.
# - "read_error"       — OSError on read_text (permissions / encoding).
# - "parse_error"      — PyYAML / JSON / tiny-parser raised on parse.
# - "wrong_root_type"  — parsed object's root is the wrong shape (list when
#                        a mapping was expected, scalar when either, ...).
# - "schema_invalid"   — schema_validator returned a non-empty warning list.
LOAD_STATUSES: tuple[str, ...] = (
    "ok",
    "missing",
    "empty_file",
    "empty_yaml",
    "read_error",
    "parse_error",
    "wrong_root_type",
    "schema_invalid",
)
LoadStatus = Literal[
    "ok",
    "missing",
    "empty_file",
    "empty_yaml",
    "read_error",
    "parse_error",
    "wrong_root_type",
    "schema_invalid",
]

# Per-callsite schema validator. Receives the parsed object (dict, or
# top-level list when ``allow_list_root=True``), returns an empty list on
# success or a list of pre-formatted warning strings on validation
# failure. The validator OWNS the warning vocabulary; the helper owns the
# I/O + parser-fallback shape.
SchemaValidator = Callable[[Any], list[str]]

# Per-callsite fallback parser used when PyYAML is unavailable. Receives
# the raw text; returns the parsed object. Allowed to return ``None`` or
# raise ``ValueError`` to signal "I could not parse this." Each callsite
# supplies its own — the tiny YAML parsers are intentionally schema-specific
# (W706 expects a top-level ``rules:`` list, W994 expects ``suppressions:``,
# etc.).
TinyParser = Callable[[str], Any]


def append_warning(
    warnings_out: WarningsOut,
    config_label: str,
    path: Path,
    body: str,
) -> None:
    """Format-and-append the Pattern-2 canonical warning shape.

    The helper prepends the standard label + path prefix so every loader
    emits warnings in the same shape::

        ``{config_label}: {path!s}: {body}``

    ``body`` carries the failure-and-resolution clause; the prefix is
    centralised here so the seven callsites converge on one vocabulary.

    No-ops when ``warnings_out`` is ``None`` — pre-Pattern-2 callers stay
    byte-identical (the silent-empty fallback shape is intentional for
    happy-path envelopes).
    """
    if warnings_out is None:
        return
    warnings_out.append(f"{config_label}: {str(path)!r}: {body}")


# W1038 — shared "load → check type → warn-or-default" extractor.
def extract_typed(
    config: Mapping[str, Any],
    key: str,
    expected_type: type[T] | tuple[type, ...],
    default: T,
    *,
    warnings_out: WarningsOut = None,
    context: str = "",
    expected_shape: str = "",
    validator: Callable[[T], bool] | None = None,
) -> T:
    """Extract a typed value from a config dict; warn + default on shape mismatch.

    Captures the recurring W1019/W1019b/W1019c/W1019d/W1019e/W1036/W1051/W1052
    micro-pattern: a top-level key is fetched from a parsed config mapping,
    its type is checked, and on mismatch a structured warning is appended and
    the default is returned. Without this helper the shape appeared 8+ times
    across :mod:`cmd_check_rules`, :mod:`cmd_alerts`, :mod:`cmd_budget`,
    :mod:`cmd_fitness`, :mod:`cmd_health`. W1038 consolidates the shape.

    Parameters
    ----------
    config
        Parsed config dict (the post-``load_yaml_with_warnings`` mapping).
    key
        Top-level key to extract.
    expected_type
        Class (or tuple of classes) the value must be an instance of. Use a
        tuple to accept multiple shapes (e.g. ``(int, float)`` for numeric).
        On mismatch the warning names ``expected_type.__name__`` when a single
        class is supplied, or ``expected_shape`` when supplied (preferred for
        tuple-typed checks where the joint name is more readable).
    default
        Value returned on key-absent, type-mismatch, or value-is-None paths.
        On type-mismatch the warning includes ``{default!r}`` so the agent
        can see the resolution.
    warnings_out
        Append-only accumulator (see :data:`WarningsOut`). When ``None``,
        the helper stays silent — byte-identical to a pre-W1038 silent-empty
        callsite. The silent path is an EXPLICIT opt-in for pre-Pattern-2
        callers; never default-on warning emission.
    context
        Short prefix prepended to the warning (e.g. ``"fitness: 'rules.yaml'"``).
        Mirrors the :func:`append_warning` ``{config_label}: {path!r}`` shape
        callers already build — the helper does not enforce a format on it,
        so callsites can carry their existing vocabulary.
    expected_shape
        Optional clause used in the warning body in place of the bare
        ``expected_type.__name__``. Useful when the expected shape needs a
        more descriptive phrasing than the bare class name (e.g.
        ``"a list"`` for ``list`` to read naturally, or ``"a mapping"`` for
        ``dict``). When empty, the helper uses ``expected_type.__name__``.
    validator
        Optional callable run on the value AFTER the type check passes.
        Captures the recurring "right type but semantically empty / out of
        range" sub-pattern (W1038-followup; e.g. ``isinstance(v, str) and
        v.strip()`` for non-empty strings, ``isinstance(v, int) and v > 0``
        for positive ints). When ``validator(value)`` returns ``False`` the
        helper treats the value as a shape mismatch — returns ``default`` and
        appends a warning using ``expected_shape`` if supplied (e.g.
        ``"non-empty string"``) or the bare type name otherwise. Type-mismatch
        always wins: an instance-of-wrong-type short-circuits before the
        validator runs so the warning correctly names the type failure.

    Returns
    -------
    The value at ``config[key]`` when it is an instance of ``expected_type``
    AND ``validator(value)`` is ``True`` (when supplied); otherwise
    ``default`` (key-absent OR shape-mismatch OR validator-fail — all paths
    converge on the same resolution, the warning distinguishes them via
    wording).

    Warning shape (when ``warnings_out`` is set and the value is the wrong type)::

        ``{context}: `{key}` is '{actual_type}', expected {shape}. Treating as default {default!r}.``

    When ``context`` is empty the leading prefix is omitted, but the rest of
    the shape is invariant so callers can assert on substrings
    (``"expected a list"``, ``"expected a mapping"``, ``` `rules` ```).
    """
    value = config.get(key, default)
    if isinstance(value, expected_type):
        # Type check passed; run optional semantic validator (W1038-followup).
        # Type-mismatch wins over validator-fail by design — the isinstance
        # short-circuit above ensures the validator only runs on values of
        # the expected type, so the warning never misattributes a type
        # failure to the validator clause.
        if validator is not None and not validator(value):
            if warnings_out is not None:
                if expected_shape:
                    shape_name = expected_shape
                elif isinstance(expected_type, tuple):
                    shape_name = " or ".join(t.__name__ for t in expected_type)
                else:
                    shape_name = expected_type.__name__
                where = f"{context}: " if context else ""
                warnings_out.append(
                    f"{where}`{key}` is {value!r}, expected {shape_name}. Treating as default {default!r}."
                )
            return default
        return value
    if warnings_out is not None:
        # ``expected_type`` may be a tuple (e.g. ``(int, float)``); guard the
        # ``__name__`` access so the warning never raises.
        if expected_shape:
            shape_name = expected_shape
        elif isinstance(expected_type, tuple):
            shape_name = " or ".join(t.__name__ for t in expected_type)
        else:
            shape_name = expected_type.__name__
        where = f"{context}: " if context else ""
        warnings_out.append(
            f"{where}`{key}` is {type(value).__name__!r}, expected {shape_name}. Treating as default {default!r}."
        )
    return default


def _run_tiny_parser_branch(
    text: str,
    path: Path,
    *,
    tiny_parser: TinyParser | None,
    config_label: str,
    warnings_out: WarningsOut,
    empty: dict[str, Any] | list[Any],
    force: bool,
) -> tuple[bool, Any]:
    """Run the no-PyYAML fallback: strict JSON, then tiny_parser.

    Returns ``(success, value)``. When ``success`` is False, ``value`` is
    the empty-container sentinel the caller should return. When True,
    ``value`` is the parsed object that should proceed to root-type +
    schema checks.

    ``force=True`` skips the strict-JSON probe and routes straight to
    ``tiny_parser`` (W1040 — used by ``force_tiny_parser=True`` callsites
    that intentionally want their domain-permissive parser as sole engine).
    Warning wording is identical to the ImportError fallback so the warning
    vocabulary stays single-sourced.
    """
    if not force:
        try:
            return True, _json.loads(text)
        except _json.JSONDecodeError as exc:
            if tiny_parser is None:
                append_warning(
                    warnings_out,
                    config_label,
                    path,
                    f"PyYAML not installed and content is not valid JSON: "
                    f"{exc}. Install PyYAML or use a JSON-shaped file.",
                )
                return False, empty
    try:
        data = tiny_parser(text) if tiny_parser is not None else None
    except ValueError as exc2:
        append_warning(
            warnings_out,
            config_label,
            path,
            f"PyYAML not installed; fallback parser failed: {exc2}. Install PyYAML or use the documented shape.",
        )
        return False, empty
    if data is None or data == {} or data == []:
        # tiny_parser found nothing recognisable — same outcome as
        # a parse error from the agent's perspective.
        append_warning(
            warnings_out,
            config_label,
            path,
            "PyYAML not installed and the no-PyYAML fallback parser "
            "could not extract any documented-shape entries. "
            "Install PyYAML or use the documented shape.",
        )
        return False, empty
    return True, data


@overload
def load_yaml_with_warnings(
    path: Path,
    *,
    schema_validator: SchemaValidator | None = ...,
    tiny_parser: TinyParser | None = ...,
    allow_list_root: Literal[False] = False,
    config_label: str = ...,
    warnings_out: WarningsOut = ...,
    parse_error_label: str = ...,
    force_tiny_parser: bool = ...,
    return_status: Literal[False] = False,
) -> Mapping[str, Any] | None: ...


@overload
def load_yaml_with_warnings(
    path: Path,
    *,
    schema_validator: SchemaValidator | None = ...,
    tiny_parser: TinyParser | None = ...,
    allow_list_root: Literal[True],
    config_label: str = ...,
    warnings_out: WarningsOut = ...,
    parse_error_label: str = ...,
    force_tiny_parser: bool = ...,
    return_status: Literal[False] = False,
) -> Mapping[str, Any] | list[Any] | None: ...


@overload
def load_yaml_with_warnings(
    path: Path,
    *,
    schema_validator: SchemaValidator | None = ...,
    tiny_parser: TinyParser | None = ...,
    allow_list_root: Literal[False] = False,
    config_label: str = ...,
    warnings_out: WarningsOut = ...,
    parse_error_label: str = ...,
    force_tiny_parser: bool = ...,
    return_status: Literal[True],
) -> tuple[Mapping[str, Any] | None, LoadStatus]: ...


@overload
def load_yaml_with_warnings(
    path: Path,
    *,
    schema_validator: SchemaValidator | None = ...,
    tiny_parser: TinyParser | None = ...,
    allow_list_root: Literal[True],
    config_label: str = ...,
    warnings_out: WarningsOut = ...,
    parse_error_label: str = ...,
    force_tiny_parser: bool = ...,
    return_status: Literal[True],
) -> tuple[Mapping[str, Any] | list[Any] | None, LoadStatus]: ...


def load_yaml_with_warnings(
    path: Path,
    *,
    schema_validator: SchemaValidator | None = None,
    tiny_parser: TinyParser | None = None,
    allow_list_root: bool = False,
    config_label: str = "config",
    warnings_out: WarningsOut = None,
    parse_error_label: str = "YAML",
    force_tiny_parser: bool = False,
    return_status: bool = False,
) -> Any:
    """Load a YAML file and surface every silent-fallback path as a warning.

    Returns
    -------
    * ``None`` when the file does not exist (callers treat as "default
      state"; no warning emitted — absence is not an error).
    * The parsed object (``dict``, or ``list`` when
      ``allow_list_root=True``) when parsing succeeds AND
      ``schema_validator`` returns ``[]`` (or no validator was supplied).
    * An empty container (``{}`` by default, ``[]`` when
      ``allow_list_root=True``) when ANY of: OSError on read, malformed
      YAML/JSON, wrong root type, or ``schema_validator`` returned a
      non-empty list. In every such case, ``warnings_out`` (when
      supplied) is populated with one actionable warning per failure mode.

    The "empty container, not None, on parse failure" rule matters:
    callers iterate the result with
    ``for x in result.get("rules", []):`` — returning ``{}`` lets that
    loop short-circuit naturally. Returning ``None`` would force every
    caller to ``if result is None`` first. The historical bespoke loaders
    (W706, W918, W994, W1009) all converged on the empty-container shape
    and the helper preserves it.

    Parameters
    ----------
    path
        File to load. Missing-file path is the only short-circuit that
        returns ``None`` — every other failure returns the empty
        container.
    schema_validator
        Optional callable run on the parsed object. Returns a list of
        warning strings (already formatted with their full message); the
        helper appends each through :func:`append_warning` and falls back
        to the empty container when the list is non-empty. When the
        validator returns ``[]``, the parsed object is returned as-is.
    tiny_parser
        Optional fallback parser invoked when PyYAML import fails. When
        ``None`` and PyYAML is unavailable, the helper tries
        :func:`json.loads` and appends a warning if that also fails. Each
        existing tiny-YAML parser understands a specific schema shape
        (rules-list, suppressions-list, thresholds-with-sections); they
        are NOT interchangeable, so the callback is per-callsite.
    allow_list_root
        When ``True``, a top-level list is accepted as the parsed root.
        When ``False`` (default), a list root is treated as a wrong-root-
        type failure.
    config_label
        Short label used to namespace warning strings
        (``"ignore-findings"``, ``"alerts"``, ``"smells-suppress"``, ...).
        See :func:`append_warning`.
    warnings_out
        Append-only accumulator the caller drains into the envelope's
        ``summary.warnings_out``. When ``None``, no warnings are
        collected — the helper stays silent-empty on failure (byte-
        identical to pre-Pattern-2 behaviour, by design).
    parse_error_label
        Format name used in the "malformed {label}: ..." warning body
        when the on-disk content fails to parse (W1035). Defaults to
        ``"YAML"`` so every pre-W1035 caller produces byte-identical
        warning text. JSON-shaped callsites
        (``_load_per_finding_suppressions``, SARIF
        ``_load_suppressions``) pass ``"JSON"`` for accurate wording::

            load_yaml_with_warnings(
                path,
                config_label="per-finding-suppressions",
                parse_error_label="JSON",
                warnings_out=warnings,
            )

        Any non-empty string is accepted — the helper does not validate
        the label, so callers can use ``"TOML"`` or any future format
        name without code changes here.
    force_tiny_parser
        When ``True``, the PyYAML import + ``yaml.safe_load`` branch is
        skipped entirely and the supplied ``tiny_parser`` is used as the
        SOLE parser — the strict-JSON probe is also skipped. Required when
        a callsite's domain-aware permissive parser intentionally tolerates
        values PyYAML rejects (W1040 — e.g. ``smells_suppress`` accepts
        ``expires: 2026-13-01``; PyYAML's strict timestamp coercion would
        short-circuit to the empty container before the validator gets to
        emit its warning). Raises ``ValueError`` at the boundary when
        ``tiny_parser is None``::

            load_yaml_with_warnings(
                path,
                tiny_parser=_parse_smells_suppress,
                schema_validator=_validate_smells_suppress,
                config_label="smells-suppress",
                warnings_out=warnings,
                force_tiny_parser=True,
            )
    return_status
        When ``True`` (W1030), the helper returns ``(value, status)``
        instead of just ``value``. ``status`` is a closed enum drawn from
        :data:`LOAD_STATUSES`: ``"ok"`` / ``"missing"`` / ``"empty_file"``
        / ``"empty_yaml"`` / ``"read_error"`` / ``"parse_error"`` /
        ``"wrong_root_type"`` / ``"schema_invalid"``. The status
        disambiguates the empty-container return path so callers can
        tell "file is intentionally empty" (``empty_yaml`` -- file has
        bytes but parses to None, e.g. comments-only) from "file is
        truncated / zero-byte" (``empty_file``) from "file is missing"
        (``missing`` -- same as the bare ``None`` return). Default
        ``False`` preserves the legacy single-value return shape so every
        pre-W1030 callsite stays byte-identical.

        Example use::

            data, status = load_yaml_with_warnings(
                path, config_label="alerts", warnings_out=warnings,
                return_status=True,
            )
            if status == "empty_file":
                warnings.append("alerts: file is empty; using defaults.")
    """
    if force_tiny_parser and tiny_parser is None:
        raise ValueError(
            "force_tiny_parser=True requires tiny_parser to be set "
            "(callsite must supply a domain-aware permissive parser)."
        )

    empty: dict[str, Any] | list[Any] = [] if allow_list_root else {}

    def _ret(value: Any, status: LoadStatus) -> Any:
        # W1030 centralised exit. Pre-W1030 callers see the bare value
        # (return_status default False); opt-in callers see
        # ``(value, status)`` so they can distinguish "file is
        # intentionally empty" from "file is missing" from "file is
        # malformed" without re-statting the path.
        return (value, status) if return_status else value

    if not path.exists():
        # Absence is not an error -- it's the default state. No warning;
        # callers distinguish "no config" from "broken config" via the
        # None return (or status=="missing" when return_status=True).
        return _ret(None, "missing")

    # ---- Read ------------------------------------------------------------
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        append_warning(
            warnings_out,
            config_label,
            path,
            f"could not read file: {exc}. Treating as empty; fix file permissions / encoding to re-enable.",
        )
        return _ret(empty, "read_error")

    # W1030 -- empty-file disambiguation. A zero-byte (or whitespace-only)
    # file is a distinct on-disk state from "file with content that parses
    # to None" (comments-only YAML). Detect it BEFORE parsing so the
    # opt-in caller can act on it. No warning here -- an empty file is a
    # valid (if unhelpful) input; the caller decides whether to warn
    # based on whether the config is required.
    if not text.strip():
        return _ret(empty, "empty_file")

    # ---- Parse: PyYAML -> JSON -> tiny_parser ----------------------------
    data: Any
    if force_tiny_parser:
        # W1040: skip PyYAML + strict JSON entirely. Domain-aware tiny
        # parsers (e.g. smells_suppress) intentionally tolerate values
        # PyYAML rejects — routing through PyYAML would short-circuit to
        # empty container BEFORE the schema validator can emit its
        # warning. tiny_parser is guaranteed non-None by the boundary
        # check above.
        ok, value = _run_tiny_parser_branch(
            text,
            path,
            tiny_parser=tiny_parser,
            config_label=config_label,
            warnings_out=warnings_out,
            empty=empty,
            force=True,
        )
        if not ok:
            return _ret(value, "parse_error")
        data = value
    else:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            # No PyYAML: try strict JSON first (every well-formed JSON is also
            # well-formed YAML 1.2, so JSON files on disk still load), then a
            # callsite-supplied minimal-YAML fallback so the documented shape
            # works on installs without PyYAML (PyYAML is dev-only).
            ok, value = _run_tiny_parser_branch(
                text,
                path,
                tiny_parser=tiny_parser,
                config_label=config_label,
                warnings_out=warnings_out,
                empty=empty,
                force=False,
            )
            if not ok:
                return _ret(value, "parse_error")
            data = value
        else:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                append_warning(
                    warnings_out,
                    config_label,
                    path,
                    f"malformed {parse_error_label}: {exc}. Treating as empty; "
                    f"fix the file or remove it to clear this warning.",
                )
                return _ret(empty, "parse_error")

    # W1030: empty-yaml (file had content, parser returned None) -- e.g.
    # comments-only YAML. Distinct from empty_file (zero-byte input
    # handled above). Both collapse to the empty container on the legacy
    # return path; status enum keeps them separable for opt-in callers.
    if data is None:
        return _ret(empty, "empty_yaml")

    # ---- Root-type check -------------------------------------------------
    if allow_list_root:
        if not isinstance(data, (dict, list)):
            append_warning(
                warnings_out,
                config_label,
                path,
                f"root is {type(data).__name__!r}, expected a mapping or a list. Treating as empty.",
            )
            return _ret(empty, "wrong_root_type")
    else:
        if not isinstance(data, dict):
            append_warning(
                warnings_out,
                config_label,
                path,
                f"root is {type(data).__name__!r}, expected a mapping. Treating as empty.",
            )
            return _ret(empty, "wrong_root_type")

    # ---- Schema validation ----------------------------------------------
    if schema_validator is not None:
        try:
            schema_warnings = schema_validator(data)
        except Exception as exc:  # noqa: BLE001 — defend the helper against buggy validators
            append_warning(
                warnings_out,
                config_label,
                path,
                f"schema validator raised {type(exc).__name__}: {exc}. "
                f"Treating as empty; fix the validator or the file.",
            )
            return _ret(empty, "schema_invalid")
        if schema_warnings:
            if warnings_out is not None:
                # Validator strings are already fully-formatted -- the
                # validator owns its vocabulary. Append as-is so the
                # caller can preserve callsite-specific wording (e.g.
                # "rules[2] has neither task_id nor path_glob").
                warnings_out.extend(schema_warnings)
            return _ret(empty, "schema_invalid")

    return _ret(data, "ok")


def parse_rule_list(text: str) -> list[dict[str, Any]]:
    """Parse a minimal ``- name: X\\n  key: val`` rule list (no PyYAML dep).

    Handles the documented shape::

        - name: rule-a
          metric: cycles
          max_increase: 5
          enabled: true
        - name: rule-b
          ...

    Returns a list of mappings (one per ``- name:`` line). Empty lines and
    ``#``-prefixed comments are ignored. Scalar values are coerced in order:
    ``true``/``false`` -> bool, then ``int``, then ``float``, otherwise the
    string is kept as-is (with surrounding single/double quotes stripped).

    Shared by ``cmd_fitness`` and ``cmd_budget`` (W1058 hoist of the W1019c
    + W1051 clones). Each callsite wraps the result in a domain-specific
    top-level key (``{"rules": [...]}`` / ``{"budgets": [...]}``) so the
    helper's mapping-root invariant holds; see :func:`load_yaml_with_warnings`.

    Callers pass this via ``tiny_parser=`` to :func:`load_yaml_with_warnings`
    inside a thin wrapper that re-keys the list under the callsite's
    expected top-level key.
    """
    rules: list[dict[str, Any]] = []
    current_rule: dict[str, Any] | None = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- name:"):
            if current_rule:
                rules.append(current_rule)
            current_rule = {"name": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
        elif current_rule and ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val_str = val.strip().strip('"').strip("'")
            coerced: Any
            if val_str.lower() == "true":
                coerced = True
            elif val_str.lower() == "false":
                coerced = False
            else:
                try:
                    coerced = int(val_str)
                except ValueError:
                    try:
                        coerced = float(val_str)
                    except ValueError:
                        coerced = val_str
            current_rule[key] = coerced

    if current_rule:
        rules.append(current_rule)

    return rules
