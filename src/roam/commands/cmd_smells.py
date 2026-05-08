"""Detect and report code smells across the codebase."""

from __future__ import annotations

from collections import Counter

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import (
    format_table,
    json_envelope,
    summary_envelope,
    to_json,
)

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_VALID_SEVERITIES = frozenset(_SEVERITY_ORDER)


def _file_role_lookup(conn) -> dict[str, str]:
    """Return a {path: file_role} map for tooling-exclusion filtering.

    Uses the canonical ``files.file_role`` column populated at index
    time. Falls back to an empty dict if the schema doesn't have that
    column (very old indexes pre-v9).
    """
    try:
        rows = conn.execute("SELECT path, file_role FROM files").fetchall()
    except Exception:
        return {}
    return {
        (r["path"] if hasattr(r, "keys") else r[0]): (r["file_role"] if hasattr(r, "keys") else r[1]) or ""
        for r in rows
    }


def _short_loc(location: str) -> str:
    """Render a location string compact: ``last/two/segments.py:line``.

    Empty input → empty output. Lines without ``:line`` survive."""
    if not location:
        return ""
    norm = location.replace("\\", "/")
    parts = norm.split("/")
    tail = "/".join(parts[-2:]) if len(parts) >= 2 else norm
    return tail


@click.command()
@click.option(
    "--file",
    "file_path",
    default=None,
    type=click.Path(),
    help="Filter smells to a specific file path",
)
@click.option(
    "--min-severity",
    default=None,
    type=click.Choice(["critical", "warning", "info"], case_sensitive=False),
    help="Minimum severity to include (critical > warning > info)",
)
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, build scripts, dev tooling, and generated "
        "files in the smell count. Excluded by default because high "
        "complexity in one-shot scripts and codegen output is expected "
        "and uninteresting — surfacing them dominates the headline number."
    ),
)
@click.pass_context
def smells(ctx, file_path, min_severity, include_tooling):
    """Detect code smells: brain methods, god classes, deep nesting, and more.

    Unlike ``vibe-check`` (which detects AI-generated code anti-patterns via
    source-file regex) and ``health`` (which gives an aggregate codebase
    score), this command runs 15 deterministic DB-query-based structural smell
    detectors: brain methods, god classes, deep nesting, shotgun surgery,
    excessive parameters, and more.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    ensure_index()

    from roam.catalog.smells import run_all_detectors

    with open_db(readonly=True) as conn:
        findings = run_all_detectors(conn)

        # Default: exclude tooling, generated, examples, vendor, workspaces,
        # docs. The headline number is dominated by paths the user didn't
        # write or doesn't want to refactor (``dev/``, ``.github/scripts/``,
        # ``examples/``, vendored packages, codegen output). The shared
        # path-hint set lives in ``roam.output.file_role_hints`` so all
        # headline commands stay in sync. ``--include-tooling`` opts back
        # into the full set.
        from roam.output.file_role_hints import is_excluded_path

        excluded_tooling = 0
        if not include_tooling:
            tooling_roles = {"ci", "scripts", "build", "generated"}
            tooling_roles_per_file = _file_role_lookup(conn)
            kept: list[dict] = []
            for f in findings:
                loc = (f.get("location") or "").replace("\\", "/")
                file_path_only = loc.split(":", 1)[0] if loc else ""
                role = tooling_roles_per_file.get(file_path_only)
                if role in tooling_roles:
                    excluded_tooling += 1
                    continue
                if is_excluded_path(file_path_only):
                    excluded_tooling += 1
                    continue
                kept.append(f)
            findings = kept

        # Filter by file
        if file_path:
            norm = file_path.replace("\\", "/")
            findings = [f for f in findings if norm in f.get("location", "").replace("\\", "/")]

        # Filter by minimum severity
        if min_severity:
            min_sev = min_severity.lower()
            max_order = _SEVERITY_ORDER.get(min_sev, 2)
            findings = [f for f in findings if _SEVERITY_ORDER.get(f.get("severity", "info"), 2) <= max_order]

        # Compute summary stats
        total_smells = len(findings)
        severity_counts = Counter(f.get("severity", "info") for f in findings)
        smell_types = Counter(f.get("smell_id", "unknown") for f in findings)
        files_affected = len(set(f.get("location", "").split(":")[0] for f in findings if f.get("location")))

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
            # Show top 5 with truncated location so the user can jump
            # straight to the offender. 
            # bare symbol names ("main", "buildComment") were
            # ambiguous when the same name lived in multiple files.
            top = findings[:5]
            if top:
                rows = [
                    [
                        f["severity"].upper(),
                        f["smell_id"],
                        f["symbol_name"],
                        _short_loc(f.get("location") or ""),
                        f["description"],
                    ]
                    for f in top
                ]
                click.echo(
                    format_table(
                        ["Sev", "Smell", "Symbol", "Where", "Description"],
                        rows,
                    )
                )
                if total_smells > 5:
                    click.echo(f"\n(+{total_smells - 5} more, run `roam --detail smells` for the full list)")
            if not include_tooling and excluded_tooling:
                click.echo(
                    f"\n(excluded {excluded_tooling} smell(s) in tooling/scripts/ci/generated; "
                    f"pass --include-tooling to surface them)"
                )
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
        click.echo(
            format_table(
                ["Sev", "Smell", "Symbol", "Value", "Threshold", "Location", "Description"],
                rows,
            )
        )
