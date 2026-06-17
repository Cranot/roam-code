"""Estimate the percentage of AI-generated code using git commit heuristics.

Uses five detection signals:
1. Change concentration (Gini coefficient of lines-changed distribution)
2. Burst additions (large single-commit additions with >80% adds)
3. Commit message patterns (co-author tags, conventional-commit prefixes)
4. Comment density anomaly (extreme deviation from codebase median)
5. Temporal patterns (burst sessions, regular intervals)

Each signal produces a probability [0, 1]. A weighted average yields the
overall AI ratio estimate.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ai-ratio outputs are invocation-scoped AI-generation
probability estimates — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import time

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.graph.stats import gini_coefficient as compute_gini
from roam.output.formatter import json_envelope, to_json

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------

_W_GINI = 0.25
_W_BURST = 0.25
_W_PATTERNS = 0.20
_W_COMMENT = 0.15
_W_TEMPORAL = 0.15


def _gini_signal(commits: list[dict]) -> float:
    """Score [0, 1] based on change concentration across commits.

    High Gini (>0.7) suggests AI-heavy commits (large, concentrated
    changes to few files).
    """
    if not commits:
        return 0.0
    changes_per_commit = []
    for c in commits:
        total = sum(f["lines_added"] + f["lines_removed"] for f in c["files"])
        changes_per_commit.append(float(total))
    gini = compute_gini(changes_per_commit)
    # Map gini to AI probability:
    #   gini < 0.4 -> 0.0 (normal human distribution)
    #   gini 0.4-0.7 -> linear ramp
    #   gini > 0.7 -> approaches 1.0
    if gini < 0.4:
        return 0.0
    if gini > 0.85:
        return 1.0
    return (gini - 0.4) / (0.85 - 0.4)


# ---------------------------------------------------------------------------
# Burst addition detection
# ---------------------------------------------------------------------------

_BURST_ADD_THRESHOLD = 100  # lines
_BURST_ADD_RATIO = 0.80  # 80% additions


def _is_burst_add(commit: dict) -> bool:
    """True if commit is a burst-addition: >80% adds and >100 lines added."""
    total_added = sum(f["lines_added"] for f in commit["files"])
    total_removed = sum(f["lines_removed"] for f in commit["files"])
    total = total_added + total_removed
    if total_added < _BURST_ADD_THRESHOLD:
        return False
    if total == 0:
        return False
    return (total_added / total) >= _BURST_ADD_RATIO


def _burst_signal(commits: list[dict]) -> tuple[float, int]:
    """Score [0, 1] from burst-addition commit ratio.

    Returns (score, burst_count).
    """
    if not commits:
        return 0.0, 0
    burst_count = sum(1 for c in commits if _is_burst_add(c))
    ratio = burst_count / len(commits)
    # Map: 0-5% -> low, 5-30% -> linear ramp, >30% -> high
    if ratio < 0.05:
        score = 0.0
    elif ratio > 0.50:
        score = 1.0
    else:
        score = (ratio - 0.05) / (0.50 - 0.05)
    return score, burst_count


# ---------------------------------------------------------------------------
# Commit message pattern detection
# ---------------------------------------------------------------------------

# AI co-author patterns (case-insensitive)
_CO_AUTHOR_PATTERNS = [
    re.compile(
        r"co-authored-by:\s*.*?(claude|copilot|cursor|codeium|"
        r"tabnine|amazon\s*q|cody|gemini|windsurf|devin|"
        r"codex|gpt|chatgpt|anthropic|openai|aider)",
        re.IGNORECASE,
    ),
]

# Conventional commit prefixes heavily used by AI tools
_AI_MSG_PATTERNS = [
    re.compile(r"^(feat|fix|refactor|chore|docs|style|test|perf|ci|build)(\(.+?\))?:\s", re.IGNORECASE),
    re.compile(r"^(Add|Update|Implement|Create|Remove|Delete|Refactor|Fix)\s", re.IGNORECASE),
    re.compile(r"^(Generated|Auto-generated|Automated)\s", re.IGNORECASE),
]


def _has_co_author_tag(message: str) -> bool:
    """Check if the commit message contains an AI co-author tag."""
    return any(p.search(message) for p in _CO_AUTHOR_PATTERNS)


def _has_ai_message_pattern(message: str) -> bool:
    """Check if the commit message matches common AI-generated patterns."""
    return any(p.search(message) for p in _AI_MSG_PATTERNS)


def _pattern_signal(commits: list[dict]) -> tuple[float, int, int]:
    """Score [0, 1] from commit message patterns.

    Returns (score, co_author_count, pattern_count).
    """
    if not commits:
        return 0.0, 0, 0
    co_author_count = 0
    pattern_count = 0
    for c in commits:
        msg = c.get("message", "")
        if _has_co_author_tag(msg):
            co_author_count += 1
        elif _has_ai_message_pattern(msg):
            pattern_count += 1

    # Co-author tags are strong evidence; message patterns are weaker
    co_author_ratio = co_author_count / len(commits)
    pattern_ratio = pattern_count / len(commits)
    # Strong signal: co-author tags directly map to confirmed AI usage
    # Weak signal: conventional commit style (many humans use it too)
    score = min(1.0, co_author_ratio + pattern_ratio * 0.15)
    return score, co_author_count, pattern_count


# ---------------------------------------------------------------------------
# Comment density anomaly
# ---------------------------------------------------------------------------


_COMMENT_MARKERS = ("#", "//", "/*", "*", "<!--", "--", ";", "%")
_COMMENT_SAMPLE_LIMIT = 200


def _comment_density_rows(conn, file_ids: list[int] | None = None):
    if not file_ids:
        return conn.execute("SELECT id, path, line_count FROM files WHERE line_count > 0").fetchall()

    sample_ids = file_ids[:_COMMENT_SAMPLE_LIMIT]
    placeholders = ",".join("?" for _ in sample_ids)
    return conn.execute(
        f"SELECT id, path, line_count FROM files WHERE line_count > 0 AND id IN ({placeholders})",
        tuple(sample_ids),
    ).fetchall()


def _code_comment_counts(text: str) -> tuple[int, int]:
    comment_count = 0
    code_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        code_lines += 1
        if any(stripped.startswith(marker) for marker in _COMMENT_MARKERS):
            comment_count += 1
    return code_lines, comment_count


def _file_comment_density(project_root, row) -> tuple[int, float] | None:
    total_lines = row["line_count"] or 0
    if total_lines < 5:
        return None

    try:
        text = (project_root / row["path"]).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    code_lines, comment_count = _code_comment_counts(text)
    if code_lines < 5:
        return None
    return row["id"], comment_count / code_lines


def _sample_comment_densities(conn, file_ids: list[int] | None = None) -> list[float]:
    project_root = find_project_root()
    densities: list[float] = []
    for row in _comment_density_rows(conn, file_ids)[:_COMMENT_SAMPLE_LIMIT]:
        sample = _file_comment_density(project_root, row)
        if sample is not None:
            _file_id, density = sample
            densities.append(density)
    return densities


def _median(values: list[float]) -> float:
    sorted_values = sorted(values)
    return sorted_values[len(sorted_values) // 2]


def _median_absolute_deviation(values: list[float], median: float) -> float:
    mad = sorted(abs(value - median) for value in values)[len(values) // 2]
    return max(mad, 0.001)


def _anomalous_density_count(densities: list[float], median: float, mad: float) -> int:
    return sum(1 for density in densities if abs(0.6745 * (density - median) / mad) > 3.5)


def _density_anomaly_score(anomalous_ratio: float) -> float:
    if anomalous_ratio < 0.03:
        return 0.0
    if anomalous_ratio > 0.25:
        return 1.0
    return (anomalous_ratio - 0.03) / (0.25 - 0.03)


def _comment_density_signal(conn, file_ids: list[int] | None = None) -> tuple[float, int]:
    """Score [0, 1] from comment density anomaly across files.

    AI-generated code often has unusually uniform or extreme comment density.
    We look at the ratio of comment lines (lines containing # or //) to total
    lines for each file, then find outliers using median absolute deviation.

    Returns (score, anomalous_file_count).
    """
    densities = _sample_comment_densities(conn, file_ids)
    if len(densities) < 5:
        return 0.0, 0

    median = _median(densities)
    mad = _median_absolute_deviation(densities, median)
    anomalous = _anomalous_density_count(densities, median, mad)
    anomalous_ratio = anomalous / len(densities)
    return _density_anomaly_score(anomalous_ratio), anomalous


# ---------------------------------------------------------------------------
# Temporal pattern detection
# ---------------------------------------------------------------------------

_BURST_WINDOW_SECONDS = 600  # 10-minute window for burst detection
_MIN_BURST_COMMITS = 3  # at least 3 commits in window to qualify
_REGULARITY_THRESHOLD = 0.15  # coefficient of variation threshold for bot-like


def _commit_timestamps(commits: list[dict]) -> list[int]:
    return [commit.get("timestamp", 0) for commit in sorted(commits, key=lambda c: c.get("timestamp", 0))]


def _burst_session_count(timestamps: list[int]) -> int:
    burst_sessions = 0
    i = 0
    while i < len(timestamps):
        window_end = timestamps[i] + _BURST_WINDOW_SECONDS
        j = i + 1
        while j < len(timestamps) and timestamps[j] <= window_end:
            j += 1
        if j - i >= _MIN_BURST_COMMITS:
            burst_sessions += 1
            i = j
        else:
            i += 1
    return burst_sessions


def _positive_intervals(timestamps: list[int]) -> list[float]:
    return [
        float(timestamps[i] - timestamps[i - 1])
        for i in range(1, len(timestamps))
        if timestamps[i] - timestamps[i - 1] > 0
    ]


def _regularity_score(intervals: list[float]) -> float:
    if len(intervals) < 5:
        return 0.0
    mean_iv = sum(intervals) / len(intervals)
    if mean_iv <= 0:
        return 0.0
    std_iv = math.sqrt(sum((x - mean_iv) ** 2 for x in intervals) / len(intervals))
    cv = std_iv / mean_iv
    if cv >= _REGULARITY_THRESHOLD:
        return 0.0
    return 1.0 - (cv / _REGULARITY_THRESHOLD)


def _temporal_signal(commits: list[dict]) -> tuple[float, int]:
    """Score [0, 1] from temporal commit patterns.

    AI coding sessions show:
    - Burst patterns: many commits in short windows
    - Regular intervals: bot-like consistent spacing

    Returns (score, burst_session_count).
    """
    if len(commits) < 3:
        return 0.0, 0

    timestamps = _commit_timestamps(commits)
    burst_sessions = _burst_session_count(timestamps)
    regularity = _regularity_score(_positive_intervals(timestamps))
    burst_ratio = burst_sessions / max(1, len(timestamps) // _MIN_BURST_COMMITS)
    burst_ratio = min(burst_ratio, 1.0)

    score = max(burst_ratio * 0.7, regularity * 0.3)
    return min(score, 1.0), burst_sessions


# ---------------------------------------------------------------------------
# Per-file AI probability
# ---------------------------------------------------------------------------


def _empty_file_score() -> dict:
    return {"scores": [], "reasons": set()}


def _score_file_change(
    commit_message: str, file_change: dict, is_co_authored: bool, is_burst: bool
) -> tuple[float, set[str]]:
    score = 0.0
    reasons: set[str] = set()
    if is_co_authored:
        score += 0.5
        reasons.add("co-author tag")
    if is_burst and file_change["lines_added"] > 50:
        score += 0.3
        reasons.add("burst add")
    if _has_ai_message_pattern(commit_message):
        score += 0.1
        reasons.add("AI-style message")
    return min(score, 1.0), reasons


def _record_file_score(file_scores: dict[str, dict], path: str, score: float, reasons: set[str]) -> None:
    entry = file_scores.setdefault(path, _empty_file_score())
    entry["scores"].append(score)
    entry["reasons"].update(reasons)


def _accumulate_file_scores(commits: list[dict]) -> dict[str, dict]:
    file_scores: dict[str, dict] = {}
    for commit in commits:
        message = commit.get("message", "")
        is_co_authored = _has_co_author_tag(message)
        is_burst = _is_burst_add(commit)
        for file_change in commit["files"]:
            score, reasons = _score_file_change(message, file_change, is_co_authored, is_burst)
            _record_file_score(file_scores, file_change["path"], score, reasons)
    return file_scores


def _file_probability_result(path: str, data: dict) -> dict | None:
    if not data["scores"]:
        return None

    avg = sum(data["scores"]) / len(data["scores"])
    peak = max(data["scores"])
    probability = 0.6 * peak + 0.4 * avg
    if probability <= 0.05:
        return None
    return {
        "path": path,
        "probability": round(probability, 2),
        "reasons": sorted(data["reasons"]),
    }


def _per_file_probability(
    conn,
    commits: list[dict],
) -> list[dict]:
    """Compute per-file AI probability from commit-level signals.

    Returns a list of dicts sorted by probability descending:
        [{"path": str, "probability": float, "reasons": [str]}, ...]
    """
    file_scores = _accumulate_file_scores(commits)
    results = [_file_probability_result(path, data) for path, data in file_scores.items()]
    results = [result for result in results if result is not None]
    results.sort(key=lambda r: r["probability"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Trend calculation
# ---------------------------------------------------------------------------


def _trend_segments(commits: list[dict]) -> list[list[dict]]:
    sorted_commits = sorted(commits, key=lambda c: c.get("timestamp", 0))
    third = len(sorted_commits) // 3
    return [
        sorted_commits[:third],
        sorted_commits[third : 2 * third],
        sorted_commits[2 * third :],
    ]


def _segment_ai_count(segment: list[dict]) -> int:
    return sum(1 for commit in segment if _has_co_author_tag(commit.get("message", "")) or _is_burst_add(commit))


def _trend_data_point(segment: list[dict], now: int) -> dict:
    ai_count = _segment_ai_count(segment)
    ratio = ai_count / len(segment)
    ts = segment[len(segment) // 2].get("timestamp", 0)
    days_ago = (now - ts) // 86400 if now > ts else 0
    return {
        "days_ago": days_ago,
        "ai_ratio": round(ratio, 2),
        "commits": len(segment),
    }


def _trend_direction(data_points: list[dict]) -> str:
    if len(data_points) < 2:
        return "insufficient-data"

    delta = data_points[-1]["ai_ratio"] - data_points[0]["ai_ratio"]
    if delta > 0.05:
        return "increasing"
    if delta < -0.05:
        return "decreasing"
    return "stable"


def _compute_trend(commits: list[dict], now: int) -> dict:
    """Compute AI ratio trend over time.

    Splits the commit window into thirds and computes AI ratio for each.
    Returns dict with direction and data points.
    """
    if len(commits) < 6:
        return {"direction": "insufficient-data", "data_points": []}

    data_points = [_trend_data_point(segment, now) for segment in _trend_segments(commits) if segment]
    return {"direction": _trend_direction(data_points), "data_points": data_points}


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def _run_full_message_log(hashes: list[str], project_root):
    try:
        result = subprocess.run(
            ["git", "log", "--format=COMMIT:%H%n%B", "--no-walk", "--stdin"],
            input="\n".join(hashes),
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result if result.returncode == 0 else None


def _store_commit_message(messages: dict[str, str], commit_hash: str | None, lines: list[str]) -> None:
    if commit_hash is not None:
        messages[commit_hash] = "\n".join(lines)


def _parse_full_messages(stdout: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    current_hash: str | None = None
    current_lines: list[str] = []

    for line in stdout.splitlines():
        if line.startswith("COMMIT:"):
            _store_commit_message(messages, current_hash, current_lines)
            current_hash = line[7:].strip()
            current_lines = []
            continue
        current_lines.append(line)

    _store_commit_message(messages, current_hash, current_lines)
    return messages


def _fetch_full_messages(hashes: list[str], project_root) -> dict[str, str]:
    """Fetch full commit messages (subject + body) from git.

    The DB only stores subject lines (%s), but co-author tags live in the
    body.  This function runs ``git log --format=%H%n%B`` to retrieve full
    messages and returns a ``{hash: full_message}`` mapping.
    """
    if not hashes:
        return {}

    result = _run_full_message_log(hashes, project_root)
    return _parse_full_messages(result.stdout) if result is not None else {}


def _get_commits_from_db(conn, since_days: int) -> list[dict]:
    """Load commits and their file changes from the indexed database.

    Enriches commit messages with full bodies from git (for co-author
    tag detection).

    Returns commits in the same dict format as parse_git_log():
        [{"hash": str, "author": str, "timestamp": int, "message": str,
          "files": [{"path": str, "lines_added": int, "lines_removed": int}]}]
    """
    cutoff = int(time.time()) - (since_days * 86400)

    commit_rows = conn.execute(
        "SELECT id, hash, author, timestamp, message FROM git_commits WHERE timestamp >= ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()

    if not commit_rows:
        return []

    # Fetch full messages for co-author tag detection
    hashes = [cr["hash"] for cr in commit_rows]
    project_root = find_project_root()
    full_messages = _fetch_full_messages(hashes, project_root)

    commits = []
    for cr in commit_rows:
        cid = cr["id"]
        files = conn.execute(
            "SELECT path, lines_added, lines_removed FROM git_file_changes WHERE commit_id = ?",
            (cid,),
        ).fetchall()

        # Use full message if available, fall back to DB subject
        message = full_messages.get(cr["hash"], cr["message"] or "")

        commits.append(
            {
                "hash": cr["hash"],
                "author": cr["author"],
                "timestamp": cr["timestamp"],
                "message": message,
                "files": [
                    {
                        "path": f["path"],
                        "lines_added": f["lines_added"] or 0,
                        "lines_removed": f["lines_removed"] or 0,
                    }
                    for f in files
                ],
            }
        )

    return commits


def _confidence_label(commit_count: int) -> str:
    """Map commit count to confidence level."""
    if commit_count < 50:
        return "LOW"
    if commit_count <= 200:
        return "MEDIUM"
    return "HIGH"


def analyse_ai_ratio(conn, since_days: int = 90) -> dict:
    """Run the full AI ratio analysis.

    Returns a dict with all results including signals, top files, and trend.
    """
    commits = _get_commits_from_db(conn, since_days)
    now = int(time.time())

    if not commits:
        return {
            "ai_ratio": 0.0,
            "confidence": "LOW",
            "commits_analyzed": 0,
            "signals": {},
            "top_ai_files": [],
            "trend": {"direction": "insufficient-data", "data_points": []},
        }

    # Run signals
    gini_score = _gini_signal(commits)
    burst_score, burst_count = _burst_signal(commits)
    pattern_score, co_author_count, ai_pattern_count = _pattern_signal(commits)
    comment_score, anomalous_files = _comment_density_signal(conn)
    temporal_score, burst_sessions = _temporal_signal(commits)

    # Weighted aggregate
    ai_ratio = (
        _W_GINI * gini_score
        + _W_BURST * burst_score
        + _W_PATTERNS * pattern_score
        + _W_COMMENT * comment_score
        + _W_TEMPORAL * temporal_score
    )
    ai_ratio = round(min(ai_ratio, 1.0), 2)

    confidence = _confidence_label(len(commits))

    # Gini value for display (raw, not mapped)
    changes_per_commit = []
    for c in commits:
        total = sum(f["lines_added"] + f["lines_removed"] for f in c["files"])
        changes_per_commit.append(float(total))
    raw_gini = round(compute_gini(changes_per_commit), 2)

    signals = {
        "gini": {
            "raw_value": raw_gini,
            "score": round(gini_score, 2),
            "weight": _W_GINI,
        },
        "burst_additions": {
            "burst_commits": burst_count,
            "total_commits": len(commits),
            "score": round(burst_score, 2),
            "weight": _W_BURST,
        },
        "commit_patterns": {
            "co_author_count": co_author_count,
            "ai_pattern_count": ai_pattern_count,
            "score": round(pattern_score, 2),
            "weight": _W_PATTERNS,
        },
        "comment_density": {
            "anomalous_files": anomalous_files,
            "score": round(comment_score, 2),
            "weight": _W_COMMENT,
        },
        "temporal": {
            "burst_sessions": burst_sessions,
            "score": round(temporal_score, 2),
            "weight": _W_TEMPORAL,
        },
    }

    # Per-file breakdown
    top_files = _per_file_probability(conn, commits)

    # Trend
    trend = _compute_trend(commits, now)

    return {
        "ai_ratio": ai_ratio,
        "confidence": confidence,
        "commits_analyzed": len(commits),
        "signals": signals,
        "top_ai_files": top_files,
        "trend": trend,
    }


def _ai_ratio_verdict(result: dict) -> str:
    ai_pct = round(result["ai_ratio"] * 100)
    confidence = result["confidence"]
    return f"~{ai_pct}% estimated AI-generated code (confidence: {confidence})"


def _ai_ratio_json_payload(result: dict, verdict: str, since: int) -> dict:
    confidence = result["confidence"]
    return json_envelope(
        "ai-ratio",
        summary={
            "verdict": verdict,
            "ai_ratio": result["ai_ratio"],
            "confidence": confidence,
            "commits_analyzed": result["commits_analyzed"],
        },
        ai_ratio=result["ai_ratio"],
        confidence=confidence,
        commits_analyzed=result["commits_analyzed"],
        since_days=since,
        signals=result["signals"],
        top_ai_files=result["top_ai_files"][:20],
        trend=result["trend"],
    )


def _emit_gini_signal(signals: dict) -> None:
    gini = signals["gini"]
    click.echo(f"  Change concentration (Gini): {gini['raw_value']:.2f} -> suggests {round(gini['score'] * 100)}% AI")


def _emit_burst_signal(signals: dict) -> None:
    burst = signals["burst_additions"]
    total_commits = max(burst["total_commits"], 1)
    burst_pct = round(burst["burst_commits"] / total_commits * 100)
    click.echo(
        f"  Burst additions: {burst['burst_commits']}/{burst['total_commits']} commits "
        f"({burst_pct}%) are burst-adds -> suggests {round(burst['score'] * 100)}% AI"
    )


def _emit_pattern_signal(signals: dict) -> None:
    patterns = signals["commit_patterns"]
    click.echo(f"  Commit patterns: {patterns['co_author_count']} commits with AI co-author tags")
    if patterns["ai_pattern_count"]:
        click.echo(f"    {patterns['ai_pattern_count']} with AI-style message patterns")


def _emit_comment_density_signal(signals: dict) -> None:
    density = signals["comment_density"]
    click.echo(f"  Comment density: {density['anomalous_files']} files with anomalous density")


def _emit_temporal_signal(signals: dict) -> None:
    temporal = signals["temporal"]
    click.echo(f"  Temporal patterns: {temporal['burst_sessions']} burst sessions detected")


def _emit_signal_lines(signals: dict) -> None:
    click.echo("SIGNALS:")
    _emit_gini_signal(signals)
    _emit_burst_signal(signals)
    _emit_pattern_signal(signals)
    _emit_comment_density_signal(signals)
    _emit_temporal_signal(signals)


def _top_file_limit(detail: bool) -> int:
    return 20 if detail else 10


def _format_top_file_line(file_info: dict) -> str:
    reasons = ", ".join(file_info["reasons"]) if file_info["reasons"] else "heuristic"
    probability = round(file_info["probability"] * 100)
    return f"  {file_info['path']:<60s} ({probability}% AI probability -- {reasons})"


def _emit_more_top_files_hint(top_files: list[dict], limit: int) -> None:
    remaining = len(top_files) - limit
    if remaining > 0:
        click.echo(f"  (+{remaining} more, use --detail to see all)")


def _emit_top_ai_files(top_files: list[dict], detail: bool) -> None:
    if not top_files:
        return

    click.echo()
    limit = _top_file_limit(detail)
    click.echo("TOP AI-LIKELY FILES:")
    for file_info in top_files[:limit]:
        click.echo(_format_top_file_line(file_info))
    _emit_more_top_files_hint(top_files, limit)


def _trend_is_renderable(trend: dict) -> bool:
    return trend["direction"] != "insufficient-data" and bool(trend["data_points"])


def _format_trend_point(point: dict) -> str:
    return f"{round(point['ai_ratio'] * 100)}% ({point['days_ago']}d ago)"


def _emit_ai_ratio_trend(trend: dict) -> None:
    if not _trend_is_renderable(trend):
        return

    click.echo()
    parts = [_format_trend_point(point) for point in trend["data_points"]]
    click.echo(f"TREND: AI ratio {trend['direction']} -- " + " -> ".join(parts))


def _emit_ai_ratio_text(result: dict, verdict: str, detail: bool) -> None:
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if result["commits_analyzed"] == 0:
        click.echo("No commits found in the specified time range.")
        return

    _emit_signal_lines(result["signals"])
    _emit_top_ai_files(result["top_ai_files"], detail)
    _emit_ai_ratio_trend(result["trend"])


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="ai-ratio",
    category="health",
    summary="Estimate the percentage of AI-generated code from git patterns",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--since", default=90, type=int, help="Analyze commits from last <N> days (default: 90)")
@click.option("--detail", is_flag=True, help="Show per-file breakdown")
@click.pass_context
def ai_ratio(ctx, since, detail):
    """Estimate the percentage of AI-generated code from git patterns.

    Unlike ``vibe-check`` (which detects AI-generated source-code patterns)
    and ``dev-profile`` (which profiles individual developer commit behavior),
    this command estimates the codebase-wide AI-generated code ratio from
    commit history signals: co-author tags, burst additions, and temporal
    patterns.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    detail = bool(detail or (ctx.obj.get("detail", False) if ctx.obj else False))
    ensure_index()

    with open_db(readonly=True) as conn:
        result = analyse_ai_ratio(conn, since_days=since)
        verdict = _ai_ratio_verdict(result)

        if json_mode:
            click.echo(to_json(_ai_ratio_json_payload(result, verdict, since)))
            return

        _emit_ai_ratio_text(result, verdict, detail)
