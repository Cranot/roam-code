"""Plugin DSL for custom governance rules.

Users define architectural rules as YAML files in ``.roam/rules/``.
Roam evaluates them against the indexed graph and reports violations.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Example rule YAML templates
# ---------------------------------------------------------------------------

_EXAMPLE_PATH_RULE = """\
# Rule: Controllers must not call DB layer directly
name: "No controller calls DB directly"
description: "Controllers must go through service layer"
severity: error

match:
  from:
    file_glob: "**/controllers/**"
    kind: [function, method]
  to:
    file_glob: "**/db/**"
    kind: [function, method]
  max_distance: 1

exempt:
  symbols: [health_check]
  files: ["**/admin/**"]
"""

_EXAMPLE_SYMBOL_RULE = """\
# Rule: Exported functions must have test coverage
name: "Exported functions need tests"
description: "All exported functions with fan-in >= 2 must have test coverage"
severity: warning

match:
  kind: [function]
  exported: true
  min_fan_in: 2
  require:
    has_test: true

exempt:
  symbols: [main, cli]
  files: ["**/migrations/**"]
"""


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("rules")
@click.option("--init", "do_init", is_flag=True,
              help="Generate example rule files in .roam/rules/.")
@click.option("--ci", "ci_mode", is_flag=True,
              help="Exit code 1 on error-severity violations.")
@click.option("--rules-dir", "rules_dir_opt", default=None,
              help="Custom rules directory path.")
@click.pass_context
def rules(ctx, do_init, ci_mode, rules_dir_opt):
    """Evaluate custom governance rules defined in .roam/rules/.

    Rules are YAML files that define architectural constraints. Two rule
    types are supported: path_match (edges between from/to patterns)
    and symbol_match (symbols matching criteria with optional require).

    Use --init to create example rule files. Use --ci for CI gates
    (exit code 1 on error-severity violations).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    # --init: create example rules
    if do_init:
        _handle_init(root, json_mode, rules_dir_opt)
        return

    ensure_index()

    # Determine rules directory
    if rules_dir_opt:
        rules_dir = Path(rules_dir_opt)
    else:
        rules_dir = root / ".roam" / "rules"

    # Graceful handling when no rules directory exists
    if not rules_dir.is_dir():
        verdict = "no rules directory found"
        if json_mode:
            click.echo(to_json(json_envelope(
                "rules",
                summary={"verdict": verdict, "passed": 0, "failed": 0,
                         "warnings": 0, "total": 0},
                results=[],
            )))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(f"Create rules in {rules_dir} or run 'roam rules --init'.")
        return

    from roam.rules.engine import evaluate_all

    with open_db(readonly=True) as conn:
        results = evaluate_all(rules_dir, conn)

    # Tally results
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_errors = sum(1 for r in results if not r["passed"] and r["severity"] == "error")
    failed_warnings = sum(1 for r in results if not r["passed"] and r["severity"] == "warning")
    failed_infos = sum(1 for r in results if not r["passed"] and r["severity"] == "info")
    failed = total - passed

    if failed == 0:
        verdict = f"all {total} rules passed" if total > 0 else "no rules found"
    else:
        parts = []
        if failed_errors > 0:
            parts.append(f"{failed_errors} error(s)")
        if failed_warnings > 0:
            parts.append(f"{failed_warnings} warning(s)")
        if failed_infos > 0:
            parts.append(f"{failed_infos} info")
        verdict = f"{passed} of {total} rules passed, {', '.join(parts)}"

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "rules",
            summary={"verdict": verdict, "passed": passed, "failed": failed,
                     "warnings": failed_warnings, "total": total},
            results=results,
        )))
        if ci_mode and failed_errors > 0:
            ctx.exit(1)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        sev = r["severity"].upper()
        name = r["name"]
        violations = r.get("violations", [])
        count = len(violations)

        if r["passed"]:
            click.echo(f"  [{status}] {name}")
        else:
            click.echo(f"  [{status}] [{sev}] {name} ({count} violation(s))")
            for v in violations[:10]:
                sym = v.get("symbol", "")
                fpath = v.get("file", "")
                line = v.get("line")
                reason = v.get("reason", "")
                loc = f"{fpath}:{line}" if line else fpath
                click.echo(f"    - {sym} at {loc}")
                if reason:
                    click.echo(f"      {reason}")
            if count > 10:
                click.echo(f"    (+{count - 10} more)")

    if ci_mode and failed_errors > 0:
        ctx.exit(1)


# ---------------------------------------------------------------------------
# --init handler
# ---------------------------------------------------------------------------


def _handle_init(root: Path, json_mode: bool, rules_dir_opt: str | None):
    """Create example rule files in .roam/rules/."""
    if rules_dir_opt:
        rules_dir = Path(rules_dir_opt)
    else:
        rules_dir = root / ".roam" / "rules"

    rules_dir.mkdir(parents=True, exist_ok=True)

    path_rule_file = rules_dir / "no_controller_calls_db.yaml"
    symbol_rule_file = rules_dir / "exported_need_tests.yaml"

    created: list[str] = []

    if not path_rule_file.exists():
        path_rule_file.write_text(_EXAMPLE_PATH_RULE, encoding="utf-8")
        created.append(str(path_rule_file))

    if not symbol_rule_file.exists():
        symbol_rule_file.write_text(_EXAMPLE_SYMBOL_RULE, encoding="utf-8")
        created.append(str(symbol_rule_file))

    if json_mode:
        verdict = f"created {len(created)} example rule(s)" if created else "rule files already exist"
        click.echo(to_json(json_envelope(
            "rules",
            summary={"verdict": verdict, "passed": 0, "failed": 0,
                     "warnings": 0, "total": 0},
            created=created,
        )))
    else:
        if created:
            click.echo(f"Created {len(created)} example rule file(s):")
            for c in created:
                click.echo(f"  {c}")
            click.echo()
            click.echo("Edit these files, then run 'roam rules' to evaluate.")
        else:
            click.echo("Example rule files already exist.")
            click.echo(f"Edit them in {rules_dir}/")
