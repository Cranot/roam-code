"""Find runtime hotspots — symbols with high call_count and/or high p99 latency.

Reads from the ``runtime_stats`` table (populated by ``roam trace-ingest``)
and ranks symbols by a runtime-cost score:

    score = log10(call_count + 1) * (p99_latency_ms or 1)

Optionally filters to symbols touched by a git diff so a CI step can
gate "is this PR slowing down a hot path?"

Examples
--------
    roam why-slow                         # top 20 slowest symbols
    roam why-slow --top 50                # top 50
    roam why-slow --changed               # only symbols in `git diff`
    roam why-slow --changed --base main   # vs main branch
    roam why-slow --json                  # structured output
"""

from __future__ import annotations

import math

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import format_table, json_envelope, loc, to_json


def _runtime_score(call_count: int, p99: float | None) -> float:
    """Cost = log10(call_count + 1) * (p99_ms or 1).

    Captures the intuition that a function called 1M times at 1ms is
    typically a worse offender than a function called 10 times at 100ms.
    """
    if not call_count:
        return 0.0
    p99 = p99 or 1.0
    return math.log10(call_count + 1) * p99


def _query_hotspots(conn, top: int, changed_files: set[str] | None):
    """Return rows joined with symbol metadata.

    A row may have NULL symbol_id (trace name didn't resolve to an
    indexed symbol). Those still surface — the user might want to
    know about un-mapped hot paths.
    """
    rows = conn.execute(
        """
        SELECT
            rs.symbol_name AS name,
            rs.file_path AS file_path,
            rs.call_count AS call_count,
            rs.p50_latency_ms AS p50,
            rs.p99_latency_ms AS p99,
            rs.error_rate AS error_rate,
            rs.trace_source AS source,
            rs.last_seen AS last_seen,
            s.id AS symbol_id,
            s.qualified_name AS qname,
            s.line_start AS line_start,
            f.path AS resolved_path
        FROM runtime_stats rs
        LEFT JOIN symbols s ON rs.symbol_id = s.id
        LEFT JOIN files f ON s.file_id = f.id
        WHERE rs.call_count > 0
        """
    ).fetchall()

    scored = []
    for r in rows:
        path = r["resolved_path"] or r["file_path"] or ""
        if changed_files is not None and path not in changed_files:
            continue
        scored.append(
            {
                "name": r["qname"] or r["name"] or "<unknown>",
                "file_path": path,
                "line_start": r["line_start"],
                "call_count": r["call_count"] or 0,
                "p50_ms": r["p50"],
                "p99_ms": r["p99"],
                "error_rate": r["error_rate"] or 0.0,
                "source": r["source"],
                "last_seen": r["last_seen"],
                "score": _runtime_score(r["call_count"] or 0, r["p99"]),
                "indexed": r["symbol_id"] is not None,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top]


@roam_capability(
    name="why-slow",
    category="health",
    summary="Find runtime hotspots — symbols slow under real production traffic",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "debug"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--top", default=20, type=int, help="Limit to top N hotspots (default 20)")
@click.option("--changed", is_flag=True, help="Filter to symbols in changed files (vs base branch)")
@click.option("--base", default="HEAD~1", help="Base ref for --changed (default HEAD~1)")
@click.option("--min-calls", default=0, type=int, help="Filter out symbols below this call_count")
@click.pass_context
def why_slow(ctx, top: int, changed: bool, base: str, min_calls: int):
    """Find runtime hotspots — symbols slow under real production traffic.

    Reads from runtime_stats. Run ``roam trace-ingest`` first to populate
    this table from OpenTelemetry/Jaeger/Zipkin or generic CSV traces.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        total_traced = conn.execute("SELECT COUNT(*) FROM runtime_stats WHERE call_count > 0").fetchone()[0]

        if total_traced == 0:
            verdict = "NO RUNTIME DATA"
            message = (
                "No runtime data ingested. Run `roam trace-ingest <file>` "
                "with an OpenTelemetry, Jaeger, Zipkin, or generic CSV "
                "trace dump to populate runtime_stats."
            )
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "why-slow",
                            summary={"verdict": verdict, "total_traced": 0, "hotspots": 0},
                            message=message,
                            hotspots=[],
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict}")
            click.echo(message)
            return

        changed_files: set[str] | None = None
        if changed:
            changed_files = set(get_changed_files(base=base))
            if not changed_files:
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "why-slow",
                                summary={
                                    "verdict": "NO CHANGES",
                                    "base": base,
                                    "total_traced": total_traced,
                                    "hotspots": 0,
                                },
                                hotspots=[],
                            )
                        )
                    )
                    return
                click.echo(f"VERDICT: NO CHANGES vs {base}")
                return

        hotspots = _query_hotspots(conn, top=top, changed_files=changed_files)
        hotspots = [h for h in hotspots if h["call_count"] >= min_calls]

        verdict = "OK" if not hotspots else f"{len(hotspots)} HOTSPOT(S)"

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "why-slow",
                        summary={
                            "verdict": verdict,
                            "total_traced": total_traced,
                            "hotspots": len(hotspots),
                            "filtered_changed": changed,
                            "min_calls": min_calls,
                        },
                        hotspots=hotspots,
                    )
                )
            )
            return

        click.echo(f"VERDICT: {verdict}")
        click.echo(f"Total traced symbols: {total_traced}")
        if changed:
            click.echo(f"Filtered to changed files vs {base}: {len(changed_files or [])}")
        click.echo()

        if not hotspots:
            return

        rows = []
        for h in hotspots:
            location = loc(h["file_path"], h["line_start"]) if h["line_start"] else h["file_path"]
            p99 = f"{h['p99_ms']:.1f}ms" if h["p99_ms"] is not None else "—"
            err = f"{h['error_rate'] * 100:.1f}%" if h["error_rate"] else "0%"
            rows.append(
                [
                    h["name"][:40],
                    f"{h['call_count']:,}",
                    p99,
                    err,
                    f"{h['score']:.1f}",
                    location[:40] if location else "",
                ]
            )
        click.echo(format_table(["Symbol", "Calls", "p99", "Errs", "Score", "Location"], rows))
