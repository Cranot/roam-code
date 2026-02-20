"""TF-IDF vector storage and retrieval for pre-computed semantic search."""

from __future__ import annotations

import json
import math

from roam.search.tfidf import tokenize, cosine_similarity


# ---------------------------------------------------------------------------
# Schema
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


# ---------------------------------------------------------------------------
# Build & store
# ---------------------------------------------------------------------------

def build_and_store_tfidf(conn):
    """Compute TF-IDF vectors for all symbols and store in symbol_tfidf.

    Called during ``roam index`` to pre-compute vectors for fast search.
    """
    from roam.search.tfidf import build_corpus

    ensure_tfidf_table(conn)

    corpus = build_corpus(conn)
    if not corpus:
        return

    # Clear old data
    conn.execute("DELETE FROM symbol_tfidf")

    # Insert in batches
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


# ---------------------------------------------------------------------------
# Load stored vectors
# ---------------------------------------------------------------------------

def load_tfidf_vectors(conn) -> dict[int, dict[str, float]]:
    """Load stored vectors from DB.

    Returns ``{symbol_id: {term: score}}``.
    """
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


# ---------------------------------------------------------------------------
# Search using stored vectors
# ---------------------------------------------------------------------------

def search_stored(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search using pre-computed stored vectors (fast).

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Build query vector
    query_vec: dict[str, float] = {}
    for t in query_tokens:
        query_vec[t] = query_vec.get(t, 0) + 1

    # Load stored vectors
    vectors = load_tfidf_vectors(conn)
    if not vectors:
        return []

    # Score every symbol
    scores: list[tuple[float, int]] = []
    for sid, vec in vectors.items():
        sim = cosine_similarity(query_vec, vec)
        if sim > 0:
            scores.append((sim, sid))

    if not scores:
        return []

    # Sort by score descending
    scores.sort(key=lambda x: -x[0])
    top = scores[:top_k]

    # Fetch metadata for top results
    sym_ids = [sid for _, sid in top]
    batch_size = 400
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
