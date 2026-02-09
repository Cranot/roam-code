"""Show blast radius: what breaks if a symbol changes."""

import click

from roam.db.connection import open_db, db_exists
from roam.output.formatter import abbrev_kind, loc, format_table


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


def _find_symbol(conn, name):
    """Find symbol by name or qualified name."""
    row = conn.execute(
        "SELECT s.*, f.path as file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.qualified_name = ?",
        (name,),
    ).fetchone()
    if row:
        return row
    rows = conn.execute(
        "SELECT s.*, f.path as file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name = ?",
        (name,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        return rows  # ambiguous
    rows = conn.execute(
        "SELECT s.*, f.path as file_path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name LIKE ? LIMIT 10",
        (f"%{name}%",),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if rows:
        return rows
    return None


@click.command()
@click.argument('name')
def impact(name):
    """Show blast radius: what breaks if a symbol changes."""
    _ensure_index()

    with open_db(readonly=True) as conn:
        result = _find_symbol(conn, name)

        if result is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)

        if isinstance(result, list):
            click.echo(f"Multiple matches for '{name}':")
            for s in result:
                click.echo(f"  {abbrev_kind(s['kind'])}  {s['qualified_name'] or s['name']}  {loc(s['file_path'], s['line_start'])}")
            click.echo("Use a qualified name to disambiguate.")
            return

        sym = result
        sym_id = sym["id"]

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
