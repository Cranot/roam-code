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
from typing import Any, Callable, Literal, Mapping, overload

from roam.output.formatter import WarningsOut

# Per-callsite schema validator. Receives the parsed object (dict, or
# top-level list when ``allow_list_root=True``), returns an empty list on
# success or a list of pre-formatted warning strings on validation
# failure. The validator OWNS the warning vocabulary; the helper owns the
# I/O + parser-fallback shape.
SchemaValidator = Callable[[Any], list[str]]

# Per-callsite fallback parser used when PyYAML is unavailable. Receives
# the raw text; returns the parsed object. Allowed to return ``None`` to
# signal "I could not parse this." Each callsite supplies its own — the
# tiny YAML parsers are intentionally schema-specific (W706 expects a
# top-level ``rules:`` list, W994 expects ``suppressions:``, etc.).
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
    except Exception as exc2:  # noqa: BLE001 — tiny parser failures are non-fatal
        append_warning(
            warnings_out,
            config_label,
            path,
            f"PyYAML not installed; fallback parser failed: "
            f"{exc2}. Install PyYAML or use the documented shape.",
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
) -> Mapping[str, Any] | list[Any] | None: ...


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
) -> Mapping[str, Any] | list[Any] | None:
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
    """
    if force_tiny_parser and tiny_parser is None:
        raise ValueError(
            "force_tiny_parser=True requires tiny_parser to be set "
            "(callsite must supply a domain-aware permissive parser)."
        )

    empty: dict[str, Any] | list[Any] = [] if allow_list_root else {}

    if not path.exists():
        # Absence is not an error — it's the default state. No warning;
        # callers distinguish "no config" from "broken config" via the
        # None return.
        return None

    # ---- Read ------------------------------------------------------------
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        append_warning(
            warnings_out,
            config_label,
            path,
            f"could not read file: {exc}. Treating as empty; fix file "
            f"permissions / encoding to re-enable.",
        )
        return empty

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
            return value
        data = value
    else:
        try:
            import yaml  # type: ignore[import-untyped]

            try:
                data = yaml.safe_load(text)
            except Exception as exc:  # noqa: BLE001 — malformed YAML never crashes the loader
                append_warning(
                    warnings_out,
                    config_label,
                    path,
                    f"malformed {parse_error_label}: {exc}. Treating as empty; "
                    f"fix the file or remove it to clear this warning.",
                )
                return empty
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
                return value
            data = value

    # yaml.safe_load("") returns None; normalise that to empty container.
    if data is None:
        return empty

    # ---- Root-type check -------------------------------------------------
    if allow_list_root:
        if not isinstance(data, (dict, list)):
            append_warning(
                warnings_out,
                config_label,
                path,
                f"root is {type(data).__name__!r}, expected a mapping or a "
                f"list. Treating as empty.",
            )
            return empty
    else:
        if not isinstance(data, dict):
            append_warning(
                warnings_out,
                config_label,
                path,
                f"root is {type(data).__name__!r}, expected a mapping. "
                f"Treating as empty.",
            )
            return empty

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
            return empty
        if schema_warnings:
            if warnings_out is not None:
                # Validator strings are already fully-formatted — the
                # validator owns its vocabulary. Append as-is so the
                # caller can preserve callsite-specific wording (e.g.
                # "rules[2] has neither task_id nor path_glob").
                warnings_out.extend(schema_warnings)
            return empty

    return data
