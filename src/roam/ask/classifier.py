"""Intent classifier for ``roam ask``.

A deterministic TF-IDF-style scorer over the recipe registry plus a
small verb-rules table for unambiguous keywords. No LLM, no network —
the recipes are themselves the corpus.

The classifier returns a ranked list ``[(recipe, score)]`` where
``score`` is in [0, 1]. The CLI displays the top match and offers the
top-3 when confidence is below a threshold.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from roam.ask.recipes import RECIPES, Recipe

_TOKENISER = re.compile(r"[A-Za-z][A-Za-z0-9]+|[a-z]+_[a-z0-9_]+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "i",
        "we",
        "you",
        "they",
        "it",
        "this",
        "that",
        "these",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "have",
        "has",
        "had",
        "should",
        "what",
        "which",
        "who",
        "how",
    }
)


def _tokenise(text: str) -> list[str]:
    return [t.lower() for t in _TOKENISER.findall(text) if t.lower() not in _STOPWORDS and len(t) >= 3]


def _recipe_corpus(r: Recipe) -> list[str]:
    """The token bag for a recipe — intent + examples (heavily weighted)."""
    bag: list[str] = []
    bag.extend(_tokenise(r.intent))
    for ex in r.examples:
        bag.extend(_tokenise(ex))
    # Examples are user phrasings — each gets a 2× boost in the bag.
    bag.extend(_tokenise(" ".join(r.examples)))
    return bag


def classify(query: str, recipes: list[Recipe] | None = None) -> list[tuple[Recipe, float]]:
    """Score every recipe against *query* and return them sorted high→low.

    Score in [0, 1] is a blend of TF-IDF cosine similarity and a
    keyword bonus. Empty query → empty list.
    """
    if not query or not query.strip():
        return []
    pool = recipes if recipes is not None else RECIPES

    q_tokens = _tokenise(query)
    if not q_tokens:
        # Even with no content tokens, fall back to keyword scan so a
        # bare verb like "delete" still maps somewhere.
        return _keyword_only_score(query.lower(), pool)

    q_vec = Counter(q_tokens)

    # Build the document-frequency table for IDF.
    doc_token_sets = [(r, set(_recipe_corpus(r))) for r in pool]
    df: Counter = Counter()
    for _, tokens in doc_token_sets:
        for tok in tokens:
            df[tok] += 1
    n_docs = max(len(pool), 1)

    def _idf(tok: str) -> float:
        return math.log((n_docs + 1) / (df.get(tok, 0) + 1)) + 1

    q_norm = math.sqrt(sum((v * _idf(t)) ** 2 for t, v in q_vec.items()))
    scored: list[tuple[Recipe, float]] = []
    for r in pool:
        bag = Counter(_recipe_corpus(r))
        if not bag:
            continue
        d_norm = math.sqrt(sum((v * _idf(t)) ** 2 for t, v in bag.items()))
        if d_norm == 0 or q_norm == 0:
            cos = 0.0
        else:
            dot = sum(q_vec[t] * bag[t] * (_idf(t) ** 2) for t in q_vec if t in bag)
            cos = dot / (q_norm * d_norm)

        # Keyword bonus — strong-shape signal beats vector similarity
        kw_bonus = 0.0
        lower_query = query.lower()
        for kw in r.keywords:
            if kw in lower_query:
                kw_bonus += 0.25
        kw_bonus = min(kw_bonus, 0.5)

        score = min(1.0, cos + kw_bonus)
        scored.append((r, score))

    scored.sort(key=lambda x: -x[1])
    return scored


def _keyword_only_score(lower_query: str, pool: list[Recipe]) -> list[tuple[Recipe, float]]:
    out = []
    for r in pool:
        bonus = 0.0
        for kw in r.keywords:
            if kw in lower_query:
                bonus += 0.25
        out.append((r, min(1.0, bonus)))
    out.sort(key=lambda x: -x[1])
    return out
