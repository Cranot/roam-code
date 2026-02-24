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

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


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


def _load_raw_config(config_path: str | None) -> dict:
    """Load and parse the YAML config file into a raw dict.

    Returns an empty dict if not found or on parse error.
    """
    resolved = _find_config_path(config_path)
    if resolved is None:
        return {}

    try:
        import yaml
        with open(resolved, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        data = _parse_simple_yaml(Path(resolved))
    except Exception:
        return {}

    if not data or not isinstance(data, dict):
        return {}
    return data


def _load_user_config(config_path: str | None) -> list[dict]:
    """Load user rule overrides from .roam-rules.yml.

    Returns a list of rule override dicts. Fields:
      id (str): rule ID to match against built-in rules
      enabled (bool, optional): set to false to disable
      threshold (float, optional): override the default threshold
      severity (str, optional): override severity
    """
    data = _load_raw_config(config_path)
    if not data:
        return []

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        return []
    return rules


def _load_config_profile(config_path: str | None) -> str | None:
    """Load the profile name from .roam-rules.yml if present.

    Returns the profile name string or None.
    """
    data = _load_raw_config(config_path)
    profile = data.get("profile")
    if isinstance(profile, str) and profile.strip():
        return profile.strip()
    return None


def _parse_simple_yaml(path: Path) -> dict:
    """Minimal YAML parser fallback when PyYAML is unavailable."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
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
    from roam.rules.builtin import BUILTIN_RULES
    import copy

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
        verdict = "FAIL - {} error(s), {} warning(s), {} info".format(
            len(errors), len(warnings), len(infos)
        )
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

def _results_to_sarif(results: list[dict]) -> dict:
    """Convert check-rules results to SARIF 2.1.0 format."""
    from roam.output.sarif import rules_to_sarif

    # Transform results to the format rules_to_sarif expects
    sarif_results = []
    for r in results:
        sarif_results.append({
            "name": r["id"],
            "passed": r["passed"],
            "severity": r["severity"],
            "violations": r.get("violations", []),
        })
    return rules_to_sarif(sarif_results)


def _evaluate_custom_rules(conn, rule_filter: str | None, severity_filter: str | None) -> list[dict]:
    """Evaluate custom `.roam/rules` rules and adapt to check-rules result shape."""
    from roam.rules.engine import evaluate_all

    rules_dir = find_project_root() / ".roam" / "rules"
    if not rules_dir.is_dir():
        return []

    raw_results = evaluate_all(rules_dir, conn)
    adapted: list[dict] = []
    for item in raw_results:
        name = item.get("name", "unnamed")
        severity = item.get("severity", "error")

        if rule_filter and name != rule_filter:
            continue
        if severity_filter and severity != severity_filter:
            continue

        violations = item.get("violations", [])
        adapted.append({
            "id": name,
            "severity": severity,
            "description": item.get("description", "Custom rule"),
            "check": item.get("type", "custom"),
            "threshold": None,
            "source": "custom",
            "passed": len(violations) == 0,
            "violation_count": len(violations),
            "violations": violations,
        })
    return adapted



# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("check-rules")
@click.option(
    "--rule", "rule_filter", default=None,
    help="Run only this specific built-in rule ID or custom rule name.",
)
@click.option(
    "--severity", "severity_filter", default=None,
    type=click.Choice(["error", "warning", "info"]),
    help="Only show rules matching this severity.",
)
@click.option(
    "--config", "config_path", default=None,
    help="Path to .roam-rules.yml config file.",
)
@click.option(
    "--profile", "profile_name", default=None,
    help="Use a named rule profile (e.g. strict-security, ai-code-review, legacy-maintenance, minimal).",
)
@click.option(
    "--list", "do_list", is_flag=True,
    help="List all available built-in rules and exit.",
)
@click.option(
    "--list-profiles", "do_list_profiles", is_flag=True,
    help="List all available rule profiles and exit.",
)
@click.pass_context
def check_rules(ctx, rule_filter, severity_filter, config_path, profile_name, do_list, do_list_profiles):
    """Run structural governance rules against the indexed codebase.

    Built-in rules cover: circular imports, fan-out/fan-in, file complexity,
    file length, test coverage, god classes, deep inheritance, layer violations,
    and orphan symbols.

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
            click.echo(to_json(json_envelope(
                "check-rules",
                summary={"verdict": "profiles listed", "count": len(profiles)},
                profiles=profiles,
            )))
        else:
            click.echo("Available rule profiles ({}):".format(len(profiles)))
            for p in profiles:
                extends = " (extends: {})".format(p["extends"]) if p["extends"] else ""
                click.echo("  {:25s} {}{}".format(
                    p["name"], p["description"], extends,
                ))
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
            click.echo(to_json(json_envelope(
                "check-rules",
                summary={"verdict": "listed", "count": len(rules_list)},
                rules=rules_list,
            )))
        else:
            click.echo("Built-in rules ({}):".format(len(BUILTIN_RULES)))
            for r in BUILTIN_RULES:
                thr = " (threshold={})".format(r.threshold) if r.threshold is not None else ""
                click.echo("  {:30s} [{:7s}] {}{}".format(
                    r.id, r.severity, r.description, thr
                ))
        return

    ensure_index()

    # Load user config
    user_overrides = _load_user_config(config_path)

    # Resolve profile: CLI --profile takes precedence over config file profile:
    effective_profile = profile_name
    if effective_profile is None:
        effective_profile = _load_config_profile(config_path)

    if effective_profile:
        from roam.rules.builtin import resolve_profile
        try:
            profile_overrides = resolve_profile(effective_profile)
        except ValueError as e:
            click.echo("Error: {}".format(e))
            raise SystemExit(1)
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
            results.append({
                "id": rule.id,
                "severity": rule.severity,
                "description": rule.description,
                "check": rule.check,
                "threshold": rule.threshold,
                "passed": len(violations) == 0,
                "violation_count": len(violations),
                "violations": violations,
            })

        # Evaluate custom rules from .roam/rules
        results.extend(_evaluate_custom_rules(conn, rule_filter, severity_filter))

    if not results:
        verdict = "no rules matched"
        if json_mode:
            click.echo(to_json(json_envelope(
                "check-rules",
                summary={"verdict": verdict, "passed": 0, "failed": 0, "total": 0},
                results=[],
            )))
        else:
            click.echo("VERDICT: {}".format(verdict))
        return

    verdict, exit_code = _calculate_verdict(results)

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import write_sarif
        sarif = _results_to_sarif(results)
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

        envelope = json_envelope(
            "check-rules",
            budget=token_budget,
            summary={
                "verdict": verdict,
                "total": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "warnings": warnings,
            },
            results=results,
        )
        click.echo(to_json(envelope))
        if exit_code != 0:
            ctx.exit(exit_code)
        return

    # --- Text output ---
    click.echo("VERDICT: {}".format(verdict))
    click.echo()

    total = len(results)
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    click.echo("Rules: {}/{} passed".format(len(passed), total))
    click.echo()

    if failed:
        click.echo("=== Failing Rules ===")
        for r in failed:
            sev = r["severity"].upper()
            count = r["violation_count"]
            click.echo("  [{}] {} -- {} ({} violation{})".format(
                "FAIL", r["id"], r["description"],
                count, "s" if count != 1 else "",
            ))
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
