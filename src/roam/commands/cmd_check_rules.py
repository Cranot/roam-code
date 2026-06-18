"""Structural rule packs with optional autofix templates.

Evaluates built-in and user-defined governance rules against the indexed
codebase. Rules can be checked individually or in bulk, filtered by severity,
and configured via .roam-rules.yml.

Built-in rules (10):
  no-circular-imports  -- SCC cycles
  max-fan-out          -- outgoing edges per symbol
  max-fan-in           -- incoming edges per symbol
  max-file-complexity  -- cognitive complexity per file
  max-file-length      -- lines per file
  test-file-exists     -- source files without test files
  no-god-classes       -- classes with too many methods
  no-deep-inheritance  -- inheritance depth
  layer-violation      -- lower layer imports upper layer
  no-orphan-symbols    -- symbols with no edges
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output._severity import severity_rank
from roam.output.formatter import WarningsOut, json_envelope, to_json

# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def _find_config_path(config_path: str | None) -> str | None:
    """Resolve a config path, searching defaults if not specified."""
    if config_path is not None:
        return config_path
    for candidate in [
        Path.cwd() / ".roam-rules.yml",
        Path.cwd() / ".roam-rules.yaml",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _load_raw_config(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> dict:
    """Load and parse the YAML config file into a raw dict.

    Returns an empty dict if not found or on parse error.

    W1019d (Pattern 2 — silent fallback): when *warnings_out* is supplied
    as a ``list[str]``, every silent-fallback path (file unreadable,
    malformed YAML/JSON, non-mapping root) appends an actionable warning
    naming the path, the failure shape, and the resolution. Pre-W1019d
    callers that don't supply ``warnings_out`` retain the byte-identical
    silent-empty-dict behaviour so existing happy-path consumers keep
    emitting byte-identical envelopes when ``.roam-rules.yml`` is
    well-formed.

    W1030-followup-C: thin wrapper over :func:`_load_raw_config_with_status`
    that drops the closed-enum ``LoadStatus`` return so pre-W1030-followup-C
    callers (the existing W1019d test suite + every consumer that doesn't
    care about the on-disk state) stay byte-identical.
    """
    data, _status = _load_raw_config_with_status(config_path, warnings_out=warnings_out)
    return data


def _load_raw_config_with_status(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[dict, str]:
    """W1030-followup-C: load ``.roam-rules.yml`` and return ``(data, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`
    (``"ok"`` / ``"missing"`` / ``"empty_file"`` / ``"empty_yaml"`` /
    ``"read_error"`` / ``"parse_error"`` / ``"wrong_root_type"`` /
    ``"schema_invalid"``). Lets the check-rules command envelope
    disambiguate "no .roam-rules.yml configured yet" (``missing`` -> use
    baseline rules silently) from ".roam-rules.yml exists but is empty"
    (``empty_file`` / ``empty_yaml`` -> use baseline rules + flag the
    empty stub) from ".roam-rules.yml is broken" (``parse_error`` /
    ``wrong_root_type`` -> ``partial_success=True``, warnings already
    populated by the canonical loader).

    Mirror of :func:`roam.commands.cmd_health._load_gate_config_with_status`
    (W1030-followup-B reference impl). ``check-rules`` is a tier-2 gate
    caller (``for_compliance`` composed recipes consume its verdict), so
    the config-state disclosure rides on every governance envelope.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    resolved = _find_config_path(config_path)
    if resolved is None:
        # Path arg explicitly None AND no default config on disk: "missing"
        # is the file-absent state the helper short-circuits to.
        return {}, "missing"

    data, status = load_yaml_with_warnings(
        Path(resolved),
        tiny_parser=_parse_simple_yaml_text,
        config_label="roam-rules",
        warnings_out=warnings_out,
        return_status=True,
    )
    if data is None:
        # Missing file (helper short-circuits absence).
        return {}, status
    # ``allow_list_root`` is False (default) so the helper guarantees a
    # mapping. The assert keeps the type checker honest on the
    # post-helper extraction logic below.
    assert isinstance(data, dict)
    return data, status


def _load_user_config(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load user rule overrides from .roam-rules.yml.

    Returns a list of rule override dicts. Fields:
      id (str): rule ID to match against built-in rules
      enabled (bool, optional): set to false to disable
      threshold (float, optional): override the default threshold
      severity (str, optional): override severity

    W1019d (Pattern 2 — silent fallback): mirrors :func:`_load_raw_config`.
    A non-list ``rules:`` value surfaces a structured warning when
    ``warnings_out`` is supplied; pre-W1019d callers stay byte-identical.

    W1030-followup-C: thin wrapper over
    :func:`_load_user_config_with_status` that drops the closed-enum
    ``LoadStatus`` return so pre-W1030-followup-C callers stay byte-identical.
    """
    rules, _status = _load_user_config_with_status(config_path, warnings_out=warnings_out)
    return rules


def _load_user_config_with_status(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], str]:
    """W1030-followup-C: load user rule overrides and return ``(rules, status)``.

    ``status`` is forwarded verbatim from
    :func:`_load_raw_config_with_status` — the closed-enum ``LoadStatus``
    reflects the on-disk state of ``.roam-rules.yml``. The missing-``rules:``
    key path collapses onto the same status that the raw load reported
    (``ok`` when the file parsed cleanly but has no ``rules:`` key —
    a profile-only config is legitimate so it stays ``ok``).

    The W1030-followup-C empty-file / empty-yaml short-circuit suppresses
    the spurious missing-key warning that the legacy branch would emit on
    a stub file: an empty stub is its own disclosure surface
    (``config_state=empty_file``), so the "no `rules:` key" warning
    would just confuse agents reading the warnings_out list.
    """
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data, status = _load_raw_config_with_status(config_path, warnings_out=warnings_out)
    if not data:
        return [], status
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper / raw-loader already explained the failure (read error /
        # malformed YAML / wrong root type). Propagate the empty result
        # without piling on a second warning that would confuse the caller.
        return [], status

    if "rules" not in data:
        # No `rules:` key — a profile-only config is legitimate, so this is
        # NOT a warning. Return empty overrides and let the caller decide.
        return [], status
    # W1038 — shared "load → check type → warn-or-default" extractor.
    from roam.commands._yaml_loader import extract_typed

    resolved = _find_config_path(config_path)
    path_str = resolved if resolved is not None else "<unknown>"
    rules = extract_typed(
        data,
        "rules",
        list,
        [],
        warnings_out=warnings_out,
        context=f"roam-rules: {path_str!r}",
        expected_shape="a list of `{id, threshold?, severity?, enabled?}` entries",
    )
    return rules, status


def _load_config_profile(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> str | None:
    """Load the profile name from .roam-rules.yml if present.

    Returns the profile name string or None.

    W1019d (Pattern 2 — silent fallback): mirrors :func:`_load_raw_config`.
    A non-string / empty ``profile:`` value surfaces a structured warning
    when ``warnings_out`` is supplied; pre-W1019d callers stay
    byte-identical.

    W1030-followup-C: thin wrapper over
    :func:`_load_config_profile_with_status` that drops the closed-enum
    ``LoadStatus`` return so pre-W1030-followup-C callers stay byte-identical.
    """
    profile, _status = _load_config_profile_with_status(config_path, warnings_out=warnings_out)
    return profile


def _load_config_profile_with_status(
    config_path: str | None,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[str | None, str]:
    """W1030-followup-C: load profile name and return ``(profile, status)``.

    ``status`` is forwarded verbatim from
    :func:`_load_raw_config_with_status`. ``profile`` is ``None`` when the
    file is missing / empty / malformed / has no ``profile:`` key / has a
    non-string profile value — the closed-enum status disambiguates these
    paths for envelope-level disclosure.
    """
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data, status = _load_raw_config_with_status(config_path, warnings_out=warnings_out)
    if not data:
        return None, status
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the file-level failure; don't pile on.
        return None, status
    if "profile" not in data:
        # Absence of a profile key is legitimate (rules-only configs).
        return None, status
    # W1038-followup — shared "load → check type → validate → warn-or-default"
    # extractor. The validator captures the non-empty-string sub-pattern
    # (``isinstance(v, str) and v.strip()``) the inline branch used to do.
    from roam.commands._yaml_loader import extract_typed

    resolved = _find_config_path(config_path)
    path_str = resolved if resolved is not None else "<unknown>"
    profile = extract_typed(
        data,
        "profile",
        str,
        "",
        warnings_out=warnings_out,
        context=f"roam-rules: {path_str!r}",
        expected_shape="a non-empty string (e.g. 'strict-security')",
        validator=lambda v: bool(v.strip()),
    )
    if profile:
        return profile.strip(), status
    return None, status


# ---------------------------------------------------------------------------
# W1030-followup-C: worst-status rollup
# ---------------------------------------------------------------------------

# Severity rank for LoadStatus values. Higher rank = more degraded. Used to
# roll up the three sub-loader statuses into a single ``summary.config_state``
# field. The rule: degraded states override ``ok``; the most degraded state
# wins (mirroring cmd_critique's max-aggregation style).
#
# - ok (0)               -- file parsed cleanly
# - missing (1)          -- no config configured; legitimate default state
# - empty_file (2)       -- stub on disk; user probably meant to configure
# - empty_yaml (2)       -- comments-only file; same surface as empty_file
# - read_error (3)       -- file unreadable; broken
# - schema_invalid (3)   -- file parsed but failed validator
# - wrong_root_type (3)  -- root is list/scalar, not mapping
# - parse_error (3)      -- malformed YAML/JSON
#
# Note: all three sub-loaders read from the SAME ``.roam-rules.yml`` file,
# so in practice they will all return the same status. The rollup is
# defensive — if any of the three diverges (e.g. a future loader gains its
# own file), the most-degraded state still surfaces.
_STATUS_RANK: dict[str, int] = {
    "ok": 0,
    "missing": 1,
    "empty_file": 2,
    "empty_yaml": 2,
    "read_error": 3,
    "schema_invalid": 3,
    "wrong_root_type": 3,
    "parse_error": 3,
}


def _worst_status(*statuses: str) -> str:
    """W1030-followup-C: roll up multiple ``LoadStatus`` values to the worst.

    Degraded states override ``ok``; the most-degraded state wins. Returns
    ``"ok"`` when every status is ``"ok"`` (or no statuses are supplied).
    Unknown statuses (not in :data:`_STATUS_RANK`) sort below ``"ok"`` so
    a future LoadStatus addition can't silently downgrade the rollup.
    """
    if not statuses:
        return "ok"
    worst = statuses[0]
    worst_rank = _STATUS_RANK.get(worst, -1)
    for s in statuses[1:]:
        s_rank = _STATUS_RANK.get(s, -1)
        if s_rank > worst_rank:
            worst = s
            worst_rank = s_rank
    return worst


# W1030-followup-C: closed-enum set of LoadStatus values that flip
# ``partial_success=True`` even when no warning fired (e.g. empty stub on
# disk doesn't warn, but ``schema_invalid`` does -- treat them uniformly
# at the partial_success level). Mirrors cmd_alerts + cmd_budget +
# cmd_health vocabularies.
_DEGRADED_LOAD_STATUSES: frozenset[str] = frozenset({"parse_error", "wrong_root_type", "read_error", "schema_invalid"})


def _build_config_state_facts(config_state: str) -> list[str]:
    """W1030-followup-C: build ``agent_contract.facts`` for the ``config_state``.

    LAW 4 anchored on the concrete-noun terminal ``"rules"`` (the
    governance rules check-rules evaluates). Mirrors cmd_alerts (anchors
    on ``"defaults"``) and cmd_health (anchors on ``"gates"``) by
    using the command's own subject-noun.

    Returns an empty list when ``config_state == "ok"`` -- no need to
    disclose the happy path.
    """
    if config_state == "missing":
        return ["no .roam-rules.yml configured; using baseline rules"]
    if config_state == "empty_file":
        return ["empty .roam-rules.yml stub on disk; using baseline rules"]
    if config_state == "empty_yaml":
        return ["comment-only .roam-rules.yml on disk; using baseline rules"]
    if config_state in _DEGRADED_LOAD_STATUSES:
        return [f"check-rules config rejected ({config_state}); using baseline rules"]
    return []


def _parse_simple_yaml_text(text: str) -> dict:
    """Minimal YAML parser fallback when PyYAML is unavailable.

    W1019d: text-based tiny-parser conforming to the
    :data:`roam.commands._yaml_loader.TinyParser` shape. The helper owns
    the file-read + OSError handling now; this callback just parses the
    raw text.
    """
    result: dict = {}
    current_list: list | None = None
    current_item: dict | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        # Top-level scalar keys (e.g. "profile: strict-security")
        if indent == 0 and ":" in stripped and not stripped.endswith(":"):
            key, _, val = stripped.partition(":")
            val = val.strip().strip(chr(34)).strip(chr(39))
            if key.strip() not in ("rules",) and val:
                result[key.strip()] = val
                continue
        if stripped == "rules:":
            result["rules"] = []
            current_list = result["rules"]
            current_item = None
            continue
        if stripped.startswith("- ") and current_list is not None:
            current_item = {}
            current_list.append(current_item)
            stripped = stripped[2:]
        if current_item is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip().strip(chr(34)).strip(chr(39))
            if val.lower() == "true":
                current_item[key.strip()] = True
            elif val.lower() == "false":
                current_item[key.strip()] = False
            else:
                try:
                    current_item[key.strip()] = int(val)
                except ValueError:
                    try:
                        current_item[key.strip()] = float(val)
                    except ValueError:
                        current_item[key.strip()] = val
    return result


# ---------------------------------------------------------------------------
# Rule resolution
# ---------------------------------------------------------------------------


def _resolve_rules(
    rule_filter: str | None,
    severity_filter: str | None,
    user_overrides: list[dict],
) -> list:
    """Return list of BuiltinRule objects to evaluate.

    Applies user config overrides (enable/disable, threshold changes).
    Filters by rule ID and/or severity as requested.
    """
    import copy

    from roam.rules.builtin import BUILTIN_RULES

    # Build override map keyed by rule ID
    override_map: dict[str, dict] = {}
    for o in user_overrides:
        rid = o.get("id", "")
        if rid:
            override_map[rid] = o

    # Clone and apply overrides
    resolved = []
    for rule in BUILTIN_RULES:
        r = copy.copy(rule)
        if rule.id in override_map:
            ov = override_map[rule.id]
            if "enabled" in ov:
                r.enabled = bool(ov["enabled"])
            if "threshold" in ov and ov["threshold"] is not None:
                r.threshold = float(ov["threshold"])
            if "severity" in ov:
                r.severity = str(ov["severity"])
        resolved.append(r)

    # Filter disabled rules
    resolved = [r for r in resolved if r.enabled]

    # Filter by specific rule ID
    if rule_filter:
        resolved = [r for r in resolved if r.id == rule_filter]

    # Filter by severity. W1005-followup-C: route through the canonical
    # ``severity_rank`` (higher = worse) instead of a string-equality match,
    # so the widened Choice (critical/error/high/warning/medium/low/info)
    # collapses CVSS-style inputs onto the rules' emitted 3-tier vocab via
    # the canonical rank comparator — a single source of truth shared with
    # cmd_smells / cmd_secrets / cmd_test_gaps.
    if severity_filter:
        floor = severity_rank(severity_filter.lower())
        resolved = [r for r in resolved if severity_rank(r.severity) >= floor]

    return resolved


# ---------------------------------------------------------------------------
# Verdict calculation
# ---------------------------------------------------------------------------


def _calculate_verdict(results: list[dict]) -> tuple[str, int]:
    """Return (verdict_string, exit_code).

    PASS = 0, WARN = 0, FAIL = 1
    """
    total = len(results)
    errors = [r for r in results if not r["passed"] and r["severity"] == "error"]
    warnings = [r for r in results if not r["passed"] and r["severity"] == "warning"]
    infos = [r for r in results if not r["passed"] and r["severity"] == "info"]
    passed = [r for r in results if r["passed"]]

    if total == 0:
        return "PASS - no rules configured", 0

    if errors:
        verdict = "FAIL - {} error(s), {} warning(s), {} info".format(len(errors), len(warnings), len(infos))
        return verdict, 1
    elif warnings:
        verdict = "WARN - {} warning(s), {} info".format(len(warnings), len(infos))
        return verdict, 0
    else:
        verdict = "PASS - all {} rule(s) passed".format(len(passed))
        return verdict, 0


# ---------------------------------------------------------------------------
# SARIF output
# ---------------------------------------------------------------------------


def _results_to_sarif(
    results: list[dict],
    *,
    warnings_out: list[str] | None = None,
    runtime_overrides: list[dict] | None = None,
    runtime_notification_overrides: list[dict] | None = None,
) -> dict:
    """Convert check-rules results to SARIF 2.1.0 format.

    W1114: ``warnings_out`` plumbs caller-supplied silent-fallback warnings
    (malformed ``.roam-rules.yml`` / ``.roam/rules/*.yml`` files that were
    skipped by the YAML loaders) through to
    :func:`roam.output.sarif.rules_to_sarif`, which projects them onto the
    SARIF ``run.invocations[].toolExecutionNotifications[]`` array via the
    W1046 opt-in. Hash invariant: when ``warnings_out`` is ``None``/empty
    the SARIF bytes are identical to pre-W1114 callers (the opt-in flag
    stays ``False``).

    W1061-followup: ``runtime_overrides`` (rule-id-level via ``--rule``)
    and ``runtime_notification_overrides`` (finding-level via
    ``--severity``) project onto the SARIF
    ``ruleConfigurationOverrides`` / ``notificationConfigurationOverrides``
    arrays so CI consumers can distinguish a filtered "no findings" run
    from a clean codebase. Defaults stay byte-identical to pre-W1061
    callers via gated emission in :func:`to_sarif`.
    """
    from roam.output.sarif import rules_to_sarif

    # Transform results to the format rules_to_sarif expects
    sarif_results = []
    for r in results:
        sarif_results.append(
            {
                "name": r["id"],
                "passed": r["passed"],
                "severity": r["severity"],
                "violations": r.get("violations", []),
            }
        )
    return rules_to_sarif(
        sarif_results,
        emit_runtime_notifications=bool(warnings_out),
        warnings_out=list(warnings_out) if warnings_out else None,
        runtime_overrides=runtime_overrides,
        runtime_notification_overrides=runtime_notification_overrides,
    )


def _evaluate_custom_rules(
    conn,
    rule_filter: str | None,
    severity_filter: str | None,
    *,
    warnings_out: list[str] | None = None,
) -> list[dict]:
    """Evaluate custom `.roam/rules` rules and adapt to check-rules result shape.

    W1036: ``warnings_out`` plumbs through to the engine's YAML loader so
    malformed rule files surface as actionable warnings on the
    check-rules envelope (sibling of the W1019d ``.roam-rules.yml``
    loader warnings).
    """
    from roam.rules.engine import evaluate_all

    rules_dir = find_project_root() / ".roam" / "rules"
    if not rules_dir.is_dir():
        return []

    raw_results = evaluate_all(rules_dir, conn, warnings_out=warnings_out)
    adapted: list[dict] = []
    for item in raw_results:
        name = item.get("name", "unnamed")
        severity = item.get("severity", "error")

        if rule_filter and name != rule_filter:
            continue
        # W1005-followup-C: canonical-rank floor (same comparator as
        # _resolve_rules above) keeps the custom-rule branch in lockstep
        # with the built-in branch under the widened W547 7-tier Choice.
        if severity_filter and severity_rank(severity) < severity_rank(severity_filter.lower()):
            continue

        violations = item.get("violations", [])
        adapted.append(
            {
                "id": name,
                "severity": severity,
                "description": item.get("description", "Custom rule"),
                "check": item.get("type", "custom"),
                "threshold": None,
                "source": "custom",
                "passed": len(violations) == 0,
                "violation_count": len(violations),
                "violations": violations,
            }
        )
    return adapted


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="check-rules",
    category="health",
    summary="Run structural governance rules against the indexed codebase",
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
@click.command("check-rules")
@click.option(
    "--rule",
    "rule_filter",
    default=None,
    help="Run only this specific built-in rule ID or custom rule name.",
)
@click.option(
    "--severity",
    "severity_filter",
    default=None,
    type=click.Choice(
        # W1005-followup-C: widened from 3-tier {error, warning, info} to the W547
        # canonical 7-tier so agents can pass any of {critical, error, high,
        # warning, medium, low, info} and have the filter route through
        # ``roam.output._severity.severity_rank`` for the canonical comparison.
        # The built-in + custom rules currently EMIT only {error, warning, info}
        # (the SARIF-aligned 3-tier alphabet), so input/emit asymmetry is
        # intentional and documented at the filter site below: ``critical`` /
        # ``high`` collapse onto rank 4-or-5 floors that match the emitted
        # ``error`` rank; ``medium`` / ``low`` collapse onto floors that match
        # the emitted ``warning`` / ``info`` ranks. This means
        # ``--severity high`` keeps every ``error`` finding (rank 4 ==
        # rank 4) — the same set ``--severity error`` would keep. Aliases
        # like ``note`` / ``unknown`` are intentionally NOT in the Choice —
        # they collapse to ``info`` / sort-below-info via ``severity_rank``,
        # so a user-facing filter on them would be confusing.
        ["critical", "error", "high", "warning", "medium", "low", "info"],
        case_sensitive=False,
    ),
    help=(
        "Only show rules at or above this severity (canonical W547 7-tier "
        "ordering: critical > error == high > warning > medium > low > info). "
        "Rules emit error/warning/info today; CVSS aliases route through "
        "severity_rank() for the comparison."
    ),
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .roam-rules.yml config file.",
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Use a named rule profile (e.g. strict-security, ai-code-review, legacy-maintenance, minimal).",
)
@click.option(
    "--list",
    "do_list",
    is_flag=True,
    help="List all available built-in rules and exit.",
)
@click.option(
    "--list-profiles",
    "do_list_profiles",
    is_flag=True,
    help="List all available rule profiles and exit.",
)
@click.pass_context
def check_rules_command(ctx, rule_filter, severity_filter, config_path, profile_name, do_list, do_list_profiles):
    """Run structural governance rules against the indexed codebase.

    Built-in rules cover: circular imports, fan-out/fan-in, file complexity,
    file length, test coverage, god classes, deep inheritance, layer violations,
    and orphan symbols.

    Unlike ``rules`` (which manages custom rule definitions in .roam/rules),
    this command evaluates both built-in and custom rules and reports
    pass/fail results.

    Configure thresholds and enable/disable rules via .roam-rules.yml.
    Use --profile to select a named rule profile (default, strict-security,
    ai-code-review, legacy-maintenance, minimal).
    Exit code 1 when any error-severity rule fails (CI-friendly).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    from roam.rules.builtin import BUILTIN_RULES, rule_profile_summaries

    # --list-profiles: print available profiles and exit
    if do_list_profiles:
        profiles = rule_profile_summaries()
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "check-rules",
                        summary={"verdict": "profiles listed", "count": len(profiles)},
                        profiles=profiles,
                    )
                )
            )
        else:
            click.echo("Available rule profiles ({}):".format(len(profiles)))
            for p in profiles:
                extends = " (extends: {})".format(p["extends"]) if p["extends"] else ""
                click.echo(
                    "  {:25s} {}{}".format(
                        p["name"],
                        p["description"],
                        extends,
                    )
                )
        return

    # --list: print available rules and exit
    if do_list:
        if json_mode:
            rules_list = [
                {
                    "id": r.id,
                    "severity": r.severity,
                    "description": r.description,
                    "check": r.check,
                    "threshold": r.threshold,
                }
                for r in BUILTIN_RULES
            ]
            click.echo(
                to_json(
                    json_envelope(
                        "check-rules",
                        summary={"verdict": "listed", "count": len(rules_list)},
                        rules=rules_list,
                    )
                )
            )
        else:
            click.echo("Built-in rules ({}):".format(len(BUILTIN_RULES)))
            for r in BUILTIN_RULES:
                thr = " (threshold={})".format(r.threshold) if r.threshold is not None else ""
                click.echo("  {:30s} [{:7s}] {}{}".format(r.id, r.severity, r.description, thr))
        return

    ensure_index()

    # W1019d (Pattern 2 — silent fallback): single accumulator threaded
    # through all three sub-loaders. Drained into ``summary.warnings_out``
    # + flips ``summary.partial_success=True`` when populated so a
    # consumer reading only the summary still sees the silent-state
    # disclosure.
    check_rules_warnings: list[str] = []

    # W1030-followup-C: opt the three sub-loaders into ``return_status=True``
    # so the on-disk state of ``.roam-rules.yml`` (missing / empty_file /
    # empty_yaml / parse_error / wrong_root_type / read_error /
    # schema_invalid / ok) surfaces on the envelope as a closed-enum
    # ``summary.config_state`` field. All three sub-loaders read the SAME
    # file (``.roam-rules.yml``); the worst-status rollup is defensive
    # against future divergence + cleanly disambiguates the empty-stub
    # state from the missing-state for downstream agents reading the
    # envelope.
    user_overrides, _user_status = _load_user_config_with_status(config_path, warnings_out=check_rules_warnings)

    # Resolve profile: CLI --profile takes precedence over config file profile:
    effective_profile = profile_name
    # Profile sub-loader runs regardless of whether --profile was supplied
    # so we always sample a third LoadStatus for the rollup. When
    # --profile wins, the loaded value is discarded but the status feeds
    # ``config_state``. The sub-loader is cheap (re-reads + re-parses the
    # same path via the canonical helper; identical warnings collapse via
    # the dedup pass at the bottom of this command).
    _file_profile, _profile_status = _load_config_profile_with_status(config_path, warnings_out=check_rules_warnings)
    if effective_profile is None:
        effective_profile = _file_profile
    # Third sub-loader sample: the raw load. Today this returns the same
    # status as the other two (single file, single load step), but keep
    # the rollup honest if a future raw-loader migration diverges.
    _, _raw_status = _load_raw_config_with_status(config_path, warnings_out=check_rules_warnings)
    _config_state = _worst_status(_user_status, _profile_status, _raw_status)

    if effective_profile:
        from roam.rules.builtin import resolve_profile

        try:
            profile_overrides = resolve_profile(effective_profile)
        except ValueError as e:
            click.echo(f"Error: {e}")
            raise SystemExit(1) from None
        # Profile overrides are the base; user overrides (from rules: section) layer on top
        merged = {ov.get("id"): ov for ov in profile_overrides}
        for ov in user_overrides:
            rid = ov.get("id", "")
            if rid:
                if rid in merged:
                    merged[rid].update(ov)
                else:
                    merged[rid] = ov
        user_overrides = list(merged.values())

    # Resolve rules to evaluate
    rules_to_run = _resolve_rules(rule_filter, severity_filter, user_overrides)

    # Build graph once (needed by several rules)
    from roam.graph.builder import build_symbol_graph

    with open_db(readonly=True) as conn:
        try:
            G = build_symbol_graph(conn)
        except sqlite3.Error:
            G = None

        # Evaluate each built-in rule
        results = []
        for rule in rules_to_run:
            violations = rule.evaluate(conn, G)
            results.append(
                {
                    "id": rule.id,
                    "severity": rule.severity,
                    "description": rule.description,
                    "check": rule.check,
                    "threshold": rule.threshold,
                    "passed": len(violations) == 0,
                    "violation_count": len(violations),
                    "violations": violations,
                }
            )

        # Evaluate custom rules from .roam/rules
        # W1036: plumb the same accumulator through so per-rule-file
        # parse failures appear alongside the .roam-rules.yml warnings.
        results.extend(
            _evaluate_custom_rules(
                conn,
                rule_filter,
                severity_filter,
                warnings_out=check_rules_warnings,
            )
        )

    # W1019d: dedup warnings while preserving insertion order. The two
    # sub-loaders both call _load_raw_config, so a file-level malformation
    # would otherwise be reported twice.
    seen_warnings: set[str] = set()
    deduped_warnings: list[str] = []
    for w in check_rules_warnings:
        if w not in seen_warnings:
            seen_warnings.add(w)
            deduped_warnings.append(w)

    if not results:
        verdict = "no rules matched"
        if json_mode:
            empty_summary: dict = {"verdict": verdict, "passed": 0, "failed": 0, "total": 0}
            # W1030-followup-C: closed-enum LoadStatus disclosure so agents
            # can tell "no .roam-rules.yml configured yet" (missing -> use
            # baseline rules silently) from ".roam-rules.yml exists but is
            # empty" (empty_file / empty_yaml -> use baseline rules AND
            # the user probably meant to configure something) from
            # ".roam-rules.yml is broken" (parse_error / wrong_root_type
            # / read_error / schema_invalid -- already accompanied by a
            # warning in warnings_out).
            empty_summary["config_state"] = _config_state
            _empty_degraded = _config_state in _DEGRADED_LOAD_STATUSES
            if deduped_warnings:
                empty_summary["partial_success"] = True
            elif _empty_degraded:
                # W1030-followup-C: a degraded config_state flips
                # partial_success regardless of warning emission, mirroring
                # cmd_alerts + cmd_budget + cmd_health. The user's
                # rules config did not materialize -- agents must see the
                # discard, not just the "no rules matched" verdict.
                empty_summary["partial_success"] = True
            _empty_facts = _build_config_state_facts(_config_state)
            empty_envelope_kwargs: dict = dict(
                summary=empty_summary,
                results=[],
                warnings_out=list(deduped_warnings),
            )
            if _empty_facts:
                empty_envelope_kwargs["agent_contract"] = {
                    "facts": [verdict, *_empty_facts],
                    "next_commands": ["roam check-rules"],
                }
            click.echo(to_json(json_envelope("check-rules", **empty_envelope_kwargs)))
        else:
            click.echo("VERDICT: {}".format(verdict))
            if deduped_warnings:
                click.echo()
                click.echo(f"Warnings ({len(deduped_warnings)}):")
                for w in deduped_warnings:
                    click.echo(f"  - {w}")
        return

    verdict, exit_code = _calculate_verdict(results)

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import runtime_filter_disclosure, write_sarif

        # W1114: pass accumulated loader warnings onto the SARIF
        # toolExecutionNotifications[] array so a CI consumer sees the
        # silent-fallback disclosure that the JSON/text paths already
        # carry via ``summary.warnings_out`` / the text Warnings block.
        # Hash invariant: empty/missing warnings keep the SARIF output
        # byte-identical to pre-W1114.
        #
        # W1061-followup-2: rule-level + finding-level filter disclosure
        # delegated to the shared :func:`runtime_filter_disclosure`
        # helper. Original W1061-followup semantics preserved:
        #   --rule    -> rule-id-level disable; every rule NOT matching
        #               the filter becomes a ``ruleConfigurationOverride``
        #               with ``configuration.enabled: false``.
        #   --severity -> finding-level filter (rules don't carry severity
        #               1:1 — same rule can emit multiple severity tiers).
        #               Surfaces as a ``notificationConfigurationOverride``
        #               under a synthetic ``severity-filter`` descriptor.
        rule_disabled: list[tuple[str, dict]] = []
        finding_filters: list[tuple[str, dict]] = []
        if rule_filter:
            # ``rule_filter`` selects exactly one rule by id — the
            # disabled set is every other rule in the result set.
            for r in results:
                rid = r["id"]
                if rid != rule_filter:
                    rule_disabled.append(
                        (
                            f"rules/{rid}",
                            {"disabled_by": "--rule", "filter_value": rule_filter},
                        )
                    )
        if severity_filter:
            finding_filters.append(
                (
                    "severity-filter",
                    {"filter": "--severity", "filter_value": severity_filter},
                )
            )
        sarif_overrides, sarif_notif_overrides = runtime_filter_disclosure(
            rule_ids_disabled=rule_disabled,
            finding_level_filters=finding_filters,
        )
        sarif = _results_to_sarif(
            results,
            warnings_out=list(deduped_warnings),
            runtime_overrides=sarif_overrides or None,
            runtime_notification_overrides=sarif_notif_overrides or None,
        )
        click.echo(write_sarif(sarif))
        if exit_code != 0:
            ctx.exit(exit_code)
        return

    # --- JSON output ---
    if json_mode:
        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        failed = total - passed
        errors = sum(1 for r in results if not r["passed"] and r["severity"] == "error")
        warnings = sum(1 for r in results if not r["passed"] and r["severity"] == "warning")

        summary: dict = {
            "verdict": verdict,
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "warnings": warnings,
        }
        # W1030-followup-C: closed-enum LoadStatus disclosure on the
        # envelope. Mirrors the cmd_alerts + cmd_budget + cmd_health
        # vocabulary so the four W1030-followup-cohort emitters use a
        # uniform field name (``summary.config_state``) and value space
        # (LOAD_STATUSES). Lets agents disambiguate "no .roam-rules.yml
        # configured yet" (missing -> use baseline rules silently) from
        # ".roam-rules.yml exists but is empty" (empty_file / empty_yaml
        # -> use baseline rules + flag the empty stub) from
        # ".roam-rules.yml is broken" (parse_error / wrong_root_type /
        # read_error / schema_invalid -- already accompanied by a
        # warning in warnings_out).
        summary["config_state"] = _config_state
        # W1019d: silent-fallback disclosure on the envelope. A consumer
        # reading only the summary still sees that the config file was
        # malformed via ``partial_success=True``.
        # W1030-followup-C: a degraded config_state flips partial_success
        # too -- even when no warning fired (e.g. empty stub on disk),
        # agents must see that the user's intent did not materialize.
        _config_degraded = _config_state in _DEGRADED_LOAD_STATUSES
        if deduped_warnings or _config_degraded:
            summary["partial_success"] = True

        # W1030-followup-C: surface the on-disk state via
        # agent_contract.facts so consumers reading only the contract
        # still see the silent-state disclosure. LAW 4 anchored on the
        # concrete-noun terminal "rules" (the governance rules
        # check-rules evaluates). LAW 6: every fact stands alone.
        _state_facts = _build_config_state_facts(_config_state)
        envelope_kwargs: dict = dict(
            budget=token_budget,
            summary=summary,
            results=results,
            warnings_out=list(deduped_warnings),
        )
        if _state_facts:
            envelope_kwargs["agent_contract"] = {
                "facts": [verdict, *_state_facts],
                "next_commands": ["roam check-rules"],
            }

        # Pattern-1 machine-gate: only flag a run with error-severity
        # violations (exit_code != 0 -> FAIL). PASS/WARN both return exit 0
        # and must stay clean — gate strictly on the computed exit code.
        if exit_code != 0:
            envelope_kwargs["status"] = "partial_failure"
            envelope_kwargs["isError"] = True
            envelope_kwargs["error_code"] = "PARTIAL_FAILURE"
            envelope_kwargs["error"] = verdict

        envelope = json_envelope("check-rules", **envelope_kwargs)
        click.echo(to_json(envelope))
        if exit_code != 0:
            ctx.exit(exit_code)
        return

    # --- Text output ---
    click.echo("VERDICT: {}".format(verdict))
    click.echo()

    # W1019d: surface accumulated config-load warnings prominently — before
    # the rule list so the user sees the silent-state disclosure even when
    # stdout is piped to ``head``. Mirrors the cmd_smells discipline (W987).
    if deduped_warnings:
        click.echo(f"Warnings ({len(deduped_warnings)}):")
        for w in deduped_warnings:
            click.echo(f"  - {w}")
        click.echo()

    total = len(results)
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    click.echo("Rules: {}/{} passed".format(len(passed), total))
    click.echo()

    if failed:
        click.echo("=== Failing Rules ===")
        for r in failed:
            count = r["violation_count"]
            click.echo(
                "  [{}] {} -- {} ({} violation{})".format(
                    "FAIL",
                    r["id"],
                    r["description"],
                    count,
                    "s" if count != 1 else "",
                )
            )
            for v in r["violations"][:5]:
                loc = v.get("file", "")
                if v.get("line"):
                    loc += ":{}".format(v["line"])
                sym = v.get("symbol", "")
                reason = v.get("reason", "")
                if sym:
                    click.echo("    - {} at {}".format(sym, loc))
                else:
                    click.echo("    - {}".format(loc))
                if reason:
                    click.echo("      {}".format(reason))
            if count > 5:
                click.echo("    (+{} more violations)".format(count - 5))
        click.echo()

    if passed:
        click.echo("=== Passing Rules ===")
        for r in passed:
            click.echo("  [PASS] {}".format(r["id"]))

    if exit_code != 0:
        ctx.exit(exit_code)
