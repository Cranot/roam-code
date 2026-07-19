"""``roam observability-opt`` — the diagnosability super-optimizer (family P2).

Scans a repo's SOURCE for code that leaves a system hard to debug — raw debug
prints left in non-test source today, with string-only logs / missing error
context / traces-without-status to follow — and emits "solving TASK_X with weak
shape Y -> use shape Z" findings. Diagnosis + direction, the super-optimizer
shape.

Displaces: hand-grepping for ``print(`` / ``console.log`` / ``var_dump`` across
a polyglot tree, then deciding per-language whether each is a forgotten debug
line. The detection logic lives in ``roam.observability_opt`` (the reusable
per-language source-signal substrate later families reuse).

SARIF is deliberately NOT emitted in this first slice. observability-opt
findings DO carry source coordinates (``path:line``), so SARIF projection is
meaningful and the natural follow-up — but emission is deferred until the
family has more than one source-tier task; this slice ships text + ``--json``
only. (Contrast ``agent-opt``, whose findings target tool/command surfaces with
no source location at all and so will never emit SARIF.)
"""

from __future__ import annotations

from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import json_envelope, to_json

# Heuristic-basis findings are dropped under --profile strict.
_STRICT_DROPS_BASIS = {"heuristic"}


@roam_capability(
    name="observability-opt",
    category="health",
    summary="Detect code that leaves systems hard to debug (raw debug prints, ...) and recommend the structured-logging shape",
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option(
    "--list-tasks",
    "list_tasks",
    is_flag=True,
    default=False,
    help="Print observability-opt task ids + best ways, then exit.",
)
@click.option(
    "--list-detectors",
    "list_detectors",
    is_flag=True,
    default=False,
    help="Print registered observability-opt detectors with metadata, then exit.",
)
@click.option(
    "--only",
    "only_tasks",
    multiple=True,
    default=(),
    help="Restrict the scan to these task ids (repeatable). See `roam observability-opt --list-tasks`.",
)
@click.option(
    "--exclude",
    "exclude_tasks",
    multiple=True,
    default=(),
    help="Skip these task ids (repeatable). Ignored if --only names the same task.",
)
@click.option(
    "--language",
    "languages",
    multiple=True,
    default=(),
    help="Restrict the scan to these languages (repeatable, e.g. --language python). Default: all supported.",
)
@click.option(
    "--max-files",
    "max_files",
    default=0,
    type=int,
    help="Cap the number of source files harvested (0 = no cap).",
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
    help="strict drops heuristic-tier findings; aggressive == balanced today.",
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
def observability_opt_cmd(
    ctx,
    list_tasks,
    list_detectors,
    only_tasks,
    exclude_tasks,
    languages,
    max_files,
    confidence_floor,
    profile,
    limit,
    persist,
):
    """Optimize a repo's diagnosability: find debug prints / weak observability shape."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    from roam.observability_opt import (
        list_observability_opt_detectors,
        list_observability_opt_tasks,
        run_observability_opt,
    )

    # ---- --list-tasks ----
    if list_tasks:
        tasks = list_observability_opt_tasks()
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "observability-opt",
                        summary={
                            "verdict": f"{len(tasks)} diagnosability tasks registered",
                            "task_count": len(tasks),
                        },
                        tasks=tasks,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(tasks)} diagnosability tasks registered")
        for t in tasks:
            click.echo(f"  {t['task_id']:<30} {t['name']} -> best: {t['best_name']}")
        return

    # ---- --list-detectors ----
    if list_detectors:
        dets = list_observability_opt_detectors()
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "observability-opt",
                        summary={
                            "verdict": f"{len(dets)} observability-opt detectors registered",
                            "detector_count": len(dets),
                        },
                        detectors=dets,
                    )
                )
            )
            return
        click.echo(f"VERDICT: {len(dets)} observability-opt detectors registered")
        for d in dets:
            click.echo(f"  {d['name']:<40} task={d['task_id']} basis={d['confidence_basis']} v{d['version']}")
        return

    # ---- run the optimizer ----
    ensure_index()
    with open_db(readonly=True) as conn:
        findings, meta = run_observability_opt(
            conn,
            only=only_tasks,
            exclude=exclude_tasks,
            languages=tuple(languages) or None,
            max_files=max_files,
        )

    # Profile filter (strict drops heuristic-tier).
    if profile.lower() == "strict":
        findings = [f for f in findings if f.get("confidence_basis") not in _STRICT_DROPS_BASIS]
    # Confidence floor filter.
    if confidence_floor:
        floor = confidence_level_rank(confidence_floor)
        findings = [f for f in findings if confidence_level_rank(f.get("confidence"), fallback=1) >= floor]

    # Stable, useful ordering: highest confidence first, then task, then subject.
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
    n_files = meta.get("sources", {}).get("source_files_scanned", 0)

    # ---- A4 persistence (explicit per-family wiring) ----
    persisted = 0
    persist_error = None
    if persist and findings:
        try:
            from roam.db.findings import emit_finding
            from roam.observability_opt import build_finding_records

            records = build_finding_records(findings)
            with open_db() as wconn:
                for rec in records:
                    emit_finding(wconn, rec)
                    persisted += 1
                wconn.commit()
        except Exception as exc:  # noqa: BLE001 — disclose, never silent (Pattern 2)
            persist_error = f"{type(exc).__name__}: {exc}"
            meta["partial_success"] = True

    # ---- bounded output (Pattern 6): cap shown findings ----
    truncated = False
    findings_out = findings
    if limit and limit > 0 and total > limit:
        findings_out = findings[:limit]
        truncated = True

    # ---- verdict (LAW 6: works standalone) ----
    if total == 0:
        verdict = f"0 diagnosability improvements found — {n_files} source files scanned"
    else:
        plural = "s" if total != 1 else ""
        top_frag = f", top: {top_task}" if top_task else ""
        verdict = f"{total} diagnosability improvement{plural} found ({high_count} high-confidence{top_frag})"
    partial = bool(meta.get("partial_success"))
    if partial:
        bits = []
        if meta.get("failed_detectors"):
            bits.append(f"{len(meta['failed_detectors'])} detector(s) failed")
        if not n_files:
            bits.append("no source files harvested")
        if persist_error:
            bits.append("persist failed")
        if bits:
            verdict = f"WARNING (degraded: {'; '.join(bits)}) — {verdict}"

    # ---- agent_contract (LAW 4 anchored, flat, imperative; CONSTRAINT 12 next_commands) ----
    facts = [
        f"{total} diagnosability findings",
        f"{high_count} high-confidence findings",
        f"{n_files} source files scanned",
    ]
    if top_task:
        facts.append(f"{by_task[top_task]} {top_task} findings")  # LAW 4: terminal anchor 'findings'
    facts.append("Run roam observability-opt --list-tasks to see every check")
    next_commands = ["roam observability-opt --list-tasks"]
    if top_task:
        next_commands.append(f"roam observability-opt --only {top_task}")
    next_commands.append("roam observability-opt --persist")

    if json_mode:
        summary: dict = {
            "verdict": verdict,
            "total": total,
            "by_task": by_task,
            "by_confidence": by_confidence,
            "top_task": top_task,
            "profile": profile,
            "truncated": truncated,
            "detectors_executed": meta.get("detectors_executed", 0),
            "detectors_failed": meta.get("detectors_failed", 0),
            "failed_detectors": meta.get("failed_detectors", []),
            "source_files_scanned": n_files,
            "files_unreadable": meta.get("sources", {}).get("files_unreadable", []),
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
                    "observability-opt",
                    summary=summary,
                    findings=findings_out,
                    agent_contract={"facts": facts, "next_commands": next_commands},
                )
            )
        )
        return

    # ---- text output ----
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Scanned: {n_files} source files (profile={profile})")
    if meta.get("only_unknown"):
        click.echo(f"WARNING: unknown --only tasks: {', '.join(meta['only_unknown'])}")
    if persist:
        click.echo(
            f"Persisted {persisted} finding(s) to the findings registry"
            + (f" (error: {persist_error})" if persist_error else "")
        )
    for f in findings_out:
        click.echo(
            f"  [{f.get('confidence', '?'):>6}] {f['task_id']:<24} {f.get('subject', '?')}\n"
            f"          {f.get('reason', '')}\n"
            f"          -> {f.get('suggestion', '')}"
        )
    if truncated:
        click.echo(f"  ... {total - len(findings_out)} more (raise --limit to see all)")
    if total == 0:
        click.echo("Run `roam observability-opt --list-tasks` to see every check.")
