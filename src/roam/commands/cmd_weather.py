"""Show code hotspots: churn x complexity ranking."""

import math

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

        # Collect raw values for percentile-based normalization
        raw = []
        for r in rows:
            churn = r["total_churn"] or 0
            complexity = r["complexity"] or 1
            commits = r["commit_count"] or 0
            authors = r["distinct_authors"] or 0
            raw.append((churn, complexity, commits, authors, r["path"]))

        all_churns = sorted(v[0] for v in raw)
        all_complexities = sorted(v[1] for v in raw)
        churn_p75 = all_churns[int(len(all_churns) * 0.75)] if all_churns else 1
        cmplx_p75 = all_complexities[int(len(all_complexities) * 0.75)] if all_complexities else 1
        churn_p75 = max(churn_p75, 1)
        cmplx_p75 = max(cmplx_p75, 1)

        scored = []
        for churn, complexity, commits, authors, path in raw:
            # Geometric mean of normalized values avoids explosive growth
            # and balances both signals equally (CodeScene-inspired, Tornhill 2018).
            churn_norm = churn / churn_p75
            cmplx_norm = complexity / cmplx_p75
            score = math.sqrt(max(churn_norm, 0) * max(cmplx_norm, 0))

            # Percentile-adaptive classification instead of magic thresholds
            if churn_norm > 1.0 and cmplx_norm > 1.0:
                reason = "BOTH"
            elif churn_norm > cmplx_norm * 2:
                reason = "HIGH-CHURN"
            else:
                reason = "HIGH-COMPLEXITY"
            scored.append((score, churn, complexity, commits, authors, reason, path))
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
