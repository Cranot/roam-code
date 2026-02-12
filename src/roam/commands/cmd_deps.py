"""Show file import/imported-by relationships."""

import click

from roam.db.connection import open_db
from roam.db.queries import FILE_BY_PATH, FILE_IMPORTS, FILE_IMPORTED_BY
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.argument('path')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.pass_context
def deps(ctx, path, full):
    """Show file import/imported-by relationships."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
        if frow is None:
            frow = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{path}",),
            ).fetchone()
        if frow is None:
            click.echo(f"File not found in index: {path}")
            raise SystemExit(1)

        # --- Imports ---
        imports = conn.execute(FILE_IMPORTS, (frow["id"],)).fetchall()
        used_from: dict = {}
        if imports:
            import_file_ids = set(i["id"] for i in imports)
            sym_edges = conn.execute(
                "SELECT s_tgt.file_id as tgt_fid, s_tgt.name as tgt_name "
                "FROM edges e "
                "JOIN symbols s_src ON e.source_id = s_src.id "
                "JOIN symbols s_tgt ON e.target_id = s_tgt.id "
                "WHERE s_src.file_id = ? AND s_tgt.file_id != ?",
                (frow["id"], frow["id"]),
            ).fetchall()
            for se in sym_edges:
                if se["tgt_fid"] in import_file_ids:
                    used_from.setdefault(se["tgt_fid"], set()).add(se["tgt_name"])

        # --- Imported by ---
        imported_by = conn.execute(FILE_IMPORTED_BY, (frow["id"],)).fetchall()

        if json_mode:
            click.echo(to_json(json_envelope("deps",
                summary={
                    "imports": len(imports),
                    "imported_by": len(imported_by),
                },
                path=frow["path"],
                imports=[
                    {
                        "path": i["path"],
                        "symbol_count": i["symbol_count"],
                        "used_symbols": sorted(used_from.get(i["id"], set())),
                    }
                    for i in imports
                ],
                imported_by=[
                    {"path": i["path"], "symbol_count": i["symbol_count"]}
                    for i in imported_by
                ],
            )))
            return

        # --- Text output ---
        click.echo(f"{frow['path']}")
        click.echo()

        if imports:
            rows = []
            for i in imports:
                names = used_from.get(i["id"], set())
                sym_str = ", ".join(sorted(names)[:5])
                if len(names) > 5:
                    sym_str += f" (+{len(names) - 5})"
                rows.append([i["path"], str(i["symbol_count"]), sym_str])
            click.echo("Imports:")
            click.echo(format_table(["file", "symbols", "used"], rows))
        else:
            click.echo("Imports: (none)")
        click.echo()

        if imported_by:
            rows = [[i["path"], str(i["symbol_count"])] for i in imported_by]
            click.echo("Imported by:")
            click.echo(format_table(["file", "symbols"], rows))
        else:
            click.echo("Imported by: (none)")
