"""Show blast radius: what breaks if a symbol changes."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index, find_symbol


def _collect_dependents(G, RG, sym_id, conn):
    """Collect affected files, direct callers by kind, and SF test files."""
    import networkx as nx

    dependents = nx.descendants(RG, sym_id)
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

    # Convention-based Salesforce test discovery
    sf_test_files = set()
    for dep_id in dependents | {sym_id}:
        dep_name = G.nodes.get(dep_id, {}).get("name", "")
        if dep_name:
            conv_tests = conn.execute(
                "SELECT f.path FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE (s.name = ? OR s.name = ?) AND s.kind = 'class'",
                (f"{dep_name}Test", f"{dep_name}_Test"),
            ).fetchall()
            for ct in conv_tests:
                sf_test_files.add(ct["path"])

    return dependents, affected_files, direct_callers, by_kind, sf_test_files


def _impact_verdict(dependents, affected_files, total_syms):
    """Generate blast radius verdict string."""
    reach_pct = (len(dependents) / total_syms * 100) if total_syms > 0 else 0
    if reach_pct >= 10 or len(dependents) >= 50:
        return f"Large blast radius — {len(dependents)} symbols ({reach_pct:.0f}%) in {len(affected_files)} files affected", reach_pct
    if reach_pct >= 2 or len(dependents) >= 10:
        return f"Moderate blast radius — {len(dependents)} symbols ({reach_pct:.0f}%) in {len(affected_files)} files affected", reach_pct
    if len(dependents) > 0:
        return f"Small blast radius — {len(dependents)} symbols in {len(affected_files)} files affected", reach_pct
    return "No dependents — safe to change", reach_pct


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

        RG = G.reverse()
        dependents, affected_files, direct_callers, by_kind, sf_test_files = \
            _collect_dependents(G, RG, sym_id, conn)

        # Personalized PageRank for distance-weighted importance (Gleich 2015)
        ppr = {}
        if dependents:
            try:
                ppr = nx.pagerank(RG, alpha=0.85, personalization={sym_id: 1.0})
            except Exception:
                pass

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

        weighted_impact = sum(ppr.get(d, 0) for d in dependents)
        verdict, reach_pct = _impact_verdict(dependents, affected_files, len(G))

        if json_mode:
            json_deps = {
                ek: [{"name": i[1], "kind": i[0], "file": i[2]} for i in items]
                for ek, items in by_kind.items()
            }
            click.echo(to_json(json_envelope("impact",
                summary={
                    "verdict": verdict,
                    "affected_symbols": len(dependents),
                    "affected_files": len(affected_files),
                    "weighted_impact": round(weighted_impact, 4),
                    "reach_pct": round(reach_pct, 1),
                    "sf_convention_tests": len(sf_test_files),
                },
                symbol=sym["qualified_name"] or sym["name"],
                affected_symbols=len(dependents),
                affected_files=len(affected_files),
                weighted_impact=round(weighted_impact, 4),
                reach_pct=round(reach_pct, 1),
                direct_dependents=json_deps,
                affected_file_list=sorted(affected_files),
                sf_convention_tests=sorted(sf_test_files),
            )))
            return

        click.echo(f"VERDICT: {verdict}\n")
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

        if affected_files:
            click.echo(f"\nAffected files ({len(affected_files)}):")
            for fp in sorted(affected_files)[:20]:
                click.echo(f"  {fp}")
            if len(affected_files) > 20:
                click.echo(f"  (+{len(affected_files) - 20} more)")

        if sf_test_files:
            click.echo(f"\nSalesforce convention tests ({len(sf_test_files)}):")
            for tf in sorted(sf_test_files):
                click.echo(f"  {tf}")
