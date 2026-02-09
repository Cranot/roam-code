from collections import Counter

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import FILE_BY_PATH, SYMBOLS_IN_FILE
from roam.output.formatter import abbrev_kind, loc, format_signature


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command("file")
@click.argument('path')
@click.option('--full', is_flag=True, help='Show all results without truncation')
def file_cmd(path, full):
    """Show file skeleton: all definitions with signatures."""
    _ensure_index()

    # Normalise separators
    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        frow = conn.execute(FILE_BY_PATH, (path,)).fetchone()
        if frow is None:
            # Try partial match
            frow = conn.execute(
                "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{path}",),
            ).fetchone()
        if frow is None:
            click.echo(f"File not found in index: {path}")
            click.echo("Hint: use the path relative to the project root.")
            raise SystemExit(1)

        click.echo(f"{frow['path']}  ({frow['language'] or '?'}, {frow['line_count']} lines)")
        click.echo()

        symbols = conn.execute(SYMBOLS_IN_FILE, (frow["id"],)).fetchall()
        if not symbols:
            click.echo("  (no symbols)")
            return

        # Symbol type summary
        kind_counts = Counter(abbrev_kind(s["kind"]) for s in symbols)
        summary_parts = [f"{k}:{v}" for k, v in kind_counts.most_common()]
        click.echo("  ".join(summary_parts))
        click.echo()

        # Build parent lookup for indentation
        parent_ids = {s["id"]: s["parent_id"] for s in symbols}

        for s in symbols:
            level = 0
            if s["parent_id"] is not None:
                level = 1
                # Check for deeper nesting
                pid = s["parent_id"]
                while pid in parent_ids and parent_ids[pid] is not None:
                    level += 1
                    pid = parent_ids[pid]

            prefix = "  " * level
            kind = abbrev_kind(s["kind"])
            sig = format_signature(s["signature"])
            line_info = f"L{s['line_start']}"
            if s["line_end"] and s["line_end"] != s["line_start"]:
                line_info += f"-{s['line_end']}"

            parts = [kind, s["name"]]
            if sig:
                parts.append(sig)
            parts.append(line_info)
            click.echo(f"{prefix}{'  '.join(parts)}")
