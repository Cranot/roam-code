"""Find symbols matching a name substring (case-insensitive)."""

import click

from roam.db.connection import open_db
from roam.db.queries import SEARCH_SYMBOLS
from roam.output.formatter import abbrev_kind, loc, format_signature, format_table, KIND_ABBREV, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.argument('pattern')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.option('-k', '--kind', 'kind_filter', default=None,
              help='Filter by symbol kind (fn, cls, meth, var, iface, etc.)')
@click.pass_context
def search(ctx, pattern, full, kind_filter):
    """Find symbols matching a name substring (case-insensitive)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    like_pattern = f"%{pattern}%"
    with open_db(readonly=True) as conn:
        rows = conn.execute(SEARCH_SYMBOLS, (like_pattern, 9999 if full else 50)).fetchall()

        if kind_filter:
            abbrev_to_kind = {v: k for k, v in KIND_ABBREV.items()}
            full_kind = abbrev_to_kind.get(kind_filter, kind_filter)
            rows = [r for r in rows if r["kind"] == full_kind]

        if not rows:
            suffix = f" of kind '{kind_filter}'" if kind_filter else ""
            if json_mode:
                click.echo(to_json(json_envelope("search",
                    summary={"total": 0},
                    pattern=pattern, results=[],
                )))
            else:
                click.echo(f"No symbols matching '{pattern}'{suffix}")
            return

        # Batch-fetch incoming edge counts
        sym_ids = [r["id"] for r in rows]
        ref_counts = {}
        for i in range(0, len(sym_ids), 500):
            batch = sym_ids[i:i + 500]
            ph = ",".join("?" for _ in batch)
            for rc in conn.execute(
                f"SELECT target_id, COUNT(*) as cnt FROM edges "
                f"WHERE target_id IN ({ph}) GROUP BY target_id",
                batch,
            ).fetchall():
                ref_counts[rc["target_id"]] = rc["cnt"]

        if json_mode:
            click.echo(to_json(json_envelope("search",
                summary={"total": len(rows), "pattern": pattern},
                pattern=pattern,
                total=len(rows),
                results=[
                    {
                        "name": r["name"],
                        "qualified_name": r["qualified_name"] or "",
                        "kind": r["kind"],
                        "signature": r["signature"] or "",
                        "refs": ref_counts.get(r["id"], 0),
                        "pagerank": round(r["pagerank"], 4) if r["pagerank"] else 0,
                        "location": loc(r["file_path"], r["line_start"]),
                    }
                    for r in rows
                ],
            )))
            return

        # --- Text output ---
        total = len(rows)
        if not full and total == 50:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE name LIKE ? COLLATE NOCASE",
                (like_pattern,),
            ).fetchone()[0]
            click.echo(f"=== Symbols matching '{pattern}' ({total} of {cnt}, use --full for all) ===")
        else:
            click.echo(f"=== Symbols matching '{pattern}' ({total}) ===")

        table_rows = []
        for r in rows:
            refs = ref_counts.get(r["id"], 0)
            pr = r["pagerank"] or 0
            pr_str = f"{pr:.4f}" if pr > 0 else ""
            # Show qualified name when it differs (helps disambiguate)
            qn = r["qualified_name"] or ""
            name_col = qn if qn and qn != r["name"] else r["name"]
            sig = format_signature(r["signature"], max_len=40) if r["signature"] else ""
            table_rows.append([
                name_col,
                abbrev_kind(r["kind"]),
                sig,
                str(refs),
                pr_str,
                loc(r["file_path"], r["line_start"]),
            ])
        click.echo(format_table(
            ["Name", "Kind", "Sig", "Refs", "PR", "Location"],
            table_rows,
            budget=0 if full else 50,
        ))
