"""Hybrid semantic search: FTS5/BM25 + TF-IDF vector fusion."""

from __future__ import annotations

import heapq
import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Callable, TypeAlias

from roam.observability import log_swallowed
from roam.search.framework_packs import packs_for_languages, search_pack_symbols
from roam.search.tfidf import _RE_CAMEL_SPLIT, _RE_UPPER_SPLIT, cosine_similarity, tokenize

# W901: ``_camel_split`` is imported by ``roam.retrieve.seeds`` (single
# source of truth across the search/retrieve boundary). The leading
# underscore is preserved to avoid churning the 4 internal call-sites,
# but ``__all__`` declares it intentionally exported so private-access
# lints don't flag the cross-module import.
__all__ = ["_camel_split", "SearchOptions", "WarningsOut"]

# W605: Pattern-2 silent-fallback warnings accumulator type. Mirrors the
# substrate-floor type contract in ``roam.db.connection`` (W603) and
# ``roam.output.formatter`` (W1043). The alias is duplicated locally
# rather than imported from ``roam.output.formatter`` because the
# search substrate is on every command's hot read path (cmd_retrieve /
# cmd_search / cmd_search_semantic / retrieve.seeds) and formatter.py
# is ~50KB of import surface; the same hot-path-cost rationale that
# justifies the W603 local duplication applies here. W907 verify-cycle
# check: formatter.py has NO top-level roam imports (verified by grep
# ``^from roam`` returning empty), so the duplication is a cost choice
# not a false-cycle hedge.
WarningsOut: TypeAlias = list[str] | None


@dataclass(frozen=True)
class SearchOptions:
    """Configuration knobs for :func:`search_stored`.

    Bundling the optional search configuration into one object keeps
    ``search_stored``'s parameter list under the long-params threshold
    (``roam.catalog.smells.detect_long_params``). Field defaults mirror
    the legacy per-parameter defaults, so ``SearchOptions()`` reproduces
    the old all-defaults behaviour exactly.
    """

    top_k: int = 10
    include_packs: bool = True
    packs: list[str] | None = None
    semantic_backend: str = "auto"
    project_root: object | None = None


# ---------------------------------------------------------------------------
# camelCase preprocessing for FTS5 tokenizer
# ---------------------------------------------------------------------------


def _camel_split(text: str | None) -> str:
    """Insert spaces at camelCase/PascalCase boundaries for FTS5.

    ``OpenDatabase`` -> ``Open Database``
    ``XMLParser``    -> ``XML Parser``
    ``file_path``    -> ``file_path`` (underscores handled by unicode61)

    W929: the two regex patterns are reused from the pre-compiled module-level
    constants in ``roam.search.tfidf`` so the camelCase / PascalCase split
    semantics stay in lockstep across the search/retrieve boundary. A drift
    here previously meant two regex bodies could diverge silently.

    W1029: ``text`` accepts ``None`` so callers can pass raw SQL row values
    without the cargo-cult ``or ""`` defensive wrapper. Returns ``""`` on
    ``None``/empty.
    """
    if not text:
        return ""
    result = _RE_CAMEL_SPLIT.sub(r"\1 \2", text)
    result = _RE_UPPER_SPLIT.sub(r"\1 \2", result)
    return result


# ---------------------------------------------------------------------------
# FTS5 availability detection
# ---------------------------------------------------------------------------


def fts5_available(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> bool:
    """Check if the symbol_fts virtual table exists and is usable.

    When ``warnings_out`` is threaded in, a SQLite substrate failure
    (corrupted ``sqlite_master`` row, locked DB) emits the
    ``semantic_fts_check_failed:symbol_fts:<exc_class>:<detail>``
    marker before the silent ``return False`` so operators see WHY
    BM25-ranked search is missing. ``warnings_out=None`` preserves the
    legacy silent fallback (the substrate floor exposes this on every
    command's read path).
    """
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except sqlite3.Error as exc:
        if warnings_out is not None:
            warnings_out.append(f"semantic_fts_check_failed:symbol_fts:{type(exc).__name__}:{exc}")
        return False


def fts5_populated(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> bool:
    """Check if symbol_fts has data.

    Plumbs ``warnings_out`` to ``fts5_available`` and emits its own
    marker on a COUNT-query substrate failure (locked table, broken
    virtual-table state). A legitimately-empty corpus (cold start
    pre-index) returns ``False`` SILENTLY because that is the expected
    path, not a substrate corruption signal.
    """
    if not fts5_available(conn, warnings_out=warnings_out):
        return False
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbol_fts").fetchone()
        return row is not None and row[0] > 0
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"semantic_fts_check_failed:symbol_fts_count:{type(exc).__name__}:{exc}")
        return False


def tfidf_populated(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> bool:
    """Check if symbol_tfidf has data.

    Plumbs ``warnings_out`` for substrate-corruption disclosure
    (missing table, locked DB). A legitimately-empty TF-IDF table
    (cold start, never indexed) returns ``False`` SILENTLY.
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbol_tfidf").fetchone()
        return row is not None and row[0] > 0
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"semantic_tfidf_check_failed:symbol_tfidf:{type(exc).__name__}:{exc}")
        return False


def onnx_populated(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> bool:
    """Check if ONNX embedding table has data.

    Plumbs ``warnings_out`` for substrate-corruption disclosure
    (missing table, locked DB). A legitimately-empty embeddings table
    (ONNX backend not installed / not enabled) returns ``False``
    SILENTLY — the fallback-contracts arc already discloses the
    degraded-but-correct contract loudly at the backend-readiness
    layer (``_onnx_ready``).
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbol_embeddings WHERE provider='onnx'").fetchone()
        return row is not None and row[0] > 0
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"semantic_onnx_check_failed:symbol_embeddings:{type(exc).__name__}:{exc}")
        return False


def _load_semantic_settings(project_root=None) -> dict[str, Any]:
    """Load semantic backend settings with env-over-config precedence."""
    from roam.search.onnx_embeddings import load_semantic_settings

    return load_semantic_settings(project_root=project_root)


def _onnx_ready(project_root=None, settings=None):
    """Return ONNX backend readiness status."""
    from roam.search.onnx_embeddings import onnx_ready

    return onnx_ready(project_root=project_root, settings=settings)


def _get_onnx_embedder(project_root=None, settings=None):
    """Return ONNX embedder if backend is configured and dependencies are present."""
    from roam.search.onnx_embeddings import get_onnx_embedder

    return get_onnx_embedder(project_root=project_root, settings=settings)


# ---------------------------------------------------------------------------
# FTS5 index build (called during `roam index`)
# ---------------------------------------------------------------------------


def build_fts_index(
    conn: sqlite3.Connection,
    project_root: str | None = None,
    *,
    force: bool = False,
):
    """Populate the FTS5 symbol_fts table for BM25-ranked search.

    Pushes tokenization and indexing entirely to SQLite's C engine.
    Also persists TF-IDF vectors for hybrid ranking.

    R9.B7 — incremental sync. Instead of DELETE-then-INSERT on every
    build, this now diffs ``symbols`` against ``symbol_fts`` and only
    issues the INSERT/DELETE work that's actually needed:

    * symbols whose rowid isn't in symbol_fts → INSERT (new entries)
    * symbol_fts rowids that no longer exist in symbols → DELETE
    * untouched rows pay zero cost

    On a 100K-symbol monorepo with 50 changed files, the per-index
    FTS5 work drops from ~6s (full rebuild) to ~80ms (diff-and-sync)
    after the first run.

    Pass ``force=True`` to bypass the diff and do a full DELETE+INSERT
    rebuild — useful after schema migrations or `roam index --rebuild`.
    """
    if not fts5_available(conn):
        build_and_store_tfidf(conn)
        try:
            build_and_store_onnx_embeddings(conn, project_root=project_root)
        except (ImportError, RuntimeError, OSError, sqlite3.Error) as exc:
            # ImportError/RuntimeError: ONNX backend optional and may be
            # absent (line 119 of onnx_embeddings.py raises RuntimeError on
            # missing deps). OSError: missing model/tokenizer files.
            # sqlite3.Error: embedding table write conflict.
            # Programmer errors propagate per W531 fail-loud discipline.
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
            # TF-IDF path stays authoritative, but a genuine ONNX build error
            # (vs documented absence) gets a discoverable lineage signal.
            log_swallowed(f"search.index_embeddings:build_fts:onnx:no_fts5:{type(exc).__name__}", exc)
        return

    # The symbol_fts schema now includes a ``docstring`` column (audit B8); the
    # ensure_schema migration drops the old table and recreates it the first
    # time this runs after the upgrade.
    if force:
        conn.execute("DELETE FROM symbol_fts")

    # Snapshot the live rowids on both sides. An empty FTS5 table is the
    # cold-start case (covers both first-ever-index and post-`force`).
    fts_rowids: set[int] = {r[0] for r in conn.execute("SELECT rowid FROM symbol_fts").fetchall()}
    sym_rowids: set[int] = {r[0] for r in conn.execute("SELECT id FROM symbols").fetchall()}

    # 1) DELETE rowids that left the symbols table (file removals, renames).
    stale = fts_rowids - sym_rowids
    if stale:
        from roam.db.connection import batched_in

        # batched_in is a SELECT helper; for DELETE we chunk ourselves
        # to avoid the SQLite parameter-count limit.
        stale_list = list(stale)
        chunk = 400
        for i in range(0, len(stale_list), chunk):
            slice_ = stale_list[i : i + chunk]
            placeholders = ",".join("?" * len(slice_))
            conn.execute(
                f"DELETE FROM symbol_fts WHERE rowid IN ({placeholders})",
                slice_,
            )

    # 2) INSERT rowids that exist in symbols but not in FTS5 (new + modified).
    fresh = sym_rowids - fts_rowids
    if not fresh:
        # Sync was already complete — vector signals + ONNX still need rebuild
        # because those are authoritative-no-diff stores.
        build_and_store_tfidf(conn)
        try:
            build_and_store_onnx_embeddings(conn, project_root=project_root)
        except (ImportError, RuntimeError, OSError, sqlite3.Error) as exc:
            # See narrowing rationale on the cold-start branch above.
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
            # genuine ONNX build error is surfaced even on the no-diff path.
            log_swallowed(f"search.index_embeddings:build_fts:onnx:no_diff:{type(exc).__name__}", exc)
        return

    from roam.db.connection import batched_in

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, s.qualified_name, s.signature, s.docstring, s.kind, "
        "f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        list(fresh),
    )

    # Insert with camelCase preprocessing for better tokenization. Docstring
    # is left as-is (no camel-split) — natural-language text doesn't benefit
    # from token splitting and porter stemming handles it correctly.
    # Build the full parameter list first so the SQLite write happens as one
    # executemany boundary instead of inside the per-row loop (N+1 I/O).
    records = [
        (
            row["id"],
            _camel_split(row["name"]),
            _camel_split(row["qualified_name"]),
            _camel_split(row["signature"]),
            row["docstring"] or "",
            row["kind"] or "",
            row["file_path"] or "",
        )
        for row in rows
    ]
    conn.executemany(
        "INSERT INTO symbol_fts(rowid, name, qualified_name, signature, docstring, kind, file_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        records,
    )

    # Keep vector signals available even when FTS5 exists (hybrid #54).
    build_and_store_tfidf(conn)
    # Optional dense local embeddings (ONNX) for semantic search (#56).
    try:
        build_and_store_onnx_embeddings(conn, project_root=project_root)
    except Exception as exc:
        # ONNX is optional; TF-IDF path remains authoritative fallback.
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a genuine
        # ONNX build error (vs documented backend absence) is surfaced so a
        # silently-missing semantic index has a discoverable cause.
        log_swallowed(f"search.index_embeddings:build_fts:onnx:fts5:{type(exc).__name__}", exc)


# ---------------------------------------------------------------------------
# FTS5 search (primary path)
# ---------------------------------------------------------------------------

# BM25 column weights — order MUST match _FTS5_SCHEMA_COLUMNS in
# db/connection.py: name, qualified_name, signature, docstring, kind, file_path.
# Audit B8: docstring weight 4 — high enough that natural-language
# queries match docstring text but lower than name/qname so a query
# matching both still ranks the name-match higher.
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 4.0, 1.0, 3.0"

# Hybrid fusion defaults (backlog #54).
_HYBRID_LEXICAL_WEIGHT = 0.65
_HYBRID_SEMANTIC_WEIGHT = 0.35
_HYBRID_RANK_WEIGHT = 0.40
_HYBRID_RRF_K = 60
_HYBRID_MIN_CANDIDATES = 25
_HYBRID_CANDIDATE_MULTIPLIER = 4
_PACK_SEARCH_RECOVERABLE_ERRORS = (ImportError, OSError, RuntimeError, ValueError)

_FTS5_SEARCH_SQL = f"""
    SELECT sf.rowid as symbol_id,
           -bm25(symbol_fts, {_BM25_WEIGHTS}) as score,
           s.name, f.path as file_path, s.kind, s.line_start, s.line_end
    FROM symbol_fts sf
    JOIN symbols s ON sf.rowid = s.id
    JOIN files f ON s.file_id = f.id
    WHERE symbol_fts MATCH ?
    ORDER BY bm25(symbol_fts, {_BM25_WEIGHTS})
    LIMIT ?
"""


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Search using FTS5 BM25 ranking (fast, all in C).

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.

    When ``warnings_out`` is threaded in:

    * The first-pass FTS5 query failure emits
      ``semantic_fts_query_failed:<query>:<exc_class>:<detail>`` so a
      malformed expression / locked-table / broken-FTS5 substrate is
      disclosed even though the prefix-only fallback may rescue it.
    * The second-pass prefix-only fallback failure emits
      ``semantic_fts_query_failed:<prefix_query>:fallback:<exc>`` —
      both passes failed, the caller sees an empty list AND a marker
      explaining why.

    An empty query / empty fts_query stays SILENT (legitimate filter,
    not a substrate failure).
    """
    if not query or not query.strip():
        return []

    # Preprocess query: camelCase split + escape FTS5 special chars
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    rows, exc = _run_fts_pass(conn, fts_query, top_k)
    if rows is None:
        # FTS5 query syntax error — fall back to prefix match
        _append_fts_failure(warnings_out, fts_query, exc)
        fts_query = _build_fts_query(query, prefix_only=True)
        if not fts_query:
            return []
        rows, exc = _run_fts_pass(conn, fts_query, top_k)
        if rows is None:
            _append_fts_failure(warnings_out, fts_query, exc, fallback=True)
            return []

    results = []
    for row in rows:
        results.append(
            {
                "score": round(row["score"], 4),
                "symbol_id": row["symbol_id"],
                "name": row["name"],
                "file_path": row["file_path"],
                "kind": row["kind"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
            }
        )
    return results


def _run_fts_pass(
    conn: sqlite3.Connection,
    fts_query: str,
    top_k: int,
) -> tuple[list | None, Exception | None]:
    """Run one FTS5 MATCH pass; return ``(rows, exc)``.

    ``rows`` is ``None`` when the pass failed (so the caller can
    distinguish a broken query/substrate from a legitimate empty
    result); ``exc`` carries the caught exception for the caller to
    disclose via :func:`_append_fts_failure`, or ``None`` on success.
    Splitting failure *detection* (here) from failure *disclosure*
    (the helper) keeps this pass-executor to its three SQL inputs.
    """
    try:
        return conn.execute(_FTS5_SEARCH_SQL, (fts_query, top_k)).fetchall(), None
    except Exception as exc:
        return None, exc


def _append_fts_failure(
    warnings_out: WarningsOut,
    fts_query: str,
    exc: Exception,
    *,
    fallback: bool = False,
) -> None:
    """Append a ``semantic_fts_query_failed`` marker for a failed FTS5 pass.

    Owns the marker format + the ``fallback:`` segment so the
    pass-executor (:func:`_run_fts_pass`) stays free of disclosure
    plumbing. A ``None`` sink preserves the legacy silent fallback.
    """
    if warnings_out is None:
        return
    tag = "fallback:" if fallback else ""
    warnings_out.append(f"semantic_fts_query_failed:{fts_query}:{tag}{type(exc).__name__}:{exc}")


def _build_fts_query(query: str, prefix_only: bool = False) -> str:
    """Convert a natural language query into an FTS5 MATCH expression.

    - Splits camelCase
    - Removes FTS5 special characters
    - Joins tokens with implicit AND (FTS5 default)
    - Adds prefix matching (``*``) for the last token for typeahead
    """
    preprocessed = _camel_split(query)
    # Remove FTS5 special syntax chars: ^, *, ", (, ), {, }, :
    cleaned = re.sub(r"[^\w\s]", " ", preprocessed)
    tokens = cleaned.split()
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return ""

    if prefix_only:
        return " ".join(f'"{t}"*' for t in tokens)

    # Last token gets prefix matching for typeahead behavior
    parts = [f'"{t}"' for t in tokens[:-1]]
    parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Unified search: hybrid BM25 + TF-IDF vector fusion
# ---------------------------------------------------------------------------


def _normalize_scores(results: list[dict]) -> dict[int, float]:
    """Normalize result scores to [0,1] by list max score."""
    if not results:
        return {}
    max_score = max(float(r.get("score", 0.0) or 0.0) for r in results)
    if max_score <= 0:
        return {int(r["symbol_id"]): 0.0 for r in results if r.get("symbol_id") is not None}
    return {
        int(r["symbol_id"]): float(r.get("score", 0.0) or 0.0) / max_score
        for r in results
        if r.get("symbol_id") is not None
    }


def _fuse_hybrid_results(
    lexical_results: list[dict],
    semantic_results: list[dict],
    top_k: int,
) -> list[dict]:
    """Fuse BM25 and TF-IDF rankings with weighted RRF + score blending."""
    if not lexical_results and not semantic_results:
        return []

    lex_rank = {int(r["symbol_id"]): idx + 1 for idx, r in enumerate(lexical_results) if r.get("symbol_id") is not None}
    sem_rank = {
        int(r["symbol_id"]): idx + 1 for idx, r in enumerate(semantic_results) if r.get("symbol_id") is not None
    }
    lex_norm = _normalize_scores(lexical_results)
    sem_norm = _normalize_scores(semantic_results)
    lex_meta = {int(r["symbol_id"]): r for r in lexical_results if r.get("symbol_id") is not None}
    sem_meta = {int(r["symbol_id"]): r for r in semantic_results if r.get("symbol_id") is not None}

    max_rrf = 1.0 / (_HYBRID_RRF_K + 1)
    merged: list[dict] = []
    for sid in sorted(set(lex_rank) | set(sem_rank)):
        lr = lex_rank.get(sid)
        sr = sem_rank.get(sid)
        lex_rrf = (1.0 / (_HYBRID_RRF_K + lr)) if lr else 0.0
        sem_rrf = (1.0 / (_HYBRID_RRF_K + sr)) if sr else 0.0
        rank_score = ((_HYBRID_LEXICAL_WEIGHT * lex_rrf) + (_HYBRID_SEMANTIC_WEIGHT * sem_rrf)) / max_rrf
        signal_score = (_HYBRID_LEXICAL_WEIGHT * lex_norm.get(sid, 0.0)) + (
            _HYBRID_SEMANTIC_WEIGHT * sem_norm.get(sid, 0.0)
        )
        score = (_HYBRID_RANK_WEIGHT * rank_score) + ((1.0 - _HYBRID_RANK_WEIGHT) * signal_score)

        meta = lex_meta.get(sid) or sem_meta.get(sid)
        if not meta:
            continue
        merged.append(
            {
                "score": round(score, 4),
                "symbol_id": sid,
                "name": meta["name"],
                "file_path": meta["file_path"],
                "kind": meta["kind"],
                "line_start": meta["line_start"],
                "line_end": meta.get("line_end"),
                "_rank_sort": min(lr or 10_000, sr or 10_000),
            }
        )

    merged.sort(
        key=lambda r: (
            -r["score"],
            r["_rank_sort"],
            r["name"] or "",
            r["symbol_id"],
        )
    )
    for row in merged:
        row.pop("_rank_sort", None)
    return merged[:top_k]


def _repo_languages(conn) -> set[str]:
    """Distinct lowercased file languages in the index (empty on any error)."""
    try:
        rows = conn.execute("SELECT DISTINCT language FROM files WHERE language IS NOT NULL").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[0]).strip().lower() for r in rows if r and r[0]}


def _merge_language_relevant_pack_results(
    conn: sqlite3.Connection,
    query: str,
    semantic_results: list[dict],
    *,
    top_k: int,
    options: SearchOptions,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Add framework-pack hits only when repo languages make them relevant."""
    if not options.include_packs:
        return semantic_results

    effective_packs = options.packs
    if options.packs is None:
        effective_packs = packs_for_languages(_repo_languages(conn))
        if not effective_packs:
            return semantic_results

    try:
        pack_results = search_pack_symbols(query, top_k=top_k, packs=effective_packs)
    except _PACK_SEARCH_RECOVERABLE_ERRORS as exc:
        if warnings_out is not None:
            warnings_out.append(f"semantic_pack_search_failed:{type(exc).__name__}:{exc}")
        return semantic_results

    if not pack_results:
        return semantic_results

    # Only the top candidate_k of the merged list is needed; nsmallest
    # is O(n log candidate_k) and avoids fully sorting the combined
    # list. The (-score, name, symbol_id) key keeps selection stable.
    return heapq.nsmallest(
        top_k,
        semantic_results + pack_results,
        key=lambda r: (
            -r.get("score", 0.0),
            r.get("name", ""),
            r.get("symbol_id", 0),
        ),
    )


def _collect_available_rank_signals_for_fallback(
    conn: sqlite3.Connection,
    query: str,
    *,
    candidate_k: int,
    backend: str,
    project_root: object | None,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], list[dict]]:
    """Probe stored indexes before fallback chooses the ranking shape."""
    lexical_results: list[dict] = []
    onnx_results: list[dict] = []
    tfidf_results: list[dict] = []

    if fts5_populated(conn, warnings_out=warnings_out):
        lexical_results = search_fts(conn, query, top_k=candidate_k, warnings_out=warnings_out)

    if backend in {"auto", "onnx", "hybrid"} and onnx_populated(conn, warnings_out=warnings_out):
        onnx_results = _search_onnx_stored(
            conn,
            query,
            top_k=candidate_k,
            project_root=project_root,
            warnings_out=warnings_out,
        )

    if backend in {"auto", "tfidf", "hybrid"} and tfidf_populated(conn, warnings_out=warnings_out):
        tfidf_results = _search_tfidf_stored(conn, query, top_k=candidate_k, warnings_out=warnings_out)

    return lexical_results, _merge_semantic_results(
        onnx_results,
        tfidf_results,
        top_k=candidate_k,
    )


def search_stored(
    conn,
    query: str,
    options: SearchOptions | None = None,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Search using hybrid BM25+vector fusion with optional framework packs.

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.

    When ``warnings_out`` is threaded in, every sub-branch's silent-
    pass disclosure (FTS5 corruption / vector decode / pack import
    failure) surfaces on the caller's bucket without intermediate
    loss. ``warnings_out=None`` preserves the legacy silent-empty
    behaviour for ~3 callers (cmd_search_semantic, cmd_retrieve,
    retrieve.seeds) that haven't opted in yet.

    Configuration is bundled in ``options`` (:class:`SearchOptions`) so
    the signature stays under the long-params threshold; pass
    ``options=None`` (the default) for the legacy all-defaults behaviour.
    """
    if not query or not query.strip():
        return []

    opts = options if options is not None else SearchOptions()
    top_k = opts.top_k
    semantic_backend = opts.semantic_backend
    project_root = opts.project_root

    candidate_k = max(top_k * _HYBRID_CANDIDATE_MULTIPLIER, _HYBRID_MIN_CANDIDATES)
    backend = (semantic_backend or "auto").strip().lower()
    if backend not in {"auto", "tfidf", "onnx", "hybrid"}:
        backend = "auto"

    lexical_results, semantic_results = _collect_available_rank_signals_for_fallback(
        conn,
        query,
        candidate_k=candidate_k,
        backend=backend,
        project_root=project_root,
        warnings_out=warnings_out,
    )

    semantic_results = _merge_language_relevant_pack_results(
        conn,
        query,
        semantic_results,
        top_k=candidate_k,
        options=opts,
        warnings_out=warnings_out,
    )

    # Hybrid fusion when both signals exist.
    if lexical_results and semantic_results:
        return _fuse_hybrid_results(lexical_results, semantic_results, top_k)

    # Graceful single-branch fallback.
    if lexical_results:
        return lexical_results[:top_k]
    if semantic_results:
        return semantic_results[:top_k]
    return []


# ---------------------------------------------------------------------------
# Legacy TF-IDF paths (fallback for pre-v11 databases)
# ---------------------------------------------------------------------------

# The symbol_tfidf table is defined in roam.db.schema (SCHEMA_SQL) and is
# created by ensure_schema() during open_db().  The helper below is kept for
# callers that operate on standalone connections (e.g. tests, external tools).


def ensure_tfidf_table(conn: sqlite3.Connection):
    """Ensure the symbol_tfidf table exists.

    Delegates to the canonical schema in roam.db.schema so there is a single
    source of truth for the table definition.
    """
    from roam.db.schema import SCHEMA_SQL

    conn.executescript(SCHEMA_SQL)


def _stream_tfidf_records(
    corpus: dict[int, dict[str, float]],
) -> Iterator[tuple[int, str]]:
    """Stream TF-IDF insert rows so persistence spends one SQLite write boundary."""
    for sid, vec in corpus.items():
        yield (sid, json.dumps(vec))


def build_and_store_tfidf(conn: sqlite3.Connection):
    """Compute TF-IDF vectors for all symbols and store in symbol_tfidf.

    Called during ``roam index`` as part of hybrid search index build.
    """
    from roam.search.tfidf import build_corpus

    ensure_tfidf_table(conn)

    corpus = build_corpus(conn)
    if not corpus:
        return

    conn.execute("DELETE FROM symbol_tfidf")
    conn.executemany(
        "INSERT OR REPLACE INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
        _stream_tfidf_records(corpus),
    )


def _build_symbol_embedding_text(row) -> str:
    """Compose symbol text payload for dense embedding."""
    parts = [
        row["name"] or "",
        row["qualified_name"] or "",
        row["signature"] or "",
        row["kind"] or "",
        row["docstring"] or "",
    ]
    return " \n ".join(p for p in parts if p)


def _stream_onnx_embedding_records_for_single_write(
    rows: Sequence[Any],
    vectors: Sequence[Any],
    count: int,
    dims: int,
    model_id: str,
) -> Iterator[tuple[Any, str, int, str, str]]:
    """Stream insert rows so ONNX persistence spends one SQLite write boundary."""
    for idx in range(count):
        yield (
            rows[idx]["id"],
            json.dumps(vectors[idx]),
            dims,
            "onnx",
            model_id,
        )


def build_and_store_onnx_embeddings(conn: sqlite3.Connection, project_root: str | None = None) -> dict[str, Any]:
    """Compute dense ONNX vectors for symbols and store in symbol_embeddings."""
    settings = _load_semantic_settings(project_root=project_root)
    ready, reason, settings = _onnx_ready(project_root=project_root, settings=settings)
    if not ready:
        return {"enabled": False, "reason": reason}

    embedder = _get_onnx_embedder(project_root=project_root, settings=settings)
    if embedder is None:
        return {"enabled": False, "reason": "embedder-unavailable"}

    rows = conn.execute("SELECT id, name, qualified_name, signature, kind, docstring FROM symbols").fetchall()
    if not rows:
        return {"enabled": True, "stored": 0, "dims": 0}

    texts = [_build_symbol_embedding_text(row) for row in rows]
    vectors = embedder.embed_texts(texts)
    if not vectors:
        return {"enabled": True, "stored": 0, "dims": 0}

    count = min(len(rows), len(vectors))
    dims = len(vectors[0]) if vectors and vectors[0] else 0
    model_id = getattr(embedder, "model_id", "onnx-model")

    conn.execute("DELETE FROM symbol_embeddings WHERE provider='onnx'")
    insert_sql = (
        "INSERT OR REPLACE INTO symbol_embeddings (symbol_id, vector, dims, provider, model_id) VALUES (?, ?, ?, ?, ?)"
    )
    conn.executemany(
        insert_sql,
        _stream_onnx_embedding_records_for_single_write(rows, vectors, count, dims, model_id),
    )

    return {
        "enabled": True,
        "stored": count,
        "dims": dims,
        "model_id": model_id,
    }


def load_onnx_vectors(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> dict[int, list[float]]:
    """Load stored ONNX vectors from DB.

    Per-row JSON decode failures silently DROP the corrupt vector
    from the result (preserving caller contract — search still
    returns hits from the surviving vectors). When ``warnings_out``
    is threaded in, every dropped row emits
    ``semantic_vector_decode_failed:onnx:<symbol_id>:<exc_class>:<detail>``
    so operators see the substrate-corruption signal that would
    otherwise be invisible.
    """
    rows = conn.execute("SELECT symbol_id, vector FROM symbol_embeddings WHERE provider='onnx'").fetchall()
    result: dict[int, list[float]] = {}
    for row in rows:
        try:
            vec = json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError) as exc:
            if warnings_out is not None:
                warnings_out.append(f"semantic_vector_decode_failed:onnx:{row['symbol_id']}:{type(exc).__name__}:{exc}")
            continue
        if isinstance(vec, list) and vec:
            result[int(row["symbol_id"])] = [float(v) for v in vec]
    return result


def _cosine_dense(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity for dense vectors."""
    if not vec_a or not vec_b:
        return 0.0
    n = min(len(vec_a), len(vec_b))
    if n == 0:
        return 0.0
    dot = sum(vec_a[i] * vec_b[i] for i in range(n))
    norm_a = math.sqrt(sum(vec_a[i] * vec_a[i] for i in range(n)))
    norm_b = math.sqrt(sum(vec_b[i] * vec_b[i] for i in range(n)))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _score_vector_corpus(
    query_vec: Any,
    vectors: dict[int, Any],
    similarity_fn: Callable[[Any, Any], float],
) -> list[tuple[float, int]]:
    """Score every stored vector against a query vector, keeping positive hits.

    Extracted from the clone cluster across ONNX, TF-IDF stored, and
    TF-IDF corpus search. Callers prepare the query vector and decide
    which similarity function to use; this helper owns the invariant
    "score every vector, filter > 0, preserve (score, symbol_id) pairs".
    """
    scores: list[tuple[float, int]] = []
    for sid, vec in vectors.items():
        sim = similarity_fn(query_vec, vec)
        if sim > 0:
            scores.append((sim, sid))
    return scores


def _symbol_results_preserving_score_order(
    conn: sqlite3.Connection,
    scores: list[tuple[float, int]],
    top_k: int,
) -> list[dict]:
    """Attach symbol metadata without letting DB row order change ranking."""
    if not scores:
        return []

    scores.sort(key=lambda x: -x[0])
    top = scores[:top_k]
    sym_ids = [sid for _, sid in top]
    if not sym_ids:
        return []

    from roam.db.connection import batched_in

    rows = batched_in(
        conn,
        "SELECT s.id, s.name, f.path as file_path, s.kind, s.line_start, s.line_end "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        sym_ids,
    )
    meta: dict[int, Any] = {row["id"]: row for row in rows}

    results = []
    for score, sid in top:
        row = meta.get(sid)
        if row is None:
            continue
        results.append(
            {
                "score": round(score, 4),
                "symbol_id": sid,
                "name": row["name"],
                "file_path": row["file_path"],
                "kind": row["kind"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
            }
        )
    return results


def _search_onnx_stored(
    conn,
    query: str,
    top_k: int = 10,
    project_root=None,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Search using precomputed ONNX vectors + query embedding.

    Threads ``warnings_out`` into ``load_onnx_vectors`` so per-row
    JSON-decode failures surface. The ONNX-not-ready / empty-vector
    branches stay SILENT (legitimate degraded-but-correct fallback
    per the fallback-contracts arc).
    """
    settings = _load_semantic_settings(project_root=project_root)
    ready, _, settings = _onnx_ready(project_root=project_root, settings=settings)
    if not ready:
        return []

    embedder = _get_onnx_embedder(project_root=project_root, settings=settings)
    if embedder is None:
        return []

    query_vecs = embedder.embed_texts([query])
    if not query_vecs:
        return []
    query_vec = query_vecs[0]

    vectors = load_onnx_vectors(conn, warnings_out=warnings_out)
    if not vectors:
        return []

    scores = _score_vector_corpus(query_vec, vectors, _cosine_dense)
    return _symbol_results_preserving_score_order(conn, scores, top_k)


def _merge_semantic_results(*branches: list[dict], top_k: int) -> list[dict]:
    """Merge semantic result branches by symbol id, keeping max score."""
    merged: dict[int, dict] = {}
    for branch in branches:
        for row in branch:
            sid = row.get("symbol_id")
            if sid is None:
                continue
            existing = merged.get(int(sid))
            if existing is None or float(row.get("score", 0.0)) > float(existing.get("score", 0.0)):
                merged[int(sid)] = dict(row)

    values = list(merged.values())
    values.sort(
        key=lambda r: (
            -float(r.get("score", 0.0)),
            r.get("name", ""),
            int(r.get("symbol_id", 0)),
        )
    )
    return values[:top_k]


def load_tfidf_vectors(conn: sqlite3.Connection, *, warnings_out: WarningsOut = None) -> dict[int, dict[str, float]]:
    """Load stored TF-IDF vectors from DB.

    Per-row JSON decode failures silently DROP the corrupt vector
    from the result (preserving caller contract). When
    ``warnings_out`` is threaded in, every dropped row emits
    ``semantic_vector_decode_failed:tfidf:<symbol_id>:<exc_class>:<detail>``
    so the substrate-corruption signal surfaces.
    """
    ensure_tfidf_table(conn)
    rows = conn.execute("SELECT symbol_id, terms FROM symbol_tfidf").fetchall()

    result: dict[int, dict[str, float]] = {}
    for row in rows:
        try:
            vec = json.loads(row["terms"])
            result[row["symbol_id"]] = vec
        except (json.JSONDecodeError, TypeError) as exc:
            if warnings_out is not None:
                warnings_out.append(
                    f"semantic_vector_decode_failed:tfidf:{row['symbol_id']}:{type(exc).__name__}:{exc}"
                )
            continue
    return result


def _search_tfidf_stored(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Search using pre-computed TF-IDF vectors (legacy fallback).

    Threads ``warnings_out`` into ``load_tfidf_vectors`` so per-row
    JSON-decode failures surface. Empty-corpus / empty-tokens stays
    SILENT (legitimate cold-start / filter, not substrate failure).
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    query_vec: Counter[str] = Counter(query_tokens)

    vectors = load_tfidf_vectors(conn, warnings_out=warnings_out)
    if not vectors:
        return []

    scores = _score_vector_corpus(query_vec, vectors, cosine_similarity)
    return _symbol_results_preserving_score_order(conn, scores, top_k)
