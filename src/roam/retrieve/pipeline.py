"""A.1 — `roam retrieve` pipeline orchestrator.

End-to-end flow:

1. **Seeds** — caller-supplied ``--seed-file`` resolved to symbol ids,
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
from dataclasses import dataclass
from pathlib import Path

from roam.config import get_retrieve_config, get_retrieve_weights
from roam.db.connection import batched_in
from roam.retrieve.rerank import structural_score
from roam.retrieve.seeds import _fts5_query_for, extract_tokens, infer_seeds

# Default fallback when config is unavailable (e.g. called from a unit
# test that doesn't go through `roam init`). Source of truth is
# `[retrieve] tokens_per_line` in `.roam/config.toml`.
DEFAULT_TOKENS_PER_LINE = 4
DEFAULT_FIRST_STAGE_TOKEN_CAP = 8


@dataclass(frozen=True)
class RetrieveOptions:
    """Tuning knobs for the retrieve pipeline.

    Grouped into a parameter object so ``run_retrieve`` does not need
    one explicit argument per knob. Callers that only need defaults can
    omit the object entirely.
    """

    budget: int = 4000
    k: int = 20
    rerank: str = "fast"
    first_stage_limit: int = 200
    weights: dict[str, float] | None = None


def _resolve_options(options: RetrieveOptions | None) -> RetrieveOptions:
    """Return concrete options, applying the built-in defaults."""
    if options is None:
        return RetrieveOptions()
    return options


def _retrieve_settings(
    config_root: Path | None,
    weight_overrides: dict[str, float] | None,
) -> tuple[dict[str, float], int, int]:
    cfg = get_retrieve_config(config_root)
    config_weights = get_retrieve_weights(config_root)
    if weight_overrides is None:
        weights = config_weights
    else:
        # Merge: caller weights take priority; missing keys fall back to config.
        weights = dict(config_weights)
        weights.update(weight_overrides)
    tokens_per_line = int(cfg.get("tokens_per_line", DEFAULT_TOKENS_PER_LINE))
    token_cap = int(cfg.get("first_stage_token_cap", DEFAULT_FIRST_STAGE_TOKEN_CAP))
    return weights, tokens_per_line, token_cap


def _is_recoverable_index_error(exc: sqlite3.OperationalError) -> bool:
    """True only for the missing-index faults the pipeline degrades for.

    FTS5 or the search/adjacency tables may be absent on a fresh or
    partially-initialized repo; in those cases the caller falls back to
    LIKE search or skips expansion. Every other ``OperationalError``
    (locked database, I/O error, malformed query, etc.) must propagate so
    the real fault is visible.
    """
    msg = str(exc).lower()
    return "no such module: fts5" in msg or "no such table: symbol_fts" in msg or "no such table: file_edges" in msg


def _ensure_recoverable_index_error(exc: sqlite3.OperationalError) -> None:
    """Re-raise the active ``OperationalError`` unless it is index-degraded."""
    if not _is_recoverable_index_error(exc):
        raise


def run_retrieve(
    conn: sqlite3.Connection,
    task: str,
    *,
    seed_files: list[str] | None = None,
    config_root: Path | None = None,
    options: RetrieveOptions | None = None,
) -> dict:
    """Execute the full retrieve pipeline.

    Returns a dict with keys: ``task``, ``seeds``, ``candidates``,
    ``total_candidates``, ``budget``, ``budget_used``, ``k``, ``rerank``,
    ``weights``.

    Pass ``options=RetrieveOptions(weights={...})`` to override the
    config-driven weights (used by the ``roam eval-retrieve --sweep``
    harness to rotate vectors deterministically without rewriting
    config files).
    """
    opts = _resolve_options(options)
    weights, tokens_per_line, token_cap = _retrieve_settings(config_root, opts.weights)

    seeds = _resolve_seeds(conn, task, seed_files)

    first_stage = _first_stage(conn, task, top_n=max(opts.first_stage_limit, opts.k * 5), token_cap=token_cap)
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
    use_personalized = opts.rerank in ("fast", "learned")
    scored = structural_score(
        conn,
        first_stage,
        seeds,
        weights,
        use_personalized=use_personalized,
        config_root=config_root,
        task=task,
    )

    if opts.rerank == "learned":
        _replace_scores_only_when_learned_model_is_available(scored, task)

    selected, budget_used = _apply_budget(scored, budget=opts.budget, k=opts.k, tokens_per_line=tokens_per_line)

    return {
        "task": task,
        "rerank": opts.rerank,
        "seeds": [{"symbol_id": sid, "weight": round(weight, 4)} for sid, weight in seeds.items()],
        "candidates": selected,
        "total_candidates": len(scored),
        "budget": opts.budget,
        "budget_used": budget_used,
        "k": opts.k,
        "weights": weights,
    }


def _replace_scores_only_when_learned_model_is_available(scored: list[dict], task: str) -> None:
    """Overlay opt-in learned scores without hiding learned-ranker faults."""
    from roam.retrieve import learned_ranker

    if not learned_ranker.is_available():
        return

    learned_scores = learned_ranker.score(scored, task)
    if not learned_scores:
        return

    # Replace the structural score with the learned model's score, but
    # keep all justifications for transparency.
    for candidate in scored:
        sid = int(candidate["symbol_id"])
        if sid not in learned_scores:
            continue
        score = round(learned_scores[sid], 4)
        candidate["score"] = score
        candidate.setdefault("justifications", {})["learned"] = score
    scored.sort(key=lambda candidate: -candidate["score"])


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _resolve_seeds(
    conn: sqlite3.Connection,
    task: str,
    seed_files: list[str] | None,
) -> dict[int, float]:
    """Resolve `--seed-file` first; if absent, infer from task text."""
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
    except sqlite3.OperationalError as exc:
        if not _is_recoverable_index_error(exc):
            raise
        return _like_first_stage(conn, tokens, top_n=top_n)

    return [dict(r) for r in rows]


def _has_symbol_fts(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except sqlite3.OperationalError as exc:
        if not _is_recoverable_index_error(exc):
            raise
        return False


def _like_first_stage(conn: sqlite3.Connection, tokens: list[str], *, top_n: int) -> list[dict]:
    """Fallback when FTS5 is unavailable. LIKE search batched by token."""
    if not tokens:
        return []

    token_rows, params = _batched_like_patterns_preserve_token_recall(tokens)
    try:
        rows = conn.execute(
            f"""
            WITH token_patterns(pattern) AS (VALUES {token_rows})
            SELECT s.id AS symbol_id, s.name, s.qualified_name, s.kind,
                   s.line_start, s.line_end, f.path AS file_path,
                   COUNT(token_patterns.pattern) * 1.0 AS fts_score
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            JOIN token_patterns
              ON s.name LIKE token_patterns.pattern
              OR s.qualified_name LIKE token_patterns.pattern
            GROUP BY s.id, s.name, s.qualified_name, s.kind,
                     s.line_start, s.line_end, f.path
            ORDER BY fts_score DESC, s.id
            LIMIT ?
            """,
            [*params, top_n],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        _ensure_recoverable_index_error(exc)
        return []

    return [dict(row) for row in rows]


def _batched_like_patterns_preserve_token_recall(tokens: list[str]) -> tuple[str, list[str]]:
    """Build one VALUES table so fallback recall does not require N queries."""
    token_rows = ",".join("(?)" for _ in tokens)
    return token_rows, [f"%{token}%" for token in tokens]


def _strong_seed_paths(first_stage: list[dict], seed_top_n: int) -> dict[str, float]:
    """Return path -> strongest fts_score for the top-N lexical seeds.

    WHY: expansion seeded by low-score hits pulls in unrelated files;
    restrict the seed set to high-confidence matches so neighbor
    expansion trades recall for precision, not noise.
    """
    if not first_stage:
        return {}
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
    return seed_paths


def _resolve_file_ids(conn: sqlite3.Connection, paths: list[str]) -> dict[int, str]:
    """Batch-resolve file paths to database IDs."""
    if not paths:
        return {}
    placeholders = ",".join("?" for _ in paths)
    try:
        rows = conn.execute(
            f"SELECT id, path FROM files WHERE path IN ({placeholders})",
            paths,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        _ensure_recoverable_index_error(exc)
        return {}
    return {int(r["id"]): r["path"] for r in rows}


def _hub_file_ids(conn: sqlite3.Connection, file_ids: list[int], hub_threshold: int) -> set[int]:
    """Return IDs of files whose total ``file_edges`` degree exceeds the threshold.

    WHY: hub files connect to so many others that expanding through them
    floods results with unrelated symbols; discard them to protect precision.
    """
    if not file_ids:
        return set()
    try:
        degree_rows = batched_in(
            conn,
            """
            SELECT fid, SUM(d) AS total FROM (
                SELECT source_file_id AS fid, COUNT(*) AS d FROM file_edges
                WHERE source_file_id IN ({ph}) GROUP BY source_file_id
                UNION ALL
                SELECT target_file_id AS fid, COUNT(*) AS d FROM file_edges
                WHERE target_file_id IN ({ph}) GROUP BY target_file_id
            ) GROUP BY fid
            """,
            file_ids,
        )
    except sqlite3.OperationalError as exc:
        _ensure_recoverable_index_error(exc)
        return set()
    return {int(r["fid"]) for r in degree_rows if int(r["total"] or 0) > hub_threshold}


def _neighbor_seed_map(
    conn: sqlite3.Connection,
    seed_ids: list[int],
    file_id_to_path: dict[int, str],
) -> dict[int, int]:
    """Map neighbor file ID -> originating seed file ID via ``file_edges``.

    WHY: discover files related to the seed set while avoiding
    re-expansion back into the seed files themselves.
    """
    if not seed_ids:
        return {}
    placeholders = ",".join("?" for _ in seed_ids)
    try:
        neighbor_rows = conn.execute(
            f"""
            SELECT source_file_id AS seed_id, target_file_id AS neighbor_id
            FROM file_edges WHERE source_file_id IN ({placeholders})
            UNION
            SELECT target_file_id AS seed_id, source_file_id AS neighbor_id
            FROM file_edges WHERE target_file_id IN ({placeholders})
            """,
            seed_ids + seed_ids,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        _ensure_recoverable_index_error(exc)
        return {}

    neighbor_to_seed: dict[int, int] = {}
    for r in neighbor_rows:
        nid = int(r["neighbor_id"])
        sid = int(r["seed_id"])
        if nid in file_id_to_path:
            continue  # already a seed file — no need to expand to itself
        neighbor_to_seed.setdefault(nid, sid)
    return neighbor_to_seed


def _without_hub_neighbors(
    conn: sqlite3.Connection,
    neighbor_to_seed: dict[int, int],
    hub_threshold: int,
) -> dict[int, int]:
    """Drop neighbors that are hubs themselves.

    WHY: even a non-hub seed may legitimately import a utility hub;
    that hub is not the answer to the user's query, so reject it
    symmetrically to the seed-side filter.
    """
    if not neighbor_to_seed:
        return {}
    nb_hub_ids = _hub_file_ids(conn, list(neighbor_to_seed), hub_threshold)
    if not nb_hub_ids:
        return neighbor_to_seed
    return {nid: sid for nid, sid in neighbor_to_seed.items() if nid not in nb_hub_ids}


def _build_expansion_symbols(
    conn: sqlite3.Connection,
    neighbor_to_seed: dict[int, int],
    file_id_to_path: dict[int, str],
    seed_paths: dict[str, float],
    first_stage: list[dict],
    max_neighbors_per_file: int,
    expansion_cap: int,
) -> list[dict]:
    """Fetch symbols from neighbor files with per-file and total caps.

    WHY: without caps a single related file could contribute dozens of
    symbols and drown out the original lexical hits.
    """
    existing_symbol_ids = {int(c["symbol_id"]) for c in first_stage if c.get("symbol_id") is not None}
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

    expansion: list[dict] = []
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
    return expansion


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

    R.8: three precision filters layered on top
    of the original implementation —

    * ``seed_top_n`` — only the top-N first-stage hits by fts_score
      seed the expansion. Previously *all* 200 hits seeded; the
      bottom-150 had near-zero fts and pulled in unrelated files.
    * ``hub_threshold`` — files whose total file_edges degree exceeds
      this are skipped as expansion seeds. Utility files (logger,
      formatter, db.connection) are imported by 100+ other files; their
      "neighbours" are the entire codebase, which is the leak the
      dogfood notes flagged for ``seeds.py``.
    * **hub neighbour rejection** (v12.12) — even if a seed file is
      itself non-hub, its neighbour list often includes utility hubs
      (e.g. ``cmd_critique.py`` imports ``src/roam/output/formatter.py``). Those
      utility imports are not the answer to "where is X" queries, so
      reject neighbour files that exceed ``hub_threshold`` themselves.
      Symmetric to the seed-side filter; closes the residual hub-seed
      leak flagged in.

    Returns the original ``first_stage`` plus expansion symbols.
    """
    seed_paths = _strong_seed_paths(first_stage, seed_top_n)
    if not seed_paths:
        return first_stage

    file_id_to_path = _resolve_file_ids(conn, list(seed_paths))
    if not file_id_to_path:
        return first_stage

    hub_ids = _hub_file_ids(conn, list(file_id_to_path), hub_threshold)
    non_hub_seed_ids = [sid for sid in file_id_to_path if sid not in hub_ids]
    if not non_hub_seed_ids:
        return first_stage

    neighbor_to_seed = _neighbor_seed_map(conn, non_hub_seed_ids, file_id_to_path)
    if not neighbor_to_seed:
        return first_stage

    neighbor_to_seed = _without_hub_neighbors(conn, neighbor_to_seed, hub_threshold)
    if not neighbor_to_seed:
        return first_stage

    expansion = _build_expansion_symbols(
        conn,
        neighbor_to_seed,
        file_id_to_path,
        seed_paths,
        first_stage,
        max_neighbors_per_file,
        expansion_cap,
    )
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
