"""Pure-Python TF-IDF engine for semantic symbol search (zero external deps)."""

from __future__ import annotations

import math
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns for tokenization
# ---------------------------------------------------------------------------

_RE_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]+")
_RE_CAMEL_SPLIT = re.compile(r"([a-z])([A-Z])")
_RE_UPPER_SPLIT = re.compile(r"([A-Z]+)([A-Z][a-z])")

# ---------------------------------------------------------------------------
# Stopwords: common English + common code tokens
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {
        # English
        "a",
        "an",
        "the",
        "is",
        "it",
        "in",
        "on",
        "of",
        "to",
        "and",
        "or",
        "for",
        "with",
        "not",
        "be",
        "are",
        "was",
        "were",
        "been",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "but",
        "at",
        "by",
        "this",
        "that",
        "from",
        "as",
        "if",
        "else",
        "then",
        "than",
        "so",
        "no",
        "all",
        "any",
        "each",
        "can",
        "will",
        "may",
        "should",
        "would",
        "could",
        # Code keywords
        "self",
        "return",
        "import",
        "def",
        "class",
        "function",
        "var",
        "let",
        "const",
        "new",
        "null",
        "none",
        "true",
        "false",
        "try",
        "except",
        "catch",
        "throw",
        "raises",
        "pass",
        "continue",
        "break",
        "yield",
        "async",
        "await",
        "static",
        "public",
        "private",
        "protected",
        "void",
        "int",
        "str",
        "bool",
        "float",
        "string",
        "type",
    }
)

# Suffixes to strip (simple stemming)
_SUFFIXES = (
    "tion",
    "ment",
    "ness",
    "able",
    "ible",
    "ing",
    "est",
    "ly",
    "ed",
    "er",
)


def tokenize(text: str | None) -> list[str]:
    """Split text into tokens: lowercase, split on non-alnum, filter stopwords, stem.

    W1029: ``text`` accepts ``None`` so callers can pass raw SQL row values
    (e.g. ``row["signature"]``) without the cargo-cult ``or ""`` defensive
    wrapper. Returns an empty list on ``None``/empty.
    """
    if not text:
        return []
    # Split on non-alphanumeric first (preserve case for camelCase detection)
    raw = _RE_NON_ALNUM.split(text)
    tokens = []
    split_camel_case = _RE_CAMEL_SPLIT.sub
    split_acronym_boundary = _RE_UPPER_SPLIT.sub
    boundary_replacement = r"\1 \2"
    for tok in raw:
        if not tok:
            continue
        # camelCase / PascalCase split (before lowercasing)
        parts = split_camel_case(boundary_replacement, tok)
        # Also split on transitions like "XMLParser" -> "XML Parser"
        parts = split_acronym_boundary(boundary_replacement, parts)
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
    rows = conn.execute("SELECT s.id, s.name, s.qualified_name, s.signature, s.kind FROM symbols s").fetchall()

    if not rows:
        return {}

    # Build per-document term frequencies
    doc_tfs: dict[int, dict[str, float]] = {}
    df: Counter[str] = Counter()  # document frequency per term

    for row in rows:
        sid = row["id"]
        # Weight: name 3x, qualified_name 2x, signature 1x, kind 1x
        tokens: list[str] = []
        name_tokens = tokenize(row["name"])
        tokens.extend(name_tokens * 3)
        qn_tokens = tokenize(row["qualified_name"])
        tokens.extend(qn_tokens * 2)
        tokens.extend(tokenize(row["signature"]))
        tokens.extend(tokenize(row["kind"]))

        if not tokens:
            continue

        # Compute raw TF
        tf_raw = Counter(tokens)

        # Normalize TF by max frequency
        max_freq = max(tf_raw.values()) if tf_raw else 1
        tf: dict[str, float] = {t: c / max_freq for t, c in tf_raw.items()}

        doc_tfs[sid] = tf

        # Update document frequency (each term counted once per doc)
        df.update(tf.keys())

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


def tfidf_search(conn, query: str, top_k: int = 10) -> list[dict]:
    """Search symbols using TF-IDF cosine similarity.

    Returns top-k results: ``[{score, symbol_id, name, file_path, kind, line_start}]``.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Build query vector (simple TF, no IDF needed for short queries)
    query_vec: Counter[str] = Counter(query_tokens)

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
    from roam.db.connection import batched_in

    sym_ids = [sid for _, sid in top]
    rows = batched_in(
        conn,
        "SELECT s.id, s.name, f.path as file_path, s.kind, s.line_start, s.line_end "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id IN ({ph})",
        sym_ids,
    )

    meta = {r["id"]: r for r in rows}

    results = []
    for score, sid in top:
        m = meta.get(sid)
        if not m:
            continue
        results.append(
            {
                "score": round(score, 4),
                "symbol_id": sid,
                "name": m["name"],
                "file_path": m["file_path"],
                "kind": m["kind"],
                "line_start": m["line_start"],
                "line_end": m["line_end"],
            }
        )

    return results
