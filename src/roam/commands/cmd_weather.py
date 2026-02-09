"""Show code hotspots: churn x complexity ranking."""

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import TOP_CHURN_FILES
from roam.output.formatter import format_table


def _ensure_index():
    from roam.db.connection import db_exists
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.option('-n', 'count', default=20, help='Number of hotspots')
def weather(count):
    """Show code hotspots: churn x complexity ranking."""
    _ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(TOP_CHURN_FILES, (count * 2,)).fetchall()
        if not rows:
            click.echo("No churn data available. Is this a git repository?")
            return

        scored = []
        for r in rows:
            churn = r["total_churn"] or 0
            complexity = r["complexity"] or 1
            score = churn * complexity
            commits = r["commit_count"] or 0
            authors = r["distinct_authors"] or 0
            # Determine the primary driver
            if churn > 100 and complexity > 5:
                reason = "BOTH"
            elif churn > complexity * 20:
                reason = "HIGH-CHURN"
            else:
                reason = "HIGH-COMPLEXITY"
            scored.append((score, churn, complexity, commits, authors, reason, r["path"]))
        scored.sort(reverse=True)

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
