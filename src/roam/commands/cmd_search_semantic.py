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

# ---------------------------------------------------------------------------
# W607-BO substrate-CALL boundaries (ADDITIVE to W607-A)
# ---------------------------------------------------------------------------
# Module-level helpers that delegate to the underlying semantic-search
# substrate. Tests monkeypatch THESE shims (not the substrate modules) so
# the W607-BO marker plumbing inside ``search_semantic`` can disclose
# substrate-CALL failures without colliding with the existing W607-A
# outer-guard (``semantic_search_stored_failed:``).
#
# Each shim accepts the same arguments as the underlying substrate call
# and returns the same result. A raise inside any shim becomes a
# ``search_semantic_<phase>_failed:<exc_class>:<detail>`` marker via the
# ``_run_check_bo`` closure inside the click command body.
#
# EMBEDDING DEGRADATION test relies on the separation between
# ``_compute_embedding_search`` (ONNX/hybrid backend pathway) and
# ``_cosine_rank_tfidf`` (lexical-only fallback). When the embedding
# search raises, the wrapper degrades to the tfidf fallback so the agent
# still receives lexical results rather than a wholesale empty envelope.


def _load_search_semantic_config():
    """W607-BO substrate-CALL: configuration load.

    cmd_search_semantic does not currently consume a dedicated config
    block (the four Click options carry the full state), so this shim
    returns an empty dict. The wrapper exists for parity with sibling
    W607-* layers (cmd_retrieve W607-BI ``_load_retrieve_config``,
    cmd_context W607-BF) so a future config addition can land without
    re-instrumenting the marker plumbing.
    """
    return {}


def _compute_semantic_coverage(conn):
    """W607-BO substrate-CALL: semantic-coverage diagnostic.

    Mirrors cmd_retrieve W607-BI ``_compute_semantic_coverage`` so the
    two embedding-aware consumers expose the same diagnostic boundary
    on their envelopes. Tests can monkeypatch this to simulate a
    semantic-substrate fault.
    """
    from roam.retrieve.semantic import semantic_coverage

    return semantic_coverage(conn)


def _compute_embedding_search(conn, query, *, top_k, semantic_backend, warnings_out):
    """W607-BO substrate-CALL: ONNX/hybrid embedding search (search_stored).

    Delegates to ``search_stored``. On raise, the W607-BO wrapper
    retries with ``_cosine_rank_tfidf`` so an ONNX-runtime fault or
    embedding-table corruption still emits lexical results.
    """
    from roam.search.index_embeddings import search_stored

    return search_stored(
        conn,
        query,
        top_k=top_k,
        semantic_backend=semantic_backend,
        warnings_out=warnings_out,
    )


def _cosine_rank_tfidf(conn, query, *, top_k):
    """W607-BO substrate-CALL: TF-IDF cosine-rank fallback.

    Reached when ``_compute_embedding_search`` returned no results
    (empty corpus / hybrid disabled) AND the requested backend is in
    {auto, tfidf, hybrid}. Surfaces a marker on raise + degrades to an
    empty list so the envelope still emits cleanly.
    """
    from roam.search.tfidf import tfidf_search

    return tfidf_search(conn, query, top_k=top_k)


def _apply_threshold(results, threshold):
    """W607-BO substrate-CALL: threshold filter on result scores.

    A raise here (malformed score row, non-numeric score) becomes the
    ``search_semantic_apply_threshold_failed:`` marker; degraded default
    returns the unfiltered results so the envelope still emits.
    """
    return [r for r in results if r["score"] >= threshold]


def _extract_spans(results):
    """W607-BO substrate-CALL: span-shape extraction + pack-match count.

    Builds the JSON-result dict-list AND counts pack-source matches.
    Returns ``(spans, pack_matches)`` so a raise inside the formatter
    surfaces as a structured marker rather than a Click traceback.
    """
    spans = [
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
    ]
    pack_matches = sum(1 for r in results if r.get("source") == "pack")
    return spans, pack_matches


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
@click.option(
    "--top", "--limit", "top_k", default=10, type=int, help="Number of results (default 10)"
)  # W1142: --limit alias
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

    # W607-A: Pattern-2 consumer-layer wiring — thread the warnings_out
    # bucket onto the W605 producer-floor plumbed in
    # ``search/index_embeddings.search_stored``. The producer-side substrate
    # accumulates ``semantic_*`` markers (semantic_fts_check_failed /
    # semantic_fts_query_failed / semantic_tfidf_check_failed /
    # semantic_onnx_check_failed / semantic_vector_decode_failed /
    # semantic_pack_search_failed); without this thread the markers were
    # generated and dropped on the floor before the envelope was emitted.
    # Canonical Pattern-2 disclosure idiom mirrors cmd_complexity (W1086)
    # and cmd_dark_matter: empty bucket -> envelope unchanged (hash-stable);
    # non-empty bucket -> summary.warnings_out populated +
    # summary.partial_success flipped True.
    warnings_out: list[str] = []

    # W607-BO: ADDITIVE per-phase substrate-CALL marker plumbing on top of
    # the W607-A outer-guard above. cmd_search_semantic is the embedding-
    # based sibling of cmd_retrieve (W607-BI). A silent failure in any of
    # its substrate boundaries (config load, semantic-coverage diagnostic,
    # embedding-search compute, cosine-rank tfidf fallback, threshold
    # filter, span extraction, serialize) directly degrades agent
    # productivity. W607-BO wraps each substrate call so a raise becomes
    # a structured ``search_semantic_<phase>_failed:<exc_class>:<detail>``
    # marker instead of a wholesale Click traceback. The W607-A
    # outer-guard remains for ``semantic_search_stored_failed:`` as a
    # final safety net.
    #
    # Empty W607-BO bucket -> byte-identical envelope (hash-stable).
    _w607bo_warnings_out: list[str] = []

    def _run_check_bo(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BO marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``search_semantic_<phase>_failed:<exc_class>:
        <detail>`` marker via ``_w607bo_warnings_out`` and return
        *default* — the envelope still emits cleanly with the remaining
        substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bo_warnings_out.append(f"search_semantic_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BO load_config substrate-CALL. cmd_search_semantic has no
    # dedicated config block today; the wrapper exists for parity so
    # a future config addition can land without re-instrumenting.
    _cfg = _run_check_bo("load_config", _load_search_semantic_config, default={})

    with open_db(readonly=True) as conn:
        # W607-BO compute_semantic_coverage substrate-CALL: diagnostic only
        # (no behavioural branch). Mirrors cmd_retrieve W607-BI. Surfaces
        # ONNX/embedding-table substrate faults independently of the
        # search_stored hot path so agents see WHEN embedding coverage
        # broke (vs WHEN search itself broke).
        _semantic_diag = _run_check_bo(
            "compute_semantic_coverage",
            _compute_semantic_coverage,
            conn,
            default={"embeddings": 0, "coverage_pct": 0.0, "ready": False},
        )

        # W607-BO compute_embedding substrate-CALL with cosine_rank
        # degradation fallback. ``_compute_embedding_search`` runs the
        # full hybrid (BM25 + vector + packs) pipeline. If that raises
        # (e.g. ONNX runtime unavailable, embedding-table corruption),
        # the W607-A outer-guard captures the marker AND the W607-BO
        # marker surfaces both. We retry via ``_cosine_rank_tfidf`` so
        # agents still receive lexical-only results rather than a
        # wholesale empty envelope.
        _captured_search_exceptions: dict = {}

        def _embedding_search_capture(*args, **kwargs):
            try:
                return _compute_embedding_search(*args, **kwargs)
            except Exception as exc:
                _captured_search_exceptions["embedding"] = exc
                # Preserve the W607-A outer-guard marker shape as well
                # so the established outer-guard contract still holds.
                warnings_out.append(f"semantic_search_stored_failed:{type(exc).__name__}:{exc}")
                raise

        results = _run_check_bo(
            "compute_embedding",
            _embedding_search_capture,
            conn,
            query,
            top_k=top_k,
            semantic_backend=backend,
            warnings_out=warnings_out,
            default=None,
        )
        if results is None:
            results = []

        if not results and backend in {"auto", "tfidf", "hybrid"}:
            # W607-BO cosine_rank substrate-CALL: the lexical/TF-IDF
            # fallback. Reached when embedding search returned no
            # results (either organically or after a degradation
            # capture). A raise here becomes a structured marker; the
            # default empty list keeps the envelope clean.
            results = _run_check_bo(
                "cosine_rank",
                _cosine_rank_tfidf,
                conn,
                query,
                top_k=top_k,
                default=[],
            )

        # W607-BO apply_threshold substrate-CALL: filter results by the
        # ``--threshold`` floor. A raise surfaces a marker + degrades
        # to the unfiltered list so the envelope still emits.
        results = _run_check_bo(
            "apply_threshold",
            _apply_threshold,
            results,
            threshold,
            default=results,
        )

        # W607-BO extract_spans substrate-CALL: build the JSON-result
        # span list AND count pack-source matches. A raise surfaces a
        # marker + degrades to an empty span list (pack_matches=0).
        spans, pack_matches = _run_check_bo(
            "extract_spans",
            _extract_spans,
            results,
            default=([], 0),
        )

        if json_mode:
            summary: dict = {
                "verdict": f'{len(results)} matches for "{query}"',
                "query": query,
                "total_matches": len(results),
                "pack_matches": pack_matches,
                "backend_requested": backend,
            }
            # W607-A + W607-BO combined disclosure: merge BOTH buckets
            # so consumers see every marker (outer-guard
            # ``semantic_*`` + per-substrate
            # ``search_semantic_<phase>_*``). Empty combined bucket
            # -> byte-identical envelope (hash-stable).
            combined = list(warnings_out) + list(_w607bo_warnings_out)
            if combined:
                # Pattern-2 disclosure: surface the markers AND flip
                # partial_success so consumers can distinguish "clean
                # search" from "search ran with substrate degradation".
                summary["warnings_out"] = list(combined)
                summary["partial_success"] = True
            envelope = json_envelope(
                "search-semantic",
                summary=summary,
                results=spans,
                **({"warnings_out": list(combined)} if combined else {}),
            )
            # W607-BO serialize_envelope substrate-CALL: wrap to_json so
            # a serialize raise falls back to a minimal envelope rather
            # than crashing the entire search-semantic call. Mirrors
            # cmd_retrieve W607-BI ``serialize_envelope`` discipline.
            text = _run_check_bo(
                "serialize_envelope",
                lambda: to_json(envelope),
                default=None,
            )
            if text is None:
                final_combined = list(warnings_out) + list(_w607bo_warnings_out)
                text = to_json(
                    json_envelope(
                        "search-semantic",
                        summary={
                            "verdict": "search-semantic serialize failed",
                            "warnings_out": list(final_combined),
                            "partial_success": True,
                        },
                        warnings_out=list(final_combined),
                    )
                )
            click.echo(text)
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
