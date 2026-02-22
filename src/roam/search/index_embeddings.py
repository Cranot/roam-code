"""Semantic search: FTS5/BM25 (primary) with TF-IDF cosine fallback."""

from __future__ import annotations

import json
import re

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


# ---------------------------------------------------------------------------
# FTS5 index build (called during `roam index`)
# ---------------------------------------------------------------------------

def build_fts_index(conn):
    """Populate the FTS5 symbol_fts table for BM25-ranked search.

    Pushes tokenization and indexing entirely to SQLite's C engine.
    Falls back to building TF-IDF vectors if FTS5 is unavailable.
    """
    if not fts5_available(conn):
        build_and_store_tfidf(conn)
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


# ---------------------------------------------------------------------------
# FTS5 search (primary path)
# ---------------------------------------------------------------------------

# BM25 column weights: name=10, qualified_name=5, signature=2, kind=1, file_path=3
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"

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
# Unified search: FTS5 → TF-IDF fallback
# ---------------------------------------------------------------------------

def search_stored(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search using the best available method: FTS5 BM25 or TF-IDF cosine.

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    # Try FTS5 first (1000x faster)
    if fts5_populated(conn):
        results = search_fts(conn, query, top_k)
        if results:
            return results

    # Fall back to stored TF-IDF vectors
    return _search_tfidf_stored(conn, query, top_k)


# ---------------------------------------------------------------------------
# Legacy TF-IDF paths (fallback for pre-v11 databases)
# ---------------------------------------------------------------------------

TFIDF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS symbol_tfidf (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id),
    terms TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def ensure_tfidf_table(conn):
    """Create the symbol_tfidf table if it does not exist."""
    conn.execute(TFIDF_TABLE_SQL)


def build_and_store_tfidf(conn):
    """Compute TF-IDF vectors for all symbols and store in symbol_tfidf.

    Called during ``roam index`` as fallback when FTS5 is unavailable.
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
