"""Detect ownership drift: where declared owners differ from actual contributors."""

from __future__ import annotations

import math
import time
from collections import defaultdict

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.cmd_codeowners import find_codeowners, parse_codeowners, resolve_owners


# ---------------------------------------------------------------------------
# Time-decayed ownership scoring
# ---------------------------------------------------------------------------

# Half-life in days: contributions lose half their weight every 180 days.
_HALF_LIFE_DAYS = 180


def _compute_time_decay(days_old: float, half_life: float = _HALF_LIFE_DAYS) -> float:
    """Exponential decay weight: ``0.5 ** (days_old / half_life)``.

    Recent contributions receive weight ~1.0, contributions from 180 days
    ago receive weight 0.5, contributions from 360 days ago receive 0.25,
    and so on.
    """
    if days_old <= 0:
        return 1.0
    return math.pow(0.5, days_old / half_life)


def compute_file_ownership(
    conn,
    file_id: int,
    now_ts: int | None = None,
) -> dict[str, float]:
    """Compute time-decayed ownership scores for a file.

    Queries ``git_file_changes`` joined with ``git_commits`` to get
    per-author, per-commit contribution data.  Each contribution
    (lines_added + lines_removed) is weighted by exponential time decay
    so recent work counts more.

    Returns a dict mapping author name to normalised ownership share
    (values sum to 1.0).  Returns an empty dict if the file has no
    git history.
    """
    if now_ts is None:
        now_ts = int(time.time())

    rows = conn.execute(
        """SELECT gc.author,
                  gfc.lines_added + gfc.lines_removed AS churn,
                  gc.timestamp
           FROM git_file_changes gfc
           JOIN git_commits gc ON gfc.commit_id = gc.id
           WHERE gfc.file_id = ?""",
        (file_id,),
    ).fetchall()

    if not rows:
        return {}

    author_scores: dict[str, float] = defaultdict(float)
    for row in rows:
        author = row["author"]
        churn = row["churn"] or 0
        ts = row["timestamp"] or 0
        days_old = max(0.0, (now_ts - ts) / 86400.0)
        weight = _compute_time_decay(days_old)
        author_scores[author] += churn * weight

    total = sum(author_scores.values())
    if total <= 0:
        return {}

    return {author: score / total for author, score in author_scores.items()}


def _normalise_name(name: str) -> str:
    """Lower-case and strip leading ``@`` for comparison."""
    return name.lstrip("@").lower()


def compute_drift_score(
    declared_owners: list[str],
    ownership_shares: dict[str, float],
) -> float:
    """Compute a drift score between 0.0 and 1.0.

    Drift = ``1 - max(declared owner shares)``.  If no declared owner
    appears in the actual ownership map, drift = 1.0 (maximum).  If
    the top declared owner has 100% actual ownership, drift = 0.0.
    """
    if not declared_owners or not ownership_shares:
        return 0.0

    declared_norm = {_normalise_name(o) for o in declared_owners}

    best_declared_share = 0.0
    for author, share in ownership_shares.items():
        if _normalise_name(author) in declared_norm:
            best_declared_share = max(best_declared_share, share)

    # Also check if declared name *contains* an actual author or vice-versa
    # to handle email/name mismatches. Sum shares of all declared owners.
    sum_declared_share = 0.0
    for author, share in ownership_shares.items():
        if _normalise_name(author) in declared_norm:
            sum_declared_share += share

    # Drift is 1 minus the combined declared owner share
    return round(1.0 - min(sum_declared_share, 1.0), 4)


def _top_contributor(ownership_shares: dict[str, float]) -> tuple[str, float]:
    """Return ``(name, share)`` of the top contributor."""
    if not ownership_shares:
        return ("", 0.0)
    top = max(ownership_shares.items(), key=lambda x: x[1])
    return top


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command("drift")
@click.option(
    "--threshold",
    type=float,
    default=0.5,
    help="Drift threshold (0-1, default 0.5)",
)
@click.option("--limit", default=30, help="Max items to display")
@click.pass_context
def drift(ctx, threshold, limit):
    """Detect ownership drift: where declared owners differ from actual contributors."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()
    co_path = find_codeowners(project_root)

    # --- No CODEOWNERS file ---
    if co_path is None:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "drift",
                        budget=budget,
                        summary={
                            "verdict": "No CODEOWNERS file found",
                            "codeowners_found": False,
                        },
                    )
                )
            )
            return
        click.echo("VERDICT: No CODEOWNERS file found")
        click.echo()
        click.echo("  A CODEOWNERS file is required for drift detection.")
        click.echo("  Create one and run `roam drift` to analyse ownership drift.")
        return

    rules = parse_codeowners(co_path)

    with open_db(readonly=True) as conn:
        # Get all indexed files
        all_files = conn.execute(
            "SELECT id, path FROM files ORDER BY path"
        ).fetchall()

        if not all_files:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "drift",
                            budget=budget,
                            summary={
                                "verdict": "No files in index",
                                "total_files": 0,
                            },
                        )
                    )
                )
            else:
                click.echo("VERDICT: No files in index")
            return

        now_ts = int(time.time())

        # Resolve ownership for each file
        owned_files: list[dict] = []
        for f in all_files:
            fpath = f["path"].replace("\\", "/")
            owners = resolve_owners(rules, fpath)
            if owners:
                owned_files.append(
                    {
                        "file_id": f["id"],
                        "path": fpath,
                        "owners": owners,
                    }
                )

        if not owned_files:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "drift",
                            budget=budget,
                            summary={
                                "verdict": "No files matched by CODEOWNERS rules",
                                "total_files": len(all_files),
                                "owned_files": 0,
                                "drift_files": 0,
                            },
                        )
                    )
                )
            else:
                click.echo("VERDICT: No files matched by CODEOWNERS rules")
            return

        # Compute time-decayed ownership and drift for each owned file
        drift_entries: list[dict] = []
        for fo in owned_files:
            shares = compute_file_ownership(conn, fo["file_id"], now_ts)
            if not shares:
                # No git history for this file -- skip
                continue

            dscore = compute_drift_score(fo["owners"], shares)
            top_name, top_share = _top_contributor(shares)

            if dscore >= threshold:
                drift_entries.append(
                    {
                        "path": fo["path"],
                        "declared_owners": fo["owners"],
                        "actual_top_contributor": top_name,
                        "actual_top_share": round(top_share, 4),
                        "drift_score": dscore,
                        "ownership_shares": {
                            a: round(s, 4) for a, s in sorted(
                                shares.items(), key=lambda x: x[1], reverse=True
                            )
                        },
                    }
                )

        # Sort by drift score descending
        drift_entries.sort(key=lambda e: e["drift_score"], reverse=True)

        total_owned = len(owned_files)
        drift_count = len(drift_entries)
        drift_pct = round(drift_count * 100 / total_owned, 1) if total_owned else 0.0
        avg_drift = (
            round(sum(e["drift_score"] for e in drift_entries) / drift_count, 2)
            if drift_count
            else 0.0
        )
        highest_entry = drift_entries[0] if drift_entries else None

        # Build recommendations
        recommendations: list[str] = []
        if drift_count > 0:
            recommendations.append(
                f"Update CODEOWNERS for {drift_count} files where "
                f"declared owners are no longer active"
            )
            # Group drift files by top contributor to suggest owner additions
            contributor_groups: dict[str, list[str]] = defaultdict(list)
            for de in drift_entries:
                contributor_groups[de["actual_top_contributor"]].append(de["path"])
            for contrib, paths in sorted(
                contributor_groups.items(), key=lambda x: len(x[1]), reverse=True
            ):
                if len(paths) >= 2:
                    # Find common directory
                    common = _common_directory(paths)
                    recommendations.append(
                        f"{contrib} should be added as owner for "
                        f"{len(paths)} files in {common}"
                    )

        # Build verdict
        if drift_count == 0:
            verdict = (
                f"No ownership drift detected (threshold={threshold}, "
                f"{total_owned} owned files analysed)"
            )
        else:
            verdict = (
                f"{drift_count} files with ownership drift "
                f"({drift_pct}% of {total_owned} owned files)"
            )

        # --- JSON output ---
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "drift",
                        budget=budget,
                        summary={
                            "verdict": verdict,
                            "total_files": len(all_files),
                            "owned_files": total_owned,
                            "drift_files": drift_count,
                            "drift_pct": drift_pct,
                            "avg_drift_score": avg_drift,
                            "highest_drift": (
                                {
                                    "path": highest_entry["path"],
                                    "score": highest_entry["drift_score"],
                                }
                                if highest_entry
                                else None
                            ),
                            "threshold": threshold,
                        },
                        drift=drift_entries[:limit],
                        recommendations=recommendations,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if drift_entries:
            tbl_rows = []
            for de in drift_entries[:limit]:
                declared_str = ", ".join(de["declared_owners"])
                actual_str = (
                    f"{de['actual_top_contributor']} "
                    f"({round(de['actual_top_share'] * 100)}%)"
                )
                tbl_rows.append(
                    [
                        de["path"],
                        declared_str,
                        actual_str,
                        f"{de['drift_score']:.2f}",
                    ]
                )
            click.echo(
                format_table(
                    ["File", "Declared Owner", "Actual Top Contributor", "Drift Score"],
                    tbl_rows,
                )
            )
            if len(drift_entries) > limit:
                click.echo(f"  (+{len(drift_entries) - limit} more)")
            click.echo()

        # Summary
        click.echo("  Summary:")
        click.echo(f"    Files analysed: {total_owned}")
        click.echo(f"    Files with drift: {drift_count} ({drift_pct}%)")
        click.echo(f"    Average drift score: {avg_drift}")
        if highest_entry:
            click.echo(
                f"    Highest drift: {highest_entry['path']} "
                f"({highest_entry['drift_score']:.2f})"
            )
        click.echo()

        # Recommendations
        if recommendations:
            click.echo("  Recommendations:")
            for rec in recommendations:
                click.echo(f"    - {rec}")


def _common_directory(paths: list[str]) -> str:
    """Find the most common directory prefix among a list of file paths."""
    if not paths:
        return "./"
    dirs: dict[str, int] = defaultdict(int)
    for p in paths:
        p = p.replace("\\", "/")
        last_slash = p.rfind("/")
        if last_slash >= 0:
            dirs[p[: last_slash + 1]] += 1
        else:
            dirs["./"] += 1
    if not dirs:
        return "./"
    return max(dirs.items(), key=lambda x: x[1])[0]
