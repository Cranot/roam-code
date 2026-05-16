"""Rank files by churn x complexity score (highest-leverage refactoring targets).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because weather outputs are invocation-scoped
refactoring-target rankings — not per-location violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import math

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.db.queries import TOP_CHURN_FILES
from roam.output.formatter import format_table, json_envelope, to_json


@roam_capability(
    name="weather",
    category="health",
    summary="Rank files by churn x complexity score (highest-leverage refactoring targets)",
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
@click.command()
@click.option("-n", "count", default=20, help="Number of hotspots")
@click.pass_context
def weather(ctx, count):
    """Rank files by churn x complexity score (highest-leverage refactoring targets).

    Unlike ``debt`` (which computes comprehensive technical debt including cycles and
    god-component penalties), this command provides a lightweight churn-times-complexity
    ranking using geometric mean normalization — no graph traversal required.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        rows = conn.execute(TOP_CHURN_FILES, (count * 2,)).fetchall()
        if not rows:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "weather",
                            summary={"verdict": "no churn data available", "hotspots": 0},
                            hotspots=[],
                        )
                    )
                )
            else:
                click.echo("VERDICT: no churn data available\n")
                click.echo("No churn data available. Is this a git repository?")
            return

        # Collect raw values for percentile-based normalization
        raw = []
        for r in rows:
            churn = r["total_churn"] or 0
            complexity = r["complexity"] or 1
            commits = r["commit_count"] or 0
            authors = r["distinct_authors"] or 0
            raw.append((churn, complexity, commits, authors, r["path"]))

        all_churns = sorted(v[0] for v in raw)
        all_complexities = sorted(v[1] for v in raw)
        churn_p75 = all_churns[int(len(all_churns) * 0.75)] if all_churns else 1
        cmplx_p75 = all_complexities[int(len(all_complexities) * 0.75)] if all_complexities else 1
        churn_p75 = max(churn_p75, 1)
        cmplx_p75 = max(cmplx_p75, 1)

        scored = []
        for churn, complexity, commits, authors, path in raw:
            # Geometric mean of normalized values avoids explosive growth
            # and balances both signals equally (CodeScene-inspired, Tornhill 2018).
            churn_norm = churn / churn_p75
            cmplx_norm = complexity / cmplx_p75
            score = math.sqrt(max(churn_norm, 0) * max(cmplx_norm, 0))

            # Percentile-adaptive classification instead of magic thresholds
            if churn_norm > 1.0 and cmplx_norm > 1.0:
                reason = "BOTH"
            elif churn_norm > cmplx_norm * 2:
                reason = "HIGH-CHURN"
            else:
                reason = "HIGH-COMPLEXITY"
            scored.append((score, churn, complexity, commits, authors, reason, path))
        scored.sort(reverse=True)

        if json_mode:
            hotspot_list = [
                {
                    "path": path,
                    "score": round(score),
                    "churn": churn,
                    "complexity": round(complexity, 1),
                    "commits": commits,
                    "authors": authors,
                    "reason": reason,
                }
                for score, churn, complexity, commits, authors, reason, path in scored[:count]
            ]
            if hotspot_list:
                _top = hotspot_list[0]
                _verdict = (
                    f"{len(hotspot_list)} hotspots; top: {_top['path'].split('/')[-1]}"
                    f"(churn={_top['churn']}, score={_top['score']})"
                )
            else:
                _verdict = "no hotspots found"
            click.echo(
                to_json(
                    json_envelope(
                        "weather",
                        summary={
                            "verdict": _verdict,
                            "hotspots": len(hotspot_list),
                            "max_score": hotspot_list[0]["score"] if hotspot_list else 0,
                        },
                        hotspots=hotspot_list,
                    )
                )
            )
            return

        # Score is geometric mean of churn_norm × cmplx_norm. Empirically
        # the top-N rows fall in 1.0-3.0 — formatting with ``.0f`` rounded
        # everything to 1, killing the discrimination the column is
        # supposed to provide. Two decimals
        # restore signal.
        table_rows = []
        for score, churn, complexity, commits, authors, reason, path in scored[:count]:
            table_rows.append(
                [
                    f"{score:.2f}",
                    str(churn),
                    f"{complexity:.1f}",
                    str(commits),
                    str(authors),
                    reason,
                    path,
                ]
            )

        if scored:
            _top_path, _top_churn, _top_score = scored[0][6], scored[0][1], scored[0][0]
            _verdict = (
                f"{len(scored[:count])} hotspots; top: {_top_path.split('/')[-1]}"
                f"(churn={_top_churn}, score={_top_score:.2f})"
            )
        else:
            _verdict = "no hotspots found"
        click.echo(f"VERDICT: {_verdict}\n")
        from roam.commands.resolve import index_staleness_hint as _stale_hint

        _stale = _stale_hint()
        if _stale:
            click.echo(f"NOTE: {_stale}\n")
        click.echo("=== Hotspots (churn x complexity) ===")
        click.echo(
            format_table(
                ["Score", "Churn", "Cmplx", "Commits", "Authors", "Reason", "Path"],
                table_rows,
            )
        )
