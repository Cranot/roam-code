"""Detect suboptimal algorithms and suggest better approaches."""

from __future__ import annotations

from collections import defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.catalog.tasks import CATALOG, get_task, best_way


@click.command()
@click.option("--task", "task_filter", default=None,
              help="Filter by task ID (e.g. sorting, membership)")
@click.option("--confidence", "confidence_filter", default=None,
              type=click.Choice(["high", "medium", "low"], case_sensitive=False),
              help="Filter by confidence level")
@click.option("--limit", "-n", default=30, help="Max findings to show")
@click.pass_context
def math_cmd(ctx, task_filter, confidence_filter, limit):
    """Detect suboptimal algorithms and suggest better approaches.

    Scans indexed symbols for common algorithmic anti-patterns
    (manual sort, linear search, nested-loop lookup, busy wait, etc.)
    and recommends better alternatives from a universal catalog.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    from roam.catalog.detectors import run_detectors

    with open_db(readonly=True) as conn:
        findings = run_detectors(conn, task_filter, confidence_filter)

        # Sort: high first, then medium, then low
        _conf_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: _conf_order.get(f["confidence"], 9))

        # Apply limit
        truncated = len(findings) > limit
        findings = findings[:limit]

        # Group by task category
        by_category = defaultdict(list)
        for f in findings:
            task = get_task(f["task_id"])
            cat = task["category"] if task else "other"
            by_category[cat].append(f)

        by_confidence = defaultdict(int)
        for f in findings:
            by_confidence[f["confidence"]] += 1

        total = len(findings)
        conf_parts = []
        for c in ("high", "medium", "low"):
            if by_confidence.get(c):
                conf_parts.append(f"{by_confidence[c]} {c}")
        conf_str = ", ".join(conf_parts) if conf_parts else "none"

        verdict = (
            f"{total} algorithmic improvement{'s' if total != 1 else ''} found "
            f"({conf_str})"
            if total else "No algorithmic issues detected"
        )

        # --- JSON output ---
        if json_mode:
            click.echo(to_json(json_envelope("math",
                summary={
                    "verdict": verdict,
                    "total": total,
                    "by_category": dict(
                        (k, len(v)) for k, v in by_category.items()
                    ),
                    "by_confidence": dict(by_confidence),
                    "truncated": truncated,
                },
                findings=findings,
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        if not findings:
            return

        click.echo()

        # Group by task_id for display
        by_task = defaultdict(list)
        for f in findings:
            by_task[f["task_id"]].append(f)

        for task_id, task_findings in by_task.items():
            task = get_task(task_id)
            task_name = task["name"] if task else task_id
            click.echo(f"{task_name} ({len(task_findings)}):")

            for f in task_findings:
                kind_abbr = abbrev_kind(f["kind"])
                name = f["symbol_name"]
                location = f["location"]
                conf = f["confidence"]

                # Get catalog info for display
                detected = None
                suggested = None
                if task:
                    for w in task["ways"]:
                        if w["id"] == f["detected_way"]:
                            detected = w
                        if w["id"] == f["suggested_way"]:
                            suggested = w

                click.echo(f"  {kind_abbr:<5s} {name:<40s} {location}  [{conf}]")
                if detected:
                    click.echo(f"        Current: {detected['name']} -- {detected['time']}")
                if suggested:
                    click.echo(f"        Better:  {suggested['name']} -- {suggested['time']}")
                    if suggested.get("tip"):
                        click.echo(f"        Tip: {suggested['tip']}")

            click.echo()

        if truncated:
            click.echo(f"  (showing {limit} of more findings, use --limit to see more)")
