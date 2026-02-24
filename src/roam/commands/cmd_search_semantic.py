"""Semantic search: hybrid BM25 + vector ranking + framework packs."""

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
@click.option(
    "--backend",
    type=click.Choice(["auto", "tfidf", "onnx", "hybrid"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Semantic backend selection.",
)
@click.pass_context
def search_semantic(ctx, query, top_k, threshold, backend):
    """Find symbols by natural language query (hybrid BM25 + vector + packs)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    backend = (backend or "auto").lower()

    with open_db(readonly=True) as conn:
        # Hybrid BM25+vector primary, with explicit backend control.
        try:
            from roam.search.index_embeddings import search_stored
            results = search_stored(
                conn,
                query,
                top_k=top_k,
                semantic_backend=backend,
            )
        except Exception:
            results = []

        if not results and backend in {"auto", "tfidf", "hybrid"}:
            from roam.search.tfidf import search as tfidf_search
            results = tfidf_search(conn, query, top_k=top_k)

        # Apply threshold filter
        results = [r for r in results if r["score"] >= threshold]
        pack_matches = sum(1 for r in results if r.get("source") == "pack")

        if json_mode:
            click.echo(to_json(json_envelope("search-semantic",
                summary={
                    "verdict": f'{len(results)} matches for "{query}"',
                    "query": query,
                    "total_matches": len(results),
                    "pack_matches": pack_matches,
                    "backend_requested": backend,
                },
                results=[
                    {
                        "score": r["score"],
                        "name": r["name"],
                        "file_path": r["file_path"],
                        "kind": r["kind"],
                        "line_start": r["line_start"],
                        "line_end": r.get("line_end"),
                        "source": r.get("source", "code"),
                        "pack": r.get("pack"),
                    }
                    for r in results
                ],
            )))
            return

        # --- Text output ---
        click.echo(f'VERDICT: {len(results)} matches for "{query}" (backend={backend})')

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
            source_hint = ""
            if r.get("source") == "pack":
                source_hint = f"  [pack:{r.get('pack', 'unknown')}]"
            click.echo(
                f"  {r['score']:.2f}  {line_info}::{r['name']}"
                f"       {kind_str}{line_count}{source_hint}"
            )
