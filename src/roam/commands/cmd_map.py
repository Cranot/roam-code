import os
from collections import Counter

import click

from roam.db.connection import open_db, db_exists
from roam.db.queries import (
    ALL_FILES, FILE_COUNT, TOP_SYMBOLS_BY_PAGERANK,
)
from roam.output.formatter import (
    abbrev_kind, loc, format_signature, format_table, section,
)


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command("map")
@click.option('-n', 'count', default=20, help='Number of top symbols to show')
@click.option('--full', is_flag=True, help='Show all results without truncation')
def map_cmd(count, full):
    """Show project skeleton with entry points and key symbols."""
    _ensure_index()

    with open_db(readonly=True) as conn:
        # --- Project stats ---
        files = conn.execute(ALL_FILES).fetchall()
        total_files = len(files)
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        lang_counts = Counter(f["language"] for f in files if f["language"])
        lang_str = ", ".join(f"{lang}={n}" for lang, n in lang_counts.most_common(8))

        # Edge kind distribution
        edge_kinds = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()
        edge_str = ", ".join(f"{r['kind']}={r['cnt']}" for r in edge_kinds) if edge_kinds else "none"

        click.echo(f"Files: {total_files}  Symbols: {sym_count}  Edges: {edge_count}")
        click.echo(f"Languages: {lang_str}")
        click.echo(f"Edge kinds: {edge_str}")
        click.echo()

        # --- Top directories ---
        dir_rows_raw = conn.execute("""
            SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
                   THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
                   ELSE '.' END as dir,
                   COUNT(*) as cnt
            FROM files GROUP BY dir ORDER BY cnt DESC
        """).fetchall()
        dir_counts = {r["dir"]: r["cnt"] for r in dir_rows_raw}

        dir_items = sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)
        dir_rows = [[d, str(c)] for d, c in (dir_items if full else dir_items[:15])]
        click.echo(section("Directories:", []))
        click.echo(format_table(["dir", "files"], dir_rows, budget=0 if full else 15))
        click.echo()

        # --- Entry points ---
        entry_names = {
            "main.py", "__main__.py", "__init__.py", "index.js", "index.ts",
            "main.go", "main.rs", "app.py", "app.js", "app.ts",
            "mod.rs", "lib.rs", "setup.py", "manage.py",
        }
        entries = [f["path"] for f in files
                   if os.path.basename(f["path"]) in entry_names]

        # Also detect entry points by symbol analysis
        # 1. Files containing main() functions
        main_files = conn.execute(
            "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.name = 'main' AND s.kind = 'function'",
        ).fetchall()
        for r in main_files:
            if r["path"] not in entries:
                entries.append(r["path"])

        # 2. Files with decorated entry points (@app.route, @click.command, etc.)
        decorated_files = conn.execute(
            "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.kind = 'decorator' AND (s.name LIKE '%route%' OR s.name LIKE '%command%')",
        ).fetchall()
        for r in decorated_files:
            if r["path"] not in entries:
                entries.append(r["path"])

        if entries:
            click.echo("Entry points:")
            for e in (entries if full else entries[:20]):
                click.echo(f"  {e}")
            if not full and len(entries) > 20:
                click.echo(f"  (+{len(entries) - 20} more)")
            click.echo()

        # --- Top symbols by PageRank ---
        top = conn.execute(TOP_SYMBOLS_BY_PAGERANK, (count,)).fetchall()
        if top:
            rows = []
            for s in top:
                sig = format_signature(s["signature"], max_len=50)
                rows.append([
                    abbrev_kind(s["kind"]),
                    s["name"],
                    sig,
                    loc(s["file_path"], s["line_start"]),
                    f"{s['pagerank']:.4f}",
                ])
            click.echo("Top symbols (PageRank):")
            click.echo(format_table(
                ["kind", "name", "signature", "location", "PR"],
                rows,
            ))
        else:
            click.echo("No graph metrics available. Run `roam index` first.")
