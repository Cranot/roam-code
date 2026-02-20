"""Predict when per-symbol metrics will exceed thresholds by analyzing trends.

Uses Theil-Sen regression on snapshot history for aggregate metric forecasting
and ranks symbols by cognitive complexity * churn rate for per-symbol risk.
"""

from __future__ import annotations

import os

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Thresholds for aggregate snapshot metrics
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "health_score":   {"warning": 60, "critical": 40, "higher_is_better": True},
    "avg_complexity": {"warning": 20, "critical": 30, "higher_is_better": False},
    "cycles":         {"warning": 5,  "critical": 10, "higher_is_better": False},
    "brain_methods":  {"warning": 5,  "critical": 10, "higher_is_better": False},
    "god_components": {"warning": 3,  "critical": 5,  "higher_is_better": False},
    "dead_exports":   {"warning": 20, "critical": 50, "higher_is_better": False},
}

# Minimum absolute slope to consider a metric "trending" at all.
_MIN_ABS_SLOPE = 0.05


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_status(current, slope, horizon, metric_cfg):
    """Return one of: stable / trending / warning / alert.

    For higher_is_better metrics, alert when forecast drops below thresholds.
    For lower_is_better metrics, alert when forecast rises above thresholds.
    """
    if abs(slope) < _MIN_ABS_SLOPE:
        return "stable"

    forecast_val = current + slope * horizon
    hib = metric_cfg["higher_is_better"]
    warn_thresh = metric_cfg["warning"]
    crit_thresh = metric_cfg["critical"]

    # Already exceeded the critical threshold right now
    if hib:
        if current <= crit_thresh:
            return "alert"
        if current <= warn_thresh:
            return "warning"
    else:
        if current >= crit_thresh:
            return "alert"
        if current >= warn_thresh:
            return "warning"

    # Will exceed threshold within the horizon?
    if hib:
        if forecast_val <= crit_thresh:
            return "alert"
        if forecast_val <= warn_thresh:
            return "warning"
    else:
        if forecast_val >= crit_thresh:
            return "alert"
        if forecast_val >= warn_thresh:
            return "warning"

    return "trending"


def _aggregate_forecasts(conn, horizon):
    """Compute Theil-Sen trend + forecast for each snapshot metric.

    Returns a list of dicts, one per tracked metric.  Skips metrics that
    have fewer than 4 non-None values (Theil-Sen requires n >= 4).
    """
    from roam.graph.anomaly import theil_sen_slope

    rows = conn.execute(
        "SELECT timestamp, health_score, avg_complexity, cycles, "
        "       god_components, bottlenecks, dead_exports, brain_methods "
        "FROM snapshots ORDER BY timestamp ASC"
    ).fetchall()

    if len(rows) < 3:
        return [], len(rows)

    results = []
    for metric, cfg in _THRESHOLDS.items():
        values = [r[metric] for r in rows if r[metric] is not None]
        if len(values) < 4:
            # Not enough history -- still report current value as stable
            current = values[-1] if values else None
            if current is None:
                continue
            results.append({
                "metric": metric,
                "current": round(float(current), 2),
                "slope": 0.0,
                "forecast_value": round(float(current), 2),
                "forecast_horizon": horizon,
                "status": "stable",
            })
            continue

        ts_result = theil_sen_slope(values)
        if ts_result is None:
            continue

        slope = ts_result["slope"]
        current = values[-1]
        forecast_val = current + slope * horizon
        status = _classify_status(current, slope, horizon, cfg)

        results.append({
            "metric": metric,
            "current": round(float(current), 2),
            "slope": round(slope, 4),
            "forecast_value": round(forecast_val, 2),
            "forecast_horizon": horizon,
            "status": status,
        })

    return results, len(rows)


def _at_risk_symbols(conn, symbol_filter, min_slope, limit=20):
    """Rank symbols by cognitive_complexity * normalized_churn.

    High-complexity code in high-churn files is most likely to degrade
    further because each commit has a chance of adding more complexity.

    Returns a list of dicts sorted by risk_score descending.
    """
    # Fetch symbol metrics
    sym_rows = conn.execute(
        """SELECT s.id, s.name, s.qualified_name, s.kind, f.path,
                  s.line_start, sm.cognitive_complexity
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           LEFT JOIN symbol_metrics sm ON sm.symbol_id = s.id
           WHERE s.kind IN ('function', 'method')
             AND sm.cognitive_complexity IS NOT NULL
             AND sm.cognitive_complexity > 0
           ORDER BY sm.cognitive_complexity DESC"""
    ).fetchall()

    if not sym_rows:
        return []

    # Fetch churn per file
    churn_rows = conn.execute(
        "SELECT file_id, total_churn FROM file_stats"
    ).fetchall()
    churn_map = {r["file_id"]: (r["total_churn"] or 0) for r in churn_rows}

    # Fetch file id lookup
    file_id_rows = conn.execute("SELECT id, path FROM files").fetchall()
    file_id_map = {r["path"]: r["id"] for r in file_id_rows}

    # Compute max churn for normalization (avoid division by zero)
    max_churn = max(churn_map.values(), default=1) or 1

    results = []
    for r in sym_rows:
        # Apply symbol name filter
        if symbol_filter:
            name = r["name"] or ""
            qname = r["qualified_name"] or ""
            filt = symbol_filter.lower()
            if filt not in name.lower() and filt not in qname.lower():
                continue

        file_id = file_id_map.get(r["path"])
        churn = churn_map.get(file_id, 0)
        cc = float(r["cognitive_complexity"] or 0)
        churn_norm = churn / max_churn  # 0.0 – 1.0

        # Risk score: CC scaled by churn factor
        risk_score = cc * (1.0 + churn_norm)

        # Skip symbols below the min-slope proxy threshold
        # (min_slope acts as a minimum risk coefficient — symbols
        #  with negligible cc*churn are not worth reporting)
        if risk_score < min_slope * 10:
            continue

        results.append({
            "name": r["name"],
            "qualified_name": r["qualified_name"] or r["name"],
            "kind": r["kind"],
            "file": r["path"],
            "line": r["line_start"],
            "cognitive_complexity": round(cc, 1),
            "churn": churn,
            "risk_score": round(risk_score, 1),
        })

    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("forecast")
@click.option("--symbol", default=None, help="Filter to a specific symbol name")
@click.option(
    "--horizon", default=30, type=int, show_default=True,
    help="Look-ahead window in snapshots/commits",
)
@click.option(
    "--alert-only", "alert_only", is_flag=True,
    help="Show only metrics with non-stable status",
)
@click.option(
    "--min-slope", "min_slope", default=0.1, type=float, show_default=True,
    help="Minimum slope (or risk coefficient) to report",
)
@click.pass_context
def forecast(ctx, symbol, horizon, alert_only, min_slope):
    """Predict when metrics will exceed thresholds using trend analysis.

    Combines Theil-Sen regression on snapshot history (aggregate trends)
    with a churn-weighted complexity ranking (per-symbol risk) to surface
    the most likely future pain points.

    With --alert-only, only metrics trending toward warning/alert thresholds
    are shown, suppressing stable metrics from the output.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        aggregate_result = _aggregate_forecasts(conn, horizon)
        # _aggregate_forecasts returns (trends_list, snapshot_count)
        # when there are >= 3 snapshots, otherwise ([], n) for n < 3
        if isinstance(aggregate_result, tuple):
            agg_trends, n_snapshots = aggregate_result
        else:
            agg_trends, n_snapshots = aggregate_result, 0

        # Apply alert-only filter on aggregate trends
        if alert_only:
            agg_trends = [t for t in agg_trends if t["status"] != "stable"]

        # Apply min-slope filter on aggregate trends
        if min_slope > 0:
            agg_trends = [
                t for t in agg_trends
                if abs(t["slope"]) >= min_slope or t["status"] in ("warning", "alert")
            ]

        at_risk = _at_risk_symbols(conn, symbol, min_slope)

    # Summary counts
    metrics_trending = sum(
        1 for t in agg_trends if t["status"] in ("trending", "warning", "alert")
    )
    symbols_at_risk = len(at_risk)

    # Build verdict
    parts = []
    if n_snapshots < 3:
        parts.append("insufficient snapshot history for aggregate trends")
    elif metrics_trending:
        parts.append(
            f"{metrics_trending} metric{'s' if metrics_trending != 1 else ''} "
            f"trending toward threshold{'s' if metrics_trending != 1 else ''}"
        )
    else:
        parts.append("all aggregate metrics stable")

    if symbols_at_risk:
        parts.append(
            f"{symbols_at_risk} symbol{'s' if symbols_at_risk != 1 else ''} at risk"
        )
    else:
        parts.append("no high-risk symbols found")

    verdict = ", ".join(parts)

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "forecast",
            summary={
                "verdict": verdict,
                "snapshots_available": n_snapshots,
                "metrics_trending": metrics_trending,
                "symbols_at_risk": symbols_at_risk,
            },
            aggregate_trends=agg_trends,
            at_risk_symbols=at_risk,
        )))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    # Aggregate trends section
    if n_snapshots < 3:
        click.echo(
            f"AGGREGATE TRENDS: insufficient snapshot history "
            f"({n_snapshots} snapshot{'s' if n_snapshots != 1 else ''} available, "
            f"need >= 3)"
        )
    else:
        click.echo(f"AGGREGATE TRENDS (from {n_snapshots} snapshots):")
        if not agg_trends:
            click.echo("  all metrics stable")
        else:
            for t in agg_trends:
                status = t["status"].upper()
                slope_str = f"{t['slope']:+.4f}/snapshot"
                forecast_note = (
                    f"forecast {t['forecast_value']:.1f} in {t['forecast_horizon']} snapshots"
                )
                flag = ""
                if t["status"] == "warning":
                    flag = "  << WARNING"
                elif t["status"] == "alert":
                    flag = "  << ALERT"
                click.echo(
                    f"  {t['metric']:<18s}  {t['current']:.1f}, "
                    f"slope {slope_str}, {forecast_note}{flag}"
                )

    click.echo()

    # At-risk symbols section
    if symbol:
        click.echo(f"AT-RISK SYMBOLS (filtered to '{symbol}'):")
    else:
        click.echo("AT-RISK SYMBOLS (high complexity in high-churn files):")

    if not at_risk:
        click.echo("  no high-risk symbols found")
    else:
        for s in at_risk:
            kind_abbr = abbrev_kind(s["kind"])
            fname = os.path.basename(s["file"])
            line = s["line"] or 0
            click.echo(
                f"  {kind_abbr} {s['name']:<30s}  "
                f"CC={s['cognitive_complexity']:.0f}  "
                f"churn={s['churn']}  "
                f"score={s['risk_score']:.1f}  "
                f"{fname}:{line}"
            )
