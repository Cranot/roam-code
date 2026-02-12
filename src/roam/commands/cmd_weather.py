"""Show code hotspots: churn x complexity ranking."""

import click

from roam.db.connection import open_db
from roam.db.queries import TOP_CHURN_FILES
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option('-n', 'count', default=20, help='Number of hotspots')
@click.pass_context
def weather(ctx, count):
    """Show code hotspots: churn x complexity ranking."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(TOP_CHURN_FILES, (count * 2,)).fetchall()
        if not rows:
            if json_mode:
                click.echo(to_json(json_envelope("weather",
                    summary={"hotspots": 0},
                    hotspots=[],
                )))
            else:
                click.echo("No churn data available. Is this a git repository?")
            return

        scored = []
        for r in rows:
            churn = r["total_churn"] or 0
            complexity = r["complexity"] or 1
            score = churn * complexity
            commits = r["commit_count"] or 0
            authors = r["distinct_authors"] or 0
            if churn > 100 and complexity > 5:
                reason = "BOTH"
            elif churn > complexity * 20:
                reason = "HIGH-CHURN"
            else:
                reason = "HIGH-COMPLEXITY"
            scored.append((score, churn, complexity, commits, authors, reason, r["path"]))
        scored.sort(reverse=True)

        if json_mode:
            hotspot_list = [
                {
                    "path": path, "score": round(score), "churn": churn,
                    "complexity": round(complexity, 1), "commits": commits,
                    "authors": authors, "reason": reason,
                }
                for score, churn, complexity, commits, authors, reason, path in scored[:count]
            ]
            click.echo(to_json(json_envelope("weather",
                summary={
                    "hotspots": len(hotspot_list),
                    "max_score": hotspot_list[0]["score"] if hotspot_list else 0,
                },
                hotspots=hotspot_list,
            )))
            return

        table_rows = []
        for score, churn, complexity, commits, authors, reason, path in scored[:count]:
            table_rows.append([
                f"{score:.0f}", str(churn), f"{complexity:.1f}",
                str(commits), str(authors), reason, path,
            ])

        click.echo("=== Hotspots (churn x complexity) ===")
        click.echo(format_table(
            ["Score", "Churn", "Cmplx", "Commits", "Authors", "Reason", "Path"],
            table_rows,
        ))
