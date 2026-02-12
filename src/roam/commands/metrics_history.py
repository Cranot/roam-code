"""Collect and persist health metrics for snapshot/trend tracking."""

import subprocess
import time

from roam.db.connection import find_project_root
from roam.db.queries import UNREFERENCED_EXPORTS


def collect_metrics(conn):
    """Query the DB for all health metrics and compute a health score.

    Returns a dict with keys: files, symbols, edges, cycles,
    god_components, bottlenecks, dead_exports, layer_violations,
    health_score (0-100, higher = healthier).
    """
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # Cycles
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
        G = build_symbol_graph(conn)
        cycles = len(find_cycles(G))
    except Exception:
        cycles = 0
        G = None

    # God components (degree > 20)
    degree_rows = conn.execute(
        "SELECT (gm.in_degree + gm.out_degree) as total "
        "FROM graph_metrics gm WHERE (gm.in_degree + gm.out_degree) > 20"
    ).fetchall()
    god_components = len(degree_rows)

    # Bottlenecks (betweenness > 0.5)
    bn_rows = conn.execute(
        "SELECT 1 FROM graph_metrics WHERE betweenness > 0.5"
    ).fetchall()
    bottlenecks = len(bn_rows)

    # Dead exports
    dead_rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
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

    # Health score: 100 minus penalties, clamped to 0-100
    # Penalties scale with codebase size
    penalty = 0
    if symbols > 0:
        penalty += min(20, cycles * 3)                          # cycles: up to 20
        penalty += min(15, god_components * 2)                  # god: up to 15
        penalty += min(15, bottlenecks * 2)                     # bottlenecks: up to 15
        penalty += min(25, dead_exports * 100 / symbols)        # dead %: up to 25
        penalty += min(15, layer_violations * 3)                # layers: up to 15
        penalty += min(10, max(0, (cycles + god_components + bottlenecks + layer_violations) - 5))

    health_score = max(0, min(100, 100 - int(penalty)))

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
