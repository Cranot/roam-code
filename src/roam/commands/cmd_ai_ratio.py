"""Estimate the percentage of AI-generated code using git commit heuristics.

Uses five detection signals:
1. Change concentration (Gini coefficient of lines-changed distribution)
2. Burst additions (large single-commit additions with >80% adds)
3. Commit message patterns (co-author tags, conventional-commit prefixes)
4. Comment density anomaly (extreme deviation from codebase median)
5. Temporal patterns (burst sessions, regular intervals)

Each signal produces a probability [0, 1]. A weighted average yields the
overall AI ratio estimate.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import time

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------

_W_GINI = 0.25
_W_BURST = 0.25
_W_PATTERNS = 0.20
_W_COMMENT = 0.15
_W_TEMPORAL = 0.15

# ---------------------------------------------------------------------------
# Gini coefficient
# ---------------------------------------------------------------------------


def compute_gini(values: list[float]) -> float:
    """Compute the Gini coefficient for a list of non-negative values.

    Returns 0.0 for perfectly equal distributions and approaches 1.0
    for maximally unequal distributions. Returns 0.0 for empty or
    single-element lists.
    """
    n = len(values)
    if n <= 1:
        return 0.0
    total = sum(values)
    if total == 0:
        return 0.0
    sorted_vals = sorted(values)
    cum = 0.0
    numerator = 0.0
    for i, v in enumerate(sorted_vals):
        cum += v
        numerator += (2 * (i + 1) - n - 1) * v
    return numerator / (n * total)


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
_BURST_ADD_RATIO = 0.80     # 80% additions


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
    re.compile(r"co-authored-by:\s*.*?(claude|copilot|cursor|codeium|"
               r"tabnine|amazon\s*q|cody|gemini|windsurf|devin|"
               r"codex|gpt|chatgpt|anthropic|openai|aider)",
               re.IGNORECASE),
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


def _comment_density_signal(conn, file_ids: list[int] | None = None) -> tuple[float, int]:
    """Score [0, 1] from comment density anomaly across files.

    AI-generated code often has unusually uniform or extreme comment density.
    We look at the ratio of comment lines (lines containing # or //) to total
    lines for each file, then find outliers using median absolute deviation.

    Returns (score, anomalous_file_count).
    """
    # Get line counts from files table
    rows = conn.execute(
        "SELECT id, path, line_count FROM files WHERE line_count > 0"
    ).fetchall()
    if not rows:
        return 0.0, 0

    # We measure comment density from the file_stats or by simple heuristic:
    # use the lines stored in DB.  Since we lack a dedicated comment_ratio
    # column, we read files and compute ratios on-the-fly (limited sample).
    # For efficiency, use cognitive_load or complexity as proxy.
    # Actually, let's use the file on disk if available.
    project_root = find_project_root()
    densities: list[float] = []
    file_density_map: dict[int, float] = {}

    _COMMENT_MARKERS = ("#", "//", "/*", "*", "<!--", "--", ";", "%")
    sample_limit = 200  # limit disk reads for large codebases

    for i, row in enumerate(rows):
        if i >= sample_limit:
            break
        fid = row["id"]
        path = row["path"]
        total_lines = row["line_count"] or 0
        if total_lines < 5:
            continue
        full_path = project_root / path
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        comment_count = 0
        code_lines = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            code_lines += 1
            if any(stripped.startswith(m) for m in _COMMENT_MARKERS):
                comment_count += 1

        if code_lines < 5:
            continue
        density = comment_count / code_lines
        densities.append(density)
        file_density_map[fid] = density

    if len(densities) < 5:
        return 0.0, 0

    # Compute median and MAD (Median Absolute Deviation)
    sorted_d = sorted(densities)
    n = len(sorted_d)
    median = sorted_d[n // 2]
    mad = sorted(abs(d - median) for d in sorted_d)[n // 2]
    if mad < 0.001:
        mad = 0.001  # avoid division by zero

    # Files with modified z-score > 3.5 are anomalous
    anomalous = 0
    for d in densities:
        z = 0.6745 * (d - median) / mad
        if abs(z) > 3.5:
            anomalous += 1

    anomalous_ratio = anomalous / len(densities)
    # Map: 0-3% anomalous is normal, 3-20% is suspicious
    if anomalous_ratio < 0.03:
        score = 0.0
    elif anomalous_ratio > 0.25:
        score = 1.0
    else:
        score = (anomalous_ratio - 0.03) / (0.25 - 0.03)
    return score, anomalous


# ---------------------------------------------------------------------------
# Temporal pattern detection
# ---------------------------------------------------------------------------

_BURST_WINDOW_SECONDS = 600   # 10-minute window for burst detection
_MIN_BURST_COMMITS = 3        # at least 3 commits in window to qualify
_REGULARITY_THRESHOLD = 0.15  # coefficient of variation threshold for bot-like


def _temporal_signal(commits: list[dict]) -> tuple[float, int]:
    """Score [0, 1] from temporal commit patterns.

    AI coding sessions show:
    - Burst patterns: many commits in short windows
    - Regular intervals: bot-like consistent spacing

    Returns (score, burst_session_count).
    """
    if len(commits) < 3:
        return 0.0, 0

    # Sort by timestamp
    sorted_commits = sorted(commits, key=lambda c: c.get("timestamp", 0))
    timestamps = [c.get("timestamp", 0) for c in sorted_commits]

    # Detect burst sessions (clusters of commits within _BURST_WINDOW_SECONDS)
    burst_sessions = 0
    i = 0
    while i < len(timestamps):
        window_end = timestamps[i] + _BURST_WINDOW_SECONDS
        j = i + 1
        while j < len(timestamps) and timestamps[j] <= window_end:
            j += 1
        cluster_size = j - i
        if cluster_size >= _MIN_BURST_COMMITS:
            burst_sessions += 1
            i = j  # skip past the cluster
        else:
            i += 1

    # Detect regularity: compute coefficient of variation of inter-commit intervals
    intervals = []
    for i in range(1, len(timestamps)):
        dt = timestamps[i] - timestamps[i - 1]
        if dt > 0:  # ignore zero-interval duplicates
            intervals.append(float(dt))

    regularity_score = 0.0
    if len(intervals) >= 5:
        mean_iv = sum(intervals) / len(intervals)
        if mean_iv > 0:
            std_iv = math.sqrt(sum((x - mean_iv) ** 2 for x in intervals) / len(intervals))
            cv = std_iv / mean_iv
            # Very low CV (<0.15) suggests bot-like regularity
            if cv < _REGULARITY_THRESHOLD:
                regularity_score = 1.0 - (cv / _REGULARITY_THRESHOLD)

    # Burst ratio: fraction of commits that are in burst sessions
    burst_ratio = burst_sessions / max(1, len(timestamps) // _MIN_BURST_COMMITS)
    burst_ratio = min(burst_ratio, 1.0)

    # Combine: max of burst and regularity signals
    score = max(burst_ratio * 0.7, regularity_score * 0.3)
    return min(score, 1.0), burst_sessions


# ---------------------------------------------------------------------------
# Per-file AI probability
# ---------------------------------------------------------------------------


def _per_file_probability(
    conn,
    commits: list[dict],
) -> list[dict]:
    """Compute per-file AI probability from commit-level signals.

    Returns a list of dicts sorted by probability descending:
        [{"path": str, "probability": float, "reasons": [str]}, ...]
    """
    file_scores: dict[str, dict] = {}  # path -> {"scores": [], "reasons": set}

    for c in commits:
        msg = c.get("message", "")
        is_co_authored = _has_co_author_tag(msg)
        is_burst = _is_burst_add(c)

        for f in c["files"]:
            path = f["path"]
            if path not in file_scores:
                file_scores[path] = {"scores": [], "reasons": set()}

            entry = file_scores[path]
            score = 0.0
            if is_co_authored:
                score += 0.5
                entry["reasons"].add("co-author tag")
            if is_burst:
                added = f["lines_added"]
                if added > 50:
                    score += 0.3
                    entry["reasons"].add("burst add")
            if _has_ai_message_pattern(msg):
                score += 0.1
                entry["reasons"].add("AI-style message")
            entry["scores"].append(min(score, 1.0))

    results = []
    for path, data in file_scores.items():
        if not data["scores"]:
            continue
        # Use max score across commits (any strong signal counts)
        avg = sum(data["scores"]) / len(data["scores"])
        peak = max(data["scores"])
        # Weighted: 60% peak + 40% average
        prob = 0.6 * peak + 0.4 * avg
        if prob > 0.05:  # only include files with non-trivial probability
            results.append({
                "path": path,
                "probability": round(prob, 2),
                "reasons": sorted(data["reasons"]),
            })

    results.sort(key=lambda r: r["probability"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Trend calculation
# ---------------------------------------------------------------------------


def _compute_trend(commits: list[dict], now: int) -> dict:
    """Compute AI ratio trend over time.

    Splits the commit window into thirds and computes AI ratio for each.
    Returns dict with direction and data points.
    """
    if len(commits) < 6:
        return {"direction": "insufficient-data", "data_points": []}

    sorted_c = sorted(commits, key=lambda c: c.get("timestamp", 0))
    third = len(sorted_c) // 3
    segments = [
        sorted_c[:third],
        sorted_c[third:2 * third],
        sorted_c[2 * third:],
    ]

    data_points = []
    for seg in segments:
        if not seg:
            continue
        # Simple AI ratio per segment based on co-author + burst signals
        ai_count = sum(1 for c in seg if _has_co_author_tag(c.get("message", "")) or _is_burst_add(c))
        ratio = ai_count / len(seg) if seg else 0.0
        ts = seg[len(seg) // 2].get("timestamp", 0)
        days_ago = (now - ts) // 86400 if now > ts else 0
        data_points.append({
            "days_ago": days_ago,
            "ai_ratio": round(ratio, 2),
            "commits": len(seg),
        })

    # Determine direction
    if len(data_points) >= 2:
        first_ratio = data_points[0]["ai_ratio"]
        last_ratio = data_points[-1]["ai_ratio"]
        delta = last_ratio - first_ratio
        if delta > 0.05:
            direction = "increasing"
        elif delta < -0.05:
            direction = "decreasing"
        else:
            direction = "stable"
    else:
        direction = "insufficient-data"

    return {"direction": direction, "data_points": data_points}


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def _fetch_full_messages(hashes: list[str], project_root) -> dict[str, str]:
    """Fetch full commit messages (subject + body) from git.

    The DB only stores subject lines (%s), but co-author tags live in the
    body.  This function runs ``git log --format=%H%n%B`` to retrieve full
    messages and returns a ``{hash: full_message}`` mapping.
    """
    if not hashes:
        return {}
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
        if result.returncode != 0:
            return {}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}

    messages: dict[str, str] = {}
    current_hash: str | None = None
    current_lines: list[str] = []

    for line in result.stdout.splitlines():
        if line.startswith("COMMIT:"):
            if current_hash is not None:
                messages[current_hash] = "\n".join(current_lines)
            current_hash = line[7:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_hash is not None:
        messages[current_hash] = "\n".join(current_lines)

    return messages


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
        "SELECT id, hash, author, timestamp, message "
        "FROM git_commits WHERE timestamp >= ? ORDER BY timestamp DESC",
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
            "SELECT path, lines_added, lines_removed "
            "FROM git_file_changes WHERE commit_id = ?",
            (cid,),
        ).fetchall()

        # Use full message if available, fall back to DB subject
        message = full_messages.get(cr["hash"], cr["message"] or "")

        commits.append({
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
        })

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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.option("--since", default=90, type=int,
              help="Analyze commits from last N days (default: 90)")
@click.option("--detail", is_flag=True,
              help="Show per-file breakdown")
@click.pass_context
def ai_ratio(ctx, since, detail):
    """Estimate the percentage of AI-generated code from git patterns."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        result = analyse_ai_ratio(conn, since_days=since)

        ai_pct = round(result["ai_ratio"] * 100)
        confidence = result["confidence"]
        verdict = (
            f"~{ai_pct}% estimated AI-generated code "
            f"(confidence: {confidence})"
        )

        if json_mode:
            click.echo(to_json(json_envelope(
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
            )))
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if result["commits_analyzed"] == 0:
            click.echo("No commits found in the specified time range.")
            return

        # Signals section
        click.echo("SIGNALS:")
        sig = result["signals"]

        g = sig["gini"]
        click.echo(
            f"  Change concentration (Gini): {g['raw_value']:.2f} "
            f"-> suggests {round(g['score'] * 100)}% AI"
        )

        b = sig["burst_additions"]
        click.echo(
            f"  Burst additions: {b['burst_commits']}/{b['total_commits']} commits "
            f"({round(b['burst_commits'] / max(b['total_commits'], 1) * 100)}%) "
            f"are burst-adds -> suggests {round(b['score'] * 100)}% AI"
        )

        p = sig["commit_patterns"]
        click.echo(
            f"  Commit patterns: {p['co_author_count']} commits with AI co-author tags"
        )
        if p["ai_pattern_count"]:
            click.echo(
                f"    {p['ai_pattern_count']} with AI-style message patterns"
            )

        cd = sig["comment_density"]
        click.echo(
            f"  Comment density: {cd['anomalous_files']} files with anomalous density"
        )

        t = sig["temporal"]
        click.echo(
            f"  Temporal patterns: {t['burst_sessions']} burst sessions detected"
        )

        # Top AI-likely files
        top_files = result["top_ai_files"]
        if top_files:
            click.echo()
            limit = 20 if detail else 10
            shown = top_files[:limit]
            click.echo("TOP AI-LIKELY FILES:")
            for f in shown:
                reasons = ", ".join(f["reasons"]) if f["reasons"] else "heuristic"
                click.echo(
                    f"  {f['path']:<60s} ({round(f['probability'] * 100)}% AI probability "
                    f"-- {reasons})"
                )
            if len(top_files) > limit:
                click.echo(f"  (+{len(top_files) - limit} more, use --detail to see all)")

        # Trend
        trend = result["trend"]
        if trend["direction"] != "insufficient-data" and trend["data_points"]:
            click.echo()
            pts = trend["data_points"]
            parts = []
            for pt in pts:
                parts.append(f"{round(pt['ai_ratio'] * 100)}% ({pt['days_ago']}d ago)")
            click.echo(
                f"TREND: AI ratio {trend['direction']} -- "
                + " -> ".join(parts)
            )
