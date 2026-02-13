"""Detect health degradation trends and generate actionable alerts."""

import click

from roam.db.connection import open_db
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.metrics_history import collect_metrics, get_snapshots


# ---------------------------------------------------------------------------
# Alert levels
# ---------------------------------------------------------------------------

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

_LEVEL_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "health_score": {"op": "<", "value": 60, "level": CRITICAL},
    "cycles":       {"op": ">", "value": 10, "level": WARNING},
    "god_components": {"op": ">", "value": 5, "level": WARNING},
    "layer_violations": {"op": ">", "value": 0, "level": INFO},
}

_RATE_OF_CHANGE_PCT = 20  # alert if metric changes more than 20%

# Metrics where an increase means degradation
_WORSE_WHEN_HIGHER = {"cycles", "god_components", "bottlenecks",
                      "dead_exports", "layer_violations"}
# Metrics where a decrease means degradation
_WORSE_WHEN_LOWER = {"health_score"}

_TREND_LABELS = {
    "cycles": "Cycle count trending up",
    "health_score": "Health score declining",
    "dead_exports": "Dead code accumulating",
    "bottlenecks": "New bottlenecks emerging",
    "god_components": "God components increasing",
    "layer_violations": "Layer violations growing",
}


# ---------------------------------------------------------------------------
# Alert construction helpers
# ---------------------------------------------------------------------------

def _make_alert(level, metric, message, current_value,
                trend_direction=None):
    alert = {
        "level": level,
        "metric": metric,
        "message": message,
        "current_value": current_value,
    }
    if trend_direction is not None:
        alert["trend_direction"] = trend_direction
    return alert


def _mann_kendall_s(values):
    """Compute the Mann-Kendall S statistic and its significance.

    The Mann-Kendall test is a non-parametric trend test robust to outliers
    and noise.  S > 0 indicates an upward trend; S < 0 indicates downward.

    For n >= 3, we also compute a two-sided p-value using the normal
    approximation of the variance:  Var(S) = n(n-1)(2n+5)/18.

    Returns (S, p_value).  p_value is None for n < 3.
    Reference: Mann (1945), Kendall (1975).
    """
    import math
    n = len(values)
    s = 0
    for i in range(n):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1
    if n < 3:
        return s, None
    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    if var_s == 0:
        return s, 1.0
    std_s = math.sqrt(var_s)
    # Continuity-corrected z
    if s > 0:
        z = (s - 1) / std_s
    elif s < 0:
        z = (s + 1) / std_s
    else:
        z = 0
    # Two-sided p-value via complementary error function
    p = math.erfc(abs(z) / math.sqrt(2))
    return s, p


def _sens_slope(values):
    """Compute Sen's slope estimator: robust trend magnitude.

    slope = median of (xj - xk) / (j - k) for all k < j.

    Unlike linear regression, Sen's slope is resistant to outliers
    and gives a robust estimate of the rate of change per time unit.
    Reference: Sen (1968), "Estimates of the Regression Coefficient
    Based on Kendall's Tau."
    """
    slopes = []
    n = len(values)
    for i in range(n):
        for j in range(i + 1, n):
            slopes.append((values[j] - values[i]) / (j - i))
    if not slopes:
        return 0.0
    slopes.sort()
    mid = len(slopes) // 2
    if len(slopes) % 2 == 0:
        return (slopes[mid - 1] + slopes[mid]) / 2
    return slopes[mid]


def _is_monotonic_worsening(values, metric):
    """Detect statistically significant worsening trends.

    Uses the Mann-Kendall trend test instead of strict monotonicity,
    making detection robust to noise (e.g., [5, 5, 5, 6] is not flagged
    but [5, 7, 8, 12] is).  Requires p < 0.10 for significance.
    """
    if len(values) < 3:
        return False
    s, p = _mann_kendall_s(values)
    if p is None or p >= 0.10:
        return False
    # S > 0 → upward trend; S < 0 → downward trend
    if metric in _WORSE_WHEN_HIGHER:
        return s > 0
    elif metric in _WORSE_WHEN_LOWER:
        return s < 0
    return False


# ---------------------------------------------------------------------------
# Detection routines
# ---------------------------------------------------------------------------

def _check_thresholds(current):
    """Check current metrics against absolute thresholds."""
    alerts = []
    for metric, rule in _THRESHOLDS.items():
        val = current.get(metric)
        if val is None:
            continue
        op, threshold, level = rule["op"], rule["value"], rule["level"]
        triggered = False
        if op == "<" and val < threshold:
            triggered = True
        elif op == ">" and val > threshold:
            triggered = True
        elif op == ">=" and val >= threshold:
            triggered = True
        elif op == "<=" and val <= threshold:
            triggered = True
        if triggered:
            msg = f"below {threshold} threshold" if op == "<" else f"above {threshold} threshold"
            alerts.append(_make_alert(
                level, metric,
                f"{metric}={val} ({msg})",
                val,
            ))
    return alerts


def _check_trends(snapshots_chrono):
    """Detect monotonic degradation over 3+ consecutive snapshots.

    *snapshots_chrono* is a list of snapshot dicts ordered oldest-first.
    """
    alerts = []
    if len(snapshots_chrono) < 3:
        return alerts

    tracked = list(_WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER)
    for metric in tracked:
        values = [s.get(metric, 0) or 0 for s in snapshots_chrono]
        # Check the last 3..N window sizes for a monotonic run
        for window in range(len(values), 2, -1):
            tail = values[-window:]
            if _is_monotonic_worsening(tail, metric):
                current = tail[-1]
                arrow = " -> ".join(str(v) for v in tail)
                label = _TREND_LABELS.get(metric, f"{metric} worsening")
                # Sen's slope: robust rate of change per snapshot
                slope = _sens_slope(tail)
                slope_str = f", rate={slope:+.1f}/snapshot" if abs(slope) >= 0.1 else ""
                alerts.append(_make_alert(
                    WARNING, metric,
                    f"{label}: {arrow} over {window} snapshots{slope_str}",
                    current,
                    trend_direction="up" if metric in _WORSE_WHEN_HIGHER else "down",
                ))
                break  # largest matching window is enough
    return alerts


def _check_rate_of_change(snapshots_chrono):
    """Alert if a metric changed more than _RATE_OF_CHANGE_PCT between the
    last two consecutive snapshots."""
    alerts = []
    if len(snapshots_chrono) < 2:
        return alerts

    prev = snapshots_chrono[-2]
    curr = snapshots_chrono[-1]

    tracked = list(_WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER)
    for metric in tracked:
        prev_val = prev.get(metric, 0) or 0
        curr_val = curr.get(metric, 0) or 0
        if prev_val == 0:
            # Can't compute percentage change from zero.
            # But if the metric appeared from nothing, that is notable.
            if curr_val > 0 and metric in _WORSE_WHEN_HIGHER:
                alerts.append(_make_alert(
                    INFO, metric,
                    f"{metric}={curr_val} (new since last snapshot)",
                    curr_val,
                    trend_direction="up",
                ))
            elif curr_val < prev_val and metric in _WORSE_WHEN_LOWER:
                alerts.append(_make_alert(
                    INFO, metric,
                    f"{metric}={curr_val} (new since last snapshot)",
                    curr_val,
                    trend_direction="down",
                ))
            continue

        pct = abs(curr_val - prev_val) / abs(prev_val) * 100
        if pct <= _RATE_OF_CHANGE_PCT:
            continue

        # Only alert if change is in the worsening direction
        worsening = False
        if metric in _WORSE_WHEN_HIGHER and curr_val > prev_val:
            worsening = True
        elif metric in _WORSE_WHEN_LOWER and curr_val < prev_val:
            worsening = True

        if worsening:
            direction = "increased" if curr_val > prev_val else "decreased"
            alerts.append(_make_alert(
                WARNING, metric,
                f"{metric}={curr_val} ({direction} {pct:.0f}% since last snapshot)",
                curr_val,
                trend_direction="up" if curr_val > prev_val else "down",
            ))
    return alerts


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(alerts):
    """Remove duplicate alerts for the same metric, keeping the highest severity."""
    seen = {}
    for a in alerts:
        key = (a["metric"], a.get("trend_direction"))
        if key not in seen or _LEVEL_ORDER[a["level"]] < _LEVEL_ORDER[seen[key]["level"]]:
            seen[key] = a
    # Return sorted: CRITICAL first, then WARNING, then INFO
    return sorted(seen.values(), key=lambda a: (_LEVEL_ORDER[a["level"]], a["metric"]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.pass_context
def alerts(ctx):
    """Detect health degradation trends and generate actionable alerts.

    Analyzes snapshot history to find:
    - Metrics that consistently worsen over 3+ snapshots
    - Current values that exceed severity thresholds
    - Metrics that changed more than 20% since the last snapshot
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    all_alerts = []

    with open_db(readonly=True) as conn:
        # Fetch snapshot history (newest first from DB)
        snaps_raw = get_snapshots(conn)

        # Build chronological list of snapshot dicts (oldest first)
        snap_dicts = []
        for s in reversed(snaps_raw):
            snap_dicts.append({
                "timestamp": s["timestamp"],
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

        if snap_dicts:
            # Use the most recent snapshot as "current" metrics
            current = snap_dicts[-1]
        else:
            # No snapshots at all -- compute live metrics
            current = collect_metrics(conn)

        # 1) Threshold checks (always run)
        all_alerts.extend(_check_thresholds(current))

        # 2) Trend detection (need >= 3 snapshots)
        if len(snap_dicts) >= 3:
            all_alerts.extend(_check_trends(snap_dicts))

        # 3) Rate-of-change detection (need >= 2 snapshots)
        if len(snap_dicts) >= 2:
            all_alerts.extend(_check_rate_of_change(snap_dicts))

    # Deduplicate and sort
    all_alerts = _deduplicate(all_alerts)

    # Count by level
    counts = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for a in all_alerts:
        counts[a["level"]] += 1

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope("alerts",
            summary={
                "total": len(all_alerts),
                "critical": counts[CRITICAL],
                "warning": counts[WARNING],
                "info": counts[INFO],
                "snapshots_analyzed": len(snap_dicts),
            },
            alerts=all_alerts,
        )))
        return

    # --- Text output ---
    if not all_alerts:
        click.echo("No health alerts. All metrics are within normal ranges.")
        return

    click.echo("Health alerts:\n")

    for a in all_alerts:
        level_str = a["level"].ljust(9)
        click.echo(f"  {level_str} {a['message']}")

    click.echo()

    # Summary line
    parts = []
    if counts[CRITICAL]:
        parts.append(f"{counts[CRITICAL]} critical")
    if counts[WARNING]:
        parts.append(f"{counts[WARNING]} warning{'s' if counts[WARNING] != 1 else ''}")
    if counts[INFO]:
        parts.append(f"{counts[INFO]} info")
    click.echo(", ".join(parts))
