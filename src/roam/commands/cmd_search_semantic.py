"""Semantic search: hybrid BM25 + vector ranking + framework packs.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because search-semantic outputs are invocation-scoped
semantic-vector retrieval rankings (top-k symbol matches by hybrid
BM25 + vector + framework-pack score) — not per-location code
violations. The ranked symbols are retrieval results, not findings;
parallel to ``cmd_search`` / ``cmd_retrieve`` SKIP-DISCLOSURE wording.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1221-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json


@roam_capability(
    name="search-semantic",
    category="exploration",
    summary="Find symbols by natural language query (hybrid BM25 + vector + packs)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.argument("query")
@click.option("--top", "--limit", "top_k", default=10, type=int, help="Number of results (default 10)")  # W1142: --limit alias
@click.option("--threshold", default=0.05, type=float, help="Minimum similarity score (default 0.05)")
@click.option(
    "--backend",
    type=click.Choice(["auto", "tfidf", "onnx", "hybrid"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Semantic backend selection.",
)
@click.pass_context
def search_semantic(ctx, query, top_k, threshold, backend):
    """Find symbols by natural language query (hybrid BM25 + vector + packs).

    Unlike ``search`` (which matches exact symbol name substrings), this
    command uses semantic similarity to find conceptually related symbols.
    Query "auth middleware" may return ``validate_token`` or ``check_session``
    even though they share no keywords with the query.
    """
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
            click.echo(
                to_json(
                    json_envelope(
                        "search-semantic",
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
                    )
                )
            )
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
            click.echo(f"  {r['score']:.2f}  {line_info}::{r['name']}       {kind_str}{line_count}{source_hint}")
