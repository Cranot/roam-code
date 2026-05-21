"""Detect stale docstrings whose code body has drifted since the docs were written.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because doc-staleness outputs are invocation-scoped
docstring-vs-code drift rankings (per-symbol staleness scores derived
from git blame ages) — not per-location code violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    to_json,
)

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
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
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
# Semantic drift detection
# ---------------------------------------------------------------------------

# Common docstring patterns that name parameters explicitly. We treat any
# identifier in these positions as a "documented param", then compare
# against the signature to detect renames or removals.
_DOC_PARAM_PATTERNS = (
    re.compile(r":param\s+(\w+)\s*:"),
    re.compile(r"@param\s+(?:\w+\s+)?(\w+)\b"),
    re.compile(r"^\s{0,8}(\w+)\s*\([^)]*\)\s*:", re.MULTILINE),  # Sphinx-style "name (type): desc"
    re.compile(r"^\s{0,8}(\w+)\s*:\s*[A-Z]", re.MULTILINE),  # Google-style "name: Description starting with capital"
)
_DOC_RETURNS_RE = re.compile(r"\b(?:Returns?|@returns?|:returns?:|:rtype:)\b", re.IGNORECASE)
_DOC_BEHAVIOUR_CITATION_RE = re.compile(r"\b(?:returns?|raises?|throws?)\s+`[^`]+`", re.IGNORECASE)
_SIGNATURE_PARAM_RE = re.compile(r"def\s+\w+\s*\(([^)]*)\)|\(([^)]*)\)\s*=>|\bfunction\s+\w*\s*\(([^)]*)\)")
_SIGNATURE_HAS_RETURN_RE = re.compile(r"->\s*\S|:\s*\S+\s*=>")


def _signature_param_names(signature: str | None) -> set[str]:
    """Extract parameter identifiers from a stored function signature."""
    if not signature:
        return set()
    match = _SIGNATURE_PARAM_RE.search(signature)
    if not match:
        return set()
    raw = next((g for g in match.groups() if g), "")
    names = set()
    for part in raw.split(","):
        part = part.strip().split("=", 1)[0].strip()
        if not part or part in ("self", "cls"):
            continue
        # Strip type annotation: "name: int" → "name"
        ident = part.split(":", 1)[0].strip().lstrip("*")
        if ident.isidentifier():
            names.add(ident)
    return names


def _docstring_facts(docstring: str | None, signature: str | None) -> dict:
    """Extract testable claims from a docstring.

    Returns a dict describing what the docstring documents:
      - ``params``: identifier names referenced as parameters
      - ``has_returns_clause``: True when the docstring discusses a
        return value (Returns:, @return, :returns:, :rtype:)
      - ``has_specific_facts``: True if any of the above is non-empty —
        used to distinguish "documents implementation details" from
        "pure prose summary"
    """
    if not docstring:
        return {"params": set(), "has_returns_clause": False, "has_specific_facts": False, "behaviour_citations": []}

    sig_params = _signature_param_names(signature)
    documented_params: set[str] = set()
    for pat in _DOC_PARAM_PATTERNS:
        for match in pat.finditer(docstring):
            ident = match.group(1)
            # Google-style "name: Description" must look like a real param
            # to qualify — restrict to identifiers that look param-shaped
            # OR that already appear in the signature so we don't pick up
            # field names from the prose ("Note: ...").
            if ident.isidentifier() and (ident in sig_params or pat.pattern.startswith((":param", "@param"))):
                documented_params.add(ident)

    has_returns_clause = bool(_DOC_RETURNS_RE.search(docstring))
    behaviour_citations = [m.group(0) for m in _DOC_BEHAVIOUR_CITATION_RE.finditer(docstring)]
    has_facts = bool(documented_params or has_returns_clause or behaviour_citations)
    return {
        "params": documented_params,
        "has_returns_clause": has_returns_clause,
        "has_specific_facts": has_facts,
        "behaviour_citations": behaviour_citations,
    }


def _semantic_drift(facts: dict, signature: str | None) -> dict:
    """Return per-symbol semantic drift: doc claims that the signature contradicts.

    - ``phantom_params``: docstring names a param that no longer exists
    - ``return_signature_mismatch``: docstring promises a return value
      while the signature has no return annotation (only flagged for
      function/method symbols where a return arrow is structurally
      possible — silent for non-callable kinds)
    """
    sig_params = _signature_param_names(signature)
    phantom_params = sorted(facts["params"] - sig_params)
    sig_has_return = bool(_SIGNATURE_HAS_RETURN_RE.search(signature or ""))
    return_mismatch = facts["has_returns_clause"] and signature is not None and not sig_has_return
    has_drift = bool(phantom_params) or return_mismatch
    return {
        "phantom_params": phantom_params,
        "return_signature_mismatch": return_mismatch,
        "has_drift": has_drift,
    }


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


def _analyze_staleness(symbols_by_file, project_root, threshold_days, *, include_prose_drift=False):
    """Analyze docstring staleness for all symbols grouped by file.

    Two paths into the stale list:

    * **semantic_mismatch** — docstring claims contradict the signature
      (phantom params, return clause without return annotation). Always
      flagged.
    * **commit_drift** — body changed long after the docstring last
      changed AND the docstring contains specific testable claims
      (param names, return clause, behaviour citations). Pure-prose
      summaries on commit drift are skipped by default; pass
      ``include_prose_drift=True`` to keep the historic behaviour.
    """
    threshold_seconds = threshold_days * 86400
    stale = []

    # git blame is subprocess-bound (one `git blame` per documented file) and
    # was the dominant cost (~88s serial on roam-code). Pre-fetch all blames
    # across a bounded thread pool: blame is a read-only git operation (no
    # index lock, no shared mutable state), and ThreadPoolExecutor.map yields
    # results in input order, so the result-building loop below stays
    # byte-identical to the old serial version — only wall-clock changes.
    file_paths = list(symbols_by_file.keys())
    max_workers = min(8, (os.cpu_count() or 1))
    blame_by_file: dict[str, str | None] = {}
    if file_paths:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fp, out in zip(
                file_paths,
                pool.map(lambda p: _run_git_blame(p, project_root), file_paths),
            ):
                blame_by_file[fp] = out

    for file_path, symbols in symbols_by_file.items():
        blame_output = blame_by_file.get(file_path)
        if not blame_output:
            continue

        blame_map = _parse_blame(blame_output)
        if not blame_map:
            continue

        for sym in symbols:
            ranges = _estimate_docstring_lines(
                sym["line_start"],
                sym["line_end"],
                sym["docstring"],
            )
            if ranges is None:
                continue

            facts = _docstring_facts(sym["docstring"], sym.get("signature"))
            drift_info = _semantic_drift(facts, sym.get("signature"))

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
            commit_drift = drift_seconds >= threshold_seconds

            reasons = []
            if drift_info["has_drift"]:
                reasons.append("semantic_mismatch")
            if commit_drift:
                if facts["has_specific_facts"]:
                    reasons.append("commit_drift")
                elif include_prose_drift:
                    reasons.append("commit_drift_prose")

            if not reasons:
                # Pure prose docstring with commit drift only is no longer
                # treated as stale. This was the dominant false positive
                # ("Start coordinated polling" flagged because the body
                # had a small refactor 100 days later — docstring still
                # accurate as a high-level summary).
                continue

            drift_days = drift_seconds // 86400
            doc_date = datetime.fromtimestamp(doc_latest, tz=timezone.utc)
            body_date = datetime.fromtimestamp(body_latest, tz=timezone.utc)
            stale.append(
                {
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "doc_date": doc_date.strftime("%Y-%m-%d"),
                    "doc_author": doc_authors.get(doc_latest, "?"),
                    "body_date": body_date.strftime("%Y-%m-%d"),
                    "body_author": body_authors.get(body_latest, "?"),
                    "drift_days": drift_days,
                    "reasons": reasons,
                    "phantom_params": drift_info["phantom_params"],
                    "return_mismatch": drift_info["return_signature_mismatch"],
                    "has_specific_facts": facts["has_specific_facts"],
                }
            )

    # Sort by semantic drift first (most actionable), then commit drift days.
    def _key(item):
        sem_priority = 0 if "semantic_mismatch" in item["reasons"] else 1
        return (sem_priority, -item["drift_days"])

    stale.sort(key=_key)
    return stale


# ---------------------------------------------------------------------------
# SQL query
# ---------------------------------------------------------------------------

_DOCUMENTED_SYMBOLS_SQL = """
SELECT s.name, s.kind, f.path AS file_path,
       s.line_start, s.line_end, s.docstring, s.signature
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


@roam_capability(
    name="doc-staleness",
    category="refactoring",
    summary="Detect stale docstrings where the code body changed long after the docs",
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
@click.command("doc-staleness")
@click.option("--limit", default=20, show_default=True, help="Maximum number of stale symbols to display.")
@click.option(
    "--days",
    default=90,
    show_default=True,
    help="Staleness threshold in days (body changed N+ days after docstring).",
)
@click.option(
    "--include-prose-drift",
    is_flag=True,
    default=False,
    help=(
        "Also flag pure-prose docstrings on commit-drift alone. Off by "
        "default — high-level summary docstrings stay accurate even when "
        "the body is refactored, and the resulting noise drowned the "
        "actionable semantic-mismatch findings."
    ),
)
@click.pass_context
def doc_staleness(ctx, limit, days, include_prose_drift):
    """Detect stale docstrings where the code body changed long after the docs.

    Scans ALL symbols with docstrings (including private/internal) and uses
    ``git blame`` to compare docstring timestamps against code body timestamps.

    Unlike ``docs-coverage`` (which reports missing docs for exported public
    symbols, ranked by PageRank), this command focuses on existing docs that
    have gone stale. Audits *what* the docs say. For *where* the docs point —
    dangling links, missing files referenced by README — see ``stale-refs``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        rows = conn.execute(_DOCUMENTED_SYMBOLS_SQL).fetchall()

    if not rows:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "doc-staleness",
                        summary={
                            "verdict": "no documented symbols found in index",
                            "stale_count": 0,
                            "threshold_days": days,
                        },
                        stale=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: no documented symbols found in index\n")
            click.echo("No documented symbols found in index.")
        return

    # Group by file path for efficient blame (one git blame per file)
    symbols_by_file = defaultdict(list)
    for r in rows:
        symbols_by_file[r["file_path"]].append(
            {
                "name": r["name"],
                "kind": r["kind"],
                "file_path": r["file_path"],
                "line_start": r["line_start"],
                "line_end": r["line_end"],
                "docstring": r["docstring"],
                "signature": r["signature"],
            }
        )

    stale = _analyze_staleness(symbols_by_file, project_root, days, include_prose_drift=include_prose_drift)

    # Apply limit
    displayed = stale[:limit]

    if stale:
        _verdict = f"{len(stale)} stale docs (>{days} days since code change)"
    else:
        _verdict = "all docs up to date"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "doc-staleness",
                    summary={
                        "verdict": _verdict,
                        "stale_count": len(stale),
                        "displayed": len(displayed),
                        "threshold_days": days,
                        "files_scanned": len(symbols_by_file),
                        "symbols_scanned": len(rows),
                    },
                    stale=displayed,
                )
            )
        )
        return

    # --- Text output ---
    if not stale:
        click.echo(f"VERDICT: {_verdict}\n")
        click.echo(f"No stale docstrings found (threshold: {days} days).")
        click.echo(f"  Scanned {len(rows)} documented symbols across {len(symbols_by_file)} files.")
        return

    click.echo(f"VERDICT: {_verdict}\n")
    click.echo(f"Stale documentation (body changed >{days} days after docstring):\n")

    for item in displayed:
        click.echo(f"  {item['name']:<25s} {abbrev_kind(item['kind']):<5s} {loc(item['file'], item['line'])}")
        click.echo(f"    Reason: {', '.join(item['reasons'])}")
        if item.get("phantom_params"):
            click.echo(f"    Phantom params: {', '.join(item['phantom_params'])}")
        if item.get("return_mismatch"):
            click.echo("    Documents return value but signature has no return annotation")
        click.echo(f"    Docstring: last updated {item['doc_date']} (by {item['doc_author']})")
        click.echo(f"    Body:      last updated {item['body_date']} (by {item['body_author']})")
        click.echo(f"    Drift: {item['drift_days']} days")
        click.echo()

    if len(stale) > limit:
        click.echo(f"  (+{len(stale) - limit} more stale docstrings, use --limit to see all)")
    click.echo(
        f"  Total: {len(stale)} stale docstring(s) across {len(symbols_by_file)} files ({len(rows)} symbols scanned)"
    )
