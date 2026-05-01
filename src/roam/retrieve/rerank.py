"""A.1 — structural reranker for `roam retrieve`.

The reranker takes a list of first-stage candidates (from FTS5) plus a
seed map (from `seeds.infer_seeds` or caller-supplied `--seed-files`)
and produces a re-scored, re-ranked list. The score is a weighted blend
of structural signals that competitors with vector-only RAG cannot
compute:

* **alpha** — PageRank score (personalised on seeds when available, falling
  back to the persisted global PageRank in ``graph_metrics``).
* **epsilon** — clone-canonical boost. If a candidate has clone siblings
  in the persisted ``clone_pairs`` table (A.0), the candidate is tagged so
  the JSON envelope can surface "canonical / sibling-of" relationships.
* A retained lexical baseline from the FTS5 score so candidates with
  weak structural signal but strong textual relevance still rank.

`beta` (co-change), `gamma` (layer-distance) and `delta` (runtime hotspot)
are present in the weights dict for forward-compatibility but the v12.0
MVP does not yet apply them. They land in the v12.1 reranker once the
incremental signal plumbing is wired through the daemon.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from roam.config import get_retrieve_config
from roam.db.connection import batched_in
from roam.graph.clone_detect import get_clone_siblings
from roam.graph.dark_matter import co_change_score_to_seed_set
from roam.runtime.hotspots import runtime_score

#: Default lexical-baseline coefficient when config is unavailable.
#: Source of truth: ``[retrieve] lexical_baseline`` in ``.roam/config.toml``.
DEFAULT_LEXICAL_BASELINE = 0.5


def structural_score(
    conn: sqlite3.Connection,
    candidates: list[dict],
    seeds: dict[int, float],
    weights: dict[str, float],
    *,
    use_personalized: bool = True,
    config_root: Path | None = None,
    lexical_baseline: float | None = None,
) -> list[dict]:
    """Rerank *candidates* with structural signals.

    Parameters
    ----------
    conn:
        Open roam DB connection (read-only is fine).
    candidates:
        List of dicts with at least ``symbol_id`` and ``fts_score``. May
        also carry ``name``, ``qualified_name``, ``file_path``, ``kind``,
        ``line_start``, ``line_end``.
    seeds:
        Personalisation mass for PageRank. Empty dict triggers a
        global-PageRank lookup against ``graph_metrics``.
    weights:
        ``{alpha, beta, gamma, delta, epsilon}`` from
        ``roam.config.get_retrieve_weights``. Unrecognised keys are
        ignored.
    use_personalized:
        When ``True`` and ``seeds`` is non-empty, build the symbol graph
        and run :func:`personalized_pagerank`. Otherwise rely on the
        cached ``graph_metrics.pagerank`` column.

    Returns
    -------
    list[dict]
        ``candidates`` with two added fields per item: ``score`` (final
        sortable float) and ``justifications`` (dict of tag → value used
        by the JSON envelope).  Sorted descending by ``score``.
    """
    if not candidates:
        return []

    candidate_ids = {int(c["symbol_id"]) for c in candidates}

    pr_scores = _pagerank_scores(conn, candidate_ids, seeds, use_personalized=use_personalized)
    clone_tags = _clone_tags(conn, candidates)
    cochange_scores = _cochange_scores(conn, candidate_ids, seeds)
    runtime_scores = _runtime_scores(conn, candidate_ids)

    pr_max = max(pr_scores.values()) if pr_scores else 0.0
    fts_max = max((float(c.get("fts_score", 0.0)) for c in candidates), default=0.0)
    cochange_max = max(cochange_scores.values()) if cochange_scores else 0.0
    runtime_max = max(runtime_scores.values()) if runtime_scores else 0.0

    alpha = float(weights.get("alpha", 0.40))
    beta = float(weights.get("beta", 0.25))
    delta = float(weights.get("delta", 0.15))
    epsilon = float(weights.get("epsilon", 0.05))

    # Lexical baseline: explicit kwarg > config > module default. Independent
    # of the alpha/beta/... structural weight vector. Without it, candidates
    # with zero PR (rare — un-imported leaves) drop out even when textually
    # exact.
    if lexical_baseline is None:
        cfg = get_retrieve_config(config_root)
        lexical_baseline = float(cfg.get("lexical_baseline", DEFAULT_LEXICAL_BASELINE))

    out: list[dict] = []
    for c in candidates:
        sid = int(c["symbol_id"])
        pr_norm = (pr_scores.get(sid, 0.0) / pr_max) if pr_max > 0 else 0.0
        fts_norm = (float(c.get("fts_score", 0.0)) / fts_max) if fts_max > 0 else 0.0
        cochange_norm = cochange_scores.get(sid, 0.0) / cochange_max if cochange_max > 0 else 0.0
        runtime_norm = runtime_scores.get(sid, 0.0) / runtime_max if runtime_max > 0 else 0.0

        clone_info = clone_tags.get(sid)
        clone_boost = epsilon if clone_info else 0.0

        score = (
            alpha * pr_norm + beta * cochange_norm + delta * runtime_norm + lexical_baseline * fts_norm + clone_boost
        )

        justifications: dict[str, object] = {}
        if pr_norm > 0:
            justifications["pagerank"] = round(pr_norm, 4)
            if seeds and use_personalized:
                justifications["pagerank_kind"] = "personalized"
            else:
                justifications["pagerank_kind"] = "global"
        if fts_norm > 0:
            justifications["fts"] = round(fts_norm, 4)
        if cochange_norm > 0:
            justifications["co_change"] = round(cochange_norm, 4)
        if runtime_norm > 0:
            justifications["runtime_hot"] = round(runtime_norm, 4)
        if clone_info:
            justifications["clone_cluster"] = clone_info["cluster_id"]
            justifications["clone_siblings"] = clone_info["sibling_count"]

        out.append({**c, "score": round(score, 4), "justifications": justifications})

    out.sort(key=lambda x: -x["score"])
    return out


def _cochange_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    seeds: dict[int, float],
) -> dict[int, float]:
    """Per-candidate β contribution: max co-change score against any seed.

    No seeds → empty dict (β contributes 0 to every candidate). The
    reranker normalises across the candidate set so absolute magnitudes
    don't matter — only the *relative* ordering of co-change links.
    """
    if not seeds:
        return {}
    seed_ids = list(seeds.keys())
    out: dict[int, float] = {}
    for sid in candidate_ids:
        try:
            score = co_change_score_to_seed_set(conn, sid, seed_ids)
        except sqlite3.OperationalError:
            score = 0.0
        if score > 0:
            out[sid] = score
    return out


def _runtime_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
) -> dict[int, float]:
    """Per-candidate δ contribution: runtime-importance score in [0, 1].

    Scans ``runtime_stats`` once per call; symbols without ingested
    traces are simply absent from the result (β/δ are additive — no
    runtime data means δ contributes 0, never negative).
    """
    cand_set = set(candidate_ids)
    if not cand_set:
        return {}
    out: dict[int, float] = {}
    for sid in cand_set:
        try:
            score = runtime_score(conn, sid)
        except sqlite3.OperationalError:
            score = 0.0
        if score > 0:
            out[sid] = score
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _pagerank_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    seeds: dict[int, float],
    *,
    use_personalized: bool,
) -> dict[int, float]:
    """Return ``{symbol_id: pagerank}`` for the candidate set.

    With seeds and personalisation on, builds the symbol graph and runs
    :func:`personalized_pagerank`. Otherwise pulls cached scores from
    ``graph_metrics``. Either path returns scores only for the candidate
    ids — the caller never sees the full graph distribution.
    """
    candidate_set = set(candidate_ids)
    if not candidate_set:
        return {}

    if seeds and use_personalized:
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.pagerank import personalized_pagerank

            G = build_symbol_graph(conn)
            full = personalized_pagerank(G, seeds)
        except Exception:
            full = {}
        scores = {sid: full.get(sid, 0.0) for sid in candidate_set}
        if any(v > 0 for v in scores.values()):
            return scores
        # Personalised PR returned nothing useful — fall through to cached.

    # batched_in() chunks at SQLITE_MAX_VARIABLE_NUMBER (default 999) — required
    # by CLAUDE.md when callers may pass --k > 80 (top_n grows to 1000 placeholders).
    rows = batched_in(
        conn,
        "SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
        list(candidate_set),
    )
    return {int(row[0]): float(row[1]) for row in rows}


def _clone_tags(conn: sqlite3.Connection, candidates: list[dict]) -> dict[int, dict]:
    """Resolve clone membership per candidate.

    Returns ``{symbol_id: {cluster_id, sibling_count}}`` for every
    candidate with at least one persisted clone sibling.
    """
    out: dict[int, dict] = {}
    for c in candidates:
        file_path = c.get("file_path")
        name = c.get("name")
        if not file_path or not name:
            continue
        siblings = get_clone_siblings(conn, file_path, name)
        if not siblings:
            continue
        # All siblings of one symbol share its cluster (by construction
        # in store_clones); pick the first non-null cluster_id.
        cluster_id = next(
            (s["cluster_id"] for s in siblings if s.get("cluster_id") is not None),
            None,
        )
        out[int(c["symbol_id"])] = {
            "cluster_id": cluster_id,
            "sibling_count": len(siblings),
        }
    return out
