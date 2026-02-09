"""Find symbols matching a name substring (case-insensitive)."""

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import SEARCH_SYMBOLS
from roam.output.formatter import abbrev_kind, loc, format_table, KIND_ABBREV


def _ensure_index():
    from roam.db.connection import db_exists
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument('pattern')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.option('-k', '--kind', 'kind_filter', default=None,
              help='Filter by symbol kind (fn, cls, meth, var, iface, etc.)')
def search(pattern, full, kind_filter):
    """Find symbols matching a name substring (case-insensitive)."""
    _ensure_index()
    like_pattern = f"%{pattern}%"
    with open_db(readonly=True) as conn:
        rows = conn.execute(SEARCH_SYMBOLS, (like_pattern, 9999 if full else 50)).fetchall()

        if kind_filter:
            # Resolve abbreviation back to full kind name
            abbrev_to_kind = {v: k for k, v in KIND_ABBREV.items()}
            full_kind = abbrev_to_kind.get(kind_filter, kind_filter)
            rows = [r for r in rows if r["kind"] == full_kind]

        if not rows:
            suffix = f" of kind '{kind_filter}'" if kind_filter else ""
            click.echo(f"No symbols matching '{pattern}'{suffix}")
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
