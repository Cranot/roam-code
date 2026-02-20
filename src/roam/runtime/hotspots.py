"""Runtime hotspot analysis: compare static vs runtime rankings."""

from __future__ import annotations

import sqlite3


def compute_hotspots(conn: sqlite3.Connection) -> list[dict]:
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
        runtime_ranked.append({
            "symbol_id": row[0],
            "symbol_name": row[1],
            "file_path": row[2],
            "call_count": row[3],
            "p50_latency_ms": row[4],
            "p99_latency_ms": row[5],
            "error_rate": row[6],
            "runtime_rank": rank,
        })

    # Build static ranking for matched symbols
    # Use a composite score: churn * complexity * pagerank
    static_scores: dict[int, dict] = {}
    for item in runtime_ranked:
        sid = item["symbol_id"]
        if sid is None:
            continue

        # Get static metrics
        row = conn.execute(
            "SELECT gm.pagerank, sm.cognitive_complexity, fs.total_churn "
            "FROM symbols s "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
            "LEFT JOIN file_stats fs ON s.file_id = fs.file_id "
            "WHERE s.id = ?",
            (sid,),
        ).fetchone()

        if row:
            pagerank = row[0] or 0.0
            complexity = row[1] or 0.0
            churn = row[2] or 0
            # Composite static score: higher = more statically important
            score = (churn + 1) * (complexity + 1) * (pagerank * 1000 + 1)
            static_scores[sid] = {
                "pagerank": round(pagerank, 4),
                "complexity": complexity,
                "churn": churn,
                "score": score,
            }

    # Rank by static score
    sorted_static = sorted(static_scores.items(), key=lambda x: x[1]["score"], reverse=True)
    static_rank_map: dict[int, int] = {}
    for rank, (sid, _) in enumerate(sorted_static, 1):
        static_rank_map[sid] = rank

    total_runtime = len(runtime_ranked)
    total_static = len(static_rank_map) if static_rank_map else total_runtime

    # Classify each runtime entry
    hotspots = []
    for item in runtime_ranked:
        sid = item["symbol_id"]
        runtime_rank = item["runtime_rank"]

        if sid is not None and sid in static_rank_map:
            static_rank = static_rank_map[sid]
            static_info = static_scores.get(sid, {})
        else:
            # Unmatched symbols get a high (bad) static rank
            static_rank = total_static + 1
            static_info = {"pagerank": 0, "complexity": 0, "churn": 0, "score": 0}

        # Classification based on rank discrepancy
        # Use relative position: top 30% = high, bottom 30% = low
        runtime_high = runtime_rank <= max(1, total_runtime * 0.3)
        static_high = static_rank <= max(1, total_static * 0.3)

        if runtime_high and not static_high:
            classification = "UPGRADE"
        elif runtime_high and static_high:
            classification = "CONFIRMED"
        elif not runtime_high and static_high:
            classification = "DOWNGRADE"
        else:
            classification = "CONFIRMED"

        hotspots.append({
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
                "pagerank": static_info.get("pagerank", 0),
                "complexity": static_info.get("complexity", 0),
                "churn": static_info.get("churn", 0),
            },
        })

    return hotspots
