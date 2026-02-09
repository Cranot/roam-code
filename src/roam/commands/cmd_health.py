"""Detect and report code health issues."""

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import TOP_BY_DEGREE, TOP_BY_BETWEENNESS
from roam.graph.builder import build_symbol_graph
from roam.graph.cycles import find_cycles, format_cycles
from roam.graph.layers import detect_layers, find_violations
from roam.output.formatter import (
    abbrev_kind, loc, section, format_table, truncate_lines,
)


def _ensure_index():
    from roam.db.connection import db_exists
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
def health():
    """Show code health: cycles, god components, bottlenecks."""
    _ensure_index()
    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        # --- Cycles ---
        cycles = find_cycles(G)
        click.echo("=== Cycles ===")
        if cycles:
            formatted = format_cycles(cycles, conn)
            for i, cyc in enumerate(formatted, 1):
                names = [s["name"] for s in cyc["symbols"]]
                click.echo(f"  cycle {i} ({cyc['size']} symbols): {', '.join(names[:10])}")
                if len(names) > 10:
                    click.echo(f"    (+{len(names) - 10} more)")
                click.echo(f"    files: {', '.join(cyc['files'][:5])}")
            click.echo(f"  total: {len(cycles)} cycle(s)")
        else:
            click.echo("  (none)")

        # --- God components ---
        click.echo("\n=== God Components (degree > 20) ===")
        rows = conn.execute(TOP_BY_DEGREE, (50,)).fetchall()
        god_rows = []
        for r in rows:
            total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
            if total > 20:
                god_rows.append([
                    r["name"],
                    abbrev_kind(r["kind"]),
                    str(total),
                    loc(r["file_path"]),
                ])
        if god_rows:
            click.echo(format_table(
                ["Name", "Kind", "Degree", "File"],
                god_rows,
                budget=20,
            ))
        else:
            click.echo("  (none)")

        # --- Bottlenecks ---
        click.echo("\n=== Bottlenecks (high betweenness) ===")
        rows = conn.execute(TOP_BY_BETWEENNESS, (15,)).fetchall()
        bn_rows = []
        for r in rows:
            bw = r["betweenness"] or 0
            if bw > 0.5:
                bw_str = f"{bw:.0f}" if bw >= 10 else f"{bw:.1f}"
                bn_rows.append([
                    r["name"],
                    abbrev_kind(r["kind"]),
                    bw_str,
                    loc(r["file_path"]),
                ])
        if bn_rows:
            click.echo(format_table(
                ["Name", "Kind", "Betweenness", "File"],
                bn_rows,
                budget=15,
            ))
        else:
            click.echo("  (none)")

        # --- Layer violations ---
        click.echo("\n=== Layer Violations ===")
        layers = detect_layers(G)
        if layers:
            violations = find_violations(G, layers)
            if violations:
                # Annotate with names
                all_ids = {v["source"] for v in violations} | {v["target"] for v in violations}
                ph = ",".join("?" for _ in all_ids)
                lookup_rows = conn.execute(
                    f"SELECT s.id, s.name, f.path as file_path "
                    f"FROM symbols s JOIN files f ON s.file_id = f.id "
                    f"WHERE s.id IN ({ph})",
                    list(all_ids),
                ).fetchall()
                names = {r["id"]: r["name"] for r in lookup_rows}
                v_rows = []
                for v in violations[:20]:
                    src_name = names.get(v["source"], str(v["source"]))
                    tgt_name = names.get(v["target"], str(v["target"]))
                    v_rows.append([
                        src_name,
                        f"L{v['source_layer']}",
                        tgt_name,
                        f"L{v['target_layer']}",
                    ])
                click.echo(format_table(
                    ["Source", "Layer", "Target", "Layer"],
                    v_rows,
                    budget=20,
                ))
                if len(violations) > 20:
                    click.echo(f"  (+{len(violations) - 20} more)")
            else:
                click.echo("  (none)")
        else:
            click.echo("  (no layers detected)")
