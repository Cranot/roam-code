"""Plugin DSL for custom governance rules.

Users define architectural rules as YAML files in ``.roam/rules/``.
Roam evaluates them against the indexed graph and reports violations.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

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

_EXAMPLE_AST_RULE = """\
# Rule: Forbid dynamic eval-style execution
name: "No eval-style execution"
description: "Disallow eval(...) calls in Python source"
severity: error
type: ast_match

match:
  ast: "eval($EXPR)"
  language: python
  file_glob: "**/*.py"
  max_matches: 50

exempt:
  files: ["**/tests/**"]
"""

_EXAMPLE_DATAFLOW_RULE = """\
# Rule: Detect intra-procedural dataflow issues
name: "Basic dataflow hygiene"
description: "Find dead assignments, unused params, and source-to-sink flows in functions"
severity: warning
type: dataflow_match

match:
  patterns: [dead_assignment, unused_param, source_to_sink]
  file_glob: "**/*.py"
  max_matches: 100
  sources: ["input(", "request.args"]
  sinks: ["eval(", "exec("]

exempt:
  files: ["**/tests/**"]
"""

# R18: graph-aware clauses — these only fire when the index has the
# corresponding tables populated (`roam clones --persist` for clones_with,
# `roam index` for the rest).
_EXAMPLE_GRAPH_CLAUSE_RULE = """\
# Rule: graph-aware architectural constraints (R18)
# Demonstrates the four new clause types: reachable_from, imports_from,
# clones_with, tested_by. Each rule may carry a `must` or `must_not` block
# (or both). Combine with `when:` to scope the rule.
name: "Handlers must reach the canonical DB"
description: "Every src/handlers/**.py file must be able to reach src/db/__init__.py"
severity: error
when:
  pattern: "src/handlers/**.py"
must_not:
  imports_from: "src/legacy"
"""


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="rules",
    category="reports",
    summary="Evaluate custom governance rules defined in .roam/rules/",
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
@click.command("rules")
@click.option("--init", "do_init", is_flag=True, help="Generate example rule files in .roam/rules/.")
@click.option("--ci", "ci_mode", is_flag=True, help="Exit code 1 on error-severity violations.")
@click.option("--rules-dir", "rules_dir_opt", default=None, help="Custom rules directory path.")
@click.option(
    "--top",
    "--limit",
    "top_n",
    default=10,
    type=int,
    show_default=True,
    help="Cap on violations shown per failing rule (alias: --limit). Pass 0 for unlimited.",
)
@click.option(
    "--depth",
    "depth",
    default=3,
    type=int,
    show_default=True,
    help=(
        "BFS depth for graph-aware clauses (reachable_from, tested_by). "
        "Default 3 — mirrors `roam impact` to keep evaluation fast on large repos."
    ),
)
@click.option(
    "--max-nodes",
    "max_nodes",
    default=100,
    type=int,
    show_default=True,
    help="Max visited nodes per graph-aware clause BFS (W3.4 guardrail).",
)
@click.pass_context
def rules(ctx, do_init, ci_mode, rules_dir_opt, top_n, depth, max_nodes):
    """Evaluate custom governance rules defined in .roam/rules/.

    Unlike ``check-rules`` (which evaluates pre-packaged structural rules),
    this command evaluates user-authored YAML governance rules with custom
    constraints.

    Rules are YAML files that define architectural constraints. Five rule
    types are supported: path_match (edges between from/to patterns),
    symbol_match (symbols matching criteria with optional require),
    ast_match (AST structural patterns with `$METAVAR` captures),
    dataflow_match (basic intra-procedural dataflow heuristics), and
    graph_clause (R18 — must/must_not clauses backed by the indexed graph:
    reachable_from, imports_from, clones_with, tested_by).

    Use --init to create example rule files. Use --ci for CI gates
    (exit code 1 on error-severity violations). Use --depth to control
    BFS depth for graph-aware clauses (default 3).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    root = find_project_root()

    # --init: create example rules
    if do_init:
        _handle_init(root, json_mode, rules_dir_opt)
        return

    ensure_index()

    # Determine rules directory. Project-local ``.roam/rules`` wins;
    # otherwise the empty state mentions the bundled community corpus
    # so first-time users can opt in.
    if rules_dir_opt:
        rules_dir = Path(rules_dir_opt)
    else:
        rules_dir = root / ".roam" / "rules"

    # Graceful handling when no rules directory exists
    bundled_count = 0
    if not rules_dir.is_dir():
        bundled = root / "rules" / "community"
        if bundled.is_dir():
            bundled_count = sum(1 for _ in bundled.rglob("*.yaml")) + sum(1 for _ in bundled.rglob("*.yml"))
        verdict = "no rules directory found"
        if sarif_mode:
            from roam.output.sarif import rules_to_sarif, write_sarif

            sarif = rules_to_sarif([])
            click.echo(write_sarif(sarif))
            return
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "rules",
                        summary={
                            "verdict": verdict,
                            "passed": 0,
                            "failed": 0,
                            "warnings": 0,
                            "total": 0,
                        },
                        results=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo()
            click.echo(f"Create rules in {rules_dir} or run 'roam rules --init'.")
            if bundled_count:
                click.echo(f"\n{bundled_count} community rule(s) bundled at rules/community/.")
                click.echo(
                    "Evaluate them with `roam rules --rules-dir rules/community`"
                    " or copy a subset into .roam/rules to keep eval fast."
                )
        return

    from roam.rules.engine import evaluate_all

    with open_db(readonly=True) as conn:
        results = evaluate_all(rules_dir, conn, max_depth=depth, max_nodes=max_nodes)

    # Tally results
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_errors = sum(1 for r in results if not r["passed"] and r["severity"] == "error")
    failed_warnings = sum(1 for r in results if not r["passed"] and r["severity"] == "warning")
    failed_infos = sum(1 for r in results if not r["passed"] and r["severity"] == "info")
    failed = total - passed
    partial_success = any(r.get("partial_success") for r in results)

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

    # R18: derive imperative `next_commands` from graph-clause violations so
    # an agent that consumes only the verdict can keep going. LAW 2: every
    # entry is a copy-paste-executable `roam <cmd>` string.
    next_commands: list[str] = []
    seen_cmds: set[str] = set()
    for r in results:
        if r.get("passed"):
            continue
        for v in r.get("violations", []):
            sym = (v.get("symbol") or "").strip()
            file_path = (v.get("file") or "").strip()
            clause = (v.get("clause") or "").strip()
            cmd: str | None = None
            if clause == "reachable_from" and sym:
                cmd = f"roam impact {sym}"
            elif clause == "imports_from" and file_path:
                cmd = f"roam file {file_path}"
            elif clause == "clones_with" and sym:
                cmd = f"roam clones --persist && roam impact {sym}"
            elif clause == "tested_by" and sym:
                cmd = f"roam coverage-gaps --symbol {sym}"
            if cmd and cmd not in seen_cmds:
                seen_cmds.add(cmd)
                next_commands.append(cmd)
            if len(next_commands) >= 10:
                break
        if len(next_commands) >= 10:
            break

    # --- SARIF output ---
    if sarif_mode:
        from roam.output.sarif import rules_to_sarif, write_sarif

        sarif = rules_to_sarif(results)
        click.echo(write_sarif(sarif))
        if ci_mode and failed_errors > 0:
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)
        return

    # --- JSON output ---
    if json_mode:
        summary_dict = {
            "verdict": verdict,
            "passed": passed,
            "failed": failed,
            "warnings": failed_warnings,
            "total": total,
        }
        if partial_success:
            summary_dict["partial_success"] = True
        click.echo(
            to_json(
                json_envelope(
                    "rules",
                    summary=summary_dict,
                    results=results,
                    next_commands=next_commands,
                )
            )
        )
        if ci_mode and failed_errors > 0:
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)
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
            limit = count if top_n <= 0 else top_n
            for v in violations[:limit]:
                sym = v.get("symbol", "")
                fpath = v.get("file", "")
                line = v.get("line")
                reason = v.get("reason", "")
                loc = f"{fpath}:{line}" if line else fpath
                click.echo(f"    - {sym} at {loc}")
                if reason:
                    click.echo(f"      {reason}")
            if top_n > 0 and count > top_n:
                click.echo(f"    (+{count - top_n} more — pass `--top N` or `--top 0` to see them)")

    if next_commands:
        click.echo()
        click.echo("Next commands:")
        for nc in next_commands:
            click.echo(f"  - {nc}")

    if partial_success:
        click.echo()
        click.echo(
            "NOTE: at least one rule could not fully evaluate (graph clause "
            "had unresolved targets or missing clone index). Verdict reflects "
            "rules that DID run; consult evidence for partial cases."
        )

    if ci_mode and failed_errors > 0:
        from roam.exit_codes import EXIT_GATE_FAILURE

        ctx.exit(EXIT_GATE_FAILURE)


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
    ast_rule_file = rules_dir / "no_eval_style_execution.yaml"
    dataflow_rule_file = rules_dir / "basic_dataflow_hygiene.yaml"
    graph_clause_rule_file = rules_dir / "graph_clauses_example.yaml"

    created: list[str] = []

    if not path_rule_file.exists():
        path_rule_file.write_text(_EXAMPLE_PATH_RULE, encoding="utf-8")
        created.append(str(path_rule_file))

    if not symbol_rule_file.exists():
        symbol_rule_file.write_text(_EXAMPLE_SYMBOL_RULE, encoding="utf-8")
        created.append(str(symbol_rule_file))

    if not ast_rule_file.exists():
        ast_rule_file.write_text(_EXAMPLE_AST_RULE, encoding="utf-8")
        created.append(str(ast_rule_file))

    if not dataflow_rule_file.exists():
        dataflow_rule_file.write_text(_EXAMPLE_DATAFLOW_RULE, encoding="utf-8")
        created.append(str(dataflow_rule_file))

    if not graph_clause_rule_file.exists():
        graph_clause_rule_file.write_text(_EXAMPLE_GRAPH_CLAUSE_RULE, encoding="utf-8")
        created.append(str(graph_clause_rule_file))

    if json_mode:
        verdict = f"created {len(created)} example rule(s)" if created else "rule files already exist"
        click.echo(
            to_json(
                json_envelope(
                    "rules",
                    summary={
                        "verdict": verdict,
                        "passed": 0,
                        "failed": 0,
                        "warnings": 0,
                        "total": 0,
                    },
                    created=created,
                )
            )
        )
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
