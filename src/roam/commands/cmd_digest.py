"""Generate a narrative digest comparing current metrics to the last snapshot."""

from datetime import datetime, timezone

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.metrics_history import collect_metrics, get_snapshots


_METRICS = [
    ("health_score",      "Health score"),
    ("files",             "Files"),
    ("symbols",           "Symbols"),
    ("cycles",            "Cycles"),
    ("god_components",    "God components"),
    ("bottlenecks",       "Bottlenecks"),
    ("dead_exports",      "Dead exports"),
    ("layer_violations",  "Violations"),
]

# Metrics where a decrease is good (lower = healthier)
_LOWER_IS_BETTER = frozenset({
    "cycles", "god_components", "bottlenecks",
    "dead_exports", "layer_violations",
})


def _arrow(key, delta):
    """Return ▲ for improvement, ▼ for regression, empty for no change."""
    if delta == 0:
        return ""
    if key in _LOWER_IS_BETTER:
        return "\u25b2" if delta < 0 else "\u25bc"
    # Higher is better (health_score, files, symbols, edges)
    return "\u25b2" if delta > 0 else "\u25bc"


def _delta_str(delta):
    """Format a delta value with sign."""
    if delta == 0:
        return "="
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}"


def _build_recommendations(current, previous, deltas):
    """Generate actionable recommendations based on metric changes."""
    recs = []

    new_dead = deltas.get("dead_exports", 0)
    if new_dead > 0:
        recs.append(f"Run `roam dead --summary` to review {new_dead} new dead export{'s' if new_dead != 1 else ''}")

    new_cycles = deltas.get("cycles", 0)
    if new_cycles > 0:
        recs.append(f"Run `roam health` to inspect {new_cycles} new cycle{'s' if new_cycles != 1 else ''}")

    new_gods = deltas.get("god_components", 0)
    if new_gods > 0:
        recs.append(f"Run `roam health --no-framework` to review {new_gods} new god component{'s' if new_gods != 1 else ''}")

    new_violations = deltas.get("layer_violations", 0)
    if new_violations > 0:
        recs.append(f"Run `roam layers` to review {new_violations} new layer violation{'s' if new_violations != 1 else ''}")

    score_drop = deltas.get("health_score", 0)
    if score_drop < -5:
        recs.append(f"Health dropped by {abs(score_drop)} points — run `roam health` for details")

    if not recs:
        if deltas.get("health_score", 0) > 0:
            recs.append("Health is improving — keep it up!")
        else:
            recs.append("No significant changes detected")

    return recs


@click.command("digest")
@click.option("--root", default=".", help="Project root")
@click.option("--since", default=None, help="Compare against snapshot tag")
@click.option("--brief", is_flag=True, help="One-line summary")
@click.pass_context
def digest(ctx, root, since, brief):
    """Compare current metrics against the most recent snapshot.

    Shows deltas with directional arrows and recommendations.
    Use --since to compare against a specific tagged snapshot.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        current = collect_metrics(conn)

        # Find the comparison snapshot
        snaps = get_snapshots(conn, limit=50)
        if not snaps:
            if json_mode:
                click.echo(to_json(json_envelope("digest",
                    summary={"error": "No snapshots found"},
                    current=current,
                    previous=None,
                    deltas=None,
                )))
            else:
                click.echo("No snapshots found. Run `roam snapshot` first to create a baseline.")
            return

        # Pick the right snapshot for comparison
        previous = None
        if since:
            for s in snaps:
                if s["tag"] == since:
                    previous = dict(s)
                    break
            if previous is None:
                tags = [s["tag"] for s in snaps if s["tag"]]
                tag_list = ", ".join(tags[:10]) if tags else "(none)"
                if json_mode:
                    click.echo(to_json(json_envelope("digest",
                        summary={"error": f"Tag '{since}' not found"},
                        available_tags=tags[:20],
                    )))
                else:
                    click.echo(f"Tag '{since}' not found. Available tags: {tag_list}")
                return
        else:
            previous = dict(snaps[0])

        # Compute deltas
        deltas = {}
        for key, _label in _METRICS:
            cur_val = current.get(key, 0) or 0
            prev_val = previous.get(key, 0) or 0
            deltas[key] = cur_val - prev_val

        recommendations = _build_recommendations(current, previous, deltas)

        # Format snapshot date
        snap_ts = previous.get("timestamp", 0)
        snap_date = datetime.fromtimestamp(snap_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        snap_tag = previous.get("tag")
        snap_label = f"{snap_date}"
        if snap_tag:
            snap_label += f" [{snap_tag}]"

    if json_mode:
        click.echo(to_json(json_envelope("digest",
            summary={
                "health_score": current.get("health_score"),
                "previous_health_score": previous.get("health_score"),
                "health_delta": deltas.get("health_score", 0),
                "snapshot_date": snap_date,
                "snapshot_tag": snap_tag,
            },
            current=current,
            previous={k: previous.get(k) for k, _ in _METRICS},
            deltas=deltas,
            recommendations=recommendations,
        )))
        return

    # Brief mode: single line
    if brief:
        score = current.get("health_score", 0)
        prev_score = previous.get("health_score", 0)
        d = score - prev_score
        arrow = _arrow("health_score", d)
        click.echo(f"Health: {prev_score} \u2192 {score} ({_delta_str(d)}) {arrow}  (vs {snap_label})")
        return

    # Full text output
    click.echo(f"Digest (vs {snap_label} snapshot):\n")

    max_label = max(len(label) for _, label in _METRICS)
    for key, label in _METRICS:
        cur_val = current.get(key, 0) or 0
        prev_val = previous.get(key, 0) or 0
        d = deltas[key]
        arrow = _arrow(key, d)
        padded = label.ljust(max_label)
        click.echo(f"  {padded}  {prev_val} \u2192 {cur_val} ({_delta_str(d)}) {arrow}")

    if recommendations:
        click.echo("\nRecommendations:")
        for rec in recommendations:
            click.echo(f"  \u2022 {rec}")
