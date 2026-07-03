"""``roam rules-validate`` — lint a ``.roam/rules.yml`` file before shipping.

Catches typos, schema mistakes, and unsupported pattern names so customers
don't discover them when ``pr-analyze`` silently skips a malformed rule
in production. Optional ``--against DIFF`` runs the loaded rules against
a sample diff and prints which would fire — handy for dry-running a new
rule against a representative PR.

Pairs with ``cmd_pr_analyze`` (the consumer) and the sample at
``templates/examples/.roam-rules.yml``.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because rules-validate checks the RULES FILE SCHEMA itself
(YAML structure, glob syntax, severity spelling) — not code violations.
The two-stage rules workflow separates concerns: ``rules-validate`` ensures
rule definitions are well-formed BEFORE shipping; ``roam rules`` runs the
validated rules against code and emits per-violation SARIF. SARIF here
would conflate validator-output with code-analyzer-output. See action.yml
_SUPPORTED_SARIF allowlist + W1185 audit memo.
"""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

EXIT_GATE_FAILURE = 5

# Mirror of cmd_pr_analyze._PATTERN_MATCHERS keys — kept in sync via the
# ``ALLOWED_PATTERNS`` re-export below; tested in test_rules_validate.
ALLOWED_PATTERNS = ("import_from", "function_call", "class_inherit", "decorator_use")
ALLOWED_SEVERITIES = ("BLOCK", "WARN", "INFO", "WARNING")
REQUIRED_KEYS = ("id", "pattern", "forbidden_target_glob")
OPTIONAL_KEYS = ("description", "source_glob", "severity")


def _load_yaml(path: Path) -> tuple[dict | None, str | None]:
    """Return ``(parsed, error_message)``.

    Falls back to the in-tree :func:`roam.rules.engine._parse_simple_yaml`
    when PyYAML isn't installed (PyYAML is intentionally not a runtime
    dep). Distinguishes ``parsed=None`` (read failure) from
    ``parsed={}`` (empty / no rules).
    """
    if not path.exists():
        return None, f"file not found: {path}"
    try:
        import yaml
    except ImportError:
        # PyYAML is optional; use the in-tree minimal parser. It returns None
        # for expected read/parse failures, so check the return value instead
        # of swallowing every exception type.
        from roam.rules.engine import _parse_simple_yaml

        data = _parse_simple_yaml(path)
        if data is None:
            return None, "fallback YAML parser failed: malformed or unreadable YAML"
    else:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, UnicodeDecodeError) as exc:
            return None, f"YAML read error: {exc}"
        except yaml.YAMLError as exc:
            return None, f"YAML parse error: {exc}"

    if not isinstance(data, dict):
        return None, "top-level YAML must be a mapping with a `rules:` key"
    return data, None


def _validate_glob(glob_str: str, *, field: str, rule_id: str) -> str | None:
    """Sanity-check a glob — return error message or None.

    fnmatch is permissive (almost everything is a valid glob), so we
    only reject the obviously broken cases: empty strings, leading whitespace,
    unbalanced brackets that would crash :func:`fnmatch.fnmatch`.
    """
    if not isinstance(glob_str, str):
        return f"rule `{rule_id}` `{field}` must be a string, got {type(glob_str).__name__}"
    if not glob_str.strip():
        return f"rule `{rule_id}` `{field}` is empty"
    if glob_str != glob_str.strip():
        return f"rule `{rule_id}` `{field}` has leading or trailing whitespace"
    # fnmatch accepts any chars but `[` without `]` raises re.error inside.
    open_brackets = glob_str.count("[")
    close_brackets = glob_str.count("]")
    if open_brackets != close_brackets:
        return f"rule `{rule_id}` `{field}` has unbalanced brackets: {open_brackets} `[` vs {close_brackets} `]`"
    try:
        fnmatch.fnmatch("smoke/test/path", glob_str)
    except Exception as exc:  # noqa: BLE001 — defensive
        return f"rule `{rule_id}` `{field}` rejected by fnmatch: {exc}"
    return None


def _validate_rule(rule: dict, index: int) -> tuple[list[str], list[str]]:
    """Validate a single rule mapping. Returns ``(errors, warnings)``."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(rule, dict):
        return [f"rule #{index} is not a mapping (got {type(rule).__name__})"], []

    rule_id = rule.get("id", f"<unnamed-{index}>")

    for key in REQUIRED_KEYS:
        if key not in rule or rule[key] in (None, ""):
            errors.append(f"rule `{rule_id}` missing required field `{key}`")

    pattern = rule.get("pattern", "")
    if pattern and pattern not in ALLOWED_PATTERNS:
        errors.append(f"rule `{rule_id}` has unknown pattern `{pattern}` (supported: {', '.join(ALLOWED_PATTERNS)})")

    severity_raw = rule.get("severity")
    if severity_raw is not None:
        if not isinstance(severity_raw, str):
            errors.append(f"rule `{rule_id}` `severity` must be a string, got {type(severity_raw).__name__}")
        elif severity_raw.upper() not in ALLOWED_SEVERITIES:
            errors.append(
                f"rule `{rule_id}` has unknown severity `{severity_raw}` "
                f"(supported: {', '.join(s for s in ALLOWED_SEVERITIES if s != 'WARNING')})"
            )
    else:
        warnings.append(f"rule `{rule_id}` has no `severity` — will default to WARN")

    for field in ("source_glob", "forbidden_target_glob"):
        if field in rule and rule[field] not in (None, ""):
            err = _validate_glob(rule[field], field=field, rule_id=rule_id)
            if err:
                errors.append(err)
    if not rule.get("source_glob"):
        warnings.append(f"rule `{rule_id}` has no `source_glob` — will match every file (`*`)")

    if not rule.get("description"):
        warnings.append(f"rule `{rule_id}` has no `description` — surface in PR comments will be terse")

    unknown_keys = set(rule) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
    if unknown_keys:
        warnings.append(f"rule `{rule_id}` has unknown field(s): {', '.join(sorted(unknown_keys))} (typo?)")

    return errors, warnings


def _apply_safe_fixes(rules: list[dict]) -> tuple[list[dict], list[str]]:
    """Auto-coerce safe schema mistakes. Returns (fixed_rules, applied_log).

    Fixes applied (all conservative — never touch unknown fields, never guess
    a missing required field):
      - severity case normalisation: "block" / "Block" → "BLOCK"
      - trim leading/trailing whitespace on glob fields
      - dedupe trailing slashes in source_glob (cosmetic)

    Skipped (require human judgment):
      - misspelled severities (BLOK, BLCK) → still flagged as errors
      - unknown patterns → still flagged as errors
      - missing required fields → still flagged as errors
    """
    out: list[dict] = []
    applied: list[str] = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            out.append(r)
            continue
        fixed = dict(r)
        rid = fixed.get("id", f"<unnamed-{i}>")

        sev = fixed.get("severity")
        if isinstance(sev, str) and sev != sev.upper() and sev.upper() in ALLOWED_SEVERITIES:
            applied.append(f"rule `{rid}`: severity '{sev}' → '{sev.upper()}'")
            fixed["severity"] = sev.upper()

        for field in ("source_glob", "forbidden_target_glob"):
            val = fixed.get(field)
            if isinstance(val, str) and val != val.strip() and val.strip():
                applied.append(f"rule `{rid}`: {field} trimmed whitespace ({val!r} → {val.strip()!r})")
                fixed[field] = val.strip()

        out.append(fixed)
    return out, applied


def _check_duplicate_ids(rules: list[dict]) -> list[str]:
    """Return errors for any duplicated rule IDs."""
    errors: list[str] = []
    seen: dict[str, int] = {}
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid in seen:
            errors.append(f"duplicate rule id `{rid}` at indices {seen[rid]} and {i}")
        else:
            seen[rid] = i
    return errors


def _dry_run_against_diff(rules: list[dict], diff_path: Path) -> tuple[list[dict], str | None]:
    """Run the loaded rules against ``diff_path`` and return matching violations."""
    if not diff_path.exists():
        return [], f"diff file not found: {diff_path}"
    try:
        diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"diff read error: {exc}"

    # Lazy import — only when --against is used, keeps cmd_rules_validate
    # decoupled from cmd_pr_analyze in the lazy-load happy path.
    from roam.commands.cmd_pr_analyze import _check_rules

    return _check_rules(diff_text, rules), None


# Pattern documentation block, surfaced by --explain.
_PATTERN_DOCS: dict[str, dict[str, str]] = {
    "import_from": {
        "matches": 'Python `from X import` / `import X` and JS/TS `import ... from "X"` whose target matches the forbidden glob.',
        "example_source_glob": "src/**/*.py",
        "example_forbidden": "lib.unsafe.*",
        "example_line": "from lib.unsafe.crypto import md5_hash",
        "use_case": "Layer-violation enforcement, banned-library bans.",
    },
    "function_call": {
        "matches": "Any call `name(` or `ns.name(` whose qualified name matches (skips `def`/`class` definition lines).",
        "example_source_glob": "src/**/*.py",
        "example_forbidden": "eval",
        "example_line": "result = eval(user_input)",
        "use_case": "Banning dangerous APIs (eval, pickle.loads, os.system, exec).",
    },
    "class_inherit": {
        "matches": "A class declaration whose base list contains a forbidden base.",
        "example_source_glob": "src/**/*.py",
        "example_forbidden": "DangerousMixin",
        "example_line": "class MyHandler(DangerousMixin, BaseHandler):",
        "use_case": "Forbidding deprecated or dangerous parent classes.",
    },
    "decorator_use": {
        "matches": "A decorator line `@name` or `@ns.name` matching the forbidden glob.",
        "example_source_glob": "src/**/*.py",
        "example_forbidden": "deprecated",
        "example_line": "@deprecated",
        "use_case": "Catch decorators marking new code as already-stale (or forbidden meta-programming).",
    },
}


def _print_explain_block() -> None:
    """Print pattern-matcher reference suitable for first-time rule authors."""
    click.echo()
    click.echo("Pattern reference:")
    click.echo()
    for pattern, info in _PATTERN_DOCS.items():
        click.echo(f"  {pattern}")
        click.echo(f"    Matches: {info['matches']}")
        click.echo(f"    Example: {info['example_line']}")
        click.echo(f"    Use case: {info['use_case']}")
        click.echo(
            f"    Sample rule: id, pattern: {pattern}, source_glob: {info['example_source_glob']}, "
            f"forbidden_target_glob: {info['example_forbidden']}"
        )
        click.echo()
    click.echo("Glob syntax: fnmatch (`*`, `**`, `{a,b}`, `[abc]`).")
    click.echo("Severities: BLOCK (gate failure) | WARN | INFO. Default: WARN.")


@roam_capability(
    name="rules-validate",
    category="workflow",
    summary="Lint a `.roam/rules.yml` file before shipping it to your team",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="rules-validate")
@click.argument("rules_path", type=click.Path(), default=".roam/rules.yml")
@click.option(
    "--against",
    "diff_path",
    type=click.Path(),
    default=None,
    help="Optional sample diff to dry-run rules against; reports which would fire.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Treat warnings as errors (exit 5 on any warning).",
)
@click.option(
    "--gate",
    is_flag=True,
    help="Exit 5 (gate failure) on any error; useful in CI.",
)
@click.option(
    "--explain",
    is_flag=True,
    help="Print pattern reference (matchers + glob examples) — handy for first-time rule authors.",
)
@click.option(
    "--fix",
    is_flag=True,
    help="Auto-coerce safe schema mistakes (severity casing, trim whitespace) and write back. Skips real typos.",
)
@click.pass_context
def rules_validate_cmd(
    ctx,
    rules_path: str,
    diff_path: str | None,
    strict: bool,
    gate: bool,
    explain: bool,
    fix: bool,
) -> None:
    """Lint a `.roam/rules.yml` file before shipping it to your team.

    \b
    Examples:
      roam rules-validate                                 # checks .roam/rules.yml
      roam rules-validate templates/examples/.roam-rules.yml
      roam rules-validate --against pr.diff               # dry-run against a sample
      roam rules-validate --gate                          # CI mode: exit 5 on error

    Catches typos like `severity: BLOK`, missing required fields, unknown
    pattern names, duplicate rule IDs, and unbalanced glob brackets — all
    before customers discover them mid-PR.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    path = Path(rules_path)
    parsed, load_error = _load_yaml(path)

    if load_error and parsed is None:
        summary = {
            "verdict": f"load failed: {load_error}",
            "rules_path": str(path),
            "rules_loaded": 0,
            "errors_count": 1,
            "warnings_count": 0,
        }
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "rules-validate",
                        summary=summary,
                        errors=[load_error],
                        warnings=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {summary['verdict']}")
            click.echo(f"  path: {path}")
        if gate:
            sys.exit(EXIT_GATE_FAILURE)
        return

    raw_rules = parsed.get("rules", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_rules, list):
        raw_rules = []

    fixes_applied: list[str] = []
    if fix:
        # Apply safe fixes before validation so the output reports the
        # post-fix state. Real errors (typos, missing fields) are still surfaced.
        raw_rules, fixes_applied = _apply_safe_fixes(raw_rules)
        if fixes_applied:
            doc = parsed if isinstance(parsed, dict) else {}
            doc["rules"] = raw_rules
            try:
                # Re-emit YAML; preserve top-level structure beyond `rules:`.
                import yaml

                path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            except ImportError:
                # 12.37 (2026-05-06) — installs without PyYAML use the
                # in-tree minimal emitter so --fix still works.
                try:
                    from roam.rules.engine import _emit_simple_yaml

                    path.write_text(_emit_simple_yaml(doc), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    fixes_applied.append(f"WARN — write-back failed (no PyYAML): {exc}")
            except Exception as exc:  # noqa: BLE001 — surfaced as a warning
                warnings_list_for_fix_failure = f"--fix: could not write back to {path}: {exc}"
                fixes_applied.append(f"WARN — write-back failed: {warnings_list_for_fix_failure}")

    errors: list[str] = []
    warnings: list[str] = []
    rule_count = 0

    for i, rule in enumerate(raw_rules):
        rule_count += 1
        rule_errors, rule_warnings = _validate_rule(rule, i)
        errors.extend(rule_errors)
        warnings.extend(rule_warnings)

    errors.extend(_check_duplicate_ids(raw_rules))

    dry_run_violations: list[dict] = []
    dry_run_error: str | None = None
    if diff_path:
        valid_rules = [r for r in raw_rules if isinstance(r, dict) and r.get("pattern") in ALLOWED_PATTERNS]
        dry_run_violations, dry_run_error = _dry_run_against_diff(valid_rules, Path(diff_path))
        if dry_run_error:
            errors.append(dry_run_error)

    error_count = len(errors)
    warning_count = len(warnings)

    if error_count == 0 and warning_count == 0:
        verdict = f"valid ({rule_count} rule(s) loaded clean)"
    elif error_count == 0:
        verdict = f"valid with {warning_count} warning(s) ({rule_count} rule(s))"
    else:
        verdict = f"INVALID ({error_count} error(s), {warning_count} warning(s) across {rule_count} rule(s))"

    summary = {
        "verdict": verdict,
        "rules_path": str(path),
        "rules_loaded": rule_count,
        "errors_count": error_count,
        "warnings_count": warning_count,
    }
    if diff_path:
        summary["dry_run_diff"] = diff_path
        summary["dry_run_matches"] = len(dry_run_violations)

    if fix:
        summary["fixes_applied"] = len(fixes_applied)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "rules-validate",
                    summary=summary,
                    errors=errors,
                    warnings=warnings,
                    dry_run_violations=dry_run_violations,
                    fixes_applied=fixes_applied,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path:     {path}")
        click.echo(f"  rules:    {rule_count}")
        click.echo(f"  errors:   {error_count}")
        click.echo(f"  warnings: {warning_count}")
        if fixes_applied:
            click.echo()
            click.echo(f"Fixes applied ({len(fixes_applied)}):")
            for f in fixes_applied:
                click.echo(f"  - {f}")
        if errors:
            click.echo()
            click.echo("Errors:")
            for e in errors:
                click.echo(f"  - {e}")
        if warnings:
            click.echo()
            click.echo("Warnings:")
            for w in warnings:
                click.echo(f"  - {w}")
        if diff_path:
            click.echo()
            click.echo(f"Dry-run against {diff_path}:")
            if dry_run_error:
                click.echo(f"  ERROR: {dry_run_error}")
            elif not dry_run_violations:
                click.echo("  no rules matched the sample diff")
            else:
                click.echo(f"  {len(dry_run_violations)} match(es):")
                for v in dry_run_violations[:10]:
                    click.echo(
                        f"    [{v.get('severity', '?')}] {v.get('rule_id', '?')}: "
                        f"{v.get('file', '?')} -> {v.get('matched_target', v.get('matched_import', '?'))}"
                    )
                if len(dry_run_violations) > 10:
                    click.echo(f"    ... and {len(dry_run_violations) - 10} more")

    if explain and not json_mode:
        _print_explain_block()

    fail = error_count > 0 or (strict and warning_count > 0)
    if gate and fail:
        if not json_mode:
            click.echo(err=True)
            click.echo(
                f"Gate fired (exit {EXIT_GATE_FAILURE}). Re-run without --gate to see full diagnostics interactively.",
                err=True,
            )
        sys.exit(EXIT_GATE_FAILURE)
