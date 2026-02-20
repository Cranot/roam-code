"""Semantic search: find symbols by natural language query using TF-IDF."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.argument("query")
@click.option("--top", "top_k", default=10, type=int,
              help="Number of results (default 10)")
@click.option("--threshold", default=0.05, type=float,
              help="Minimum similarity score (default 0.05)")
@click.pass_context
def search_semantic(ctx, query, top_k, threshold):
    """Find symbols by natural language query (TF-IDF semantic search)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Try stored vectors first; fall back to live computation
        try:
            from roam.search.index_embeddings import search_stored
            results = search_stored(conn, query, top_k=top_k)
        except Exception:
            results = []

        if not results:
            from roam.search.tfidf import search as tfidf_search
            results = tfidf_search(conn, query, top_k=top_k)

        # Apply threshold filter
        results = [r for r in results if r["score"] >= threshold]

        if json_mode:
            click.echo(to_json(json_envelope("search-semantic",
                summary={
                    "verdict": f'{len(results)} matches for "{query}"',
                    "query": query,
                    "total_matches": len(results),
                },
                results=[
                    {
                        "score": r["score"],
                        "name": r["name"],
                        "file_path": r["file_path"],
                        "kind": r["kind"],
                        "line_start": r["line_start"],
                        "line_end": r.get("line_end"),
                    }
                    for r in results
                ],
            )))
            return

        # --- Text output ---
        click.echo(f'VERDICT: {len(results)} matches for "{query}"')

        if not results:
            click.echo("  (no matches above threshold)")
            return

        click.echo("")
        for r in results:
            kind_str = abbrev_kind(r["kind"])
            line_info = loc(r["file_path"], r["line_start"])
            line_count = ""
            if r.get("line_end") and r["line_start"]:
                lines = r["line_end"] - r["line_start"] + 1
                line_count = f"  {lines} lines"
            click.echo(
                f"  {r['score']:.2f}  {line_info}::{r['name']}"
                f"       {kind_str}{line_count}"
            )
