"""Collect and persist health metrics for snapshot/trend tracking."""

from __future__ import annotations

import sqlite3
import subprocess
import time

# W886: delegate to the canonical commands-layer helper (W873 audit).
# Previous narrow Python-only variant missed test files in non-Python
# projects (Go ``*_test.go``, Java ``*Test.java``, JS ``*.spec.ts``,
# etc.), which let their dead-export rows leak into the snapshot. The
# canonical helper covers all of those.
from roam.commands.changed_files import is_test_file as _is_test_path
from roam.coverage_reports import imported_coverage_overview
from roam.db.connection import find_project_root
from roam.db.queries import TOP_BY_BETWEENNESS, TOP_BY_DEGREE, UNREFERENCED_EXPORTS

# W1451 — the 0-100 score written into ``snapshots`` MUST be the same number
# ``roam health`` prints today, because ``roam health --baseline <ref>`` diffs
# one against the other. Two implementations existed and disagreed (64 vs 71
# on this repository at the same instant), so every --baseline run reported a
# phantom +7 improvement. One function, both call sites.
from roam.quality.health_score import (
    compute_health_score,
    compute_tangle_ratio,
    fetch_average_file_health,
)

# W1460 — the DEFINITION version of the numbers ``collect_metrics`` returns.
#
# BUMP THIS on any change that moves the metrics on an UNCHANGED tree:
#   * the health-score formula (weights, inputs, caps), OR
#   * graph construction (edge resolution, cycle detection, betweenness
#     sampling, any index-time change to what lands in ``edges``).
#
# Why both: a stored snapshot is compared against a live ``collect_metrics``
# by ``roam budget``, ``roam health --baseline``, ``fitness``, ``alerts``,
# ``forecast``, ``bisect`` and the workspace aggregator. Those comparisons
# are only meaningful between rows produced by the SAME definition.
#
# Version 1 (implicit, NULL in the DB): everything written before this column
#   existed.
# Version 2: the three metric-moving fixes released together — health-score
#   unification (one implementation, was two that disagreed 64 vs 71),
#   seeded betweenness sampling, and the case-insensitive edge-resolution
#   guard that removed ~6.5% of edges and split the largest SCC 1484 -> 123.
#   Cycles go UP under v2 while tangle_ratio goes DOWN: the fabricated edges
#   had fused unrelated code into one giant SCC, and breaking it yields many
#   small genuine cycles with far fewer symbols trapped. A v1->v2 delta is
#   therefore NOT a regression, and must never be reported as one.
SNAPSHOT_METRICS_VERSION = 2

# Rows written before the column existed carry NULL. Every consumer resolves
# NULL to this value rather than guessing, so "no stamp" is a definite old
# version and never accidentally equal to the current one.
LEGACY_METRICS_VERSION = 1

# W1461 — the metrics DERIVED FROM the symbol graph, i.e. the ones that are
# unmeasurable when ``build_symbol_graph`` raises.
#
# The failure mode this names: a failed graph build floors THREE penalty
# inputs to their BEST possible values at once — ``cycles`` to 0, an empty
# SCC list to ``tangle_ratio`` 0.0, and ``G = None`` to ``layer_violations``
# 0 — so ``compute_health_score`` returns a near-perfect number. Measured on
# a 3-symbol fixture holding one real import cycle: health_score 9 -> 85
# (+76); on this repository's own index, 64 -> 71. The inflation SCALES WITH
# HOW TANGLED THE CODE ACTUALLY IS, i.e. the floor helps most exactly where
# the code is worst, and the resulting dict was byte-identical in shape to an
# honest one, so no consumer could tell.
#
# "Could not be measured" is NOT "measured clean". Every value listed here is
# reported alongside an explicit ``degraded_metrics`` disclosure, is never
# persisted into ``snapshots`` (the row would be permanently indistinguishable
# from a genuinely healthy measurement — the table has no column able to
# record that the graph build failed), and is never allowed to satisfy a
# budget rule (``cmd_budget._evaluate_rule``).
GRAPH_DERIVED_METRICS = ("cycles", "tangle_ratio", "layer_violations", "health_score")


def snapshot_metrics_version(row) -> int:
    """Read a snapshot row's metrics-definition version.

    Accepts a ``sqlite3.Row``, a dict, or anything supporting ``[]`` /
    ``.get``. A missing column or a NULL value means the row predates the
    stamp and is version 1 — never the current version, so a pre-version
    baseline can never be silently compared against live v2 numbers.
    """
    if row is None:
        return LEGACY_METRICS_VERSION
    value = None
    try:
        if hasattr(row, "get"):
            value = row.get("metrics_version")
        else:
            value = row["metrics_version"]
    except (KeyError, IndexError, TypeError):
        value = None
    if value is None:
        return LEGACY_METRICS_VERSION
    try:
        return int(value)
    except (TypeError, ValueError):
        return LEGACY_METRICS_VERSION


def is_current_metrics_version(row) -> bool:
    """True when *row* was produced by the metrics definition running now."""
    return snapshot_metrics_version(row) == SNAPSHOT_METRICS_VERSION


def snapshots_have_metrics_version(conn) -> bool:
    """True when the ``snapshots`` table actually carries the stamp column.

    ``open_db(readonly=True)`` deliberately does NOT run ``ensure_schema``,
    so migrations never apply on a read-only connection. That makes the
    FIRST post-upgrade run of every read-only consumer — ``budget``,
    ``forecast``, ``fitness``, ``alerts``, ``bisect``, the workspace
    aggregator — see the OLD table shape. Naming ``metrics_version`` in a
    SELECT there raises ``OperationalError: no such column``, i.e. the fix
    for the incident would itself crash on the exact run it exists to
    protect. Callers that must name the column probe with this first;
    callers using ``SELECT *`` need nothing, because
    ``snapshot_metrics_version`` already resolves a missing key to legacy.
    """
    try:
        return any(row[1] == "metrics_version" for row in conn.execute("PRAGMA table_info(snapshots)"))
    except sqlite3.Error:
        return False


def _count_layer_violations_for_resilient_snapshots(G):
    """Count optional layer violations without hiding non-layer bugs."""
    if G is None:
        return 0

    from networkx.exception import NetworkXException

    from roam.graph.layers import detect_layers, find_violations

    try:
        layer_map = detect_layers(G)
        if not layer_map:
            return 0
        return len(find_violations(G, layer_map))
    except NetworkXException as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history:layers", _exc)
        return 0


def collect_metrics(conn):
    """Query the DB for all health metrics and compute a health score.

    Returns a dict with keys: files, symbols, edges, cycles,
    god_components, bottlenecks, dead_exports, layer_violations,
    health_score (0-100, higher = healthier).
    """
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    # Keep health math aligned with `roam health`. W1451: the detection caps
    # and thresholds are imported as NAMED constants rather than re-typed as
    # literals — a cap raised on one side and not the other is exactly the
    # silent-drift shape this module's score divergence came from.
    from roam.commands.cmd_health import (
        _BOTTLENECK_LIST_LIMIT,
        _BOTTLENECK_MIN_BETWEENNESS,
        _GOD_COMPONENT_LIST_LIMIT,
        _GOD_COMPONENT_MIN_DEGREE,
        _betweenness_percentiles,
    )

    # Cycles. Tarjan runs ONCE here; the SCC list is reused by the ``cycles``
    # count and by the tangle ratio below, which in turn feeds the health
    # score (an earlier revision re-ran find_cycles at each of those sites —
    # 3x SCC per collect_metrics call, pure waste on big graphs).
    cycle_list: list = []
    # W1461 — names of metrics whose value below is a FLOOR rather than a
    # measurement. Empty on every healthy run; the disclosure sibling that
    # was missing when this handler was the only one in the module without a
    # ``log_swallowed`` call.
    degraded_metrics: list[str] = []
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        cycle_list = find_cycles(G)
        cycles = len(cycle_list)
    except (ImportError, sqlite3.Error) as _exc:
        # W1461 — the graph is unavailable, so cycles / tangle_ratio /
        # layer_violations / health_score below are all floors. Say so
        # explicitly rather than emitting a dict that is indistinguishable
        # from a clean measurement. See GRAPH_DERIVED_METRICS.
        from roam.observability import log_swallowed

        log_swallowed("metrics_history:symbol_graph", _exc)
        cycles = 0
        G = None
        degraded_metrics = list(GRAPH_DERIVED_METRICS)

    # God components (same query + thresholds as cmd_health.py)
    degree_rows = conn.execute(TOP_BY_DEGREE, (_GOD_COMPONENT_LIST_LIMIT,)).fetchall()
    god_items = []
    for r in degree_rows:
        total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
        if total > _GOD_COMPONENT_MIN_DEGREE:
            god_items.append(
                {
                    "degree": total,
                    "file": r["file_path"],
                }
            )
    god_components = len(god_items)

    # Bottlenecks (same query + thresholds as cmd_health.py)
    bn_p90 = _betweenness_percentiles(conn, (90,)).values[0]

    bw_rows = conn.execute(TOP_BY_BETWEENNESS, (_BOTTLENECK_LIST_LIMIT,)).fetchall()
    _round = round
    bn_items = [
        {"betweenness": _round(r["betweenness"] or 0, 1), "file": r["file_path"]}
        for r in bw_rows
        if (r["betweenness"] or 0) > _BOTTLENECK_MIN_BETWEENNESS
    ]
    bottlenecks = len(bn_items)

    # Dead exports (filter test files — they're discovered by pytest, not imported)
    dead_rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
    dead_rows = [r for r in dead_rows if not _is_test_path(r["file_path"])]
    dead_exports = len(dead_rows)

    # Layer violations
    layer_violations = _count_layer_violations_for_resilient_snapshots(G)

    # Tangle ratio: percent of symbols inside a non-trivial SCC. Reuses the
    # single Tarjan result computed above.
    tangle_ratio = compute_tangle_ratio(cycle_list, symbols).ratio

    # W1451 — imported test coverage was previously read by `roam health` and
    # NOT by this writer, so on any project with coverage data the stored
    # snapshot score and the live score would drift apart by the 10% coverage
    # factor. Both call sites now feed the identical inputs to the identical
    # function.
    try:
        coverage = imported_coverage_overview(conn)
    except sqlite3.Error as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history:coverage", _exc)
        coverage = {}

    try:
        avg_file_health = fetch_average_file_health(conn)
    except sqlite3.Error as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history:file_health", _exc)
        avg_file_health = None

    health_score = compute_health_score(
        tangle_ratio=tangle_ratio,
        god_items=god_items,
        bn_items=bn_items,
        bn_p90=bn_p90,
        layer_violations=layer_violations,
        total_symbols=symbols,
        avg_file_health=avg_file_health,
        coverage_pct=coverage.get("coverage_pct"),
        coverable_lines=coverage.get("coverable_lines", 0),
    ).score

    # Average complexity from symbol_metrics
    avg_complexity = 0.0
    try:
        row = conn.execute("SELECT AVG(cognitive_complexity) FROM symbol_metrics").fetchone()
        if row and row[0] is not None:
            avg_complexity = round(row[0], 1)
    except sqlite3.Error as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history", _exc)

    # Brain methods (cc >= 25 AND line_count >= 50)
    brain_methods = 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbol_metrics WHERE cognitive_complexity >= 25 AND line_count >= 50"
        ).fetchone()
        if row:
            brain_methods = row[0]
    except sqlite3.Error as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history", _exc)

    # B8: spectral gap (algebraic connectivity / lambda2) of the file graph.
    # Persisting one gap per snapshot lets `roam forecast` project a TRUE
    # historical decay series toward structural failure. None on a degenerate
    # graph (< 2 nodes) or eigensolver failure — stored as NULL, never a
    # misleading 0.0.
    # Expected failure families only: module chain unavailable (ImportError),
    # schema drift on an old index (sqlite3.Error), graph-algorithm failure
    # (NetworkXException). Eigensolver/scipy failures never reach here — they
    # degrade to a 0.0 sentinel inside spectral._compute_algebraic_connectivity.
    # Logic errors (TypeError/AttributeError/...) propagate.
    spectral_gap_val = None
    from networkx.exception import NetworkXException

    try:
        from roam.graph.spectral_forecast import compute_current_spectral_gap

        spectral_gap_val = compute_current_spectral_gap(conn)
    except (ImportError, sqlite3.Error, NetworkXException) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("metrics_history", _exc)

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
        "spectral_gap": spectral_gap_val,
        # W1461 — [] on a healthy run; the names in GRAPH_DERIVED_METRICS
        # when the symbol graph could not be built.
        "degraded_metrics": degraded_metrics,
    }


def _git_info(root):
    """Get current git branch and commit hash."""
    branch = None
    commit = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if r.returncode == 0:
            branch = r.stdout.strip()
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("metrics_history", _exc)
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("metrics_history", _exc)
    return branch, commit


def append_snapshot(conn, tag=None, source="snapshot"):
    """Insert a new snapshot row with current metrics and git info.

    Returns the snapshot dict that was inserted.
    """
    root = find_project_root()
    metrics = collect_metrics(conn)
    branch, commit = _git_info(root)

    # W1461 — a measurement that could not be TAKEN is never written down.
    #
    # ``snapshots`` has no column able to record "the graph build failed"
    # (checked: PRAGMA table_info holds no status/partial/degraded field), so
    # a floored row is PERMANENTLY indistinguishable from a genuinely healthy
    # one. Storing it is worse than storing nothing twice over: the degraded
    # run silently passes the health gate, and then the next HONEST run reads
    # as a large regression against the fabricated baseline and fails the gate
    # spuriously. Refusing the write keeps the history honest — the previous
    # real baseline stays the baseline.
    if metrics.get("degraded_metrics"):
        from roam.observability import log_swallowed

        log_swallowed(
            "metrics_history:snapshot_refused",
            RuntimeError("unmeasured metrics: " + ", ".join(metrics["degraded_metrics"])),
        )
        return {
            "tag": tag,
            "source": source,
            "git_branch": branch,
            "git_commit": commit,
            "metrics_version": SNAPSHOT_METRICS_VERSION,
            **metrics,
            "stored": False,
        }

    # Dedup by commit: re-indexing the same commit (e.g. a no-op / comment-only
    # reindex) must replace the data point, not append a duplicate row. Without this,
    # all snapshot rows share one commit and the trend commands (forecast / bisect /
    # alerts-trend) run Theil-Sen / Mann-Kendall over identical values = confidently
    # vacuous output. Keep one row per (git_commit, source, metrics_version).
    #
    # W1460 — the dedup is now scoped to the metrics DEFINITION. Two rows at the
    # same commit written by the SAME definition really are duplicates: same tree,
    # same code, same numbers. Two rows written by DIFFERENT definitions are not
    # duplicates at all — they are one tree measured two different ways, and the
    # older one is the only surviving evidence of what this commit scored under
    # the previous definition. A plain version-blind DELETE silently destroyed it
    # on the first post-upgrade reindex, which is the opposite of the "never lose
    # a data point" property this whole column exists to provide.
    #
    # Consequence, handled: a commit can now hold more than one row, so any
    # single-row lookup keyed on git_commit MUST order deterministically. See
    # ``roam.graph.diff.find_before_snapshot``, whose bare ``LIMIT 1`` would
    # otherwise return whichever row SQLite happened to reach first — in practice
    # the OLDEST, i.e. exactly the stale one.
    if commit:
        conn.execute(
            "DELETE FROM snapshots WHERE git_commit = ? AND source = ? AND COALESCE(metrics_version, ?) = ?",
            (commit, source, LEGACY_METRICS_VERSION, SNAPSHOT_METRICS_VERSION),
        )
    conn.execute(
        """INSERT INTO snapshots
           (timestamp, tag, source, git_branch, git_commit,
            files, symbols, edges, cycles, god_components,
            bottlenecks, dead_exports, layer_violations, health_score,
            tangle_ratio, avg_complexity, brain_methods, spectral_gap,
            metrics_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(time.time()),
            tag,
            source,
            branch,
            commit,
            metrics["files"],
            metrics["symbols"],
            metrics["edges"],
            metrics["cycles"],
            metrics["god_components"],
            metrics["bottlenecks"],
            metrics["dead_exports"],
            metrics["layer_violations"],
            metrics["health_score"],
            metrics.get("tangle_ratio", 0),
            metrics.get("avg_complexity", 0),
            metrics.get("brain_methods", 0),
            metrics.get("spectral_gap"),
            SNAPSHOT_METRICS_VERSION,
        ),
    )
    conn.commit()

    return {
        "tag": tag,
        "source": source,
        "git_branch": branch,
        "git_commit": commit,
        "metrics_version": SNAPSHOT_METRICS_VERSION,
        **metrics,
        "stored": True,
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
