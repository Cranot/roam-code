"""A.1 — `roam retrieve` pipeline orchestrator.

End-to-end flow:

1. **Seeds** — caller-supplied ``--seed-files`` resolved to symbol ids,
   or :func:`infer_seeds` over the free-form task text.
2. **First-stage** — FTS5 BM25 over ``symbol_fts`` using the same token
   extraction the seed inference uses (so query and reranker agree on
   what "matters").
3. **Structural rerank** — :func:`structural_score` blends PageRank +
   clone-canonical signal + lexical baseline.
4. **Budget cap** — sort by score, take the top until the token budget
   is reached or *k* is hit (whichever comes first).
5. **Result envelope** — list of candidates with file/line/score/
   justifications + meta (seeds_used, budget, weights).

The pipeline is intentionally pure (no I/O, no Click) so it is easy to
test and reuse from the MCP tool wrapper.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from roam.config import get_retrieve_config, get_retrieve_weights
from roam.retrieve.rerank import structural_score
from roam.retrieve.seeds import _fts5_query_for, extract_tokens, infer_seeds

# Default fallback when config is unavailable (e.g. called from a unit
# test that doesn't go through `roam init`). Source of truth is
# `[retrieve] tokens_per_line` in `.roam/config.toml`.
DEFAULT_TOKENS_PER_LINE = 4
DEFAULT_FIRST_STAGE_TOKEN_CAP = 8


def run_retrieve(
    conn: sqlite3.Connection,
    task: str,
    *,
    budget: int = 4000,
    k: int = 20,
    rerank: str = "fast",
    seed_files: list[str] | None = None,
    config_root: Path | None = None,
    first_stage_limit: int = 200,
    weights: dict[str, float] | None = None,
) -> dict:
    """Execute the full retrieve pipeline.

    Returns a dict with keys: ``task``, ``seeds``, ``candidates``,
    ``total_candidates``, ``budget``, ``budget_used``, ``k``, ``rerank``,
    ``weights``.

    Pass ``weights={"alpha": ..., "beta": ..., ...}`` to override the
    config-driven weights (used by the ``roam eval-retrieve --sweep``
    harness to rotate vectors deterministically without rewriting
    config files).
    """
    cfg = get_retrieve_config(config_root)
    config_weights = get_retrieve_weights(config_root)
    if weights is not None:
        # Merge: caller weights take priority; missing keys fall back to config.
        merged = dict(config_weights)
        merged.update(weights)
        weights = merged
    else:
        weights = config_weights
    tokens_per_line = int(cfg.get("tokens_per_line", DEFAULT_TOKENS_PER_LINE))
    token_cap = int(cfg.get("first_stage_token_cap", DEFAULT_FIRST_STAGE_TOKEN_CAP))

    seeds = _resolve_seeds(conn, task, seed_files)

    first_stage = _first_stage(conn, task, top_n=max(first_stage_limit, k * 5), token_cap=token_cap)

    # 'heavy' (ColBERT/jina-reranker-v3) was cut from MVP per CodeRAG-Bench
    # evidence — re-introduce here when A.13 ships and eval shows ≥3pt
    # recall@20 lift over 'fast'. For now only 'fast' triggers structural
    # rerank; 'off' falls back to lexical-only.
    use_personalized = rerank in ("fast", "learned")
    scored = structural_score(
        conn,
        first_stage,
        seeds,
        weights,
        use_personalized=use_personalized,
        config_root=config_root,
        task=task,
    )

    # v12.2: optional learned-ranker overlay. Robs the score from the
    # structural blend when a model is available; otherwise no-op.
    if rerank == "learned":
        try:
            from roam.retrieve.learned_ranker import is_available
            from roam.retrieve.learned_ranker import score as learned_score

            if is_available():
                learned_scores = learned_score(scored, task)
                if learned_scores:
                    # Replace the structural score with the learned model's
                    # score, but keep all justifications for transparency.
                    for c in scored:
                        sid = int(c["symbol_id"])
                        if sid in learned_scores:
                            c["score"] = round(learned_scores[sid], 4)
                            c.setdefault("justifications", {})["learned"] = round(learned_scores[sid], 4)
                    scored.sort(key=lambda x: -x["score"])
        except Exception:
            # Silent fallback — keep the structural ranking
            pass

    selected, budget_used = _apply_budget(scored, budget=budget, k=k, tokens_per_line=tokens_per_line)

    return {
        "task": task,
        "rerank": rerank,
        "seeds": [{"symbol_id": sid, "weight": round(weight, 4)} for sid, weight in seeds.items()],
        "candidates": selected,
        "total_candidates": len(scored),
        "budget": budget,
        "budget_used": budget_used,
        "k": k,
        "weights": weights,
    }


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _resolve_seeds(
    conn: sqlite3.Connection,
    task: str,
    seed_files: list[str] | None,
) -> dict[int, float]:
    """Resolve `--seed-files` first; if absent, infer from task text."""
    if seed_files:
        seeds = _seeds_from_files(conn, seed_files)
        if seeds:
            return seeds
    return infer_seeds(conn, task)


def _seeds_from_files(conn: sqlite3.Connection, files: list[str]) -> dict[int, float]:
    """Map file paths to all symbols inside, weighted equally.

    Resolution order per file:

    1. **Exact match** against ``files.path`` — the canonical case.
    2. **Suffix match anchored at ``/``** — so ``src/auth.py`` matches
       ``packages/x/src/auth.py`` but **not** ``otherpath/src/authNotMine.py``.
       Implemented via ``LIKE '%/<path>'`` (the leading ``%`` plus the
       fixed ``/`` separator gives the anchor).
    3. **Top-level filename suffix** — when the supplied path has no
       directory part (e.g. ``auth.py``), match anywhere via
       ``LIKE '%/<basename>'`` **or** an exact basename match for files
       that live at the repo root.

    Substring-anywhere matches (the previous ``LIKE '%path'`` shape) are
    not used — they were the source of false positives for paths that
    are substrings of unrelated paths (``foo`` matching ``foobar``).

    Files queried in a single batch via SQL UNION to avoid an N+1.
    """
    cleaned: list[str] = []
    for raw in files:
        norm = raw.replace("\\", "/").strip().lstrip("./")
        if norm:
            cleaned.append(norm)
    if not cleaned:
        return {}

    seeds: dict[int, float] = {}

    # Single batched query — exact OR anchored-suffix per file.
    where_parts: list[str] = []
    params: list[str] = []
    for path in cleaned:
        # Exact path
        where_parts.append("f.path = ?")
        params.append(path)
        # Anchored-suffix path. "/<path>" guarantees the supplied segment
        # starts at a directory boundary, so substrings of unrelated names
        # don't match.
        if "/" in path:
            where_parts.append("f.path LIKE ?")
            params.append(f"%/{path}")
        else:
            where_parts.append("f.path LIKE ?")
            params.append(f"%/{path}")

    sql = f"SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE {' OR '.join(where_parts)}"
    for row in conn.execute(sql, params).fetchall():
        seeds[int(row[0])] = seeds.get(int(row[0]), 0.0) + 1.0
    return seeds


# Same column weights as the rest of the search stack.
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"


def _first_stage(
    conn: sqlite3.Connection,
    task: str,
    *,
    top_n: int,
    token_cap: int = DEFAULT_FIRST_STAGE_TOKEN_CAP,
) -> list[dict]:
    """FTS5 BM25 first stage. Returns up to *top_n* candidate dicts.

    Caps the OR fan-out at *token_cap* tokens — a query with 50
    token-shaped fragments shouldn't emit a 50-clause MATCH expression.
    Tokens are kept in the order returned by :func:`extract_tokens`,
    which prioritises file paths > dotted > snake > camel > Pascal —
    most-specific first.
    """
    tokens = extract_tokens(task)
    if not tokens:
        return []
    if token_cap > 0:
        tokens = tokens[:token_cap]

    fts_clauses = [_fts5_query_for(t) for t in tokens]
    fts_clauses = [c for c in fts_clauses if c]
    if not fts_clauses:
        return []
    # Wrap each token-clause in parens so the OR-fan-out is unambiguous.
    fts_query = " OR ".join(f"({c})" for c in fts_clauses)

    if not _has_symbol_fts(conn):
        return _like_first_stage(conn, tokens, top_n=top_n)

    try:
        rows = conn.execute(
            f"""
            SELECT sf.rowid AS symbol_id,
                   -bm25(symbol_fts, {_BM25_WEIGHTS}) AS fts_score,
                   s.name, s.qualified_name, s.kind, s.line_start, s.line_end,
                   f.path AS file_path
            FROM symbol_fts sf
            JOIN symbols s ON sf.rowid = s.id
            JOIN files f ON s.file_id = f.id
            WHERE symbol_fts MATCH ?
            ORDER BY bm25(symbol_fts, {_BM25_WEIGHTS})
            LIMIT ?
            """,
            (fts_query, top_n),
        ).fetchall()
    except sqlite3.OperationalError:
        return _like_first_stage(conn, tokens, top_n=top_n)

    return [dict(r) for r in rows]


def _has_symbol_fts(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def _like_first_stage(conn: sqlite3.Connection, tokens: list[str], *, top_n: int) -> list[dict]:
    """Fallback when FTS5 is unavailable. LIKE search per token."""
    accumulated: dict[int, dict] = {}
    for token in tokens:
        like = f"%{token}%"
        try:
            rows = conn.execute(
                "SELECT s.id AS symbol_id, s.name, s.qualified_name, s.kind, "
                "       s.line_start, s.line_end, f.path AS file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name LIKE ? OR s.qualified_name LIKE ? "
                "LIMIT ?",
                (like, like, top_n),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            sid = int(row["symbol_id"])
            entry = accumulated.setdefault(sid, dict(row))
            entry["fts_score"] = entry.get("fts_score", 0.0) + 1.0
    return list(accumulated.values())[:top_n]


def _apply_budget(
    scored: list[dict],
    *,
    budget: int,
    k: int,
    tokens_per_line: int = DEFAULT_TOKENS_PER_LINE,
) -> tuple[list[dict], int]:
    """Take the top items until *budget* tokens or *k* items, whichever first."""
    selected: list[dict] = []
    used = 0
    for item in scored:
        line_start = item.get("line_start") or 0
        line_end = item.get("line_end") or line_start
        line_count = max(1, int(line_end) - int(line_start) + 1)
        cost = line_count * tokens_per_line

        if budget and used + cost > budget and selected:
            break
        if k and len(selected) >= k:
            break

        selected.append({**item, "estimated_tokens": cost})
        used += cost

    return selected, used
