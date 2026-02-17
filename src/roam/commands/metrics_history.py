"""Collect and persist health metrics for snapshot/trend tracking."""

from __future__ import annotations

import os
import subprocess
import time

from roam.db.connection import find_project_root
from roam.db.queries import UNREFERENCED_EXPORTS, TOP_BY_DEGREE, TOP_BY_BETWEENNESS


def _is_test_path(file_path):
    """Check if a file is a test file (discovered by pytest, not imported)."""
    base = os.path.basename(file_path).lower()
    return base.startswith("test_") or base.endswith("_test.py")


def _compute_health_score(conn, G, symbols, god_items, bn_items, bn_p90,
                          layer_violations, find_cycles_fn, is_utility_path_fn):
    """Compute the weighted geometric mean health score (0-100)."""
    import math

    def _hf(value, scale):
        return math.exp(-value / scale) if scale > 0 else 1.0

    tangle_r = 0.0
    if G is not None and symbols > 0:
        try:
            cyc_ids = set()
            for scc in find_cycles_fn(G):
                cyc_ids.update(scc)
            tangle_r = len(cyc_ids) / symbols * 100
        except Exception:
            pass

    god_critical = sum(
        1 for g in god_items
        if (g["degree"] > 150 if is_utility_path_fn(g["file"]) else g["degree"] > 50)
    )
    bn_critical = sum(
        1 for b in bn_items
        if b["betweenness"] > bn_p90 * (1.5 if is_utility_path_fn(b["file"]) else 1.0)
    )

    god_signal = god_critical * 3 + len(god_items) * 0.5
    bn_signal = bn_critical * 2 + len(bn_items) * 0.3

    factors = [
        (_hf(tangle_r, 10), 0.30),
        (_hf(god_signal, 5), 0.20),
        (_hf(bn_signal, 4), 0.15),
        (_hf(layer_violations, 5), 0.15),
    ]
    try:
        avg_fh = conn.execute(
            "SELECT AVG(health_score) FROM file_stats WHERE health_score IS NOT NULL"
        ).fetchone()[0]
        factors.append((min(1.0, (avg_fh or 10) / 10.0), 0.20))
    except Exception:
        factors.append((1.0, 0.20))

    log_score = sum(w * math.log(max(h, 1e-9)) for h, w in factors)
    return max(0, min(100, int(100 * math.exp(log_score))))


def collect_metrics(conn):
    """Query the DB for all health metrics and compute a health score.

    Returns a dict with keys: files, symbols, edges, cycles,
    god_components, bottlenecks, dead_exports, layer_violations,
    health_score (0-100, higher = healthier).
    """
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    # Keep health math aligned with `roam health`.
    from roam.commands.cmd_health import _is_utility_path, _percentile

    # Cycles
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
        G = build_symbol_graph(conn)
        cycles = len(find_cycles(G))
    except Exception:
        cycles = 0
        G = None

    # God components (same query + thresholds as cmd_health.py)
    degree_rows = conn.execute(TOP_BY_DEGREE, (50,)).fetchall()
    god_items = []
    for r in degree_rows:
        total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
        if total > 20:
            god_items.append({
                "degree": total,
                "file": r["file_path"],
            })
    god_components = len(god_items)

    # Bottlenecks (same query + thresholds as cmd_health.py)
    all_bw = sorted(
        r[0] for r in conn.execute(
            "SELECT betweenness FROM graph_metrics WHERE betweenness > 0"
        ).fetchall()
    )
    bn_p70 = _percentile(all_bw, 70)
    bn_p90 = _percentile(all_bw, 90)

    bw_rows = conn.execute(TOP_BY_BETWEENNESS, (15,)).fetchall()
    bn_items = []
    for r in bw_rows:
        bw = r["betweenness"] or 0
        if bw > 0.5:
            bn_items.append({
                "betweenness": round(bw, 1),
                "file": r["file_path"],
            })
    bottlenecks = len(bn_items)

    # Dead exports (filter test files — they're discovered by pytest, not imported)
    dead_rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
    dead_rows = [r for r in dead_rows
                 if not _is_test_path(r["file_path"])]
    dead_exports = len(dead_rows)

    # Layer violations
    layer_violations = 0
    if G is not None:
        try:
            from roam.graph.layers import detect_layers, find_violations
            layer_map = detect_layers(G)
            if layer_map:
                layer_violations = len(find_violations(G, layer_map))
        except Exception:
            pass

    health_score = _compute_health_score(
        conn, G, symbols, god_items, bn_items, bn_p90,
        layer_violations, find_cycles, _is_utility_path,
    )

    # Tangle ratio: percentage of symbols in cycles
    tangle_ratio = 0.0
    if G is not None and symbols > 0:
        try:
            from roam.graph.cycles import find_cycles as _find_cycles
            cycle_list = _find_cycles(G)
            cycle_sym_ids = set()
            for scc in cycle_list:
                cycle_sym_ids.update(scc)
            tangle_ratio = round(len(cycle_sym_ids) / symbols * 100, 1)
        except Exception:
            pass

    # Average complexity from symbol_metrics
    avg_complexity = 0.0
    try:
        row = conn.execute(
            "SELECT AVG(cognitive_complexity) FROM symbol_metrics"
        ).fetchone()
        if row and row[0] is not None:
            avg_complexity = round(row[0], 1)
    except Exception:
        pass

    # Brain methods (cc >= 25 AND line_count >= 50)
    brain_methods = 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbol_metrics "
            "WHERE cognitive_complexity >= 25 AND line_count >= 50"
        ).fetchone()
        if row:
            brain_methods = row[0]
    except Exception:
        pass

    return {
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "cycles": cycles,
        "god_components": god_components,
        "bottlenecks": bottlenecks,
        "dead_exports": dead_exports,
        "layer_violations": layer_violations,
        "health_score": health_score,
        "tangle_ratio": tangle_ratio,
        "avg_complexity": avg_complexity,
        "brain_methods": brain_methods,
    }


def _git_info(root):
    """Get current git branch and commit hash."""
    branch = None
    commit = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            branch = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass
    return branch, commit


def append_snapshot(conn, tag=None, source="snapshot"):
    """Insert a new snapshot row with current metrics and git info.

    Returns the snapshot dict that was inserted.
    """
    root = find_project_root()
    metrics = collect_metrics(conn)
    branch, commit = _git_info(root)

    conn.execute(
        """INSERT INTO snapshots
           (timestamp, tag, source, git_branch, git_commit,
            files, symbols, edges, cycles, god_components,
            bottlenecks, dead_exports, layer_violations, health_score,
            tangle_ratio, avg_complexity, brain_methods)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(time.time()), tag, source, branch, commit,
            metrics["files"], metrics["symbols"], metrics["edges"],
            metrics["cycles"], metrics["god_components"],
            metrics["bottlenecks"], metrics["dead_exports"],
            metrics["layer_violations"], metrics["health_score"],
            metrics.get("tangle_ratio", 0),
            metrics.get("avg_complexity", 0),
            metrics.get("brain_methods", 0),
        ),
    )
    conn.commit()

    return {
        "tag": tag,
        "source": source,
        "git_branch": branch,
        "git_commit": commit,
        **metrics,
    }


def get_snapshots(conn, limit=None, since=None):
    """Fetch snapshot history, newest first.

    Args:
        limit: Max rows to return.
        since: Unix timestamp — only return snapshots after this time.
    """
    sql = "SELECT * FROM snapshots"
    params = []
    if since is not None:
        sql += " WHERE timestamp >= ?"
        params.append(since)
    sql += " ORDER BY timestamp DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
