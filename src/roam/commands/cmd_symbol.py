"""Show symbol definition, callers, and callees."""

import click

from roam.db.connection import open_db
from roam.db.queries import (
    CALLERS_OF, CALLEES_OF, METRICS_FOR_SYMBOL,
)
from roam.output.formatter import (
    abbrev_kind, loc, format_signature, format_edge_kind,
    truncate_lines, section, to_json, json_envelope,
)
from roam.commands.resolve import ensure_index, find_symbol

_EDGE_PRIORITY = {"call": 0, "template": 0, "inherits": 1, "implements": 2, "import": 3}


def _dedup_edges(edges):
    """Dedup edges by symbol, preferring call > inherits > implements > import."""
    best = {}
    for c in edges:
        sid = c["id"]
        prio = _EDGE_PRIORITY.get(c["edge_kind"], 1)
        if sid not in best or prio < best[sid][1]:
            best[sid] = (c, prio)
    return [v[0] for v in best.values()]


@click.command()
@click.argument('name')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.pass_context
def symbol(ctx, name, full):
    """Show symbol definition, callers, and callees."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        s = find_symbol(conn, name)

        if s is None:
            click.echo(f"Symbol not found: {name}")
            raise SystemExit(1)
        metrics = conn.execute(METRICS_FOR_SYMBOL, (s["id"],)).fetchone()
        callers = conn.execute(CALLERS_OF, (s["id"],)).fetchall()
        callees = conn.execute(CALLEES_OF, (s["id"],)).fetchall()
        deduped_callers = _dedup_edges(callers) if callers else []
        deduped_callees = _dedup_edges(callees) if callees else []

        if json_mode:
            data = {
                "name": s["qualified_name"] or s["name"],
                "kind": s["kind"],
                "signature": s["signature"] or "",
                "location": loc(s["file_path"], s["line_start"]),
                "docstring": s["docstring"] or "",
            }
            if metrics:
                data["pagerank"] = round(metrics["pagerank"], 4)
                data["in_degree"] = metrics["in_degree"]
                data["out_degree"] = metrics["out_degree"]
            data["callers"] = [
                {"name": c["name"], "kind": c["kind"], "edge_kind": c["edge_kind"],
                 "location": loc(c["file_path"], c["edge_line"])}
                for c in deduped_callers
            ]
            data["callees"] = [
                {"name": c["name"], "kind": c["kind"], "edge_kind": c["edge_kind"],
                 "location": loc(c["file_path"], c["edge_line"])}
                for c in deduped_callees
            ]
            click.echo(to_json(json_envelope("symbol",
                summary={
                    "callers": len(deduped_callers),
                    "callees": len(deduped_callees),
                },
                **data,
            )))
            return

        # --- Text output ---
        sig = format_signature(s["signature"])
        click.echo(f"{abbrev_kind(s['kind'])}  {s['qualified_name'] or s['name']}")
        if sig:
            click.echo(f"  {sig}")
        click.echo(f"  {loc(s['file_path'], s['line_start'])}")

        if s["docstring"]:
            doc_lines = s["docstring"].strip().splitlines()
            if not full:
                doc_lines = truncate_lines(doc_lines, 5)
            for dl in doc_lines:
                click.echo(f"  | {dl}")

        if metrics:
            click.echo(f"  PR={metrics['pagerank']:.4f}  in={metrics['in_degree']}  out={metrics['out_degree']}")

        if deduped_callers:
            lines = []
            for c in deduped_callers:
                edge = format_edge_kind(c["edge_kind"])
                lines.append(f"  {abbrev_kind(c['kind'])}  {c['name']}  ({edge})  {loc(c['file_path'], c['edge_line'])}")
            click.echo(section(f"Callers ({len(deduped_callers)}):", lines, budget=0 if full else 15))

        if deduped_callees:
            lines = []
            for c in deduped_callees:
                edge = format_edge_kind(c["edge_kind"])
                lines.append(f"  {abbrev_kind(c['kind'])}  {c['name']}  ({edge})  {loc(c['file_path'], c['edge_line'])}")
            click.echo(section(f"Callees ({len(deduped_callees)}):", lines, budget=0 if full else 15))
