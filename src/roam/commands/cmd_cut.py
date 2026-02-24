"""Minimum cut analysis — find fragile domain boundaries."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command("cut")
@click.option("--between", nargs=2, default=None, help="Analyze boundary between two clusters")
@click.option("--leak-edges", "leak_edges", is_flag=True, help="Focus on leak edge analysis")
@click.option("--top", "top_n", default=10, type=int, help="Show top N boundaries")
@click.pass_context
def cut(ctx, between, leak_edges, top_n):
    """Minimum cut analysis — find fragile domain boundaries.

    Computes minimum edge cuts between architectural clusters to identify
    the thinnest (most fragile) boundaries and the highest-impact "leak
    edges" whose removal would best improve domain isolation.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.clusters import detect_clusters, label_clusters
            import networkx as nx
        except ImportError:
            click.echo("VERDICT: NetworkX required for cut analysis")
            return

        G = build_symbol_graph(conn)

        if len(G) < 2:
            verdict = "No cross-cluster boundaries found"
            if json_mode:
                click.echo(to_json(json_envelope("cut",
                    summary={
                        "verdict": verdict,
                        "boundaries_analyzed": 0,
                        "fragile_boundaries": 0,
                        "leak_edges_found": 0,
                    },
                    boundaries=[],
                    leak_edges=[],
                )))
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        clusters = detect_clusters(G)
        labels = label_clusters(clusters, conn)

        # Build undirected version for min-cut
        UG = G.to_undirected()

        # Group nodes by cluster
        cluster_nodes: dict[int, set] = {}
        for node_id, cid in clusters.items():
            cluster_nodes.setdefault(cid, set()).add(node_id)

        # Need at least 2 clusters for boundary analysis
        cluster_ids = sorted(cluster_nodes.keys())

        boundaries: list[dict] = []

        for i, c1 in enumerate(cluster_ids):
            for c2 in cluster_ids[i + 1:]:
                if between:
                    # Filter to requested pair
                    l1 = labels.get(c1, str(c1)).lower()
                    l2 = labels.get(c2, str(c2)).lower()
                    if not (
                        (between[0].lower() in l1 and between[1].lower() in l2)
                        or (between[1].lower() in l1 and between[0].lower() in l2)
                    ):
                        continue

                # Count cross-edges
                cross_edges: list[tuple] = []
                nodes_c1 = cluster_nodes.get(c1, set())
                nodes_c2 = cluster_nodes.get(c2, set())
                for u, v in G.edges():
                    if (u in nodes_c1 and v in nodes_c2) or (u in nodes_c2 and v in nodes_c1):
                        cross_edges.append((u, v))

                if not cross_edges:
                    continue

                # Find a pair of connected nodes across clusters for min-cut seed
                src_node = None
                tgt_node = None
                for u, v in cross_edges:
                    if u in nodes_c1 and v in nodes_c2:
                        src_node, tgt_node = u, v
                        break
                    elif u in nodes_c2 and v in nodes_c1:
                        src_node, tgt_node = u, v
                        break

                if src_node is None:
                    continue

                try:
                    min_cut_edges = nx.minimum_edge_cut(UG, src_node, tgt_node)
                    min_cut_size = len(min_cut_edges)
                except (nx.NetworkXError, nx.NetworkXUnbounded, nx.exception.NetworkXError):
                    min_cut_edges = set()
                    min_cut_size = len(cross_edges)

                thinness = min_cut_size / len(cross_edges) if cross_edges else 1.0
                fragile = thinness < 0.4

                # Get edge details for cut edges
                cut_details: list[dict] = []
                for u, v in sorted(min_cut_edges):
                    u_node = G.nodes.get(u, {})
                    v_node = G.nodes.get(v, {})
                    cut_details.append({
                        "source": u_node.get("name", str(u)),
                        "source_file": u_node.get("file_path", ""),
                        "target": v_node.get("name", str(v)),
                        "target_file": v_node.get("file_path", ""),
                    })

                boundaries.append({
                    "cluster_a": labels.get(c1, f"cluster_{c1}"),
                    "cluster_b": labels.get(c2, f"cluster_{c2}"),
                    "cross_edges": len(cross_edges),
                    "min_cut": min_cut_size,
                    "thinness": round(thinness, 2),
                    "fragile": fragile,
                    "cut_edges": cut_details[:5],
                })

        # Sort by thinness (most fragile first)
        boundaries.sort(key=lambda b: b["thinness"])
        boundaries = boundaries[:top_n]

        # Leak edge analysis
        leak_edge_list: list[dict] = []
        if leak_edges or not between:
            try:
                ebc = nx.edge_betweenness_centrality(UG)
            except Exception:
                ebc = {}

            for (u, v), bc in sorted(ebc.items(), key=lambda x: -x[1]):
                u_cluster = clusters.get(u)
                v_cluster = clusters.get(v)
                if (
                    u_cluster is not None
                    and v_cluster is not None
                    and u_cluster != v_cluster
                ):
                    u_node = G.nodes.get(u, {})
                    v_node = G.nodes.get(v, {})
                    leak_edge_list.append({
                        "source": u_node.get("name", str(u)),
                        "source_file": u_node.get("file_path", ""),
                        "target": v_node.get("name", str(v)),
                        "target_file": v_node.get("file_path", ""),
                        "betweenness": round(bc, 4),
                        "suggestion": (
                            f"Extract interface between "
                            f"{u_node.get('name', str(u))} and "
                            f"{v_node.get('name', str(v))}"
                        ),
                    })
                    if len(leak_edge_list) >= top_n:
                        break

        # Verdict
        fragile_count = sum(1 for b in boundaries if b["fragile"])
        total_boundaries = len(boundaries)
        if total_boundaries == 0:
            verdict = "No cross-cluster boundaries found"
        elif fragile_count == 0:
            verdict = f"{total_boundaries} boundaries analyzed, all adequately isolated"
        else:
            verdict = f"{total_boundaries} boundaries analyzed, {fragile_count} fragile"

        # JSON output
        if json_mode:
            click.echo(to_json(json_envelope("cut",
                summary={
                    "verdict": verdict,
                    "boundaries_analyzed": total_boundaries,
                    "fragile_boundaries": fragile_count,
                    "leak_edges_found": len(leak_edge_list),
                },
                boundaries=boundaries,
                leak_edges=leak_edge_list,
            )))
            return

        # Text output — verdict first
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        for b in boundaries:
            tag = "<< FRAGILE" if b["fragile"] else "(adequate)"
            click.echo(f"BOUNDARY: {b['cluster_a']} <-> {b['cluster_b']}")
            click.echo(
                f"  Cross-edges: {b['cross_edges']} | "
                f"Min cut: {b['min_cut']} edges | "
                f"Thinness: {b['thinness']}  {tag}"
            )
            if b["cut_edges"]:
                click.echo("  Cut edges:")
                for ce in b["cut_edges"]:
                    click.echo(
                        f"    {ce['source_file']}::{ce['source']} -> "
                        f"{ce['target_file']}::{ce['target']}"
                    )
            click.echo()

        if leak_edge_list:
            click.echo("LEAK EDGES (highest blast-radius amplification):")
            for i, le in enumerate(leak_edge_list[:5], 1):
                click.echo(
                    f"  {i}. {le['source_file']}::{le['source']} -> "
                    f"{le['target_file']}::{le['target']}"
                )
                click.echo(f"     Edge betweenness: {le['betweenness']}")
                click.echo(f"     Suggest: {le['suggestion']}")
