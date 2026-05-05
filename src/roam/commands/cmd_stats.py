"""``roam stats`` — aggregate metrics over the index.

redactedsingle overview number for "where am I". Counts files by
language, file role, and symbol kind; reports recent commit activity.
Useful as the first thing an agent runs after ``roam init`` to size
the codebase.
"""

from __future__ import annotations

import time

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@click.command()
@click.option(
    "--days",
    type=int,
    default=30,
    show_default=True,
    help="Window for the recent-commit activity counter.",
)
@click.pass_context
def stats(ctx, days: int) -> None:
    """Aggregate metrics: language, role, kind counts + recent activity."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        file_total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbol_total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        try:
            commits_total = conn.execute("SELECT COUNT(*) FROM git_commits").fetchone()[0]
        except Exception:
            commits_total = 0
        cutoff_ts = int(time.time()) - days * 86400
        try:
            commits_recent = conn.execute(
                "SELECT COUNT(*) FROM git_commits WHERE timestamp >= ?",
                (cutoff_ts,),
            ).fetchone()[0]
        except Exception:
            commits_recent = 0

        languages = dict(
            conn.execute(
                "SELECT COALESCE(language, 'unknown') AS lang, COUNT(*) AS n FROM files GROUP BY lang ORDER BY n DESC"
            ).fetchall()
        )
        roles = dict(
            conn.execute(
                "SELECT COALESCE(file_role, 'unknown') AS role, COUNT(*) AS n FROM files GROUP BY role ORDER BY n DESC"
            ).fetchall()
        )
        kinds = dict(conn.execute("SELECT kind, COUNT(*) AS n FROM symbols GROUP BY kind ORDER BY n DESC").fetchall())

        loc_row = conn.execute("SELECT COALESCE(SUM(line_count), 0) AS lines FROM files").fetchone()
        line_total = int(loc_row["lines"] or 0)

    verdict = (
        f"{file_total} file(s) · {symbol_total} symbol(s) · {line_total:,} line(s) · "
        f"{commits_recent} commit(s) in the last {days} day(s)"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "stats",
                    summary={
                        "verdict": verdict,
                        "file_total": file_total,
                        "symbol_total": symbol_total,
                        "line_total": line_total,
                        "commits_total": commits_total,
                        "commits_recent": commits_recent,
                        "days": days,
                    },
                    by_language=languages,
                    by_role=roles,
                    by_kind=kinds,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Files          : {file_total:,}")
    click.echo(f"Symbols        : {symbol_total:,}")
    click.echo(f"Lines of code  : {line_total:,}")
    click.echo(f"Total commits  : {commits_total:,}")
    click.echo(f"Recent commits : {commits_recent} in {days} days")
    click.echo()
    click.echo("By language    " + " " * 7 + "Files")
    for lang, n in list(languages.items())[:10]:
        click.echo(f"  {str(lang):<20}  {n:>5}")
    click.echo()
    click.echo("By file role   " + " " * 7 + "Files")
    for role, n in roles.items():
        click.echo(f"  {role:<20}  {n:>5}")
    click.echo()
    click.echo("By symbol kind " + " " * 7 + "Symbols")
    for kind, n in list(kinds.items())[:10]:
        click.echo(f"  {kind:<20}  {n:>7}")
