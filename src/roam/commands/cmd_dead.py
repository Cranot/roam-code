"""Show unreferenced exported symbols (dead code)."""

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import UNREFERENCED_EXPORTS
from roam.output.formatter import abbrev_kind, loc, format_table


def _ensure_index():
    from roam.db.connection import db_exists
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Include low-confidence results")
def dead(show_all):
    """Show unreferenced exported symbols (dead code)."""
    _ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()

        if not rows:
            click.echo("=== Unreferenced Exports (0) ===")
            click.echo("  (none -- all exports are referenced)")
            return

        # Split by confidence: file is imported (high) vs not imported (low)
        imported_files = set()
        for r in conn.execute(
            "SELECT DISTINCT target_file_id FROM file_edges"
        ).fetchall():
            imported_files.add(r["target_file_id"])

        # Get file_id for each dead symbol
        high = []
        low = []
        for r in rows:
            file_id = r["file_id"]
            if file_id in imported_files:
                high.append(r)
            else:
                low.append(r)

        click.echo(f"=== Unreferenced Exports ({len(high)} high confidence, {len(low)} low) ===\n")

        # Build imported-by lookup for high-confidence results
        if high:
            high_file_ids = {r["file_id"] for r in high}
            ph = ",".join("?" for _ in high_file_ids)
            importer_rows = conn.execute(
                f"SELECT fe.target_file_id, f.path "
                f"FROM file_edges fe JOIN files f ON fe.source_file_id = f.id "
                f"WHERE fe.target_file_id IN ({ph})",
                list(high_file_ids),
            ).fetchall()
            importers_by_file: dict = {}
            for ir in importer_rows:
                importers_by_file.setdefault(ir["target_file_id"], []).append(ir["path"])

            click.echo(f"-- High confidence ({len(high)}) --")
            click.echo("(file is imported but symbol has no references)")
            table_rows = []
            for r in high:
                imp_list = importers_by_file.get(r["file_id"], [])
                imp_str = ", ".join(imp_list[:3])
                if len(imp_list) > 3:
                    imp_str += f" (+{len(imp_list) - 3})"
                table_rows.append([
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                    imp_str,
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location", "Imported by"],
                table_rows,
                budget=50,
            ))

        if show_all and low:
            click.echo(f"\n-- Low confidence ({len(low)}) --")
            click.echo("(file has no importers — may be entry point or used by unparsed files)")
            table_rows = []
            for r in low:
                table_rows.append([
                    r["name"],
                    abbrev_kind(r["kind"]),
                    loc(r["file_path"], r["line_start"]),
                ])
            click.echo(format_table(
                ["Name", "Kind", "Location"],
                table_rows,
                budget=50,
            ))
        elif low:
            click.echo(f"\n({len(low)} low-confidence results hidden — use --all to show)")

        # Check for files with no extracted symbols
        unparsed = conn.execute(
            "SELECT COUNT(*) FROM files f "
            "WHERE NOT EXISTS (SELECT 1 FROM symbols s WHERE s.file_id = f.id)"
        ).fetchone()[0]
        if unparsed:
            click.echo(f"\nNote: {unparsed} files had no symbols extracted (may cause false positives)")
