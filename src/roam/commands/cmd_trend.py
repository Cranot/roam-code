"""Display health history with sparklines, CI assertions, and anomaly detection."""

from __future__ import annotations

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
# Anomaly detection integration
# ---------------------------------------------------------------------------

# Metrics classified by type for appropriate analysis
_QUALITY_METRICS = ["cycles", "god_components", "bottlenecks", "dead_exports", "layer_violations"]
_GROWTH_METRICS = ["files", "symbols", "edges"]
_COMPOSITE_METRICS = ["health_score"]

# Sensitivity presets: (z_threshold, we_sigma_mult)
_SENSITIVITY = {
    "low": (4.0, 1.2),
    "medium": (3.5, 1.0),
    "high": (3.0, 0.8),
}


def _analyze_trends(chrono, sensitivity="medium"):
    """Run full anomaly + trend analysis on chronological snapshots.

    Returns dict with anomalies, trends, forecasts, patterns.
    """
    from roam.graph.anomaly import (
        modified_z_score, theil_sen_slope, mann_kendall_test,
        western_electric_rules, forecast,
    )

    z_thresh, _ = _SENSITIVITY.get(sensitivity, _SENSITIVITY["medium"])
    all_metrics = _QUALITY_METRICS + _GROWTH_METRICS + _COMPOSITE_METRICS

    anomalies = []
    trends = []
    forecasts = []
    patterns = []

    for metric in all_metrics:
        values = [s.get(metric) or 0 for s in chrono]
        if len(values) < 4:
            continue

        # Point anomaly detection
        z_results = modified_z_score(values, threshold=z_thresh)
        for r in z_results:
            if r["is_anomaly"]:
                anomalies.append({
                    "metric": metric,
                    "index": r["index"],
                    "value": r["value"],
                    "z_score": round(r["z_score"], 2),
                    "typical": f"{r.get('median', 0):.0f}",
                })

        # Trend estimation
        ts = theil_sen_slope(values)
        if ts:
            entry = {
                "metric": metric,
                "slope": round(ts["slope"], 3),
                "direction": ts["direction"],
            }
            # Add significance if enough data
            if len(values) >= 8:
                mk = mann_kendall_test(values)
                if mk:
                    entry["p_value"] = round(mk["p_value"], 4)
                    entry["significant"] = mk["significant"]
            trends.append(entry)

            # Forecasting for quality metrics (bad = increasing)
            if metric in _QUALITY_METRICS and ts["direction"] == "increasing":
                # Forecast when metric doubles from current
                current = values[-1]
                target = max(current * 2, current + 10)
                fc = forecast(values, target=target)
                if fc and fc.get("steps_until"):
                    forecasts.append({
                        "metric": metric,
                        "current": current,
                        "target": target,
                        "slope": round(fc["slope"], 2),
                        "snapshots_until": fc["steps_until"],
                    })

        # Pattern detection
        we_results = western_electric_rules(values)
        for r in we_results:
            patterns.append({
                "metric": metric,
                "rule": r["rule"],
                "description": r["description"],
                "indices": r.get("indices", []),
            })

    return {
        "anomalies": anomalies,
        "trends": trends,
        "forecasts": forecasts,
        "patterns": patterns,
    }


def _trend_verdict(analysis):
    """Derive a verdict from analysis results."""
    n_anomalies = len(analysis["anomalies"])
    n_patterns = len(analysis["patterns"])

    # Check for degrading quality trends
    degrading = [
        t for t in analysis["trends"]
        if t["metric"] in _QUALITY_METRICS
        and t["direction"] == "increasing"
        and t.get("significant", True)
    ]
    improving = [
        t for t in analysis["trends"]
        if t["metric"] in _COMPOSITE_METRICS
        and t["direction"] == "increasing"
        and t.get("significant", True)
    ]

    if n_anomalies > 2 or len(degrading) > 2:
        return "degrading"
    if n_anomalies > 0 or len(degrading) > 0 or n_patterns > 2:
        return "warning"
    if improving:
        return "improving"
    return "stable"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--range", "count", default=10, help="Number of snapshots to show")
@click.option("--since", "since_date", default=None,
              help="Only show snapshots after this date (YYYY-MM-DD)")
@click.option("--assert", "assertions", default=None,
              help="CI gate: comma-separated expressions (e.g. 'cycles<=5,dead_exports<=20')")
@click.option("--anomalies", is_flag=True, default=False,
              help="Flag anomalous metric values using Modified Z-Score")
@click.option("--forecast", "do_forecast", is_flag=True, default=False,
              help="Show trend slopes and forecasts using Theil-Sen regression")
@click.option("--analyze", is_flag=True, default=False,
              help="Full analysis: anomalies + trends + patterns + forecasts")
@click.option("--fail-on-anomaly", is_flag=True, default=False,
              help="CI: exit 1 if any anomaly detected")
@click.option("--sensitivity", default="medium",
              type=click.Choice(["low", "medium", "high"]),
              help="Anomaly sensitivity (low=4sigma, medium=3.5sigma, high=3sigma)")
@click.pass_context
def trend(ctx, count, since_date, assertions, anomalies, do_forecast,
          analyze, fail_on_anomaly, sensitivity):
    """Display health trend with sparklines, anomaly detection, and CI gates.

    Shows historical snapshots from `roam index` and `roam snapshot`.
    Use --analyze for full statistical analysis with anomaly detection,
    trend estimation (Theil-Sen), and pattern alerts (Western Electric rules).
    Use --assert or --fail-on-anomaly for CI pipelines.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    do_analysis = analyze or anomalies or do_forecast or fail_on_anomaly
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

        # --- Anomaly analysis ---
        analysis = None
        if do_analysis and len(chrono) >= 4:
            analysis = _analyze_trends(chrono, sensitivity=sensitivity)

        if json_mode:
            summary = {
                "snapshots": len(snap_dicts),
                "latest_health": snap_dicts[0]["health_score"] if snap_dicts else None,
            }
            if analysis:
                verdict = _trend_verdict(analysis)
                summary["verdict"] = verdict
                summary["anomaly_count"] = len(analysis["anomalies"])
                summary["trend_direction"] = verdict

            envelope = json_envelope("trend",
                summary=summary,
                snapshots=snap_dicts,
            )
            if assertions:
                envelope["assertions"] = {
                    "expression": assertions,
                    "passed": len(assertion_results) == 0,
                    "failures": assertion_results,
                }
            if analysis:
                if anomalies or analyze:
                    envelope["anomalies"] = analysis["anomalies"]
                if do_forecast or analyze:
                    envelope["trends"] = analysis["trends"]
                    envelope["forecasts"] = analysis["forecasts"]
                if analyze:
                    envelope["patterns"] = analysis["patterns"]
            click.echo(to_json(envelope))
            if assertion_results:
                raise SystemExit(1)
            if fail_on_anomaly and analysis and analysis["anomalies"]:
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

        # --- Anomaly analysis text output ---
        if analysis:
            if analysis["anomalies"]:
                click.echo(f"\nAnomalies ({len(analysis['anomalies'])}):")
                for a in analysis["anomalies"]:
                    click.echo(
                        f"  ANOMALY: {a['metric']}={a['value']} "
                        f"(z={a['z_score']}, typical ~{a['typical']})"
                    )

            # Trends
            sig_trends = [t for t in analysis["trends"]
                          if t["direction"] != "stable"
                          and t.get("significant", t["slope"] != 0)]
            if sig_trends:
                click.echo(f"\nTrends ({len(sig_trends)} significant):")
                for t in sig_trends:
                    p_str = f" (p={t['p_value']:.3f})" if "p_value" in t else ""
                    sign = "+" if t["slope"] > 0 else ""
                    click.echo(
                        f"  TREND: {t['metric']} {t['direction']} "
                        f"{sign}{t['slope']:.2f}/snapshot{p_str}"
                    )

            # Forecasts
            if analysis["forecasts"]:
                click.echo(f"\nForecasts:")
                for f in analysis["forecasts"]:
                    click.echo(
                        f"  FORECAST: {f['metric']} will reach {f['target']} "
                        f"in ~{f['snapshots_until']} snapshots "
                        f"(current: {f['current']}, rate: +{f['slope']:.1f}/snap)"
                    )

            # Patterns
            if analysis["patterns"]:
                click.echo(f"\nPatterns ({len(analysis['patterns'])}):")
                for p in analysis["patterns"]:
                    click.echo(
                        f"  WARNING: {p['metric']} -- {p['description']} "
                        f"(Rule {p['rule']})"
                    )

            # Verdict
            verdict = _trend_verdict(analysis)
            click.echo(f"\nVERDICT: {verdict}")

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

        # CI gate for anomalies
        if fail_on_anomaly and analysis and analysis["anomalies"]:
            raise SystemExit(1)
