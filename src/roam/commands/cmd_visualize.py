"""Generate Mermaid or DOT architecture diagrams from the codebase graph.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because visualize outputs are invocation-scoped topology
visualization documents — not per-location violations. Editor consumers
should use the JSON envelope directly. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1148 audit
memo.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import networkx as nx

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.graph.builder import build_file_graph, build_symbol_graph
from roam.graph.clusters import detect_clusters, label_clusters
from roam.graph.cycles import find_cycles
from roam.graph.pagerank import compute_pagerank
from roam.output.formatter import json_envelope, resolution_disclosure, to_json

# -- Node shape helpers -------------------------------------------------------

_CLASS_KINDS = {"class", "struct", "interface", "enum", "trait"}
_FUNC_KINDS = {"function", "method", "constructor"}


def _escape_mermaid(text: str) -> str:
    """Remove characters that break Mermaid syntax."""
    return re.sub(r'["\[\](){}|<>#]', "", text)


def _mermaid_node(node_id: int, label: str, kind: str, is_file: bool) -> str:
    """Return a Mermaid node definition with shape based on kind."""
    nid = f"n{node_id}"
    safe = _escape_mermaid(label)
    if is_file:
        return f'    {nid}["{safe}"]:::fileNode'
    if kind in _CLASS_KINDS:
        return f'    {nid}["{safe}"]:::classNode'
    if kind in _FUNC_KINDS:
        return f'    {nid}("{safe}"):::funcNode'
    return f'    {nid}["{safe}"]'


def _dot_node(node_id: int, label: str, kind: str, is_file: bool) -> str:
    """Return a DOT node definition."""
    nid = f"n{node_id}"
    safe = label.replace('"', '\\"')
    if is_file:
        return f'    {nid} [label="{safe}", shape=box, style=filled, fillcolor="#e0e0e0"];'
    if kind in _CLASS_KINDS:
        return f'    {nid} [label="{safe}", shape=box, style=filled, fillcolor="#d0e8ff"];'
    if kind in _FUNC_KINDS:
        return f'    {nid} [label="{safe}", shape=ellipse, style=filled, fillcolor="#d0ffd0"];'
    return f'    {nid} [label="{safe}"];'


# -- Subgraph filtering -------------------------------------------------------


def _filter_by_focus(G: nx.DiGraph, conn, focus_name: str, depth: int) -> tuple[nx.DiGraph, str, str]:
    """BFS neighborhood around a focal symbol.

    W1245 Pattern-2 variant-D: returns ``(subG, resolution_tier,
    resolved_target)`` so the caller can disclose which resolver tier
    matched the focal symbol. ``resolution_tier`` is one of
    ``{"symbol", "fuzzy"}``; ``resolved_target`` is the qualified name
    the resolver actually landed on (may differ from the input on a
    fuzzy LIKE-fallback). Unresolved focus still raises ClickException
    so the caller can convert to a structured envelope.
    """
    import networkx as nx

    sym = find_symbol(conn, focus_name)
    if sym is None:
        raise click.ClickException(
            f'Symbol not found: "{focus_name}"\n  Tip: Run `roam search {focus_name}` to find similar symbols.'
        )
    sid = sym["id"]
    if sid not in G:
        raise click.ClickException(
            f"Symbol '{focus_name}' (id={sid}) exists in the index but is not in the graph.\n"
            "  Tip: Run `roam index` to rebuild the graph."
        )
    tier = sym.get("_resolution_tier", "symbol")
    resolved_name = sym.get("qualified_name") or sym["name"]
    return nx.ego_graph(G, sid, radius=depth, undirected=True), tier, resolved_name


def _filter_by_pagerank(G: nx.DiGraph, limit: int) -> nx.DiGraph:
    """Keep the top-N nodes by PageRank and all edges between them.

    Filters framework type aliases (``computed<T>``, ``useState<T>``)
    before truncation so the visualisation centres on real architectural
    anchors rather than type-system noise.
    """
    from roam.output.framework_filter import is_framework_alias

    if len(G) <= limit:
        return G
    pr = compute_pagerank(G)
    ordered = sorted(pr, key=pr.get, reverse=True)
    top_ids: list[int] = []
    for nid in ordered:
        node = G.nodes[nid]
        if is_framework_alias(
            node.get("qualified_name") or node.get("name"),
            node.get("kind"),
            node.get("file_path"),
        ):
            continue
        top_ids.append(nid)
        if len(top_ids) >= limit:
            break
    return G.subgraph(top_ids).copy()


# -- Cycle edge detection -----------------------------------------------------


def _cycle_edges(G: nx.DiGraph) -> set[tuple[int, int]]:
    """Return the set of edges that participate in strongly connected components."""
    sccs = find_cycles(G, min_size=2)
    scc_nodes = set()
    for scc in sccs:
        scc_nodes.update(scc)
    edges = set()
    for u, v in G.edges():
        if u in scc_nodes and v in scc_nodes:
            # Check both are in the same SCC
            for scc in sccs:
                scc_set = set(scc)
                if u in scc_set and v in scc_set:
                    edges.add((u, v))
                    break
    return edges


# -- Mermaid generation --------------------------------------------------------


def _generate_mermaid(
    G: nx.DiGraph,
    conn,
    direction: str,
    use_clusters: bool,
    is_file_level: bool,
) -> str:
    """Generate Mermaid diagram text from a filtered graph."""
    lines: list[str] = [f"graph {direction}"]

    # Style definitions
    lines.append("    classDef classNode fill:#d0e8ff,stroke:#336")
    lines.append("    classDef funcNode fill:#d0ffd0,stroke:#363")
    lines.append("    classDef fileNode fill:#e0e0e0,stroke:#666")

    cycle_e = _cycle_edges(G)

    if use_clusters and not is_file_level:
        clusters = detect_clusters(G)
        # Only label if we have clusters for nodes in G
        relevant = {n: c for n, c in clusters.items() if n in G}
        if relevant:
            cluster_labels = label_clusters(relevant, conn)
            # Group nodes by cluster
            groups: dict[int, list[int]] = {}
            ungrouped: list[int] = []
            for n in G.nodes():
                cid = relevant.get(n)
                if cid is not None:
                    groups.setdefault(cid, []).append(n)
                else:
                    ungrouped.append(n)

            for cid, members in sorted(groups.items()):
                label = _escape_mermaid(cluster_labels.get(cid, f"cluster-{cid}"))
                lines.append(f'    subgraph Cluster_{cid} ["{label}"]')
                for n in sorted(members):
                    data = G.nodes[n]
                    if is_file_level:
                        node_label = data.get("path", str(n))
                    else:
                        node_label = data.get("name", str(n))
                    kind = data.get("kind", "")
                    lines.append("    " + _mermaid_node(n, node_label, kind, is_file_level))
                lines.append("    end")

            for n in sorted(ungrouped):
                data = G.nodes[n]
                node_label = data.get("path" if is_file_level else "name", str(n))
                kind = data.get("kind", "")
                lines.append(_mermaid_node(n, node_label, kind, is_file_level))
        else:
            _emit_flat_nodes_mermaid(G, lines, is_file_level)
    else:
        _emit_flat_nodes_mermaid(G, lines, is_file_level)

    # Edges
    for u, v in sorted(G.edges()):
        nid_u = f"n{u}"
        nid_v = f"n{v}"
        if (u, v) in cycle_e:
            lines.append(f"    {nid_u} -.-> {nid_v}")
        else:
            lines.append(f"    {nid_u} --> {nid_v}")

    # Style cycle edges
    if cycle_e:
        lines.append("    linkStyle default stroke:#333")
        edge_list = sorted(G.edges())
        for idx, (u, v) in enumerate(edge_list):
            if (u, v) in cycle_e:
                lines.append(f"    linkStyle {idx} stroke:red,stroke-dasharray:5")

    return "\n".join(lines)


def _emit_flat_nodes_mermaid(G: nx.DiGraph, lines: list[str], is_file_level: bool) -> None:
    """Emit all nodes without subgraph grouping."""
    for n in sorted(G.nodes()):
        data = G.nodes[n]
        if is_file_level:
            node_label = data.get("path", str(n))
        else:
            node_label = data.get("name", str(n))
        kind = data.get("kind", "")
        lines.append(_mermaid_node(n, node_label, kind, is_file_level))


# -- DOT generation -----------------------------------------------------------


def _generate_dot(
    G: nx.DiGraph,
    conn,
    use_clusters: bool,
    is_file_level: bool,
) -> str:
    """Generate DOT diagram text from a filtered graph."""
    lines: list[str] = ["digraph G {"]
    lines.append("    rankdir=TB;")
    lines.append('    node [fontname="Helvetica", fontsize=10];')
    lines.append('    edge [fontname="Helvetica", fontsize=8];')

    cycle_e = _cycle_edges(G)

    if use_clusters and not is_file_level:
        clusters = detect_clusters(G)
        relevant = {n: c for n, c in clusters.items() if n in G}
        if relevant:
            cluster_labels = label_clusters(relevant, conn)
            groups: dict[int, list[int]] = {}
            ungrouped: list[int] = []
            for n in G.nodes():
                cid = relevant.get(n)
                if cid is not None:
                    groups.setdefault(cid, []).append(n)
                else:
                    ungrouped.append(n)

            for cid, members in sorted(groups.items()):
                label = cluster_labels.get(cid, f"cluster-{cid}").replace('"', '\\"')
                lines.append(f"    subgraph cluster_{cid} {{")
                lines.append(f'        label="{label}";')
                for n in sorted(members):
                    data = G.nodes[n]
                    node_label = data.get("path" if is_file_level else "name", str(n))
                    kind = data.get("kind", "")
                    lines.append("    " + _dot_node(n, node_label, kind, is_file_level))
                lines.append("    }")

            for n in sorted(ungrouped):
                data = G.nodes[n]
                node_label = data.get("path" if is_file_level else "name", str(n))
                kind = data.get("kind", "")
                lines.append(_dot_node(n, node_label, kind, is_file_level))
        else:
            _emit_flat_nodes_dot(G, lines, is_file_level)
    else:
        _emit_flat_nodes_dot(G, lines, is_file_level)

    # Edges
    for u, v in sorted(G.edges()):
        nid_u = f"n{u}"
        nid_v = f"n{v}"
        if (u, v) in cycle_e:
            lines.append(f"    {nid_u} -> {nid_v} [style=dashed, color=red];")
        else:
            lines.append(f"    {nid_u} -> {nid_v};")

    lines.append("}")
    return "\n".join(lines)


def _emit_flat_nodes_dot(G: nx.DiGraph, lines: list[str], is_file_level: bool) -> None:
    """Emit all DOT nodes without subgraph grouping."""
    for n in sorted(G.nodes()):
        data = G.nodes[n]
        if is_file_level:
            node_label = data.get("path", str(n))
        else:
            node_label = data.get("name", str(n))
        kind = data.get("kind", "")
        lines.append(_dot_node(n, node_label, kind, is_file_level))


# -- CLI command ---------------------------------------------------------------


@roam_capability(
    name="visualize",
    category="architecture",
    summary="Generate a Mermaid or DOT architecture diagram",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["mermaid", "dot"]),
    default="mermaid",
    show_default=True,
    help="Output format",
)
@click.option("--focus", default="", help="Focus on a symbol (BFS neighborhood)")
@click.option("--depth", default=1, show_default=True, help="BFS depth for focus mode")
@click.option("--limit", default=30, show_default=True, help="Max nodes in overview mode (top-N by PageRank)")
@click.option("--no-clusters", is_flag=True, help="Disable cluster grouping")
@click.option(
    "--direction",
    type=click.Choice(["TD", "LR"]),
    default="TD",
    show_default=True,
    help="Mermaid direction (TD=top-down, LR=left-right)",
)
@click.option("--file-level", is_flag=True, help="Use file-level graph")
@click.pass_context
def visualize(ctx, fmt, focus, depth, limit, no_clusters, direction, file_level):
    """Generate a Mermaid or DOT architecture diagram.

    Unlike ``map`` (which produces a text-based project skeleton), this
    command generates Mermaid or DOT architecture diagrams with cluster
    grouping and cycle highlighting.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Build graph
        if file_level:
            G = build_file_graph(conn)
        else:
            G = build_symbol_graph(conn)

        if len(G) == 0:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "visualize",
                            summary={"verdict": "EMPTY", "nodes": 0, "edges": 0},
                            diagram="",
                        )
                    )
                )
            else:
                click.echo("VERDICT: EMPTY -- no symbols in index")
            return

        # Filter
        # W1245 Pattern-2 variant-D: track focal-symbol resolver tier so the
        # envelope can disclose fuzzy-LIKE-fallback matches on --focus. The
        # overview path (no --focus) skips find_symbol entirely so the tier
        # stays "symbol" by default. An unresolved --focus is converted to
        # a structured envelope in json_mode (text-mode keeps the legacy
        # ClickException for stderr-shape compatibility).
        resolution_tier = "symbol"
        resolved_target = focus or None
        if focus:
            try:
                subG, resolution_tier, resolved_target = _filter_by_focus(G, conn, focus, depth)
            except click.ClickException as exc:
                if json_mode:
                    unresolved_block = resolution_disclosure("unresolved", target=focus)
                    click.echo(
                        to_json(
                            json_envelope(
                                "visualize",
                                summary={
                                    "verdict": f"Cannot visualize: {exc.message}",
                                    "nodes": 0,
                                    "edges": 0,
                                    "format": fmt,
                                    "focus": focus,
                                    "error": exc.message,
                                    **unresolved_block,
                                },
                                diagram="",
                                **unresolved_block,
                            )
                        )
                    )
                    return
                raise
        else:
            subG = _filter_by_pagerank(G, limit)

        node_count = len(subG)
        edge_count = subG.number_of_edges()
        use_clusters = not no_clusters

        # Generate diagram text
        if fmt == "dot":
            diagram = _generate_dot(subG, conn, use_clusters, file_level)
        else:
            diagram = _generate_mermaid(subG, conn, direction, use_clusters, file_level)

        # W1245 disclosure block (only meaningful when --focus walked the
        # resolver chain; otherwise it's the no-op ``symbol`` default).
        resolution_block = resolution_disclosure(resolution_tier, target=resolved_target)
        fuzzy_suffix = " [fuzzy resolution]" if focus and resolution_tier == "fuzzy" else ""

        # Output
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "visualize",
                        summary={
                            # LAW 4 (W17.3): bare ``"OK"`` verdict is
                            # abstract; name the analytical product.
                            "verdict": (
                                f"visualize rendered {fmt} diagram with "
                                f"{node_count} nodes and {edge_count} edges"
                                + (f" focused on {focus}" if focus else "")
                                + fuzzy_suffix
                            ),
                            "nodes": node_count,
                            "edges": edge_count,
                            "format": fmt,
                            "focus": focus or None,
                            **resolution_block,
                        },
                        diagram=diagram,
                        **resolution_block,
                    )
                )
            )
        else:
            mode = f"focus={focus} depth={depth}" if focus else f"top-{min(limit, len(G))} by PageRank"
            click.echo(f"VERDICT: OK -- {node_count} nodes, {edge_count} edges ({mode}){fuzzy_suffix}")
            click.echo("")
            click.echo(diagram)
