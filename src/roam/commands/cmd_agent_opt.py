"""``roam agent-opt`` — the envelope-contract super-optimizer (family P1).

Scans roam's OWN agent-facing surface (MCP tool descriptions + ``roam --json``
envelopes) for violations of the agi-in-md LAWs / systemic anti-patterns
(AGENTS.md § Quality discipline) and emits "solving TASK_X with weak shape Y ->
use shape Z" findings. Diagnosis + direction — the super-optimizer shape.

Displaces: hand-reading 229 MCP descriptions for declarative voice, or eyeballing
every command's verdict/next_commands for LAW 6 / CONSTRAINT 12 adherence during
review. The detection logic lives in ``roam.agent_opt`` (the reusable
envelope-signal substrate later families reuse).

SARIF is deliberately NOT emitted: agent-opt findings target roam's OWN
envelope / tool-description surface (tool names + command labels), not
source-code coordinates, so there are no ``locations[]`` to project into a
SARIF result. Output formats are text (default) and ``--json``.
"""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import json_envelope, to_json

# Heuristic-basis findings are dropped under --profile strict.
_STRICT_DROPS_BASIS = {"heuristic"}


@roam_capability(
    name="agent-opt",
    category="health",
    summary="Detect weak agent-contract shape in roam's tool descriptions and envelopes and recommend the stronger shape",
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command()
@click.option(
    "--list-tasks", "list_tasks", is_flag=True, default=False, help="Print agent-opt task ids + best ways, then exit."
)
@click.option(
    "--list-detectors",
    "list_detectors",
    is_flag=True,
    default=False,
    help="Print registered agent-opt detectors with metadata, then exit.",
)
@click.option(
    "--only",
    "only_tasks",
    multiple=True,
    default=(),
    help="Restrict the scan to these task ids (repeatable). See `roam agent-opt --list-tasks`.",
)
@click.option(
    "--exclude",
    "exclude_tasks",
    multiple=True,
    default=(),
    help="Skip these task ids (repeatable). Ignored if --only names the same task.",
)
@click.option(
    "--scope",
    "scope",
    default="full",
    help="Tool-description scope: 'core' (core preset) or 'full' (all tools, default). Unknown values fall back to full.",
)
@click.option(
    "--confidence",
    "confidence_floor",
    default=None,
    type=click.Choice(["high", "medium", "low"], case_sensitive=False),
    help="Minimum confidence floor (high > medium > low). Default shows all.",
)
@click.option(
    "--profile",
    "profile",
    default="balanced",
    type=click.Choice(["balanced", "strict", "aggressive"], case_sensitive=False),
    help="strict drops heuristic-tier findings (e.g. tool-description-declarative); aggressive == balanced today.",
)
@click.option(
    "--top", "--limit", "-n", "limit", default=50, type=int, help="Max findings to show (alias --limit/-n; 0 = all)."
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Mirror findings into the findings registry so `roam findings list` sees them.",
)
@click.pass_context
def agent_opt_cmd(
    ctx,
    list_tasks,
    list_detectors,
    only_tasks,
    exclude_tasks,
    scope,
    confidence_floor,
    profile,
    limit,
    persist,
):
    """Optimize roam's agent-contract surface: find weak envelope/description shape."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    from roam.agent_opt import (
        list_agent_opt_detectors,
        list_agent_opt_tasks,
        run_agent_opt,
    )

    # ---- --list-tasks ----
    if list_tasks:
        tasks = list_agent_opt_tasks()
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "agent-opt",
                        summary={
                            "verdict": f"{len(tasks)} agent-contract tasks registered",
                            "task_count": len(tasks),
                        },
                        tasks=tasks,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(tasks)} agent-contract tasks registered")
        for t in tasks:
            click.echo(f"  {t['task_id']:<30} {t['name']} -> best: {t['best_name']}")
        return

    # ---- --list-detectors ----
    if list_detectors:
        dets = list_agent_opt_detectors()
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "agent-opt",
                        summary={
                            "verdict": f"{len(dets)} agent-opt detectors registered",
                            "detector_count": len(dets),
                        },
                        detectors=dets,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(dets)} agent-opt detectors registered")
        for d in dets:
            click.echo(f"  {d['name']:<40} task={d['task_id']} basis={d['confidence_basis']} v{d['version']}")
        return

    # ---- run the optimizer ----
    findings, meta = run_agent_opt(scope=scope, only=only_tasks, exclude=exclude_tasks)

    # Profile filter (strict drops heuristic-tier).
    if profile.lower() == "strict":
        findings = [f for f in findings if f.get("confidence_basis") not in _STRICT_DROPS_BASIS]
    # Confidence floor filter.
    if confidence_floor:
        floor = confidence_level_rank(confidence_floor)
        findings = [f for f in findings if confidence_level_rank(f.get("confidence"), fallback=1) >= floor]

    # Stable, useful ordering: highest confidence first, then by task.
    findings.sort(
        key=lambda f: (
            -confidence_level_rank(f.get("confidence"), fallback=1),
            f.get("task_id", ""),
            str(f.get("subject", "")),
        )
    )

    total = len(findings)
    by_task = dict(Counter(f["task_id"] for f in findings))
    by_confidence = dict(Counter(f.get("confidence", "low") for f in findings))
    top_task = max(by_task, key=by_task.get) if by_task else None
    high_count = by_confidence.get("high", 0)

    # ---- A4 persistence (explicit per-family wiring) ----
    persisted = 0
    persist_error = None
    if persist and findings:
        try:
            from roam.agent_opt import build_finding_records
            from roam.db.connection import open_db
            from roam.db.findings import emit_finding

            records = build_finding_records(findings)
            with open_db() as conn:
                for rec in records:
                    emit_finding(conn, rec)
                    persisted += 1
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — disclose, never silent (Pattern 2)
            persist_error = f"{type(exc).__name__}: {exc}"
            meta["partial_success"] = True

    # ---- bounded output (Pattern 6): cap shown findings ----
    truncated = False
    findings_out = findings
    if limit and limit > 0 and total > limit:
        findings_out = findings[:limit]
        truncated = True

    n_tools = meta.get("sources", {}).get("tool_descriptions_scanned", 0)
    n_env = meta.get("sources", {}).get("envelopes_scanned", 0)

    # ---- verdict (LAW 6: works standalone) ----
    if total == 0:
        verdict = f"0 agent-contract improvements found — {n_tools} tool descriptions and {n_env} envelopes scanned"
    else:
        plural = "s" if total != 1 else ""
        top_frag = f", top: {top_task}" if top_task else ""
        verdict = f"{total} agent-contract improvement{plural} found ({high_count} high-confidence{top_frag})"
    partial = bool(meta.get("partial_success"))
    if partial:
        bits = []
        if meta.get("failed_detectors"):
            bits.append(f"{len(meta['failed_detectors'])} detector(s) failed")
        if meta.get("sources", {}).get("commands_unavailable"):
            bits.append(f"{len(meta['sources']['commands_unavailable'])} command(s) unavailable")
        if persist_error:
            bits.append("persist failed")
        if bits:
            verdict = f"WARNING (degraded: {'; '.join(bits)}) — {verdict}"

    # ---- agent_contract (LAW 4 anchored, flat, imperative; CONSTRAINT 12 next_commands) ----
    facts = [
        f"{total} agent-contract findings",
        f"{high_count} high-confidence findings",
        f"{n_tools} tool descriptions scanned",
    ]
    if n_env:
        facts.append(f"{n_env} command envelopes scanned")
    facts.append("Run roam agent-opt --list-tasks to see every check")
    next_commands = ["roam agent-opt --list-tasks"]
    if top_task:
        next_commands.append(f"roam agent-opt --only {top_task}")
    next_commands.append("roam agent-opt --persist")

    if json_mode:
        summary: dict = {
            "verdict": verdict,
            "total": total,
            "by_task": by_task,
            "by_confidence": by_confidence,
            "top_task": top_task,
            "scope": scope,
            "profile": profile,
            "truncated": truncated,
            "detectors_executed": meta.get("detectors_executed", 0),
            "detectors_failed": meta.get("detectors_failed", 0),
            "failed_detectors": meta.get("failed_detectors", []),
            "tool_descriptions_scanned": n_tools,
            "envelopes_scanned": n_env,
            "commands_unavailable": meta.get("sources", {}).get("commands_unavailable", []),
            "partial_success": partial,
        }
        if persist:
            summary["persisted"] = persisted
        if persist_error:
            summary["persist_error"] = persist_error
        if meta.get("only_unknown"):
            summary["only_unknown"] = meta["only_unknown"]
        if meta.get("exclude_unknown"):
            summary["exclude_unknown"] = meta["exclude_unknown"]
        click.echo(
            to_json(
                json_envelope(
                    "agent-opt",
                    summary=summary,
                    findings=findings_out,
                    agent_contract={"facts": facts, "next_commands": next_commands},
                )
            )
        )
        return

    # ---- text output ----
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Scanned: {n_tools} tool descriptions, {n_env} command envelopes (scope={scope}, profile={profile})")
    if meta.get("only_unknown"):
        click.echo(f"WARNING: unknown --only tasks: {', '.join(meta['only_unknown'])}")
    if persist:
        click.echo(
            f"Persisted {persisted} finding(s) to the findings registry"
            + (f" (error: {persist_error})" if persist_error else "")
        )
    for f in findings_out:
        click.echo(
            f"  [{f.get('confidence', '?'):>6}] {f['task_id']:<30} {f.get('subject', '?')}\n"
            f"          {f.get('reason', '')}\n"
            f"          -> {f.get('suggestion', '')}"
        )
    if truncated:
        click.echo(f"  ... {total - len(findings_out)} more (raise --limit to see all)")
    if total == 0:
        click.echo("Run `roam agent-opt --list-tasks` to see every check.")
