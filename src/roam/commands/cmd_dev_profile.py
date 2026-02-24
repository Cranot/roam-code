"""Analyze developer commit patterns and behavioral metrics for PR risk scoring."""

from __future__ import annotations

import subprocess
from collections import defaultdict, Counter
from datetime import datetime, timezone

import click

from roam.output.formatter import format_table, to_json, json_envelope


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def gini_coefficient(values: list[int | float]) -> float:
    """Calculate Gini coefficient (0=perfectly equal, 1=perfectly concentrated).

    A Gini of 0 means changes are spread perfectly evenly across files.
    A Gini of 1 means all changes are in a single file.
    """
    if not values or sum(values) == 0:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = sum((2 * i - n + 1) * v for i, v in enumerate(sorted_vals))
    return cumulative / (n * sum(sorted_vals))


def detect_bursts(timestamps: list[int], window_seconds: int = 3600) -> dict:
    """Detect unusually high commit frequency in short time windows.

    Returns a dict with:
    - max_in_window: the maximum number of commits in any single window
    - avg_per_window: average commits across all windows where work occurred
    - burst_score: ratio of max_in_window to avg_per_window (1.0 = no bursts)
    - burst_windows: list of (window_start_epoch, count) for windows > 2 commits
    """
    if not timestamps:
        return {"max_in_window": 0, "avg_per_window": 0.0, "burst_score": 1.0, "burst_windows": []}

    sorted_ts = sorted(timestamps)

    # Sliding window: for each commit, count commits in [ts, ts+window]
    window_counts = []
    for i, ts in enumerate(sorted_ts):
        count = sum(1 for t in sorted_ts if ts <= t <= ts + window_seconds)
        window_counts.append(count)

    max_in_window = max(window_counts) if window_counts else 0
    avg_per_window = sum(window_counts) / len(window_counts) if window_counts else 0.0
    burst_score = (max_in_window / avg_per_window) if avg_per_window > 0 else 1.0

    # Identify burst windows (more than 2 commits in a window)
    burst_windows = []
    seen_starts = set()
    for i, ts in enumerate(sorted_ts):
        count = sum(1 for t in sorted_ts if ts <= t <= ts + window_seconds)
        if count > 2:
            # Normalize to hour-aligned window start
            hour_start = (ts // window_seconds) * window_seconds
            if hour_start not in seen_starts:
                burst_windows.append({"window_start": ts, "commit_count": count})
                seen_starts.add(hour_start)

    return {
        "max_in_window": max_in_window,
        "avg_per_window": round(avg_per_window, 2),
        "burst_score": round(burst_score, 2),
        "burst_windows": burst_windows[:10],  # cap to avoid huge output
    }


def detect_sessions(timestamps: list[int], gap_seconds: int = 1800) -> dict:
    """Detect coding sessions: groups of commits separated by < gap_seconds.

    Returns a dict with:
    - session_count: total number of sessions
    - avg_session_length_minutes: average session duration
    - avg_commits_per_session: average number of commits per session
    - max_session_length_minutes: longest session
    """
    if not timestamps:
        return {
            "session_count": 0,
            "avg_session_length_minutes": 0.0,
            "avg_commits_per_session": 0.0,
            "max_session_length_minutes": 0.0,
        }

    sorted_ts = sorted(timestamps)
    sessions: list[list[int]] = []
    current_session = [sorted_ts[0]]

    for ts in sorted_ts[1:]:
        if ts - current_session[-1] <= gap_seconds:
            current_session.append(ts)
        else:
            sessions.append(current_session)
            current_session = [ts]
    sessions.append(current_session)

    session_lengths = [
        (s[-1] - s[0]) / 60.0 for s in sessions if len(s) > 1
    ]
    commits_per_session = [len(s) for s in sessions]

    return {
        "session_count": len(sessions),
        "avg_session_length_minutes": (
            round(sum(session_lengths) / len(session_lengths), 1)
            if session_lengths else 0.0
        ),
        "avg_commits_per_session": round(
            sum(commits_per_session) / len(commits_per_session), 1
        ) if commits_per_session else 0.0,
        "max_session_length_minutes": (
            round(max(session_lengths), 1) if session_lengths else 0.0
        ),
    }


def hour_distribution(timestamps: list[int]) -> list[int]:
    """Return 24-element list counting commits per hour-of-day (UTC)."""
    hist = [0] * 24
    for ts in timestamps:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hist[dt.hour] += 1
    return hist


def day_distribution(timestamps: list[int]) -> list[int]:
    """Return 7-element list counting commits per weekday (Mon=0, Sun=6)."""
    hist = [0] * 7
    for ts in timestamps:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hist[dt.weekday()] += 1
    return hist


def risk_score(
    late_night_pct: float,
    weekend_pct: float,
    scatter_gini: float,
    burst_score: float,
) -> int:
    """Compute a 0-100 behavioral risk modifier.

    Higher values indicate patterns correlated with higher PR risk:
    - Late-night commits (higher error rates)
    - Weekend commits (reduced review availability)
    - High change scatter (broad, unfocused changes)
    - Burst coding (rapid-fire commits without reflection)
    """
    score = 0.0
    score += late_night_pct * 0.30   # 30% weight
    score += weekend_pct * 0.20      # 20% weight
    score += scatter_gini * 100 * 0.30  # 30% weight
    # burst_score: 1.0 = normal, higher = more bursty; cap at 5x
    normalized_burst = min((burst_score - 1.0) / 4.0, 1.0) if burst_score > 1.0 else 0.0
    score += normalized_burst * 100 * 0.20  # 20% weight
    return min(100, int(round(score)))


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------


def _run_git_log(days: int, root: str) -> str | None:
    """Run git log and return raw output, or None on failure."""
    try:
        result = subprocess.run(
            [
                "git", "log",
                "--format=%H|%ae|%aI|%s",
                "--numstat",
                f"--since={days} days ago",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def parse_git_log(raw: str) -> list[dict]:
    """Parse git log --format=... --numstat output into a list of commit dicts.

    Each commit dict has:
    - hash: str
    - author_email: str
    - timestamp: int (epoch seconds)
    - subject: str
    - files: list of file paths touched
    - lines_added: int
    - lines_removed: int
    """
    commits: list[dict] = []
    current: dict | None = None

    for line in raw.splitlines():
        line = line.rstrip()

        # Header line: "HASH|EMAIL|ISO8601|subject"
        if "|" in line and not line[0].isdigit() and line[0] != "-" and "\t" not in line:
            parts = line.split("|", 3)
            # Accept git SHA: exactly 40 hex chars (full) or 7+ (abbreviated)
            hash_candidate = parts[0].strip()
            _is_sha = (
                len(parts) >= 3
                and 7 <= len(hash_candidate) <= 41
                and all(c in "0123456789abcdefABCDEF" for c in hash_candidate)
            )
            if _is_sha:
                if current is not None:
                    commits.append(current)
                ts = _parse_iso8601(parts[2].strip())
                current = {
                    "hash": parts[0].strip(),
                    "author_email": parts[1].strip().lower(),
                    "timestamp": ts,
                    "subject": parts[3].strip() if len(parts) > 3 else "",
                    "files": [],
                    "lines_added": 0,
                    "lines_removed": 0,
                }
                continue

        # numstat line: "added\tremoved\tfilepath"
        if current is not None and "\t" in line:
            parts = line.split("\t", 2)
            if len(parts) == 3:
                added_str, removed_str, filepath = parts
                try:
                    added = int(added_str) if added_str != "-" else 0
                    removed = int(removed_str) if removed_str != "-" else 0
                except ValueError:
                    added, removed = 0, 0
                current["files"].append(filepath.strip())
                current["lines_added"] += added
                current["lines_removed"] += removed

    if current is not None:
        commits.append(current)

    return commits


def _parse_iso8601(s: str) -> int:
    """Parse an ISO 8601 timestamp to epoch seconds. Returns 0 on failure."""
    try:
        # Python 3.11+ supports %z with colon offset; older versions need strip
        s_clean = s.replace("Z", "+00:00")
        # Handle offsets like +05:30 — fromisoformat works in 3.7+
        dt = datetime.fromisoformat(s_clean)
        return int(dt.timestamp())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Per-author profile computation
# ---------------------------------------------------------------------------


def _top_dirs(files: list[str], top_n: int = 5) -> list[dict]:
    """Count files per top-level directory and return top N."""
    dir_counts: Counter = Counter()
    for f in files:
        f = f.replace("\\", "/")
        slash = f.find("/")
        top_dir = f[:slash] if slash != -1 else "."
        dir_counts[top_dir] += 1
    return [
        {"directory": d, "file_count": c}
        for d, c in dir_counts.most_common(top_n)
    ]


def build_author_profile(
    author_email: str,
    commits: list[dict],
) -> dict:
    """Build a behavioral profile for a single author from their commits."""
    if not commits:
        return {
            "author": author_email,
            "commit_count": 0,
            "risk_score": 0,
            "verdict": "no commits",
        }

    timestamps = [c["timestamp"] for c in commits if c["timestamp"]]
    all_files = [f for c in commits for f in c["files"]]
    file_counts = Counter(all_files)

    # File change distribution for Gini
    file_change_values = list(file_counts.values()) if file_counts else [0]
    scatter_gini = round(gini_coefficient(file_change_values), 4)

    # Hour/day distributions
    hour_hist = hour_distribution(timestamps)
    day_hist = day_distribution(timestamps)

    # Late-night = 22:00-05:00 UTC
    late_night_commits = sum(hour_hist[h] for h in list(range(22, 24)) + list(range(0, 6)))
    late_night_pct = round(
        late_night_commits * 100 / len(timestamps) if timestamps else 0.0, 1
    )

    # Weekend = Saturday (5) + Sunday (6)
    weekend_commits = day_hist[5] + day_hist[6]
    weekend_pct = round(
        weekend_commits * 100 / len(timestamps) if timestamps else 0.0, 1
    )

    # Burst detection
    bursts = detect_bursts(timestamps)

    # Session patterns
    sessions = detect_sessions(timestamps)

    # Top directories
    top_directories = _top_dirs(all_files)

    # Average files per commit
    files_per_commit = round(
        sum(len(c["files"]) for c in commits) / len(commits), 1
    )

    # Risk score
    rscore = risk_score(
        late_night_pct,
        weekend_pct,
        scatter_gini,
        bursts["burst_score"],
    )

    # Risk indicators
    risk_indicators: list[str] = []
    if late_night_pct > 25:
        risk_indicators.append(f"high late-night commit rate ({late_night_pct}%)")
    if weekend_pct > 30:
        risk_indicators.append(f"high weekend commit rate ({weekend_pct}%)")
    if scatter_gini > 0.7:
        risk_indicators.append(f"high change scatter (Gini={scatter_gini})")
    if bursts["burst_score"] > 3.0:
        risk_indicators.append(f"burst coding detected (score={bursts['burst_score']})")

    return {
        "author": author_email,
        "commit_count": len(commits),
        "files_touched": len(file_counts),
        "avg_files_per_commit": files_per_commit,
        "hour_distribution": hour_hist,
        "day_distribution": day_hist,
        "late_night_pct": late_night_pct,
        "weekend_pct": weekend_pct,
        "scatter_gini": scatter_gini,
        "bursts": bursts,
        "sessions": sessions,
        "top_directories": top_directories,
        "risk_score": rscore,
        "risk_indicators": risk_indicators,
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("dev-profile")
@click.argument("author", required=False)
@click.option("--days", default=90, show_default=True, help="Lookback window in days.")
@click.option("--limit", default=20, show_default=True, help="Max authors to show.")
@click.pass_context
def dev_profile(ctx, author, days, limit):
    """Analyze developer commit patterns and behavioral metrics.

    Produces per-developer behavioral metrics useful for PR risk scoring:
    commit time patterns (late-night %), change scatter (Gini coefficient),
    burst detection, session patterns, and top file directories.

    With no AUTHOR argument, profiles all active developers.
    With an AUTHOR (email or substring), profiles only that developer.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Find project root (best-effort)
    try:
        from roam.db.connection import find_project_root
        root = str(find_project_root())
    except Exception:
        import os
        root = os.getcwd()

    raw = _run_git_log(days, root)

    if raw is None:
        verdict = "no git history found"
        if json_mode:
            click.echo(to_json(json_envelope(
                "dev-profile",
                summary={"verdict": verdict, "author_count": 0, "days": days},
                profiles=[],
            )))
            return
        click.echo(f"VERDICT: {verdict}")
        return

    all_commits = parse_git_log(raw)

    if not all_commits:
        verdict = f"no commits in the last {days} days"
        if json_mode:
            click.echo(to_json(json_envelope(
                "dev-profile",
                summary={"verdict": verdict, "author_count": 0, "days": days},
                profiles=[],
            )))
            return
        click.echo(f"VERDICT: {verdict}")
        return

    # Group commits by author
    by_author: dict[str, list[dict]] = defaultdict(list)
    for commit in all_commits:
        by_author[commit["author_email"]].append(commit)

    # Filter to requested author if specified
    if author:
        author_lower = author.lower()
        matching = {
            email: commits
            for email, commits in by_author.items()
            if author_lower in email
        }
        if not matching:
            verdict = f"no commits found for author matching '{author}'"
            if json_mode:
                click.echo(to_json(json_envelope(
                    "dev-profile",
                    summary={"verdict": verdict, "author_count": 0, "days": days, "filter": author},
                    profiles=[],
                )))
                return
            click.echo(f"VERDICT: {verdict}")
            return
        by_author = matching

    # Build profiles
    profiles = []
    for email, commits in sorted(
        by_author.items(), key=lambda x: len(x[1]), reverse=True
    )[:limit]:
        profile = build_author_profile(email, commits)
        profiles.append(profile)

    # Sort profiles by risk_score descending
    profiles.sort(key=lambda p: p["risk_score"], reverse=True)

    # Summary statistics
    total_commits = sum(p["commit_count"] for p in profiles)
    highest_risk = profiles[0] if profiles else None
    authors_with_risk = sum(1 for p in profiles if p["risk_score"] > 50)

    if highest_risk and highest_risk["risk_score"] >= 70:
        verdict = (
            f"{len(profiles)} developer(s) profiled — "
            f"{highest_risk['author']} is highest risk (score={highest_risk['risk_score']})"
        )
    elif authors_with_risk:
        verdict = (
            f"{len(profiles)} developer(s) profiled — "
            f"{authors_with_risk} with elevated behavioral risk score"
        )
    else:
        verdict = (
            f"{len(profiles)} developer(s) profiled over {days} days — "
            f"no elevated behavioral risk detected"
        )

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "dev-profile",
            summary={
                "verdict": verdict,
                "author_count": len(profiles),
                "total_commits": total_commits,
                "days": days,
                "highest_risk_author": highest_risk["author"] if highest_risk else None,
                "highest_risk_score": highest_risk["risk_score"] if highest_risk else 0,
                "authors_with_elevated_risk": authors_with_risk,
            },
            profiles=profiles,
        )))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"  Lookback: {days} days  |  Authors: {len(profiles)}  |  Total commits: {total_commits}")
    click.echo()

    # Table of profiles
    if profiles:
        tbl_rows = []
        for p in profiles:
            indicators = "; ".join(p["risk_indicators"][:2]) if p["risk_indicators"] else "none"
            tbl_rows.append([
                p["author"],
                str(p["commit_count"]),
                f"{p['late_night_pct']}%",
                f"{p['weekend_pct']}%",
                f"{p['scatter_gini']:.3f}",
                f"{p['bursts']['burst_score']:.1f}x",
                str(p["risk_score"]),
                indicators,
            ])
        click.echo(format_table(
            ["Author", "Commits", "LateNight%", "Weekend%", "Scatter(Gini)", "BurstScore", "RiskScore", "Risk Indicators"],
            tbl_rows,
        ))
        click.echo()

    # Detailed section for single-author or top risk profile
    focus = profiles[0] if profiles else None
    if focus and (author or focus["risk_score"] > 0):
        click.echo(f"PROFILE: {focus['author']}")
        click.echo()

        # Hour distribution bar
        click.echo("  Hour-of-day commit distribution (UTC):")
        hour_hist = focus["hour_distribution"]
        max_h = max(hour_hist) if hour_hist else 1
        for h in range(24):
            bar_len = int(hour_hist[h] * 20 / max_h) if max_h else 0
            bar = "#" * bar_len
            label = "**" if h in (0, 1, 2, 3, 4, 5, 22, 23) else "  "
            click.echo(f"    {h:02d}:00 {label} [{bar:<20}] {hour_hist[h]}")
        click.echo()

        # Sessions
        s = focus["sessions"]
        click.echo(
            f"  Sessions: {s['session_count']} sessions, "
            f"avg {s['avg_session_length_minutes']}min, "
            f"avg {s['avg_commits_per_session']} commits/session"
        )
        click.echo()

        # Top directories
        if focus["top_directories"]:
            click.echo("  Top directories:")
            for d in focus["top_directories"]:
                click.echo(f"    {d['directory']:30s} {d['file_count']} files")
            click.echo()
