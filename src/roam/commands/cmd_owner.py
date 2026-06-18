"""Show code ownership: who owns a file or directory.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because owner outputs are invocation-scoped ownership
attribution rankings (top-N authors by commits / lines / recency) —
not per-location code violations. Ownership describes who touched a
file most recently, which is metadata about the change history, not
a code analysis finding suitable for SARIF ``locations[]``. See
``cmd_codeowners`` for the parallel advisory-attribution disclosure
pattern (W1197) + action.yml _SUPPORTED_SARIF allowlist + W1224-audit
memo.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, find_project_root, open_db
from roam.index.git_stats import get_blame_for_file
from roam.output.formatter import format_table, json_envelope, to_json


def _format_date(epoch: int) -> str:
    """Format a unix timestamp as YYYY-MM-DD."""
    if not epoch:
        return "?"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def _ownership_for_file(project_root, file_path):
    """Compute ownership breakdown for a single file."""
    blame = get_blame_for_file(project_root, file_path)
    if not blame:
        return None

    author_lines: dict[str, int] = defaultdict(int)
    last_active = {}
    for entry in blame:
        author = entry["author"]
        author_lines[author] += 1
        ts = entry.get("timestamp", 0)
        if ts and (author not in last_active or ts > last_active[author]):
            last_active[author] = ts

    total = sum(author_lines.values())
    if total == 0:
        return None

    # Sort by lines desc
    sorted_authors = sorted(author_lines.items(), key=lambda x: x[1], reverse=True)

    # Compute fragmentation: 1 - sum(p_i^2) (Herfindahl index complement)
    fragmentation = 1.0 - sum((n / total) ** 2 for _, n in sorted_authors)

    return {
        "authors": sorted_authors,
        "total": total,
        "fragmentation": round(fragmentation, 3),
        "main_dev": sorted_authors[0][0] if sorted_authors else "?",
        "last_active": last_active,
    }


@roam_capability(
    name="owner",
    category="reports",
    summary="Show code ownership: who owns a file or directory",
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
@click.argument("path")
@click.pass_context
def owner(ctx, path):
    """Show code ownership: who owns a file or directory.

    Unlike ``codeowners`` (which reads the CODEOWNERS file), this command
    computes actual ownership from git blame history.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    project_root = find_project_root()
    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        dir_files = conn.execute(
            "SELECT id, path FROM files WHERE path LIKE ? ORDER BY path",
            (f"{path}%",),
        ).fetchall()

        if not dir_files:
            frow = conn.execute("SELECT id, path FROM files WHERE path = ?", (path,)).fetchone()
            if frow is None:
                frow = conn.execute(
                    "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{path}",),
                ).fetchone()
            if frow is None:
                # W362: "path not in index" is a valid analytical result,
                # not a failure. Emit a structured envelope and exit 0 —
                # Pattern-2 always-emit discipline (CLAUDE.md). Concrete-
                # noun terminal anchored via the long-sentence rule
                # (LAW 4 rule 5: >4 tokens with non-numeric lead).
                verdict = f"No files match {path!r} — no file indexed at that path"
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "owner",
                                budget=token_budget,
                                summary={
                                    "verdict": verdict,
                                    "state": "path_not_found",
                                    "partial_success": False,
                                    "target_path": path,
                                },
                                target_path=path,
                                state="path_not_found",
                                authors=[],
                                file_count=0,
                            )
                        )
                    )
                    return
                click.echo(f"VERDICT: {verdict}")
                click.echo(f"  Try: roam search {path!r} to find an indexed path")
                return
            dir_files = [frow]

        if json_mode:
            if len(dir_files) == 1:
                info = _ownership_for_file(project_root, dir_files[0]["path"])
                data = {"path": dir_files[0]["path"], "type": "file"}
                if info:
                    data["main_dev"] = info["main_dev"]
                    data["fragmentation"] = info["fragmentation"]
                    data["authors"] = [
                        {
                            "name": a,
                            "lines": n,
                            "pct": round(n * 100 / info["total"]),
                            "last_active": _format_date(info["last_active"].get(a, 0)),
                        }
                        for a, n in info["authors"]
                    ]
                main_dev = data.get("main_dev", "?")
                frag = data.get("fragmentation", 0)
                n_authors = len(info["authors"]) if info else 0
                owner_verdict = f"top owner: {main_dev}, {n_authors} contributor{'s' if n_authors != 1 else ''}, fragmentation={frag}"
                click.echo(
                    to_json(
                        json_envelope(
                            "owner",
                            budget=token_budget,
                            summary={
                                "verdict": owner_verdict,
                                "main_dev": main_dev,
                                "fragmentation": frag,
                            },
                            **data,
                        )
                    )
                )
            else:
                file_ids = [f["id"] for f in dir_files]
                # Batched per-author churn (safe on >999-file directories).
                rows = _dir_author_churn(conn, file_ids)
                top_owner_dir = rows[0]["author"] if rows else "?"
                dir_verdict = f"top owner: {top_owner_dir}, {len(rows)} contributor{'s' if len(rows) != 1 else ''}, {len(dir_files)} files"
                click.echo(
                    to_json(
                        json_envelope(
                            "owner",
                            budget=token_budget,
                            summary={
                                "verdict": dir_verdict,
                                "file_count": len(dir_files),
                                "authors": len(rows),
                            },
                            path=path,
                            type="directory",
                            file_count=len(dir_files),
                            authors=[
                                {
                                    "name": r["author"],
                                    "commits": r["commits"],
                                    "churn": r["churn"] or 0,
                                    "files_touched": r["files_touched"],
                                    "last_active": _format_date(r["last_active"]),
                                }
                                for r in rows
                            ],
                        )
                    )
                )
            return

        if len(dir_files) == 1:
            _show_file_owner(conn, project_root, dir_files[0])
        else:
            _show_dir_owner(conn, project_root, path, dir_files)


def _show_file_owner(conn, project_root, file_row):
    """Show ownership for a single file."""
    file_path = file_row["path"]
    file_id = file_row["id"]

    info = _ownership_for_file(project_root, file_path)
    if info is None:
        click.echo("VERDICT: no blame data available\n")
        click.echo(f"{file_path}")
        click.echo()
        click.echo("  (no blame data available)")
        return

    # Compute bus factor: how many authors to cover 80% of lines
    cumulative = 0
    bus_factor = 0
    for _, lines in info["authors"]:
        cumulative += lines
        bus_factor += 1
        if cumulative >= info["total"] * 0.8:
            break

    top_pct = round(info["authors"][0][1] * 100 / info["total"]) if info["authors"] else 0
    file_verdict = (
        f"top owner: {info['main_dev']} ({top_pct}%), bus factor {bus_factor}, {len(info['authors'])} contributors"
    )
    click.echo(f"VERDICT: {file_verdict}\n")
    click.echo(f"{file_path}")
    click.echo()

    click.echo(f"Main developer: {info['main_dev']}")
    click.echo(f"Bus factor:     {bus_factor} (authors covering 80% of lines)")
    click.echo(f"Fragmentation:  {info['fragmentation']} (0=one owner, 1=many)")
    click.echo()

    rows = []
    for author, lines in info["authors"]:
        pct = f"{lines * 100 / info['total']:.0f}%"
        last = _format_date(info["last_active"].get(author, 0))
        rows.append([author, str(lines), pct, last])
    click.echo(format_table(["Author", "Lines", "Pct", "Last active"], rows))

    # Recent commits touching this file
    recent = conn.execute(
        """SELECT gc.author, gc.message, gc.timestamp
           FROM git_file_changes gfc
           JOIN git_commits gc ON gfc.commit_id = gc.id
           WHERE gfc.file_id = ?
           ORDER BY gc.timestamp DESC LIMIT 5""",
        (file_id,),
    ).fetchall()

    if recent:
        click.echo("\nRecent commits:")
        for r in recent:
            date = _format_date(r["timestamp"])
            msg = r["message"][:60]
            click.echo(f"  {date}  {r['author']}  {msg}")


def _dir_author_churn(conn, file_ids):
    """Per-author git churn across a directory's files, batched to avoid
    SQLITE_MAX_VARIABLE_NUMBER (default 999) on large directories.

    Returns a list of dicts sorted by churn DESC, each with the same columns the
    legacy GROUP BY query produced: ``author`` / ``commits`` / ``churn`` /
    ``last_active`` / ``files_touched``. Distinct-commit counting uses a set
    because one commit can touch files in more than one batch; ``churn`` and the
    file-id set are disjoint per batch so they aggregate exactly.

    caller_metric_definition: commits=distinct git_commits.id, churn=sum of
    lines_added+lines_removed, files_touched=distinct git_file_changes.file_id.
    """
    raw = batched_in(
        conn,
        """SELECT gc.author AS author, gfc.commit_id AS commit_id,
                  gfc.file_id AS file_id,
                  (gfc.lines_added + gfc.lines_removed) AS churn,
                  gc.timestamp AS timestamp
           FROM git_file_changes gfc
           JOIN git_commits gc ON gfc.commit_id = gc.id
           WHERE gfc.file_id IN ({ph})""",
        file_ids,
    )
    agg: dict = {}
    for r in raw:
        a = r["author"]
        e = agg.get(a)
        if e is None:
            e = {"commits": set(), "files": set(), "churn": 0, "last_active": None}
            agg[a] = e
        e["commits"].add(r["commit_id"])
        e["files"].add(r["file_id"])
        e["churn"] += r["churn"] or 0
        ts = r["timestamp"]
        if ts is not None and (e["last_active"] is None or ts > e["last_active"]):
            e["last_active"] = ts
    rows = [
        {
            "author": a,
            "commits": len(e["commits"]),
            "files_touched": len(e["files"]),
            "churn": e["churn"],
            "last_active": e["last_active"],
        }
        for a, e in agg.items()
    ]
    rows.sort(key=lambda r: r["churn"], reverse=True)
    return rows


def _dir_top_churned_files(conn, file_ids, limit=10):
    """Top-churned files in a directory, batched. ``file_stats`` has one row per
    file_id and the file-id set is disjoint per batch, so per-batch rows merge by
    plain concatenation; ORDER BY total_churn DESC + LIMIT are re-applied here."""
    rows = batched_in(
        conn,
        """SELECT f.path AS path, fs.commit_count AS commit_count,
                  fs.total_churn AS total_churn, fs.distinct_authors AS distinct_authors
           FROM file_stats fs
           JOIN files f ON fs.file_id = f.id
           WHERE fs.file_id IN ({ph})""",
        file_ids,
    )
    rows.sort(key=lambda r: r["total_churn"] or 0, reverse=True)
    return rows[:limit]


def _show_dir_owner(conn, project_root, path, dir_files):
    """Show ownership for a directory using stored git data (fast)."""
    file_ids = [f["id"] for f in dir_files]

    # Batched per-author churn aggregation (safe on >999-file directories).
    rows = _dir_author_churn(conn, file_ids)

    if not rows:
        click.echo("VERDICT: no git data available\n")
        click.echo(f"{path}/ ({len(dir_files)} files)")
        click.echo()
        click.echo("  (no git data available)")
        return

    total_churn = sum(r["churn"] or 0 for r in rows)
    main_dev = rows[0]["author"] if rows else "?"

    # Compute bus factor for directory
    cumulative = 0
    bus_factor = 0
    for r in rows:
        cumulative += r["churn"] or 0
        bus_factor += 1
        if cumulative >= total_churn * 0.8:
            break

    dir_verdict = (
        f"top owner: {main_dev}, bus factor {bus_factor}, {len(rows)} contributor{'s' if len(rows) != 1 else ''}"
    )
    click.echo(f"VERDICT: {dir_verdict}\n")
    click.echo(f"{path}/ ({len(dir_files)} files)")
    click.echo()

    click.echo(f"Main developer: {main_dev}")
    click.echo(f"Bus factor:     {bus_factor} (authors covering 80% of churn)")
    click.echo()

    table_rows = []
    for r in rows:
        churn = r["churn"] or 0
        pct = f"{churn * 100 / total_churn:.0f}%" if total_churn else "0%"
        table_rows.append(
            [
                r["author"],
                str(r["commits"]),
                str(r["files_touched"]),
                str(churn),
                pct,
                _format_date(r["last_active"]),
            ]
        )
    click.echo(
        format_table(
            ["Author", "Commits", "Files", "Churn", "Pct", "Last active"],
            table_rows,
            budget=15,
        )
    )

    # Top churned files in this directory (batched; top-10 re-applied in Python)
    churn_rows = _dir_top_churned_files(conn, file_ids, limit=10)

    if churn_rows:
        click.echo("\nTop churned files:")
        tr = []
        for r in churn_rows:
            tr.append(
                [
                    r["path"],
                    str(r["commit_count"]),
                    str(r["total_churn"]),
                    str(r["distinct_authors"]),
                ]
            )
        click.echo(
            format_table(
                ["File", "Commits", "Churn", "Authors"],
                tr,
                budget=10,
            )
        )
