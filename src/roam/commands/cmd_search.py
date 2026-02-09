"""Find symbols matching a name substring (case-insensitive)."""

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import SEARCH_SYMBOLS
from roam.output.formatter import abbrev_kind, loc, format_table


def _ensure_index():
    from roam.db.connection import db_exists
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument('pattern')
@click.option('--full', is_flag=True, help='Show all results without truncation')
def search(pattern, full):
    """Find symbols matching a name substring (case-insensitive)."""
    _ensure_index()
    like_pattern = f"%{pattern}%"
    with open_db(readonly=True) as conn:
        rows = conn.execute(SEARCH_SYMBOLS, (like_pattern, 9999 if full else 50)).fetchall()

        if not rows:
            click.echo(f"No symbols matching '{pattern}'")
            return

        click.echo(f"=== Symbols matching '{pattern}' ({len(rows)}) ===")
        table_rows = []
        for r in rows:
            table_rows.append([
                r["name"],
                abbrev_kind(r["kind"]),
                loc(r["file_path"], r["line_start"]),
            ])
        click.echo(format_table(
            ["Name", "Kind", "Location"],
            table_rows,
            budget=0 if full else 50,
        ))
