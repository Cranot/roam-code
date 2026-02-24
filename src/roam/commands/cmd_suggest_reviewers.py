"""Suggest optimal code reviewers for changed files using multi-signal scoring."""

from __future__ import annotations

import fnmatch
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db


# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------

_W_OWNERSHIP = 0.40
_W_CODEOWNERS = 0.25
_W_RECENCY = 0.20
_W_BREADTH = 0.15


# ---------------------------------------------------------------------------
# CODEOWNERS parsing
# ---------------------------------------------------------------------------

def _find_codeowners(project_root: Path) -> Path | None:
    """Locate the CODEOWNERS file (GitHub/GitLab conventions)."""
    candidates = [
        project_root / "CODEOWNERS",
        project_root / ".github" / "CODEOWNERS",
        project_root / ".gitlab" / "CODEOWNERS",
        project_root / "docs" / "CODEOWNERS",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _parse_codeowners(codeowners_path: Path) -> list[tuple[str, list[str]]]:
    """Parse a CODEOWNERS file into a list of (pattern, [owners]).

    Returns entries in file order (last match wins in GitHub semantics).
    """
    entries: list[tuple[str, list[str]]] = []
    try:
        text = codeowners_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        owners = [o.lstrip("@") for o in parts[1:] if not o.startswith("#")]
        if owners:
            entries.append((pattern, owners))
    return entries


def _resolve_codeowners(file_path: str, entries: list[tuple[str, list[str]]]) -> list[str]:
    """Resolve which CODEOWNERS entries match a file path.

    GitHub uses last-match-wins semantics: only the last matching pattern
    applies.  Returns the owner list for that pattern, or [] if no match.
    """
    matched_owners: list[str] = []
    norm = file_path.replace("\\", "/")
    for pattern, owners in entries:
        pat = pattern.replace("\\", "/")
        # Directory pattern: *.py matches anywhere; /dir/ matches root dir
        if pat.startswith("/"):
            pat = pat.lstrip("/")
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, f"**/{pat}"):
            matched_owners = owners
        # Also try matching as directory prefix
        if pat.endswith("/") and norm.startswith(pat):
            matched_owners = owners
        # Handle **/pattern style
        if fnmatch.fnmatch(norm, pat):
            matched_owners = owners
    return matched_owners


# ---------------------------------------------------------------------------
# Time-decayed ownership (blame-based)
# ---------------------------------------------------------------------------

_DECAY_RATE = 0.005  # per day; half-life ~139 days (~4.6 months)


def _compute_file_ownership(conn, file_id: int, now: int | None = None) -> dict[str, float]:
    """Compute time-decayed blame ownership for a single file.

    Returns {author: decayed_score} where scores are normalised to [0, 1]
    (relative to the highest-scoring author for this file).
    """
    if now is None:
        now = int(time.time())

    rows = conn.execute(
        "SELECT gc.author, gc.timestamp, gfc.lines_added, gfc.lines_removed "
        "FROM git_file_changes gfc "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE gfc.file_id = ?",
        (file_id,),
    ).fetchall()

    if not rows:
        return {}

    author_score: dict[str, float] = defaultdict(float)
    for r in rows:
        author = r["author"] or ""
        if not author:
            continue
        days_since = max(0, (now - (r["timestamp"] or 0)) / 86400)
        churn = (r["lines_added"] or 0) + (r["lines_removed"] or 0)
        weight = churn * math.exp(-_DECAY_RATE * days_since)
        author_score[author] += weight

    if not author_score:
        return {}

    max_score = max(author_score.values())
    if max_score <= 0:
        return {}

    return {a: s / max_score for a, s in author_score.items()}


# ---------------------------------------------------------------------------
# Recency signal
# ---------------------------------------------------------------------------

_RECENCY_DAYS = 30


def _compute_recency(conn, file_id: int, now: int | None = None) -> dict[str, float]:
    """Compute recency signal: how recently each author committed to a file.

    Authors with commits in the last 30 days get score 1.0, decaying
    linearly to 0.0 at 90 days.  No commits within 90 days => 0.0.
    """
    if now is None:
        now = int(time.time())

    cutoff_90d = now - (90 * 86400)
    rows = conn.execute(
        "SELECT gc.author, MAX(gc.timestamp) as latest "
        "FROM git_file_changes gfc "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE gfc.file_id = ? AND gc.timestamp >= ? "
        "GROUP BY gc.author",
        (file_id, cutoff_90d),
    ).fetchall()

    result: dict[str, float] = {}
    for r in rows:
        author = r["author"] or ""
        if not author:
            continue
        latest = r["latest"] or 0
        days_ago = max(0, (now - latest) / 86400)
        if days_ago <= _RECENCY_DAYS:
            result[author] = 1.0
        elif days_ago <= 90:
            result[author] = max(0.0, 1.0 - (days_ago - _RECENCY_DAYS) / 60.0)
        else:
            result[author] = 0.0
    return result


# ---------------------------------------------------------------------------
# Expertise breadth signal
# ---------------------------------------------------------------------------


def _compute_breadth(conn, file_ids: list[int], changed_dirs: set[str]) -> dict[str, float]:
    """Compute expertise breadth: authors who own files in the same directories.

    For each changed directory, find authors who have committed to *other*
    files in that directory.  More directories covered => higher breadth.
    """
    if not changed_dirs:
        return {}

    # Get all files in the changed directories
    all_files = conn.execute("SELECT id, path FROM files").fetchall()
    sibling_file_ids: set[int] = set()
    for f in all_files:
        fdir = os.path.dirname(f["path"].replace("\\", "/"))
        if fdir in changed_dirs and f["id"] not in file_ids:
            sibling_file_ids.add(f["id"])

    if not sibling_file_ids:
        return {}

    # Find authors who have touched those sibling files
    author_dirs: dict[str, set[str]] = defaultdict(set)
    for sfid in sibling_file_ids:
        rows = conn.execute(
            "SELECT DISTINCT gc.author "
            "FROM git_file_changes gfc "
            "JOIN git_commits gc ON gfc.commit_id = gc.id "
            "WHERE gfc.file_id = ?",
            (sfid,),
        ).fetchall()
        # Get this sibling file's dir
        frow = conn.execute(
            "SELECT path FROM files WHERE id = ?", (sfid,)
        ).fetchone()
        if frow:
            fdir = os.path.dirname(frow["path"].replace("\\", "/"))
            for r in rows:
                author = r["author"] or ""
                if author:
                    author_dirs[author].add(fdir)

    if not author_dirs:
        return {}

    total_dirs = len(changed_dirs)
    return {
        author: len(dirs & changed_dirs) / total_dirs
        for author, dirs in author_dirs.items()
    }


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------


def _aggregate_reviewer_scores(
    changed_files: dict[str, int],
    conn,
    codeowners_entries: list[tuple[str, list[str]]],
    exclude: set[str],
    now: int | None = None,
) -> tuple[list[dict], dict]:
    """Compute multi-signal reviewer scores across all changed files.

    Returns (ranked_reviewers, coverage_info).
    """
    if now is None:
        now = int(time.time())

    file_ids = list(changed_files.values())
    changed_dirs = {
        os.path.dirname(p.replace("\\", "/"))
        for p in changed_files.keys()
    }

    # Per-candidate accumulators: {author: {signal: [per-file scores]}}
    candidates: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"ownership": [], "codeowners": [], "recency": [], "breadth": []}
    )

    # Breadth is computed once across all changed files
    breadth_scores = _compute_breadth(conn, file_ids, changed_dirs)

    # Track coverage: which files have at least one candidate
    covered_files: set[str] = set()
    uncovered_files: list[str] = []

    for path, fid in changed_files.items():
        # Ownership signal
        ownership = _compute_file_ownership(conn, fid, now=now)
        # Recency signal
        recency = _compute_recency(conn, fid, now=now)
        # CODEOWNERS signal
        co_owners = _resolve_codeowners(path, codeowners_entries)
        co_owners_set = set(co_owners)

        has_candidate = False
        all_authors = set(ownership.keys()) | set(recency.keys()) | co_owners_set
        for author in all_authors:
            if author in exclude:
                continue
            has_candidate = True
            candidates[author]["ownership"].append(ownership.get(author, 0.0))
            candidates[author]["codeowners"].append(1.0 if author in co_owners_set else 0.0)
            candidates[author]["recency"].append(recency.get(author, 0.0))

        if has_candidate:
            covered_files.add(path)
        else:
            uncovered_files.append(path)

    # Add breadth scores for each candidate
    for author in list(candidates.keys()):
        candidates[author]["breadth"].append(breadth_scores.get(author, 0.0))

    # Aggregate scores
    n_files = len(changed_files) or 1
    ranked: list[dict] = []
    for author, signals in candidates.items():
        ownership_avg = sum(signals["ownership"]) / n_files
        codeowners_avg = sum(signals["codeowners"]) / n_files
        recency_avg = sum(signals["recency"]) / n_files
        breadth_avg = sum(signals["breadth"]) / max(len(signals["breadth"]), 1)

        total = (
            _W_OWNERSHIP * ownership_avg
            + _W_CODEOWNERS * codeowners_avg
            + _W_RECENCY * recency_avg
            + _W_BREADTH * breadth_avg
        )

        # Count files this reviewer covers (has non-zero ownership or is codeowner)
        files_covered = sum(
            1 for o, c in zip(signals["ownership"], signals["codeowners"])
            if o > 0 or c > 0
        )

        ranked.append({
            "name": author,
            "score": round(total, 2),
            "signals": {
                "ownership": round(ownership_avg, 2),
                "codeowners": round(codeowners_avg, 2),
                "recency": round(recency_avg, 2),
                "breadth": round(breadth_avg, 2),
            },
            "files_covered": files_covered,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    coverage_info = {
        "covered": len(covered_files),
        "total": len(changed_files),
        "uncovered_files": uncovered_files,
    }

    return ranked, coverage_info


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("suggest-reviewers")
@click.argument("files", nargs=-1)
@click.option("--top", "top_n", type=int, default=3,
              help="Number of reviewers to suggest (default: 3)")
@click.option("--exclude", "excludes", multiple=True,
              help="Exclude a developer (repeatable, e.g. --exclude alice)")
@click.option("--changed", "use_changed", is_flag=True,
              help="Use git diff HEAD to detect changed files")
@click.pass_context
def suggest_reviewers(ctx, files, top_n, excludes, use_changed):
    """Suggest optimal code reviewers for changed files.

    Uses multi-signal scoring: git blame ownership, CODEOWNERS
    declarations, recent activity, and expertise breadth.

    Provide file paths as arguments, or use --changed to auto-detect
    from git diff.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # Resolve changed files
    if use_changed:
        changed = get_changed_files(root)
    elif files:
        changed = [f.replace("\\", "/") for f in files]
    else:
        # Default: unstaged changes (same as pr-risk)
        changed = get_changed_files(root)

    if not changed:
        if json_mode:
            click.echo(to_json(json_envelope("suggest-reviewers",
                summary={"verdict": "No changed files found"},
                reviewers=[],
                coverage={"covered": 0, "total": 0, "uncovered_files": []},
                changed_files=[],
            )))
        else:
            click.echo("VERDICT: No changed files found")
        return

    with open_db(readonly=True) as conn:
        file_map = resolve_changed_to_db(conn, changed)

        if not file_map:
            if json_mode:
                click.echo(to_json(json_envelope("suggest-reviewers",
                    summary={"verdict": "Changed files not in index"},
                    reviewers=[],
                    coverage={"covered": 0, "total": 0, "uncovered_files": changed},
                    changed_files=changed,
                )))
            else:
                click.echo("VERDICT: Changed files not in index. Run `roam index` first.")
            return

        # Parse CODEOWNERS
        codeowners_path = _find_codeowners(root)
        codeowners_entries = _parse_codeowners(codeowners_path) if codeowners_path else []

        exclude_set = set(excludes)

        ranked, coverage_info = _aggregate_reviewer_scores(
            file_map, conn, codeowners_entries, exclude_set,
        )

        top = ranked[:top_n]
        n_changed = len(file_map)

        if top:
            verdict = f"{len(top)} reviewer{'s' if len(top) != 1 else ''} suggested for {n_changed} changed file{'s' if n_changed != 1 else ''}"
        else:
            verdict = f"No reviewers found for {n_changed} changed file{'s' if n_changed != 1 else ''}"

        if json_mode:
            click.echo(to_json(json_envelope("suggest-reviewers",
                summary={
                    "verdict": verdict,
                    "reviewers_suggested": len(top),
                    "changed_files": n_changed,
                },
                reviewers=top,
                coverage=coverage_info,
                changed_files=list(file_map.keys()),
            )))
            return

        # Text output
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        if top:
            rows = []
            for i, r in enumerate(top, 1):
                sig = r["signals"]
                signals_str = (
                    f"ownership({sig['ownership']:.2f}) "
                    f"codeowners({sig['codeowners']:.2f}) "
                    f"recency({sig['recency']:.2f}) "
                    f"breadth({sig['breadth']:.2f})"
                )
                rows.append([
                    str(i),
                    r["name"],
                    f"{r['score']:.2f}",
                    signals_str,
                ])
            click.echo(format_table(
                ["RANK", "REVIEWER", "SCORE", "SIGNALS"],
                rows,
            ))
            click.echo()

        cov = coverage_info
        click.echo(f"COVERAGE: {cov['covered']}/{cov['total']} files covered by suggested reviewers")
        if cov["uncovered_files"]:
            for uf in cov["uncovered_files"]:
                click.echo(f"UNCOVERED: {uf} (no blame history)")
