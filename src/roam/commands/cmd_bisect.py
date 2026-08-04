"""Find which snapshots caused architectural degradation.

Walks snapshot history and ranks snapshots by the magnitude of metric
changes between consecutive snapshots. Identifies commits that caused
the biggest structural regressions.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because bisect outputs are invocation-scoped commit rankings
(time-series analysis) — not per-code-location violations. See
action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation
plan + W1197-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

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

    Returns ``(deltas, straddling_pairs)``.

    W1460 — a consecutive pair whose two rows were written by DIFFERENT
    metrics definitions is skipped, and the count is returned so the command
    discloses it. This command's entire output is "which commit did this",
    so attributing a definition change to the commit that happens to sit at
    the boundary is the single most misleading thing it can do — and it is
    the LARGEST delta in the history, so it sorts to rank 1 by construction.
    ``_HIGHER_IS_BETTER["edges"] = True`` makes it worse: the case-fold guard
    removed ~6.5% of the edges, all of them fabricated, and a shrinking
    edge count reads as "degraded". Every user upgrading would have been
    told the fix was their worst regression.
    """
    from roam.commands.metrics_history import LEGACY_METRICS_VERSION

    def _ver(snap):
        value = snap.get("metrics_version")
        return LEGACY_METRICS_VERSION if value is None else value

    deltas = []
    straddling_pairs = 0
    higher_is_better = _HIGHER_IS_BETTER.get(metric, False)

    for i in range(1, len(snapshots)):
        prev = snapshots[i]  # older (snapshots are newest-first)
        curr = snapshots[i - 1]  # newer

        if _ver(prev) != _ver(curr):
            straddling_pairs += 1
            continue

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

        deltas.append(
            {
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
            }
        )

    return deltas, straddling_pairs


@roam_capability(
    name="bisect",
    category="health",
    summary="Find which snapshots caused architectural degradation",
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
@click.command("bisect")
@click.option(
    "--metric",
    default="health_score",
    type=click.Choice(_VALID_METRICS, case_sensitive=False),
    help="Metric to track",
)
@click.option("--threshold", default=None, type=float, help="Flag deltas exceeding this threshold")
@click.option("--top", "top_n", default=10, type=int, help="Show top <N> snapshots")
@click.option(
    "--direction",
    type=click.Choice(["degraded", "improved", "both"]),
    default="degraded",
    help="Which direction to show",
)
@click.pass_context
def bisect(ctx, metric, threshold, top_n, direction):
    """Find which snapshots caused architectural degradation.

    Unlike ``trends`` (which shows metric history as sparklines), this
    command ranks snapshots by degradation magnitude to find which commits
    broke the architecture.

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
            # W1010 Pattern 2: prerequisite missing (need >=2 snapshots). The
            # pre-fix envelope was structurally indistinguishable from
            # "analyzed cleanly with zero deltas" — agents reading only
            # ``summary`` couldn't tell missing-input from stable-metric.
            verdict = "Not enough snapshots for bisect (need >= 2). Run 'roam trends --save' to create them."
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "bisect",
                            summary={
                                "verdict": verdict,
                                "snapshots": len(snapshots),
                                "metric": metric,
                                "deltas": 0,
                                "partial_success": True,
                                "state": "insufficient_snapshots",
                            },
                            hint="Run 'roam trends --save' after each significant change to seed the snapshot history.",
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        deltas, straddling_pairs = _compute_deltas(snapshots, metric)

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
                verdict = f"No degradation found for {metric} across {len(snapshots)} snapshots"
            else:
                verdict = f"No {direction} changes for {metric} across {len(snapshots)} snapshots"
        else:
            worst = deltas[0]
            commit_info = f" (commit {worst['git_commit']})" if worst["git_commit"] else ""
            verdict = f"{len(deltas)} snapshots with {direction} {metric}, worst: {worst['delta']:+.1f}{commit_info}"

        # W1460: blame that skipped a transition must say which transition it
        # skipped, or the history silently has a hole where the upgrade was.
        if straddling_pairs:
            verdict += (
                f"; {straddling_pairs} snapshot transition(s) excluded (metrics definition changed, not the code)"
            )

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "bisect",
                        summary={
                            "verdict": verdict,
                            "metric": metric,
                            "snapshots": len(snapshots),
                            "deltas_found": len(deltas),
                            "direction_filter": direction,
                            **(
                                {
                                    "transitions_skipped_metrics_version": straddling_pairs,
                                    "partial_success": True,
                                }
                                if straddling_pairs
                                else {}
                            ),
                        },
                        deltas=deltas,
                        metric_range={
                            "first": snapshots[-1].get(metric),
                            "last": snapshots[0].get(metric),
                        },
                    )
                )
            )
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if not deltas:
            click.echo(f"  {metric} has been stable across {len(snapshots)} snapshots.")
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
            click.echo(f"  Overall: {first_val} -> {last_val} (total delta: {total:+.1f})")
