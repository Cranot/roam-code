"""Unified single-screen codebase status dashboard.

Combines health, hotspots, bus factor, dead symbols, and AI rot (vibe-check)
into a single concise view.  Queries the DB directly for speed -- no shelling
out to other commands.
"""

from __future__ import annotations

import time

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Lightweight data collection helpers
# ---------------------------------------------------------------------------

def _overview(conn):
    """Basic project stats: files, symbols, edges, clusters, languages."""
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    try:
        cluster_count = conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM clusters"
        ).fetchone()[0]
    except Exception:
        cluster_count = 0

    lang_rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files "
        "WHERE language IS NOT NULL "
        "GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    languages = []
    for r in lang_rows:
        pct = round(r["cnt"] * 100 / files, 1) if files else 0
        languages.append({"name": r["language"], "files": r["cnt"], "pct": pct})

    # Index age
    try:
        from roam.db.connection import get_db_path
        db_path = get_db_path()
        if db_path.exists():
            index_age_s = int(time.time() - db_path.stat().st_mtime)
        else:
            index_age_s = None
    except Exception:
        index_age_s = None

    return {
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "clusters": cluster_count,
        "languages": languages,
        "index_age_s": index_age_s,
    }


def _format_age(seconds):
    """Format seconds into a human-readable relative string."""
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _top_hotspots(conn, limit=5):
    """Top files by churn * complexity, annotated with bus factor."""
    rows = conn.execute(
        "SELECT fs.file_id, f.path, fs.total_churn, fs.complexity, "
        "fs.commit_count, fs.distinct_authors "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit * 2,),  # over-fetch to filter tests
    ).fetchall()

    results = []
    for r in rows:
        path = r["path"]
        # skip test files
        base = path.replace("\\", "/").split("/")[-1].lower()
        if base.startswith("test_") or base.endswith("_test.py"):
            continue

        # Bus factor: count distinct authors for this file
        authors = r["distinct_authors"] or 1

        results.append({
            "path": path,
            "churn": r["total_churn"] or 0,
            "complexity": round(r["complexity"] or 0, 0),
            "bus_factor": authors,
        })
        if len(results) >= limit:
            break

    return results


def _risk_areas(conn):
    """Compute key risk indicators from DB."""
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 1

    # Bus factor 1 files (files with only 1 distinct author)
    try:
        bf1_count = conn.execute(
            "SELECT COUNT(*) FROM file_stats WHERE distinct_authors = 1"
        ).fetchone()[0]
    except Exception:
        bf1_count = 0
    bf1_pct = round(bf1_count * 100 / total_files, 1)

    # Dead symbols (high confidence only -- exported symbols with no callers)
    try:
        from roam.db.queries import UNREFERENCED_EXPORTS
        dead_rows = conn.execute(UNREFERENCED_EXPORTS).fetchall()
        # Filter test files
        dead_count = sum(
            1 for r in dead_rows
            if not r["file_path"].replace("\\", "/").split("/")[-1].lower().startswith("test_")
            and not r["file_path"].replace("\\", "/").split("/")[-1].lower().endswith("_test.py")
        )
    except Exception:
        dead_count = 0

    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 1
    dead_pct = round(dead_count * 100 / total_symbols, 1)

    # Cycles (SCCs)
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
        G = build_symbol_graph(conn)
        cycles = find_cycles(G)
        cycle_count = len(cycles)
    except Exception:
        cycle_count = 0

    return {
        "bus_factor_1_files": bf1_count,
        "bus_factor_1_pct": bf1_pct,
        "dead_symbols": dead_count,
        "dead_pct": dead_pct,
        "cycles": cycle_count,
        "total_files": total_files,
    }


def _vibe_check_fast(conn):
    """Lightweight vibe-check using DB-only detectors (no file I/O).

    Computes a rough AI rot score from the two DB-only patterns
    (dead exports, hallucinated imports) and returns the score plus
    top category counts.  Falls back gracefully if data is missing.
    """
    try:
        from roam.commands.cmd_vibe_check import (
            _detect_dead_exports,
            _detect_hallucinated_imports,
            _severity_label,
        )
    except ImportError:
        return None

    try:
        p1_found, p1_total = _detect_dead_exports(conn)
        p5_found, p5_total, _ = _detect_hallucinated_imports(conn)
    except Exception:
        return None

    # Simple approximation of the full score using only DB patterns
    def _rate(found, total):
        return round(found / max(total, 1) * 100, 1)

    rate_dead = _rate(p1_found, p1_total)
    rate_halluc = _rate(p5_found, p5_total)

    # Weighted average (simplified from full 8-pattern score)
    approx_score = min(100, int(round((rate_dead * 15 + rate_halluc * 15) / 30)))
    severity = _severity_label(approx_score)
    total_issues = p1_found + p5_found

    categories = []
    if p1_found > 0:
        categories.append({"name": "Dead exports", "count": p1_found})
    if p5_found > 0:
        categories.append({"name": "Hallucinated imports", "count": p5_found})

    return {
        "score": approx_score,
        "severity": severity,
        "total_issues": total_issues,
        "categories": categories,
        "approximate": True,
    }


def _health_label(score):
    """Map health score to a label."""
    if score >= 80:
        return "HEALTHY"
    elif score >= 60:
        return "FAIR"
    elif score >= 40:
        return "NEEDS ATTENTION"
    else:
        return "UNHEALTHY"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("dashboard")
@click.pass_context
def dashboard(ctx):
    """Unified codebase status: health, hotspots, debt, bus factor, AI rot."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        # -- Overview --
        overview = _overview(conn)

        # -- Health (reuse collect_metrics for consistency with health cmd) --
        from roam.commands.metrics_history import collect_metrics
        health = collect_metrics(conn)

        # -- Top hotspots --
        hotspots = _top_hotspots(conn)

        # -- Risk areas --
        risks = _risk_areas(conn)

        # -- Vibe-check (fast, DB-only) --
        vibe = _vibe_check_fast(conn)

        # -- Build verdict --
        hs = health["health_score"]
        h_label = _health_label(hs)
        vibe_part = ""
        if vibe is not None:
            vibe_part = f", AI rot {vibe['score']}/100"
        verdict = f"Codebase is {h_label} (health {hs}/100{vibe_part})"

        # -- JSON output --
        if json_mode:
            envelope = json_envelope("dashboard",
                budget=budget,
                summary={
                    "verdict": verdict,
                    "health_score": hs,
                    "files": overview["files"],
                    "symbols": overview["symbols"],
                    "edges": overview["edges"],
                },
                overview=overview,
                health={
                    "score": hs,
                    "label": h_label,
                    "tangle_ratio": health.get("tangle_ratio", 0),
                    "cycles": health.get("cycles", 0),
                    "god_components": health.get("god_components", 0),
                    "bottlenecks": health.get("bottlenecks", 0),
                    "dead_exports": health.get("dead_exports", 0),
                    "layer_violations": health.get("layer_violations", 0),
                    "avg_complexity": health.get("avg_complexity", 0),
                },
                hotspots=[
                    {
                        "path": h["path"],
                        "churn": h["churn"],
                        "complexity": h["complexity"],
                        "bus_factor": h["bus_factor"],
                    }
                    for h in hotspots
                ],
                risks=risks,
                vibe_check=vibe,
            )
            click.echo(to_json(envelope))
            return

        # -- Text output (<40 lines) --
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # === Overview ===
        lang_parts = []
        for lang in overview["languages"][:4]:
            lang_parts.append(f"{lang['name']} {lang['pct']:.0f}%")
        if len(overview["languages"]) > 4:
            lang_parts.append(f"+{len(overview['languages']) - 4} more")
        lang_str = ", ".join(lang_parts) if lang_parts else "none"

        click.echo("  === Overview ===")
        click.echo(f"  Files: {overview['files']} ({lang_str})")
        click.echo(f"  Symbols: {overview['symbols']} | Edges: {overview['edges']}"
                   f" | Clusters: {overview['clusters']}")
        click.echo(f"  Last indexed: {_format_age(overview['index_age_s'])}")
        click.echo()

        # === Health ===
        click.echo("  === Health ===")
        click.echo(f"  Score: {hs}/100 ({h_label})")
        click.echo(f"  Tangle ratio: {health.get('tangle_ratio', 0)}"
                   f" | Avg complexity: {health.get('avg_complexity', 0)}"
                   f" | Dead symbols: {risks['dead_symbols']}")
        click.echo()

        # === Top Hotspots ===
        if hotspots:
            click.echo("  === Top Hotspots (change with care) ===")
            for i, h in enumerate(hotspots, 1):
                click.echo(
                    f"  {i}. {h['path']:<40s}"
                    f" churn:{h['churn']:<5d}"
                    f" complexity:{int(h['complexity']):<4d}"
                    f" bus-factor:{h['bus_factor']}"
                )
            click.echo()

        # === Risk Areas ===
        click.echo("  === Risk Areas ===")
        click.echo(f"  Bus factor 1: {risks['bus_factor_1_files']} files"
                   f" ({risks['bus_factor_1_pct']}%)")
        click.echo(f"  Dead symbols: {risks['dead_symbols']}"
                   f" ({risks['dead_pct']}%)")
        click.echo(f"  Cycles: {risks['cycles']} SCCs")
        click.echo()

        # === AI Rot ===
        if vibe is not None and vibe["total_issues"] > 0:
            click.echo("  === AI Rot (vibe-check) ===")
            cat_parts = []
            for cat in vibe["categories"]:
                cat_parts.append(f"{cat['name']} ({cat['count']})")
            cats_str = ", ".join(cat_parts) if cat_parts else "none"
            approx_note = " (approximate)" if vibe.get("approximate") else ""
            click.echo(f"  Score: {vibe['score']}/100 ({vibe['severity']})"
                       f"{approx_note}"
                       f" | {vibe['total_issues']} issues")
            click.echo(f"  Top: {cats_str}")
            click.echo()

        click.echo("  Run `roam health`, `roam hotspot`, `roam vibe-check` for details.")
