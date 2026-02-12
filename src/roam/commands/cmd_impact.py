"""Show blast radius: what breaks if a symbol changes."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


@click.command()
@click.argument('name')
@click.pass_context
def impact(ctx, name):
    """Show blast radius: what breaks if a symbol changes."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sym = find_symbol(conn, name)

        if sym is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)
        sym_id = sym["id"]

        if not json_mode:
            click.echo(f"{abbrev_kind(sym['kind'])}  {sym['qualified_name'] or sym['name']}  {loc(sym['file_path'], sym['line_start'])}")
            click.echo()

        # Build transitive closure using NetworkX
        try:
            from roam.graph.builder import build_symbol_graph
            import networkx as nx
        except ImportError:
            click.echo("Graph module not available. Run `roam index` first.")
            return

        G = build_symbol_graph(conn)
        if sym_id not in G:
            click.echo("Symbol not in graph.")
            return

        # Reverse graph: we want who depends on this symbol (incoming edges = callers)
        # descendants in the reverse graph = everything that transitively calls/uses this symbol
        RG = G.reverse()
        dependents = nx.descendants(RG, sym_id)

        if not dependents:
            if json_mode:
                click.echo(to_json(json_envelope("impact",
                    summary={"affected_symbols": 0, "affected_files": 0},
                    symbol=sym["qualified_name"] or sym["name"],
                    affected_symbols=0, affected_files=0,
                    direct_dependents={}, affected_file_list=[],
                )))
            else:
                click.echo("No dependents found.")
            return

        # Collect affected files and group direct callers by edge kind
        affected_files = set()
        direct_callers = set(RG.successors(sym_id))
        by_kind: dict[str, list] = {}
        for dep_id in dependents:
            node = G.nodes.get(dep_id, {})
            if not node:
                continue
            affected_files.add(node.get("file_path", "?"))
            if dep_id in direct_callers:
                edge_data = G.edges.get((dep_id, sym_id), {})
                edge_kind = edge_data.get("kind", "unknown")
                by_kind.setdefault(edge_kind, []).append([
                    abbrev_kind(node.get("kind", "?")),
                    node.get("name", "?"),
                    loc(node.get("file_path", "?"), None),
                ])

        if json_mode:
            json_deps = {}
            for edge_kind, items in by_kind.items():
                json_deps[edge_kind] = [
                    {"name": i[1], "kind": i[0], "file": i[2]}
                    for i in items
                ]
            click.echo(to_json(json_envelope("impact",
                summary={
                    "affected_symbols": len(dependents),
                    "affected_files": len(affected_files),
                },
                symbol=sym["qualified_name"] or sym["name"],
                affected_symbols=len(dependents),
                affected_files=len(affected_files),
                direct_dependents=json_deps,
                affected_file_list=sorted(affected_files),
            )))
            return

        click.echo(f"Affected symbols: {len(dependents)}  Affected files: {len(affected_files)}")
        click.echo()

        if by_kind:
            for edge_kind in sorted(by_kind.keys()):
                items = by_kind[edge_kind]
                click.echo(f"Direct dependents ({edge_kind}, {len(items)}):")
                click.echo(format_table(["kind", "name", "file"], items, budget=15))
                click.echo()
            if len(dependents) > len(direct_callers):
                click.echo(f"(+{len(dependents) - len(direct_callers)} transitive dependents)")

        # List affected files
        if affected_files:
            click.echo(f"\nAffected files ({len(affected_files)}):")
            for fp in sorted(affected_files)[:20]:
                click.echo(f"  {fp}")
            if len(affected_files) > 20:
                click.echo(f"  (+{len(affected_files) - 20} more)")
