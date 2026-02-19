"""Check database migration files for non-idempotent operations.

Scans migration source files and flags operations that will fail or behave
incorrectly if the migration is run more than once.  This is critical in
projects that maintain dual migration tables (e.g. Laravel apps with
Forge deployments) where a migration can be re-run unexpectedly.

Detection categories:

1. Schema::create without hasTable guard
   - Pattern: Schema::create('x') or Schema::connection(...)->create('x')
     not preceded by an if (!...hasTable) check in the same up() block
   - Confidence: high

2. addColumn / column-type calls without hasColumn guard
   - Pattern: ->addColumn(...) or ->string/integer/uuid/boolean/etc(
     inside a Schema::table() call not guarded by hasColumn
   - Confidence: medium

3. ->index() / ->unique() without pg_indexes (or similar) existence check
   - Pattern: $table->index(...) / $table->unique(...) in a table block
     not surrounded by a pg_indexes lookup
   - Confidence: medium

4. Schema::drop() without dropIfExists
   - Pattern: Schema::drop( (bare drop, NOT dropIfExists)
   - dropColumn without hasColumn guard
   - Confidence: high

5. Missing down() method
   - Migration class with an up() method but no down() method
   - Confidence: low

All detections read the raw PHP source; no PHP parsing is performed —
purely line-based regex analysis.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches Schema::create( or Schema::connection(...)->create(
# The optional group matches either:
#   ::connection(...)->   (connection-chained call)
#   ::                    (direct static call)
_RE_SCHEMA_CREATE = re.compile(
    r"""Schema\s*(?:::\s*connection\s*\([^)]*\)\s*->\s*|::\s*)create\s*\(""",
    re.IGNORECASE,
)

# Matches hasTable( guard in any form
_RE_HAS_TABLE = re.compile(r"""hasTable\s*\(""", re.IGNORECASE)

# Matches Schema::drop( — bare drop (NOT dropIfExists).
# Uses a negative lookahead to exclude dropIfExists, dropColumns, etc.
_RE_SCHEMA_DROP = re.compile(
    r"""Schema\s*(?:::\s*connection\s*\([^)]*\)\s*->\s*|::\s*)drop\s*(?!If)\(""",
    re.IGNORECASE,
)

# Matches Schema::dropIfExists( — this is the safe form
_RE_SCHEMA_DROP_IF_EXISTS = re.compile(
    r"""Schema\s*(?:::\s*connection\s*\([^)]*\)\s*->\s*|::\s*)dropIfExists\s*\(""",
    re.IGNORECASE,
)

# Matches $table->dropColumn(
_RE_DROP_COLUMN = re.compile(r"""\$table\s*->\s*dropColumn\s*\(""", re.IGNORECASE)

# Matches hasColumn( guard
_RE_HAS_COLUMN = re.compile(r"""hasColumn\s*\(""", re.IGNORECASE)

# Matches Schema::table( — alter-table context
_RE_SCHEMA_TABLE = re.compile(
    r"""Schema\s*(?:::\s*connection\s*\([^)]*\)\s*->\s*|::\s*)table\s*\(""",
    re.IGNORECASE,
)

# Blueprint column type methods that add new columns
_RE_COLUMN_DEF = re.compile(
    r"""\$table\s*->\s*(?:"""
    r"""string|integer|bigInteger|unsignedBigInteger|unsignedInteger"""
    r"""|boolean|text|longText|mediumText|json|jsonb|uuid|char"""
    r"""|decimal|float|double|date|dateTime|timestamp|timestamps"""
    r"""|softDeletes|tinyInteger|smallInteger|binary|enum"""
    r"""|addColumn"""
    r""")\s*\(""",
    re.IGNORECASE,
)

# Matches ->index( or ->unique( on Blueprint
_RE_INDEX_DEF = re.compile(r"""\$table\s*->\s*(?:index|unique|primary)\s*\(""", re.IGNORECASE)

# Matches a pg_indexes query (existence check for an index)
_RE_PG_INDEXES = re.compile(r"""pg_indexes""", re.IGNORECASE)

# Also accept indexExists( or hasIndex( as valid guards
_RE_INDEX_EXISTS = re.compile(r"""(?:indexExists|hasIndex|getIndexes)\s*\(""", re.IGNORECASE)

# Matches a down() method definition
_RE_DOWN_METHOD = re.compile(r"""function\s+down\s*\(""", re.IGNORECASE)

# Matches an up() method definition
_RE_UP_METHOD = re.compile(r"""function\s+up\s*\(""", re.IGNORECASE)

# CREATE INDEX (raw SQL) without IF NOT EXISTS
_RE_CREATE_INDEX_RAW = re.compile(
    r"""CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS)""",
    re.IGNORECASE,
)

# CREATE INDEX ... IF NOT EXISTS — safe form
_RE_CREATE_INDEX_IF_NOT_EXISTS = re.compile(
    r"""CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Source reading helpers
# ---------------------------------------------------------------------------

def _read_source(abs_path: Path) -> list[str]:
    """Read file lines (1-indexed by position in list)."""
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _extract_up_block(lines: list[str]) -> tuple[int, int]:
    """Find the line range of the up() method body.

    Returns (start_line, end_line) as 1-indexed line numbers, or (0, 0) if
    up() is not found.  The range covers from the opening brace to the
    matching closing brace of the method body.

    Algorithm:
    - Detect the 'function up()' line.
    - After that, accumulate brace depth.  The method body starts at the
      first '{' after the declaration (which may be on the same line or the
      next).  We record 'entered' once depth goes positive, then we stop
      when depth returns to 0.
    """
    up_start = 0
    brace_depth = 0
    in_up = False
    entered_body = False  # True once we've seen the opening { of the method
    up_end = 0

    for i, line in enumerate(lines, start=1):
        if not in_up:
            if _RE_UP_METHOD.search(line):
                in_up = True
                up_start = i

        if in_up:
            opens = line.count("{")
            closes = line.count("}")
            brace_depth += opens - closes

            if not entered_body and opens > 0:
                entered_body = True  # We've opened the method body

            if entered_body and brace_depth <= 0:
                up_end = i
                break

    return up_start, up_end


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _check_schema_create(lines: list[str], up_start: int, up_end: int) -> list[dict]:
    """Detect Schema::create() without a hasTable guard."""
    findings = []
    if up_start == 0:
        return findings

    up_lines = lines[up_start - 1: up_end]

    for rel_i, line in enumerate(up_lines):
        if not _RE_SCHEMA_CREATE.search(line):
            continue

        abs_line = up_start + rel_i  # 1-indexed

        # Look backwards within the up() block for a hasTable guard.
        # We look up to 30 lines back (covers wrapping if/function calls).
        context_start = max(0, rel_i - 30)
        context_snippet = "".join(up_lines[context_start:rel_i + 1])

        if _RE_HAS_TABLE.search(context_snippet):
            continue  # guarded — OK

        # Extract table name for better messaging
        table_name = _extract_arg(line)

        findings.append({
            "line": abs_line,
            "confidence": "high",
            "issue": f"Schema::create({table_name!r}) without hasTable guard",
            "fix": (
                f"Wrap with: if (!Schema::hasTable({table_name!r})) {{ ... }}"
                if table_name else
                "Wrap Schema::create() with an if (!Schema::hasTable(...)) check"
            ),
            "category": "create_without_check",
        })

    return findings


def _check_schema_drop(lines: list[str], up_start: int, up_end: int) -> list[dict]:
    """Detect bare Schema::drop() (not dropIfExists)."""
    findings = []
    if up_start == 0:
        return findings

    up_lines = lines[up_start - 1: up_end]

    for rel_i, line in enumerate(up_lines):
        # Skip lines that contain dropIfExists — they're safe
        if _RE_SCHEMA_DROP_IF_EXISTS.search(line):
            continue
        if not _RE_SCHEMA_DROP.search(line):
            continue

        abs_line = up_start + rel_i
        table_name = _extract_arg(line)

        findings.append({
            "line": abs_line,
            "confidence": "high",
            "issue": f"Schema::drop({table_name!r}) — unsafe bare drop",
            "fix": f"Use Schema::dropIfExists({table_name!r}) instead",
            "category": "unsafe_drop",
        })

    return findings


def _check_drop_column(lines: list[str], up_start: int, up_end: int) -> list[dict]:
    """Detect dropColumn without hasColumn guard."""
    findings = []
    if up_start == 0:
        return findings

    up_lines = lines[up_start - 1: up_end]

    for rel_i, line in enumerate(up_lines):
        if not _RE_DROP_COLUMN.search(line):
            continue

        abs_line = up_start + rel_i

        # Look backwards up to 30 lines for a hasColumn guard
        context_start = max(0, rel_i - 30)
        context_snippet = "".join(up_lines[context_start:rel_i + 1])

        if _RE_HAS_COLUMN.search(context_snippet):
            continue

        col_name = _extract_arg(line)

        findings.append({
            "line": abs_line,
            "confidence": "medium",
            "issue": f"dropColumn({col_name!r}) without hasColumn guard",
            "fix": (
                f"Wrap with: if (Schema::hasColumn($table, {col_name!r})) {{ ... }}"
                if col_name else
                "Wrap dropColumn() with an if (Schema::hasColumn(...)) check"
            ),
            "category": "drop_column_without_check",
        })

    return findings


def _check_add_column(lines: list[str], up_start: int, up_end: int) -> list[dict]:
    """Detect column definitions inside Schema::table() without hasColumn guard.

    Strategy: find Schema::table() blocks and check whether each column
    definition inside them is preceded by a hasColumn guard in the same
    containing block or outer if-statement.
    """
    findings = []
    if up_start == 0:
        return findings

    up_lines = lines[up_start - 1: up_end]

    # Find Schema::table() calls; each opens a closure for altering a table.
    # We detect the surrounding if-block or direct call and see if hasColumn
    # exists in the surrounding context.
    for rel_i, line in enumerate(up_lines):
        if not _RE_COLUMN_DEF.search(line):
            continue

        abs_line = up_start + rel_i

        # We only flag column additions inside a Schema::table() context
        # (altering an existing table).  Skip if this looks like it's inside
        # a Schema::create() block (adding columns to a brand-new table is
        # always safe — the table didn't exist before).
        context_start = max(0, rel_i - 60)
        pre_context = "".join(up_lines[context_start:rel_i + 1])

        if _RE_SCHEMA_CREATE.search(pre_context) and not _RE_SCHEMA_TABLE.search(pre_context):
            continue  # Inside Schema::create() — new table, no guard needed

        if not _RE_SCHEMA_TABLE.search(pre_context):
            continue  # Not inside an alter-table block — skip

        # Check if hasColumn guard appears near this column definition
        if _RE_HAS_COLUMN.search(pre_context):
            continue  # Guarded — OK

        col_name = _extract_arg(line)

        findings.append({
            "line": abs_line,
            "confidence": "medium",
            "issue": f"->column({col_name!r}) without hasColumn guard in Schema::table() block",
            "fix": (
                f"Wrap with: if (!Schema::hasColumn($table, {col_name!r})) {{ ... }}"
                if col_name else
                "Wrap column addition with an if (!Schema::hasColumn(...)) check"
            ),
            "category": "add_column_without_check",
        })

    return findings


def _check_index_creation(lines: list[str], up_start: int, up_end: int) -> list[dict]:
    """Detect ->index() / ->unique() and raw CREATE INDEX without existence checks."""
    findings = []
    if up_start == 0:
        return findings

    up_lines = lines[up_start - 1: up_end]

    for rel_i, line in enumerate(up_lines):
        abs_line = up_start + rel_i

        # --- Raw SQL CREATE INDEX without IF NOT EXISTS ---
        if _RE_CREATE_INDEX_RAW.search(line) and not _RE_CREATE_INDEX_IF_NOT_EXISTS.search(line):
            # Check if the surrounding context has a pg_indexes existence check
            context_start = max(0, rel_i - 40)
            context_snippet = "".join(up_lines[context_start:rel_i + 1])
            if not _RE_PG_INDEXES.search(context_snippet) and not _RE_INDEX_EXISTS.search(context_snippet):
                findings.append({
                    "line": abs_line,
                    "confidence": "high",
                    "issue": "CREATE INDEX without IF NOT EXISTS",
                    "fix": (
                        "Use 'CREATE INDEX IF NOT EXISTS ...' or check pg_indexes "
                        "before creating the index"
                    ),
                    "category": "index_without_check",
                })
            continue

        # --- Blueprint $table->index() / ->unique() ---
        if not _RE_INDEX_DEF.search(line):
            continue

        # These are inside Schema::create() — safe, table is brand new
        context_start = max(0, rel_i - 60)
        pre_context = "".join(up_lines[context_start:rel_i + 1])

        if _RE_SCHEMA_CREATE.search(pre_context) and not _RE_SCHEMA_TABLE.search(pre_context):
            continue  # Brand-new table — no guard needed

        # Inside Schema::table() — needs a pg_indexes or hasIndex check
        if not _RE_SCHEMA_TABLE.search(pre_context):
            continue  # Not an alter-table context — skip

        if (_RE_PG_INDEXES.search(pre_context) or _RE_INDEX_EXISTS.search(pre_context)):
            continue  # Has an existence check — OK

        findings.append({
            "line": abs_line,
            "confidence": "medium",
            "issue": "->index() / ->unique() inside Schema::table() without index existence check",
            "fix": (
                "Check pg_indexes before adding: "
                "SELECT indexname FROM pg_indexes WHERE indexname = '...' "
                "and only create if result is empty"
            ),
            "category": "index_without_check",
        })

    return findings


def _check_missing_down(lines: list[str]) -> list[dict]:
    """Detect migration class that has up() but no down() method."""
    full_text = "".join(lines)
    has_up = bool(_RE_UP_METHOD.search(full_text))
    has_down = bool(_RE_DOWN_METHOD.search(full_text))

    if has_up and not has_down:
        # Find the line of the up() definition for reporting
        up_line = 1
        for i, line in enumerate(lines, start=1):
            if _RE_UP_METHOD.search(line):
                up_line = i
                break
        return [{
            "line": up_line,
            "confidence": "low",
            "issue": "Migration has up() but no down() method — cannot rollback",
            "fix": "Add a down() method that reverses the up() operations",
            "category": "missing_down",
        }]
    return []


# ---------------------------------------------------------------------------
# Argument extractor (best-effort — not a PHP parser)
# ---------------------------------------------------------------------------

def _extract_arg(line: str) -> str:
    """Extract the first string argument from a PHP call on this line.

    Handles single and double quotes.  Returns empty string if not found.
    """
    m = re.search(r"""['"]([\w.${}/_-]+)['"]""", line)
    if m:
        return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def _analyze_file(abs_path: Path, rel_path: str) -> list[dict]:
    """Run all detectors on a single migration file.

    Returns a list of finding dicts, each with:
      file, line, confidence, issue, fix, category
    """
    lines = _read_source(abs_path)
    if not lines:
        return []

    up_start, up_end = _extract_up_block(lines)

    all_findings: list[dict] = []

    all_findings.extend(_check_schema_create(lines, up_start, up_end))
    all_findings.extend(_check_schema_drop(lines, up_start, up_end))
    all_findings.extend(_check_drop_column(lines, up_start, up_end))
    all_findings.extend(_check_add_column(lines, up_start, up_end))
    all_findings.extend(_check_index_creation(lines, up_start, up_end))
    all_findings.extend(_check_missing_down(lines))

    # Attach file path to every finding
    for f in all_findings:
        f["file"] = rel_path

    return all_findings


def analyze_migration_safety(conn, limit: int = 50, include_archive: bool = False) -> list[dict]:
    """Query the index for migration files and analyze each one.

    Returns a flat list of finding dicts sorted by confidence (high first)
    then by file path.
    """
    root = find_project_root()

    # Migration files: path contains 'migration' (case-insensitive), PHP only
    rows = conn.execute(
        "SELECT f.path FROM files f "
        "WHERE LOWER(f.path) LIKE '%migration%' "
        "  AND LOWER(f.path) LIKE '%.php' "
        "ORDER BY f.path"
    ).fetchall()

    findings: list[dict] = []

    for row in rows:
        rel_path = row["path"]
        rel_lower = rel_path.replace("\\", "/").lower()

        # Skip archive migrations — these are historical and never re-run
        if not include_archive:
            if "/archive/" in rel_lower or "/archived/" in rel_lower:
                continue

        # Skip vendor directory — framework-provided migrations aren't user-authored
        if "/vendor/" in rel_lower:
            continue

        abs_path = root / rel_path if root else Path(rel_path)

        if not abs_path.is_file():
            # Try the path as-is (already absolute on some OS configurations)
            abs_path = Path(rel_path)
            if not abs_path.is_file():
                continue

        file_findings = _analyze_file(abs_path, rel_path)
        findings.extend(file_findings)

    # Sort: high → medium → low, then by file path, then by line number
    _conf_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (
        _conf_order.get(f["confidence"], 9),
        f["file"],
        f["line"],
    ))

    return findings[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("migration-safety")
@click.option("--limit", "-n", default=50, help="Max findings to show")
@click.option(
    "--confidence",
    "confidence_filter",
    default=None,
    type=click.Choice(["high", "medium", "low"], case_sensitive=False),
    help="Filter by confidence level",
)
@click.option("--include-archive", "include_archive", is_flag=True, default=False,
              help="Include archive/ migrations (skipped by default)")
@click.pass_context
def migration_safety_cmd(ctx, limit, confidence_filter, include_archive):
    """Check migration files for non-idempotent (unsafe if run twice) operations.

    Scans all PHP migration files in the indexed project and reports
    operations that will fail or corrupt data if executed more than once.
    This matters for projects with dual migration tables (e.g. Laravel +
    Forge) where migrations can be unexpectedly re-run.

    \b
    Detects:
      [high]   Schema::create() without hasTable guard
      [high]   Schema::drop() instead of dropIfExists()
      [high]   CREATE INDEX without IF NOT EXISTS
      [medium] Column additions in Schema::table() without hasColumn check
      [medium] dropColumn() without hasColumn check
      [medium] ->index() / ->unique() in alter-table without existence check
      [low]    Missing down() method (cannot rollback)

    \b
    Examples:
        roam migration-safety                    # Full scan
        roam migration-safety --confidence high  # Only high-severity issues
        roam migration-safety -n 20              # Limit to 20 findings
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        findings = analyze_migration_safety(conn, limit=limit * 3, include_archive=include_archive)

    # Apply confidence filter
    if confidence_filter:
        findings = [f for f in findings if f["confidence"] == confidence_filter]

    # Re-apply limit after filter
    truncated = len(findings) > limit
    findings = findings[:limit]

    # Confidence counts
    by_confidence: dict[str, int] = defaultdict(int)
    for f in findings:
        by_confidence[f["confidence"]] += 1

    total = len(findings)
    n_high = by_confidence.get("high", 0)
    n_medium = by_confidence.get("medium", 0)
    n_low = by_confidence.get("low", 0)

    verdict = (
        f"{total} migration safety issue{'s' if total != 1 else ''} found "
        f"({n_high} high, {n_medium} medium, {n_low} low)"
        if total else
        "No migration safety issues found — all migrations appear idempotent"
    )

    # --- JSON output ---
    if json_mode:
        click.echo(to_json(json_envelope(
            "migration-safety",
            summary={
                "verdict": verdict,
                "total": total,
                "by_confidence": {"high": n_high, "medium": n_medium, "low": n_low},
                "truncated": truncated,
            },
            findings=[
                {
                    "file": f["file"],
                    "line": f["line"],
                    "confidence": f["confidence"],
                    "issue": f["issue"],
                    "fix": f["fix"],
                    "category": f["category"],
                }
                for f in findings
            ],
        )))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    if not findings:
        return

    click.echo()

    # Group findings by file for readable output
    by_file: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_file[f["file"]].append(f)

    for file_path, file_findings in by_file.items():
        # Normalize path separators for display
        display_path = file_path.replace("\\", "/")
        click.echo(display_path)

        for f in file_findings:
            conf = f["confidence"]
            issue = f["issue"]
            fix = f["fix"]
            line = f["line"]

            click.echo(f"  [{conf}]  {issue}  (line {line})")
            click.echo(f"          Fix: {fix}")

        click.echo()

    if truncated:
        click.echo(f"  (showing {limit} of more findings — use --limit to see more)")
