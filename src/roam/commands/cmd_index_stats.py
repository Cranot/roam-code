"""``roam index-stats`` — index size, row counts, fragmentation.

redactedsurfaces the size of the on-disk index plus a SQLite
fragmentation heuristic (free_pages / page_count). When fragmentation
exceeds ~25%, suggest ``VACUUM`` (or ``roam reset`` for a full
rebuild). Useful when an index has grown after a long-lived dev
session and the user wonders if it's bloated.
"""

from __future__ import annotations

import os

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import get_db_path, open_db
from roam.output.formatter import json_envelope, to_json


def _humanize(bytes_: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(bytes_)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} {units[-1]}"


@click.command(name="index-stats")
@click.pass_context
def index_stats(ctx) -> None:
    """Report .roam index size, row counts, and fragmentation."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    db_path = get_db_path()
    try:
        size_bytes = os.path.getsize(db_path)
    except OSError:
        size_bytes = 0

    table_counts: dict[str, int] = {}
    with open_db(readonly=True) as conn:
        for table in (
            "files",
            "symbols",
            "edges",
            "git_commits",
            "git_file_changes",
            "graph_metrics",
            "symbol_metrics",
            "clusters",
        ):
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                table_counts[table] = row[0] if row else 0
            except Exception:
                table_counts[table] = 0
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]

    frag_pct = (free_pages / page_count * 100.0) if page_count else 0.0
    threshold_mb = float(os.environ.get("ROAM_INDEX_SIZE_WARN_MB", "200"))
    size_mb = size_bytes / (1024 * 1024)

    bloat = frag_pct >= 25.0
    oversized = size_mb >= threshold_mb
    if bloat and oversized:
        verdict = f"{_humanize(size_bytes)} index, {frag_pct:.1f}% fragmented — run `roam reset` to rebuild compactly."
    elif bloat:
        verdict = f"{_humanize(size_bytes)} index, {frag_pct:.1f}% fragmented — VACUUM recommended."
    elif oversized:
        verdict = (
            f"{_humanize(size_bytes)} index exceeds {int(threshold_mb)} MB threshold; "
            "consider exclude patterns or `roam clean`."
        )
    else:
        verdict = f"OK — {_humanize(size_bytes)} index, {frag_pct:.1f}% fragmentation"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "index-stats",
                    summary={
                        "verdict": verdict,
                        "size_bytes": size_bytes,
                        "fragmentation_pct": round(frag_pct, 2),
                        "size_warn_mb": threshold_mb,
                    },
                    db_path=str(db_path),
                    table_counts=table_counts,
                    page_size=page_size,
                    page_count=page_count,
                    free_pages=free_pages,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"DB path:        {db_path}")
    click.echo(f"Size:           {_humanize(size_bytes)}")
    click.echo(f"Pages:          {page_count} ({free_pages} free, {frag_pct:.1f}%)")
    click.echo(f"Page size:      {page_size} bytes")
    click.echo()
    click.echo("Table          Rows")
    click.echo("-------------  ----------")
    for tbl, n in table_counts.items():
        click.echo(f"{tbl:<13}  {n:>10}")
