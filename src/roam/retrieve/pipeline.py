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
    # R.2 — pull in symbols from files structurally adjacent to the
    # first-stage hits (imports both directions). The lexical query
    # finds e.g. ``ruby_lang.py`` directly, but the eval expects also
    # ``registry.py`` and ``test_ruby.py`` — neither shares query
    # tokens. Expanding through file_edges restores those structurally-
    # related files into the candidate pool so the structural reranker
    # can score them.
    first_stage = _expand_via_file_neighbors(conn, first_stage, max_neighbors_per_file=4, expansion_cap=80)

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
        except ImportError:
            # ``lightgbm`` not installed — that's the documented opt-in
            # extra. Fall back to structural ranking silently.
            is_available = lambda: False  # noqa: E731
            learned_score = None  # type: ignore[assignment]

        if learned_score is not None and is_available():
            try:
                learned_scores = learned_score(scored, task)
            except Exception as exc:
                # Don't silently swallow runtime errors — they signal a
                # real bug in the learned ranker we want surfaced. Log
                # via the standard logger so CI captures it.
                import logging

                logging.getLogger(__name__).debug("learned ranker failed; falling back to structural: %s", exc)
                learned_scores = None
            if learned_scores:
                # Replace the structural score with the learned model's
                # score, but keep all justifications for transparency.
                for c in scored:
                    sid = int(c["symbol_id"])
                    if sid in learned_scores:
                        c["score"] = round(learned_scores[sid], 4)
                        c.setdefault("justifications", {})["learned"] = round(learned_scores[sid], 4)
                scored.sort(key=lambda x: -x["score"])

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


def _expand_via_file_neighbors(
    conn: sqlite3.Connection,
    first_stage: list[dict],
    *,
    max_neighbors_per_file: int = 4,
    expansion_cap: int = 80,
    seed_top_n: int = 30,
    hub_threshold: int = 20,
) -> list[dict]:
    """Add symbols from files connected via ``file_edges`` to the seed
    files (the files that produced first-stage hits).

    Each added symbol gets a small ``fts_score`` (5% of the originating
    seed file's median score) so structural rerank can lift it without
    letting expansion dominate the lexical hits. We cap the expansion
    so a single hub file can't drag in hundreds of unrelated symbols.

    R.8 (redacted): two precision filters layered on top
    of the original implementation —

    * ``seed_top_n`` — only the top-N first-stage hits by fts_score
      seed the expansion. Previously *all* 200 hits seeded; the
      bottom-150 had near-zero fts and pulled in unrelated files.
    * ``hub_threshold`` — files whose total file_edges degree exceeds
      this are skipped as expansion seeds. Utility files (logger,
      formatter, db.connection) are imported by 100+ other files; their
      "neighbours" are the entire codebase, which is the leak the
      dogfood notes flagged for ``seeds.py``.

    Returns the original ``first_stage`` plus expansion symbols.
    """
    if not first_stage:
        return first_stage

    # Sort by fts_score descending and take only the top-N as seeds.
    sorted_by_fts = sorted(first_stage, key=lambda c: -float(c.get("fts_score") or 0.0))
    strong_seeds = sorted_by_fts[: max(seed_top_n, 1)]

    seed_paths: dict[str, float] = {}
    for c in strong_seeds:
        path = c.get("file_path") or c.get("file") or ""
        score = float(c.get("fts_score") or 0.0)
        if not path:
            continue
        if score > seed_paths.get(path, 0.0):
            seed_paths[path] = score
    if not seed_paths:
        return first_stage

    # Resolve seed paths to file ids in one batch.
    placeholders = ",".join("?" for _ in seed_paths)
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE path IN ({placeholders})",
        list(seed_paths.keys()),
    ).fetchall()
    file_id_to_path: dict[int, str] = {int(r["id"]): r["path"] for r in rows}
    if not file_id_to_path:
        return first_stage

    # Skip hub seeds — files with degree > hub_threshold pollute
    # expansion with unrelated importers. Compute degree once across
    # the seed set.
    seed_ids = list(file_id_to_path)
    placeholders = ",".join("?" for _ in seed_ids)
    try:
        degree_rows = conn.execute(
            f"""
            SELECT fid, SUM(d) AS total FROM (
                SELECT source_file_id AS fid, COUNT(*) AS d FROM file_edges
                WHERE source_file_id IN ({placeholders}) GROUP BY source_file_id
                UNION ALL
                SELECT target_file_id AS fid, COUNT(*) AS d FROM file_edges
                WHERE target_file_id IN ({placeholders}) GROUP BY target_file_id
            ) GROUP BY fid
            """,
            seed_ids + seed_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        degree_rows = []
    hub_ids = {int(r["fid"]) for r in degree_rows if int(r["total"] or 0) > hub_threshold}
    non_hub_seed_ids = [sid for sid in seed_ids if sid not in hub_ids]
    if not non_hub_seed_ids:
        return first_stage

    # Neighbour file ids via file_edges (both directions), only from
    # non-hub seeds.
    placeholders = ",".join("?" for _ in non_hub_seed_ids)
    try:
        neighbor_rows = conn.execute(
            f"""
            SELECT source_file_id AS seed_id, target_file_id AS neighbor_id
            FROM file_edges WHERE source_file_id IN ({placeholders})
            UNION
            SELECT target_file_id AS seed_id, source_file_id AS neighbor_id
            FROM file_edges WHERE target_file_id IN ({placeholders})
            """,
            non_hub_seed_ids + non_hub_seed_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return first_stage
    if not neighbor_rows:
        return first_stage

    neighbor_to_seed: dict[int, int] = {}
    for r in neighbor_rows:
        nid = int(r["neighbor_id"])
        sid = int(r["seed_id"])
        if nid in file_id_to_path:
            continue  # already a seed file — no need to expand to itself
        neighbor_to_seed.setdefault(nid, sid)

    if not neighbor_to_seed:
        return first_stage

    existing_symbol_ids = {int(c["symbol_id"]) for c in first_stage if c.get("symbol_id") is not None}
    expansion: list[dict] = []
    placeholders = ",".join("?" for _ in neighbor_to_seed)
    sym_rows = conn.execute(
        f"""
        SELECT s.id AS symbol_id, s.name, s.qualified_name, s.kind,
               s.line_start, s.line_end, f.id AS file_id, f.path AS file_path
        FROM symbols s JOIN files f ON s.file_id = f.id
        WHERE f.id IN ({placeholders})
        ORDER BY f.id, s.line_start
        """,
        list(neighbor_to_seed.keys()),
    ).fetchall()

    per_file_count: dict[int, int] = {}
    for row in sym_rows:
        sid = int(row["symbol_id"])
        if sid in existing_symbol_ids:
            continue
        fid = int(row["file_id"])
        if per_file_count.get(fid, 0) >= max_neighbors_per_file:
            continue
        if len(expansion) >= expansion_cap:
            break
        seed_fid = neighbor_to_seed.get(fid)
        seed_path = file_id_to_path.get(seed_fid, "") if seed_fid else ""
        seed_score = seed_paths.get(seed_path, 0.0)
        entry = dict(row)
        entry["fts_score"] = seed_score * 0.05  # weak boost, not lexical hit
        entry["expansion"] = True
        expansion.append(entry)
        per_file_count[fid] = per_file_count.get(fid, 0) + 1

    return first_stage + expansion


def _apply_budget(
    scored: list[dict],
    *,
    budget: int,
    k: int,
    tokens_per_line: int = DEFAULT_TOKENS_PER_LINE,
) -> tuple[list[dict], int]:
    """Take the top items until *budget* tokens or *k* items, whichever
    first — with **file-level diversity** so a single hot file can't
    monopolise the top-K.

    Pass 1 takes the highest-scoring symbol from each unique file (in
    score order). Pass 2 fills any remaining slots with lower-scoring
    symbols from already-seen files. This solves the failure mode the
    self-bench surfaced (run 2026-05-01): a 20-candidate window
    collapsing to 5 unique files because of repeat hits inside hot test
    files. The user-visible "where is X" task wants files first; if
    they specifically need the multiple-symbols-per-file shape they
    can pass ``k`` larger.
    """
    selected: list[dict] = []
    used = 0
    seen_files: set[str] = set()
    deferred: list[dict] = []

    def _cost(item: dict) -> int:
        line_start = item.get("line_start") or 0
        line_end = item.get("line_end") or line_start
        line_count = max(1, int(line_end) - int(line_start) + 1)
        return line_count * tokens_per_line

    def _add(item: dict) -> bool:
        nonlocal used
        cost = _cost(item)
        if budget and used + cost > budget and selected:
            return False
        if k and len(selected) >= k:
            return False
        selected.append({**item, "estimated_tokens": cost})
        used += cost
        return True

    # Pass 1: best-of-file (preserve score order)
    for item in scored:
        f = item.get("file_path") or item.get("file") or ""
        if f in seen_files:
            deferred.append(item)
            continue
        if not _add(item):
            # budget/k exhausted on first pass — stop entirely
            return selected, used
        seen_files.add(f)

    # Pass 2: fill leftover slots with deferred (lower-ranked) symbols
    for item in deferred:
        if not _add(item):
            break

    return selected, used
