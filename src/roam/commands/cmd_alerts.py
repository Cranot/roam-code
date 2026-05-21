"""Detect health degradation trends and generate actionable alerts.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because alerts outputs are invocation-scoped health-degradation
alerts — not per-location violations. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import click

from roam.capability import roam_capability
from roam.commands.metrics_history import collect_metrics, get_snapshots
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.formatter import WarningsOut, json_envelope, to_json

# ---------------------------------------------------------------------------
# Alert levels
# ---------------------------------------------------------------------------

# W649: alert-level labels are LOWERCASE (canonical roam vocabulary, per
# W547 ``roam.output._severity``). Pre-W649 these were UPPER-cased
# (``CRITICAL`` / ``WARNING`` / ``INFO``) and out of vocabulary with the
# rest of the surface — agents reading the envelope across roam commands
# would get mixed casing for the same concept. W640 added a transparent
# lower-case at the sort-key boundary; W649 makes the canonical lower-case
# the only spelling that ever appears in code or output.
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


def _level_order(level: str) -> int:
    """Ascending-sort key for alert levels (critical first).

    Delegates ORDER to the canonical :func:`severity_rank` (higher = worse)
    and negates so the original cmd_alerts polarity (critical=0,
    warning=1, info=2) is preserved byte-identically. The label is
    lower-cased defensively in case a caller passes a pre-W649 UPPER-cased
    label.
    """
    return -severity_rank(level.lower() if isinstance(level, str) else level)


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------


class AlertThreshold(TypedDict):
    """W919: structural typing for ``_DEFAULT_THRESHOLDS`` rows.

    Codifies the shape ``_check_thresholds`` unpacks as
    ``rule["op"], rule["value"], rule["level"]`` so a row that ships
    without ``level`` (regression risk after W910 backfilled
    ``bottlenecks`` / ``dead_exports``) fails at type-check time
    instead of at runtime.

    ``op`` is the closed set of comparators recognised by
    ``_check_thresholds`` — anything else is silently a no-op.
    ``level`` is the canonical lowercase vocabulary
    (``"critical"`` / ``"warning"`` / ``"info"``) defined in
    ``roam.output._severity``.

    W974: ``level`` is a ``Literal`` (post-W969). Pre-W969 it was
    ``str`` so ``_resolved_thresholds`` could lowercase legacy
    UPPER-cased configs at load time; W969's ``_coerce_level``
    heals every load site (PyYAML path, tiny-parser path,
    ``_resolved_thresholds`` belt-and-braces), so the type can now
    safely be tightened to the canonical lowercase set. Drift-guarded
    by ``test_canonical_levels_matches_alert_threshold_literal``
    alongside the W968 op drift guard.
    """

    op: Literal[">", "<", ">=", "<=", "=="]
    value: float | int
    level: Literal["critical", "warning", "info"]


# W962 + W963 (Pattern 2 — silent fallback): the closed set of comparators
# ``_check_thresholds`` knows how to evaluate. Anything outside this set was
# previously silently skipped — a typo like ``op: '!='`` in
# ``.roam/alerts.yaml`` would turn the alert into a no-op with no signal to
# the user. Module-level so the parse-time (W962) and check-time (W963)
# validators share one source of truth.
#
# Kept in sync (by hand, like the LAW 4 anchor lists) with the
# ``AlertThreshold.op`` Literal above — adding a new operator means BOTH
# updating the Literal AND extending this frozenset AND teaching
# ``_check_thresholds`` the new comparison.
_VALID_OPS: frozenset[str] = frozenset({">", "<", ">=", "<=", "=="})


# W969 (Pattern 2 — silent fallback): the canonical lowercase severity
# vocabulary (W649) re-expressed as a closed set so ``_coerce_level``
# and the alerts CLI's ``counts`` initialiser can share one source of
# truth. Adding a new severity level means updating BOTH this frozenset
# AND the CRITICAL/WARNING/INFO module constants (and downstream
# severity_rank in ``roam.output._severity``).
_CANONICAL_LEVELS: frozenset[str] = frozenset({CRITICAL, WARNING, INFO})


def _coerce_scalar(value: str) -> Any:
    """W967: shared scalar-coercion for the tiny YAML parser.

    Pre-W967 this logic lived inline twice in :func:`_parse_alerts_yaml`
    (once for the ``{}``-block value parser, once for the scalar-value
    path inside a section). W967 added a third site — flush-left scalar
    top-level keys — so factor the shared logic out instead of triplicating
    it. Strips one layer of quotes, then tries int / float / bool / else
    returns the raw string.
    """
    v = value.strip().strip("'\"")
    if v.lstrip("-").isdigit():
        try:
            return int(v)
        except ValueError:
            return v
    try:
        return float(v)
    except ValueError:
        pass
    lowered = v.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return v


def _coerce_level(
    value: Any,
    default: str,
    *,
    field_name: str,
    warnings_out: WarningsOut,
) -> str:
    """W969 (Pattern 2 — silent fallback): coerce a YAML ``level`` scalar.

    Pre-W969 the ``level`` field had no closed-set validation. A typo like
    ``level: "fatal"`` in ``.roam/alerts.yaml`` flowed unchanged through
    ``_make_alert`` into the alerts command's
    ``counts[a["level"]] += 1`` line and KeyError'd at runtime — Pattern 2
    silent fallback in the worst form: silent until it crashes.

    This helper validates against the canonical lowercase set
    ``{"critical", "warning", "info"}`` (W649). Returns:

    - the value untouched when it is already canonical lowercase (happy path);
    - the value lowercased + accepted when it is canonical when lowercased
      (handles pre-W649 ``"WARNING"`` / ``"Critical"`` configs silently —
      they round-trip into the canonical vocabulary without a warning);
    - ``default`` + an actionable warning for any other shape (unknown
      string, int, list, ...). Pattern 2 discipline: name the offending
      field, name the value, name the resolution and the valid spellings.
    """
    if isinstance(value, str):
        if value in _CANONICAL_LEVELS:
            return value
        lowered = value.strip().lower()
        if lowered in _CANONICAL_LEVELS:
            return lowered
    if warnings_out is not None:
        warnings_out.append(
            f"Config field {field_name!r} value {value!r} is not a valid "
            f"level (must be one of {sorted(_CANONICAL_LEVELS)}); "
            f"defaulting to {default!r}. Edit .roam/alerts.yaml to use "
            f"a canonical level for {field_name!r}."
        )
    return default


def _coerce_bool(
    value: Any,
    default: bool,
    *,
    field_name: str,
    warnings_out: WarningsOut,
) -> bool:
    """W964 (Pattern 2 — silent fallback): coerce a YAML scalar to a bool.

    Pre-W964, ``cfg.get("delta_alerts", True)`` returned the raw YAML scalar.
    If the user wrote ``delta_alerts: "yes"`` (string, truthy under
    ``if delta_alerts:`` but distinct from the bool ``True``), the value
    silently survived as a string. Later boolean checks worked by accident
    until somebody wrote a strict ``is True`` test — at which point the
    feature silently disabled itself with no signal.

    This helper:

    - returns the value untouched when it IS a bool (happy path);
    - accepts the common YAML-truthy / YAML-falsy strings (``true``, ``yes``,
      ``on``, ``1`` / ``false``, ``no``, ``off``, ``0``) and coerces them to
      bool *without* a warning — they are unambiguous user intent;
    - appends an actionable warning AND returns *default* for any other
      shape (int, float, unknown string, list, ...). Pattern 2 discipline:
      name the offending field, name the value, name the resolution.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
    if warnings_out is not None:
        warnings_out.append(
            f"Config field {field_name!r} value {value!r} is not a bool; "
            f"defaulting to {default!r}. Edit .roam/alerts.yaml to use "
            f"true/false for {field_name!r}."
        )
    return default


_DEFAULT_THRESHOLDS: dict[str, AlertThreshold] = {
    "health_score": {"op": "<", "value": 60, "level": CRITICAL},
    "cycles": {"op": ">", "value": 10, "level": WARNING},
    "god_components": {"op": ">", "value": 5, "level": WARNING},
    "layer_violations": {"op": ">", "value": 0, "level": INFO},
    # W910: bottlenecks + dead_exports were tracked directionally in
    # ``_WORSE_WHEN_HIGHER`` and labelled in ``_TREND_LABELS`` but had no
    # threshold row — ``_check_thresholds`` skipped them silently, so any
    # ``.roam/alerts.yaml`` override was the ONLY way to surface them as a
    # severity alert. The values here mirror cmd_health.py's
    # ``_BASELINE_METRICS`` classification (bottlenecks=WARNING,
    # dead_exports=INFO) and the betweenness > 0.5 / top-15 cap in
    # ``metrics_history.collect_metrics`` (5+ severe bottlenecks is the
    # action floor; 20+ dead exports is the noise threshold where the
    # accumulation is large enough to be worth reading).
    "bottlenecks": {"op": ">", "value": 5, "level": WARNING},
    "dead_exports": {"op": ">", "value": 20, "level": INFO},
}

# Backwards-compat alias for any plug-ins that imported the old name.
_THRESHOLDS = _DEFAULT_THRESHOLDS


def _parse_alerts_yaml(
    text: str,
    warnings_out: WarningsOut = None,
) -> dict[str, dict[str, Any]]:
    """Tiny YAML reader for ``.roam/alerts.yaml`` — avoids the PyYAML dep.

    Schema accepted (W649: ``level`` may be ``critical`` / ``warning`` /
    ``info`` in any case; legacy UPPER-cased configs continue to work and
    are normalised to lowercase at resolve time):

        thresholds:
          health_score: { op: '<', value: 50, level: critical }
          cycles: { op: '>', value: 50, level: warning }
        delta_alerts: true

    W962 (Pattern 2 — silent fallback): when *warnings_out* is supplied,
    threshold rows under the ``thresholds:`` section whose ``op`` is
    outside :data:`_VALID_OPS` (e.g. a typo like ``!=``) get an actionable
    warning appended naming the metric, the offending operator, and the
    valid alternatives. The rule is kept in the result with its invalid
    ``op`` untouched — :func:`_check_thresholds` re-validates at check
    time (W963 belt-and-braces) and skips the rule there. Keeping the
    rule rather than dropping it means the warning text + the alert
    output describe the same rule, so the user can correlate the two.

    W967 (Pattern 2 — silent fallback): flush-left ``key: value`` lines
    with a non-empty scalar value are parsed as top-level scalar fields
    (e.g. ``delta_alerts: true``), not as empty section headers. Pre-W967
    the tiny parser stuffed the whole ``delta_alerts: true`` string into
    the section-header key and threw away the value — meaning
    ``_load_alerts_config`` for users WITHOUT PyYAML installed returned
    ``{'delta_alerts: true': {}}`` and the eventual
    ``cfg.get("delta_alerts", True)`` lookup returned ``True`` (default)
    regardless of what the user wrote. The feature silently disabled (or
    silently enabled) itself depending on the default. Keeping a
    flush-left ``key:`` with EMPTY value as a section header preserves
    every existing ``thresholds:`` use.

    W969 (Pattern 2 — silent fallback): inline ``{}``-block rules under
    the ``thresholds:`` section get their ``level`` validated against
    the canonical set ``{"critical", "warning", "info"}`` (W649).
    Unknown levels (e.g. ``"fatal"``) surface an actionable warning AND
    fall back to the safe default ``WARNING`` so the downstream
    ``counts[a["level"]] += 1`` cannot KeyError.
    """
    result: dict[str, dict] = {}
    current_section: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            # W967: distinguish flush-left section headers (``key:`` with
            # empty / whitespace-only value) from flush-left scalar
            # top-level fields (``key: value``). Pre-W967 both shapes
            # collapsed onto the section-header path and the scalar's
            # value was silently dropped.
            if ":" in line:
                head_key, _, head_value = line.partition(":")
                head_key = head_key.strip()
                head_value = head_value.strip()
                if head_value:
                    # Scalar top-level field — coerce + store at the
                    # outer dict, NOT under a section.
                    result[head_key] = _coerce_scalar(head_value)
                    current_section = None
                    continue
                current_section = head_key
                result[current_section] = {}
                continue
            # No colon at all: legacy section-header fallback. Mirrors
            # pre-W967 behaviour for any malformed input.
            key = line.rstrip(":").strip()
            current_section = key
            result[current_section] = {}
            continue
        if current_section is None:
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("{") and value.endswith("}"):
            inner = value[1:-1]
            d: dict = {}
            for part in inner.split(","):
                if ":" not in part:
                    continue
                k, _, v = part.partition(":")
                k = k.strip()
                d[k] = _coerce_scalar(v)
            # W962 (Pattern 2): validate ``op`` against the closed set
            # of comparators ``_check_thresholds`` actually evaluates.
            # Only validate when this row sits under ``thresholds:`` —
            # other sections (e.g. ``delta_alerts``) do not carry ops.
            if warnings_out is not None and current_section == "thresholds" and "op" in d and d["op"] not in _VALID_OPS:
                warnings_out.append(
                    f"Metric {key!r} has invalid op {d['op']!r} (must be "
                    f"one of {sorted(_VALID_OPS)}); skipping this "
                    f"threshold. Edit .roam/alerts.yaml to use a valid "
                    f"comparator for {key!r}."
                )
            # W969 (Pattern 2): validate ``level`` against the canonical
            # lowercase severity vocabulary. Only validate when this row
            # sits under ``thresholds:`` — outside that section the
            # ``level`` key has no special semantics.
            if current_section == "thresholds" and "level" in d:
                d["level"] = _coerce_level(
                    d["level"],
                    default=WARNING,
                    field_name=f"thresholds.{key}.level",
                    warnings_out=warnings_out,
                )
            result[current_section][key] = d
        else:
            result[current_section][key] = _coerce_scalar(value)
    return result


def _load_alerts_config(
    project_root: Path | None = None,
    warnings_out: WarningsOut = None,
) -> dict[str, dict[str, Any]]:
    """Load ``.roam/alerts.yaml`` overrides if present.

    Round 4 #3, G: hardcoded thresholds force every project to live with
    the same noise floor. A small YAML lets users (and roam itself, via
    ``--init`` later) tune what 'critical' means for their codebase.

    W962 (Pattern 2 — silent fallback): when *warnings_out* is supplied,
    threshold rows with an invalid ``op`` are surfaced at parse time
    (tiny-parser path) or here (PyYAML path) so the user gets one signal
    per offending row regardless of which YAML backend resolved the file.

    W1030-followup-A: legacy single-value return preserved for the existing
    callers (resolver + tests). Callers that need the on-disk state for
    envelope disambiguation use :func:`_load_alerts_config_with_status`
    instead.
    """
    cfg, _status = _load_alerts_config_with_status(project_root, warnings_out=warnings_out)
    return cfg


def _load_alerts_config_with_status(
    project_root: Path | None = None,
    warnings_out: WarningsOut = None,
) -> tuple[dict[str, dict[str, Any]], str]:
    """W1030-followup-A: load ``.roam/alerts.yaml`` and return ``(cfg, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`
    (``"ok"`` / ``"missing"`` / ``"empty_file"`` / ``"empty_yaml"`` /
    ``"read_error"`` / ``"parse_error"`` / ``"wrong_root_type"`` /
    ``"schema_invalid"``). Lets the alerts command envelope disambiguate
    "no alerts.yaml configured yet" (``missing`` -> use defaults silently)
    from "alerts.yaml exists but is empty" (``empty_file`` -> use defaults
    + flag the empty stub) from "alerts.yaml is broken" (``parse_error`` /
    ``wrong_root_type`` -> partial_success, warnings already populated by
    the canonical loader).

    The per-threshold validator walks
    (W962/W963/W964/W969/W972/W1025) run on the parsed mapping AFTER the
    canonical loader, exactly like pre-W1030-followup-A — the
    silent-fallback warning vocabulary is unchanged.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    root = project_root or find_project_root()
    cfg_path = root / ".roam" / "alerts.yaml"

    # W1030-followup-A: route I/O + parse through the canonical helper so
    # the on-disk state is disambiguated for envelope-level disclosure.
    # The helper handles missing-file / read-error / parse-error /
    # wrong-root-type / empty-file / empty-yaml uniformly. The
    # per-threshold validators (W962/W963/W964/W969/W1025) still run below
    # on the parsed mapping because they need to walk the ``thresholds:``
    # sub-mapping shape.
    #
    # W972 wording preservation: the canonical loader's parse_error /
    # wrong_root_type diagnostics are more terse than the pre-W1030
    # bespoke wording (no "Edit ..." imperative). To keep the existing
    # W972 test contract intact (warning[0] must contain "Edit" + the
    # root type), we drain canonical-loader warnings into a local buffer
    # and re-emit a single W972-style warning that joins the loader's
    # type-name with the historical imperative phrasing.
    _canonical_warns: list[str] = []
    data, status = load_yaml_with_warnings(
        cfg_path,
        tiny_parser=_parse_alerts_yaml_tiny,
        config_label=".roam/alerts.yaml",
        warnings_out=_canonical_warns,
        return_status=True,
    )
    if data is None:
        # status == "missing" -- absent file is the default state.
        # No canonical-loader warning either (missing == not-an-error).
        return {}, status
    if not data:
        # empty_file / empty_yaml / parse_error / wrong_root_type /
        # schema_invalid -- collapse canonical loader's diagnostics
        # into the per-status wording the existing tests pin on, so
        # warning[0] keeps the W972 imperative shape.
        if warnings_out is not None:
            if status == "wrong_root_type":
                # Pull the actual root-type name out of the canonical
                # loader's diagnostic (``root is 'list'``) so the new
                # wording can name the same offender, matching the W972
                # test contract.
                root_kind = _root_kind_from_canonical_warn(_canonical_warns)
                warnings_out.append(
                    f".roam/alerts.yaml root is a {root_kind}, not a "
                    f"mapping; using empty config. Edit the file to use "
                    f"top-level keys (thresholds:, delta_alerts:, etc)."
                )
            else:
                # parse_error / read_error / empty_file / empty_yaml /
                # schema_invalid -- forward the canonical-loader
                # diagnostics verbatim (they are already actionable
                # per the loader's "Treating as empty; fix..." pattern).
                warnings_out.extend(_canonical_warns)
        return {}, status
    # Happy path: data parsed cleanly. Forward any non-fatal canonical-
    # loader warnings (none today, but kept for forward-compat).
    if warnings_out is not None and _canonical_warns:
        warnings_out.extend(_canonical_warns)
    assert isinstance(data, dict)

    # W1025 (Pattern 2 — silent fallback): a non-dict ``thresholds:``
    # section (scalar, list, etc.) used to flow straight through to
    # ``_resolved_thresholds`` which then crashed on ``.items()`` (truthy
    # non-dict) or silently collapsed to ``{}`` (falsy non-dict). Detect
    # the shape mismatch HERE, surface an actionable warning, AND coerce
    # the section to ``{}`` so the rest of the pipeline sees a well-formed
    # value. Same wording across PyYAML and tiny-parser paths because the
    # canonical loader homogenises the parse step (W1030-followup-A).
    raw_thresholds = data.get("thresholds")
    if raw_thresholds is not None and not isinstance(raw_thresholds, dict):
        if warnings_out is not None:
            warnings_out.append(
                f".roam/alerts.yaml 'thresholds:' section is a "
                f"{type(raw_thresholds).__name__}, expected a "
                f"mapping keyed by metric name; ignoring. Edit "
                f"the file to use "
                f"`thresholds:\\n  <metric>: {{op: ..., value: "
                f"..., level: ...}}` form."
            )
        data["thresholds"] = {}
    thresholds = data.get("thresholds") or {}
    if isinstance(thresholds, dict):
        # W962: validate ``op`` against the closed set _check_thresholds
        # evaluates. W969: normalise ``level`` in-place so unknown level
        # values default to WARNING and the canonical-set validation
        # fires before ``counts[a["level"]] += 1`` can KeyError.
        for metric, rule in thresholds.items():
            if not isinstance(rule, dict):
                continue
            if warnings_out is not None and "op" in rule and rule["op"] not in _VALID_OPS:
                warnings_out.append(
                    f"Metric {metric!r} has invalid op "
                    f"{rule['op']!r} (must be one of "
                    f"{sorted(_VALID_OPS)}); skipping this "
                    f"threshold. Edit .roam/alerts.yaml to "
                    f"use a valid comparator for {metric!r}."
                )
            if "level" in rule:
                rule["level"] = _coerce_level(
                    rule["level"],
                    default=WARNING,
                    field_name=f"thresholds.{metric}.level",
                    warnings_out=warnings_out,
                )
    return data, status


def _parse_alerts_yaml_tiny(text: str) -> dict[str, dict[str, Any]]:
    """W1030-followup-A: tiny-parser entrypoint for the canonical loader.

    Thin shim around :func:`_parse_alerts_yaml` so the canonical loader's
    ``tiny_parser=`` callback signature (``(text) -> Any``) matches the
    legacy tiny parser's ``(text, warnings_out=...)`` signature. The
    canonical loader handles ``warnings_out`` itself for parse-failure
    paths; per-row ``op``/``level`` validators that need the accumulator
    run AFTER the canonical loader returns (see
    :func:`_load_alerts_config_with_status`).
    """
    return _parse_alerts_yaml(text)


def _root_kind_from_canonical_warn(warns: list[str]) -> str:
    """W1030-followup-A: extract the root type name from the canonical
    loader's ``root is 'X', expected a mapping`` diagnostic so the W972
    re-emitted warning can name the same offender.

    Returns ``"list"`` / ``"str"`` / ``"int"`` / etc. when the diagnostic
    is present; falls back to a neutral ``"non-mapping"`` token when the
    canonical-loader output shape changes -- the W972 test only requires
    the offender's type name when a mapping was expected, and a benign
    fallback keeps the call site silent on shape drift.
    """
    import re as _re

    for warn in warns:
        # Canonical wording: "...: root is 'list', expected a mapping..."
        match = _re.search(r"root is '([^']+)', expected a mapping", warn)
        if match:
            return match.group(1)
    return "non-mapping"


def _resolved_thresholds(
    project_root: Path | None = None,
    warnings_out: WarningsOut = None,
) -> dict[str, dict[str, Any]]:
    """Merge ``.roam/alerts.yaml`` overrides on top of the defaults.

    W649: any user-supplied ``level`` string is lower-cased on load so
    pre-W649 configs that wrote ``CRITICAL`` / ``WARNING`` / ``INFO``
    continue to round-trip into the canonical lowercase vocabulary.

    W933: return annotation is ``dict[str, dict[str, Any]]`` rather than
    ``dict[str, AlertThreshold]`` because ``slot.update(rule)`` feeds
    arbitrary ``.roam/alerts.yaml`` overrides into each row — declaring
    the tighter TypedDict here would be a lie without runtime validation
    of ``op`` / ``value`` / ``level`` on every override, which
    ``_check_thresholds`` deliberately keeps as a silent no-op for
    unknown comparators.

    W918 (Pattern 2 — silent fallback): when a user-supplied metric is
    NOT in :data:`_DEFAULT_THRESHOLDS` AND the override does not specify
    a full ``{op, value, level}`` triple, this function previously
    defaulted to ``{"op": ">", "value": 0, "level": WARNING}`` *silently*.
    A user who adds a "worse-when-lower" metric (e.g. ``coverage``) via
    ``.roam/alerts.yaml`` without specifying ``op`` would get nonsense
    alerts (every positive value would trip the ``>0`` rule).

    The fallback is preserved for backward compatibility, but when
    *warnings_out* is supplied as a ``list[str]``, this function appends
    an actionable warning naming the metric and pointing at the config
    file. The CLI surfaces the warnings on a ``warnings_out`` field in
    the JSON envelope (and prominently in text mode) so the silent
    fallback state is made explicit per Pattern 2 discipline.
    """
    # W962: plumb ``warnings_out`` through ``_load_alerts_config`` so
    # invalid-op rows under ``thresholds:`` surface as Pattern 2 warnings
    # at parse time (alongside the existing W918 silent-fallback warnings
    # this function appends below).
    cfg = _load_alerts_config(project_root, warnings_out=warnings_out)
    overrides = cfg.get("thresholds", {}) or {}
    merged = {k: dict(v) for k, v in _DEFAULT_THRESHOLDS.items()}
    for metric, rule in overrides.items():
        if not isinstance(rule, dict):
            continue
        # W918: detect the silent-fallback path. A metric absent from the
        # canonical defaults AND missing one or more of ``op`` / ``value``
        # / ``level`` in its YAML row would otherwise be papered over by
        # the ``setdefault`` below. Tag it BEFORE the merge so the
        # warning text reflects what the user actually wrote, not the
        # post-merge papered-over state.
        if warnings_out is not None and metric not in _DEFAULT_THRESHOLDS:
            missing_fields = [field for field in ("op", "value", "level") if field not in rule]
            if missing_fields:
                warnings_out.append(
                    f"Metric {metric!r} has no threshold defined in alerts "
                    f"config (missing {missing_fields}); defaulting to "
                    f"op='>', value=0, level='warning'. Add a complete "
                    f"threshold entry "
                    f"({{op, value, level}}) to .roam/alerts.yaml to "
                    f"silence this warning."
                )
        slot = merged.setdefault(metric, {"op": ">", "value": 0, "level": WARNING})
        slot.update(rule)
        # W649 + W969: canonicalise level. ``_coerce_level`` lowercases
        # legacy UPPER-cased configs (silently, per W649) AND surfaces a
        # Pattern 2 warning for any non-canonical spelling so the bad
        # value never reaches ``counts[a["level"]] += 1`` and KeyErrors.
        # Belt-and-braces against rules constructed directly in-process
        # (tests, downstream callers) — same discipline as W963 for op.
        if "level" in slot:
            slot["level"] = _coerce_level(
                slot["level"],
                default=WARNING,
                field_name=f"thresholds.{metric}.level",
                warnings_out=warnings_out,
            )
    return merged


_RATE_OF_CHANGE_PCT = 20  # alert if metric changes more than 20%

# Cap snapshot history fed into the O(n^2) Mann-Kendall / Sen's-slope trend
# tests. _check_trends runs an O(n) window loop x an O(window^2) test per
# tracked metric, i.e. O(n^3); an unbounded get_snapshots() fetch made `roam
# alerts` latent-cubic on long-history repos. get_snapshots() returns
# newest-first and trend significance is dominated by recent history, so
# capping the fetch keeps the most-recent N snapshots -- the meaningful
# "is it trending now" signal. Override via ROAM_ALERTS_SNAPSHOT_LIMIT for the
# rare repo that wants a wider analysis window.
_TREND_SNAPSHOT_LIMIT = 200


def _trend_snapshot_limit() -> int:
    """Return the snapshot-history cap for trend analysis (env-overridable)."""
    raw = os.environ.get("ROAM_ALERTS_SNAPSHOT_LIMIT")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return _TREND_SNAPSHOT_LIMIT


# Metrics where an increase means degradation
_WORSE_WHEN_HIGHER = {"cycles", "god_components", "bottlenecks", "dead_exports", "layer_violations"}
# Metrics where a decrease means degradation
_WORSE_WHEN_LOWER = {"health_score"}

_TREND_LABELS = {
    "cycles": "Cycle count trending up",
    "health_score": "Health score declining",
    "dead_exports": "Dead code accumulating",
    "bottlenecks": "New bottlenecks emerging",
    "god_components": "God components increasing",
    "layer_violations": "Layer violations growing",
}


# ---------------------------------------------------------------------------
# Alert construction helpers
# ---------------------------------------------------------------------------


class _AlertBase(TypedDict):
    """W959 (required-field core for :class:`Alert`).

    Split out so the optional ``trend_direction`` field can be modelled
    on Python 3.10 (which ships ``TypedDict`` but NOT ``NotRequired``).
    See :class:`Alert` for the full record shape; downstream consumers
    type-annotate against :class:`Alert`, not this base.
    """

    level: Literal["critical", "warning", "info"]
    metric: str
    message: str
    current_value: float | int


class Alert(_AlertBase, total=False):
    """W959: structural typing for one alert record emitted by
    ``_check_thresholds`` / ``_check_trends`` / ``_check_rate_of_change`` /
    ``_delta_baseline_alerts``.

    Codifies the shape ``_make_alert`` constructs canonically (the only
    in-process producer of alert records — the construction is hand-written
    as a dict literal, NOT a ``dict.update(arbitrary)``, so the W966 strict-
    typing discipline applies). Downstream consumers index ``a["level"]`` /
    ``a["metric"]`` / ``a["current_value"]`` / ``a.get("trend_direction")``;
    declaring the shape here lets type checkers catch the day a new
    consumer mistypes a key.

    Mirrors of closed sets:

    - ``level`` is the canonical lowercase severity vocabulary (W649)
      defined in :data:`_CANONICAL_LEVELS`. ``_make_alert`` asserts this
      defensively at construction (W973). The W974 drift guard pins
      ``AlertThreshold.level`` to the same set; the W959 drift guard pins
      ``Alert.level`` to the same set.
    - ``trend_direction`` is optional — ``_check_thresholds`` omits it
      entirely; ``_check_trends`` / ``_check_rate_of_change`` /
      ``_delta_baseline_alerts`` set it. Modelled via the
      Required-base + ``total=False`` subclass idiom so the file stays
      Python 3.10 compatible (``typing.NotRequired`` is 3.11+, and
      ``pyproject.toml`` declares ``requires-python = ">=3.10"``). The
      field is dropped, not set to ``None``, when absent — matching the
      existing ``_make_alert`` behaviour and keeping the serialization
      output byte-identical to pre-W959.

    NOTE: ``trend_direction`` is a free-form ``str`` today (the producers
    use ``"up"`` / ``"down"`` / ``"worse"``); a future tightening could
    promote it to ``Literal["up", "down", "worse"]`` with a sister W968-
    style drift guard, but the current call sites have not converged on a
    single canonical vocabulary yet.
    """

    trend_direction: str


def _make_alert(
    level: str,
    metric: str,
    message: str,
    current_value: float | int,
    trend_direction: str | None = None,
) -> Alert:
    # W973 (Pattern 2 — silent fallback, belt-and-braces): defensively
    # validate ``level`` against the canonical lowercase severity set.
    # All 5 internal call sites pass canonical levels today (CRITICAL /
    # WARNING / INFO module constants OR levels that flowed through
    # ``_coerce_level`` upstream), so this assert is latent. It fires
    # the day a future internal caller injects a non-canonical level
    # — surfacing the bug at the construction site instead of at the
    # downstream ``counts[a["level"]] += 1`` KeyError. Sibling of W969
    # which guards the YAML-parse-time path; this guards the
    # in-process-construction path.
    assert level in _CANONICAL_LEVELS, f"_make_alert level {level!r} must be canonical ({sorted(_CANONICAL_LEVELS)})"
    # W959: cast-narrow ``level`` from ``str`` to the canonical Literal
    # AFTER the assert has proven membership in ``_CANONICAL_LEVELS``. Same
    # discipline as ``_resolved_thresholds`` etc — runtime checks gate the
    # type narrowing, so the TypedDict literal contract holds.
    alert: Alert = {
        "level": level,  # type: ignore[typeddict-item]
        "metric": metric,
        "message": message,
        "current_value": current_value,
    }
    if trend_direction is not None:
        alert["trend_direction"] = trend_direction
    return alert


def _mann_kendall_s(values):
    """Compute the Mann-Kendall S statistic and its significance.

    The Mann-Kendall test is a non-parametric trend test robust to outliers
    and noise.  S > 0 indicates an upward trend; S < 0 indicates downward.

    For n >= 3, we also compute a two-sided p-value using the normal
    approximation of the variance:  Var(S) = n(n-1)(2n+5)/18.

    Returns (S, p_value).  p_value is None for n < 3.
    Reference: Mann (1945), Kendall (1975).
    """
    import math

    n = len(values)
    s = 0
    for i in range(n):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1
    if n < 3:
        return s, None
    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    if var_s == 0:
        return s, 1.0
    std_s = math.sqrt(var_s)
    # Continuity-corrected z
    if s > 0:
        z = (s - 1) / std_s
    elif s < 0:
        z = (s + 1) / std_s
    else:
        z = 0
    # Two-sided p-value via complementary error function
    p = math.erfc(abs(z) / math.sqrt(2))
    return s, p


def _sens_slope(values):
    """Compute Sen's slope estimator: robust trend magnitude.

    slope = median of (xj - xk) / (j - k) for all k < j.

    Unlike linear regression, Sen's slope is resistant to outliers
    and gives a robust estimate of the rate of change per time unit.
    Reference: Sen (1968), "Estimates of the Regression Coefficient
    Based on Kendall's Tau."
    """
    slopes = []
    n = len(values)
    for i in range(n):
        for j in range(i + 1, n):
            slopes.append((values[j] - values[i]) / (j - i))
    if not slopes:
        return 0.0
    slopes.sort()
    mid = len(slopes) // 2
    if len(slopes) % 2 == 0:
        return (slopes[mid - 1] + slopes[mid]) / 2
    return slopes[mid]


def _is_monotonic_worsening(values, metric):
    """Detect statistically significant worsening trends.

    Uses the Mann-Kendall trend test instead of strict monotonicity,
    making detection robust to noise (e.g., [5, 5, 5, 6] is not flagged
    but [5, 7, 8, 12] is).  Requires p < 0.10 for significance.
    """
    if len(values) < 3:
        return False
    s, p = _mann_kendall_s(values)
    if p is None or p >= 0.10:
        return False
    # S > 0 → upward trend; S < 0 → downward trend
    if metric in _WORSE_WHEN_HIGHER:
        return s > 0
    elif metric in _WORSE_WHEN_LOWER:
        return s < 0
    return False


# ---------------------------------------------------------------------------
# Detection routines
# ---------------------------------------------------------------------------


def _check_thresholds(
    current: dict[str, Any],
    thresholds: dict[str, dict[str, Any]] | None = None,
    warnings_out: WarningsOut = None,
) -> list[Alert]:
    """Check current metrics against thresholds (defaults + ``.roam/alerts.yaml``).

    W963 (Pattern 2 — silent fallback, belt-and-braces): if a rule
    survives parse-time validation (W962) with an ``op`` outside
    :data:`_VALID_OPS` — e.g. a typo introduced directly in
    :data:`_DEFAULT_THRESHOLDS`, or a rule constructed in-process by a
    test or downstream caller — surface an actionable warning and skip
    the rule. The pre-W963 code path silently fell through the if/elif
    chain and emitted no alert for that metric, with no signal to the
    user.
    """
    alerts: list[Alert] = []
    rules = thresholds if thresholds is not None else _resolved_thresholds()
    for metric, rule in rules.items():
        val = current.get(metric)
        if val is None:
            continue
        op = rule.get("op")
        # W963: validate ``op`` against the closed comparator set BEFORE
        # reading ``value`` / ``level`` — an invalid op is a config bug
        # the user must see, not a no-op.
        if op not in _VALID_OPS:
            if warnings_out is not None:
                warnings_out.append(
                    f"Metric {metric!r} threshold has invalid op "
                    f"{op!r} (must be one of {sorted(_VALID_OPS)}); "
                    f"skipping this alert. Edit .roam/alerts.yaml to "
                    f"use a valid comparator for {metric!r}."
                )
            continue
        threshold, level = rule["value"], rule["level"]
        triggered = False
        if op == "<" and val < threshold:
            triggered = True
        elif op == ">" and val > threshold:
            triggered = True
        elif op == ">=" and val >= threshold:
            triggered = True
        elif op == "<=" and val <= threshold:
            triggered = True
        elif op == "==" and val == threshold:
            triggered = True
        if triggered:
            msg = f"below {threshold} threshold" if op == "<" else f"above {threshold} threshold"
            alerts.append(
                _make_alert(
                    level,
                    metric,
                    f"{metric}={val} ({msg})",
                    val,
                )
            )
    return alerts


def _check_trends(snapshots_chrono):
    """Detect monotonic degradation over 3+ consecutive snapshots.

    *snapshots_chrono* is a list of snapshot dicts ordered oldest-first.
    """
    alerts = []
    if len(snapshots_chrono) < 3:
        return alerts

    tracked = list(_WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER)
    for metric in tracked:
        values = [s.get(metric, 0) or 0 for s in snapshots_chrono]
        # Check the last 3..N window sizes for a monotonic run
        for window in range(len(values), 2, -1):
            tail = values[-window:]
            if _is_monotonic_worsening(tail, metric):
                current = tail[-1]
                arrow = " -> ".join(str(v) for v in tail)
                label = _TREND_LABELS.get(metric, f"{metric} worsening")
                # Sen's slope: robust rate of change per snapshot
                slope = _sens_slope(tail)
                slope_str = f", rate={slope:+.1f}/snapshot" if abs(slope) >= 0.1 else ""
                alerts.append(
                    _make_alert(
                        WARNING,
                        metric,
                        f"{label}: {arrow} over {window} snapshots{slope_str}",
                        current,
                        trend_direction="up" if metric in _WORSE_WHEN_HIGHER else "down",
                    )
                )
                break  # largest matching window is enough
    return alerts


def _check_rate_of_change(snapshots_chrono):
    """Alert if a metric changed more than _RATE_OF_CHANGE_PCT between the
    last two consecutive snapshots."""
    alerts = []
    if len(snapshots_chrono) < 2:
        return alerts

    prev = snapshots_chrono[-2]
    curr = snapshots_chrono[-1]

    tracked = list(_WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER)
    for metric in tracked:
        prev_val = prev.get(metric, 0) or 0
        curr_val = curr.get(metric, 0) or 0
        if prev_val == 0:
            # Can't compute percentage change from zero.
            # But if the metric appeared from nothing, that is notable.
            if curr_val > 0 and metric in _WORSE_WHEN_HIGHER:
                alerts.append(
                    _make_alert(
                        INFO,
                        metric,
                        f"{metric}={curr_val} (new since last snapshot)",
                        curr_val,
                        trend_direction="up",
                    )
                )
            continue

        pct = abs(curr_val - prev_val) / abs(prev_val) * 100
        if pct <= _RATE_OF_CHANGE_PCT:
            continue

        # Only alert if change is in the worsening direction
        worsening = False
        if metric in _WORSE_WHEN_HIGHER and curr_val > prev_val:
            worsening = True
        elif metric in _WORSE_WHEN_LOWER and curr_val < prev_val:
            worsening = True

        if worsening:
            direction = "increased" if curr_val > prev_val else "decreased"
            alerts.append(
                _make_alert(
                    WARNING,
                    metric,
                    f"{metric}={curr_val} ({direction} {pct:.0f}% since last snapshot)",
                    curr_val,
                    trend_direction="up" if curr_val > prev_val else "down",
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _deduplicate(alerts):
    """Remove duplicate alerts for the same metric, keeping the highest severity."""
    seen = {}
    for a in alerts:
        key = (a["metric"], a.get("trend_direction"))
        if key not in seen or _level_order(a["level"]) < _level_order(seen[key]["level"]):
            seen[key] = a
    # Return sorted: CRITICAL first, then WARNING, then INFO
    return sorted(seen.values(), key=lambda a: (_level_order(a["level"]), a["metric"]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_snap_dicts(snaps_raw) -> list[dict]:
    """Convert the DB rows (newest-first) into chronological dict list."""
    out: list[dict] = []
    for s in reversed(snaps_raw):
        out.append(
            {
                "timestamp": s["timestamp"],
                "files": s["files"],
                "symbols": s["symbols"],
                "edges": s["edges"],
                "cycles": s["cycles"],
                "god_components": s["god_components"],
                "bottlenecks": s["bottlenecks"],
                "dead_exports": s["dead_exports"],
                "layer_violations": s["layer_violations"],
                "health_score": s["health_score"],
            }
        )
    return out


def _delta_baseline_alerts(current: dict, baseline_snap: dict) -> list[dict]:
    """Per-metric regression alerts vs. the previous snapshot."""
    out: list[dict] = []
    for metric, current_value in current.items():
        if not isinstance(current_value, (int, float)):
            continue
        baseline_value = baseline_snap.get(metric)
        if not isinstance(baseline_value, (int, float)) or baseline_value == 0:
            continue
        delta = current_value - baseline_value
        pct = abs(delta) / max(abs(baseline_value), 1) * 100
        regressed = (metric in _WORSE_WHEN_HIGHER and delta > 0) or (metric in _WORSE_WHEN_LOWER and delta < 0)
        if regressed and pct >= 10:
            arrow = "+" if delta > 0 else ""
            out.append(
                _make_alert(
                    WARNING if pct < 25 else CRITICAL,
                    metric,
                    f"{metric} regressed since baseline: {baseline_value} -> {current_value} "
                    f"({arrow}{delta}, {pct:.0f}%)",
                    current_value,
                    trend_direction="worse",
                )
            )
    return out


def _alerts_summary_parts(counts: dict) -> list[str]:
    """Render the ``N critical, M warnings, K info`` clauses."""
    parts: list[str] = []
    if counts[CRITICAL]:
        parts.append(f"{counts[CRITICAL]} critical")
    if counts[WARNING]:
        parts.append(f"{counts[WARNING]} warning{'s' if counts[WARNING] != 1 else ''}")
    if counts[INFO]:
        parts.append(f"{counts[INFO]} info")
    return parts


def _alerts_verdict(all_alerts: list[dict], counts: dict) -> str:
    if not all_alerts:
        return "no alerts — all metrics within normal ranges"
    parts = _alerts_summary_parts(counts)
    return f"{len(all_alerts)} alert{'s' if len(all_alerts) != 1 else ''}: {', '.join(parts)}"


def _emit_alerts_json(
    verdict: str,
    all_alerts: list[dict],
    counts: dict,
    snapshots_analyzed: int,
    warnings_out: WarningsOut = None,
    config_state: str = "ok",
    w607cx_warnings_out: list[str] | None = None,
) -> None:
    # LAW 4 (CLAUDE.md): supply explicit agent_contract.facts anchored on
    # the concrete subject ("alerts scan") with analytical verbs, instead of
    # leaning on the formatter's auto-derive that turns ``critical: 5`` into
    # an abstract key:value fact.
    facts: list[str] = [verdict]
    if counts.get(CRITICAL):
        facts.append(
            f"alerts scan flagged {counts[CRITICAL]} critical health degradations across {snapshots_analyzed} snapshots"
        )
    if counts.get(WARNING):
        facts.append(f"alerts scan flagged {counts[WARNING]} warning-level health trends")
    if counts.get(INFO):
        facts.append(f"alerts scan emitted {counts[INFO]} info-level observations")
    if all_alerts:
        top = all_alerts[0]
        facts.append(f"highest-priority alert: [{top.get('level', '?')}] {top.get('message', '?')}")
    # W918 (Pattern 2): if the alerts config triggered any silent-fallback
    # warnings (unknown user-supplied metric defaulted to ``op='>', value=0``),
    # surface them as a fact so consumers reading only ``agent_contract.facts``
    # still see the silent-state disclosure.
    warnings_list = list(warnings_out) if warnings_out else []
    if warnings_list:
        facts.append(f"alerts config triggered {len(warnings_list)} silent-fallback warnings")
    # W1030-followup-A: surface the alerts.yaml on-disk state as a fact so
    # agents reading the envelope can distinguish "no alerts.yaml configured"
    # from "alerts.yaml exists but is empty" from "alerts.yaml is broken".
    # The fact terminates on a concrete-noun anchor ("defaults") to keep
    # the LAW 4 lint happy.
    if config_state == "missing":
        facts.append("no .roam/alerts.yaml configured; using baseline defaults")
    elif config_state == "empty_file":
        facts.append("empty .roam/alerts.yaml stub on disk; using baseline defaults")
    elif config_state == "empty_yaml":
        facts.append("comment-only .roam/alerts.yaml on disk; using baseline defaults")
    elif config_state in ("parse_error", "wrong_root_type", "schema_invalid", "read_error"):
        facts.append(f"alerts config rejected ({config_state}); using baseline defaults")
    next_commands = [
        "roam health",
        "roam architecture-drift",
    ]
    summary: dict[str, Any] = {
        "verdict": verdict,
        "total": len(all_alerts),
        "critical": counts[CRITICAL],
        "warning": counts[WARNING],
        "info": counts[INFO],
        "snapshots_analyzed": snapshots_analyzed,
    }
    # W1030-followup-A: expose the on-disk state as a closed-enum string
    # so agents can disambiguate "no alerts.yml configured yet" (defaults
    # used silently) from "alerts.yml exists but is empty" (defaults used
    # AND the user probably meant to configure something) from
    # "alerts.yml is broken" (parse_error / wrong_root_type — already
    # accompanied by a warning in ``warnings_out``).
    summary["config_state"] = config_state
    # W918: ``partial_success`` makes the silent-fallback state machine
    # readable. When the alerts threshold path silently defaulted for an
    # unknown metric, the run is technically successful but the user's
    # config did not produce the alerts they configured for.
    # W1030-followup-A: parse_error / wrong_root_type / read_error /
    # schema_invalid also flip partial_success because the user's config
    # was discarded — agents must see the discard, not just the verdict.
    # W607-CX: a non-empty substrate-marker bucket ALSO flips
    # partial_success so degraded substrate paths are NOT mistaken for
    # clean runs (Pattern-2 silent-fallback guard).
    config_degraded = config_state in ("parse_error", "wrong_root_type", "read_error", "schema_invalid")
    cx_markers = list(w607cx_warnings_out) if w607cx_warnings_out else []
    if warnings_list or config_degraded or cx_markers:
        summary["partial_success"] = True
    # W607-CX: mirror substrate markers into BOTH the top-level
    # envelope ``warnings_out`` AND ``summary.warnings_out`` so MCP
    # consumers see disclosure regardless of which surface they read.
    # Per-layer separation discipline: the user-facing config warnings
    # (W918/W962/W964) flow through their existing ``warnings_out``
    # field unchanged; the W607-CX bucket APPENDS substrate markers
    # to that same surface, so a single consumer read covers both.
    combined_top: list[str] = list(warnings_list)
    if cx_markers:
        combined_top.extend(cx_markers)
        summary["warnings_out"] = cx_markers
    click.echo(
        to_json(
            json_envelope(
                "alerts",
                summary=summary,
                alerts=all_alerts,
                # W918: ``warnings_out`` mirrors the pre-existing
                # convention used by ``pr-bundle`` (W377-batch /
                # W425) — actionable, per-source warnings that the
                # CLI accumulates but does not raise on. Empty list
                # when every override row was well-formed (Pattern 2
                # — consumers can rely on the key being present).
                # W607-CX: substrate markers are appended to the same
                # surface so consumers see one combined list.
                warnings_out=combined_top,
                agent_contract={
                    "facts": facts,
                    "next_commands": next_commands,
                },
            )
        )
    )


def _emit_alerts_text(
    verdict: str,
    all_alerts: list[dict],
    counts: dict,
    warnings_out: WarningsOut = None,
) -> None:
    click.echo(f"VERDICT: {verdict}\n")
    # W918 (Pattern 2): surface silent-fallback warnings prominently
    # BEFORE the alert list so users see the config issue before they
    # spend cycles reading defaulted-and-probably-wrong alerts.
    if warnings_out:
        click.echo("Configuration warnings:")
        for warning in warnings_out:
            click.echo(f"  {warning}")
        click.echo()
    if not all_alerts:
        click.echo("No health alerts. All metrics are within normal ranges.")
        return
    click.echo("Health alerts:\n")
    for a in all_alerts:
        click.echo(f"  {a['level'].ljust(9)} {a['message']}")
    click.echo()
    click.echo(", ".join(_alerts_summary_parts(counts)))


@roam_capability(
    name="alerts",
    category="health",
    summary="Detect health degradation trends and generate actionable alerts",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.pass_context
def alerts(ctx):
    """Detect health degradation trends and generate actionable alerts.

    Analyzes snapshot history to find:
    - Metrics that consistently worsen over 3+ snapshots
    - Current values that exceed severity thresholds
    - Metrics that changed more than 20% since the last snapshot

    Unlike ``health`` (which gives a point-in-time codebase score), this
    command performs time-series analysis over snapshot history using
    Mann-Kendall trend tests and Sen's slope to detect degradation trends.
    Run ``trends --save`` regularly to build history for trend detection.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    all_alerts: list[dict] = []
    # W918 (Pattern 2): accumulator for silent-fallback warnings from
    # ``_resolved_thresholds``. When a user-supplied metric in
    # ``.roam/alerts.yaml`` is unknown AND incomplete, the resolver
    # appends an actionable warning here that the CLI then surfaces on
    # the envelope's ``warnings_out`` field (and prominently in text
    # mode). Empty list when every override row is well-formed.
    config_warnings: list[str] = []

    # W607-CX -- substrate-boundary plumbing for cmd_alerts.
    # ``_run_check_cx`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cx_warnings_out`` rather than
    # crashing the alerts detector outright. Marker family
    # ``alerts_<phase>_failed:<exc_class>:<detail>``.
    #
    # W607-CX COEXISTS WITH the mature Pattern-2 validators
    # (W918 / W962 / W963 / W964 / W969 / W972 / W973 / W974 / W1025 /
    # W1030-followup-A) that surface CONFIG-shape errors through
    # ``config_warnings``. The two layers serve distinct purposes:
    #
    #   * ``config_warnings``  -- actionable, user-facing diagnostics
    #                             for malformed ``.roam/alerts.yaml``
    #                             rows (invalid op, unknown level,
    #                             missing fields, non-dict thresholds).
    #                             These flow through the existing
    #                             ``warnings_out`` envelope field that
    #                             the W918/W962/W964 tests pin on.
    #
    #   * ``_w607cx_warnings_out`` -- substrate-CALL markers for an
    #                                  uncaught raise INSIDE one of the
    #                                  helpers (``get_snapshots`` raising,
    #                                  ``_check_thresholds`` raising,
    #                                  ``_check_trends`` raising, etc.).
    #                                  These mirror into BOTH top-level
    #                                  ``warnings_out`` AND
    #                                  ``summary.warnings_out`` so MCP
    #                                  consumers see disclosure regardless
    #                                  of which surface they read.
    #
    # Substrates wrapped:
    #
    #   * get_snapshots             -- DB-row ingest (newest-first)
    #   * collect_metrics           -- live-metric collector (no-snapshot
    #                                  fallback)
    #   * build_snap_dicts          -- raw-row -> chronological dict
    #                                  conversion
    #   * load_alerts_config        -- ``.roam/alerts.yaml`` I/O + parse
    #   * resolved_thresholds       -- defaults + overrides merge
    #   * check_thresholds          -- W962/W963 op-validated checks
    #   * coerce_delta_alerts       -- W964 bool coercion for the
    #                                  ``delta_alerts`` flag
    #   * delta_baseline_alerts     -- per-metric regression-vs-baseline
    #                                  alerts
    #   * check_trends              -- Mann-Kendall + Sen's slope trend
    #                                  detection
    #   * check_rate_of_change      -- per-snapshot rate-of-change alerts
    #   * deduplicate               -- dedup + sort
    #   * compose_verdict           -- LAW 6 single-line verdict
    _w607cx_warnings_out: list[str] = []

    def _run_check_cx(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CX marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``alerts_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607cx_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cx_warnings_out.append(f"alerts_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # W607-CX: ``get_snapshots`` substrate -- DB-row ingest.
        snaps_raw = _run_check_cx("get_snapshots", get_snapshots, conn, _trend_snapshot_limit(), default=[])
        if snaps_raw is None:
            snaps_raw = []
        # W607-CX: ``build_snap_dicts`` substrate -- raw-row -> dict.
        snap_dicts = _run_check_cx("build_snap_dicts", _build_snap_dicts, snaps_raw, default=[])
        if snap_dicts is None:
            snap_dicts = []
        if snap_dicts:
            current = snap_dicts[-1]
        else:
            # W607-CX: ``collect_metrics`` substrate -- live-metric
            # fallback when no snapshots exist.
            current = _run_check_cx("collect_metrics", collect_metrics, conn, default={})
            if current is None:
                current = {}

        # 1) Threshold checks (respect .roam/alerts.yaml overrides).
        # W962 + W963 + W964: ``_load_alerts_config`` /
        # ``_resolved_thresholds`` / ``_check_thresholds`` all append to
        # the same ``config_warnings`` list so the envelope carries one
        # actionable warning per offending row, regardless of which
        # validator detected it.
        # W1030-followup-A: use the with-status variant so the on-disk
        # state ("missing" / "empty_file" / "ok" / ...) reaches the
        # envelope as a closed-enum field — agents reading the alerts
        # envelope can disambiguate "no thresholds configured yet" from
        # "thresholds file is broken / empty stub" without re-statting
        # the file.
        # W607-CX: ``load_alerts_config`` substrate -- the canonical
        # YAML loader already catches parse_error / wrong_root_type /
        # read_error via the closed-enum status. A raise here is
        # therefore unusual (genuine bug in the loader, not a config
        # shape issue) but we still wrap it so the alerts detector
        # cannot crash on a loader regression.
        _cfg_pair = _run_check_cx(
            "load_alerts_config",
            _load_alerts_config_with_status,
            warnings_out=config_warnings,
            default=({}, "missing"),
        )
        if _cfg_pair is None:
            _cfg_pair = ({}, "missing")
        cfg, config_state = _cfg_pair
        # W607-CX: ``resolved_thresholds`` substrate.
        thresholds = _run_check_cx(
            "resolved_thresholds",
            _resolved_thresholds,
            warnings_out=config_warnings,
            default={},
        )
        if thresholds is None:
            thresholds = {}
        # W607-CX: ``check_thresholds`` substrate.
        threshold_alerts = _run_check_cx(
            "check_thresholds",
            _check_thresholds,
            current,
            thresholds,
            warnings_out=config_warnings,
            default=[],
        )
        if threshold_alerts:
            all_alerts.extend(threshold_alerts)

        # 2) Delta-vs-baseline alerts (need >= 2 snapshots, opt-out via config).
        # W964 (Pattern 2 — silent fallback): coerce ``delta_alerts``
        # through the bool helper so YAML strings (``"yes"`` / ``"no"``)
        # behave as the user intended AND any other shape surfaces an
        # actionable warning instead of silently disabling the feature.
        if cfg:
            delta_enabled = _run_check_cx(
                "coerce_delta_alerts",
                _coerce_bool,
                cfg.get("delta_alerts", True),
                True,
                field_name="delta_alerts",
                warnings_out=config_warnings,
                default=True,
            )
            if delta_enabled is None:
                delta_enabled = True
        else:
            delta_enabled = True
        if delta_enabled and len(snap_dicts) >= 2:
            # W607-CX: ``delta_baseline_alerts`` substrate.
            delta_alerts = _run_check_cx(
                "delta_baseline_alerts",
                _delta_baseline_alerts,
                current,
                snap_dicts[-2],
                default=[],
            )
            if delta_alerts:
                all_alerts.extend(delta_alerts)

        # 3) Trend detection (Mann-Kendall + Sen's slope, need >= 3 snapshots).
        if len(snap_dicts) >= 3:
            # W607-CX: ``check_trends`` substrate.
            trend_alerts = _run_check_cx("check_trends", _check_trends, snap_dicts, default=[])
            if trend_alerts:
                all_alerts.extend(trend_alerts)

        # 4) Rate-of-change detection (need >= 2 snapshots).
        if len(snap_dicts) >= 2:
            # W607-CX: ``check_rate_of_change`` substrate.
            rate_alerts = _run_check_cx(
                "check_rate_of_change",
                _check_rate_of_change,
                snap_dicts,
                default=[],
            )
            if rate_alerts:
                all_alerts.extend(rate_alerts)

    # W607-CX: ``deduplicate`` substrate -- a malformed alert row could
    # KeyError on ``a["metric"]`` / ``a["level"]`` inside the sort key.
    deduped = _run_check_cx("deduplicate", _deduplicate, all_alerts, default=[])
    if deduped is None:
        deduped = []
    all_alerts = deduped
    # W969 (Pattern 2): derive ``counts`` from the canonical level set
    # so adding a new severity in ``_CANONICAL_LEVELS`` does not require
    # a manual edit here. Defensive against any level that escapes the
    # validators — unknown levels fold into ``WARNING`` so we cannot
    # KeyError. The canonical-set validators (W969 in ``_coerce_level``)
    # are the FIRST line of defence; this is the second.
    counts = {level: 0 for level in _CANONICAL_LEVELS}
    for a in all_alerts:
        level = a["level"]
        if level not in counts:
            level = WARNING
        counts[level] += 1
    # W607-CX: ``compose_verdict`` substrate -- LAW 6 single-line verdict.
    verdict = _run_check_cx(
        "compose_verdict",
        _alerts_verdict,
        all_alerts,
        counts,
        default="no alerts — all metrics within normal ranges",
    )
    if not verdict:
        verdict = "no alerts — all metrics within normal ranges"

    if json_mode:
        _emit_alerts_json(
            verdict,
            all_alerts,
            counts,
            len(snap_dicts),
            warnings_out=config_warnings,
            config_state=config_state,
            w607cx_warnings_out=_w607cx_warnings_out,
        )
        return
    _emit_alerts_text(verdict, all_alerts, counts, warnings_out=config_warnings)
