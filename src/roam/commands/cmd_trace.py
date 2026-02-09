import click

from roam.db.connection import open_db, db_exists
from roam.output.formatter import abbrev_kind, loc


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument('source')
@click.argument('target')
def trace(source, target):
    """Show shortest path between two symbols."""
    _ensure_index()

    from roam.graph.builder import build_symbol_graph
    from roam.graph.pathfinding import find_path, find_symbol_id, format_path

    with open_db(readonly=True) as conn:
        src_ids = find_symbol_id(conn, source)
        if not src_ids:
            click.echo(f"Source symbol not found: {source}")
            raise SystemExit(1)

        tgt_ids = find_symbol_id(conn, target)
        if not tgt_ids:
            click.echo(f"Target symbol not found: {target}")
            raise SystemExit(1)

        G = build_symbol_graph(conn)

        # Try all combinations of src/tgt, pick shortest
        best = None
        for sid in src_ids:
            for tid in tgt_ids:
                p = find_path(G, sid, tid)
                if p and (best is None or len(p) < len(best)):
                    best = p

        if best is None:
            click.echo(f"No path from '{source}' to '{target}'.")
            return

        annotated = format_path(best, conn)
        click.echo(f"Path ({len(annotated)} hops):")
        for i, node in enumerate(annotated):
            if i == 0:
                click.echo(f"    {abbrev_kind(node['kind'])}  {node['name']}  {loc(node['file_path'], node['line'])}")
            else:
                # Look up edge kind between previous and current node
                prev_id = best[i - 1]
                curr_id = best[i]
                edge_kind = G.edges.get((prev_id, curr_id), {}).get("kind", "")
                if not edge_kind:
                    # Check reverse edge (undirected fallback)
                    edge_kind = G.edges.get((curr_id, prev_id), {}).get("kind", "")
                edge_label = f"[{edge_kind}] " if edge_kind else ""
                click.echo(f"  -> {edge_label}{abbrev_kind(node['kind'])}  {node['name']}  {loc(node['file_path'], node['line'])}")
