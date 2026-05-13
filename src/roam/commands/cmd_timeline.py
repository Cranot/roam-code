"""``roam timeline <symbol>`` — chronological commit history that touched a symbol.

joins ``symbols`` (for the file id of a symbol) with
``git_file_changes`` (for commits touching that file) and ``git_commits``
(for author + timestamp + message) to give one tight view of "who has
worked on this and when". Useful before refactoring a symbol — you see
the active maintainers and the rate of change.
"""

from __future__ import annotations

from datetime import datetime

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


def _fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "?"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "?"


@roam_capability(
    name="timeline",
    category="health",
    summary="Show commits that touched the file owning <symbol>",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.argument("symbol")
@click.option("--limit", default=20, show_default=True, help="Max commits to show.")
@click.pass_context
def timeline(ctx, symbol: str, limit: int) -> None:
    """Show commits that touched the file owning <symbol>."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        sym_rows = conn.execute(
            "SELECT s.id, s.name, s.file_id, f.path FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? OR s.qualified_name = ? OR s.qualified_name LIKE ? "
            "LIMIT 1",
            (symbol, symbol, f"%.{symbol}"),
        ).fetchone()
        if sym_rows is None:
            verdict = f"no symbol named '{symbol}' in index"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "timeline",
                            summary={"verdict": verdict, "commit_count": 0},
                            commits=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return
        file_id = sym_rows["file_id"]
        file_path = sym_rows["path"]
        # Dedup duplicate (commit, file) rows: incremental indexing can
        # leave more than one ``git_file_changes`` row per pair.
        commit_rows = conn.execute(
            """
            SELECT c.hash, c.author, c.timestamp, c.message,
                   MAX(gfc.lines_added) AS lines_added,
                   MAX(gfc.lines_removed) AS lines_removed
              FROM git_file_changes gfc
              JOIN git_commits c ON c.id = gfc.commit_id
             WHERE gfc.file_id = ?
             GROUP BY c.id
             ORDER BY c.timestamp DESC
             LIMIT ?
            """,
            (file_id, int(limit)),
        ).fetchall()

    commits = []
    authors: dict[str, int] = {}
    total_added = 0
    total_removed = 0
    for r in commit_rows:
        added = int(r["lines_added"] or 0)
        removed = int(r["lines_removed"] or 0)
        total_added += added
        total_removed += removed
        author = r["author"] or "?"
        authors[author] = authors.get(author, 0) + 1
        commits.append(
            {
                "sha": (r["hash"] or "")[:12],
                "date": _fmt_ts(r["timestamp"]),
                "author": author,
                "added": added,
                "removed": removed,
                "subject": (r["message"] or "").splitlines()[0] if r["message"] else "",
            }
        )
    distinct_authors = len(authors)
    top_author = max(authors.items(), key=lambda x: x[1])[0] if authors else None
    verdict = (
        f"{len(commits)} commit(s) touched {file_path} "
        f"(+{total_added} −{total_removed} lines, {distinct_authors} author(s))"
        if commits
        else f"no commits touch {file_path}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "timeline",
                    summary={
                        "verdict": verdict,
                        "commit_count": len(commits),
                        "file_path": file_path,
                        "added_total": total_added,
                        "removed_total": total_removed,
                        "distinct_authors": distinct_authors,
                        "top_author": top_author,
                    },
                    commits=commits,
                    authors=authors,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not commits:
        return
    click.echo()
    click.echo(f"{'Date':<11}  {'SHA':<12}  {'+/-':>9}  {'Author':<20}  Subject")
    click.echo(f"{'-' * 11}  {'-' * 12}  {'-' * 9}  {'-' * 20}  {'-' * 30}")
    for c in commits:
        author = (c["author"] or "?")[:20]
        diff = f"+{c['added']}/−{c['removed']}"
        click.echo(f"{c['date']:<11}  {c['sha']:<12}  {diff:>9}  {author:<20}  {c['subject'][:60]}")
