"""Show per-symbol cognitive complexity metrics.

Surfaces the symbol_metrics table populated during indexing. Ranks
functions/methods by cognitive complexity to identify the hardest-to-
understand code in the project.
"""

import click

from roam.db.connection import open_db
from roam.commands.resolve import ensure_index
from roam.output.formatter import loc, abbrev_kind, to_json, json_envelope


def _severity(score: float) -> str:
    """Map cognitive complexity score to a severity label."""
    if score >= 25:
        return "CRITICAL"
    if score >= 15:
        return "HIGH"
    if score >= 8:
        return "MEDIUM"
    return "LOW"


def _severity_icon(sev: str) -> str:
    icons = {"CRITICAL": "!!", "HIGH": "! ", "MEDIUM": "~ ", "LOW": "  "}
    return icons.get(sev, "  ")


@click.command("complexity")
@click.argument("target", required=False, default=None)
@click.option("--limit", "-n", default=20, help="Number of results to show")
@click.option(
    "--threshold", "-t", type=float, default=None,
    help="Minimum cognitive complexity to include",
)
@click.option(
    "--by-file", is_flag=True,
    help="Group results by file and show per-file summary",
)
@click.option(
    "--bumpy-road", is_flag=True,
    help="Detect bumpy-road pattern: files with multiple medium-complexity functions",
)
@click.pass_context
def complexity(ctx, target, limit, threshold, by_file, bumpy_road):
    """Show cognitive complexity metrics for functions and methods.

    Ranks symbols by a multi-factor complexity score that accounts for
    nesting depth, boolean operators, callback depth, and control-flow
    breaks. Use --bumpy-road to find files where many functions are
    individually moderate but collectively hard to maintain.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        # Check if symbol_metrics table has data
        try:
            count = conn.execute("SELECT COUNT(*) FROM symbol_metrics").fetchone()[0]
        except Exception:
            click.echo("No complexity data found. Re-index with: roam index --force")
            raise SystemExit(1)

        if count == 0:
            click.echo("No complexity data found. Re-index with: roam index --force")
            raise SystemExit(1)

        if bumpy_road:
            _bumpy_road(conn, json_mode, limit, threshold)
            return

        # Build query
        where_parts = []
        params = []

        if target:
            # Filter by file path or symbol name
            where_parts.append(
                "(f.path LIKE ? OR s.name LIKE ? OR s.qualified_name LIKE ?)"
            )
            pattern = f"%{target}%"
            params.extend([pattern, pattern, pattern])

        if threshold is not None:
            where_parts.append("sm.cognitive_complexity >= ?")
            params.append(threshold)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        rows = conn.execute(
            f"""SELECT sm.*, s.name, s.qualified_name, s.kind,
                       s.line_start, s.line_end, f.path as file_path
                FROM symbol_metrics sm
                JOIN symbols s ON sm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE {where_clause}
                ORDER BY sm.cognitive_complexity DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()

        if not rows:
            click.echo("No matching symbols found.")
            return

        if by_file:
            _by_file_output(conn, rows, json_mode)
            return

        # Compute distribution stats
        all_scores = conn.execute(
            "SELECT cognitive_complexity FROM symbol_metrics "
            "ORDER BY cognitive_complexity DESC"
        ).fetchall()
        scores = [r[0] for r in all_scores]
        total = len(scores)
        avg = sum(scores) / total if total else 0
        p90 = scores[int(total * 0.1)] if total > 10 else (scores[0] if scores else 0)
        critical_count = sum(1 for s in scores if s >= 25)
        high_count = sum(1 for s in scores if 15 <= s < 25)

        if json_mode:
            click.echo(to_json(json_envelope("complexity",
                summary={
                    "total_analyzed": total,
                    "average_complexity": round(avg, 1),
                    "p90_complexity": round(p90, 1),
                    "critical_count": critical_count,
                    "high_count": high_count,
                    "showing": len(rows),
                },
                symbols=[
                    {
                        "name": r["qualified_name"] or r["name"],
                        "kind": r["kind"],
                        "file": r["file_path"],
                        "line": r["line_start"],
                        "cognitive_complexity": r["cognitive_complexity"],
                        "nesting_depth": r["nesting_depth"],
                        "param_count": r["param_count"],
                        "line_count": r["line_count"],
                        "return_count": r["return_count"],
                        "bool_op_count": r["bool_op_count"],
                        "callback_depth": r["callback_depth"],
                        "severity": _severity(r["cognitive_complexity"]),
                    }
                    for r in rows
                ],
            )))
            return

        # Text output
        click.echo(
            f"Cognitive complexity ({total} functions analyzed, "
            f"avg={avg:.1f}, p90={p90:.1f}, "
            f"{critical_count} critical, {high_count} high):\n"
        )

        for r in rows:
            sev = _severity(r["cognitive_complexity"])
            icon = _severity_icon(sev)
            name = r["qualified_name"] or r["name"]
            location = loc(r["file_path"], r["line_start"])
            kind = abbrev_kind(r["kind"])

            factors = []
            if r["nesting_depth"] >= 4:
                factors.append(f"nest={r['nesting_depth']}")
            if r["bool_op_count"] >= 3:
                factors.append(f"bool={r['bool_op_count']}")
            if r["callback_depth"] >= 2:
                factors.append(f"cb={r['callback_depth']}")
            if r["param_count"] >= 5:
                factors.append(f"params={r['param_count']}")
            if r["return_count"] >= 4:
                factors.append(f"ret={r['return_count']}")

            factor_str = f" ({', '.join(factors)})" if factors else ""

            click.echo(
                f"  {icon}{r['cognitive_complexity']:5.0f}  "
                f"{name:<45s} {kind} {location}{factor_str}"
            )


def _by_file_output(conn, rows, json_mode):
    """Group complexity results by file."""
    from collections import defaultdict

    by_file = defaultdict(list)
    for r in rows:
        by_file[r["file_path"]].append(r)

    file_summaries = []
    for fpath, syms in sorted(by_file.items()):
        scores = [s["cognitive_complexity"] for s in syms]
        file_summaries.append({
            "file": fpath,
            "symbols": len(syms),
            "max_complexity": max(scores),
            "avg_complexity": round(sum(scores) / len(scores), 1),
            "total_complexity": round(sum(scores), 1),
            "items": syms,
        })

    file_summaries.sort(key=lambda f: f["total_complexity"], reverse=True)

    if json_mode:
        click.echo(to_json(json_envelope("complexity",
            summary={"files": len(file_summaries)},
            files=[
                {
                    "file": fs["file"],
                    "symbol_count": fs["symbols"],
                    "max_complexity": fs["max_complexity"],
                    "avg_complexity": fs["avg_complexity"],
                    "total_complexity": fs["total_complexity"],
                }
                for fs in file_summaries
            ],
        )))
        return

    for fs in file_summaries:
        click.echo(
            f"  {fs['file']} — {fs['symbols']} functions, "
            f"max={fs['max_complexity']:.0f}, avg={fs['avg_complexity']:.1f}, "
            f"total={fs['total_complexity']:.0f}"
        )
        for s in sorted(fs["items"], key=lambda x: x["cognitive_complexity"], reverse=True):
            sev = _severity(s["cognitive_complexity"])
            icon = _severity_icon(sev)
            click.echo(
                f"    {icon}{s['cognitive_complexity']:5.0f}  {s['name']}"
            )
        click.echo()


def _bumpy_road(conn, json_mode, limit, threshold):
    """Detect bumpy-road pattern: files with many moderate-complexity functions.

    A file with 10 functions at complexity 8 is harder to maintain than
    a file with 1 function at complexity 20, even though the single
    function scores higher. The bumpy road score captures this.
    """
    min_score = threshold or 5  # Minimum per-function complexity to count

    rows = conn.execute(
        """SELECT f.path, COUNT(*) as func_count,
                  SUM(sm.cognitive_complexity) as total,
                  AVG(sm.cognitive_complexity) as avg_cc,
                  MAX(sm.cognitive_complexity) as max_cc,
                  MAX(sm.nesting_depth) as max_nest
           FROM symbol_metrics sm
           JOIN symbols s ON sm.symbol_id = s.id
           JOIN files f ON s.file_id = f.id
           WHERE sm.cognitive_complexity >= ?
           GROUP BY f.path
           HAVING COUNT(*) >= 3
           ORDER BY SUM(sm.cognitive_complexity) DESC
           LIMIT ?""",
        (min_score, limit),
    ).fetchall()

    if not rows:
        click.echo("No bumpy-road files found.")
        return

    if json_mode:
        click.echo(to_json(json_envelope("complexity",
            summary={
                "mode": "bumpy-road",
                "threshold": min_score,
                "files_found": len(rows),
            },
            files=[
                {
                    "file": r["path"],
                    "complex_functions": r["func_count"],
                    "total_complexity": round(r["total"], 1),
                    "avg_complexity": round(r["avg_cc"], 1),
                    "max_complexity": round(r["max_cc"], 1),
                    "max_nesting": r["max_nest"],
                    "bumpy_score": round(r["func_count"] * r["avg_cc"], 1),
                }
                for r in rows
            ],
        )))
        return

    click.echo(
        f"Bumpy-road files (3+ functions with complexity >= {min_score}):\n"
    )
    for r in rows:
        bumpy = r["func_count"] * r["avg_cc"]
        click.echo(
            f"  {r['path']}\n"
            f"    {r['func_count']} complex functions, "
            f"total={r['total']:.0f}, avg={r['avg_cc']:.1f}, "
            f"max={r['max_cc']:.0f}, bumpy_score={bumpy:.0f}"
        )
