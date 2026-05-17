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

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
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
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    resolved = _find_config_path(config_path)
    if resolved is None:
        return {}

    data = load_yaml_with_warnings(
        Path(resolved),
        tiny_parser=_parse_simple_yaml_text,
        config_label="roam-rules",
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file (helper short-circuits absence).
        return {}
    # ``allow_list_root`` is False (default) so the helper guarantees a
    # mapping. The assert keeps the type checker honest on the
    # post-helper extraction logic below.
    assert isinstance(data, dict)
    return data


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
    """
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = _load_raw_config(config_path, warnings_out=warnings_out)
    if not data:
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper / raw-loader already explained the failure (read error /
        # malformed YAML / wrong root type). Propagate the empty result
        # without piling on a second warning that would confuse the caller.
        return []

    if "rules" not in data:
        # No `rules:` key — a profile-only config is legitimate, so this is
        # NOT a warning. Return empty overrides and let the caller decide.
        return []
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
    return rules


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
    """
    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = _load_raw_config(config_path, warnings_out=warnings_out)
    if not data:
        return None
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the file-level failure; don't pile on.
        return None
    if "profile" not in data:
        # Absence of a profile key is legitimate (rules-only configs).
        return None
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
        return profile.strip()
    return None


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

    # Filter by severity
    if severity_filter:
        resolved = [r for r in resolved if r.severity == severity_filter]

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
        if severity_filter and severity != severity_filter:
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
    type=click.Choice(["error", "warning", "info"]),
    help="Only show rules matching this severity.",
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
def check_rules(ctx, rule_filter, severity_filter, config_path, profile_name, do_list, do_list_profiles):
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

    from roam.rules.builtin import BUILTIN_RULES, list_profiles

    # --list-profiles: print available profiles and exit
    if do_list_profiles:
        profiles = list_profiles()
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

    # Load user config
    user_overrides = _load_user_config(config_path, warnings_out=check_rules_warnings)

    # Resolve profile: CLI --profile takes precedence over config file profile:
    effective_profile = profile_name
    if effective_profile is None:
        effective_profile = _load_config_profile(config_path, warnings_out=check_rules_warnings)

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
    with open_db(readonly=True) as conn:
        try:
            from roam.graph.builder import build_symbol_graph

            G = build_symbol_graph(conn)
        except Exception:
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
            if deduped_warnings:
                empty_summary["partial_success"] = True
            click.echo(
                to_json(
                    json_envelope(
                        "check-rules",
                        summary=empty_summary,
                        results=[],
                        warnings_out=list(deduped_warnings),
                    )
                )
            )
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
        # W1019d: silent-fallback disclosure on the envelope. A consumer
        # reading only the summary still sees that the config file was
        # malformed via ``partial_success=True``.
        if deduped_warnings:
            summary["partial_success"] = True

        envelope = json_envelope(
            "check-rules",
            budget=token_budget,
            summary=summary,
            results=results,
            warnings_out=list(deduped_warnings),
        )
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
