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
from roam.exit_codes import EXIT_GATE_FAILURE, gate_should_fail
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
# W1030-followup-F: config-state disclosure helpers
# ---------------------------------------------------------------------------

# Closed-enum set of LoadStatus values that flip ``partial_success=True``
# even when no warning fired (e.g. empty stub doesn't warn, but
# ``schema_invalid`` does -- treat them uniformly at the partial_success
# level). Mirrors cmd_alerts + cmd_budget + cmd_health + cmd_check_rules +
# cmd_fitness vocabularies.
_DEGRADED_LOAD_STATUSES: frozenset[str] = frozenset({"parse_error", "wrong_root_type", "read_error", "schema_invalid"})


def _build_config_state_facts(config_state: str) -> list[str]:
    """W1030-followup-F: build ``agent_contract.facts`` for the ``config_state``.

    LAW 4 anchored on the concrete-noun terminal ``"rules"`` (the
    governance rules ``cmd_rules`` evaluates). Mirrors cmd_check_rules
    (anchors on ``"rules"``), cmd_fitness (anchors on ``"rules"``),
    cmd_alerts (anchors on ``"defaults"``), and cmd_health (anchors on
    ``"gates"``) by using the command's own subject-noun.

    Returns an empty list when ``config_state == "ok"`` -- no need to
    disclose the happy path.
    """
    if config_state == "missing":
        return ["no .roam/rules/ directory configured; using baseline rules"]
    if config_state == "empty_file":
        return ["empty .roam/rules/ stub on disk; using baseline rules"]
    if config_state == "empty_yaml":
        return ["comment-only .roam/rules/ files on disk; using baseline rules"]
    if config_state in _DEGRADED_LOAD_STATUSES:
        return [f"rules config rejected ({config_state}); using baseline rules"]
    return []


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
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    help=(
        "Exit non-zero on error-severity violations, and on error-severity "
        "rules that could not be evaluated at all. Warning/info rules that "
        "cannot be evaluated are disclosed, never gated."
    ),
)
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
        # Absent-input disclosure + gate. No rules were evaluated, so every
        # channel must say so: the JSON path already carried
        # config_state="missing" and the text path said "no rules directory
        # found", but the SARIF path emitted a bare zero-result document --
        # a Code-Scanning consumer read a scan that never ran as a clean
        # one. And when the caller EXPLICITLY pointed --rules-dir at a
        # directory that does not exist, a --ci gate cannot certify clean
        # on a measurement that never happened (gate_should_fail semantics:
        # an absent measurement is UNKNOWN, never CLEAN). The implicit
        # default arm (.roam/rules simply not configured) keeps exit 0 --
        # that is "rules are not set up", reporting rather than gating,
        # and is inventoried in test_gate_channel_exit_parity Law 3.
        missing_note = f"rules directory not found: {rules_dir} -- no rules were evaluated"
        gate_failed = gate_should_fail(
            ci_mode and rules_dir_opt is not None,
            findings=0,
            scan_incomplete=True,
        )
        if sarif_mode:
            from roam.output.sarif import rules_to_sarif, write_sarif

            sarif = rules_to_sarif([], emit_runtime_notifications=True, warnings_out=[missing_note])
            click.echo(write_sarif(sarif))
            if gate_failed:
                ctx.exit(EXIT_GATE_FAILURE)
            return
        if json_mode:
            # W1030-followup-F: missing rules directory is the "missing"
            # state; surface it on summary.config_state with the
            # state-disclosure agent_contract.facts.
            missing_summary = {
                "verdict": verdict,
                "passed": 0,
                "failed": 0,
                "warnings": 0,
                "total": 0,
                "config_state": "missing",
            }
            if gate_failed:
                missing_summary["scan_incomplete"] = True
            missing_facts = _build_config_state_facts("missing")
            click.echo(
                to_json(
                    json_envelope(
                        "rules",
                        summary=missing_summary,
                        results=[],
                        agent_contract={"facts": missing_facts} if missing_facts else None,
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
            if gate_failed:
                click.echo(f"REFUSED: {missing_note}.")
        if gate_failed:
            ctx.exit(EXIT_GATE_FAILURE)
        return

    from roam.rules.engine import evaluate_all_with_status

    # W1036: plumb the per-file YAML-loader warnings up to the envelope so
    # malformed rule files surface as actionable warnings instead of silent
    # `_error` placeholders.
    # W1030-followup-F: also surface the directory-level LoadStatus rollup
    # so the envelope can disambiguate missing / empty / parse_error / ok.
    _rules_warnings_out: list[str] = []
    with open_db(readonly=True) as conn:
        results, config_state = evaluate_all_with_status(
            rules_dir,
            conn,
            max_depth=depth,
            max_nodes=max_nodes,
            warnings_out=_rules_warnings_out,
        )

    # Tally results
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_errors = sum(1 for r in results if not r["passed"] and r["severity"] == "error")
    failed_warnings = sum(1 for r in results if not r["passed"] and r["severity"] == "warning")
    failed_infos = sum(1 for r in results if not r["passed"] and r["severity"] == "info")
    failed = total - passed
    partial_success = any(r.get("partial_success") for r in results)
    # A graph clause that could not resolve returns (False, {...}) from
    # check_clones_with / friends, so `fired = must_not and matches` is False
    # and no violation is recorded -- and `_graph_clause_result` then emits
    # passed=True + partial_success=True. That rule did not PASS; it did not
    # RUN. `--ci` is documented as error-severity only, so only an
    # error-severity rule that could not be evaluated blocks: an advisory
    # graph-clause rule that legitimately cannot resolve (every `clones_with`
    # rule before `roam clones --persist` has run -- documented as normal by
    # the R18 comment and the `rules --init` template) must disclose, not gate.
    #
    # W1531 -- DELIBERATE, NARROW CARVE-OUT, and an open decision for the
    # owner. ``traversal_truncated`` (the BFS hit --depth / --max-nodes and
    # answered NEGATIVE, so it measured nothing) now flips partial_success
    # and is counted in ``unevaluated_total`` -- it changes the verdict from
    # "all N rules passed" to "N of M rules passed, K could not be
    # evaluated", which is the false-clean this fix exists to remove. It is
    # NOT counted in ``unevaluated_errors``, so the ``--ci`` exit code is
    # unchanged. Reason: DEFAULT_DEPTH is 3, so every ``must_not:
    # reachable_from`` rule in every repo with a chain longer than 3 hops --
    # the norm in layered code -- would flip from exit 0 to exit 5 on
    # upgrade. Whether UNANALYZABLE-by-truncation should refuse (which is
    # what this file's own doctrine at exit_codes.py:139-146 argues) is a
    # release decision with a large blast radius, not something to change as
    # a side effect of making the report honest. Removing this filter is the
    # entire diff if that decision goes the other way.
    _NON_GATING_INCOMPLETE_REASONS = {"traversal_truncated"}

    def _blocks_gate(r: dict) -> bool:
        reasons = set(r.get("incomplete_reasons") or [])
        return bool(reasons - _NON_GATING_INCOMPLETE_REASONS) if reasons else True

    unevaluated_errors = sum(
        1
        for r in results
        if r.get("partial_success") and r.get("severity") == "error" and r.get("passed") and _blocks_gate(r)
    )
    unevaluated_total = sum(1 for r in results if r.get("partial_success") and r.get("passed"))
    passed_fully = passed - unevaluated_total

    if failed == 0 and unevaluated_total > 0:
        # Never claim a total the run did not measure.
        verdict = f"{passed_fully} of {total} rules passed, {unevaluated_total} could not be evaluated"
    elif failed == 0:
        verdict = f"all {total} rules passed" if total > 0 else "no rules found"
    else:
        parts = []
        if failed_errors > 0:
            parts.append(f"{failed_errors} error(s)")
        if failed_warnings > 0:
            parts.append(f"{failed_warnings} warning(s)")
        if failed_infos > 0:
            parts.append(f"{failed_infos} info")
        if unevaluated_total > 0:
            parts.append(f"{unevaluated_total} could not be evaluated")
        verdict = f"{passed_fully} of {total} rules passed, {', '.join(parts)}"

    # One decision, read by all three channels -- gate_should_fail's stated
    # reason for existing. UNANALYZABLE shares exit 5 with a measured
    # VIOLATION; sharing exit 0 with a measured CLEAN is the defect.
    gate_failed = gate_should_fail(ci_mode, findings=failed_errors, scan_incomplete=unevaluated_errors > 0)
    incomplete_reason = (
        f"{unevaluated_errors} error-severity rule(s) could not be evaluated "
        "(graph clause had unresolved targets or missing clone index)"
        if unevaluated_errors > 0
        else None
    )

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

        # W1114: project loader warnings onto the SARIF
        # run.invocations[].toolExecutionNotifications[] array so a CI
        # consumer doesn't see a green SARIF that silently dropped half
        # its rules (matches the W1036 envelope plumbing on the JSON
        # path above; mirrors W1060 complexity SARIF). Hash invariant:
        # empty/missing warnings keep the SARIF output byte-identical to
        # pre-W1114 because emit_runtime_notifications stays False.
        # Feed the partial disclosure into the channel that already exists for
        # W1114 loader warnings rather than inventing a second mechanism.
        _sarif_notes = list(_rules_warnings_out)
        if incomplete_reason:
            _sarif_notes.append(incomplete_reason)
        sarif = rules_to_sarif(
            results,
            emit_runtime_notifications=bool(_sarif_notes),
            warnings_out=_sarif_notes,
        )
        click.echo(write_sarif(sarif))
        if gate_failed:
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
            # W1030-followup-F: uniform config_state disclosure across the
            # W1030-followup cohort (alerts / budget / health / check-rules /
            # fitness / rules).
            "config_state": config_state,
        }
        if partial_success:
            summary_dict["partial_success"] = True
        if unevaluated_total > 0:
            summary_dict["unevaluated"] = unevaluated_total
            summary_dict["unevaluated_errors"] = unevaluated_errors
        # Narrow companion to the broad partial_success: scan_incomplete is the
        # subset that makes the GATE refuse, so a caller can tell "some
        # advisory rule could not resolve" from "the gate could not decide".
        summary_dict["scan_incomplete"] = unevaluated_errors > 0
        if _rules_warnings_out:
            # W1036: surface loader warnings (malformed files that were
            # skipped) so the agent doesn't see a green verdict that
            # silently dropped half its rules.
            summary_dict["warnings_out"] = list(_rules_warnings_out)
            summary_dict["partial_success"] = True
        # W1030-followup-F: degraded config_state flips partial_success
        # even when no warning fired (e.g. empty stub directory).
        if config_state in _DEGRADED_LOAD_STATUSES:
            summary_dict["partial_success"] = True
        # W1030-followup-F: agent_contract.facts disclose the config_state
        # so an agent reading only the contract sees the lineage of the
        # verdict (missing config -> baseline rules / degraded -> warning).
        state_facts = _build_config_state_facts(config_state)
        if incomplete_reason:
            state_facts = [*state_facts, incomplete_reason]
        envelope_kwargs: dict = {
            "summary": summary_dict,
            "results": results,
            "next_commands": next_commands,
        }
        if state_facts:
            envelope_kwargs["agent_contract"] = {"facts": state_facts}
        click.echo(
            to_json(
                json_envelope(
                    "rules",
                    **envelope_kwargs,
                )
            )
        )
        if gate_failed:
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
            # A rule whose graph clause could not resolve returns passed=True
            # with no violations. Labelling that [PASS] told the reader the
            # rule held; it did not run.
            if r.get("partial_success"):
                click.echo(f"  [UNEVAL] [{sev}] {name} (could not be evaluated)")
            else:
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
        # W1531: the old wording named only two causes and did not cover a
        # walk that ran out of budget, which is the most common one.
        click.echo(
            "NOTE: at least one rule could not fully evaluate (graph clause "
            "had unresolved targets, a missing clone index, or a traversal "
            "that hit --depth / --max-nodes before answering). Verdict "
            "reflects rules that DID run; consult evidence for partial cases."
        )
        if any("traversal_truncated" in (r.get("incomplete_reasons") or []) for r in results):
            click.echo(
                f"  A bounded walk that found nothing has NOT proven non-reachability. "
                f"Re-run with a larger --depth (current: {depth}) to search further."
            )
        if incomplete_reason:
            click.echo(f"REFUSED: {incomplete_reason}." if ci_mode else f"UNEVALUATED: {incomplete_reason}.")

    if _rules_warnings_out:
        click.echo()
        for w in _rules_warnings_out:
            click.echo(f"WARNING: {w}")

    if gate_failed:
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
