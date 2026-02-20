"""Find which snapshots caused architectural degradation.

Walks snapshot history and ranks snapshots by the magnitude of metric
changes between consecutive snapshots. Identifies commits that caused
the biggest structural regressions.
"""

from __future__ import annotations

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


_HIGHER_IS_BETTER = {
    "health_score": True,
    "files": True,
    "symbols": True,
    "edges": True,
    "cycles": False,
    "god_components": False,
    "bottlenecks": False,
    "dead_exports": False,
    "layer_violations": False,
    "tangle_ratio": False,
    "avg_complexity": False,
    "brain_methods": False,
}

_VALID_METRICS = list(_HIGHER_IS_BETTER.keys())


def _compute_deltas(snapshots, metric):
    """Compare consecutive snapshots and compute deltas.

    snapshots is ordered newest-first, so snapshots[0] is the most recent
    and snapshots[-1] is the oldest.
    """
    deltas = []
    higher_is_better = _HIGHER_IS_BETTER.get(metric, False)

    for i in range(1, len(snapshots)):
        prev = snapshots[i]      # older (snapshots are newest-first)
        curr = snapshots[i - 1]  # newer

        prev_val = prev.get(metric)
        curr_val = curr.get(metric)

        if prev_val is None or curr_val is None:
            continue

        prev_val = float(prev_val)
        curr_val = float(curr_val)
        delta = curr_val - prev_val

        # Determine direction
        if delta == 0:
            direction = "unchanged"
        elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
            direction = "improved"
        else:
            direction = "degraded"

        deltas.append({
            "snapshot_id": curr.get("id"),
            "timestamp": curr.get("timestamp"),
            "tag": curr.get("tag") or "",
            "git_commit": curr.get("git_commit") or "",
            "git_branch": curr.get("git_branch") or "",
            "before": prev_val,
            "after": curr_val,
            "delta": round(delta, 2),
            "abs_delta": round(abs(delta), 2),
            "direction": direction,
        })

    return deltas


@click.command("bisect")
@click.option("--metric", default="health_score",
              type=click.Choice(_VALID_METRICS, case_sensitive=False),
              help="Metric to track")
@click.option("--threshold", default=None, type=float,
              help="Flag deltas exceeding this threshold")
@click.option("--top", "top_n", default=10, type=int, help="Show top N snapshots")
@click.option("--direction", type=click.Choice(["degraded", "improved", "both"]),
              default="degraded", help="Which direction to show")
@click.pass_context
def bisect(ctx, metric, threshold, top_n, direction):
    """Find which snapshots caused architectural degradation.

    Walks the snapshot history and ranks snapshots by the magnitude of
    metric changes. Identifies the commits that caused the biggest
    structural regressions.

    \b
    Examples:
      roam bisect                          # health score blame
      roam bisect --metric cycles          # who introduced cycles
      roam bisect --metric avg_complexity  # complexity blame
      roam bisect --threshold 5            # only big changes
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        from roam.commands.metrics_history import get_snapshots

        snapshots_raw = get_snapshots(conn)
        snapshots = [dict(s) for s in snapshots_raw]

        if len(snapshots) < 2:
            verdict = (
                "Not enough snapshots for bisect (need >= 2). "
                "Run 'roam snapshot' to create them."
            )
            if json_mode:
                click.echo(to_json(json_envelope("bisect",
                    summary={
                        "verdict": verdict,
                        "snapshots": len(snapshots),
                        "metric": metric,
                        "deltas": 0,
                    })))
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        deltas = _compute_deltas(snapshots, metric)

        # Filter by direction
        if direction == "degraded":
            deltas = [d for d in deltas if d["direction"] == "degraded"]
        elif direction == "improved":
            deltas = [d for d in deltas if d["direction"] == "improved"]

        # Filter by threshold
        if threshold is not None:
            deltas = [d for d in deltas if d["abs_delta"] >= threshold]

        # Sort by absolute delta descending, then slice
        deltas.sort(key=lambda d: -d["abs_delta"])
        deltas = deltas[:top_n]

        # Build verdict
        if not deltas:
            if direction == "degraded":
                verdict = (
                    f"No degradation found for {metric} across {len(snapshots)} snapshots"
                )
            else:
                verdict = (
                    f"No {direction} changes for {metric} across {len(snapshots)} snapshots"
                )
        else:
            worst = deltas[0]
            commit_info = f" (commit {worst['git_commit']})" if worst["git_commit"] else ""
            verdict = (
                f"{len(deltas)} snapshots with {direction} {metric}, "
                f"worst: {worst['delta']:+.1f}{commit_info}"
            )

        if json_mode:
            click.echo(to_json(json_envelope("bisect",
                summary={
                    "verdict": verdict,
                    "metric": metric,
                    "snapshots": len(snapshots),
                    "deltas_found": len(deltas),
                    "direction_filter": direction,
                },
                deltas=deltas,
                metric_range={
                    "first": snapshots[-1].get(metric),
                    "last": snapshots[0].get(metric),
                },
            )))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if not deltas:
            click.echo(
                f"  {metric} has been stable across {len(snapshots)} snapshots."
            )
            return

        click.echo(f"BISECT LOG ({metric}, {direction}):")
        for i, d in enumerate(deltas, 1):
            tag_str = f" [{d['tag']}]" if d["tag"] else ""
            commit_str = f" {d['git_commit']}" if d["git_commit"] else ""
            marker = " << WORST" if i == 1 else ""
            click.echo(
                f"  {i}. {d['before']} -> {d['after']}  "
                f"(delta: {d['delta']:+.1f}){commit_str}{tag_str}  "
                f"{d['direction'].upper()}{marker}"
            )

        # Summary
        click.echo()
        first_val = snapshots[-1].get(metric)
        last_val = snapshots[0].get(metric)
        if first_val is not None and last_val is not None:
            total = float(last_val) - float(first_val)
            click.echo(
                f"  Overall: {first_val} -> {last_val} (total delta: {total:+.1f})"
            )
