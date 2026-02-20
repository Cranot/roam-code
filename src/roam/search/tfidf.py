"""Pure-Python TF-IDF engine for semantic symbol search (zero external deps)."""

from __future__ import annotations

import math
import re

# ---------------------------------------------------------------------------
# Stopwords: common English + common code tokens
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    # English
    "a", "an", "the", "is", "it", "in", "on", "of", "to", "and", "or",
    "for", "with", "not", "be", "are", "was", "were", "been", "has", "have",
    "had", "do", "does", "did", "but", "at", "by", "this", "that", "from",
    "as", "if", "else", "then", "than", "so", "no", "all", "any", "each",
    "can", "will", "may", "should", "would", "could",
    # Code keywords
    "self", "return", "import", "from", "def", "class", "function", "var",
    "let", "const", "new", "null", "none", "true", "false", "try", "except",
    "catch", "throw", "raises", "pass", "continue", "break", "yield",
    "async", "await", "static", "public", "private", "protected", "void",
    "int", "str", "bool", "float", "string", "type",
})

# Suffixes to strip (simple stemming)
_SUFFIXES = (
    "tion", "ment", "ness", "able", "ible", "ing", "est", "ly", "ed", "er",
)


def tokenize(text: str) -> list[str]:
    """Split text into tokens: lowercase, split on non-alnum, filter stopwords, stem."""
    if not text:
        return []
    # Split on non-alphanumeric first (preserve case for camelCase detection)
    raw = re.split(r"[^a-zA-Z0-9]+", text)
    tokens = []
    for tok in raw:
        if not tok:
            continue
        # camelCase / PascalCase split (before lowercasing)
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", tok)
        # Also split on transitions like "XMLParser" -> "XML Parser"
        parts = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", parts)
        for part in parts.split():
            part = part.lower()
            if part in _STOPWORDS or len(part) < 2:
                continue
            stemmed = _stem(part)
            if stemmed and stemmed not in _STOPWORDS and len(stemmed) >= 2:
                tokens.append(stemmed)
    return tokens


def _stem(word: str) -> str:
    """Very simple suffix-stripping stemmer."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


# ---------------------------------------------------------------------------
# TF-IDF computation
# ---------------------------------------------------------------------------

def build_corpus(conn) -> dict[int, dict[str, float]]:
    """Build a TF-IDF corpus from the symbols table.

    Returns ``{symbol_id: {term: tfidf_score}}``.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.signature, s.kind "
        "FROM symbols s"
    ).fetchall()

    if not rows:
        return {}

    # Build per-document term frequencies
    doc_tfs: dict[int, dict[str, float]] = {}
    df: dict[str, int] = {}  # document frequency per term

    for row in rows:
        sid = row["id"]
        # Weight: name 3x, qualified_name 2x, signature 1x, kind 1x
        tokens: list[str] = []
        name_tokens = tokenize(row["name"] or "")
        tokens.extend(name_tokens * 3)
        qn_tokens = tokenize(row["qualified_name"] or "")
        tokens.extend(qn_tokens * 2)
        tokens.extend(tokenize(row["signature"] or ""))
        tokens.extend(tokenize(row["kind"] or ""))

        if not tokens:
            continue

        # Compute raw TF
        tf: dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        # Normalize TF by max frequency
        max_freq = max(tf.values()) if tf else 1
        for t in tf:
            tf[t] = tf[t] / max_freq

        doc_tfs[sid] = tf

        # Update document frequency (each term counted once per doc)
        for t in tf:
            df[t] = df.get(t, 0) + 1

    # Compute IDF
    n_docs = len(doc_tfs)
    if n_docs == 0:
        return {}

    idf: dict[str, float] = {}
    for t, count in df.items():
        idf[t] = math.log((n_docs + 1) / (count + 1)) + 1  # smoothed IDF

    # Compute TF-IDF vectors
    result: dict[int, dict[str, float]] = {}
    for sid, tf in doc_tfs.items():
        vec: dict[str, float] = {}
        for t, tf_val in tf.items():
            vec[t] = tf_val * idf.get(t, 1.0)
        result[sid] = vec

    return result


def compute_tfidf_vectors(conn) -> list[dict]:
    """Return list of ``{symbol_id, name, file_path, kind, vector}`` dicts."""
    corpus = build_corpus(conn)
    if not corpus:
        return []

    sym_ids = list(corpus.keys())
    # Batch-fetch symbol metadata
    meta: dict[int, dict] = {}
    batch_size = 400
    for i in range(0, len(sym_ids), batch_size):
        batch = sym_ids[i : i + batch_size]
        ph = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT s.id, s.name, f.path as file_path, s.kind "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({ph})",
            batch,
        ).fetchall()
        for r in rows:
            meta[r["id"]] = {
                "name": r["name"],
                "file_path": r["file_path"],
                "kind": r["kind"],
            }

    results = []
    for sid, vec in corpus.items():
        m = meta.get(sid, {})
        results.append({
            "symbol_id": sid,
            "name": m.get("name", ""),
            "file_path": m.get("file_path", ""),
            "kind": m.get("kind", ""),
            "vector": vec,
        })
    return results


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0

    # Dot product (only over shared keys)
    dot = 0.0
    for key in vec_a:
        if key in vec_b:
            dot += vec_a[key] * vec_b[key]

    if dot == 0.0:
        return 0.0

    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search symbols using TF-IDF cosine similarity.

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Build query vector (simple TF, no IDF needed for short queries)
    query_vec: dict[str, float] = {}
    for t in query_tokens:
        query_vec[t] = query_vec.get(t, 0) + 1

    # Build corpus
    corpus = build_corpus(conn)
    if not corpus:
        return []

    # Score every symbol
    scores: list[tuple[float, int]] = []
    for sid, vec in corpus.items():
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
    ph = ",".join("?" for _ in sym_ids)
    rows = conn.execute(
        f"SELECT s.id, s.name, f.path as file_path, s.kind, s.line_start, s.line_end "
        f"FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE s.id IN ({ph})",
        sym_ids,
    ).fetchall()

    meta = {r["id"]: r for r in rows}

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
