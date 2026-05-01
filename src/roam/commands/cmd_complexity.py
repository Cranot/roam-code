"""Show per-symbol cognitive complexity metrics.

Surfaces the symbol_metrics table populated during indexing. Ranks
functions/methods by cognitive complexity to identify the hardest-to-
understand code in the project.
"""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json


def _safe_metric(row, key, default=0.0):
    """Safely access a metric column that may not exist in older DBs."""
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError):
        return default


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
@click.option(
    "--top",
    "--limit",
    "-n",
    "limit",
    default=20,
    type=int,
    help="Number of results to show (alias: --limit, -n)",
)
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=None,
    help="Minimum cognitive complexity to include",
)
@click.option(
    "--by-file",
    is_flag=True,
    help="Group results by file and show per-file summary",
)
@click.option(
    "--bumpy-road",
    is_flag=True,
    help="Detect bumpy-road pattern: files with multiple medium-complexity functions",
)
@click.option(
    "--include-tooling",
    is_flag=True,
    default=False,
    help=(
        "Include CI scripts, examples, generated code, vendor, and "
        "workspaces directories. Excluded by default — high complexity "
        "in tooling/codegen is expected and uninteresting (Python pivot "
        "dogfood 2026-05-02 found agent-generated workspaces dominating)."
    ),
)
@click.pass_context
def complexity(ctx, target, limit, threshold, by_file, bumpy_road, include_tooling):
    """Show cognitive complexity metrics for functions and methods.

    Unlike ``health`` (which scores the whole codebase) and ``debt`` (which
    estimates remediation effort), this command ranks individual symbols by
    cognitive complexity.

    Ranks symbols by a multi-factor complexity score that accounts for
    nesting depth, boolean operators, callback depth, and control-flow
    breaks. Use --bumpy-road to find files where many functions are
    individually moderate but collectively hard to maintain.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
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
            where_parts.append("(f.path LIKE ? OR s.name LIKE ? OR s.qualified_name LIKE ?)")
            pattern = f"%{target}%"
            params.extend([pattern, pattern, pattern])

        if threshold is not None:
            where_parts.append("sm.cognitive_complexity >= ?")
            params.append(threshold)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        # Pull more rows than ``limit`` when default-excluding tooling
        # so the displayed top-N still has the requested count after
        # filtering. 5x is comfortable for typical exclusion shares.
        fetch_limit = limit * 5 if not include_tooling else limit
        rows = conn.execute(
            f"""SELECT sm.*, s.name, s.qualified_name, s.kind,
                       s.line_start, s.line_end, f.path as file_path
                FROM symbol_metrics sm
                JOIN symbols s ON sm.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE {where_clause}
                ORDER BY sm.cognitive_complexity DESC
                LIMIT ?""",
            params + [fetch_limit],
        ).fetchall()
        if not include_tooling:
            from roam.output.file_role_hints import is_excluded_path

            rows = [r for r in rows if not is_excluded_path(r["file_path"] or "")]
            rows = rows[:limit]

        if not rows:
            if sarif_mode:
                from roam.output.sarif import complexity_to_sarif, write_sarif

                sarif = complexity_to_sarif([], threshold=threshold or 0)
                click.echo(write_sarif(sarif))
                return
            click.echo("No matching symbols found.")
            return

        if sarif_mode:
            from roam.output.sarif import complexity_to_sarif, write_sarif

            complex_symbols = [
                {
                    "name": r["qualified_name"] or r["name"],
                    "kind": r["kind"],
                    "file": r["file_path"],
                    "line": r["line_start"],
                    "cognitive_complexity": r["cognitive_complexity"],
                    "severity": _severity(r["cognitive_complexity"]),
                }
                for r in rows
            ]
            sarif = complexity_to_sarif(complex_symbols, threshold=threshold or 0)
            click.echo(write_sarif(sarif))
            return

        if by_file:
            _by_file_output(conn, rows, json_mode)
            return

        # Compute distribution stats
        all_scores = conn.execute(
            "SELECT cognitive_complexity FROM symbol_metrics ORDER BY cognitive_complexity DESC"
        ).fetchall()
        scores = [r[0] for r in all_scores]
        total = len(scores)
        avg = sum(scores) / total if total else 0
        p90 = scores[int(total * 0.1)] if total > 10 else (scores[0] if scores else 0)
        critical_count = sum(1 for s in scores if s >= 25)
        high_count = sum(1 for s in scores if 15 <= s < 25)

        if json_mode:
            _worst_name = (rows[0]["qualified_name"] or rows[0]["name"]) if rows else "none"
            _worst_cc = rows[0]["cognitive_complexity"] if rows else 0
            _cx_verdict = (
                f"avg complexity {avg:.1f}, "
                f"{critical_count} critical, {high_count} high; "
                f"worst: {_worst_name}({_worst_cc:.0f})"
            )
            click.echo(
                to_json(
                    json_envelope(
                        "complexity",
                        summary={
                            "verdict": _cx_verdict,
                            "total_analyzed": total,
                            "average_complexity": round(avg, 1),
                            "p90_complexity": round(p90, 1),
                            "critical_count": critical_count,
                            "high_count": high_count,
                            "showing": len(rows),
                        },
                        budget=token_budget,
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
                                "cyclomatic_density": _safe_metric(r, "cyclomatic_density"),
                                "halstead_volume": _safe_metric(r, "halstead_volume"),
                                "halstead_difficulty": _safe_metric(r, "halstead_difficulty"),
                                "halstead_effort": _safe_metric(r, "halstead_effort"),
                                "halstead_bugs": _safe_metric(r, "halstead_bugs"),
                                "severity": _severity(r["cognitive_complexity"]),
                            }
                            for r in rows
                        ],
                    )
                )
            )
            return

        # Text output
        _worst_name_txt = (rows[0]["qualified_name"] or rows[0]["name"]) if rows else "none"
        _worst_cc_txt = rows[0]["cognitive_complexity"] if rows else 0
        _cx_verdict_txt = (
            f"avg complexity {avg:.1f}, "
            f"{critical_count} critical, {high_count} high; "
            f"worst: {_worst_name_txt}({_worst_cc_txt:.0f})"
        )
        click.echo(f"VERDICT: {_cx_verdict_txt}")
        click.echo()
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
            cd = _safe_metric(r, "cyclomatic_density")
            if cd > 0.15:
                factors.append(f"density={cd:.2f}")
            hv = _safe_metric(r, "halstead_volume")
            if hv > 500:
                factors.append(f"H.vol={hv:.0f}")

            factor_str = f" ({', '.join(factors)})" if factors else ""

            click.echo(f"  {icon}{r['cognitive_complexity']:5.0f}  {name:<45s} {kind} {location}{factor_str}")


def _by_file_output(conn, rows, json_mode):
    """Group complexity results by file."""
    from collections import defaultdict

    by_file = defaultdict(list)
    for r in rows:
        by_file[r["file_path"]].append(r)

    file_summaries = []
    for fpath, syms in sorted(by_file.items()):
        scores = [s["cognitive_complexity"] for s in syms]
        file_summaries.append(
            {
                "file": fpath,
                "symbols": len(syms),
                "max_complexity": max(scores),
                "avg_complexity": round(sum(scores) / len(scores), 1),
                "total_complexity": round(sum(scores), 1),
                "items": syms,
            }
        )

    file_summaries.sort(key=lambda f: f["total_complexity"], reverse=True)

    if json_mode:
        _bf_max = file_summaries[0]["max_complexity"] if file_summaries else 0
        _bf_file = file_summaries[0]["file"].split("/")[-1] if file_summaries else "none"
        _bf_verdict = f"{len(file_summaries)} files analyzed, worst file: {_bf_file} (max={_bf_max:.0f})"
        click.echo(
            to_json(
                json_envelope(
                    "complexity",
                    summary={"verdict": _bf_verdict, "files": len(file_summaries)},
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
                )
            )
        )
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
            click.echo(f"    {icon}{s['cognitive_complexity']:5.0f}  {s['name']}")
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
        _br_verdict = f"{len(rows)} bumpy-road files found (3+ functions with complexity >= {min_score})"
        click.echo(
            to_json(
                json_envelope(
                    "complexity",
                    summary={
                        "verdict": _br_verdict,
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
                )
            )
        )
        return

    _br_verdict_txt = f"{len(rows)} bumpy-road files found (3+ functions with complexity >= {min_score})"
    click.echo(f"VERDICT: {_br_verdict_txt}")
    click.echo()
    click.echo(f"Bumpy-road files (3+ functions with complexity >= {min_score}):\n")
    for r in rows:
        bumpy = r["func_count"] * r["avg_cc"]
        click.echo(
            f"  {r['path']}\n"
            f"    {r['func_count']} complex functions, "
            f"total={r['total']:.0f}, avg={r['avg_cc']:.1f}, "
            f"max={r['max_cc']:.0f}, bumpy_score={bumpy:.0f}"
        )
