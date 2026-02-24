"""Historical metric trends with sparkline output."""

from __future__ import annotations

import json as _json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.index.git_stats import get_blame_for_file


# ---------------------------------------------------------------------------
# Metric definitions — name, SQL/computation, "higher_is_better" flag
# ---------------------------------------------------------------------------

# Each entry: (metric_name, higher_is_better)
# higher_is_better controls direction interpretation:
#   True  → increase = "improving", decrease = "worsening"
#   False → increase = "worsening", decrease = "improving"
_METRIC_DEFS = {
    "health_score":     True,
    "total_files":      True,   # neutral/growing, but not "worsening"
    "total_symbols":    True,   # neutral/growing
    "dead_symbols":     False,
    "avg_complexity":   False,
    "max_complexity":   False,
    "cycle_count":      False,
    "test_file_ratio":  True,
}

_SPARK_CHARS = "._-=+*#%@"
_AI_AUTHOR_RE = re.compile(
    r"(copilot|claude|cursor|codeium|tabnine|gemini|chatgpt|openai|anthropic|"
    r"aider|codex|devin|bot|dependabot|renovate|sweep)",
    re.IGNORECASE,
)


def _collect_current_metrics(conn):
    """Query the DB and return a dict of {metric_name: value}."""
    metrics = {}

    # health_score — reuse the snapshot infrastructure
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
        row = conn.execute(
            "SELECT AVG(cognitive_complexity), MAX(cognitive_complexity) "
            "FROM symbol_metrics"
        ).fetchone()
        metrics["avg_complexity"] = round(row[0], 2) if row and row[0] is not None else 0.0
        metrics["max_complexity"] = round(row[1], 2) if row and row[1] is not None else 0.0
    except Exception:
        metrics["avg_complexity"] = 0.0
        metrics["max_complexity"] = 0.0

    # test_file_ratio
    try:
        total_files = metrics["total_files"] or 1
        test_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE file_role = 'test'"
        ).fetchone()[0] or 0
        source_count = total_files - test_count
        metrics["test_file_ratio"] = round(
            test_count / max(source_count, 1), 3
        )
    except Exception:
        metrics["test_file_ratio"] = 0.0

    return metrics


def _record_snapshot(conn):
    """Collect current metrics and insert rows into metric_snapshots.

    Returns the dict of recorded metrics.
    """
    metrics = _collect_current_metrics(conn)
    ts = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    for name, value in metrics.items():
        conn.execute(
            "INSERT INTO metric_snapshots (timestamp, metric_name, metric_value) "
            "VALUES (?, ?, ?)",
            (ts, name, value),
        )
    conn.commit()
    return metrics


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------

def _get_metric_history(conn, metric_name, days):
    """Fetch time-ordered values for a metric within the last N days."""
    cutoff = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
    )
    # Compute the cutoff timestamp string
    from datetime import timedelta
    since = cutoff - timedelta(days=days)
    since_str = since.isoformat().replace("+00:00", "Z")

    rows = conn.execute(
        "SELECT timestamp, metric_value FROM metric_snapshots "
        "WHERE metric_name = ? AND timestamp >= ? "
        "ORDER BY timestamp ASC",
        (metric_name, since_str),
    ).fetchall()
    return [(r["timestamp"], r["metric_value"]) for r in rows]


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

    # Compute a rough magnitude (1-max_width)
    diff = abs(last_val - first_val)
    # Scale relative to max(abs(first), 1) to avoid div-by-zero
    scale = max(abs(first_val), 1.0)
    magnitude = min(max_width, max(1, int(diff / scale * max_width) + 1))

    improving = _direction_label(first_val, last_val, higher_is_better) == "improving"
    bar = "=" * magnitude
    if improving:
        return bar + ">"
    else:
        return "<" + bar


def _format_value(val):
    """Format a metric value for display — integers for whole numbers, else 2dp."""
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
            alerts.append({
                "metric": m["name"],
                "message": f"{m['name']} {_worsening_verb(m['name'])} -- investigate",
            })
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
    return re.match(
        r"^(feat|fix|refactor|chore|docs|style|test|perf|ci|build)(\(.+?\))?:\s",
        msg,
    ) is not None


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
            0.40 * complexity_pct
            + 0.25 * churn_pct
            + 0.20 * pagerank_pct
            + 0.10 * test_penalty
            + 0.05 * health_penalty
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
    churn_by_file: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "ai": 0, "human": 0}
    )
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

        events.append({
            "path": path,
            "timestamp": int(row["timestamp"] or 0),
            "lines": lines,
            "is_ai_commit": is_ai_commit,
        })

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
            "ai_blame_ratio": (
                round(ai_blame_ratio, 3) if ai_blame_ratio is not None else None
            ),
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
        avg_risk = (
            sum(r["risk_index"] for r in recs) / len(recs) if recs else 0.0
        )
        avg_complexity = (
            sum(r["complexity"] for r in recs) / len(recs) if recs else 0.0
        )
        avg_health = (
            sum(r["health_score"] for r in recs) / len(recs) if recs else 0.0
        )
        avg_percentile = (
            sum(r["risk_percentile"] for r in recs) / len(recs) if recs else 0.0
        )
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
        verdict = (
            "Insufficient cohort separation in the selected window; "
            "collect more mixed-author history"
        )
    elif delta > 0.5:
        verdict = (
            "AI cohort degrading faster than human cohort "
            f"(delta +{delta:.2f} risk/week)"
        )
    elif delta < -0.5:
        verdict = (
            "Human cohort degrading faster than AI cohort "
            f"(delta {delta:.2f} risk/week)"
        )
    else:
        verdict = (
            "AI and human cohorts have similar degradation velocity "
            f"(delta {delta:+.2f} risk/week)"
        )

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
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.option("--record", is_flag=True,
              help="Take a snapshot of current metrics and store it")
@click.option("--days", default=30, type=int,
              help="Show trends for last N days (default: 30)")
@click.option("--metric", default=None,
              help="Filter to a specific metric name")
@click.option(
    "--cohort-by-author",
    is_flag=True,
    help=(
        "Compare AI-authored vs human-authored degradation trajectories "
        "using git authorship + ai-ratio file signals"
    ),
)
@click.pass_context
def trends(ctx, record, days, metric, cohort_by_author):
    """Historical metric trends with sparkline output.

    Tracks key codebase metrics over time so you can answer
    "is the code getting better?"

    Use --record after indexing to store a snapshot.
    Use --days to control the time window.
    Use --metric to focus on a single metric.
    Use --cohort-by-author to compare AI vs human quality trajectories.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    if cohort_by_author and record:
        click.echo("Cannot combine --record with --cohort-by-author")
        raise SystemExit(1)

    if cohort_by_author:
        with open_db(readonly=True) as conn:
            cohort = _build_cohort_analysis(conn, days=max(days, 1))

        if not cohort:
            if json_mode:
                click.echo(to_json(json_envelope(
                    "trends",
                    summary={
                        "verdict": "No cohort trend data available",
                        "mode": "cohort-by-author",
                        "days": days,
                    },
                    mode="cohort-by-author",
                    days=days,
                    cohorts={},
                    signals={},
                )))
            else:
                click.echo("VERDICT: No cohort trend data available")
                click.echo()
                click.echo(
                    "Need indexed git history in the selected window. "
                    "Try a larger --days value."
                )
            return

        if json_mode:
            click.echo(to_json(json_envelope(
                "trends",
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
            )))
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
            rows.append([
                name.upper(),
                str(c["files"]),
                f"{c['avg_risk_index']:.2f}",
                f"{c['avg_risk_percentile']:.1f}",
                f"{c['degradation_velocity_per_week']:+.3f}/wk",
                c["trend_direction"],
                c["sparkline"],
            ])
        click.echo(format_table(
            ["COHORT", "FILES", "AVG_RISK", "RISK_PCTL", "VELOCITY", "TREND", "SPARK"],
            rows,
        ))

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
        return

    if record:
        # Record mode: store snapshot and optionally show it
        with open_db() as conn:
            recorded = _record_snapshot(conn)

        if json_mode:
            click.echo(to_json(json_envelope("trends",
                summary={
                    "verdict": "Snapshot recorded",
                    "metrics_recorded": len(recorded),
                },
                action="record",
                metrics=recorded,
            )))
        else:
            click.echo("VERDICT: Snapshot recorded")
            click.echo()
            for name, val in sorted(recorded.items()):
                click.echo(f"  {name}: {_format_value(val)}")
        return

    # Display mode: show trends
    with open_db(readonly=True) as conn:
        # Determine which metrics to show
        if metric:
            if metric not in _METRIC_DEFS:
                available = ", ".join(sorted(_METRIC_DEFS.keys()))
                click.echo(f"Unknown metric: {metric}")
                click.echo(f"Available: {available}")
                raise SystemExit(1)
            metric_names = [metric]
        else:
            metric_names = list(_METRIC_DEFS.keys())

        # Gather data for each metric
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

            metric_results.append({
                "name": name,
                "latest": last_val,
                "first": first_val,
                "change": change,
                "change_pct": change_pct,
                "direction": direction,
                "trend_bar": trend_bar,
                "history": values,
                "snapshots": len(values),
            })

        if not metric_results:
            if json_mode:
                click.echo(to_json(json_envelope("trends",
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
                )))
            else:
                click.echo("VERDICT: No trend data available")
                click.echo()
                click.echo("Run `roam trends --record` after indexing to start tracking.")
            return

        # Generate alerts
        alerts = _generate_alerts(metric_results)

        # Build verdict
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
                f"{tracked_count} metrics tracked over {days} days "
                f"({total_snapshots} snapshots) -- "
                f"all stable or improving"
            )
        else:
            verdict = (
                f"{tracked_count} metrics tracked over {days} days "
                f"({total_snapshots} snapshots) -- stable"
            )

        if json_mode:
            click.echo(to_json(json_envelope("trends",
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
                alerts=[
                    {"metric": a["metric"], "message": a["message"]}
                    for a in alerts
                ],
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Table
        rows = []
        for m in metric_results:
            change_str = f"{m['change']:+.2f}" if m["change"] != int(m["change"]) else f"{int(m['change']):+d}"
            rows.append([
                m["name"],
                _format_value(m["latest"]),
                f"{change_str} ({m['change_pct']})",
                m["trend_bar"],
                m["direction"],
            ])
        click.echo(format_table(
            ["METRIC", "LATEST", "CHANGE", "TREND", "DIRECTION"],
            rows,
        ))

        # Alerts
        if alerts:
            click.echo()
            click.echo("ALERTS:")
            for a in alerts:
                click.echo(f"  {a['metric']}: {a['message']}")
