"""Generate a zero-effort onboarding guide for the codebase.

Produces a structured architecture tour: overview, top symbols by
importance, reading order based on topological layers, entry points,
and detected patterns.  Always current because it is computed from
the index, not hand-written documentation.
"""

from __future__ import annotations

import click
import networkx as nx

from roam.db.connection import open_db
from roam.graph.builder import build_symbol_graph
from roam.graph.layers import detect_layers
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


def _top_symbols(conn, G, limit=10):
    """Return the top-N symbols by PageRank with role context."""
    rows = conn.execute(
        """SELECT gm.symbol_id, gm.pagerank, gm.in_degree, gm.out_degree,
                  s.name, s.qualified_name, s.kind, f.path, s.line_start
           FROM graph_metrics gm
           JOIN symbols s ON gm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           ORDER BY gm.pagerank DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    results = []
    for r in rows:
        in_d = r["in_degree"] or 0
        out_d = r["out_degree"] or 0
        if in_d >= 5 and out_d >= 5:
            role = "Hub"
        elif in_d >= 5:
            role = "Core utility"
        elif out_d >= 5:
            role = "Orchestrator"
        elif in_d < 2 and out_d < 2:
            role = "Leaf"
        else:
            role = "Internal"
        results.append({
            "name": r["qualified_name"] or r["name"],
            "kind": abbrev_kind(r["kind"]),
            "role": role,
            "fan_in": in_d,
            "fan_out": out_d,
            "pagerank": round(r["pagerank"] or 0, 4),
            "location": loc(r["path"], r["line_start"]),
        })
    return results


def _reading_order(conn, G):
    """Suggest a reading order based on topological layers (bottom-up)."""
    layer_map = detect_layers(G)
    if not layer_map:
        return []

    # Convert {node_id: layer_num} -> list of sets indexed by layer
    max_layer = max(layer_map.values()) if layer_map else 0
    layers_list = [set() for _ in range(max_layer + 1)]
    for node_id, layer_num in layer_map.items():
        layers_list[layer_num].add(node_id)

    # Collect file paths per layer, ordered by PageRank within each layer
    order = []
    seen_files = set()
    for layer_num, sym_ids in enumerate(layers_list):
        pr_rows = conn.execute(
            "SELECT gm.symbol_id, gm.pagerank FROM graph_metrics gm "
            "WHERE gm.symbol_id IN ({}) ORDER BY gm.pagerank DESC".format(
                ",".join("?" for _ in sym_ids)
            ),
            list(sym_ids),
        ).fetchall() if sym_ids else []

        pr_lookup = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}

        # Get file paths for this layer's symbols
        if not sym_ids:
            continue
        sym_list = list(sym_ids)[:500]
        file_rows = conn.execute(
            "SELECT DISTINCT f.path, s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({})".format(",".join("?" for _ in sym_list)),
            sym_list,
        ).fetchall()

        # Rank files by max PageRank of their symbols in this layer
        file_pr = {}
        for r in file_rows:
            fp = r["path"]
            if fp not in seen_files:
                pr_val = pr_lookup.get(r["id"], 0)
                file_pr[fp] = max(file_pr.get(fp, 0), pr_val)

        for fp in sorted(file_pr, key=file_pr.get, reverse=True)[:5]:
            seen_files.add(fp)
            order.append({
                "layer": layer_num,
                "file": fp,
                "importance": round(file_pr[fp], 4),
            })

    return order


def _entry_points(conn):
    """Fetch entry points as starting exploration targets."""
    rows = conn.execute(
        """SELECT s.name, s.qualified_name, s.kind, f.path, s.line_start
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
           WHERE (gm.in_degree IS NULL OR gm.in_degree = 0)
           AND s.kind IN ('function', 'method', 'class')
           AND s.is_exported = 1
           ORDER BY gm.pagerank DESC
           LIMIT 15"""
    ).fetchall()
    return [
        {
            "name": r["qualified_name"] or r["name"],
            "kind": abbrev_kind(r["kind"]),
            "location": loc(r["path"], r["line_start"]),
        }
        for r in rows
    ]


def _language_breakdown(conn):
    """Get language distribution."""
    rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files "
        "WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    return [{"language": r["language"], "files": r["cnt"]} for r in rows]


def _patterns(conn):
    """Detect high-level patterns from the graph."""
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # Test file ratio
    test_files = conn.execute(
        "SELECT COUNT(*) FROM files WHERE path LIKE '%test%' OR path LIKE '%spec%'"
    ).fetchone()[0]

    # Health score
    health_row = conn.execute(
        "SELECT AVG(health_score) as avg_hs FROM file_stats "
        "WHERE health_score IS NOT NULL"
    ).fetchone()
    avg_health = round(health_row["avg_hs"], 1) if health_row and health_row["avg_hs"] else None

    return {
        "files": total_files,
        "symbols": total_symbols,
        "edges": total_edges,
        "test_files": test_files,
        "test_ratio": round(test_files / total_files * 100, 1) if total_files else 0,
        "avg_file_health": avg_health,
    }


@click.command()
@click.option("--write", "write_file", default=None, type=click.Path(),
              help="Write the tour to a Markdown file instead of stdout")
@click.pass_context
def tour(ctx, write_file):
    """Generate a codebase onboarding tour.

    Produces a structured guide: project overview, top symbols to learn,
    suggested reading order, entry points, and codebase statistics.
    Always current because it is derived from the index.

    Use --write to save to a file:

        roam tour --write ONBOARDING.md
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        langs = _language_breakdown(conn)
        stats = _patterns(conn)
        top = _top_symbols(conn, G, limit=10)
        order = _reading_order(conn, G)
        entries = _entry_points(conn)

        if json_mode:
            click.echo(to_json(json_envelope("tour",
                summary={
                    "files": stats["files"],
                    "symbols": stats["symbols"],
                    "languages": len(langs),
                    "top_symbols": len(top),
                },
                languages=langs,
                statistics=stats,
                top_symbols=top,
                reading_order=order,
                entry_points=entries,
            )))
            return

        lines = []
        lines.append("# Codebase Tour\n")

        # Overview
        lines.append("## Overview\n")
        lang_str = ", ".join(f"{l['language']} ({l['files']})" for l in langs[:5])
        lines.append(f"**Languages:** {lang_str}")
        lines.append(f"**Size:** {stats['files']} files, {stats['symbols']} symbols, {stats['edges']} dependency edges")
        lines.append(f"**Tests:** {stats['test_files']} test files ({stats['test_ratio']}% of codebase)")
        if stats["avg_file_health"]:
            lines.append(f"**Avg file health:** {stats['avg_file_health']}/10")
        lines.append("")

        # Top symbols
        lines.append("## Key Symbols (learn these first)\n")
        lines.append(f"{'Symbol':<40} {'Kind':<6} {'Role':<14} {'Fan-in':<8} {'Location'}")
        lines.append(f"{'-'*40} {'-'*6} {'-'*14} {'-'*8} {'-'*30}")
        for s in top:
            lines.append(
                f"{s['name']:<40} {s['kind']:<6} {s['role']:<14} "
                f"{s['fan_in']:<8} {s['location']}"
            )
        lines.append("")

        # Reading order
        if order:
            lines.append("## Suggested Reading Order\n")
            lines.append("Start from the foundation (layer 0) and work upward:\n")
            current_layer = -1
            for item in order:
                if item["layer"] != current_layer:
                    current_layer = item["layer"]
                    lines.append(f"\n**Layer {current_layer}** ({'foundation' if current_layer == 0 else 'builds on layer ' + str(current_layer - 1)}):")
                lines.append(f"  - {item['file']}")
            lines.append("")

        # Entry points
        if entries:
            lines.append("## Entry Points (start exploring here)\n")
            for e in entries:
                lines.append(f"  - `{e['name']}` ({e['kind']}) at {e['location']}")
            lines.append("")

        # Tips
        lines.append("## Next Steps\n")
        lines.append("- `roam search <pattern>` — find any symbol by name")
        lines.append("- `roam context <symbol>` — get files and line ranges to read")
        lines.append("- `roam why <symbol>` — understand why a symbol matters")
        lines.append("- `roam preflight <symbol>` — safety check before modifying")
        lines.append("")

        output = "\n".join(lines)

        if write_file:
            with open(write_file, "w", encoding="utf-8") as f:
                f.write(output)
            click.echo(f"Tour written to {write_file}")
        else:
            click.echo(output)
