"""Runtime hotspot analysis: compare static vs runtime rankings.

Public helpers
==============

* ``compute_hotspots`` — public alias for the runtime-vs-static ranking
  comparison.
* :func:`runtime_score` — symbol-level score in [0, 1] driven by call
  count, p99 latency, and error rate. Used by the retrieve reranker as
  the δ contribution and by ``roam critique``'s impact severity bump
  on changed symbols. One helper, two downstream consumers.
"""

from __future__ import annotations

import math
import sqlite3

# Score weights — keep these two consumers (`runtime_score`, the bulk
# variant `runtime_score_max_for_symbols`, and the classification
# thresholds in `compute_hotspots`) reading from the same constants so a
# weight change cannot silently drift one path vs another.
_CALL_VOLUME_WEIGHT = 0.6
_LATENCY_WEIGHT = 0.3
_ERROR_WEIGHT = 0.1
# 1000ms p99 maps to latency=1.0; anything slower is clipped.
_LATENCY_SATURATION_MS = 1000.0
# Default log baseline: a symbol with 1k+ calls saturates call_volume.
_DEFAULT_LOG_BASELINE = 1000.0
# Batched IN-clause size to stay under SQLite's parameter cap (cf.
# `roam.db.connection.batched_in`).
_BULK_FETCH_CHUNK = 400


def _score_from_metrics(
    call_count: float,
    p99_ms: float,
    error_rate: float,
    *,
    log_baseline: float = _DEFAULT_LOG_BASELINE,
) -> float:
    """Compose the runtime score from already-normalised numeric inputs.

    Pulled out of ``runtime_score`` / ``runtime_score_max_for_symbols``
    so both code paths share one weight/saturation contract. Inputs are
    expected to be non-negative finite numbers; the function clamps
    rather than raising on out-of-range values so a corrupt row cannot
    crash the reranker.
    """
    call_count = max(0.0, call_count)
    p99_ms = max(0.0, p99_ms)
    if log_baseline <= 1:
        log_baseline = _DEFAULT_LOG_BASELINE
    log_div = math.log10(log_baseline) or 1.0
    call_volume = min(1.0, math.log10(call_count + 1) / log_div)
    latency = min(1.0, p99_ms / _LATENCY_SATURATION_MS)
    err = max(0.0, min(1.0, error_rate))
    return _CALL_VOLUME_WEIGHT * call_volume + _LATENCY_WEIGHT * latency + _ERROR_WEIGHT * err


def runtime_score(
    conn: sqlite3.Connection,
    symbol_id: int,
    *,
    log_baseline: float = _DEFAULT_LOG_BASELINE,
) -> float:
    """Return a [0, 1] runtime-importance score for a symbol.

    Score = ``0.6·call_volume + 0.3·latency + 0.1·error_rate`` where:

    * **call_volume** = ``log10(call_count + 1) / log10(log_baseline)``
      capped at 1.0 (so a symbol with 1k+ calls saturates at 1.0; tunable
      via ``log_baseline``).
    * **latency** = ``min(p99 / 1000ms, 1.0)`` — anything ≥1s is maxed.
    * **error_rate** is already in [0, 1].

    Returns 0.0 when the symbol has no ``runtime_stats`` row — rather
    than raising, so the reranker can call this for every candidate
    without a separate "is hot?" check.
    """
    row = conn.execute(
        "SELECT call_count, p99_latency_ms, error_rate FROM runtime_stats WHERE symbol_id = ? LIMIT 1",
        (symbol_id,),
    ).fetchone()
    if not row:
        return 0.0

    return round(
        _score_from_metrics(
            float(row[0] or 0),
            float(row[1] or 0),
            float(row[2] or 0),
            log_baseline=log_baseline,
        ),
        4,
    )


def runtime_score_max_for_symbols(
    conn: sqlite3.Connection,
    symbol_ids: list[int] | set[int],
) -> float:
    """Return the max ``runtime_score`` across a set of symbols.

    Used by ``roam critique`` to bump the severity of an impact finding
    when at least one direct caller of the changed symbol is on a hot
    code-path. Returns 0.0 for an empty set or when none of the symbols
    have runtime data. Uses the canonical ``_DEFAULT_LOG_BASELINE`` so
    the bulk path and the per-symbol path agree on what saturates.
    """
    seen = list(set(symbol_ids))
    if not seen:
        return 0.0
    best = 0.0
    for chunk_start in range(0, len(seen), _BULK_FETCH_CHUNK):
        chunk = seen[chunk_start : chunk_start + _BULK_FETCH_CHUNK]
        rows = conn.execute(
            f"SELECT symbol_id, call_count, p99_latency_ms, error_rate "
            f"FROM runtime_stats "
            f"WHERE symbol_id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for row in rows:
            s = _score_from_metrics(
                float(row[1] or 0),
                float(row[2] or 0),
                float(row[3] or 0),
            )
            if s > best:
                best = s
    return round(best, 4)


def _static_scores_with_bounded_io(
    conn: sqlite3.Connection,
    symbol_ids: list[int | None],
) -> dict[int, dict]:
    """Fetch static metrics for runtime symbols without per-row queries."""
    seen = list(dict.fromkeys(sid for sid in symbol_ids if sid is not None))
    if not seen:
        return {}

    static_scores: dict[int, dict] = {}
    for chunk_start in range(0, len(seen), _BULK_FETCH_CHUNK):
        chunk = seen[chunk_start : chunk_start + _BULK_FETCH_CHUNK]
        rows = conn.execute(
            "SELECT s.id, gm.pagerank, sm.cognitive_complexity, fs.total_churn "
            "FROM symbols s "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
            "LEFT JOIN file_stats fs ON s.file_id = fs.file_id "
            f"WHERE s.id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()

        for row in rows:
            sid = row[0]
            pagerank = row[1] or 0.0
            complexity = row[2] or 0.0
            churn = row[3] or 0
            score = (churn + 1) * (complexity + 1) * (pagerank * 1000 + 1)
            static_scores[sid] = {
                "pagerank": round(pagerank, 4),
                "complexity": complexity,
                "churn": churn,
                "score": score,
            }

    return static_scores


def _compute_hotspots(conn: sqlite3.Connection) -> list[dict]:
    """Compare static analysis ranking vs runtime ranking.

    1. Get symbols ranked by static metrics (churn, complexity, PageRank)
    2. Get symbols ranked by runtime metrics (call_count, latency, error_rate)
    3. Find discrepancies and classify each:
       - UPGRADE: runtime-critical but statically safe
       - CONFIRMED: both agree on importance
       - DOWNGRADE: statically risky but low traffic

    Returns a list of hotspot dicts sorted by runtime rank.
    """
    # Get runtime stats joined with symbol info
    runtime_rows = conn.execute(
        "SELECT rs.symbol_id, rs.symbol_name, rs.file_path, "
        "rs.call_count, rs.p50_latency_ms, rs.p99_latency_ms, rs.error_rate "
        "FROM runtime_stats rs "
        "ORDER BY rs.call_count DESC"
    ).fetchall()

    if not runtime_rows:
        return []

    # Build runtime ranking (1-based)
    runtime_ranked = []
    for rank, row in enumerate(runtime_rows, 1):
        runtime_ranked.append(
            {
                "symbol_id": row[0],
                "symbol_name": row[1],
                "file_path": row[2],
                "call_count": row[3],
                "p50_latency_ms": row[4],
                "p99_latency_ms": row[5],
                "error_rate": row[6],
                "runtime_rank": rank,
            }
        )

    static_scores = _static_scores_with_bounded_io(
        conn,
        [item["symbol_id"] for item in runtime_ranked],
    )

    # Rank by static score
    sorted_static = sorted(static_scores.items(), key=lambda x: x[1]["score"], reverse=True)
    static_rank_map: dict[int, int] = {}
    for rank, (sid, _) in enumerate(sorted_static, 1):
        static_rank_map[sid] = rank

    total_runtime = len(runtime_ranked)
    total_static = len(static_rank_map) if static_rank_map else total_runtime
    runtime_high_cutoff = max(1, total_runtime * 0.3)
    static_high_cutoff = max(1, total_static * 0.3)

    # Classify each runtime entry
    hotspots = []
    for item in runtime_ranked:
        sid = item["symbol_id"]
        runtime_rank = item["runtime_rank"]

        if sid is not None and sid in static_rank_map:
            static_rank = static_rank_map[sid]
            static_info = static_scores[sid]
        else:
            # Unmatched symbols get a high (bad) static rank
            static_rank = total_static + 1
            static_info = {"pagerank": 0, "complexity": 0, "churn": 0, "score": 0}

        # Classification based on rank discrepancy
        # Use relative position: top 30% = high, bottom 30% = low
        runtime_high = runtime_rank <= runtime_high_cutoff
        static_high = static_rank <= static_high_cutoff

        if runtime_high and not static_high:
            classification = "UPGRADE"
        elif runtime_high and static_high:
            classification = "CONFIRMED"
        elif not runtime_high and static_high:
            classification = "DOWNGRADE"
        else:
            classification = "CONFIRMED"

        hotspots.append(
            {
                "symbol_name": item["symbol_name"],
                "file_path": item["file_path"],
                "symbol_id": sid,
                "static_rank": static_rank,
                "runtime_rank": runtime_rank,
                "classification": classification,
                "runtime_stats": {
                    "call_count": item["call_count"],
                    "p50_latency_ms": item["p50_latency_ms"],
                    "p99_latency_ms": item["p99_latency_ms"],
                    "error_rate": item["error_rate"],
                },
                "static_stats": {
                    "pagerank": static_info["pagerank"],
                    "complexity": static_info["complexity"],
                    "churn": static_info["churn"],
                },
            }
        )

    return hotspots


compute_hotspots = _compute_hotspots
