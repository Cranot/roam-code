"""A.0.3 — seed inference for `roam retrieve`.

When a user types a free-form task (or an MCP agent calls
``retrieve_context(task=...)`` without ``--seed-files``), we still want
the structural reranker to bias toward query-relevant symbols. This
module extracts symbol-shaped tokens from the task text and resolves
them to symbol ids via FTS5, returning a weighted seed map suitable for
``personalized_pagerank``.

Token classes captured (in priority order):

1. **File paths** — ``api/handler.py``, ``src/foo.ts`` (anything matching
   ``<word>.<ext>``).
2. **Dotted attribute paths** — ``user.session``, ``module.func``.
3. **snake_case multi-word identifiers** — ``user_session``,
   ``handle_request`` (must contain at least one underscore).
4. **PascalCase / camelCase identifiers** — ``UserSession``,
   ``getUser`` (length ≥ 3).

Each token is searched via FTS5 against the existing ``symbol_fts``
virtual table; per-symbol scores are accumulated across tokens. The
top ``max_seeds`` symbols (by accumulated BM25 weight) are returned.

A LIKE-based fallback is used when FTS5 is not available (rare — only
on builds without FTS5 support compiled into SQLite).
"""

from __future__ import annotations

import re
import sqlite3

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z]{1,8})\b")
_DOTTED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]+)+)\b")
_SNAKE_RE = re.compile(r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b")
# UPPER_SNAKE / CONSTANT_NAME — added 2026-05-01 dogfood R13: queries
# like ``PERSONALIZED_PAGERANK`` previously extracted zero tokens
# because every regex required at least one lowercase character.
# Captured tokens are lowercased before downstream FTS so the
# ``UPPER_SNAKE`` query resolves to the same symbols as ``upper_snake``.
_UPPER_SNAKE_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\b")
_PASCAL_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,})\b")
# camelCase: lowercase start, ≥1 uppercase boundary (e.g. getUserById)
_CAMEL_RE = re.compile(r"\b([a-z][a-z0-9]+(?:[A-Z][A-Za-z0-9]+)+)\b")
# Lowercase nouns ≥5 chars — DOG.7 fallback for natural-language queries
# like "where does critique decide finding severity" that contain zero
# identifier-shaped tokens. Each domain word still has to clear the
# extended stopword filter below before it becomes a seed.
_LOWERCASE_NOUN_RE = re.compile(r"\b([a-z][a-z0-9]{4,})\b")

# Phase-1 dogfood 2026-05-04: 4-letter programming-domain nouns the
# fallback regex misses. "where is dead code detection" had only
# ["detection"] as tokens because "dead" / "code" are 4 chars and
# below the noun-fallback floor. Adding a curated allow-list keeps
# precision high (no broad lowering of the floor) while restoring
# recall on these high-signal short words.
_FOUR_CHAR_DOMAIN_NOUNS = frozenset(
    {
        "dead",
        "code",
        "file",
        "role",
        "path",
        "node",
        "edge",
        "view",
        "task",
        "flow",
        "tree",
        "loop",
        "hash",
        "port",
        "page",
        "head",
        "tail",
        "item",
        "list",
        "type",
        "kind",
        "rank",
        "rule",
        "lint",
        "fail",
        "pass",
        "skip",
        "size",
        "cost",
        "cycle",  # 5-char but for completeness
        "auth",
        "user",
        "name",
        "json",
        "html",
        "yaml",
        "test",
        "spec",
        "diff",
        "lock",
        "pull",
        "push",
        "load",
        "save",
        "stat",
        "perf",
        "race",
        "leak",
        "null",
        "void",
        "main",
        "init",
        "exit",
        "kill",
        "stop",
        "wait",
        "sync",
    }
)
_FOUR_CHAR_NOUN_RE = re.compile(r"\b([a-z]{4})\b")

# 12.13 — programming-context abbreviation expansion. When a query
# mentions a common code abbreviation, *also* emit the full word so
# FTS5 can hit symbols spelled either way ("db connect" should
# match both ``db_connect`` and ``database_connect`` — which way
# the codebase spells it shouldn't matter to the agent). Each entry
# is bidirectional: the abbr→full direction is the common case; the
# full→abbr direction handles repos that prefer the short form.
# Curated to programming-relevant abbreviations only (no generic
# English shortenings like "vs" → "versus").
_ABBREVIATION_EXPANSIONS: dict[str, str] = {
    "db": "database",
    "ctx": "context",
    "conf": "config",
    "args": "arguments",
    "kwargs": "keyword",
    "err": "error",
    "ret": "return",
    "num": "number",
    "val": "value",
    "prop": "property",
    "attr": "attribute",
    "impl": "implementation",
    "intf": "interface",
    "mod": "module",
    "fn": "function",
    "meth": "method",
    "cls": "class",
    "src": "source",
    "repo": "repository",
    "env": "environment",
    "msg": "message",
    "req": "request",
    "res": "response",
    "resp": "response",
    "auth": "authentication",
    "perm": "permission",
    "perms": "permissions",
    "stmt": "statement",
    "expr": "expression",
    "elem": "element",
    "obj": "object",
    "cfg": "config",
    "init": "initialize",
    "exec": "execute",
    "calc": "calculate",
    "diff": "difference",
}
# Reverse map for the full→abbr direction.
_ABBREVIATION_CONTRACTIONS: dict[str, str] = {v: k for k, v in _ABBREVIATION_EXPANSIONS.items() if k not in {"init"}}

# Short and very common words we never want as seeds. Keeps the seed list
# focused on identifiers; the reranker adds non-seed structural signal.
_STOPWORDS = frozenset(
    {
        "the",
        "is",
        "a",
        "of",
        "to",
        "in",
        "and",
        "or",
        "on",
        "for",
        "with",
        "by",
        "from",
        "into",
        "out",
        "as",
        "at",
        "be",
        "was",
        "were",
        "are",
        "this",
        "that",
        "these",
        "those",
        "what",
        "where",
        "when",
        "why",
        "how",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "will",
        "may",
        "might",
        "shall",
        "must",
        "have",
        "has",
        "had",
        "get",
        "set",
        "add",
        "remove",
        "delete",
        "use",
        "make",
        "create",
        "find",
        "try",
        "any",
        "all",
        "some",
        "none",
        "not",
        "so",
        "if",
        "yes",
        "no",
        "ok",
        "true",
        "false",
    }
)

# Extra natural-language stopwords used only by the lowercase-noun
# fallback. These are programming-context filler words that almost never
# resolve to interesting symbols.
_NL_EXTRA_STOPWORDS = frozenset(
    {
        # interrogatives / connectives
        "where",
        "when",
        "while",
        "after",
        "before",
        "during",
        "until",
        "would",
        "should",
        "could",
        "might",
        "shall",
        "ought",
        "their",
        "there",
        "these",
        "those",
        "which",
        "whose",
        "about",
        "above",
        "below",
        "between",
        "through",
        "without",
        "either",
        "neither",
        # generic verbs that are never identifier names
        "decide",
        "decides",
        "check",
        "checks",
        "seems",
        "looks",
        "happens",
        "running",
        "trying",
        "matters",
        "exists",
        # generic nouns we do NOT want as seeds
        "thing",
        "things",
        "stuff",
        "place",
        "places",
        "people",
        "person",
        # very common english fillers ≥5 chars
        "really",
        "still",
        "always",
        "never",
        "often",
        "actually",
        "though",
        "until",
        "while",
    }
)

# Two- and three-letter all-caps tokens we treat as English/initialisms,
# not seeds: API, URL, ID, OK, etc. They produce too much FTS5 noise.
_INITIALISM_RE = re.compile(r"^[A-Z]{2,3}$")


def extract_tokens(query: str) -> list[str]:
    """Return the unique symbol-shaped tokens found in *query*.

    Order is stable but unspecified — callers should not rely on it.
    Stop-words and short initialisms are filtered out.
    """
    if not query:
        return []

    found: dict[str, None] = {}  # use dict for insertion-order uniqueness

    def _add(tok: str) -> None:
        tok = tok.strip()
        if len(tok) < 3:
            return
        if tok.lower() in _STOPWORDS:
            return
        if _INITIALISM_RE.match(tok):
            return
        found[tok] = None

    # File paths first (longest, most specific) so they aren't shadowed.
    for match in _FILE_RE.findall(query):
        _add(match)
    for match in _DOTTED_RE.findall(query):
        _add(match)
    for match in _SNAKE_RE.findall(query):
        _add(match)

    # Phase-1 dogfood 2026-05-04: programming-domain shorthand that
    # falls outside the identifier-shape regexes. ``n+1``, ``n-tier``,
    # ``2fa``, ``i18n``, ``l10n`` etc. are real concepts a user types
    # in a query but the standard tokenizer drops them because they
    # contain ``+``, ``-``, or are too short. Without this, "find n+1
    # query detection" returned only ["query", "detection"] and missed
    # the actual ``cmd_n1.py`` file (the implementation). Each match
    # adds the both raw and a path-shaped form so FTS5 can hit
    # ``cmd_n1.py``-style filenames.
    _DOMAIN_SHORTHANDS = {
        "n+1": "n1",
        "n-1": "n1",
        "2fa": "2fa",
        "i18n": "i18n",
        "l10n": "l10n",
        "a11y": "a11y",
    }
    lowered = query.lower()
    for src_tok, indexed_tok in _DOMAIN_SHORTHANDS.items():
        if src_tok in lowered:
            # The dict-based ``_add`` filters tokens shorter than 3
            # chars; bypass that for these intentional shorthands.
            found[indexed_tok] = None
    # UPPER_SNAKE constants — lowercase before adding so they resolve
    # to the same FTS terms as their snake_case usage.
    for match in _UPPER_SNAKE_RE.findall(query):
        _add(match.lower())
    # Mixed-case snake (``Personalized_Pagerank``, ``foo_BarBaz``) —
    # neither SNAKE nor UPPER_SNAKE catches these, and ``\b`` treats
    # ``_`` as a word character so PASCAL doesn't fire on the
    # individual halves. Lowercase + re-snake catches them. Ordered
    # before CAMEL/PASCAL so the snake form (e.g. ``personalized_pagerank``)
    # is registered first.
    if "_" in query:
        for match in _SNAKE_RE.findall(query.lower()):
            _add(match)
    for match in _CAMEL_RE.findall(query):
        _add(match)
    for match in _PASCAL_RE.findall(query):
        _add(match)

    # DOG.7 / R.1 — natural-language supplement. Always mine lowercase
    # domain nouns as additional seeds. The original DOG.7 ran this only
    # as an all-or-nothing fallback, which discarded informative domain
    # words like "language" and "extractor" whenever the query also
    # happened to contain a PascalCase token. The 30-task self-bench
    # showed that "where is the Ruby Tier 1 language extractor
    # implemented" extracted only [Ruby, Tier] — and the Tier tail
    # buried ruby_lang.py under test_dead_aging.py's TestDecayTier rows.
    # Now we always include lowercase nouns; they ride alongside the
    # Pascal/snake/dotted tokens and lift recall by adding domain
    # signal that BM25 can score against.
    for match in _LOWERCASE_NOUN_RE.findall(query):
        tok = match.strip()
        if len(tok) < 5:
            continue
        lower = tok.lower()
        if lower in _STOPWORDS or lower in _NL_EXTRA_STOPWORDS:
            continue
        found[tok] = None

    # 4-letter domain-noun pass — only allow-listed words that
    # carry programming meaning. Skipped if already present.
    for match in _FOUR_CHAR_NOUN_RE.findall(query.lower()):
        if match in _FOUR_CHAR_DOMAIN_NOUNS and match not in found:
            found[match] = None

    # 12.13 — abbreviation expansion (narrow). Two passes only when
    # the query is short enough (≤4 raw words) that adding abbrevs
    # is a net signal gain rather than noise. Long queries already
    # carry enough seed tokens; adding abbrevs to them introduced
    # noise tokens that hurt recall@10 in the bench. Short queries
    # are exactly where the user typed shorthand (``db connect``,
    # ``ctx propagation``) and benefit from expansion.
    word_count = len(re.findall(r"\b\w+\b", query))
    if word_count <= 4:
        extra_abbrev: list[str] = []
        for tok in list(found):
            lower = tok.lower()
            if lower in _ABBREVIATION_EXPANSIONS and _ABBREVIATION_EXPANSIONS[lower] not in found:
                extra_abbrev.append(_ABBREVIATION_EXPANSIONS[lower])
            if lower in _ABBREVIATION_CONTRACTIONS and _ABBREVIATION_CONTRACTIONS[lower] not in found:
                extra_abbrev.append(_ABBREVIATION_CONTRACTIONS[lower])
        # Direct scan of raw query for abbreviations that were below the
        # length floor of the regex passes (``db``, ``ctx``, ``fn``, …).
        raw_words = re.findall(r"\b([a-z]+)\b", query.lower())
        for word in raw_words:
            if word in _ABBREVIATION_EXPANSIONS:
                if word not in found:
                    extra_abbrev.append(word)
                full = _ABBREVIATION_EXPANSIONS[word]
                if full not in found and full not in extra_abbrev:
                    extra_abbrev.append(full)
        for tok in extra_abbrev:
            found[tok] = None

    return list(found)


# ---------------------------------------------------------------------------
# FTS5 / LIKE matching
# ---------------------------------------------------------------------------

# Same column weights as `search/index_embeddings.py` so behaviour matches
# the rest of the search stack: name=10, qualified_name=5, signature=2,
# kind=1, file_path=3.
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"


def _has_symbol_fts(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _camel_split(text: str) -> str:
    """Mirror of :func:`roam.search.index_embeddings._camel_split`.

    The FTS5 indexer expands ``UserSession`` to ``User Session`` at insert
    time (see ``search/index_embeddings.build_fts_index``). Query tokens
    must be split the same way or MATCH returns zero rows.
    """
    if not text:
        return ""
    out = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    out = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", out)
    return out


def _fts5_query_for(token: str) -> str:
    """Build a defensive FTS5 query for *token*.

    Splits at camelCase / underscore / dot / slash boundaries (matching
    the indexer's tokenisation) and OR-joins prefix matches so that
    ``UserSession`` finds rows storing ``User Session`` and
    ``handle_login`` finds rows whose ``name`` is exactly ``handle_login``.
    """
    safe = token.replace('"', "").replace("'", "")
    if not safe:
        return ""
    expanded = _camel_split(safe).replace("_", " ").replace(".", " ").replace("/", " ")
    parts = [p for p in expanded.split() if len(p) >= 2]
    if not parts:
        return ""
    return " OR ".join(f'"{p}"*' for p in parts)


def infer_seeds(
    conn: sqlite3.Connection,
    query: str,
    max_seeds: int = 10,
) -> dict[int, float]:
    """Return ``{symbol_id: weight}`` seeds inferred from a free-form query.

    Empty / token-less queries return ``{}``. The caller (typically the
    retrieve pipeline) feeds the result into :func:`personalized_pagerank`.

    Weights are accumulated BM25 scores; downstream consumers that need
    them in [0, 1] should normalise themselves. ``personalized_pagerank``
    already normalises so most callers don't have to.
    """
    if not query or not query.strip() or max_seeds <= 0:
        return {}

    tokens = extract_tokens(query)
    if not tokens:
        return {}

    accumulated: dict[int, float] = {}

    if _has_symbol_fts(conn):
        candidate_limit = max(max_seeds * 3, 30)
        for token in tokens:
            fts_query = _fts5_query_for(token)
            if not fts_query:
                continue
            try:
                rows = conn.execute(
                    f"SELECT sf.rowid, -bm25(symbol_fts, {_BM25_WEIGHTS}) AS score "
                    f"FROM symbol_fts sf "
                    f"WHERE symbol_fts MATCH ? "
                    f"ORDER BY bm25(symbol_fts, {_BM25_WEIGHTS}) "
                    f"LIMIT ?",
                    (fts_query, candidate_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # Malformed FTS5 token; skip rather than break the pipeline.
                continue
            for row in rows:
                sym_id = int(row[0])
                weight = float(row[1])
                if weight <= 0:
                    continue
                accumulated[sym_id] = accumulated.get(sym_id, 0.0) + weight
    else:
        accumulated = _like_fallback(conn, tokens, max_seeds)

    if not accumulated:
        return {}

    top = sorted(accumulated.items(), key=lambda kv: -kv[1])[:max_seeds]
    return dict(top)


def _like_fallback(
    conn: sqlite3.Connection,
    tokens: list[str],
    max_seeds: int,
) -> dict[int, float]:
    """Used when FTS5 is unavailable. Slower but functional."""
    accumulated: dict[int, float] = {}
    candidate_limit = max(max_seeds * 3, 30)
    for token in tokens:
        like = f"%{token}%"
        try:
            rows = conn.execute(
                "SELECT id FROM symbols WHERE name LIKE ? OR qualified_name LIKE ? LIMIT ?",
                (like, like, candidate_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            sym_id = int(row[0])
            accumulated[sym_id] = accumulated.get(sym_id, 0.0) + 1.0
    return accumulated
