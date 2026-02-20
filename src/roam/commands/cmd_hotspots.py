"""Show runtime hotspots: symbols where static and runtime rankings disagree."""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


@click.command()
@click.option("--runtime", "sort_runtime", is_flag=True, help="Sort by runtime metrics")
@click.option("--discrepancy", is_flag=True, help="Only show static/runtime mismatches")
@click.pass_context
def hotspots(ctx, sort_runtime, discrepancy):
    """Show runtime hotspots comparing static analysis vs runtime data.

    Requires prior trace ingestion via ``roam ingest-trace``.

    \b
    Classifications:
      UPGRADE   — runtime-critical but statically safe (hidden hotspot)
      CONFIRMED — both static and runtime agree on importance
      DOWNGRADE — statically risky but low traffic
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.runtime.hotspots import compute_hotspots
    from roam.runtime.trace_ingest import ensure_runtime_table

    with open_db(readonly=True) as conn:
        # Ensure table exists for query even in readonly mode
        try:
            conn.execute("SELECT COUNT(*) FROM runtime_stats")
        except Exception:
            if json_mode:
                click.echo(to_json(json_envelope("hotspots",
                    summary={
                        "verdict": "No runtime data. Run `roam ingest-trace` first.",
                        "total": 0, "upgrades": 0, "confirmed": 0, "downgrades": 0,
                    },
                    hotspots=[],
                )))
            else:
                click.echo("VERDICT: No runtime data. Run `roam ingest-trace` first.")
            return

        items = compute_hotspots(conn)

    if discrepancy:
        items = [h for h in items if h["classification"] in ("UPGRADE", "DOWNGRADE")]

    if sort_runtime:
        items.sort(key=lambda h: h["runtime_rank"])

    total = len(items)
    upgrades = sum(1 for h in items if h["classification"] == "UPGRADE")
    confirmed = sum(1 for h in items if h["classification"] == "CONFIRMED")
    downgrades = sum(1 for h in items if h["classification"] == "DOWNGRADE")

    hidden = upgrades
    verdict = f"{total} runtime hotspots ({hidden} hidden -- static analysis missed them)"

    if json_mode:
        click.echo(to_json(json_envelope("hotspots",
            summary={
                "verdict": verdict,
                "total": total,
                "upgrades": upgrades,
                "confirmed": confirmed,
                "downgrades": downgrades,
            },
            hotspots=[
                {
                    "symbol": h["symbol_name"],
                    "file": h["file_path"],
                    "static_rank": h["static_rank"],
                    "runtime_rank": h["runtime_rank"],
                    "classification": h["classification"],
                    "stats": {
                        "runtime": h["runtime_stats"],
                        "static": h["static_stats"],
                    },
                }
                for h in items
            ],
        )))
        return

    # Text output
    click.echo(f"VERDICT: {verdict}\n")

    if not items:
        click.echo("  (no runtime data ingested)")
        return

    for h in items:
        rs = h["runtime_stats"]
        ss = h["static_stats"]
        file_str = h["file_path"] or "-"
        symbol_loc = f"{file_str}::{h['symbol_name']}" if file_str != "-" else h["symbol_name"]

        click.echo(f"  {symbol_loc}")
        click.echo(
            f"    Static:  churn={ss['churn']}, CC={ss['complexity']}, "
            f"PageRank={ss['pagerank']:.4f}  -- ranked #{h['static_rank']}"
        )

        calls_str = f"{rs['call_count']}"
        if rs["call_count"] >= 1000:
            calls_str = f"{rs['call_count'] / 1000:.0f}K" if rs["call_count"] < 1_000_000 else f"{rs['call_count'] / 1_000_000:.1f}M"

        p99_str = f"p99={rs['p99_latency_ms']:.0f}ms" if rs["p99_latency_ms"] is not None else "p99=n/a"
        err_str = f"err={rs['error_rate'] * 100:.1f}%" if rs["error_rate"] else "err=0%"

        click.echo(
            f"    Runtime: {calls_str} calls, {p99_str}, {err_str} "
            f"-- ranked #{h['runtime_rank']}"
        )
        click.echo(f"    >> {h['classification']}")
        click.echo()
