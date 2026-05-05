"""Detect health degradation trends and generate actionable alerts."""

from __future__ import annotations

from pathlib import Path

import click

from roam.commands.metrics_history import collect_metrics, get_snapshots
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

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

_DEFAULT_THRESHOLDS = {
    "health_score": {"op": "<", "value": 60, "level": CRITICAL},
    "cycles": {"op": ">", "value": 10, "level": WARNING},
    "god_components": {"op": ">", "value": 5, "level": WARNING},
    "layer_violations": {"op": ">", "value": 0, "level": INFO},
}

# Backwards-compat alias for any plug-ins that imported the old name.
_THRESHOLDS = _DEFAULT_THRESHOLDS


def _parse_alerts_yaml(text: str) -> dict:
    """Tiny YAML reader for ``.roam/alerts.yaml`` — avoids the PyYAML dep.

    Schema accepted:

        thresholds:
          health_score: { op: '<', value: 50, level: CRITICAL }
          cycles: { op: '>', value: 50, level: WARNING }
        delta_alerts: true
    """
    result: dict[str, dict] = {}
    current_section: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            key = line.rstrip(":").strip()
            current_section = key
            result[current_section] = {}
            continue
        if current_section is None:
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("{") and value.endswith("}"):
            inner = value[1:-1]
            d: dict = {}
            for part in inner.split(","):
                if ":" not in part:
                    continue
                k, _, v = part.partition(":")
                v = v.strip().strip("'\"")
                k = k.strip()
                if v.lstrip("-").isdigit():
                    d[k] = int(v)
                else:
                    try:
                        d[k] = float(v)
                    except ValueError:
                        d[k] = v
            result[current_section][key] = d
        else:
            v = value.strip("'\"")
            if v.lstrip("-").isdigit():
                result[current_section][key] = int(v)
            elif v.lower() in ("true", "false"):
                result[current_section][key] = v.lower() == "true"
            else:
                result[current_section][key] = v
    return result


def _load_alerts_config(project_root: Path | None = None) -> dict:
    """Load ``.roam/alerts.yaml`` overrides if present.

    Round 4 #3, G: hardcoded thresholds force every project to live with
    the same noise floor. A small YAML lets users (and roam itself, via
    ``--init`` later) tune what 'critical' means for their codebase.
    """
    root = project_root or find_project_root()
    cfg_path = root / ".roam" / "alerts.yaml"
    if not cfg_path.exists():
        return {}
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml  # type: ignore[import-not-found]

        data = yaml.safe_load(text) or {}
    except ImportError:
        data = _parse_alerts_yaml(text)
    return data if isinstance(data, dict) else {}


def _resolved_thresholds(project_root: Path | None = None) -> dict:
    """Merge ``.roam/alerts.yaml`` overrides on top of the defaults."""
    cfg = _load_alerts_config(project_root)
    overrides = cfg.get("thresholds", {}) or {}
    merged = {k: dict(v) for k, v in _DEFAULT_THRESHOLDS.items()}
    for metric, rule in overrides.items():
        if not isinstance(rule, dict):
            continue
        slot = merged.setdefault(metric, {"op": ">", "value": 0, "level": WARNING})
        slot.update(rule)
    return merged


_RATE_OF_CHANGE_PCT = 20  # alert if metric changes more than 20%

# Metrics where an increase means degradation
_WORSE_WHEN_HIGHER = {"cycles", "god_components", "bottlenecks", "dead_exports", "layer_violations"}
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


def _make_alert(level, metric, message, current_value, trend_direction=None):
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


def _check_thresholds(current, thresholds: dict | None = None):
    """Check current metrics against thresholds (defaults + ``.roam/alerts.yaml``)."""
    alerts = []
    rules = thresholds if thresholds is not None else _resolved_thresholds()
    for metric, rule in rules.items():
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
            alerts.append(
                _make_alert(
                    level,
                    metric,
                    f"{metric}={val} ({msg})",
                    val,
                )
            )
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
                alerts.append(
                    _make_alert(
                        WARNING,
                        metric,
                        f"{label}: {arrow} over {window} snapshots{slope_str}",
                        current,
                        trend_direction="up" if metric in _WORSE_WHEN_HIGHER else "down",
                    )
                )
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
                alerts.append(
                    _make_alert(
                        INFO,
                        metric,
                        f"{metric}={curr_val} (new since last snapshot)",
                        curr_val,
                        trend_direction="up",
                    )
                )
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
            alerts.append(
                _make_alert(
                    WARNING,
                    metric,
                    f"{metric}={curr_val} ({direction} {pct:.0f}% since last snapshot)",
                    curr_val,
                    trend_direction="up" if curr_val > prev_val else "down",
                )
            )
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


def _build_snap_dicts(snaps_raw) -> list[dict]:
    """Convert the DB rows (newest-first) into chronological dict list."""
    out: list[dict] = []
    for s in reversed(snaps_raw):
        out.append(
            {
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
            }
        )
    return out


def _delta_baseline_alerts(current: dict, baseline_snap: dict) -> list[dict]:
    """Per-metric regression alerts vs. the previous snapshot."""
    out: list[dict] = []
    for metric, current_value in current.items():
        if not isinstance(current_value, (int, float)):
            continue
        baseline_value = baseline_snap.get(metric)
        if not isinstance(baseline_value, (int, float)) or baseline_value == 0:
            continue
        delta = current_value - baseline_value
        pct = abs(delta) / max(abs(baseline_value), 1) * 100
        regressed = (metric in _WORSE_WHEN_HIGHER and delta > 0) or (metric in _WORSE_WHEN_LOWER and delta < 0)
        if regressed and pct >= 10:
            arrow = "+" if delta > 0 else ""
            out.append(
                _make_alert(
                    WARNING if pct < 25 else CRITICAL,
                    metric,
                    f"{metric} regressed since baseline: {baseline_value} -> {current_value} "
                    f"({arrow}{delta}, {pct:.0f}%)",
                    current_value,
                    trend_direction="worse",
                )
            )
    return out


def _alerts_summary_parts(counts: dict) -> list[str]:
    """Render the ``N critical, M warnings, K info`` clauses."""
    parts: list[str] = []
    if counts[CRITICAL]:
        parts.append(f"{counts[CRITICAL]} critical")
    if counts[WARNING]:
        parts.append(f"{counts[WARNING]} warning{'s' if counts[WARNING] != 1 else ''}")
    if counts[INFO]:
        parts.append(f"{counts[INFO]} info")
    return parts


def _alerts_verdict(all_alerts: list[dict], counts: dict) -> str:
    if not all_alerts:
        return "no alerts — all metrics within normal ranges"
    parts = _alerts_summary_parts(counts)
    return f"{len(all_alerts)} alert{'s' if len(all_alerts) != 1 else ''}: {', '.join(parts)}"


def _emit_alerts_json(verdict: str, all_alerts: list[dict], counts: dict, snapshots_analyzed: int) -> None:
    click.echo(
        to_json(
            json_envelope(
                "alerts",
                summary={
                    "verdict": verdict,
                    "total": len(all_alerts),
                    "critical": counts[CRITICAL],
                    "warning": counts[WARNING],
                    "info": counts[INFO],
                    "snapshots_analyzed": snapshots_analyzed,
                },
                alerts=all_alerts,
            )
        )
    )


def _emit_alerts_text(verdict: str, all_alerts: list[dict], counts: dict) -> None:
    click.echo(f"VERDICT: {verdict}\n")
    if not all_alerts:
        click.echo("No health alerts. All metrics are within normal ranges.")
        return
    click.echo("Health alerts:\n")
    for a in all_alerts:
        click.echo(f"  {a['level'].ljust(9)} {a['message']}")
    click.echo()
    click.echo(", ".join(_alerts_summary_parts(counts)))


@click.command()
@click.pass_context
def alerts(ctx):
    """Detect health degradation trends and generate actionable alerts.

    Analyzes snapshot history to find:
    - Metrics that consistently worsen over 3+ snapshots
    - Current values that exceed severity thresholds
    - Metrics that changed more than 20% since the last snapshot

    Unlike ``health`` (which gives a point-in-time codebase score), this
    command performs time-series analysis over snapshot history using
    Mann-Kendall trend tests and Sen's slope to detect degradation trends.
    Run ``trends --save`` regularly to build history for trend detection.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    all_alerts: list[dict] = []
    with open_db(readonly=True) as conn:
        snap_dicts = _build_snap_dicts(get_snapshots(conn))
        current = snap_dicts[-1] if snap_dicts else collect_metrics(conn)

        # 1) Threshold checks (respect .roam/alerts.yaml overrides).
        cfg = _load_alerts_config()
        thresholds = _resolved_thresholds()
        all_alerts.extend(_check_thresholds(current, thresholds))

        # 2) Delta-vs-baseline alerts (need >= 2 snapshots, opt-out via config).
        delta_enabled = cfg.get("delta_alerts", True) if cfg else True
        if delta_enabled and len(snap_dicts) >= 2:
            all_alerts.extend(_delta_baseline_alerts(current, snap_dicts[-2]))

        # 3) Trend detection (Mann-Kendall + Sen's slope, need >= 3 snapshots).
        if len(snap_dicts) >= 3:
            all_alerts.extend(_check_trends(snap_dicts))

        # 4) Rate-of-change detection (need >= 2 snapshots).
        if len(snap_dicts) >= 2:
            all_alerts.extend(_check_rate_of_change(snap_dicts))

    all_alerts = _deduplicate(all_alerts)
    counts = {CRITICAL: 0, WARNING: 0, INFO: 0}
    for a in all_alerts:
        counts[a["level"]] += 1
    verdict = _alerts_verdict(all_alerts, counts)

    if json_mode:
        _emit_alerts_json(verdict, all_alerts, counts, len(snap_dicts))
        return
    _emit_alerts_text(verdict, all_alerts, counts)
