"""Build or rebuild the codebase index."""

import time
from collections import Counter

import click

from roam.output.formatter import to_json, json_envelope


@click.command()
@click.option('--force', is_flag=True, help='Force full reindex')
@click.option('--verbose', is_flag=True, help='Show detailed warnings during indexing')
@click.pass_context
def index(ctx, force, verbose):
    """Build or rebuild the codebase index."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    from roam.index.indexer import Indexer
    from roam.db.connection import open_db, db_exists
    t0 = time.monotonic()
    indexer = Indexer()
    indexer.run(force=force, verbose=verbose)
    elapsed = time.monotonic() - t0

    if not json_mode:
        click.echo(f"Index complete. ({elapsed:.1f}s)")

    # Show summary stats
    if db_exists():
        with open_db(readonly=True) as conn:
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

            lang_rows = conn.execute(
                "SELECT language, COUNT(*) as cnt FROM files WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
            ).fetchall()

            avg_sym = sym_count / file_count if file_count else 0

            # Parse coverage: only count files with a parseable language
            from roam.languages.registry import _SUPPORTED_LANGUAGES
            parseable_langs = ",".join(f"'{l}'" for l in _SUPPORTED_LANGUAGES)
            parseable_count = conn.execute(
                f"SELECT COUNT(*) FROM files WHERE language IN ({parseable_langs})"
            ).fetchone()[0]
            parsed_ok = conn.execute(
                f"SELECT COUNT(DISTINCT f.id) FROM files f "
                f"JOIN symbols s ON s.file_id = f.id "
                f"WHERE f.language IN ({parseable_langs})"
            ).fetchone()[0]
            coverage = (parsed_ok * 100 / parseable_count) if parseable_count else 0

            if json_mode:
                click.echo(to_json(json_envelope("index",
                    summary={
                        "files": file_count,
                        "symbols": sym_count,
                        "edges": edge_count,
                    },
                    elapsed_s=round(elapsed, 1),
                    files=file_count,
                    symbols=sym_count,
                    edges=edge_count,
                    languages={r["language"]: r["cnt"] for r in lang_rows[:8]},
                    avg_symbols_per_file=round(avg_sym, 1),
                    parse_coverage_pct=round(coverage, 0),
                )))
            else:
                lang_str = ", ".join(f"{r['language']}={r['cnt']}" for r in lang_rows[:8])
                click.echo(f"  Files: {file_count}  Symbols: {sym_count}  Edges: {edge_count}")
                click.echo(f"  Languages: {lang_str}")
                click.echo(f"  Avg symbols/file: {avg_sym:.1f}  Parse coverage: {coverage:.0f}%")

            # Auto-snapshot after every index for trend tracking
            try:
                from roam.commands.metrics_history import append_snapshot
                with open_db() as wconn:
                    snap = append_snapshot(wconn, source="index")
                if not json_mode:
                    click.echo(f"  Health: {snap['health_score']}/100 (snapshot saved)")
            except Exception:
                pass  # Don't fail indexing if snapshot fails
