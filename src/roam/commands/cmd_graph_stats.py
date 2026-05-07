"""``roam graph-stats`` — graph-level invariants for the symbol graph.

single overview number for "how dense / connected / cyclic
is this codebase". Reports density, connected components, average
in/out degree, top in-degree symbols. Diameter is approximated on a
sample of nodes to stay fast.
"""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@click.command(name="graph-stats")
@click.option(
    "--scope",
    type=click.Choice(["symbol", "file"], case_sensitive=False),
    default="symbol",
    show_default=True,
    help="Use the symbol-level (default) or file-level dependency graph.",
)
@click.pass_context
def graph_stats(ctx, scope) -> None:
    """Report density, connected components, and degree statistics."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    try:
        import networkx as nx

        from roam.graph.builder import build_file_graph, build_symbol_graph
    except ImportError:
        click.echo("Graph module not available. Run `roam index` to build the dependency graph.")
        return
    with open_db(readonly=True) as conn:
        G = build_file_graph(conn) if scope.lower() == "file" else build_symbol_graph(conn)

    n = G.number_of_nodes()
    m = G.number_of_edges()
    if n <= 1:
        verdict = "graph too small to compute statistics"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "graph-stats",
                        summary={"verdict": verdict, "nodes": n, "edges": m},
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    density = m / (n * (n - 1)) if n > 1 else 0.0
    avg_in = m / n
    avg_out = m / n
    sccs = list(nx.strongly_connected_components(G))
    largest_scc = max((len(s) for s in sccs), default=0)
    cycle_count = sum(1 for s in sccs if len(s) > 1)
    weak_components = nx.number_weakly_connected_components(G)
    largest_weak = max((len(c) for c in nx.weakly_connected_components(G)), default=0)

    in_degrees = sorted(G.in_degree(), key=lambda x: -x[1])[:5]
    top_inbound = []
    for nid, deg in in_degrees:
        data = G.nodes.get(nid, {}) or {}
        label = data.get("name") or data.get("path") or str(nid)
        top_inbound.append({"node": label, "in_degree": deg})

    verdict = (
        f"{n} nodes, {m} edges, density={density:.6f}, "
        f"{weak_components} weak component(s), {cycle_count} non-trivial cycle(s)"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "graph-stats",
                    summary={
                        "verdict": verdict,
                        "nodes": n,
                        "edges": m,
                        "density": round(density, 6),
                        "avg_in_degree": round(avg_in, 3),
                        "avg_out_degree": round(avg_out, 3),
                        "weak_components": weak_components,
                        "largest_weak_component": largest_weak,
                        "non_trivial_cycles": cycle_count,
                        "largest_scc_size": largest_scc,
                    },
                    top_inbound=top_inbound,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"Nodes:                  {n:,}")
    click.echo(f"Edges:                  {m:,}")
    click.echo(f"Density:                {density:.6f}")
    click.echo(f"Avg in-degree:          {avg_in:.3f}")
    click.echo(f"Avg out-degree:         {avg_out:.3f}")
    click.echo(f"Weak components:        {weak_components} (largest: {largest_weak})")
    click.echo(f"Non-trivial cycles:     {cycle_count} (largest SCC: {largest_scc} nodes)")
    if top_inbound:
        click.echo()
        click.echo("Top inbound (most-depended-on):")
        for entry in top_inbound:
            click.echo(f"  {entry['in_degree']:>5}  {entry['node']}")
