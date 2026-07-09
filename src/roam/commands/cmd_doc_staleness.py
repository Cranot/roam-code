"""Detect docstrings whose concrete claims no longer match the code.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because doc-staleness outputs are invocation-scoped
docstring-vs-code drift rankings (per-symbol staleness scores derived
from git blame ages) — not per-location code violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1224-audit memo.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

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

_DOC_SECTION_RE = re.compile(
    r"^\s*(Args?|Arguments?|Parameters?|Keyword Arguments?|Returns?|Raises?|Yields?)\s*:?\s*$",
    re.IGNORECASE,
)
_NUMPY_SECTION_RE = re.compile(
    r"^\s*(Args?|Arguments?|Parameters?|Returns?|Raises?|Yields?)\s*$",
    re.IGNORECASE,
)
_SECTION_UNDERLINE_RE = re.compile(r"^\s*-{3,}\s*$")
_REST_TAG_RE = re.compile(
    r"^\s*:(param(?:eter)?|return|returns?|rtype|raise|raises?)(?:\s+([^:]+))?\s*:\s*.*$",
    re.IGNORECASE,
)
_AT_TAG_RE = re.compile(r"^\s*@(param|return|returns?|raise|raises?|throws?)\b\s*(.*)$", re.IGNORECASE)


def _identifiers(value: str) -> set[str]:
    """Return identifier-like names from a compact docstring field."""
    return {part.lstrip("*").strip() for part in value.split(",") if part.lstrip("*").strip().isidentifier()}


def _section_entry_names(line: str) -> set[str]:
    """Extract names from a Google- or NumPy-style section entry."""
    if ":" not in line:
        return set()
    head = line.split(":", 1)[0].strip()
    head = re.sub(r"\s*\([^)]*\)\s*$", "", head)
    return _identifiers(head)


def _docstring_facts(docstring: str | None, signature: str | None) -> dict:
    """Extract structured parameter, return, and raises claims.

    Only section/tag syntax is treated as a concrete claim.  Free-form prose
    is deliberately ignored so a summary cannot become stale merely because
    its function body changed later.
    """
    facts = {
        "params": set(),
        "has_returns_clause": False,
        "raises": set(),
        "has_specific_facts": False,
    }
    if not docstring:
        return facts

    section = None
    lines = docstring.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        rest_match = _REST_TAG_RE.match(line)
        if rest_match:
            kind, value = rest_match.groups()
            kind = kind.lower()
            if kind.startswith("param"):
                tokens = (value or "").strip().split()
                if tokens and tokens[-1].isidentifier():
                    facts["params"].add(tokens[-1])
            elif kind.startswith("return") or kind == "rtype":
                facts["has_returns_clause"] = True
            else:
                facts["raises"].update(_identifiers((value or "").split()[0] if value else ""))
            continue

        at_match = _AT_TAG_RE.match(line)
        if at_match:
            kind, value = at_match.groups()
            kind = kind.lower()
            if kind == "param":
                tokens = value.split()
                if tokens and tokens[0].isidentifier():
                    facts["params"].add(tokens[0])
            elif kind.startswith("return"):
                facts["has_returns_clause"] = True
            elif kind.startswith(("raise", "throw")):
                facts["raises"].update(_identifiers(value.split()[0] if value.split() else ""))
            continue

        section_match = _DOC_SECTION_RE.match(line)
        numpy_section = (
            _NUMPY_SECTION_RE.match(line) and index + 1 < len(lines) and _SECTION_UNDERLINE_RE.match(lines[index + 1])
        )
        if section_match or numpy_section:
            section = (section_match or numpy_section).group(1).lower().rstrip(":")
            continue

        if section in {"args", "arg", "arguments", "parameter", "parameters", "keyword argument", "keyword arguments"}:
            facts["params"].update(_section_entry_names(line))
        elif section in {"returns", "return", "yields", "yield"}:
            if stripped:
                facts["has_returns_clause"] = True
        elif section in {"raises", "raise"}:
            facts["raises"].update(_section_entry_names(line))

    facts["has_specific_facts"] = bool(facts["params"] or facts["has_returns_clause"] or facts["raises"])
    return facts


def _ast_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = node.args
    parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        parameters.append(args.vararg)
    if args.kwarg:
        parameters.append(args.kwarg)
    return {arg.arg for arg in parameters if arg.arg not in {"self", "cls"}}


class _BodyFacts(ast.NodeVisitor):
    """Collect only the current function's direct return and raise behavior."""

    def __init__(self):
        self.returns = []
        self.raises = []

    def visit_Return(self, node):  # noqa: N802
        self.returns.append(node.value)

    def visit_Raise(self, node):  # noqa: N802
        self.raises.append(node.exc)

    def visit_FunctionDef(self, node):  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        return

    def visit_ClassDef(self, node):  # noqa: N802
        return

    def visit_Lambda(self, node):  # noqa: N802
        return


def _body_facts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _BodyFacts:
    facts = _BodyFacts()
    for statement in node.body:
        facts.visit(statement)
    return facts


def _returns_value(value: ast.expr | None) -> bool:
    return value is not None and not (isinstance(value, ast.Constant) and value.value is None)


def _semantic_drift(facts: dict, signature: str | None = None, function_node=None) -> dict:
    """Return concrete doc/code mismatches, using Python AST when available."""
    if isinstance(signature, (ast.FunctionDef, ast.AsyncFunctionDef)) and function_node is None:
        function_node = signature
    sig_params = _ast_parameter_names(function_node) if function_node is not None else set()
    phantom_params = sorted(facts["params"] - sig_params)

    return_mismatch = False
    missing_raises = []
    if function_node is not None:
        body = _body_facts(function_node)
        return_mismatch = facts["has_returns_clause"] and not any(_returns_value(value) for value in body.returns)
        if facts["raises"] and not body.raises:
            missing_raises = sorted(facts["raises"])

    has_drift = bool(phantom_params or return_mismatch or missing_raises)
    return {
        "phantom_params": phantom_params,
        "return_signature_mismatch": return_mismatch,
        "missing_raises": missing_raises,
        "has_drift": has_drift,
    }


def _python_functions(project_root: Path, file_path: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Parse Python functions for one file; unsupported files fail closed."""
    path = Path(file_path)
    if path.suffix not in {".py", ".pyi"}:
        return []
    if not path.is_absolute():
        path = project_root / path
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return []
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _find_python_function(functions, symbol: dict):
    """Match an indexed Python symbol, including decorator-covered ranges."""
    candidates = [
        node
        for node in functions
        if node.name == symbol["name"] and node.lineno >= symbol["line_start"] and node.end_lineno <= symbol["line_end"]
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda node: (node.end_lineno - node.lineno, node.lineno))


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


def _semantic_record(symbol, drift_info):
    return {
        "name": symbol["name"],
        "kind": symbol["kind"],
        "file": symbol["file_path"],
        "line": symbol["line_start"],
        "doc_date": "unknown",
        "doc_author": "?",
        "body_date": "unknown",
        "body_author": "?",
        "drift_days": 0,
        "reasons": ["semantic_mismatch"],
        "phantom_params": drift_info["phantom_params"],
        "return_mismatch": drift_info["return_signature_mismatch"],
        "missing_raises": drift_info["missing_raises"],
        "has_specific_facts": True,
    }


# ---------------------------------------------------------------------------
# Core staleness analysis
# ---------------------------------------------------------------------------


def _analyze_staleness(symbols_by_file, project_root, threshold_days, *, include_prose_drift=False):
    """Analyze docstring staleness for all symbols grouped by file.

    Two paths into the stale list:

    * **semantic_mismatch** — docstring claims contradict the signature
      or Python AST body (phantom params, missing returns, or clearly absent
      explicit raises). Always flagged.
    * **commit_drift_prose** — pure-prose body drift, only when
      ``include_prose_drift=True``. Structured claims without a concrete
      mismatch are intentionally not findings.
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

    python_functions_by_file = {file_path: _python_functions(Path(project_root), file_path) for file_path in file_paths}

    for file_path, symbols in symbols_by_file.items():
        blame_output = blame_by_file.get(file_path)
        blame_map = _parse_blame(blame_output) if blame_output else {}

        for sym in symbols:
            function_node = _find_python_function(python_functions_by_file.get(file_path, []), sym)
            facts = _docstring_facts(sym["docstring"], sym.get("signature"))
            drift_info = _semantic_drift(facts, sym.get("signature"), function_node)
            ranges = _estimate_docstring_lines(
                sym["line_start"],
                sym["line_end"],
                sym["docstring"],
            )
            if ranges is None:
                if drift_info["has_drift"]:
                    stale.append(_semantic_record(sym, drift_info))
                continue
            if not blame_map:
                if drift_info["has_drift"]:
                    stale.append(_semantic_record(sym, drift_info))
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
                if drift_info["has_drift"]:
                    stale.append(_semantic_record(sym, drift_info))
                continue

            doc_latest = max(doc_timestamps)
            body_latest = max(body_timestamps)

            drift_seconds = body_latest - doc_latest
            commit_drift = drift_seconds >= threshold_seconds

            reasons = []
            if drift_info["has_drift"]:
                reasons.append("semantic_mismatch")
            if commit_drift and include_prose_drift and not facts["has_specific_facts"]:
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
                    "missing_raises": drift_info["missing_raises"],
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
    summary="Detect docstrings whose concrete claims no longer match the code",
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
    help="Staleness threshold for optional prose drift (body changed N+ days after docstring).",
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
    """Detect concrete docstring claims that no longer match the code.

    Scans ALL symbols with docstrings (including private/internal). Python
    functions use AST-backed parameter, return, and explicit-raise checks.
    Pure-prose blame drift is opt-in via ``--include-prose-drift``.

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
            click.echo("    Documents a return value but the body returns nothing")
        if item.get("missing_raises"):
            click.echo(f"    Documents raises that the body no longer raises: {', '.join(item['missing_raises'])}")
        click.echo(f"    Docstring: last updated {item['doc_date']} (by {item['doc_author']})")
        click.echo(f"    Body:      last updated {item['body_date']} (by {item['body_author']})")
        click.echo(f"    Drift: {item['drift_days']} days")
        click.echo()

    if len(stale) > limit:
        click.echo(f"  (+{len(stale) - limit} more stale docstrings, use --limit to see all)")
    click.echo(
        f"  Total: {len(stale)} stale docstring(s) across {len(symbols_by_file)} files ({len(rows)} symbols scanned)"
    )
