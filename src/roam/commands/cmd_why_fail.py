"""``roam why-fail <test>`` — triage helper for failing tests.

given a failing test path or symbol, find recently-changed
symbols that the test transitively reaches via the call graph. The
top suspects are the ones that:

  1. Are reachable from the test in ``--max-hops`` hops.
  2. Have been touched by recent commits (last ``--days`` days).

Sorted by recency × hop distance × PageRank.
"""

from __future__ import annotations

import time

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@roam_capability(
    name="why-fail",
    category="workflow",
    summary="Find recently-changed symbols transitively reached by a failing test",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "debug"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="why-fail")
@click.argument("target")
@click.option("--days", type=int, default=14, show_default=True, help="Look back N days for commits.")
@click.option("--max-hops", type=int, default=5, show_default=True, help="BFS depth from the test.")
@click.option("--limit", type=int, default=10, show_default=True, help="Max suspects to surface.")
@click.pass_context
def why_fail(ctx, target, days, max_hops, limit) -> None:
    """Find recently-changed symbols transitively reached by a failing test."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    cutoff_ts = int(time.time()) - days * 86400

    with open_db(readonly=True) as conn:
        # Resolve target → symbols. If a path was passed, use all symbols in
        # the file; otherwise treat as a symbol name lookup.
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE f.path = ? OR s.name = ? OR s.qualified_name = ? "
            "ORDER BY s.line_start LIMIT 50",
            (target, target, target),
        ).fetchall()
        if not rows:
            verdict = f"no symbol or file '{target}' in index"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "why-fail",
                            summary={"verdict": verdict, "suspect_count": 0},
                            suspects=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        try:
            import networkx as nx

            from roam.graph.builder import build_symbol_graph
        except ImportError:
            click.echo("Graph module not available. Run `roam index` to build the dependency graph.")
            return

        G = build_symbol_graph(conn)

        # BFS reach from the seed symbols.
        seeds = [r["id"] for r in rows if r["id"] in G]
        reach: set[int] = set()
        for s in seeds:
            try:
                lengths = nx.single_source_shortest_path_length(G, s, cutoff=int(max_hops))
                for n in lengths:
                    if n != s:
                        reach.add(n)
            except Exception:
                continue

        if not reach:
            verdict = f"no reachable symbols from '{target}' within {max_hops} hop(s)"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "why-fail",
                            summary={"verdict": verdict, "suspect_count": 0},
                            suspects=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        # Among reachable symbols, find the ones whose file was touched
        # recently. Use ``batched_in`` so SQLite's parameter limit
        # (typically 999) doesn't bite on large reach sets.
        from roam.db.connection import batched_in

        rows_changed = batched_in(
            conn,
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path, "
            "       COALESCE(gm.pagerank, 0) AS pr, "
            "       MAX(c.timestamp) AS last_touch "
            "  FROM symbols s "
            "  JOIN files f ON f.id = s.file_id "
            "  LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
            "  JOIN git_file_changes gfc ON gfc.file_id = s.file_id "
            "  JOIN git_commits c ON c.id = gfc.commit_id "
            " WHERE s.id IN ({ph}) AND c.timestamp >= ? "
            " GROUP BY s.id ORDER BY last_touch DESC",
            list(reach),
            post=(cutoff_ts,),
        )

    suspects = []
    seed_set = set(seeds)
    for r in rows_changed:
        # Skip the seed itself when it was in the reach (shouldn't be, but defensive).
        if r["id"] in seed_set:
            continue
        suspects.append(
            {
                "name": r["name"],
                "qualified_name": r["qualified_name"],
                "kind": r["kind"],
                "file": r["path"],
                "last_touched": r["last_touch"],
                "pagerank": round(float(r["pr"] or 0), 6),
            }
        )
    suspects.sort(
        key=lambda s: (-(s["last_touched"] or 0), -s["pagerank"]),
    )
    suspects = suspects[: max(1, int(limit))]

    verdict = (
        f"OK — no recent changes (last {days}d) reachable from '{target}'"
        if not suspects
        else f"{len(suspects)} suspect symbol(s) changed in last {days}d, reachable from '{target}'"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "why-fail",
                    summary={
                        "verdict": verdict,
                        "suspect_count": len(suspects),
                        "max_hops": max_hops,
                        "days": days,
                    },
                    suspects=suspects,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not suspects:
        return
    click.echo()
    click.echo(f"{'Name':<36}  {'Kind':<6}  {'PR':>8}  File:Line")
    click.echo(f"{'-' * 36}  {'-' * 6}  {'-' * 8}  {'-' * 30}")
    for s in suspects:
        from datetime import datetime

        try:
            day = datetime.fromtimestamp(int(s["last_touched"])).strftime("%Y-%m-%d")
        except Exception:
            day = "?"
        loc_str = f"{s['file']} ({day})"
        click.echo(f"{s['name'][:36]:<36}  {s['kind'][:6]:<6}  {s['pagerank']:>8.5f}  {loc_str}")
