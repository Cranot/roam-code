"""Display health history with sparklines and CI assertions."""

import re
import time
from datetime import datetime, timezone

import click

from roam.db.connection import open_db
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.metrics_history import get_snapshots


# ---------------------------------------------------------------------------
# Sparkline rendering
# ---------------------------------------------------------------------------

_SPARKS = "▁▂▃▄▅▆▇█"


def _sparkline(values):
    """Render a list of numbers as a terminal sparkline."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    return "".join(
        _SPARKS[min(len(_SPARKS) - 1, int((v - mn) / rng * (len(_SPARKS) - 1)))]
        for v in values
    )


# ---------------------------------------------------------------------------
# Assertion engine
# ---------------------------------------------------------------------------

_ASSERT_RE = re.compile(r"(\w+)\s*(<=|>=|==|!=|<|>)\s*(\d+)")
_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
}


def _check_assertions(assertions_str, snap):
    """Check CI assertions against a snapshot. Returns list of failure strings."""
    failures = []
    for expr in assertions_str.split(","):
        expr = expr.strip()
        if not expr:
            continue
        m = _ASSERT_RE.match(expr)
        if not m:
            failures.append(f"invalid expression: {expr}")
            continue
        metric, op, threshold = m.group(1), m.group(2), int(m.group(3))
        actual = snap.get(metric)
        if actual is None:
            failures.append(f"unknown metric: {metric}")
            continue
        if not _OPS[op](actual, threshold):
            failures.append(f"{metric}={actual} (expected {op}{threshold})")
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--range", "count", default=10, help="Number of snapshots to show")
@click.option("--since", "since_date", default=None,
              help="Only show snapshots after this date (YYYY-MM-DD)")
@click.option("--assert", "assertions", default=None,
              help="CI gate: comma-separated expressions (e.g. 'cycles<=5,dead_exports<=20')")
@click.pass_context
def trend(ctx, count, since_date, assertions):
    """Display health trend with sparklines and CI gate assertions.

    Shows historical snapshots from `roam index` and `roam snapshot`.
    Use --assert for CI pipelines to enforce quality thresholds.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    since_ts = None
    if since_date:
        try:
            dt = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            since_ts = int(dt.timestamp())
        except ValueError:
            click.echo(f"Invalid date format: {since_date} (use YYYY-MM-DD)")
            raise SystemExit(1)

    with open_db(readonly=True) as conn:
        snaps = get_snapshots(conn, limit=count, since=since_ts)

        if not snaps:
            if json_mode:
                click.echo(to_json(json_envelope("trend",
                    summary={"snapshots": 0},
                    snapshots=[],
                )))
            else:
                click.echo("No snapshots found. Run `roam index` or `roam snapshot` first.")
            return

        # Convert to dicts for easier access
        snap_dicts = []
        for s in snaps:
            snap_dicts.append({
                "timestamp": s["timestamp"],
                "tag": s["tag"],
                "source": s["source"],
                "git_branch": s["git_branch"],
                "git_commit": s["git_commit"],
                "files": s["files"],
                "symbols": s["symbols"],
                "edges": s["edges"],
                "cycles": s["cycles"],
                "god_components": s["god_components"],
                "bottlenecks": s["bottlenecks"],
                "dead_exports": s["dead_exports"],
                "layer_violations": s["layer_violations"],
                "health_score": s["health_score"],
            })

        # Reverse for chronological order (oldest first for sparklines)
        chrono = list(reversed(snap_dicts))

        # --- Assertions ---
        assertion_results = []
        if assertions:
            latest = snap_dicts[0]  # newest first
            assertion_results = _check_assertions(assertions, latest)

        if json_mode:
            envelope = json_envelope("trend",
                summary={
                    "snapshots": len(snap_dicts),
                    "latest_health": snap_dicts[0]["health_score"] if snap_dicts else None,
                },
                snapshots=snap_dicts,
            )
            if assertions:
                envelope["assertions"] = {
                    "expression": assertions,
                    "passed": len(assertion_results) == 0,
                    "failures": assertion_results,
                }
            click.echo(to_json(envelope))
            if assertion_results:
                raise SystemExit(1)
            return

        # --- Text output ---
        click.echo(f"=== Health Trend (last {len(snap_dicts)} snapshots) ===\n")

        # Table
        rows = []
        for s in snap_dicts:
            dt = datetime.fromtimestamp(s["timestamp"], tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
            tag = s["tag"] or f"({s['source']})"
            rows.append([
                date_str, tag,
                str(s["health_score"]),
                str(s["cycles"]),
                str(s["god_components"]),
                str(s["bottlenecks"]),
                str(s["dead_exports"]),
                str(s["layer_violations"]),
            ])
        click.echo(format_table(
            ["Date", "Tag", "Score", "Cycles", "Gods", "BN", "Dead", "Violations"],
            rows,
        ))

        # Sparklines (chronological order)
        if len(chrono) >= 2:
            click.echo("\nSparklines:")
            metrics = [
                ("Score", "health_score"),
                ("Cycles", "cycles"),
                ("Gods", "god_components"),
                ("Dead", "dead_exports"),
                ("Violations", "layer_violations"),
            ]
            for label, key in metrics:
                vals = [s[key] or 0 for s in chrono]
                spark = _sparkline(vals)
                mn, mx = min(vals), max(vals)
                click.echo(f"  {label:<12s} {spark}  (range: {mn}-{mx})")

        # Assertions
        if assertions:
            click.echo()
            if assertion_results:
                click.echo(f"ASSERTIONS FAILED ({len(assertion_results)}):")
                for f in assertion_results:
                    click.echo(f"  FAIL: {f}")
                raise SystemExit(1)
            else:
                click.echo("All assertions passed.")
