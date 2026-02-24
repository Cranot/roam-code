"""Show topological layer detection and violations."""

from __future__ import annotations

from collections import Counter

import click

from roam.db.connection import open_db, batched_in
from roam.graph.builder import build_symbol_graph
from roam.graph.layers import detect_layers, find_violations, format_layers
from roam.output.formatter import abbrev_kind, loc, format_table, truncate_lines, to_json, json_envelope, summary_envelope
from roam.output.mermaid import sanitize_id, node as mnode, edge as medge, subgraph as msubgraph, diagram as mdiagram
from roam.commands.resolve import ensure_index

import networkx as nx


_DIR_SQL = (
    "SELECT CASE WHEN INSTR(REPLACE(f.path, '\\', '/'), '/') > 0 "
    "THEN SUBSTR(REPLACE(f.path, '\\', '/'), 1, INSTR(REPLACE(f.path, '\\', '/'), '/') - 1) "
    "ELSE '.' END as dir "
    "FROM symbols s JOIN files f ON s.file_id = f.id "
    "WHERE s.id IN ({ph})"
)


def _layer_dir_breakdown(conn, layer_map, layer_num):
    """Return top-5 directory counts for symbols in a given layer."""
    lids = [nid for nid, lv in layer_map.items() if lv == layer_num]
    if not lids:
        return []
    dr = batched_in(conn, _DIR_SQL, lids)
    return Counter(r["dir"] for r in dr).most_common(5)


def _layers_json(conn, formatted, layer_map, max_layer, violations, mermaid=None, detail=True, token_budget=0):
    """Emit JSON output for layers command."""
    v_lookup = {}
    if violations:
        all_ids = list({v["source"] for v in violations} | {v["target"] for v in violations})
        for r in batched_in(
            conn,
            "SELECT s.id, s.name, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
            all_ids,
        ):
            v_lookup[r["id"]] = r

    layer_dirs = {}
    for l in formatted:
        if len(l["symbols"]) > 50:
            dir_counts = _layer_dir_breakdown(conn, layer_map, l["layer"])
            if dir_counts:
                layer_dirs[l["layer"]] = [
                    {"dir": d, "count": c} for d, c in dir_counts
                ]

    extra = {}
    if mermaid is not None:
        extra["mermaid"] = mermaid

    envelope = json_envelope("layers",
        summary={
            "total_layers": max_layer + 1,
            "violations": len(violations),
        },
        budget=token_budget,
        total_layers=max_layer + 1,
        layers=[
            {
                "layer": l["layer"],
                "symbol_count": len(l["symbols"]),
                "directories": layer_dirs.get(l["layer"], []),
                "symbols": [
                    {"name": s["name"], "kind": s["kind"]}
                    for s in l["symbols"][:50]
                ],
            }
            for l in formatted
        ],
        violations=[
            {
                "source": v_lookup.get(v["source"], {}).get("name", "?"),
                "source_layer": v["source_layer"],
                "target": v_lookup.get(v["target"], {}).get("name", "?"),
                "target_layer": v["target_layer"],
            }
            for v in violations
        ],
        **extra,
    )
    if not detail:
        envelope = summary_envelope(envelope)
    click.echo(to_json(envelope))


def _print_layer_detail(conn, layer_map, layer_info):
    """Print detail for a single layer (directory breakdown or symbol list)."""
    n = layer_info["layer"]
    symbols = layer_info["symbols"]
    if len(symbols) > 50:
        label = " base layer (no dependencies)" if n == 0 else ""
        click.echo(f"\n  Layer {n} ({len(symbols)} symbols):{label}")
        layer_ids = [nid for nid, l in layer_map.items() if l == n]
        if layer_ids:
            dir_counts = _layer_dir_breakdown(conn, layer_map, n)
            if dir_counts:
                parts = [f"{d}/ {c * 100 / len(layer_ids):.0f}%" for d, c in dir_counts]
                click.echo(f"    Dirs: {', '.join(parts)}")
            top_syms = batched_in(
                conn,
                "SELECT s.name, s.kind, COALESCE(gm.pagerank, 0) as pr "
                "FROM symbols s "
                "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
                "WHERE s.id IN ({ph})",
                layer_ids,
            )
            if top_syms:
                top_syms.sort(key=lambda r: r["pr"], reverse=True)
                names = [f"{abbrev_kind(s['kind'])} {s['name']}" for s in top_syms[:5]]
                click.echo(f"    Top: {', '.join(names)}")
    else:
        names = [f"{abbrev_kind(s['kind'])} {s['name']}" for s in symbols]
        preview = truncate_lines(names, 10)
        click.echo(f"\n  Layer {n} ({len(symbols)} symbols):")
        for line in preview:
            click.echo(f"    {line}")


def _print_deepest_chain(conn, G):
    """Print the deepest dependency chain from DAG condensation."""
    try:
        condensation = nx.condensation(G)
        longest = nx.dag_longest_path(condensation)
        if len(longest) <= 1:
            return
        scc_members = condensation.graph.get("mapping", {})
        scc_to_nodes: dict[int, list] = {}
        for orig_node, scc_id in scc_members.items():
            scc_to_nodes.setdefault(scc_id, []).append(orig_node)

        chain_ids = []
        for scc_id in longest:
            members = scc_to_nodes.get(scc_id, [])
            if members:
                best = max(members, key=lambda n: G.degree(n) if n in G else 0)
                chain_ids.append(best)

        if not chain_ids:
            return
        ph = ",".join("?" for _ in chain_ids)
        chain_rows = conn.execute(
            f"SELECT s.id, s.name, s.kind, f.path as file_path "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph})",
            chain_ids,
        ).fetchall()
        chain_lookup = {r["id"]: r for r in chain_rows}
        click.echo(f"\n  Deepest dependency chain ({len(chain_ids)} levels):")
        for i, cid in enumerate(chain_ids):
            info = chain_lookup.get(cid)
            if info:
                arrow = "    " if i == 0 else "  -> "
                click.echo(f"  {arrow}{abbrev_kind(info['kind'])} {info['name']}  {loc(info['file_path'])}")
    except Exception:
        pass


def _layers_mermaid(conn, G, layer_map, formatted, max_layer):
    """Generate a Mermaid top-down flowchart for architecture layers.

    Shows up to 6 files per layer (ranked by PageRank) and inter-layer
    dependency edges.  Returns the diagram as a string.
    """
    elements: list[str] = []
    files_per_layer = 6

    # Collect top files per layer by PageRank
    layer_files: dict[int, list[dict]] = {}
    for layer_info in formatted:
        layer_num = layer_info["layer"]
        sym_ids = [nid for nid, lv in layer_map.items() if lv == layer_num]
        if not sym_ids:
            continue
        rows = batched_in(
            conn,
            "SELECT DISTINCT f.path, MAX(COALESCE(gm.pagerank, 0)) as pr "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "WHERE s.id IN ({ph}) "
            "GROUP BY f.path ORDER BY pr DESC",
            sym_ids,
        )
        top_files = [{"path": r["path"].replace("\\", "/"), "pr": r["pr"]}
                     for r in rows[:files_per_layer]]
        if top_files:
            layer_files[layer_num] = top_files

    # Build subgraphs for each layer
    for layer_num in sorted(layer_files):
        label = "Foundation" if layer_num == 0 else f"Layer {layer_num}"
        node_lines = []
        for f in layer_files[layer_num]:
            short = f["path"].rsplit("/", 1)[-1] if "/" in f["path"] else f["path"]
            node_lines.append(mnode(f["path"], short))
        elements.append(msubgraph(f"Layer {layer_num} - {label}", node_lines))

    # Add edges: for each file in a higher layer, find files it depends on
    # in lower layers.  Use the symbol graph to derive file-level deps.
    file_to_layer: dict[str, int] = {}
    for layer_num, files in layer_files.items():
        for f in files:
            file_to_layer[f["path"]] = layer_num

    # Build file-level adjacency from the symbol graph
    node_file: dict[int, str] = {}
    for nid in G.nodes:
        data = G.nodes[nid]
        fp = data.get("file_path", "").replace("\\", "/")
        if fp in file_to_layer:
            node_file[nid] = fp

    seen_edges: set[tuple[str, str]] = set()
    for src, tgt in G.edges:
        sf = node_file.get(src)
        tf = node_file.get(tgt)
        if sf and tf and sf != tf:
            sl = file_to_layer.get(sf)
            tl = file_to_layer.get(tf)
            # Only draw downward edges (higher layer -> lower layer)
            if sl is not None and tl is not None and sl > tl:
                pair = (sf, tf)
                if pair not in seen_edges:
                    seen_edges.add(pair)
                    elements.append(medge(sf, tf))

    return mdiagram("TD", elements)


def _print_violations(conn, violations):
    """Print layer violation table."""
    click.echo(f"\n=== Violations ({len(violations)}) ===")
    if not violations:
        click.echo("  (none -- clean layering)")
        return
    all_ids = list({v["source"] for v in violations} | {v["target"] for v in violations})
    lookup = {}
    for r in batched_in(
        conn,
        "SELECT s.id, s.name, s.kind, f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        all_ids,
    ):
        lookup[r["id"]] = r

    v_rows = []
    for v in violations[:30]:
        src = lookup.get(v["source"], {})
        tgt = lookup.get(v["target"], {})
        v_rows.append([
            src.get("name", "?"),
            f"L{v['source_layer']}",
            "->",
            tgt.get("name", "?"),
            f"L{v['target_layer']}",
            loc(src.get("file_path", "?")),
        ])
    click.echo(format_table(
        ["Source", "Layer", "", "Target", "Layer", "File"],
        v_rows,
        budget=30,
    ))
    if len(violations) > 30:
        click.echo(f"  (+{len(violations) - 30} more)")


@click.command()
@click.option('--mermaid', 'mermaid_mode', is_flag=True, help='Output Mermaid diagram')
@click.pass_context
def layers(ctx, mermaid_mode):
    """Show dependency layers and violations."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    detail = ctx.obj.get('detail', False) if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    ensure_index()
    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)
        layer_map = detect_layers(G)

        if not layer_map:
            if json_mode:
                click.echo(to_json(json_envelope("layers",
                    summary={"total_layers": 0, "violations": 0},
                    budget=token_budget,
                    layers=[], violations=[],
                )))
            else:
                click.echo("No layers detected (graph is empty).")
            return

        formatted = format_layers(layer_map, conn)
        max_layer = max(l["layer"] for l in formatted) if formatted else 0
        violations = find_violations(G, layer_map)

        if mermaid_mode:
            mermaid_text = _layers_mermaid(conn, G, layer_map, formatted, max_layer)
            if json_mode:
                _layers_json(conn, formatted, layer_map, max_layer, violations,
                             mermaid=mermaid_text, detail=detail, token_budget=token_budget)
            else:
                click.echo(mermaid_text)
            return

        if json_mode:
            _layers_json(conn, formatted, layer_map, max_layer, violations, detail=detail, token_budget=token_budget)
            return

        total_symbols = sum(len(l["symbols"]) for l in formatted)
        layer0_count = next((len(l["symbols"]) for l in formatted if l["layer"] == 0), 0)
        layer0_pct = layer0_count * 100 / total_symbols if total_symbols else 0

        click.echo(f"=== Layers ({max_layer + 1} levels) ===")

        if max_layer <= 1:
            shape = "Flat (no layering)"
        elif layer0_pct > 80:
            shape = f"Flat ({layer0_pct:.0f}% in Layer 0)"
        elif layer0_pct > 50:
            shape = f"Moderate ({layer0_pct:.0f}% in Layer 0, {max_layer + 1} levels)"
        else:
            shape = f"Well-layered ({max_layer + 1} levels, even distribution)"
        click.echo(f"  Architecture: {shape}")
        click.echo(f"  Violations: {len(violations)}")

        if not detail:
            # Summary mode: just show layer count + verdict
            click.echo("(use --detail for per-layer symbol breakdown and violation list)")
            return

        for layer_info in formatted:
            _print_layer_detail(conn, layer_map, layer_info)

        if max_layer >= 1:
            _print_deepest_chain(conn, G)

        _print_violations(conn, violations)
