"""Detect stale docstrings whose code body has drifted since the docs were written."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import (
    abbrev_kind, loc, format_table, to_json, json_envelope,
)
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Git blame parsing
# ---------------------------------------------------------------------------

def _run_git_blame(file_path, project_root):
    """Run ``git blame -t <file>`` and return raw stdout, or None on error.

    The ``-t`` flag gives us raw Unix timestamps instead of human dates,
    which makes downstream comparison straightforward.
    """
    try:
        result = subprocess.run(
            ["git", "blame", "-t", "--", file_path],
            capture_output=True, text=True, timeout=30,
            cwd=str(project_root),
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


_BLAME_LINE_RE = re.compile(
    r"""
    ^[0-9a-f^]+     # commit hash (possibly prefixed with ^)
    \s+
    \(
    (.+?)            # 1: author (may contain spaces)
    \s+
    (\d+)            # 2: unix timestamp
    \s+
    [+-]\d{4}        # timezone offset
    \s+
    (\d+)            # 3: line number
    \)
    """,
    re.VERBOSE,
)


def _parse_blame(blame_output):
    """Parse ``git blame -t`` output into a dict mapping line_number -> (timestamp, author).

    Returns {int: (int, str)} — line numbers are 1-based.
    """
    result = {}
    for raw_line in blame_output.splitlines():
        m = _BLAME_LINE_RE.match(raw_line)
        if m:
            author = m.group(1).strip()
            timestamp = int(m.group(2))
            lineno = int(m.group(3))
            result[lineno] = (timestamp, author)
    return result


# ---------------------------------------------------------------------------
# Docstring line-range heuristic
# ---------------------------------------------------------------------------

def _estimate_docstring_lines(line_start, line_end, docstring_text):
    """Estimate the line range of the docstring within a symbol.

    Heuristic:
    - The docstring typically starts at ``line_start + 1`` (the line right
      after the ``def`` / ``class`` statement).
    - Its length in lines is derived from the stored docstring text.
    - We clamp to ``line_end`` so we never exceed the symbol body.

    Returns (doc_start, doc_end, body_start, body_end) — all 1-based inclusive.
    """
    if not docstring_text:
        return None

    doc_line_count = max(1, docstring_text.count("\n") + 1)
    # +1 for the opening/closing triple-quotes if single-line, +2 for multi-line
    # A rough estimate: count lines in the docstring text and add 2 for delimiters
    # when the docstring is multi-line, or keep the line count for single-line ones.
    if doc_line_count == 1:
        # Single-line docstring:  """text"""  — occupies 1 line
        overhead = 1
    else:
        # Multi-line docstring: opening """ on its own line + content + closing """
        overhead = doc_line_count + 2

    doc_start = line_start + 1  # line after def/class
    doc_end = min(doc_start + overhead - 1, line_end)

    body_start = doc_end + 1
    body_end = line_end

    if body_start > body_end:
        # Symbol is too short (entire body is the docstring) — nothing to compare
        return None

    return doc_start, doc_end, body_start, body_end


# ---------------------------------------------------------------------------
# Core staleness analysis
# ---------------------------------------------------------------------------

def _analyze_staleness(symbols_by_file, project_root, threshold_days):
    """Analyze docstring staleness for all symbols grouped by file.

    Parameters
    ----------
    symbols_by_file : dict[str, list[dict]]
        Mapping of file_path -> list of symbol dicts, each with keys:
        name, kind, file_path, line_start, line_end, docstring.
    project_root : Path
        Absolute path to the project root (for running git commands).
    threshold_days : int
        Minimum drift in days to consider a docstring stale.

    Returns
    -------
    list[dict]
        Stale symbol records sorted by drift descending.
    """
    threshold_seconds = threshold_days * 86400
    stale = []

    for file_path, symbols in symbols_by_file.items():
        blame_output = _run_git_blame(file_path, project_root)
        if not blame_output:
            continue

        blame_map = _parse_blame(blame_output)
        if not blame_map:
            continue

        for sym in symbols:
            ranges = _estimate_docstring_lines(
                sym["line_start"], sym["line_end"], sym["docstring"],
            )
            if ranges is None:
                continue

            doc_start, doc_end, body_start, body_end = ranges

            # Gather timestamps for docstring lines and body lines
            doc_timestamps = []
            doc_authors = {}
            for ln in range(doc_start, doc_end + 1):
                entry = blame_map.get(ln)
                if entry:
                    ts, author = entry
                    doc_timestamps.append(ts)
                    doc_authors[ts] = author

            body_timestamps = []
            body_authors = {}
            for ln in range(body_start, body_end + 1):
                entry = blame_map.get(ln)
                if entry:
                    ts, author = entry
                    body_timestamps.append(ts)
                    body_authors[ts] = author

            if not doc_timestamps or not body_timestamps:
                continue

            doc_latest = max(doc_timestamps)
            body_latest = max(body_timestamps)

            drift_seconds = body_latest - doc_latest
            if drift_seconds >= threshold_seconds:
                drift_days = drift_seconds // 86400

                doc_date = datetime.fromtimestamp(doc_latest, tz=timezone.utc)
                body_date = datetime.fromtimestamp(body_latest, tz=timezone.utc)

                stale.append({
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "doc_date": doc_date.strftime("%Y-%m-%d"),
                    "doc_author": doc_authors.get(doc_latest, "?"),
                    "body_date": body_date.strftime("%Y-%m-%d"),
                    "body_author": body_authors.get(body_latest, "?"),
                    "drift_days": drift_days,
                })

    # Sort by drift descending (most stale first)
    stale.sort(key=lambda s: -s["drift_days"])
    return stale


# ---------------------------------------------------------------------------
# SQL query
# ---------------------------------------------------------------------------

_DOCUMENTED_SYMBOLS_SQL = """
SELECT s.name, s.kind, f.path AS file_path,
       s.line_start, s.line_end, s.docstring
FROM symbols s
JOIN files f ON s.file_id = f.id
WHERE s.docstring IS NOT NULL
  AND s.docstring != ''
  AND s.line_start IS NOT NULL
  AND s.line_end IS NOT NULL
  AND s.line_end > s.line_start
ORDER BY f.path, s.line_start
"""


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

@click.command("doc-staleness")
@click.option("--limit", default=20, show_default=True,
              help="Maximum number of stale symbols to display.")
@click.option("--days", default=90, show_default=True,
              help="Staleness threshold in days (body changed N+ days after docstring).")
@click.pass_context
def doc_staleness(ctx, limit, days):
    """Detect stale docstrings where the code body changed long after the docs."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        rows = conn.execute(_DOCUMENTED_SYMBOLS_SQL).fetchall()

    if not rows:
        if json_mode:
            click.echo(to_json(json_envelope("doc-staleness",
                summary={"stale_count": 0, "threshold_days": days},
                stale=[],
            )))
        else:
            click.echo("No documented symbols found in index.")
        return

    # Group by file path for efficient blame (one git blame per file)
    symbols_by_file = defaultdict(list)
    for r in rows:
        symbols_by_file[r["file_path"]].append({
            "name": r["name"],
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line_start": r["line_start"],
            "line_end": r["line_end"],
            "docstring": r["docstring"],
        })

    stale = _analyze_staleness(symbols_by_file, project_root, days)

    # Apply limit
    displayed = stale[:limit]

    if json_mode:
        click.echo(to_json(json_envelope("doc-staleness",
            summary={
                "stale_count": len(stale),
                "displayed": len(displayed),
                "threshold_days": days,
                "files_scanned": len(symbols_by_file),
                "symbols_scanned": len(rows),
            },
            stale=displayed,
        )))
        return

    # --- Text output ---
    if not stale:
        click.echo(f"No stale docstrings found (threshold: {days} days).")
        click.echo(f"  Scanned {len(rows)} documented symbols across "
                    f"{len(symbols_by_file)} files.")
        return

    click.echo(f"Stale documentation (body changed >{days} days after docstring):\n")

    for item in displayed:
        click.echo(f"  {item['name']:<25s} {abbrev_kind(item['kind']):<5s} "
                    f"{loc(item['file'], item['line'])}")
        click.echo(f"    Docstring: last updated {item['doc_date']} "
                    f"(by {item['doc_author']})")
        click.echo(f"    Body:      last updated {item['body_date']} "
                    f"(by {item['body_author']})")
        click.echo(f"    Drift: {item['drift_days']} days")
        click.echo()

    if len(stale) > limit:
        click.echo(f"  (+{len(stale) - limit} more stale docstrings, "
                    f"use --limit to see all)")
    click.echo(f"  Total: {len(stale)} stale docstring(s) across "
                f"{len(symbols_by_file)} files ({len(rows)} symbols scanned)")
