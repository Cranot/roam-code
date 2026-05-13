"""``roam graph-export`` — write the symbol graph as GraphML / DOT / JSONL.

handy for plugging the in-memory NetworkX graph into external
tooling (Gephi, Cytoscape, igraph, custom analyses). Stays read-only;
no network egress.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.graph.builder import build_file_graph, build_symbol_graph
from roam.output.formatter import json_envelope, to_json


def _serialise_jsonl(G, output_path: Path) -> int:
    """Write graph as one JSON object per line (node | edge)."""
    count = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for node, data in G.nodes(data=True):
            payload = {"type": "node", "id": str(node), **{k: data[k] for k in data}}
            fh.write(json.dumps(payload, default=str) + "\n")
            count += 1
        for src, tgt, data in G.edges(data=True):
            payload = {"type": "edge", "src": str(src), "tgt": str(tgt), **{k: data[k] for k in data}}
            fh.write(json.dumps(payload, default=str) + "\n")
            count += 1
    return count


def _serialise_dot(G, output_path: Path) -> int:
    """Write graph as a Graphviz DOT file. Pure stdlib — no pydot."""
    count = 0
    with output_path.open("w", encoding="utf-8") as fh:
        fh.write("digraph G {\n")
        for node, data in G.nodes(data=True):
            label = str(data.get("name") or data.get("path") or node).replace('"', "'")
            fh.write(f'  "{node}" [label="{label}"];\n')
            count += 1
        for src, tgt in G.edges():
            fh.write(f'  "{src}" -> "{tgt}";\n')
            count += 1
        fh.write("}\n")
    return count


def _serialise_graphml(G, output_path: Path) -> int:
    """Write graph as GraphML via NetworkX."""
    import networkx as nx

    nx.write_graphml(G, str(output_path))
    return G.number_of_nodes() + G.number_of_edges()


@roam_capability(
    name="graph-export",
    category="architecture",
    summary="Export the indexed graph for external tooling",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "architecture"),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="graph-export")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["graphml", "dot", "jsonl"], case_sensitive=False),
    default="jsonl",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--scope",
    type=click.Choice(["symbol", "file"], case_sensitive=False),
    default="symbol",
    show_default=True,
    help="Symbol-level (default) or file-level dependency graph.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write to this path (default: ./roam-graph.<format>).",
)
@click.pass_context
def graph_export(ctx, fmt, scope, output_path) -> None:
    """Export the indexed graph for external tooling."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    fmt = fmt.lower()
    target = Path(output_path) if output_path else Path(f"roam-graph.{fmt}")
    with open_db(readonly=True) as conn:
        G = build_file_graph(conn) if scope.lower() == "file" else build_symbol_graph(conn)

    if fmt == "jsonl":
        records = _serialise_jsonl(G, target)
    elif fmt == "dot":
        records = _serialise_dot(G, target)
    else:
        records = _serialise_graphml(G, target)

    nodes = G.number_of_nodes()
    edges = G.number_of_edges()
    verdict = f"{fmt.upper()} export: {nodes} nodes, {edges} edges → {target}"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "graph-export",
                    summary={
                        "verdict": verdict,
                        "nodes": nodes,
                        "edges": edges,
                        "format": fmt,
                        "scope": scope,
                        "records_written": records,
                    },
                    output_path=str(target),
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
