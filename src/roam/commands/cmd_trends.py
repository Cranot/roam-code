"""Historical metric trends with sparklines, anomaly detection, and CI gates.

Unified command combining health-snapshot timeline, per-metric tracking,
anomaly detection (Modified Z-Score, Theil-Sen, Western Electric), CI
assertion gates, and AI-vs-human cohort analysis.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import click

from roam.commands.metrics_history import append_snapshot, collect_metrics, get_snapshots
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.index.git_stats import get_blame_for_file
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Sparkline rendering
# ---------------------------------------------------------------------------

_SPARKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
_SPARK_CHARS = "._-=+*#%@"


def _sparkline(values):
    """Render a list of numbers as a terminal sparkline (Unicode)."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    return "".join(_SPARKS[min(len(_SPARKS) - 1, int((v - mn) / rng * (len(_SPARKS) - 1)))] for v in values)


def _ascii_sparkline(values: list[float]) -> str:
    """Render ASCII sparkline for a numeric series."""
    if not values:
        return ""
    vmin = min(values)
    vmax = max(values)
    if math.isclose(vmin, vmax):
        return "=" * len(values)

    out = []
    span = vmax - vmin
    levels = len(_SPARK_CHARS) - 1
    for v in values:
        norm = (v - vmin) / span
        idx = int(round(norm * levels))
        idx = max(0, min(levels, idx))
        out.append(_SPARK_CHARS[idx])
    return "".join(out)


# ---------------------------------------------------------------------------
# CI assertion engine
# ---------------------------------------------------------------------------

_ASSERT_RE = re.compile(r"(\w+)\s*(<=|>=|==|!=|<|>)\s*(\d+)")
_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
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
        forecast,
        mann_kendall_test,
        modified_z_score,
        theil_sen_slope,
        western_electric_rules,
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
                anomalies.append(
                    {
                        "metric": metric,
                        "index": r["index"],
                        "value": r["value"],
                        "z_score": round(r["z_score"], 2),
                        "typical": f"{r.get('median', 0):.0f}",
                    }
                )

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
                current = values[-1]
                target = max(current * 2, current + 10)
                fc = forecast(values, target=target)
                if fc and fc.get("steps_until"):
                    forecasts.append(
                        {
                            "metric": metric,
                            "current": current,
                            "target": target,
                            "slope": round(fc["slope"], 2),
                            "snapshots_until": fc["steps_until"],
                        }
                    )

        # Pattern detection
        we_results = western_electric_rules(values)
        for r in we_results:
            patterns.append(
                {
                    "metric": metric,
                    "rule": r["rule"],
                    "description": r["description"],
                    "indices": r.get("indices", []),
                }
            )

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
        t
        for t in analysis["trends"]
        if t["metric"] in _QUALITY_METRICS and t["direction"] == "increasing" and t.get("significant", True)
    ]
    improving = [
        t
        for t in analysis["trends"]
        if t["metric"] in _COMPOSITE_METRICS and t["direction"] == "increasing" and t.get("significant", True)
    ]

    if n_anomalies > 2 or len(degrading) > 2:
        return "degrading"
    if n_anomalies > 0 or len(degrading) > 0 or n_patterns > 2:
        return "warning"
    if improving:
        return "improving"
    return "stable"


# ---------------------------------------------------------------------------
# Metric definitions for --metric mode (broader metrics via metric_snapshots)
# ---------------------------------------------------------------------------

# Each entry: (metric_name, higher_is_better)
# higher_is_better controls direction interpretation:
#   True  -> increase = "improving", decrease = "worsening"
#   False -> increase = "worsening", decrease = "improving"
_METRIC_DEFS = {
    "health_score": True,
    "total_files": True,  # neutral/growing, but not "worsening"
    "total_symbols": True,  # neutral/growing
    "dead_symbols": False,
    "avg_complexity": False,
    "max_complexity": False,
    "cycle_count": False,
    "test_file_ratio": True,
}


# ---------------------------------------------------------------------------
# Metric snapshot capture and history (metric_snapshots table)
# ---------------------------------------------------------------------------


def _collect_current_metrics(conn):
    """Query the DB and return a dict of {metric_name: value}."""
    metrics = {}

    # health_score -- reuse the snapshot infrastructure
    try:
        from roam.commands.metrics_history import collect_metrics

        m = collect_metrics(conn)
        metrics["health_score"] = m.get("health_score", 0)
        metrics["cycle_count"] = m.get("cycles", 0)
        metrics["dead_symbols"] = m.get("dead_exports", 0)
    except Exception:
        metrics["health_score"] = 0
        metrics["cycle_count"] = 0
        metrics["dead_symbols"] = 0

    # total_files
    row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    metrics["total_files"] = row[0] if row else 0

    # total_symbols
    row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
    metrics["total_symbols"] = row[0] if row else 0

    # avg_complexity, max_complexity
    try:
        row = conn.execute("SELECT AVG(cognitive_complexity), MAX(cognitive_complexity) FROM symbol_metrics").fetchone()
        metrics["avg_complexity"] = round(row[0], 2) if row and row[0] is not None else 0.0
        metrics["max_complexity"] = round(row[1], 2) if row and row[1] is not None else 0.0
    except Exception:
        metrics["avg_complexity"] = 0.0
        metrics["max_complexity"] = 0.0

    # test_file_ratio
    try:
        total_files = metrics["total_files"] or 1
        test_count = conn.execute("SELECT COUNT(*) FROM files WHERE file_role = 'test'").fetchone()[0] or 0
        source_count = total_files - test_count
        metrics["test_file_ratio"] = round(test_count / max(source_count, 1), 3)
    except Exception:
        metrics["test_file_ratio"] = 0.0

    return metrics


def _record_snapshot(conn):
    """Collect current metrics and insert rows into metric_snapshots.

    Returns the dict of recorded metrics.
    """
    metrics = _collect_current_metrics(conn)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for name, value in metrics.items():
        conn.execute(
            "INSERT INTO metric_snapshots (timestamp, metric_name, metric_value) VALUES (?, ?, ?)",
            (ts, name, value),
        )
    conn.commit()
    return metrics


def _get_metric_history(conn, metric_name, days):
    """Fetch time-ordered values for a metric within the last N days."""
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    since = cutoff - timedelta(days=days)
    since_str = since.isoformat().replace("+00:00", "Z")

    rows = conn.execute(
        "SELECT timestamp, metric_value FROM metric_snapshots "
        "WHERE metric_name = ? AND timestamp >= ? "
        "ORDER BY timestamp ASC",
        (metric_name, since_str),
    ).fetchall()
    return [(r["timestamp"], r["metric_value"]) for r in rows]


# ---------------------------------------------------------------------------
# Direction labels, trend bars, alerts
# ---------------------------------------------------------------------------


def _direction_label(first_val, last_val, higher_is_better):
    """Return a direction string based on value change and polarity."""
    if last_val > first_val:
        return "improving" if higher_is_better else "worsening"
    elif last_val < first_val:
        return "worsening" if higher_is_better else "improving"
    return "stable"


def _ascii_trend_bar(first_val, last_val, higher_is_better, max_width=8):
    """Render a simple ASCII trend arrow.

    =====>  for improving (goes right)
    <====   for worsening (goes left)
    ===     for stable
    """
    if first_val == last_val:
        return "==="

    diff = abs(last_val - first_val)
    scale = max(abs(first_val), 1.0)
    magnitude = min(max_width, max(1, int(diff / scale * max_width) + 1))

    improving = _direction_label(first_val, last_val, higher_is_better) == "improving"
    bar = "=" * magnitude
    if improving:
        return bar + ">"
    else:
        return "<" + bar


def _format_value(val):
    """Format a metric value for display -- integers for whole numbers, else 2dp."""
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}"


def _compute_change(first_val, last_val):
    """Return (change_abs, change_pct_str)."""
    change = last_val - first_val
    if first_val != 0:
        pct = change / abs(first_val) * 100
        pct_str = f"{pct:+.0f}%"
    else:
        pct_str = "n/a"
    return change, pct_str


def _generate_alerts(metric_results):
    """Generate alert strings for metrics trending in a bad direction."""
    alerts = []
    for m in metric_results:
        if m["direction"] == "worsening" and abs(m["change"]) > 0:
            alerts.append(
                {
                    "metric": m["name"],
                    "message": f"{m['name']} {_worsening_verb(m['name'])} -- investigate",
                }
            )
    return alerts


def _worsening_verb(metric_name):
    """Human-readable worsening description per metric."""
    verbs = {
        "health_score": "declining -- review recent changes",
        "dead_symbols": "increasing -- remove unused exports",
        "avg_complexity": "increasing -- refactor complex functions",
        "max_complexity": "increasing -- break up the most complex function",
        "cycle_count": "increasing -- investigate new circular dependencies",
        "test_file_ratio": "declining -- add more tests",
        "total_files": "shrinking unexpectedly",
        "total_symbols": "shrinking unexpectedly",
    }
    return verbs.get(metric_name, "trending in a bad direction")


# ---------------------------------------------------------------------------
# Cohort trend analysis (AI-authored vs human-authored)
# ---------------------------------------------------------------------------

_AI_AUTHOR_RE = re.compile(
    r"(copilot|claude|cursor|codeium|tabnine|gemini|chatgpt|openai|anthropic|"
    r"aider|codex|devin|bot|dependabot|renovate|sweep)",
    re.IGNORECASE,
)


def _is_ai_author(author: str | None) -> bool:
    """Heuristic classifier for AI/bot-like author identities."""
    if not author:
        return False
    return _AI_AUTHOR_RE.search(author) is not None


def _looks_ai_message(message: str | None) -> bool:
    """Heuristic classifier for commit message AI markers."""
    if not message:
        return False
    msg = message.strip().lower()
    if not msg:
        return False
    ai_tokens = (
        "co-authored-by:",
        "generated",
        "auto-generated",
        "automated",
        "copilot",
        "claude",
        "cursor",
        "chatgpt",
        "codex",
    )
    if any(tok in msg for tok in ai_tokens):
        return True
    return (
        re.match(
            r"^(feat|fix|refactor|chore|docs|style|test|perf|ci|build)(\(.+?\))?:\s",
            msg,
        )
        is not None
    )


def _percentile_rank(value: float, population: list[float]) -> float:
    """Return percentile rank [0, 100] for value in population."""
    if not population:
        return 0.0
    n = len(population)
    le_count = sum(1 for v in population if v <= value)
    return round((le_count / n) * 100.0, 2)


def _linear_slope(values: list[float]) -> float:
    """Simple least-squares slope over equally spaced x values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0:
        return 0.0
    return num / den


def _load_source_file_risk(conn) -> dict[str, dict]:
    """Compute per-file risk index and percentile using static project signals."""
    rows = conn.execute(
        """
        SELECT
            f.id,
            f.path,
            COALESCE(fs.complexity, 0.0) AS complexity,
            COALESCE(fs.total_churn, 0) AS churn,
            COALESCE(fs.health_score, 10.0) AS health_score,
            COALESCE(gm.max_pagerank, 0.0) AS max_pagerank,
            COALESCE(tf.test_file_count, 0) AS test_file_count
        FROM files f
        LEFT JOIN file_stats fs ON fs.file_id = f.id
        LEFT JOIN (
            SELECT s.file_id, MAX(COALESCE(gm.pagerank, 0.0)) AS max_pagerank
            FROM symbols s
            LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id
            GROUP BY s.file_id
        ) gm ON gm.file_id = f.id
        LEFT JOIN (
            SELECT fe.target_file_id AS file_id,
                   COUNT(DISTINCT fe.source_file_id) AS test_file_count
            FROM file_edges fe
            JOIN files tf ON tf.id = fe.source_file_id
            WHERE tf.file_role = 'test'
            GROUP BY fe.target_file_id
        ) tf ON tf.file_id = f.id
        WHERE COALESCE(f.file_role, 'source') = 'source'
        ORDER BY f.path
        """
    ).fetchall()

    if not rows:
        return {}

    complexities = [float(r["complexity"] or 0.0) for r in rows]
    churns = [float(r["churn"] or 0.0) for r in rows]
    pageranks = [float(r["max_pagerank"] or 0.0) for r in rows]

    file_risk: dict[str, dict] = {}
    for r in rows:
        complexity = float(r["complexity"] or 0.0)
        churn = float(r["churn"] or 0.0)
        health_score = float(r["health_score"] or 0.0)
        max_pagerank = float(r["max_pagerank"] or 0.0)
        test_file_count = int(r["test_file_count"] or 0)

        complexity_pct = _percentile_rank(complexity, complexities)
        churn_pct = _percentile_rank(churn, churns)
        pagerank_pct = _percentile_rank(max_pagerank, pageranks)
        test_penalty = 100.0 if test_file_count == 0 else 0.0
        # health_score is [0,10] where higher is better.
        health_penalty = max(0.0, min(100.0, (10.0 - health_score) * 10.0))

        risk_index = (
            0.40 * complexity_pct + 0.25 * churn_pct + 0.20 * pagerank_pct + 0.10 * test_penalty + 0.05 * health_penalty
        )

        file_risk[r["path"]] = {
            "path": r["path"],
            "complexity": round(complexity, 3),
            "churn": int(churn),
            "health_score": round(health_score, 2),
            "max_pagerank": round(max_pagerank, 8),
            "test_file_count": test_file_count,
            "risk_index": round(risk_index, 2),
        }

    # Add risk percentile across source files.
    all_risks = [v["risk_index"] for v in file_risk.values()]
    for rec in file_risk.values():
        rec["risk_percentile"] = _percentile_rank(rec["risk_index"], all_risks)

    return file_risk


def _build_cohort_analysis(conn, days: int) -> dict:
    """Build AI-vs-human trend cohorts from authorship and AI-ratio signals."""
    now_ts = int(time.time())
    cutoff_ts = now_ts - max(days, 1) * 86400

    change_rows = conn.execute(
        """
        SELECT gc.author, gc.timestamp, gc.message,
               gfc.path, gfc.lines_added, gfc.lines_removed
        FROM git_commits gc
        JOIN git_file_changes gfc ON gfc.commit_id = gc.id
        WHERE gc.timestamp >= ?
        ORDER BY gc.timestamp ASC
        """,
        (cutoff_ts,),
    ).fetchall()
    if not change_rows:
        return {}

    # Per-file commit-touch attribution from recent commits.
    churn_by_file: dict[str, dict] = defaultdict(lambda: {"total": 0, "ai": 0, "human": 0})
    events: list[dict] = []
    for row in change_rows:
        lines = int(row["lines_added"] or 0) + int(row["lines_removed"] or 0)
        if lines <= 0:
            continue
        author = row["author"] or ""
        message = row["message"] or ""
        is_ai_commit = _is_ai_author(author) or _looks_ai_message(message)
        path = row["path"]

        stat = churn_by_file[path]
        stat["total"] += lines
        if is_ai_commit:
            stat["ai"] += lines
        else:
            stat["human"] += lines

        events.append(
            {
                "path": path,
                "timestamp": int(row["timestamp"] or 0),
                "lines": lines,
                "is_ai_commit": is_ai_commit,
            }
        )

    if not events:
        return {}

    # AI-ratio per-file probabilities.
    ai_prob_by_file: dict[str, float] = {}
    try:
        from roam.commands.cmd_ai_ratio import analyse_ai_ratio

        ai_ratio = analyse_ai_ratio(conn, since_days=max(days, 1))
        for item in ai_ratio.get("top_ai_files", []):
            path = item.get("path")
            prob = item.get("probability")
            if isinstance(path, str) and isinstance(prob, (int, float)):
                ai_prob_by_file[path] = float(prob)
    except Exception:
        ai_prob_by_file = {}

    # Blame-based author attribution for high-churn files.
    project_root = find_project_root()
    blame_ratio_by_file: dict[str, float] = {}
    ranked_paths = sorted(
        churn_by_file.keys(),
        key=lambda p: churn_by_file[p]["total"],
        reverse=True,
    )
    for path in ranked_paths[:200]:
        blame_rows = get_blame_for_file(project_root, path)
        if not blame_rows:
            continue
        total_lines = len(blame_rows)
        ai_lines = sum(1 for b in blame_rows if _is_ai_author(b.get("author")))
        if total_lines > 0:
            blame_ratio_by_file[path] = ai_lines / total_lines

    # File quality/risk signals.
    file_risk = _load_source_file_risk(conn)
    if not file_risk:
        return {}

    file_profiles: dict[str, dict] = {}
    for path, churn in churn_by_file.items():
        if path not in file_risk:
            continue
        total = churn["total"]
        if total <= 0:
            continue

        ai_touch_ratio = churn["ai"] / total
        ai_ratio_prob = ai_prob_by_file.get(path, 0.0)
        ai_blame_ratio = blame_ratio_by_file.get(path)

        if ai_blame_ratio is None:
            blended = max(ai_touch_ratio, ai_ratio_prob)
        else:
            blended = max(
                ai_ratio_prob,
                0.55 * ai_touch_ratio + 0.45 * ai_blame_ratio,
            )

        cohort = "ai" if blended >= 0.50 else "human"
        file_profiles[path] = {
            **file_risk[path],
            "cohort": cohort,
            "cohort_score": round(blended, 3),
            "ai_touch_ratio": round(ai_touch_ratio, 3),
            "ai_ratio_prob": round(ai_ratio_prob, 3),
            "ai_blame_ratio": (round(ai_blame_ratio, 3) if ai_blame_ratio is not None else None),
            "window_churn": total,
        }

    if not file_profiles:
        return {}

    cohorts = {"ai": [], "human": []}
    for rec in file_profiles.values():
        cohorts[rec["cohort"]].append(rec)

    total_files = len(file_profiles)

    # Time-series buckets for degradation signal (higher risk index is worse).
    bucket_count = max(4, min(12, max(4, days // 7 if days >= 7 else 4)))
    time_span = max(1, now_ts - cutoff_ts)
    bucket_width = max(1.0, time_span / bucket_count)
    bucket_days = days / bucket_count if bucket_count > 0 else float(days)

    scores = {
        "ai": [0.0] * bucket_count,
        "human": [0.0] * bucket_count,
    }
    weights = {
        "ai": [0.0] * bucket_count,
        "human": [0.0] * bucket_count,
    }

    for ev in events:
        path = ev["path"]
        profile = file_profiles.get(path)
        if profile is None:
            continue
        cohort = profile["cohort"]
        idx = int((ev["timestamp"] - cutoff_ts) / bucket_width)
        idx = max(0, min(bucket_count - 1, idx))
        w = float(ev["lines"])
        scores[cohort][idx] += profile["risk_index"] * w
        weights[cohort][idx] += w

    cohort_payload: dict[str, dict] = {}
    for cohort_name in ("ai", "human"):
        recs = cohorts[cohort_name]
        avg_risk = sum(r["risk_index"] for r in recs) / len(recs) if recs else 0.0
        avg_complexity = sum(r["complexity"] for r in recs) / len(recs) if recs else 0.0
        avg_health = sum(r["health_score"] for r in recs) / len(recs) if recs else 0.0
        avg_percentile = sum(r["risk_percentile"] for r in recs) / len(recs) if recs else 0.0
        churn_lines = sum(r["window_churn"] for r in recs)

        series: list[float] = []
        carry = avg_risk
        for i in range(bucket_count):
            if weights[cohort_name][i] > 0:
                carry = scores[cohort_name][i] / weights[cohort_name][i]
            series.append(round(carry, 2))

        slope_per_bucket = _linear_slope(series)
        velocity_per_week = slope_per_bucket * (7.0 / max(bucket_days, 1e-9))
        if velocity_per_week > 0.5:
            direction = "worsening"
        elif velocity_per_week < -0.5:
            direction = "improving"
        else:
            direction = "stable"

        top_risk = sorted(
            recs,
            key=lambda x: x["risk_index"],
            reverse=True,
        )[:5]

        cohort_payload[cohort_name] = {
            "files": len(recs),
            "share_pct": round((len(recs) / max(total_files, 1)) * 100.0, 1),
            "weighted_churn": churn_lines,
            "avg_risk_index": round(avg_risk, 2),
            "avg_complexity": round(avg_complexity, 2),
            "avg_health_score": round(avg_health, 2),
            "avg_risk_percentile": round(avg_percentile, 2),
            "trend_direction": direction,
            "degradation_velocity_per_week": round(velocity_per_week, 3),
            "sparkline": _ascii_sparkline(series),
            "series": series,
            "top_risk_files": [
                {
                    "path": t["path"],
                    "risk_index": t["risk_index"],
                    "cohort_score": t["cohort_score"],
                }
                for t in top_risk
            ],
        }

    ai_vel = cohort_payload["ai"]["degradation_velocity_per_week"]
    human_vel = cohort_payload["human"]["degradation_velocity_per_week"]
    delta = ai_vel - human_vel
    if cohort_payload["ai"]["files"] == 0 or cohort_payload["human"]["files"] == 0:
        verdict = "Insufficient cohort separation in the selected window; collect more mixed-author history"
    elif delta > 0.5:
        verdict = f"AI cohort degrading faster than human cohort (delta +{delta:.2f} risk/week)"
    elif delta < -0.5:
        verdict = f"Human cohort degrading faster than AI cohort (delta {delta:.2f} risk/week)"
    else:
        verdict = f"AI and human cohorts have similar degradation velocity (delta {delta:+.2f} risk/week)"

    return {
        "verdict": verdict,
        "window_days": days,
        "bucket_count": bucket_count,
        "bucket_days": round(bucket_days, 2),
        "files_analyzed": total_files,
        "signals": {
            "ai_ratio_file_signals": len(ai_prob_by_file),
            "blame_files_analyzed": len(blame_ratio_by_file),
            "commit_events": len(events),
        },
        "cohorts": cohort_payload,
    }


# ---------------------------------------------------------------------------
# Timeline mode helpers (health snapshots from `roam index` / `roam trends --save`)
# ---------------------------------------------------------------------------


def _render_timeline_json(
    ctx,
    cmd_name,
    snap_dicts,
    assertions,
    assertion_results,
    analysis,
    anomalies_flag,
    do_forecast,
    analyze_flag,
    fail_on_anomaly,
):
    """Render JSON output for the timeline mode."""
    summary = {
        "snapshots": len(snap_dicts),
        "latest_health": snap_dicts[0]["health_score"] if snap_dicts else None,
    }
    if analysis:
        verdict = _trend_verdict(analysis)
        summary["verdict"] = verdict
        summary["anomaly_count"] = len(analysis["anomalies"])
        summary["trend_direction"] = verdict

    envelope = json_envelope(
        cmd_name,
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
        if anomalies_flag or analyze_flag:
            envelope["anomalies"] = analysis["anomalies"]
        if do_forecast or analyze_flag:
            envelope["trends"] = analysis["trends"]
            envelope["forecasts"] = analysis["forecasts"]
        if analyze_flag:
            envelope["patterns"] = analysis["patterns"]
    click.echo(to_json(envelope))
    if assertion_results:
        raise SystemExit(1)
    if fail_on_anomaly and analysis and analysis["anomalies"]:
        raise SystemExit(1)


def _render_timeline_text(snap_dicts, chrono, assertions, assertion_results, analysis, fail_on_anomaly):
    """Render text output for the timeline mode."""
    click.echo(f"=== Health Trend (last {len(snap_dicts)} snapshots) ===\n")

    # Table
    rows = []
    for s in snap_dicts:
        dt = datetime.fromtimestamp(s["timestamp"], tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%M")
        tag = s["tag"] or f"({s['source']})"
        rows.append(
            [
                date_str,
                tag,
                str(s["health_score"]),
                str(s["cycles"]),
                str(s["god_components"]),
                str(s["bottlenecks"]),
                str(s["dead_exports"]),
                str(s["layer_violations"]),
            ]
        )
    click.echo(
        format_table(
            ["Date", "Tag", "Score", "Cycles", "Gods", "BN", "Dead", "Violations"],
            rows,
        )
    )

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

    # Anomaly analysis text output
    if analysis:
        if analysis["anomalies"]:
            click.echo(f"\nAnomalies ({len(analysis['anomalies'])}):")
            for a in analysis["anomalies"]:
                click.echo(f"  ANOMALY: {a['metric']}={a['value']} (z={a['z_score']}, typical ~{a['typical']})")

        sig_trends = [
            t for t in analysis["trends"] if t["direction"] != "stable" and t.get("significant", t["slope"] != 0)
        ]
        if sig_trends:
            click.echo(f"\nTrends ({len(sig_trends)} significant):")
            for t in sig_trends:
                p_str = f" (p={t['p_value']:.3f})" if "p_value" in t else ""
                sign = "+" if t["slope"] > 0 else ""
                click.echo(f"  TREND: {t['metric']} {t['direction']} {sign}{t['slope']:.2f}/snapshot{p_str}")

        if analysis["forecasts"]:
            click.echo("\nForecasts:")
            for f in analysis["forecasts"]:
                click.echo(
                    f"  FORECAST: {f['metric']} will reach {f['target']} "
                    f"in ~{f['snapshots_until']} snapshots "
                    f"(current: {f['current']}, rate: +{f['slope']:.1f}/snap)"
                )

        if analysis["patterns"]:
            click.echo(f"\nPatterns ({len(analysis['patterns'])}):")
            for p in analysis["patterns"]:
                click.echo(f"  WARNING: {p['metric']} -- {p['description']} (Rule {p['rule']})")

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


# ---------------------------------------------------------------------------
# CLI command (unified)
# ---------------------------------------------------------------------------


@click.command()
# Timeline / snapshot options
@click.option("--range", "count", default=10, help="Number of snapshots for timeline view")
@click.option(
    "--since",
    "since_date",
    default=None,
    help="Timeline: only show snapshots after this date (YYYY-MM-DD). With --compare: snapshot tag to compare against",
)
# CI gate options
@click.option(
    "--assert",
    "assertions",
    default=None,
    help="CI gate: comma-separated expressions (e.g. 'cycles<=5,dead_exports<=20')",
)
@click.option("--fail-on-anomaly", is_flag=True, default=False, help="CI: exit 1 if any anomaly detected")
# Anomaly detection options
@click.option(
    "--anomalies",
    is_flag=True,
    default=False,
    help="Flag anomalous metric values using Modified Z-Score",
)
@click.option(
    "--forecast",
    "do_forecast",
    is_flag=True,
    default=False,
    help="Show trend slopes and forecasts using Theil-Sen regression",
)
@click.option(
    "--analyze",
    is_flag=True,
    default=False,
    help="Full analysis: anomalies + trends + patterns + forecasts",
)
@click.option(
    "--sensitivity",
    default="medium",
    type=click.Choice(["low", "medium", "high"]),
    help="Anomaly sensitivity (low=4sigma, medium=3.5sigma, high=3sigma)",
)
# Metric snapshot options
@click.option("--record", is_flag=True, help="Take a snapshot of current metrics and store it")
@click.option("--days", default=30, type=int, help="Time window in days (for --metric and --cohort-by-author)")
@click.option("--metric", default=None, help="Show per-metric trends from metric_snapshots table")
# Cohort analysis
@click.option(
    "--cohort-by-author",
    is_flag=True,
    help="Compare AI-authored vs human-authored degradation trajectories",
)
# Save / compare options
@click.option("--save", "do_save", is_flag=True, default=False, help="Save a snapshot of current health metrics")
@click.option("--tag", "save_tag", default=None, help="Label for the saved snapshot (use with --save)")
@click.option(
    "--compare",
    "do_compare",
    is_flag=True,
    default=False,
    help="Compare current metrics against last snapshot (or tagged snapshot via --since)",
)
@click.pass_context
def trends(
    ctx,
    count,
    since_date,
    assertions,
    fail_on_anomaly,
    anomalies,
    do_forecast,
    analyze,
    sensitivity,
    record,
    days,
    metric,
    cohort_by_author,
    do_save,
    save_tag,
    do_compare,
):
    """Health trend timeline, anomaly detection, per-metric tracking, and CI gates.

    By default shows a health snapshot timeline (populated by `roam index`).
    Use --analyze for anomaly detection (Modified Z-Score, Theil-Sen, Western Electric).
    Use --assert or --fail-on-anomaly for CI pipelines.
    Use --record to capture broader metric snapshots.
    Use --metric to view per-metric trends from recorded snapshots.
    Use --cohort-by-author to compare AI vs human quality trajectories.
    Use --save [--tag TAG] to persist a health snapshot.
    Use --compare [--since TAG] to diff current metrics against a snapshot.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cmd_name = ctx.info_name or "trends"
    ensure_index()

    # --- Mode dispatch ---

    if cohort_by_author and record:
        click.echo("Cannot combine --record with --cohort-by-author")
        raise SystemExit(1)

    # Mode: Save snapshot (highest priority — runs and exits)
    if do_save:
        _handle_save(ctx, cmd_name, json_mode, save_tag)
        return

    # Mode: Compare against snapshot
    if do_compare:
        # --since is reused from the timeline option to indicate a tag for comparison
        _handle_compare(ctx, cmd_name, json_mode, since_tag=since_date)
        return

    # Mode 1: Record metric snapshot
    if record:
        _handle_record(ctx, cmd_name, json_mode)
        return

    # Mode 2: Cohort analysis
    if cohort_by_author:
        _handle_cohort(ctx, cmd_name, json_mode, days)
        return

    # Mode 3: Per-metric trends (from metric_snapshots table)
    if metric:
        _handle_metric_view(ctx, cmd_name, json_mode, days, metric)
        return

    # Mode 4: Default — health snapshot timeline
    _handle_timeline(
        ctx,
        cmd_name,
        json_mode,
        count,
        since_date,
        assertions,
        fail_on_anomaly,
        anomalies,
        do_forecast,
        analyze,
        sensitivity,
    )


# ---------------------------------------------------------------------------
# Save / compare helpers (reused from digest logic)
# ---------------------------------------------------------------------------

_DIGEST_METRICS = [
    ("health_score", "Health score"),
    ("files", "Files"),
    ("symbols", "Symbols"),
    ("cycles", "Cycles"),
    ("god_components", "God components"),
    ("bottlenecks", "Bottlenecks"),
    ("dead_exports", "Dead exports"),
    ("layer_violations", "Violations"),
]

_LOWER_IS_BETTER = frozenset(
    {
        "cycles",
        "god_components",
        "bottlenecks",
        "dead_exports",
        "layer_violations",
    }
)


def _digest_arrow(key, delta):
    """Return direction arrow: up for improvement, down for regression."""
    if delta == 0:
        return ""
    if key in _LOWER_IS_BETTER:
        return "\u25b2" if delta < 0 else "\u25bc"
    return "\u25b2" if delta > 0 else "\u25bc"


def _digest_delta_str(delta):
    """Format a delta value with sign."""
    if delta == 0:
        return "="
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}"


def _build_compare_recommendations(deltas):
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
        recs.append(
            f"Run `roam health --no-framework` to review {new_gods} new god component{'s' if new_gods != 1 else ''}"
        )

    new_violations = deltas.get("layer_violations", 0)
    if new_violations > 0:
        recs.append(
            f"Run `roam layers` to review {new_violations} new layer violation{'s' if new_violations != 1 else ''}"
        )

    score_drop = deltas.get("health_score", 0)
    if score_drop < -5:
        recs.append(f"Health dropped by {abs(score_drop)} points -- run `roam health` for details")

    if not recs:
        if deltas.get("health_score", 0) > 0:
            recs.append("Health is improving -- keep it up!")
        else:
            recs.append("No significant changes detected")

    return recs


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def _handle_record(ctx, cmd_name, json_mode):
    """Record mode: store a metric snapshot."""
    with open_db() as conn:
        recorded = _record_snapshot(conn)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={
                        "verdict": "Snapshot recorded",
                        "metrics_recorded": len(recorded),
                    },
                    action="record",
                    metrics=recorded,
                )
            )
        )
    else:
        click.echo("VERDICT: Snapshot recorded")
        click.echo()
        for name, val in sorted(recorded.items()):
            click.echo(f"  {name}: {_format_value(val)}")


def _handle_cohort(ctx, cmd_name, json_mode, days):
    """Cohort analysis mode: AI vs human degradation trajectories."""
    with open_db(readonly=True) as conn:
        cohort = _build_cohort_analysis(conn, days=max(days, 1))

    if not cohort:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        cmd_name,
                        summary={
                            "verdict": "No cohort trend data available",
                            "mode": "cohort-by-author",
                            "days": days,
                        },
                        mode="cohort-by-author",
                        days=days,
                        cohorts={},
                        signals={},
                    )
                )
            )
        else:
            click.echo("VERDICT: No cohort trend data available")
            click.echo()
            click.echo("Need indexed git history in the selected window. Try a larger --days value.")
        return

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={
                        "verdict": cohort["verdict"],
                        "mode": "cohort-by-author",
                        "days": cohort["window_days"],
                        "files_analyzed": cohort["files_analyzed"],
                    },
                    mode="cohort-by-author",
                    days=cohort["window_days"],
                    files_analyzed=cohort["files_analyzed"],
                    bucket_count=cohort["bucket_count"],
                    bucket_days=cohort["bucket_days"],
                    signals=cohort["signals"],
                    cohorts=cohort["cohorts"],
                )
            )
        )
        return

    click.echo(f"VERDICT: {cohort['verdict']}")
    click.echo()
    click.echo(
        "Window: {}d  Buckets: {} (~{}d each)  Files analyzed: {}".format(
            cohort["window_days"],
            cohort["bucket_count"],
            cohort["bucket_days"],
            cohort["files_analyzed"],
        )
    )
    click.echo(
        "Signals: {} ai-ratio file hints, {} blame-scanned files, {} commit events".format(
            cohort["signals"]["ai_ratio_file_signals"],
            cohort["signals"]["blame_files_analyzed"],
            cohort["signals"]["commit_events"],
        )
    )
    click.echo()

    rows = []
    for name in ("ai", "human"):
        c = cohort["cohorts"][name]
        rows.append(
            [
                name.upper(),
                str(c["files"]),
                f"{c['avg_risk_index']:.2f}",
                f"{c['avg_risk_percentile']:.1f}",
                f"{c['degradation_velocity_per_week']:+.3f}/wk",
                c["trend_direction"],
                c["sparkline"],
            ]
        )
    click.echo(
        format_table(
            ["COHORT", "FILES", "AVG_RISK", "RISK_PCTL", "VELOCITY", "TREND", "SPARK"],
            rows,
        )
    )

    for name in ("ai", "human"):
        c = cohort["cohorts"][name]
        if not c["top_risk_files"]:
            continue
        click.echo()
        click.echo(f"Top risk files ({name.upper()} cohort):")
        for item in c["top_risk_files"]:
            click.echo(
                "  {}  risk={}  cohort_score={}".format(
                    item["path"],
                    item["risk_index"],
                    item["cohort_score"],
                )
            )


def _handle_metric_view(ctx, cmd_name, json_mode, days, metric):
    """Per-metric trends from the metric_snapshots table."""
    with open_db(readonly=True) as conn:
        if metric not in _METRIC_DEFS:
            available = ", ".join(sorted(_METRIC_DEFS.keys()))
            click.echo(f"Unknown metric: {metric}")
            click.echo(f"Available: {available}")
            raise SystemExit(1)
        metric_names = [metric]

        metric_results = []
        total_snapshots = 0
        for name in metric_names:
            history = _get_metric_history(conn, name, days)
            if not history:
                continue
            total_snapshots = max(total_snapshots, len(history))
            values = [v for _, v in history]
            first_val = values[0]
            last_val = values[-1]
            higher_is_better = _METRIC_DEFS[name]
            direction = _direction_label(first_val, last_val, higher_is_better)
            change, change_pct = _compute_change(first_val, last_val)
            trend_bar = _ascii_trend_bar(first_val, last_val, higher_is_better)

            metric_results.append(
                {
                    "name": name,
                    "latest": last_val,
                    "first": first_val,
                    "change": change,
                    "change_pct": change_pct,
                    "direction": direction,
                    "trend_bar": trend_bar,
                    "history": values,
                    "snapshots": len(values),
                }
            )

    if not metric_results:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        cmd_name,
                        summary={
                            "verdict": "No trend data available",
                            "days": days,
                            "snapshots_count": 0,
                            "metrics": [],
                            "alerts": [],
                        },
                        days=days,
                        snapshots_count=0,
                        metrics=[],
                        alerts=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: No trend data available")
            click.echo()
            click.echo("Run `roam trends --record` after indexing to start tracking.")
        return

    alerts = _generate_alerts(metric_results)
    tracked_count = len(metric_results)
    improving = sum(1 for m in metric_results if m["direction"] == "improving")
    worsening = sum(1 for m in metric_results if m["direction"] == "worsening")

    if worsening > 0:
        verdict = (
            f"{tracked_count} metrics tracked over {days} days "
            f"({total_snapshots} snapshots) -- "
            f"{worsening} worsening, {improving} improving"
        )
    elif improving > 0:
        verdict = (
            f"{tracked_count} metrics tracked over {days} days ({total_snapshots} snapshots) -- all stable or improving"
        )
    else:
        verdict = f"{tracked_count} metrics tracked over {days} days ({total_snapshots} snapshots) -- stable"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={
                        "verdict": verdict,
                        "days": days,
                        "snapshots_count": total_snapshots,
                        "metrics_tracked": tracked_count,
                        "improving": improving,
                        "worsening": worsening,
                    },
                    days=days,
                    snapshots_count=total_snapshots,
                    metrics=[
                        {
                            "name": m["name"],
                            "latest": m["latest"],
                            "change": m["change"],
                            "change_pct": m["change_pct"],
                            "direction": m["direction"],
                            "history": m["history"],
                        }
                        for m in metric_results
                    ],
                    alerts=[{"metric": a["metric"], "message": a["message"]} for a in alerts],
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()

    rows = []
    for m in metric_results:
        change_str = f"{m['change']:+.2f}" if m["change"] != int(m["change"]) else f"{int(m['change']):+d}"
        rows.append(
            [
                m["name"],
                _format_value(m["latest"]),
                f"{change_str} ({m['change_pct']})",
                m["trend_bar"],
                m["direction"],
            ]
        )
    click.echo(
        format_table(
            ["METRIC", "LATEST", "CHANGE", "TREND", "DIRECTION"],
            rows,
        )
    )

    if alerts:
        click.echo()
        click.echo("ALERTS:")
        for a in alerts:
            click.echo(f"  {a['metric']}: {a['message']}")


def _handle_save(ctx, cmd_name, json_mode, tag):
    """Save mode: persist a snapshot of current health metrics."""
    with open_db() as conn:
        result = append_snapshot(conn, tag=tag, source="snapshot")

    _tag_str = f" [{tag}]" if tag else ""
    verdict = (
        f"snapshot saved{_tag_str}: health={result['health_score']}, "
        f"{result['files']} files, {result['symbols']} symbols"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={
                        "verdict": verdict,
                        "health_score": result["health_score"],
                        "tag": tag,
                        "mode": "save",
                    },
                    mode="save",
                    **result,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Snapshot saved{_tag_str}")
        click.echo(f"  Health: {result['health_score']}/100")
        click.echo(f"  Files: {result['files']}  Symbols: {result['symbols']}  Edges: {result['edges']}")
        click.echo(
            f"  Cycles: {result['cycles']}  God: {result['god_components']}  "
            f"Bottlenecks: {result['bottlenecks']}  Dead: {result['dead_exports']}  "
            f"Violations: {result['layer_violations']}"
        )
        if result.get("git_branch"):
            click.echo(f"  Branch: {result['git_branch']}  Commit: {result.get('git_commit', '?')}")


def _handle_compare(ctx, cmd_name, json_mode, since_tag):
    """Compare mode: show deltas between current metrics and last (or tagged) snapshot."""
    with open_db(readonly=True) as conn:
        current = collect_metrics(conn)

        snaps = get_snapshots(conn, limit=50)
        if not snaps:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            cmd_name,
                            summary={"verdict": "no snapshots found", "error": "No snapshots found", "mode": "compare"},
                            current=current,
                            previous=None,
                            deltas=None,
                        )
                    )
                )
            else:
                click.echo("VERDICT: no snapshots found\n")
                click.echo("No snapshots found. Run `roam trends --save` first.")
            return

        # Pick the right snapshot for comparison
        previous = None
        if since_tag:
            for s in snaps:
                if s["tag"] == since_tag:
                    previous = dict(s)
                    break
            if previous is None:
                tags = [s["tag"] for s in snaps if s["tag"]]
                tag_list = ", ".join(tags[:10]) if tags else "(none)"
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                cmd_name,
                                summary={"error": f"Tag '{since_tag}' not found", "mode": "compare"},
                                available_tags=tags[:20],
                            )
                        )
                    )
                else:
                    click.echo(f"Tag '{since_tag}' not found. Available tags: {tag_list}")
                return
        else:
            previous = dict(snaps[0])

        # Compute deltas
        deltas = {}
        for key, _label in _DIGEST_METRICS:
            cur_val = current.get(key, 0) or 0
            prev_val = previous.get(key, 0) or 0
            deltas[key] = cur_val - prev_val

        recommendations = _build_compare_recommendations(deltas)

        # Format snapshot label
        snap_ts = previous.get("timestamp", 0)
        snap_date = datetime.fromtimestamp(snap_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        snap_tag = previous.get("tag")
        snap_label = snap_date
        if snap_tag:
            snap_label += f" [{snap_tag}]"

    if json_mode:
        _score = current.get("health_score", 0) or 0
        _prev = previous.get("health_score", 0) or 0
        _delta = deltas.get("health_score", 0)
        _sign = "+" if _delta > 0 else ""
        verdict = f"health {_prev} -> {_score} ({_sign}{_delta}) vs {snap_label}"
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={
                        "verdict": verdict,
                        "health_score": current.get("health_score"),
                        "previous_health_score": previous.get("health_score"),
                        "health_delta": deltas.get("health_score", 0),
                        "snapshot_date": snap_date,
                        "snapshot_tag": snap_tag,
                        "mode": "compare",
                    },
                    mode="compare",
                    current=current,
                    previous={k: previous.get(k) for k, _ in _DIGEST_METRICS},
                    deltas=deltas,
                    recommendations=recommendations,
                )
            )
        )
        return

    _score = current.get("health_score", 0) or 0
    _prev = previous.get("health_score", 0) or 0
    _delta = deltas.get("health_score", 0)
    _sign = "+" if _delta > 0 else ""
    click.echo(f"VERDICT: health {_prev} -> {_score} ({_sign}{_delta}) vs {snap_label}\n")
    click.echo(f"Compare (vs {snap_label} snapshot):\n")

    max_label = max(len(label) for _, label in _DIGEST_METRICS)
    for key, label in _DIGEST_METRICS:
        cur_val = current.get(key, 0) or 0
        prev_val = previous.get(key, 0) or 0
        d = deltas[key]
        arrow = _digest_arrow(key, d)
        padded = label.ljust(max_label)
        click.echo(f"  {padded}  {prev_val} \u2192 {cur_val} ({_digest_delta_str(d)}) {arrow}")

    if recommendations:
        click.echo("\nRecommendations:")
        for rec in recommendations:
            click.echo(f"  - {rec}")


def _handle_timeline(
    ctx,
    cmd_name,
    json_mode,
    count,
    since_date,
    assertions,
    fail_on_anomaly,
    anomalies_flag,
    do_forecast,
    analyze_flag,
    sensitivity,
):
    """Default mode: health snapshot timeline with optional anomaly detection."""
    do_analysis = analyze_flag or anomalies_flag or do_forecast or fail_on_anomaly

    since_ts = None
    if since_date:
        try:
            dt = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            since_ts = int(dt.timestamp())
        except ValueError:
            click.echo(f"Invalid date format: {since_date} (use YYYY-MM-DD)")
            raise SystemExit(1) from None

    with open_db(readonly=True) as conn:
        snaps = get_snapshots(conn, limit=count, since=since_ts)

        if not snaps:
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            cmd_name,
                            summary={"snapshots": 0},
                            snapshots=[],
                        )
                    )
                )
            else:
                click.echo("No snapshots found. Run `roam index` or `roam trends --save` first.")
            return

        # Convert to dicts for easier access
        snap_dicts = []
        for s in snaps:
            snap_dicts.append(
                {
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
                }
            )

        # Reverse for chronological order (oldest first for sparklines)
        chrono = list(reversed(snap_dicts))

        # Assertions
        assertion_results = []
        if assertions:
            latest = snap_dicts[0]  # newest first
            assertion_results = _check_assertions(assertions, latest)

        # Anomaly analysis
        analysis = None
        if do_analysis and len(chrono) >= 4:
            analysis = _analyze_trends(chrono, sensitivity=sensitivity)

        if json_mode:
            _render_timeline_json(
                ctx,
                cmd_name,
                snap_dicts,
                assertions,
                assertion_results,
                analysis,
                anomalies_flag,
                do_forecast,
                analyze_flag,
                fail_on_anomaly,
            )
            return

        _render_timeline_text(
            snap_dicts,
            chrono,
            assertions,
            assertion_results,
            analysis,
            fail_on_anomaly,
        )
