"""Hybrid semantic search: FTS5/BM25 + TF-IDF vector fusion."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from roam.search.framework_packs import search_pack_symbols
from roam.search.tfidf import tokenize, cosine_similarity


# ---------------------------------------------------------------------------
# camelCase preprocessing for FTS5 tokenizer
# ---------------------------------------------------------------------------

def _camel_split(text: str) -> str:
    """Insert spaces at camelCase/PascalCase boundaries for FTS5.

    ``OpenDatabase`` → ``Open Database``
    ``XMLParser``    → ``XML Parser``
    ``file_path``    → ``file_path`` (underscores handled by unicode61)
    """
    if not text:
        return ""
    result = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", result)
    return result


# ---------------------------------------------------------------------------
# FTS5 availability detection
# ---------------------------------------------------------------------------

def fts5_available(conn) -> bool:
    """Check if the symbol_fts virtual table exists and is usable."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def fts5_populated(conn) -> bool:
    """Check if symbol_fts has data."""
    if not fts5_available(conn):
        return False
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbol_fts").fetchone()
        return row is not None and row[0] > 0
    except Exception:
        return False


def tfidf_populated(conn) -> bool:
    """Check if symbol_tfidf has data."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbol_tfidf").fetchone()
        return row is not None and row[0] > 0
    except Exception:
        return False


def onnx_populated(conn) -> bool:
    """Check if ONNX embedding table has data."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbol_embeddings WHERE provider='onnx'"
        ).fetchone()
        return row is not None and row[0] > 0
    except Exception:
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

def build_fts_index(conn, project_root=None):
    """Populate the FTS5 symbol_fts table for BM25-ranked search.

    Pushes tokenization and indexing entirely to SQLite's C engine.
    Also persists TF-IDF vectors for hybrid ranking.
    """
    if not fts5_available(conn):
        build_and_store_tfidf(conn)
        try:
            build_and_store_onnx_embeddings(conn, project_root=project_root)
        except Exception:
            pass
        return

    # Clear and rebuild — FTS5 doesn't support UPDATE well, full rebuild is fast
    conn.execute("DELETE FROM symbol_fts")

    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.signature, s.kind, "
        "f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id"
    ).fetchall()

    if not rows:
        return

    # Insert with camelCase preprocessing for better tokenization
    batch = []
    for row in rows:
        batch.append((
            row["id"],
            _camel_split(row["name"] or ""),
            _camel_split(row["qualified_name"] or ""),
            _camel_split(row["signature"] or ""),
            row["kind"] or "",
            row["file_path"] or "",
        ))
        if len(batch) >= 500:
            conn.executemany(
                "INSERT INTO symbol_fts(rowid, name, qualified_name, "
                "signature, kind, file_path) VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            batch.clear()

    if batch:
        conn.executemany(
            "INSERT INTO symbol_fts(rowid, name, qualified_name, "
            "signature, kind, file_path) VALUES (?, ?, ?, ?, ?, ?)",
            batch,
        )

    # Keep vector signals available even when FTS5 exists (hybrid #54).
    build_and_store_tfidf(conn)
    # Optional dense local embeddings (ONNX) for semantic search (#56).
    try:
        build_and_store_onnx_embeddings(conn, project_root=project_root)
    except Exception:
        # ONNX is optional; TF-IDF path remains authoritative fallback.
        pass


# ---------------------------------------------------------------------------
# FTS5 search (primary path)
# ---------------------------------------------------------------------------

# BM25 column weights: name=10, qualified_name=5, signature=2, kind=1, file_path=3
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"

# Hybrid fusion defaults (backlog #54).
_HYBRID_LEXICAL_WEIGHT = 0.65
_HYBRID_SEMANTIC_WEIGHT = 0.35
_HYBRID_RANK_WEIGHT = 0.40
_HYBRID_RRF_K = 60
_HYBRID_MIN_CANDIDATES = 25
_HYBRID_CANDIDATE_MULTIPLIER = 4

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


def search_fts(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search using FTS5 BM25 ranking (fast, all in C).

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    if not query or not query.strip():
        return []

    # Preprocess query: camelCase split + escape FTS5 special chars
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    try:
        rows = conn.execute(_FTS5_SEARCH_SQL, (fts_query, top_k)).fetchall()
    except Exception:
        # FTS5 query syntax error — fall back to prefix match
        try:
            fts_query = _build_fts_query(query, prefix_only=True)
            if fts_query:
                rows = conn.execute(_FTS5_SEARCH_SQL, (fts_query, top_k)).fetchall()
            else:
                return []
        except Exception:
            return []

    results = []
    for row in rows:
        results.append({
            "score": round(row["score"], 4),
            "symbol_id": row["symbol_id"],
            "name": row["name"],
            "file_path": row["file_path"],
            "kind": row["kind"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
        })
    return results


def _build_fts_query(query: str, prefix_only: bool = False) -> str:
    """Convert a natural language query into an FTS5 MATCH expression.

    - Splits camelCase
    - Removes FTS5 special characters
    - Joins tokens with implicit AND (FTS5 default)
    - Adds prefix matching (``*``) for the last token for typeahead
    """
    preprocessed = _camel_split(query)
    # Remove FTS5 special syntax chars: ^, *, ", (, ), {, }, :
    cleaned = re.sub(r'[^\w\s]', ' ', preprocessed)
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
        return {
            int(r["symbol_id"]): 0.0
            for r in results
            if r.get("symbol_id") is not None
        }
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

    lex_rank = {
        int(r["symbol_id"]): idx + 1
        for idx, r in enumerate(lexical_results)
        if r.get("symbol_id") is not None
    }
    sem_rank = {
        int(r["symbol_id"]): idx + 1
        for idx, r in enumerate(semantic_results)
        if r.get("symbol_id") is not None
    }
    lex_norm = _normalize_scores(lexical_results)
    sem_norm = _normalize_scores(semantic_results)
    lex_meta = {
        int(r["symbol_id"]): r
        for r in lexical_results
        if r.get("symbol_id") is not None
    }
    sem_meta = {
        int(r["symbol_id"]): r
        for r in semantic_results
        if r.get("symbol_id") is not None
    }

    max_rrf = 1.0 / (_HYBRID_RRF_K + 1)
    merged: list[dict] = []
    for sid in sorted(set(lex_rank) | set(sem_rank)):
        lr = lex_rank.get(sid)
        sr = sem_rank.get(sid)
        lex_rrf = (1.0 / (_HYBRID_RRF_K + lr)) if lr else 0.0
        sem_rrf = (1.0 / (_HYBRID_RRF_K + sr)) if sr else 0.0
        rank_score = (
            (_HYBRID_LEXICAL_WEIGHT * lex_rrf)
            + (_HYBRID_SEMANTIC_WEIGHT * sem_rrf)
        ) / max_rrf
        signal_score = (
            (_HYBRID_LEXICAL_WEIGHT * lex_norm.get(sid, 0.0))
            + (_HYBRID_SEMANTIC_WEIGHT * sem_norm.get(sid, 0.0))
        )
        score = (
            (_HYBRID_RANK_WEIGHT * rank_score)
            + ((1.0 - _HYBRID_RANK_WEIGHT) * signal_score)
        )

        meta = lex_meta.get(sid) or sem_meta.get(sid)
        if not meta:
            continue
        merged.append({
            "score": round(score, 4),
            "symbol_id": sid,
            "name": meta["name"],
            "file_path": meta["file_path"],
            "kind": meta["kind"],
            "line_start": meta["line_start"],
            "line_end": meta.get("line_end"),
            "_rank_sort": min(lr or 10_000, sr or 10_000),
        })

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


def search_stored(
    conn,
    query: str,
    top_k: int = 10,
    include_packs: bool = True,
    packs: list[str] | None = None,
    semantic_backend: str = "auto",
    project_root=None,
) -> list[dict]:
    """Search using hybrid BM25+vector fusion with optional framework packs.

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    if not query or not query.strip():
        return []

    candidate_k = max(top_k * _HYBRID_CANDIDATE_MULTIPLIER, _HYBRID_MIN_CANDIDATES)
    backend = (semantic_backend or "auto").strip().lower()
    if backend not in {"auto", "tfidf", "onnx", "hybrid"}:
        backend = "auto"

    lexical_results: list[dict] = []
    semantic_results: list[dict] = []
    onnx_results: list[dict] = []
    tfidf_results: list[dict] = []

    # Fast lexical branch (FTS5/BM25).
    if fts5_populated(conn):
        lexical_results = search_fts(conn, query, top_k=candidate_k)

    # Dense ONNX branch.
    if backend in {"auto", "onnx", "hybrid"} and onnx_populated(conn):
        onnx_results = _search_onnx_stored(
            conn,
            query,
            top_k=candidate_k,
            project_root=project_root,
        )

    # Sparse TF-IDF branch.
    if backend in {"auto", "tfidf", "hybrid"} and tfidf_populated(conn):
        tfidf_results = _search_tfidf_stored(conn, query, top_k=candidate_k)

    semantic_results = _merge_semantic_results(
        onnx_results,
        tfidf_results,
        top_k=candidate_k,
    )

    # Optional pre-indexed framework/library packs (#96).
    if include_packs:
        try:
            pack_results = search_pack_symbols(query, top_k=candidate_k, packs=packs)
        except Exception:
            pack_results = []
        if pack_results:
            semantic_results = sorted(
                semantic_results + pack_results,
                key=lambda r: (
                    -r.get("score", 0.0),
                    r.get("name", ""),
                    r.get("symbol_id", 0),
                ),
            )[:candidate_k]

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

def ensure_tfidf_table(conn):
    """Ensure the symbol_tfidf table exists.

    Delegates to the canonical schema in roam.db.schema so there is a single
    source of truth for the table definition.
    """
    from roam.db.schema import SCHEMA_SQL
    conn.executescript(SCHEMA_SQL)


def build_and_store_tfidf(conn):
    """Compute TF-IDF vectors for all symbols and store in symbol_tfidf.

    Called during ``roam index`` as part of hybrid search index build.
    """
    from roam.search.tfidf import build_corpus

    ensure_tfidf_table(conn)

    corpus = build_corpus(conn)
    if not corpus:
        return

    conn.execute("DELETE FROM symbol_tfidf")

    batch = []
    for sid, vec in corpus.items():
        terms_json = json.dumps(vec)
        batch.append((sid, terms_json))
        if len(batch) >= 500:
            conn.executemany(
                "INSERT OR REPLACE INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
                batch,
            )
            batch.clear()

    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO symbol_tfidf (symbol_id, terms) VALUES (?, ?)",
            batch,
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


def build_and_store_onnx_embeddings(conn, project_root=None) -> dict[str, Any]:
    """Compute dense ONNX vectors for symbols and store in symbol_embeddings."""
    settings = _load_semantic_settings(project_root=project_root)
    ready, reason, settings = _onnx_ready(project_root=project_root, settings=settings)
    if not ready:
        return {"enabled": False, "reason": reason}

    embedder = _get_onnx_embedder(project_root=project_root, settings=settings)
    if embedder is None:
        return {"enabled": False, "reason": "embedder-unavailable"}

    rows = conn.execute(
        "SELECT id, name, qualified_name, signature, kind, docstring FROM symbols"
    ).fetchall()
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
    batch = []
    for idx in range(count):
        sid = rows[idx]["id"]
        vec = vectors[idx]
        batch.append((sid, json.dumps(vec), dims, "onnx", model_id))
        if len(batch) >= 250:
            conn.executemany(
                "INSERT OR REPLACE INTO symbol_embeddings "
                "(symbol_id, vector, dims, provider, model_id) "
                "VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            batch.clear()

    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO symbol_embeddings "
            "(symbol_id, vector, dims, provider, model_id) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )

    return {
        "enabled": True,
        "stored": count,
        "dims": dims,
        "model_id": model_id,
    }


def load_onnx_vectors(conn) -> dict[int, list[float]]:
    """Load stored ONNX vectors from DB."""
    rows = conn.execute(
        "SELECT symbol_id, vector FROM symbol_embeddings WHERE provider='onnx'"
    ).fetchall()
    result: dict[int, list[float]] = {}
    for row in rows:
        try:
            vec = json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError):
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


def _search_onnx_stored(
    conn,
    query: str,
    top_k: int = 10,
    project_root=None,
) -> list[dict]:
    """Search using precomputed ONNX vectors + query embedding."""
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

    vectors = load_onnx_vectors(conn)
    if not vectors:
        return []

    scores: list[tuple[float, int]] = []
    for sid, vec in vectors.items():
        sim = _cosine_dense(query_vec, vec)
        if sim > 0:
            scores.append((sim, sid))
    if not scores:
        return []

    scores.sort(key=lambda x: -x[0])
    top = scores[:top_k]

    sym_ids = [sid for _, sid in top]
    batch_size = 500
    meta: dict[int, dict] = {}
    for i in range(0, len(sym_ids), batch_size):
        batch = sym_ids[i : i + batch_size]
        ph = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT s.id, s.name, f.path as file_path, s.kind, "
            f"s.line_start, s.line_end "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph})",
            batch,
        ).fetchall()
        for row in rows:
            meta[row["id"]] = row

    results = []
    for score, sid in top:
        row = meta.get(sid)
        if not row:
            continue
        results.append({
            "score": round(score, 4),
            "symbol_id": sid,
            "name": row["name"],
            "file_path": row["file_path"],
            "kind": row["kind"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
        })
    return results


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


def load_tfidf_vectors(conn) -> dict[int, dict[str, float]]:
    """Load stored TF-IDF vectors from DB."""
    ensure_tfidf_table(conn)
    rows = conn.execute(
        "SELECT symbol_id, terms FROM symbol_tfidf"
    ).fetchall()

    result: dict[int, dict[str, float]] = {}
    for row in rows:
        try:
            vec = json.loads(row["terms"])
            result[row["symbol_id"]] = vec
        except (json.JSONDecodeError, TypeError):
            continue
    return result


def _search_tfidf_stored(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search using pre-computed TF-IDF vectors (legacy fallback)."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    query_vec: dict[str, float] = {}
    for t in query_tokens:
        query_vec[t] = query_vec.get(t, 0) + 1

    vectors = load_tfidf_vectors(conn)
    if not vectors:
        return []

    scores: list[tuple[float, int]] = []
    for sid, vec in vectors.items():
        sim = cosine_similarity(query_vec, vec)
        if sim > 0:
            scores.append((sim, sid))

    if not scores:
        return []

    scores.sort(key=lambda x: -x[0])
    top = scores[:top_k]

    sym_ids = [sid for _, sid in top]
    batch_size = 500
    meta: dict[int, dict] = {}
    for i in range(0, len(sym_ids), batch_size):
        batch = sym_ids[i : i + batch_size]
        ph = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT s.id, s.name, f.path as file_path, s.kind, "
            f"s.line_start, s.line_end "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph})",
            batch,
        ).fetchall()
        for r in rows:
            meta[r["id"]] = r

    results = []
    for score, sid in top:
        m = meta.get(sid)
        if not m:
            continue
        results.append({
            "score": round(score, 4),
            "symbol_id": sid,
            "name": m["name"],
            "file_path": m["file_path"],
            "kind": m["kind"],
            "line_start": m["line_start"],
            "line_end": m["line_end"],
        })

    return results
