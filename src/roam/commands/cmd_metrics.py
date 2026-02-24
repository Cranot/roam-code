"""Unified per-file and per-symbol metrics command.

Consolidates complexity, fan-in/fan-out, PageRank, churn, test coverage,
layer depth, dead-code risk, LOC, and co-change data into a single
structured output.  All data is read from the existing SQLite index.
"""

from __future__ import annotations

import os
import sqlite3

import click

from roam.db.connection import open_db, batched_in
from roam.output.formatter import (
    to_json,
    json_envelope,
    abbrev_kind,
    loc,
    format_table,
)
from roam.commands.resolve import ensure_index, find_symbol


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
    fan_in = metrics.get("fan_in", 0) or 0
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
    result: dict = {
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

    # -- symbol_metrics (cognitive complexity, line_count) --
    try:
        sm = conn.execute(
            "SELECT cognitive_complexity, line_count "
            "FROM symbol_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if sm:
            result["complexity"] = sm["cognitive_complexity"] or 0
            result["loc"] = sm["line_count"] or 0
    except Exception:
        pass
    # Optional imported coverage columns (safe on older DB schemas)
    try:
        cov = conn.execute(
            "SELECT coverage_pct, covered_lines, coverable_lines "
            "FROM symbol_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if cov:
            result["coverage_pct"] = cov["coverage_pct"]
            result["covered_lines"] = cov["covered_lines"] or 0
            result["coverable_lines"] = cov["coverable_lines"] or 0
    except Exception:
        pass

    # -- graph_metrics (pagerank, in_degree, out_degree, betweenness) --
    try:
        gm = conn.execute(
            "SELECT pagerank, in_degree, out_degree, betweenness "
            "FROM graph_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if gm:
            result["pagerank"] = gm["pagerank"] or 0.0
            result["fan_in"] = gm["in_degree"] or 0
            result["fan_out"] = gm["out_degree"] or 0
            result["betweenness"] = gm["betweenness"] or 0.0
    except Exception:
        pass
    # Optional SNA v2 columns (safe on older DB schemas)
    try:
        extra = conn.execute(
            "SELECT closeness, eigenvector, clustering_coefficient, debt_score "
            "FROM graph_metrics WHERE symbol_id = ?",
            (symbol_id,),
        ).fetchone()
        if extra:
            result["closeness"] = extra["closeness"] or 0.0
            result["eigenvector"] = extra["eigenvector"] or 0.0
            result["clustering_coefficient"] = extra["clustering_coefficient"] or 0.0
            result["debt_score"] = extra["debt_score"] or 0.0
    except Exception:
        pass

    # -- edges (fallback fan-in / fan-out from raw edges) --
    if result["fan_in"] == 0 and result["fan_out"] == 0:
        try:
            fi = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE target_id = ?",
                (symbol_id,),
            ).fetchone()
            fo = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE source_id = ?",
                (symbol_id,),
            ).fetchone()
            result["fan_in"] = fi[0] if fi else 0
            result["fan_out"] = fo[0] if fo else 0
        except Exception:
            pass

    # -- dead_code_risk: fan_in == 0 for non-entry-point symbols --
    sym_row = conn.execute(
        "SELECT kind, is_exported, file_id FROM symbols WHERE id = ?",
        (symbol_id,),
    ).fetchone()
    if sym_row:
        kind = sym_row["kind"] or ""
        is_exported = sym_row["is_exported"]
        if result["fan_in"] == 0 and kind in ("function", "method", "class"):
            # Entry points (exported, main, __init__) are not dead code
            if not is_exported:
                result["dead_code_risk"] = True

        # -- churn / commits from git_file_stats via file_id --
        file_id = sym_row["file_id"]
        try:
            fs = conn.execute(
                "SELECT commit_count, total_churn FROM file_stats "
                "WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if fs:
                result["commits"] = fs["commit_count"] or 0
                result["churn"] = fs["total_churn"] or 0
        except Exception:
            pass

        # -- test files: count files with file_role='test' that reference
        #    the same file via file_edges --
        try:
            tf = conn.execute(
                "SELECT COUNT(DISTINCT fe.source_file_id) "
                "FROM file_edges fe "
                "JOIN files f ON fe.source_file_id = f.id "
                "WHERE fe.target_file_id = ? AND f.file_role = 'test'",
                (file_id,),
            ).fetchone()
            result["test_files"] = tf[0] if tf else 0
        except Exception:
            pass

        # -- co_change_count --
        try:
            cc_row = conn.execute(
                "SELECT SUM(cochange_count) AS total "
                "FROM git_cochange WHERE file_id_a = ? OR file_id_b = ?",
                (file_id, file_id),
            ).fetchone()
            result["co_change_count"] = cc_row["total"] or 0 if cc_row else 0
        except Exception:
            pass

        # Comprehension difficulty metrics (#71):
        # - information scatter: distinct files in 2-hop closure
        # - working set size: symbols in 2-hop closure
        # - composite score from fan-out, scatter, working set, complexity
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
    score = 100.0 * (
        0.35 * fan_out_n
        + 0.30 * scatter_n
        + 0.20 * working_n
        + 0.15 * complexity_n
    )
    return round(score, 3)


# ---------------------------------------------------------------------------
# Metric collection — file level
# ---------------------------------------------------------------------------

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

    # Gather symbols in this file
    sym_rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, s.line_end, "
        "COALESCE(sm.cognitive_complexity, 0) AS cognitive_complexity "
        "FROM symbols s "
        "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
        "WHERE s.file_id = ? ORDER BY s.line_start",
        (file_id,),
    ).fetchall()

    # Per-symbol metrics
    symbol_metrics_list = []
    total_complexity = 0.0
    total_fan_in = 0
    total_fan_out = 0
    max_pagerank = 0.0
    dead_count = 0

    for sr in sym_rows:
        sm = collect_symbol_metrics(conn, sr["id"], include_comprehension=False)
        total_complexity += sm["complexity"]
        total_fan_in += sm["fan_in"]
        total_fan_out += sm["fan_out"]
        max_pagerank = max(max_pagerank, sm["pagerank"])
        if sm["dead_code_risk"]:
            dead_count += 1
        symbol_metrics_list.append({
            "name": sr["name"],
            "kind": sr["kind"],
            "qualified_name": sr["qualified_name"],
            "line_start": sr["line_start"],
            "line_end": sr["line_end"],
            **sm,
        })

    # File-level churn / commits
    churn = 0
    commits = 0
    coverage_pct = None
    covered_lines = 0
    coverable_lines = 0
    try:
        fs = conn.execute(
            "SELECT commit_count, total_churn FROM file_stats WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if fs:
            commits = fs["commit_count"] or 0
            churn = fs["total_churn"] or 0
    except Exception:
        pass
    try:
        cov = conn.execute(
            "SELECT coverage_pct, covered_lines, coverable_lines "
            "FROM file_stats WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if cov:
            coverage_pct = cov["coverage_pct"]
            covered_lines = cov["covered_lines"] or 0
            coverable_lines = cov["coverable_lines"] or 0
    except Exception:
        pass

    # Test files referencing this file
    test_files = 0
    try:
        tf = conn.execute(
            "SELECT COUNT(DISTINCT fe.source_file_id) "
            "FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id = ? AND f.file_role = 'test'",
            (file_id,),
        ).fetchone()
        test_files = tf[0] if tf else 0
    except Exception:
        pass

    # Co-change count
    co_change = 0
    try:
        cc_row = conn.execute(
            "SELECT SUM(cochange_count) AS total "
            "FROM git_cochange WHERE file_id_a = ? OR file_id_b = ?",
            (file_id, file_id),
        ).fetchone()
        co_change = cc_row["total"] or 0 if cc_row else 0
    except Exception:
        pass

    file_metrics = {
        "complexity": round(total_complexity, 1),
        "fan_in": total_fan_in,
        "fan_out": total_fan_out,
        "max_pagerank": round(max_pagerank, 6),
        "churn": churn,
        "commits": commits,
        "test_files": test_files,
        "dead_symbols": dead_count,
        "loc": file_row["line_count"] or 0,
        "symbol_count": len(sym_rows),
        "co_change_count": co_change,
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

def _resolve_target(conn: sqlite3.Connection, target: str) -> tuple[str, int | None, dict | None]:
    """Determine if target is a file or symbol and return (type, id, row).

    Returns:
        ("file", file_id, file_row) or ("symbol", symbol_id, symbol_row)
        or ("unknown", None, None)
    """
    # Try file path first (exact match)
    norm = target.replace("\\", "/")
    row = conn.execute(
        "SELECT id, path FROM files WHERE path = ?",
        (norm,),
    ).fetchone()
    if row:
        return ("file", row["id"], row)

    # Try partial file path match
    row = conn.execute(
        "SELECT id, path FROM files WHERE path LIKE ? ORDER BY path LIMIT 1",
        (f"%{norm}%",),
    ).fetchone()
    if row:
        return ("file", row["id"], row)

    # Try symbol lookup
    sym = find_symbol(conn, target)
    if sym:
        return ("symbol", sym["id"], sym)

    return ("unknown", None, None)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("metrics")
@click.argument("target")
@click.pass_context
def metrics(ctx, target):
    """Show unified metrics for a file or symbol.

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
        target_type, target_id, target_row = _resolve_target(conn, target)

        if target_type == "unknown":
            msg = f'Target not found: "{target}"'
            if json_mode:
                click.echo(to_json(json_envelope(
                    "metrics",
                    summary={"verdict": "not found", "target": target},
                    error=msg,
                )))
            else:
                click.echo(f"VERDICT: not found -- {msg}")
                click.echo(
                    "  Tip: Use a file path or symbol name. "
                    "Run `roam search {}` to find symbols.".format(target)
                )
            raise SystemExit(1)

        if target_type == "file":
            _output_file_metrics(conn, target_id, target, json_mode, token_budget)
        else:
            _output_symbol_metrics(conn, target_id, target, json_mode, token_budget)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _output_symbol_metrics(conn, symbol_id, target, json_mode, budget):
    """Produce output for a symbol target."""
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
        click.echo(to_json(json_envelope(
            "metrics",
            budget=budget,
            summary={
                "verdict": f"{display_name}: health={health}",
                "target": display_name,
                "target_type": "symbol",
                "health": health,
            },
            target_type="symbol",
            name=display_name,
            kind=sym_row["kind"],
            location=location,
            metrics=sm,
        )))
        return

    click.echo(f"VERDICT: {display_name}: health={health}")
    click.echo(f"  type: {abbrev_kind(sym_row['kind'])}  location: {location}")
    click.echo()
    click.echo("  Metrics:")
    for key, val in sm.items():
        label = key.replace("_", " ")
        click.echo(f"    {label:<20s} {val}")


def _output_file_metrics(conn, file_id, target, json_mode, budget):
    """Produce output for a file target."""
    data = collect_file_metrics(conn, file_id)
    if not data:
        click.echo(f"File not found: {target}")
        raise SystemExit(1)

    fm = data["metrics"]
    # Compute health for file level
    file_health = _health_label({
        "complexity": fm["complexity"],
        "fan_out": fm["fan_out"],
        "churn": fm["churn"],
        "dead_code_risk": fm["dead_symbols"] > 0,
    })

    if json_mode:
        click.echo(to_json(json_envelope(
            "metrics",
            budget=budget,
            summary={
                "verdict": f"{data['file']}: health={file_health}",
                "target": data["file"],
                "target_type": "file",
                "health": file_health,
                "symbol_count": fm["symbol_count"],
            },
            target_type="file",
            file=data["file"],
            language=data["language"],
            file_role=data["file_role"],
            metrics=fm,
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
        )))
        return

    click.echo(f"VERDICT: {data['file']}: health={file_health}")
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
            rows.append([
                abbrev_kind(s["kind"]),
                s["name"],
                str(s["complexity"]),
                str(s["fan_in"]),
                str(s["fan_out"]),
                f"{s['pagerank']:.4f}",
                "Y" if s["dead_code_risk"] else "",
            ])
        table = format_table(
            ["Kind", "Name", "CC", "In", "Out", "PageRank", "Dead?"],
            rows,
        )
        for line in table.splitlines():
            click.echo(f"    {line}")
