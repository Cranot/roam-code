"""Measure compiler savings only when episode evidence is admissible.

Output formats: text (default) and ``--json``. SARIF is deliberately NOT
emitted because savings is an aggregate evidence report over local episode
ledgers, without file-located findings or source coordinates.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.savings import (
    EVENT_FIELD_DEFINITIONS,
    SavingsLedgerSafetyError,
    aggregate_savings_result,
    analyze_ledger,
)


@click.command(name="savings")
@click.option(
    "--root",
    default=".",
    show_default=True,
    help="Project root containing .roam/episodes.jsonl and .roam/compile-runs.jsonl.",
)
@click.option("--schema", is_flag=True, help="Print the episode-event field contract and exit.")
@click.option(
    "--aggregate",
    is_flag=True,
    help="Emit the aggregate-only SavingsAggregate contract for platform consumers.",
)
@click.pass_context
@roam_capability(
    name="savings",
    category="planning",
    summary="Gate compiler-savings claims on joined prompt, compile, and terminal episode evidence.",
    inputs=("--root", "--aggregate"),
    outputs=("summary_envelope", "coverage", "repeat_candidates", "savings_aggregate"),
    examples=(
        "roam savings",
        "roam --json savings --root /path/to/project",
        "roam --json savings --aggregate",
    ),
    tags=("planning", "telemetry", "compiler", "episodes", "savings"),
    ai_safe=True,
    requires_index=False,
    mcp_expose=False,
    mcp_preset=(),
    side_effect=True,
    stale_sensitive=False,
)
def savings(ctx: click.Context, root: str, schema: bool, aggregate: bool) -> None:
    """Materialize the local episode ledger and report admissible savings evidence."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    if schema and aggregate:
        raise click.UsageError("--schema and --aggregate are mutually exclusive")
    if schema:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "savings",
                        budget=token_budget,
                        event_schema={"fields": EVENT_FIELD_DEFINITIONS},
                    )
                )
            )
        else:
            click.echo("episodes.jsonl event schema:")
            for field, meaning in EVENT_FIELD_DEFINITIONS.items():
                click.echo(f"  {field:<18s} {meaning}")
        return

    try:
        result = analyze_ledger(root)
    except (OSError, SavingsLedgerSafetyError, TimeoutError, sqlite3.Error) as exc:
        verdict = "Savings ledger unavailable because materialization stopped safely"
        failure_summary = {
            "verdict": verdict,
            "state": "materialization_failed",
            "partial_success": True,
            "measurement_admissible": False,
            "policy_admissible": False,
        }
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "savings",
                        budget=token_budget,
                        summary=failure_summary,
                        status="error",
                        isError=True,
                        error_code="RUN_FAILED",
                        error=str(exc),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"reason: {exc}")
        ctx.exit(1)
        return
    if aggregate:
        aggregate_result = aggregate_savings_result(result)
        aggregate_summary = aggregate_result["summary"]
        if json_mode:
            envelope = json_envelope(
                "savings",
                budget=token_budget,
                summary=aggregate_summary,
                **{key: value for key, value in aggregate_result.items() if key != "summary"},
            )
            # SavingsAggregate is a machine-to-machine privacy boundary. The
            # formatter's generic agent helper is useful for rich reports but
            # is outside this closed producer contract.
            envelope.pop("agent_contract", None)
            click.echo(to_json(envelope))
            return

        counts = aggregate_result["opportunity_counts"]
        intervention_state = aggregate_result["intervention_state"]
        click.echo(f"VERDICT: {aggregate_summary['verdict']}")
        click.echo(f"state:                         {aggregate_summary['state']}")
        click.echo(f"repeated live candidates:      {counts['repeated_live_candidates']}")
        click.echo(f"historical pattern candidates: {counts['historical_pattern_candidates']}")
        click.echo(f"ranked work opportunities:     {counts['ranked_work_opportunities']}")
        click.echo(f"intervention mappings:         {counts['intervention_mappings']}")
        click.echo(f"intervention assignments:      {intervention_state['assignments']}")
        click.echo("privacy:                       aggregate_only")
        return

    summary = result.pop("summary")
    coverage = result.get("coverage") or {}
    next_commands = ["roam hooks claude --write", "roam savings"]
    if summary["measurement_admissible"]:
        next_commands = ["roam compile-stats --by-procedure", "roam savings"]

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "savings",
                    budget=token_budget,
                    summary=summary,
                    agent_contract={
                        "facts": [
                            f"{coverage.get('prompt_starts', 0)} prompt-start events",
                            f"{coverage.get('terminal_outcomes', 0)} terminal outcomes",
                            f"{coverage.get('fully_joined_episodes', 0)} fully joined episodes",
                            f"{len(result.get('historical_candidates', []))} historical pattern candidates",
                            f"{len((result.get('procedure_atlas') or {}).get('opportunities', []))} ranked work opportunities",
                            f"{sum(row.get('declaration_state') == 'unclaimed' for row in (result.get('procedure_atlas') or {}).get('intervention_mappings', []))} unclaimed intervention families",
                            f"{result['sensor_canaries']['passed']} sensor canaries passed",
                        ],
                        "next_commands": next_commands,
                        "risks": (
                            []
                            if summary["policy_admissible"]
                            else ["Savings claims remain gated by incomplete joined episodes"]
                        ),
                        "confidence": None,
                    },
                    **result,
                )
            )
        )
        return

    click.echo(f"VERDICT: {summary['verdict']}")
    click.echo(f"state:                         {summary['state']}")
    click.echo(f"prompt starts:                 {coverage.get('prompt_starts', 0)}")
    click.echo(f"eligible prompt starts:        {coverage.get('eligible_prompt_starts', 0)}")
    click.echo(f"terminal coverage:             {coverage.get('terminal_coverage_pct')}%")
    click.echo(f"compile + terminal join:       {coverage.get('episode_join_coverage_pct')}%")
    click.echo(f"compile identity coverage:     {coverage.get('compile_identity_coverage_pct')}%")
    click.echo(f"keyed repeat identity:         {coverage.get('repeat_identity_coverage_pct')}%")
    click.echo(f"execution-health coverage:     {coverage.get('health_context_coverage_pct')}%")
    click.echo(
        f"sensor canaries:               {result['sensor_canaries']['passed']}/"
        f"{result['sensor_canaries']['total']} {result['sensor_canaries']['state']}"
    )
    if result["repeat_candidates"]:
        click.echo("")
        click.echo("Repeated candidates (observed, not promoted):")
        for candidate in result["repeat_candidates"][:10]:
            click.echo(
                f"  {candidate['episodes']:>3} episodes  {candidate['procedure']:<22s} "
                f"{candidate['task_fingerprint'][:20]}"
            )
    if result["historical_candidates"]:
        click.echo("")
        click.echo("Historical patterns (candidate-only):")
        for candidate in result["historical_candidates"][:10]:
            click.echo(f"  {candidate['episodes']:>4} episodes  {candidate['kind']:<15s} {candidate['pattern'][:100]}")
    opportunities = (result.get("procedure_atlas") or {}).get("opportunities") or []
    if opportunities:
        click.echo("")
        click.echo("Cross-project work opportunities (historical, non-causal):")
        for opportunity in opportunities[:10]:
            click.echo(
                f"  {opportunity['opportunity_score']:>5.1f}  "
                f"{opportunity['episodes']:>5} episodes  "
                f"{opportunity['projects']:>4} projects  "
                f"{opportunity['title']}"
            )
    failure_signatures = (result.get("procedure_atlas") or {}).get("failure_signatures") or []
    if failure_signatures:
        click.echo("")
        click.echo("Sanitized failure signatures (historical, non-causal):")
        for signature in failure_signatures[:10]:
            click.echo(
                f"  {signature['failures']:>5}/{signature['attempts']:<5} failed  "
                f"{signature['projects']:>4} projects  "
                f"{signature['template'][:100]}"
            )
    recovery_targets = (result.get("procedure_atlas") or {}).get("recovery_targets") or []
    if recovery_targets:
        click.echo("")
        click.echo("Closed failure-recovery targets (raw results discarded):")
        for target in recovery_targets[:10]:
            click.echo(
                f"  {target['failures']:>5} failures  {target['projects']:>4} projects  {target['failure_class']}"
            )
    intervention_mappings = (result.get("procedure_atlas") or {}).get("intervention_mappings") or []
    if intervention_mappings:
        click.echo("")
        click.echo("Intervention hypotheses (all prospectively unmeasured):")
        for gap in intervention_mappings[:10]:
            click.echo(
                f"  {gap['research_priority_score']:>5.1f}  "
                f"{gap['declaration_state']:<18}  "
                f"{gap['projects']:>4} projects  "
                f"{gap['title']}"
            )
    click.echo("")
    click.echo("Run `roam hooks claude --write` to refresh the episode-aware hooks.")
