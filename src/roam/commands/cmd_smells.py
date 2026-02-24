"""Detect and report code smells across the codebase."""

from __future__ import annotations

from collections import Counter

import click

from roam.db.connection import open_db
from roam.output.formatter import (
    format_table, to_json, json_envelope, summary_envelope,
)
from roam.commands.resolve import ensure_index


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_VALID_SEVERITIES = frozenset(_SEVERITY_ORDER)


@click.command()
@click.option(
    '--file', 'file_path', default=None, type=click.Path(),
    help='Filter smells to a specific file path',
)
@click.option(
    '--min-severity', default=None,
    type=click.Choice(['critical', 'warning', 'info'], case_sensitive=False),
    help='Minimum severity to include (critical > warning > info)',
)
@click.pass_context
def smells(ctx, file_path, min_severity):
    """Detect code smells: brain methods, god classes, deep nesting, and more."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    detail = ctx.obj.get('detail', False) if ctx.obj else False
    ensure_index()

    from roam.catalog.smells import run_all_detectors

    with open_db(readonly=True) as conn:
        findings = run_all_detectors(conn)

        # Filter by file
        if file_path:
            norm = file_path.replace("\\", "/")
            findings = [
                f for f in findings
                if norm in f.get("location", "").replace("\\", "/")
            ]

        # Filter by minimum severity
        if min_severity:
            min_sev = min_severity.lower()
            max_order = _SEVERITY_ORDER.get(min_sev, 2)
            findings = [
                f for f in findings
                if _SEVERITY_ORDER.get(f.get("severity", "info"), 2) <= max_order
            ]

        # Compute summary stats
        total_smells = len(findings)
        severity_counts = Counter(f.get("severity", "info") for f in findings)
        smell_types = Counter(f.get("smell_id", "unknown") for f in findings)
        files_affected = len(set(
            f.get("location", "").split(":")[0]
            for f in findings
            if f.get("location")
        ))

        # Verdict
        critical = severity_counts.get("critical", 0)
        warning = severity_counts.get("warning", 0)
        if total_smells == 0:
            verdict = "Clean: no code smells detected"
        elif critical > 0:
            verdict = (
                f"Needs refactoring: {total_smells} smell"
                f"{'s' if total_smells != 1 else ''} "
                f"({critical} critical, {warning} warning) "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )
        elif warning > 0:
            verdict = (
                f"Fair: {total_smells} smell"
                f"{'s' if total_smells != 1 else ''} "
                f"({warning} warning) "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )
        else:
            verdict = (
                f"Good: {total_smells} minor smell"
                f"{'s' if total_smells != 1 else ''} "
                f"in {files_affected} file{'s' if files_affected != 1 else ''}"
            )

        if json_mode:
            envelope = json_envelope(
                "smells",
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "total_smells": total_smells,
                    "severity": dict(severity_counts),
                    "smell_types": dict(smell_types),
                    "files_affected": files_affected,
                },
                smells=[
                    {
                        "smell_id": f["smell_id"],
                        "severity": f["severity"],
                        "symbol_name": f["symbol_name"],
                        "kind": f["kind"],
                        "location": f["location"],
                        "metric_value": f["metric_value"],
                        "threshold": f["threshold"],
                        "description": f["description"],
                    }
                    for f in findings
                ],
            )
            if not detail:
                envelope = summary_envelope(envelope)
            click.echo(to_json(envelope))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}\n")

        if total_smells == 0:
            return

        # Summary line
        sev_parts = []
        for sev in ("critical", "warning", "info"):
            count = severity_counts.get(sev, 0)
            if count:
                sev_parts.append(f"{count} {sev.upper()}")
        click.echo(f"Smells: {total_smells} total -- {', '.join(sev_parts)}")
        click.echo(f"Files affected: {files_affected}")
        click.echo()

        if not detail:
            # Show top 5 only
            top = findings[:5]
            if top:
                rows = [
                    [
                        f["severity"].upper(),
                        f["smell_id"],
                        f["symbol_name"],
                        f["description"],
                    ]
                    for f in top
                ]
                click.echo(format_table(
                    ["Sev", "Smell", "Symbol", "Description"], rows,
                ))
                if total_smells > 5:
                    click.echo(f"\n(+{total_smells - 5} more, use --detail for full list)")
            return

        # Full detail mode
        rows = [
            [
                f["severity"].upper(),
                f["smell_id"],
                f["symbol_name"],
                str(f["metric_value"]),
                str(f["threshold"]),
                f["location"],
                f["description"],
            ]
            for f in findings
        ]
        click.echo(format_table(
            ["Sev", "Smell", "Symbol", "Value", "Threshold", "Location", "Description"],
            rows,
        ))
