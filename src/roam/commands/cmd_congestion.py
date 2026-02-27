"""Detect developer congestion: files where too many developers work concurrently."""

from __future__ import annotations

import time
from collections import defaultdict

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Congestion analysis helpers
# ---------------------------------------------------------------------------


def _compute_congestion(
    conn,
    window_days: int,
    min_authors: int,
) -> list[dict]:
    """Find files with many concurrent authors in a recent time window.

    Queries git_commits + git_file_changes to count distinct authors per
    file within the last *window_days* days.  Cross-references with
    file_stats for churn data and distinct_authors (all-time).

    Returns a list of dicts sorted by congestion_score descending.
    """
    now_ts = int(time.time())
    cutoff_ts = now_ts - (window_days * 86400)

    # Distinct recent authors per file within the window
    rows = conn.execute(
        """
        SELECT gfc.file_id,
               f.path,
               f.language,
               f.line_count,
               COUNT(DISTINCT gc.author) AS recent_authors,
               COUNT(DISTINCT gc.id) AS recent_commits,
               SUM(gfc.lines_added + gfc.lines_removed) AS recent_churn
        FROM git_file_changes gfc
        JOIN git_commits gc ON gfc.commit_id = gc.id
        JOIN files f ON gfc.file_id = f.id
        WHERE gc.timestamp >= ?
          AND gfc.file_id IS NOT NULL
        GROUP BY gfc.file_id
        HAVING COUNT(DISTINCT gc.author) >= ?
        ORDER BY COUNT(DISTINCT gc.author) DESC
        """,
        (cutoff_ts, min_authors),
    ).fetchall()

    if not rows:
        return []

    # Fetch file_stats for all-time churn and complexity data
    file_ids = [r["file_id"] for r in rows]
    stats_map: dict[int, dict] = {}
    # Query in batches to avoid overly large IN clauses
    for i in range(0, len(file_ids), 400):
        batch = file_ids[i : i + 400]
        placeholders = ",".join("?" * len(batch))
        stat_rows = conn.execute(
            f"SELECT * FROM file_stats WHERE file_id IN ({placeholders})",
            batch,
        ).fetchall()
        for sr in stat_rows:
            stats_map[sr["file_id"]] = dict(sr)

    # Collect per-file author details (who contributed and how much)
    author_details: dict[int, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"commits": 0, "churn": 0}))
    detail_rows = conn.execute(
        """
        SELECT gfc.file_id,
               gc.author,
               COUNT(DISTINCT gc.id) AS commits,
               SUM(gfc.lines_added + gfc.lines_removed) AS churn
        FROM git_file_changes gfc
        JOIN git_commits gc ON gfc.commit_id = gc.id
        WHERE gc.timestamp >= ?
          AND gfc.file_id IN ({})
        GROUP BY gfc.file_id, gc.author
        ORDER BY gfc.file_id, churn DESC
        """.format(",".join("?" * len(file_ids))),
        [cutoff_ts] + file_ids,
    ).fetchall()
    for dr in detail_rows:
        fid = dr["file_id"]
        author_details[fid][dr["author"]] = {
            "commits": dr["commits"],
            "churn": dr["churn"] or 0,
        }

    # Build result entries with congestion scoring
    entries: list[dict] = []
    for r in rows:
        fid = r["file_id"]
        recent_authors = r["recent_authors"]
        recent_commits = r["recent_commits"]
        recent_churn = r["recent_churn"] or 0

        fs = stats_map.get(fid, {})
        all_time_churn = fs.get("total_churn", 0) or 0
        all_time_commits = fs.get("commit_count", 0) or 0
        all_time_authors = fs.get("distinct_authors", 0) or 0
        complexity = fs.get("complexity", 0) or 0

        # Congestion score: combines author count, churn intensity,
        # and complexity into a single risk signal.
        #   - author_factor: more authors = higher congestion
        #   - churn_factor: high recent churn indicates active contention
        #   - complexity_factor: complex files are harder to merge safely
        author_factor = recent_authors
        churn_factor = min(recent_churn / 500.0, 5.0) if recent_churn > 0 else 0
        complexity_factor = min(complexity / 50.0, 2.0) if complexity > 0 else 0
        congestion_score = round(
            (author_factor * 2.0) + (churn_factor * 1.5) + (complexity_factor * 1.0),
            2,
        )

        # Risk level thresholds
        if congestion_score >= 15:
            risk = "critical"
        elif congestion_score >= 10:
            risk = "high"
        elif congestion_score >= 6:
            risk = "medium"
        else:
            risk = "low"

        # Top contributors in this window
        file_authors = author_details.get(fid, {})
        top_contributors = sorted(
            [{"author": a, **d} for a, d in file_authors.items()],
            key=lambda x: x["churn"],
            reverse=True,
        )[:5]

        entries.append(
            {
                "path": r["path"],
                "language": r["language"] or "",
                "line_count": r["line_count"] or 0,
                "recent_authors": recent_authors,
                "recent_commits": recent_commits,
                "recent_churn": recent_churn,
                "all_time_churn": all_time_churn,
                "all_time_commits": all_time_commits,
                "all_time_authors": all_time_authors,
                "complexity": round(complexity, 1),
                "congestion_score": congestion_score,
                "risk": risk,
                "top_contributors": top_contributors,
            }
        )

    # Sort by congestion score descending
    entries.sort(key=lambda e: e["congestion_score"], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("congestion")
@click.option(
    "--window",
    type=int,
    default=90,
    show_default=True,
    help="Time window in days for recent activity.",
)
@click.option(
    "--min-authors",
    type=int,
    default=3,
    show_default=True,
    help="Minimum distinct authors to flag a file as congested.",
)
@click.option("--limit", default=30, show_default=True, help="Max files to display.")
@click.pass_context
def congestion(ctx, window, min_authors, limit):
    """Detect developer congestion: files with too many concurrent authors.

    Identifies files where multiple developers work simultaneously within
    a sliding time window, predicting coordination failures and merge
    conflicts.  Combines author count, churn intensity, and code
    complexity into a single congestion score.

    Unlike ``bus-factor`` (which measures knowledge-loss risk from author
    concentration at the directory level), this command measures
    merge-conflict risk from concurrent authorship at the file level.

    High-congestion files are merge-conflict hotspots.  Consider
    splitting them, assigning clear ownership, or introducing feature
    flags to reduce contention.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        # Check that git data exists
        git_count = conn.execute("SELECT COUNT(*) AS cnt FROM git_commits").fetchone()
        if not git_count or git_count["cnt"] == 0:
            verdict = "No git history indexed -- run 'roam init' with git data"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "congestion",
                            budget=budget,
                            summary={
                                "verdict": verdict,
                                "git_commits": 0,
                            },
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            return

        entries = _compute_congestion(conn, window, min_authors)

        # Summary statistics
        total_files = conn.execute("SELECT COUNT(*) AS cnt FROM files").fetchone()["cnt"]
        congested_count = len(entries)
        critical_count = sum(1 for e in entries if e["risk"] == "critical")
        high_count = sum(1 for e in entries if e["risk"] == "high")

        if congested_count == 0:
            verdict = (
                f"No congested files detected "
                f"(window={window}d, min-authors={min_authors}, "
                f"{total_files} files analysed)"
            )
        elif critical_count > 0:
            verdict = (
                f"{congested_count} congested files -- "
                f"{critical_count} critical, {high_count} high risk "
                f"(window={window}d)"
            )
        else:
            verdict = f"{congested_count} congested files detected (window={window}d, min-authors={min_authors})"

        # Recommendations
        recommendations: list[str] = []
        if critical_count > 0:
            crit_paths = [e["path"] for e in entries if e["risk"] == "critical"][:3]
            recommendations.append(f"Split or assign ownership for critical files: {', '.join(crit_paths)}")
        if high_count > 0:
            recommendations.append(
                f"{high_count} high-risk files need coordination -- consider feature flags or module extraction"
            )
        if congested_count > 5:
            recommendations.append("Run 'roam orchestrate' to partition work across agents/developers")

        # --- JSON output ---
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "congestion",
                        budget=budget,
                        summary={
                            "verdict": verdict,
                            "total_files": total_files,
                            "congested_files": congested_count,
                            "critical": critical_count,
                            "high": high_count,
                            "medium": sum(1 for e in entries if e["risk"] == "medium"),
                            "low": sum(1 for e in entries if e["risk"] == "low"),
                            "window_days": window,
                            "min_authors": min_authors,
                        },
                        files=entries[:limit],
                        recommendations=recommendations,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if entries:
            click.echo(
                f"  Window: {window} days  |  "
                f"Min authors: {min_authors}  |  "
                f"Congested: {congested_count}/{total_files} files"
            )
            click.echo()

            tbl_rows = []
            for e in entries[:limit]:
                tbl_rows.append(
                    [
                        e["path"],
                        str(e["recent_authors"]),
                        str(e["recent_commits"]),
                        str(e["recent_churn"]),
                        f"{e['complexity']:.0f}" if e["complexity"] else "-",
                        f"{e['congestion_score']:.1f}",
                        e["risk"].upper(),
                    ]
                )
            click.echo(
                format_table(
                    ["File", "Authors", "Commits", "Churn", "Complexity", "Score", "Risk"],
                    tbl_rows,
                )
            )

            if len(entries) > limit:
                click.echo(f"  (+{len(entries) - limit} more)")
            click.echo()

            # Show top contributors for the most congested files
            top_entries = entries[:3]
            if top_entries:
                click.echo("  Top congested files -- contributors:")
                for e in top_entries:
                    contribs = ", ".join(
                        f"{c['author']} ({c['commits']}c/{c['churn']}L)" for c in e["top_contributors"][:4]
                    )
                    click.echo(f"    {e['path']}: {contribs}")
                click.echo()

        # Recommendations
        if recommendations:
            click.echo("  Recommendations:")
            for rec in recommendations:
                click.echo(f"    - {rec}")
