"""Unified per-file and per-symbol metrics command.

Consolidates complexity, fan-in/fan-out, PageRank, churn, test coverage,
layer depth, dead-code risk, LOC, and co-change data into a single
structured output.  All data is read from the existing SQLite index.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because metrics outputs are invocation-scoped per-file /
per-symbol metric exports (complexity, fan-in/fan-out, PageRank,
churn, LOC, co-change scalars) — not per-location code violations.
The export describes neutral measurement data rather than defects at
source coordinates; SARIF consumers scan for per-finding rule_id +
region rows. When SARIF-shaped findings are needed, run the underlying
detectors directly (``roam complexity --sarif``, ``roam dead --sarif``)
— metrics aggregates them but does not produce novel per-finding rows.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1224-audit memo.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index, find_symbol, resolve_file_symbols
from roam.db.connection import batched_in, open_db
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import COGNITIVE_COMPLEXITY_DEFINITION

# ---------------------------------------------------------------------------
# Health scoring
# ---------------------------------------------------------------------------


def _health_label(metrics: dict) -> str:
    """Derive a health label from collected metrics.

    Heuristic:
      - poor:  high complexity, high dead-code risk, or very high churn
      - fair:  moderate issues
      - good:  everything within norms
    """
    score = 0
    cc = metrics.get("complexity", 0) or 0
    fan_out = metrics.get("fan_out", 0) or 0
    churn = metrics.get("churn", 0) or 0
    dead_code_risk = metrics.get("dead_code_risk", False)

    if cc > 25:
        score += 2
    elif cc > 15:
        score += 1

    if fan_out > 15:
        score += 2
    elif fan_out > 10:
        score += 1

    if churn > 50:
        score += 1

    if dead_code_risk:
        score += 1

    if score >= 3:
        return "poor"
    if score >= 1:
        return "fair"
    return "good"


# ---------------------------------------------------------------------------
# Metric collection — symbol level
# ---------------------------------------------------------------------------


def _swallow(tag: str, exc: BaseException) -> None:
    """Centralised swallowed-exception logger to keep the helpers tight."""
    from roam.observability import log_swallowed

    log_swallowed(tag, exc)


def _populate_symbol_metrics(conn, symbol_id: int, result: dict) -> None:
    """Pull cognitive complexity + LOC + (optional) coverage columns."""
    try:
        sm = conn.execute(
            "SELECT cognitive_complexity, line_count FROM symbol_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if sm:
            result["complexity"] = sm["cognitive_complexity"] or 0
            result["loc"] = sm["line_count"] or 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:metric_query", exc)
    try:
        cov = conn.execute(
            "SELECT coverage_pct, covered_lines, coverable_lines FROM symbol_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if cov:
            result["coverage_pct"] = cov["coverage_pct"]
            result["covered_lines"] = cov["covered_lines"] or 0
            result["coverable_lines"] = cov["coverable_lines"] or 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:metric_query", exc)


def _populate_graph_metrics(conn, symbol_id: int, result: dict) -> None:
    """Pull pagerank/fan-in/fan-out/betweenness + (optional) SNA-v2 cols."""
    try:
        gm = conn.execute(
            "SELECT pagerank, in_degree, out_degree, betweenness FROM graph_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if gm:
            result["pagerank"] = gm["pagerank"] or 0.0
            result["fan_in"] = gm["in_degree"] or 0
            result["fan_out"] = gm["out_degree"] or 0
            result["betweenness"] = gm["betweenness"] or 0.0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:metric_query", exc)
    try:
        extra = conn.execute(
            "SELECT closeness, eigenvector, clustering_coefficient, debt_score FROM graph_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if extra:
            result["closeness"] = extra["closeness"] or 0.0
            result["eigenvector"] = extra["eigenvector"] or 0.0
            result["clustering_coefficient"] = extra["clustering_coefficient"] or 0.0
            result["debt_score"] = extra["debt_score"] or 0.0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:metric_query", exc)


def _populate_edge_fanout_fallback(conn, symbol_id: int, result: dict) -> None:
    """When graph_metrics returned 0/0, fall back to raw edge counts."""
    if result["fan_in"] != 0 or result["fan_out"] != 0:
        return
    try:
        fi = conn.execute("SELECT COUNT(*) FROM edges WHERE target_id = ?", (symbol_id,)).fetchone()
        fo = conn.execute("SELECT COUNT(*) FROM edges WHERE source_id = ?", (symbol_id,)).fetchone()
        result["fan_in"] = fi[0] if fi else 0
        result["fan_out"] = fo[0] if fo else 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:nested_query", exc)


def _collect_file_level_metrics(conn, file_id: int) -> dict:
    """Pull the file-level churn/commits, test-file count, and co-change
    count for a file ONCE.

    These four values are identical for every symbol sharing the file, so
    the bulk file-level collector computes them a single time and reuses
    the result for both the file aggregate and each per-symbol dict (the
    previous per-symbol path recomputed them once per symbol).
    """
    out = {"churn": 0, "commits": 0, "test_files": 0, "co_change_count": 0}
    try:
        fs = conn.execute(
            "SELECT commit_count, total_churn FROM file_stats WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if fs:
            out["commits"] = fs["commit_count"] or 0
            out["churn"] = fs["total_churn"] or 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:nested_query", exc)
    try:
        tf = conn.execute(
            "SELECT COUNT(DISTINCT fe.source_file_id) "
            "FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ? AND f.file_role = 'test'",
            (file_id,),
        ).fetchone()
        out["test_files"] = tf[0] if tf else 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:nested_query", exc)
    try:
        cc_row = conn.execute(
            "SELECT SUM(cochange_count) AS total FROM git_cochange WHERE file_id_a = ? OR file_id_b = ?",
            (file_id, file_id),
        ).fetchone()
        out["co_change_count"] = cc_row["total"] or 0 if cc_row else 0
    except Exception as exc:  # noqa: BLE001 — defensive
        _swallow("cmd_metrics:nested_query", exc)
    return out


def _populate_file_level_metrics(conn, file_id: int, result: dict) -> None:
    """Pull churn/commits, test-file count, and co-change count for a file."""
    fl = _collect_file_level_metrics(conn, file_id)
    result["churn"] = fl["churn"]
    result["commits"] = fl["commits"]
    result["test_files"] = fl["test_files"]
    result["co_change_count"] = fl["co_change_count"]


def _populate_dead_code_risk(sym_row, result: dict) -> None:
    """Mark symbols with zero fan-in as dead-code risks unless they're
    exported (entry points)."""
    if result["fan_in"] != 0:
        return
    kind = sym_row["kind"] or ""
    if kind in ("function", "method", "class") and not sym_row["is_exported"]:
        result["dead_code_risk"] = True


def _default_symbol_metrics() -> dict:
    """Canonical empty per-symbol metrics dict.

    Single source of truth shared by :func:`collect_symbol_metrics` and the
    bulk file-level collector (:func:`_collect_file_symbol_metrics_bulk`) so
    the two paths cannot drift apart in dict shape — callers that spread
    the result (``**sm``) see identical keys either way.
    """
    return {
        "complexity": 0,
        "fan_in": 0,
        "fan_out": 0,
        "pagerank": 0.0,
        "betweenness": 0.0,
        "closeness": 0.0,
        "eigenvector": 0.0,
        "clustering_coefficient": 0.0,
        "debt_score": 0.0,
        "churn": 0,
        "commits": 0,
        "test_files": 0,
        "layer_depth": None,
        "dead_code_risk": False,
        "loc": 0,
        "co_change_count": 0,
        "information_scatter": 0,
        "working_set_size": 0,
        "comprehension_difficulty": 0.0,
        "coverage_pct": None,
        "covered_lines": 0,
        "coverable_lines": 0,
    }


def collect_symbol_metrics(
    conn: sqlite3.Connection,
    symbol_id: int,
    *,
    include_comprehension: bool = True,
) -> dict:
    """Gather all available metrics for a single symbol.

    Returns a flat dict with keys: complexity, fan_in, fan_out, pagerank,
    betweenness, churn, commits, test_files, layer_depth, dead_code_risk,
    loc, co_change_count.
    """
    result = _default_symbol_metrics()

    _populate_symbol_metrics(conn, symbol_id, result)
    _populate_graph_metrics(conn, symbol_id, result)
    _populate_edge_fanout_fallback(conn, symbol_id, result)

    sym_row = conn.execute(
        "SELECT kind, is_exported, file_id FROM symbols WHERE id = ?",
        (symbol_id,),
    ).fetchone()
    if sym_row:
        _populate_dead_code_risk(sym_row, result)
        _populate_file_level_metrics(conn, sym_row["file_id"], result)

        # Comprehension difficulty metrics (#71): information scatter
        # (distinct files in 2-hop closure) + working set size + composite
        # score from fan-out, scatter, working set, complexity.
        if include_comprehension:
            scatter, working_set = _comprehension_neighborhood(conn, symbol_id)
            result["information_scatter"] = scatter
            result["working_set_size"] = working_set
            result["comprehension_difficulty"] = _comprehension_score(
                fan_out=result["fan_out"],
                information_scatter=scatter,
                working_set_size=working_set,
                complexity=result["complexity"],
            )

    return result


def _comprehension_neighborhood(conn: sqlite3.Connection, symbol_id: int, depth: int = 2) -> tuple[int, int]:
    """Compute (information_scatter, working_set_size) in N-hop call neighborhood."""
    visited: set[int] = {symbol_id}
    frontier: set[int] = {symbol_id}

    for _ in range(max(1, depth)):
        if not frontier:
            break
        ids = sorted(frontier)
        # Explore both callers and callees so context reflects read/write surface.
        out_rows = batched_in(
            conn,
            "SELECT target_id FROM edges WHERE source_id IN ({ph})",
            ids,
        )
        in_rows = batched_in(
            conn,
            "SELECT source_id FROM edges WHERE target_id IN ({ph})",
            ids,
        )
        neighbors = {int(r[0]) for r in out_rows if r[0] is not None}
        neighbors.update(int(r[0]) for r in in_rows if r[0] is not None)
        neighbors -= visited
        if not neighbors:
            break
        visited.update(neighbors)
        frontier = neighbors

    if len(visited) <= 1:
        return (0, 0)

    others = sorted(v for v in visited if v != symbol_id)
    file_rows = batched_in(
        conn,
        "SELECT DISTINCT file_id FROM symbols WHERE id IN ({ph})",
        others,
    )
    scatter = len([r for r in file_rows if r[0] is not None])
    working_set = len(others)
    return (scatter, working_set)


def _comprehension_score(*, fan_out: int, information_scatter: int, working_set_size: int, complexity: float) -> float:
    """Composite comprehension difficulty score (0-100)."""
    fan_out_n = min(1.0, max(0.0, float(fan_out) / 12.0))
    scatter_n = min(1.0, max(0.0, float(max(information_scatter - 1, 0)) / 8.0))
    working_n = min(1.0, max(0.0, float(working_set_size) / 30.0))
    complexity_n = min(1.0, max(0.0, float(complexity) / 30.0))
    score = 100.0 * (0.35 * fan_out_n + 0.30 * scatter_n + 0.20 * working_n + 0.15 * complexity_n)
    return round(score, 3)


# ---------------------------------------------------------------------------
# Metric collection — file level
# ---------------------------------------------------------------------------


def _collect_file_symbol_metrics_bulk(
    conn: sqlite3.Connection, file_id: int, sym_rows
) -> tuple[dict[int, dict], dict[str, int]]:
    """Bulk-collect per-symbol metrics for every symbol in a file.

    Replaces the previous N x :func:`collect_symbol_metrics` loop, which
    issued roughly seven SELECTs per symbol (symbol_metrics x2,
    graph_metrics x2, edge-count fallback x2, plus a symbols-row lookup)
    AND recomputed the file's own churn / commits / test_files /
    co_change once per symbol even though those values are identical for
    every symbol sharing the file.

    This joins ``symbol_metrics`` + ``graph_metrics`` in a single pass,
    preloads edge fan-in / fan-out via ``GROUP BY``, derives
    ``dead_code_risk`` inline, and computes the shared file-level metrics
    once. Net queries for an N-symbol file drop from ~7N to a small
    constant (one join + at most two edge GROUP BYs + the file-level set).

    Each entry in the returned ``by_id`` mapping has the SAME shape as
    ``collect_symbol_metrics(..., include_comprehension=False)`` so callers
    that spread the dict (``**sm``) are unaffected. ``file_level`` holds
    the shared churn / commits / test_files / co_change_count.
    """
    by_id: dict[int, dict] = {int(sr["id"]): _default_symbol_metrics() for sr in sym_rows}
    symbol_ids = list(by_id)
    # kind/exported come from the join when available; sym_rows already
    # carries ``kind`` so dead_code_risk still works in the fallback path.
    kind_map: dict[int, str] = {int(sr["id"]): (sr["kind"] or "") for sr in sym_rows}
    exported_map: dict[int, bool] = {}

    joined = True
    try:
        join_rows = batched_in(
            conn,
            "SELECT s.id, s.kind, s.is_exported, "
            "sm.cognitive_complexity, sm.line_count, "
            "sm.coverage_pct, sm.covered_lines, sm.coverable_lines, "
            "gm.pagerank, gm.in_degree, gm.out_degree, gm.betweenness, "
            "gm.closeness, gm.eigenvector, gm.clustering_coefficient, gm.debt_score "
            "FROM symbols s "
            "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "WHERE s.id IN ({ph})",
            symbol_ids,
        )
        for r in join_rows:
            sid = int(r["id"])
            m = by_id[sid]
            m["complexity"] = r["cognitive_complexity"] or 0
            m["loc"] = r["line_count"] or 0
            m["coverage_pct"] = r["coverage_pct"]
            m["covered_lines"] = r["covered_lines"] or 0
            m["coverable_lines"] = r["coverable_lines"] or 0
            m["pagerank"] = r["pagerank"] or 0.0
            m["fan_in"] = r["in_degree"] or 0
            m["fan_out"] = r["out_degree"] or 0
            m["betweenness"] = r["betweenness"] or 0.0
            m["closeness"] = r["closeness"] or 0.0
            m["eigenvector"] = r["eigenvector"] or 0.0
            m["clustering_coefficient"] = r["clustering_coefficient"] or 0.0
            m["debt_score"] = r["debt_score"] or 0.0
            kind_map[sid] = r["kind"] or ""
            exported_map[sid] = bool(r["is_exported"])
    except Exception as exc:  # noqa: BLE001 — older schema may lack a column
        _swallow("cmd_metrics:bulk_metric_join", exc)
        joined = False

    if not joined:
        # Older schema missing one of the joined columns: fall back to the
        # per-symbol path, whose column-group try/except degrades column by
        # column instead of all-or-nothing. Behavior is identical to the
        # pre-bulk implementation.
        for sr in sym_rows:
            sid = int(sr["id"])
            by_id[sid] = collect_symbol_metrics(conn, sid, include_comprehension=False)
    else:
        # Edge-count fallback (bulk) only for symbols graph_metrics scored 0/0,
        # matching _populate_edge_fanout_fallback's early-return guard.
        zero_symbols = [sid for sid in symbol_ids if by_id[sid]["fan_in"] == 0 and by_id[sid]["fan_out"] == 0]
        if zero_symbols:
            try:
                fi_map = {
                    row["target_id"]: row["c"]
                    for row in batched_in(
                        conn,
                        "SELECT target_id, COUNT(*) AS c FROM edges WHERE target_id IN ({ph}) GROUP BY target_id",
                        zero_symbols,
                    )
                    if row["target_id"] is not None
                }
                fo_map = {
                    row["source_id"]: row["c"]
                    for row in batched_in(
                        conn,
                        "SELECT source_id, COUNT(*) AS c FROM edges WHERE source_id IN ({ph}) GROUP BY source_id",
                        zero_symbols,
                    )
                    if row["source_id"] is not None
                }
                for sid in zero_symbols:
                    by_id[sid]["fan_in"] = fi_map.get(sid, 0)
                    by_id[sid]["fan_out"] = fo_map.get(sid, 0)
            except Exception as exc:  # noqa: BLE001 — defensive
                _swallow("cmd_metrics:bulk_edge_fallback", exc)

        # dead_code_risk derived from the FINAL fan_in (post edge fallback).
        for sid in symbol_ids:
            m = by_id[sid]
            if (
                m["fan_in"] == 0
                and kind_map.get(sid, "") in ("function", "method", "class")
                and not exported_map.get(sid, False)
            ):
                m["dead_code_risk"] = True

    # File-level metrics computed ONCE; identical for every symbol in the
    # file, so reuse for both the file aggregate and each per-symbol dict.
    file_level = _collect_file_level_metrics(conn, file_id)
    for m in by_id.values():
        m["churn"] = file_level["churn"]
        m["commits"] = file_level["commits"]
        m["test_files"] = file_level["test_files"]
        m["co_change_count"] = file_level["co_change_count"]

    return by_id, file_level


def collect_file_metrics(conn: sqlite3.Connection, file_id: int) -> dict:
    """Gather aggregate metrics for all symbols in a file.

    Returns a dict with file-level aggregates plus a ``symbols`` list
    with per-symbol breakdown.
    """
    file_row = conn.execute(
        "SELECT id, path, language, line_count, file_role FROM files WHERE id = ?",
        (file_id,),
    ).fetchone()
    if not file_row:
        return {}

    # Symbols in this file. Per-symbol metrics come from the bulk collector
    # below, so this is a plain read with no per-row symbol_metrics join.
    sym_rows = conn.execute(
        "SELECT id, name, kind, qualified_name, line_start, line_end "
        "FROM symbols WHERE file_id = ? ORDER BY line_start",
        (file_id,),
    ).fetchall()

    # Bulk-collect per-symbol metrics + the shared file-level metrics in a
    # small fixed number of queries (the previous loop ran ~7 SELECTs per
    # symbol and recomputed the file-level metrics once per symbol).
    by_id, file_level = _collect_file_symbol_metrics_bulk(conn, file_id, sym_rows)

    # Aggregate per-symbol metrics
    symbol_metrics_list = []
    total_complexity = 0.0
    total_fan_in = 0
    total_fan_out = 0
    max_pagerank = 0.0
    dead_count = 0

    for sr in sym_rows:
        sm = by_id[int(sr["id"])]
        total_complexity += sm["complexity"]
        total_fan_in += sm["fan_in"]
        total_fan_out += sm["fan_out"]
        max_pagerank = max(max_pagerank, sm["pagerank"])
        if sm["dead_code_risk"]:
            dead_count += 1
        symbol_metrics_list.append(
            {
                "name": sr["name"],
                "kind": sr["kind"],
                "qualified_name": sr["qualified_name"],
                "line_start": sr["line_start"],
                "line_end": sr["line_end"],
                **sm,
            }
        )

    # File-level coverage from file_stats (distinct from per-symbol coverage,
    # which the bulk collector reads from symbol_metrics). The churn / commits
    # / test_files / co_change aggregates are the shared file_level values.
    coverage_pct = None
    covered_lines = 0
    coverable_lines = 0
    cov = conn.execute(
        "SELECT coverage_pct, covered_lines, coverable_lines FROM file_stats WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    if cov:
        coverage_pct = cov["coverage_pct"]
        covered_lines = cov["covered_lines"] or 0
        coverable_lines = cov["coverable_lines"] or 0

    file_metrics = {
        "complexity": round(total_complexity, 1),
        "fan_in": total_fan_in,
        "fan_out": total_fan_out,
        "max_pagerank": round(max_pagerank, 6),
        "churn": file_level["churn"],
        "commits": file_level["commits"],
        "test_files": file_level["test_files"],
        "dead_symbols": dead_count,
        "loc": file_row["line_count"] or 0,
        "symbol_count": len(sym_rows),
        "co_change_count": file_level["co_change_count"],
        "coverage_pct": coverage_pct,
        "covered_lines": covered_lines,
        "coverable_lines": coverable_lines,
    }

    return {
        "file": file_row["path"],
        "language": file_row["language"],
        "file_role": file_row["file_role"],
        "metrics": file_metrics,
        "symbols": symbol_metrics_list,
    }


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_target(conn: sqlite3.Connection, target: str) -> tuple[str, int | None, dict | None, str | None]:
    """Determine if target is a file or symbol and return (type, id, row, tier).

    Pattern-1 Variant D Wave C (audit reference:
    ``(internal memo)``). Delegates the file-path
    branch to :func:`roam.commands.resolve.resolve_file_symbols` so the
    silent LIKE %name% substring fallback is surfaced via a tier
    discriminator (``"file"`` vs ``"file_substring"``) rather than emitted
    with ``resolution: null`` on the success envelope — the most severe of
    the audit's three HIGH-severity Variant D candidates.

    Returns:
        ``("file", file_id, file_row, "file"|"file_substring")`` on file
        resolution, ``("symbol", symbol_id, symbol_row, None)`` on symbol
        resolution (caller reads ``row["_resolution_tier"]`` from
        ``find_symbol``'s stamp for the symbol/fuzzy tier), or
        ``("unknown", None, None, None)`` on miss.
    """
    # Try file path first via the shared substrate. Returns a tier
    # discriminator so the callers can disclose substring fallback.
    file_id, _sym_ids, file_path, tier = resolve_file_symbols(conn, target)
    if file_id is not None:
        # Return a row-shaped mapping consistent with the legacy
        # ``SELECT id, path`` shape so downstream code stays unchanged.
        return ("file", file_id, {"id": file_id, "path": file_path}, tier)

    # Try symbol lookup
    sym = find_symbol(conn, target)
    if sym:
        return ("symbol", sym["id"], sym, None)

    return ("unknown", None, None, None)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="metrics",
    category="exploration",
    summary="Show unified metrics for a file or symbol",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("metrics")
@click.argument("target")
@click.pass_context
def metrics(ctx, target):
    """Show unified metrics for a file or symbol.

    Unlike ``health`` (which scores the entire codebase), this command
    shows detailed metrics for a single file or symbol.

    TARGET can be a file path (e.g. src/app.py) or a symbol name
    (e.g. create_user). Consolidates complexity, fan-in/fan-out,
    SNA centrality vector (PageRank/betweenness/closeness/eigenvector/
    clustering coefficient), composite debt score, churn, test coverage,
    and comprehension difficulty into one view.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        target_type, target_id, target_row, file_tier = _resolve_target(conn, target)

        if target_type == "unknown":
            msg = f'Target not found: "{target}"'
            if json_mode:
                # W1245 Pattern-2 variant-D: structured unresolved
                # envelope so MCP consumers see the same shape as the
                # resolved branches.
                unresolved_disclosure = resolution_disclosure("unresolved", target=target)
                click.echo(
                    to_json(
                        json_envelope(
                            "metrics",
                            summary={
                                "verdict": "not found",
                                "target": target,
                                "state": "not_found",
                                **unresolved_disclosure,
                            },
                            error=msg,
                            **unresolved_disclosure,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: not found -- {msg}")
                click.echo(
                    "  Tip: Use a file path or symbol name. Run `roam search {}` to find symbols.".format(target)
                )
            raise SystemExit(1)

        if target_type == "file":
            # W1245 Pattern-2 variant-D: file target succeeds through the
            # file-path tier; surface as ``resolution=file`` so consumers
            # can distinguish file vs symbol resolution paths.
            # Pattern-1 Variant D Wave C: thread ``file_tier`` through so
            # the substring-LIKE-fallback (``"file_substring"``) success
            # path is distinguishable from an exact-match (``"file"``)
            # success — pre-Wave-C this branch emitted ``resolution: null``.
            _output_file_metrics(conn, target_id, target, json_mode, token_budget, file_tier or "file")
        else:
            # W1245 \ W1249 Pattern-2 variant-D: ``find_symbol`` stamps
            # ``_resolution_tier`` (symbol|fuzzy) on the returned row.
            resolution_tier = target_row.get("_resolution_tier", "symbol") if target_row else "symbol"
            _output_symbol_metrics(conn, target_id, target, json_mode, token_budget, resolution_tier)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _output_symbol_metrics(conn, symbol_id, target, json_mode, budget, resolution_tier="symbol"):
    """Produce output for a symbol target.

    W1245 Pattern-2 variant-D: ``resolution_tier`` ("symbol"|"fuzzy") tells
    us whether the input string exact-matched a symbol or fell back to a
    LIKE substring match -- agents must see the degradation on the
    success envelope, not just an opaque verdict.
    """
    sym_row = conn.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, "
        "s.line_end, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.id = ?",
        (symbol_id,),
    ).fetchone()

    if not sym_row:
        click.echo(f"Symbol not found: {target}")
        raise SystemExit(1)

    sm = collect_symbol_metrics(conn, symbol_id)
    health = _health_label(sm)

    display_name = sym_row["qualified_name"] or sym_row["name"]
    location = loc(sym_row["file_path"], sym_row["line_start"])

    if json_mode:
        verdict = f"{display_name}: health={health}"
        if resolution_tier == "fuzzy":
            verdict = f"{verdict} [fuzzy resolution]"
        disclosure = resolution_disclosure(resolution_tier, target=display_name)
        click.echo(
            to_json(
                json_envelope(
                    "metrics",
                    budget=budget,
                    summary={
                        "verdict": verdict,
                        "target": display_name,
                        "target_type": "symbol",
                        "health": health,
                        "caller_metric_definition": "direct_in_degree (fan_in from graph_metrics.in_degree, raw_edge_rows fallback)",
                        # W1298 Pattern-3a: disclose the precise complexity
                        # scorer that produced ``metrics.complexity`` so this
                        # envelope cannot drift from cmd_complexity's reading.
                        "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                        **disclosure,
                    },
                    target_type="symbol",
                    name=display_name,
                    kind=sym_row["kind"],
                    location=location,
                    metrics=sm,
                    **disclosure,
                )
            )
        )
        return

    click.echo(f"VERDICT: {display_name}: health={health}")
    click.echo(f"  type: {abbrev_kind(sym_row['kind'])}  location: {location}")
    click.echo()
    click.echo("  Metrics:")
    for key, val in sm.items():
        label = key.replace("_", " ")
        click.echo(f"    {label:<20s} {val}")


def _output_file_metrics(conn, file_id, target, json_mode, budget, resolution_tier="file"):
    """Produce output for a file target.

    Pattern-1 Variant D Wave C: ``resolution_tier`` ("file"|"file_substring")
    tells us whether the input exact-matched a file path or fell back to a
    LIKE %name% substring match. Pre-Wave-C this path emitted
    ``resolution: null`` on the success envelope — agents had no signal
    that the file resolution was degraded.
    """
    data = collect_file_metrics(conn, file_id)
    if not data:
        click.echo(f"File not found: {target}")
        raise SystemExit(1)

    fm = data["metrics"]
    # Compute health for file level
    file_health = _health_label(
        {
            "complexity": fm["complexity"],
            "fan_out": fm["fan_out"],
            "churn": fm["churn"],
            "dead_code_risk": fm["dead_symbols"] > 0,
        }
    )

    if json_mode:
        verdict = f"{data['file']}: health={file_health}"
        # Pattern-1 Variant D Wave C: suffix the verdict when the file
        # target resolved through a substring-LIKE fallback so LAW-6
        # single-line consumers still see the disclosure.
        if resolution_tier == "file_substring":
            verdict = f"{verdict} [file substring match]"
        disclosure = resolution_disclosure(resolution_tier, target=data["file"])
        click.echo(
            to_json(
                json_envelope(
                    "metrics",
                    budget=budget,
                    summary={
                        "verdict": verdict,
                        "target": data["file"],
                        "target_type": "file",
                        "health": file_health,
                        "symbol_count": fm["symbol_count"],
                        "caller_metric_definition": "direct_in_degree (per-symbol fan_in summed across file)",
                        # W1298 Pattern-3a: per-symbol ``complexity`` and the
                        # file-level ``metrics.complexity`` aggregate are both
                        # sums of cognitive_complexity from symbol_metrics.
                        "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                        # Pattern-1 Variant D Wave C resolution disclosure.
                        # Filter helper ``target`` to avoid clobbering the
                        # explicit ``target=data["file"]`` kwarg already in
                        # the summary above.
                        **{k: v for k, v in disclosure.items() if k != "target"},
                    },
                    target_type="file",
                    file=data["file"],
                    language=data["language"],
                    file_role=data["file_role"],
                    metrics=fm,
                    # Pattern-1 Variant D Wave C resolution disclosure at the
                    # top level so the LAW-6 single-line consumer contract
                    # is satisfied. ``target`` filtered to avoid collision
                    # with the explicit ``file=data["file"]`` kwarg.
                    **{k: v for k, v in disclosure.items() if k != "target"},
                    symbols=[
                        {
                            "name": s["name"],
                            "kind": s["kind"],
                            "line_start": s["line_start"],
                            "complexity": s["complexity"],
                            "fan_in": s["fan_in"],
                            "fan_out": s["fan_out"],
                            "pagerank": round(s["pagerank"], 6),
                            "closeness": round(s["closeness"], 6),
                            "eigenvector": round(s["eigenvector"], 6),
                            "clustering_coefficient": round(s["clustering_coefficient"], 6),
                            "debt_score": round(s["debt_score"], 3),
                            "dead_code_risk": s["dead_code_risk"],
                            "loc": s["loc"],
                            "coverage_pct": s["coverage_pct"],
                            "covered_lines": s["covered_lines"],
                            "coverable_lines": s["coverable_lines"],
                        }
                        for s in data["symbols"]
                    ],
                )
            )
        )
        return

    text_verdict = f"{data['file']}: health={file_health}"
    if resolution_tier == "file_substring":
        text_verdict = f"{text_verdict} [file substring match]"
    click.echo(f"VERDICT: {text_verdict}")
    click.echo(f"  language: {data['language'] or 'unknown'}  role: {data['file_role']}")
    click.echo()
    click.echo("  File Metrics:")
    for key, val in fm.items():
        label = key.replace("_", " ")
        click.echo(f"    {label:<20s} {val}")

    if data["symbols"]:
        click.echo()
        click.echo("  Symbol Breakdown:")
        rows = []
        for s in data["symbols"]:
            rows.append(
                [
                    abbrev_kind(s["kind"]),
                    s["name"],
                    str(s["complexity"]),
                    str(s["fan_in"]),
                    str(s["fan_out"]),
                    f"{s['pagerank']:.4f}",
                    "Y" if s["dead_code_risk"] else "",
                ]
            )
        table = format_table(
            ["Kind", "Name", "CC", "In", "Out", "PageRank", "Dead?"],
            rows,
        )
        for line in table.splitlines():
            click.echo(f"    {line}")
