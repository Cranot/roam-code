"""Hotspot-weighted technical debt prioritization.

Core insight (CodeScene): unhealthy code in hotspots (frequently changed files)
costs 15x more than unhealthy cold code.  debt_score = health_penalty * hotspot_factor.
"""

import os
from collections import defaultdict

import click

from roam.db.connection import open_db, batched_in
from roam.output.formatter import loc, format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile_rank(value, sorted_values):
    """Return the percentile rank (0.0-1.0) of *value* within *sorted_values*."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    # Count how many values are strictly less than *value*
    lo = 0
    for v in sorted_values:
        if v < value:
            lo += 1
        else:
            break
    return lo / n


def _parent_dir(path):
    """Return the parent directory of a file path, normalised with forward slashes."""
    p = path.replace("\\", "/")
    idx = p.rfind("/")
    return p[:idx] if idx >= 0 else "."


# ---------------------------------------------------------------------------
# Per-file debt computation
# ---------------------------------------------------------------------------

def _compute_file_debt(conn):
    """Compute per-file debt scores.

    Returns a list of dicts sorted by debt_score descending.
    """
    # 1. Fetch file_stats rows (complexity, churn)
    file_stats = conn.execute("""
        SELECT fs.file_id, f.path, fs.complexity, fs.total_churn,
               fs.commit_count, fs.distinct_authors
        FROM file_stats fs
        JOIN files f ON fs.file_id = f.id
    """).fetchall()

    if not file_stats:
        return []

    # Build lookup: file_id -> file_stats row
    stats_by_file = {}
    for r in file_stats:
        stats_by_file[r["file_id"]] = dict(r)

    # --- Normalise complexity (0-1) ---
    complexities = [r["complexity"] or 0 for r in file_stats]
    max_complexity = max(complexities) if complexities else 1
    if max_complexity == 0:
        max_complexity = 1

    # --- Churn percentile ranks ---
    churns = sorted(r["total_churn"] or 0 for r in file_stats)

    # 2. Cycle membership: find which files have symbols participating in cycles
    cycle_files = set()
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        cycles = find_cycles(G)
        if cycles:
            all_cycle_ids = set()
            for scc in cycles:
                all_cycle_ids.update(scc)
            if all_cycle_ids:
                rows = batched_in(
                    conn,
                    "SELECT DISTINCT s.file_id FROM symbols s WHERE s.id IN ({ph})",
                    list(all_cycle_ids),
                )
                cycle_files = {r["file_id"] for r in rows}
    except Exception:
        pass  # graph not available — skip cycle detection

    # 3. God component membership (high fan-in + fan-out per file)
    god_files = set()
    god_rows = conn.execute("""
        SELECT s.file_id, SUM(gm.in_degree + gm.out_degree) as total_degree
        FROM graph_metrics gm
        JOIN symbols s ON gm.symbol_id = s.id
        GROUP BY s.file_id
        HAVING total_degree > 40
    """).fetchall()
    for r in god_rows:
        god_files.add(r["file_id"])

    # 4. Dead exports per file: symbols with is_exported=1 but zero in-degree
    dead_counts = {}
    total_exported = {}
    dead_rows = conn.execute("""
        SELECT s.file_id,
               SUM(CASE WHEN gm.in_degree = 0 OR gm.in_degree IS NULL THEN 1 ELSE 0 END) as dead_count,
               COUNT(*) as export_count
        FROM symbols s
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE s.is_exported = 1
          AND s.kind IN ('function', 'class', 'method', 'interface', 'struct')
        GROUP BY s.file_id
    """).fetchall()
    for r in dead_rows:
        dead_counts[r["file_id"]] = r["dead_count"]
        total_exported[r["file_id"]] = r["export_count"]

    # 5. Coupling intensity per file: average symbol graph degree
    coupling_by_file = {}
    coupling_rows = conn.execute("""
        SELECT s.file_id,
               AVG(COALESCE(gm.in_degree, 0) + COALESCE(gm.out_degree, 0)) AS avg_degree,
               MAX(COALESCE(gm.in_degree, 0) + COALESCE(gm.out_degree, 0)) AS max_degree
        FROM symbols s
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        GROUP BY s.file_id
    """).fetchall()
    for r in coupling_rows:
        coupling_by_file[r["file_id"]] = {
            "avg_degree": float(r["avg_degree"] or 0.0),
            "max_degree": float(r["max_degree"] or 0.0),
        }

    # --- Compute per-file debt ---
    results = []
    for fid, info in stats_by_file.items():
        complexity_raw = info["complexity"] or 0
        churn_raw = info["total_churn"] or 0
        path = info["path"]

        # Complexity normalised 0-1
        complexity_norm = complexity_raw / max_complexity

        # Churn percentile rank 0-1
        churn_pctile = _percentile_rank(churn_raw, churns)

        # Cycle penalty: 1.0 if file has symbols in cycles, else 0
        cycle_penalty = 1.0 if fid in cycle_files else 0.0

        # God component penalty: 1.0 if file has high-degree symbols, else 0
        god_penalty = 1.0 if fid in god_files else 0.0

        # Dead export ratio
        n_dead = dead_counts.get(fid, 0)
        n_exported = total_exported.get(fid, 0)
        dead_ratio = (n_dead / n_exported) if n_exported > 0 else 0.0
        coupling_info = coupling_by_file.get(fid, {})
        coupling_avg_degree = coupling_info.get("avg_degree", 0.0)
        coupling_max_degree = coupling_info.get("max_degree", 0.0)

        # --- SQALE-inspired remediation cost (minutes) ---
        # Each issue type has an estimated fix time, transforming
        # heterogeneous violations into a common currency (dev-minutes).
        # Reference: Letouzey (2012), "The SQALE Method."
        _COST_COMPLEXITY_PER_UNIT = 30   # minutes to refactor per unit of normalized complexity
        _COST_CYCLE_BREAK = 120          # minutes to break a cycle dependency
        _COST_GOD_SPLIT = 240            # minutes to split a god component
        _COST_DEAD_REMOVE = 10           # minutes to safely remove a dead export

        remediation_minutes = (
            complexity_norm * _COST_COMPLEXITY_PER_UNIT
            + cycle_penalty * _COST_CYCLE_BREAK
            + god_penalty * _COST_GOD_SPLIT
            + n_dead * _COST_DEAD_REMOVE
        )

        # Health penalty: normalized 0-1 for scoring (backwards compat)
        health_penalty = (
            complexity_norm * 0.4
            + cycle_penalty * 0.3
            + god_penalty * 0.2
            + dead_ratio * 0.1
        )

        # Hotspot factor: churn amplifies health problems (up to 3x)
        hotspot_factor = max(1.0, churn_pctile * 3)

        debt_score = health_penalty * hotspot_factor

        results.append({
            "file_id": fid,
            "path": path,
            "debt_score": round(debt_score, 3),
            "health_penalty": round(health_penalty, 3),
            "hotspot_factor": round(hotspot_factor, 2),
            "remediation_minutes": round(remediation_minutes, 0),
            "complexity_norm": round(complexity_norm, 3),
            "complexity_raw": round(complexity_raw, 1),
            "churn_pctile": round(churn_pctile, 3),
            "churn_raw": churn_raw,
            "cycle_penalty": cycle_penalty,
            "god_penalty": god_penalty,
            "dead_exports": n_dead,
            "total_exported": n_exported,
            "dead_ratio": round(dead_ratio, 3),
            "coupling_avg_degree": round(coupling_avg_degree, 2),
            "coupling_max_degree": round(coupling_max_degree, 2),
            "commit_count": info["commit_count"] or 0,
            "distinct_authors": info["distinct_authors"] or 0,
        })

    results.sort(key=lambda x: -x["debt_score"])
    return results


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def _summary_stats(items):
    """Compute aggregate project-level debt statistics."""
    if not items:
        return {
            "total_files": 0,
            "total_debt": 0,
            "mean_debt": 0,
            "median_debt": 0,
            "worst_quartile_debt": 0,
            "worst_quartile_files": 0,
            "files_with_cycles": 0,
            "files_with_god_components": 0,
            "hotspot_files": 0,
        }

    scores = sorted(r["debt_score"] for r in items)
    n = len(scores)
    total_debt = sum(scores)
    mean_debt = total_debt / n
    median_debt = scores[n // 2]

    # Worst quartile: top 25% of files by debt
    q_cutoff = max(1, n // 4)
    worst_q = items[:q_cutoff]  # already sorted desc
    worst_q_debt = sum(r["debt_score"] for r in worst_q)

    total_remediation = sum(r.get("remediation_minutes", 0) for r in items)

    return {
        "total_files": n,
        "total_debt": round(total_debt, 1),
        "mean_debt": round(mean_debt, 3),
        "median_debt": round(median_debt, 3),
        "worst_quartile_debt": round(worst_q_debt, 1),
        "worst_quartile_files": len(worst_q),
        "total_remediation_minutes": round(total_remediation, 0),
        "total_remediation_hours": round(total_remediation / 60, 1),
        "files_with_cycles": sum(1 for r in items if r["cycle_penalty"] > 0),
        "files_with_god_components": sum(1 for r in items if r["god_penalty"] > 0),
        "hotspot_files": sum(1 for r in items if r["hotspot_factor"] > 1.0),
    }


def _improvement_suggestions(items):
    """Generate actionable improvement suggestions based on debt distribution."""
    suggestions = []
    if not items:
        return suggestions

    # Top debt files with high hotspot factor
    hot_complex = [r for r in items[:10]
                   if r["hotspot_factor"] > 2.0 and r["complexity_norm"] > 0.5]
    if hot_complex:
        names = ", ".join(os.path.basename(r["path"]) for r in hot_complex[:3])
        suggestions.append(
            f"Refactor hot complex files first: {names} "
            f"(high churn + high complexity = maximum debt leverage)"
        )

    # Cycle-bearing hotspots
    cycle_hot = [r for r in items[:20]
                 if r["cycle_penalty"] > 0 and r["hotspot_factor"] > 1.5]
    if cycle_hot:
        suggestions.append(
            f"{len(cycle_hot)} hotspot file(s) participate in dependency cycles "
            f"-- breaking these cycles reduces cascading change cost"
        )

    # Dead exports in high-churn files
    dead_hot = [r for r in items[:20]
                if r["dead_exports"] > 0 and r["hotspot_factor"] > 1.5]
    if dead_hot:
        total_dead = sum(r["dead_exports"] for r in dead_hot)
        suggestions.append(
            f"{total_dead} dead export(s) in {len(dead_hot)} hotspot file(s) "
            f"-- removing them reduces cognitive load in frequently changed code"
        )

    # General advice based on distribution
    stats = _summary_stats(items)
    if stats["worst_quartile_files"] > 0:
        pct = (stats["worst_quartile_debt"] / stats["total_debt"] * 100
               if stats["total_debt"] > 0 else 0)
        suggestions.append(
            f"Worst quartile ({stats['worst_quartile_files']} files) holds "
            f"{pct:.0f}% of total debt -- focus refactoring budget here"
        )

    return suggestions


# ---------------------------------------------------------------------------
# ROI estimation
# ---------------------------------------------------------------------------

def _estimate_quarterly_touches(item):
    """Estimate per-quarter touch frequency from commit and churn signals."""
    commits = float(item.get("commit_count", 0) or 0.0)
    churn = float(item.get("churn_raw", 0) or 0.0)
    # Commits drive the baseline; churn acts as a secondary amplifier.
    return max(1.0, (commits * 0.25) + (churn / 120.0))


def _estimate_review_overhead_hours(complexity_norm, coupling_norm):
    """Estimate review overhead per touch (hours) from complexity/coupling."""
    # Baseline 9 minutes + complexity + coupling overhead.
    return 0.15 + (complexity_norm * 0.35) + (coupling_norm * 0.20)


def _estimate_refactoring_roi(items, top_n=10):
    """Estimate refactoring ROI for top debt files.

    Formula:
      savings = complexity_reduction * touch_frequency * avg_review_overhead
    """
    if not items:
        return {
            "files_analyzed": 0,
            "estimated_hours_saved_quarter": 0.0,
            "estimated_hours_saved_year": 0.0,
            "confidence_low_hours_saved_quarter": 0.0,
            "confidence_high_hours_saved_quarter": 0.0,
            "confidence": "low",
            "assumptions": (
                "Quarterly touch frequency derived from commit_count + churn; "
                "review overhead derived from complexity + coupling."
            ),
        }, {}

    target = items[:max(1, top_n)]
    max_coupling = max((r.get("coupling_avg_degree", 0.0) or 0.0) for r in target)
    if max_coupling <= 0:
        max_coupling = 1.0

    total_q = 0.0
    low_q = 0.0
    high_q = 0.0
    confidence_total = 0.0
    by_path = {}

    for row in target:
        complexity_norm = float(row.get("complexity_norm", 0.0) or 0.0)
        coupling_avg = float(row.get("coupling_avg_degree", 0.0) or 0.0)
        coupling_norm = min(1.0, coupling_avg / max_coupling)

        complexity_reduction = 0.20 + (complexity_norm * 0.50)
        touch_frequency = _estimate_quarterly_touches(row)
        review_overhead = _estimate_review_overhead_hours(complexity_norm, coupling_norm)
        savings_q = complexity_reduction * touch_frequency * review_overhead

        # Confidence reflects how many ROI signals are present for the file.
        signal_count = 0
        if (row.get("commit_count", 0) or 0) > 0:
            signal_count += 1
        if (row.get("churn_raw", 0) or 0) > 0:
            signal_count += 1
        if coupling_avg > 0:
            signal_count += 1
        if complexity_norm > 0:
            signal_count += 1
        confidence_score = signal_count / 4.0

        if confidence_score >= 0.75:
            confidence = "high"
            band = 0.20
        elif confidence_score >= 0.50:
            confidence = "medium"
            band = 0.35
        else:
            confidence = "low"
            band = 0.50

        low = max(0.0, savings_q * (1.0 - band))
        high = savings_q * (1.0 + band)

        entry = {
            "estimated_hours_saved_quarter": round(savings_q, 2),
            "estimated_hours_saved_year": round(savings_q * 4.0, 2),
            "confidence_low_hours_saved_quarter": round(low, 2),
            "confidence_high_hours_saved_quarter": round(high, 2),
            "confidence": confidence,
            "drivers": {
                "complexity_reduction": round(complexity_reduction, 3),
                "touch_frequency": round(touch_frequency, 2),
                "avg_review_overhead_hours": round(review_overhead, 3),
                "coupling_norm": round(coupling_norm, 3),
            },
        }
        by_path[row["path"]] = entry

        total_q += savings_q
        low_q += low
        high_q += high
        confidence_total += confidence_score

    avg_conf = confidence_total / len(target) if target else 0.0
    if avg_conf >= 0.75:
        confidence_label = "high"
    elif avg_conf >= 0.50:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    summary = {
        "files_analyzed": len(target),
        "estimated_hours_saved_quarter": round(total_q, 1),
        "estimated_hours_saved_year": round(total_q * 4.0, 1),
        "confidence_low_hours_saved_quarter": round(low_q, 1),
        "confidence_high_hours_saved_quarter": round(high_q, 1),
        "confidence": confidence_label,
        "assumptions": (
            "Quarterly touch frequency derived from commit_count + churn; "
            "review overhead derived from complexity + coupling."
        ),
    }
    return summary, by_path


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _group_by_directory(items):
    """Group debt items by parent directory."""
    groups = defaultdict(list)
    for item in items:
        d = _parent_dir(item["path"])
        groups[d].append(item)

    result = []
    for d, files in groups.items():
        total_debt = sum(f["debt_score"] for f in files)
        avg_debt = total_debt / len(files) if files else 0
        result.append({
            "directory": d,
            "file_count": len(files),
            "total_debt": round(total_debt, 1),
            "avg_debt": round(avg_debt, 3),
            "max_debt": round(max(f["debt_score"] for f in files), 3),
            "files": files,
        })

    result.sort(key=lambda x: -x["total_debt"])
    return result


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command()
@click.option('--limit', '-n', default=20, help='Number of files to show (default 20)')
@click.option('--by-kind', 'by_kind', is_flag=True,
              help='Group results by parent directory')
@click.option('--threshold', type=float, default=None,
              help='Only show files above this debt score')
@click.option('--roi', is_flag=True,
              help='Estimate refactoring ROI (developer-hours saved/quarter).')
@click.pass_context
def debt(ctx, limit, by_kind, threshold, roi):
    """Hotspot-weighted technical debt prioritization.

    Combines code health signals (complexity, cycles, god components, dead
    exports) with churn hotspot data. Files that are both unhealthy AND
    frequently changed get amplified debt scores -- these are the highest
    leverage refactoring targets.

    Formula: debt = health_penalty * hotspot_factor

    \b
    health_penalty = complexity*0.4 + cycles*0.3 + god*0.2 + dead*0.1
    hotspot_factor = max(1.0, churn_percentile * 3)   # up to 3x for hot files
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
        all_items = _compute_file_debt(conn)

        if not all_items:
            if json_mode:
                click.echo(to_json(json_envelope("debt",
                    summary={"total_files": 0, "total_debt": 0},
                    items=[],
                )))
            else:
                click.echo("No file stats available. Run `roam index` first.")
            return

        # Apply threshold filter
        if threshold is not None:
            all_items = [r for r in all_items if r["debt_score"] >= threshold]

        stats = _summary_stats(all_items)
        suggestions = _improvement_suggestions(all_items)
        roi_summary, roi_by_path = ({}, {})
        if roi:
            roi_summary, roi_by_path = _estimate_refactoring_roi(
                all_items, top_n=max(limit, 10),
            )

        def _roi_payload(path):
            entry = roi_by_path.get(path)
            if not entry:
                return None
            return {
                "estimated_hours_saved_quarter": entry["estimated_hours_saved_quarter"],
                "estimated_hours_saved_year": entry["estimated_hours_saved_year"],
                "confidence_low_hours_saved_quarter": (
                    entry["confidence_low_hours_saved_quarter"]
                ),
                "confidence_high_hours_saved_quarter": (
                    entry["confidence_high_hours_saved_quarter"]
                ),
                "confidence": entry["confidence"],
            }

        # --- Grouped by directory ---
        if by_kind:
            groups = _group_by_directory(all_items)

            if json_mode:
                payload = {
                    "summary": stats,
                    "budget": token_budget,
                    "suggestions": suggestions,
                    "grouping": "directory",
                    "groups": [
                        {
                            "directory": g["directory"],
                            "file_count": g["file_count"],
                            "total_debt": g["total_debt"],
                            "avg_debt": g["avg_debt"],
                            "max_debt": g["max_debt"],
                            "files": [
                                {
                                    "path": f["path"],
                                    "debt_score": f["debt_score"],
                                    "health_penalty": f["health_penalty"],
                                    "hotspot_factor": f["hotspot_factor"],
                                    **(
                                        {"roi": _roi_payload(f["path"])}
                                        if roi and _roi_payload(f["path"])
                                        else {}
                                    ),
                                }
                                for f in g["files"][:limit]
                            ],
                        }
                        for g in groups
                    ],
                }
                if roi:
                    payload["roi"] = roi_summary
                click.echo(to_json(json_envelope("debt", **payload)))
                return

            # Text output: grouped
            click.echo("=== Technical Debt by Directory ===\n")
            _print_summary(stats, suggestions)
            if roi and roi_summary:
                click.echo()
                click.echo("  Refactoring ROI estimate:")
                click.echo(
                    f"    Top {roi_summary['files_analyzed']} files could save "
                    f"~{roi_summary['estimated_hours_saved_quarter']:.1f} h/quarter "
                    f"({roi_summary['confidence_low_hours_saved_quarter']:.1f}-"
                    f"{roi_summary['confidence_high_hours_saved_quarter']:.1f} h, "
                    f"{roi_summary['confidence']} confidence)"
                )
            click.echo()

            for g in groups[:limit]:
                click.echo(f"  {g['directory']}/  "
                           f"({g['file_count']} files, "
                           f"total={g['total_debt']:.1f}, "
                           f"avg={g['avg_debt']:.3f}, "
                           f"max={g['max_debt']:.3f})")
                # Show top 5 files per group
                for f in g["files"][:5]:
                    click.echo(f"    {f['debt_score']:.3f}  {os.path.basename(f['path'])}")
            if len(groups) > limit:
                click.echo(f"\n  (+{len(groups) - limit} more directories)")
            return

        # --- Flat list (default) ---
        display = all_items[:limit]

        if json_mode:
            payload = {
                "summary": stats,
                "budget": token_budget,
                "suggestions": suggestions,
                "items": [
                    {
                        "path": r["path"],
                        "debt_score": r["debt_score"],
                        "health_penalty": r["health_penalty"],
                        "hotspot_factor": r["hotspot_factor"],
                        "breakdown": {
                            "complexity_norm": r["complexity_norm"],
                            "complexity_raw": r["complexity_raw"],
                            "churn_pctile": r["churn_pctile"],
                            "churn_raw": r["churn_raw"],
                            "cycle_penalty": r["cycle_penalty"],
                            "god_penalty": r["god_penalty"],
                            "dead_exports": r["dead_exports"],
                            "total_exported": r["total_exported"],
                            "dead_ratio": r["dead_ratio"],
                            "coupling_avg_degree": r["coupling_avg_degree"],
                            "coupling_max_degree": r["coupling_max_degree"],
                        },
                        "commit_count": r["commit_count"],
                        "distinct_authors": r["distinct_authors"],
                        **(
                            {"roi": _roi_payload(r["path"])}
                            if roi and _roi_payload(r["path"])
                            else {}
                        ),
                    }
                    for r in display
                ],
            }
            if roi:
                payload["roi"] = roi_summary
            click.echo(to_json(json_envelope("debt", **payload)))
            return

        # Text output: flat
        click.echo("=== Technical Debt (hotspot-weighted) ===\n")
        _print_summary(stats, suggestions)
        if roi and roi_summary:
            click.echo()
            click.echo("  Refactoring ROI estimate:")
            click.echo(
                f"    Top {roi_summary['files_analyzed']} files could save "
                f"~{roi_summary['estimated_hours_saved_quarter']:.1f} h/quarter "
                f"({roi_summary['confidence_low_hours_saved_quarter']:.1f}-"
                f"{roi_summary['confidence_high_hours_saved_quarter']:.1f} h, "
                f"{roi_summary['confidence']} confidence)"
            )
        click.echo()

        table_rows = []
        for r in display:
            # Build compact breakdown string
            parts = []
            if r["complexity_norm"] > 0.3:
                parts.append(f"cx={r['complexity_raw']:.0f}")
            if r["cycle_penalty"]:
                parts.append("cyc")
            if r["god_penalty"]:
                parts.append("god")
            if r["dead_exports"]:
                parts.append(f"dead={r['dead_exports']}")
            breakdown = " ".join(parts) if parts else "-"

            # Hotspot indicator
            if r["hotspot_factor"] >= 2.5:
                hot = "HOT"
            elif r["hotspot_factor"] >= 1.5:
                hot = "warm"
            else:
                hot = ""

            row = [
                f"{r['debt_score']:.3f}",
                f"{r['health_penalty']:.2f}",
                f"{r['hotspot_factor']:.1f}x",
                hot,
            ]
            if roi:
                roi_item = _roi_payload(r["path"])
                row.append(
                    f"{roi_item['estimated_hours_saved_quarter']:.1f}h"
                    if roi_item else "-"
                )
            row.extend([breakdown, loc(r["path"])])
            table_rows.append(row)

        headers = ["Debt", "Health", "Hotspot", "Heat"]
        if roi:
            headers.append("ROI/qtr")
        headers.extend(["Breakdown", "File"])

        click.echo(format_table(headers, table_rows))

        remaining = len(all_items) - limit
        if remaining > 0:
            click.echo(f"\n  (+{remaining} more files, use --limit to show more)")


def _print_summary(stats, suggestions):
    """Print text summary block."""
    click.echo(f"  Project: {stats['total_files']} files, "
               f"total debt = {stats['total_debt']:.1f}, "
               f"mean = {stats['mean_debt']:.3f}, "
               f"median = {stats['median_debt']:.3f}")
    click.echo(f"  Worst quartile: {stats['worst_quartile_files']} files hold "
               f"{stats['worst_quartile_debt']:.1f} debt")
    click.echo(f"  Signals: {stats['files_with_cycles']} files in cycles, "
               f"{stats['files_with_god_components']} with god components, "
               f"{stats['hotspot_files']} hotspots")

    if suggestions:
        click.echo()
        click.echo("  Suggestions:")
        for s in suggestions:
            click.echo(f"    - {s}")
