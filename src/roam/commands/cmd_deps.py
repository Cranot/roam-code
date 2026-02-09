import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import FILE_BY_PATH, FILE_IMPORTS, FILE_IMPORTED_BY
from roam.output.formatter import format_table


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument('path')
@click.option('--full', is_flag=True, help='Show all results without truncation')
def deps(path, full):
    """Show file import/imported-by relationships."""
    _ensure_index()

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

        click.echo(f"{frow['path']}")
        click.echo()

        # --- Imports (what this file depends on) ---
        imports = conn.execute(FILE_IMPORTS, (frow["id"],)).fetchall()
        if imports:
            # Build symbol breakdown: which symbols from each imported file are used
            import_file_ids = [i["id"] for i in imports]
            sym_edges = conn.execute(
                "SELECT e.target_id, s_src.file_id as src_fid, s_tgt.file_id as tgt_fid, s_tgt.name as tgt_name "
                "FROM edges e "
                "JOIN symbols s_src ON e.source_id = s_src.id "
                "JOIN symbols s_tgt ON e.target_id = s_tgt.id "
                "WHERE s_src.file_id = ?",
                (frow["id"],),
            ).fetchall()
            used_from: dict = {}
            for se in sym_edges:
                used_from.setdefault(se["tgt_fid"], set()).add(se["tgt_name"])

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

        # --- Imported by (who depends on this file) ---
        imported_by = conn.execute(FILE_IMPORTED_BY, (frow["id"],)).fetchall()
        if imported_by:
            rows = [[i["path"], str(i["symbol_count"])] for i in imported_by]
            click.echo("Imported by:")
            click.echo(format_table(["file", "symbols"], rows))
        else:
            click.echo("Imported by: (none)")
