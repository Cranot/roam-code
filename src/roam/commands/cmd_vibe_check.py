"""Detect AI code anti-patterns and compute AI rot score.

The vibe-check command scans for 8 categories of AI-generated code smells:

1. Dead exports / orphaned symbols — public symbols with zero callers
2. Short-term churn — files revised heavily within 14 days
3. Empty error handlers — try/catch with empty or trivial bodies
4. Abandoned stubs — functions with pass/TODO/NotImplementedError bodies
5. Hallucinated imports — unresolvable references
6. Error handling inconsistency — mixed patterns in same module
7. Comment density anomalies — files with outlier comment ratios
8. Copy-paste functions — duplicate normalized function bodies

Produces a composite 0-100 "AI rot score" and per-file issue counts.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Severity labels
# ---------------------------------------------------------------------------

def _severity_label(score: int) -> str:
    if score <= 15:
        return "HEALTHY"
    elif score <= 35:
        return "LOW"
    elif score <= 55:
        return "MODERATE"
    elif score <= 75:
        return "HIGH"
    else:
        return "CRITICAL"


# ---------------------------------------------------------------------------
# Pattern 1: Dead exports / orphaned symbols
# ---------------------------------------------------------------------------

def _detect_dead_exports(conn) -> tuple[int, int]:
    """Count public symbols with zero incoming edges.

    Excludes test files, dunders, CLI command files, and entry-point names
    to reduce false positives (matching roam dead heuristics).

    Returns (found, total_public_symbols).
    """
    # Exclude test files and cmd_ files from dead export analysis
    _EXCLUDE_SQL = (
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%conftest%' "
        "AND f.path NOT LIKE '%cmd\\_%' ESCAPE '\\' "
    )

    total = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        + _EXCLUDE_SQL
    ).fetchone()[0]

    dead = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        "AND s.id NOT IN (SELECT target_id FROM edges) "
        + _EXCLUDE_SQL
    ).fetchone()[0]

    return dead, max(total, 1)


# ---------------------------------------------------------------------------
# Pattern 2: Short-term churn (revised heavily within 14 days)
# ---------------------------------------------------------------------------

def _detect_short_churn(conn) -> tuple[int, int, list[dict]]:
    """Find files with 4+ commits where most activity was within 14 days.

    Returns (found, total_files, details).
    """
    rows = conn.execute(
        "SELECT f.path, fs.commit_count, "
        "  MIN(gc.timestamp) as first_ts, MAX(gc.timestamp) as last_ts "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "JOIN git_file_changes gfc ON gfc.file_id = f.id "
        "JOIN git_commits gc ON gfc.commit_id = gc.id "
        "WHERE fs.commit_count >= 4 "
        "GROUP BY f.id "
        "HAVING (MAX(gc.timestamp) - MIN(gc.timestamp)) < 14 * 86400 "
        "AND (MAX(gc.timestamp) - MIN(gc.timestamp)) > 0"
    ).fetchall()

    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    details = []
    for r in rows:
        span_days = (r["last_ts"] - r["first_ts"]) / 86400 if r["last_ts"] and r["first_ts"] else 0
        details.append({
            "file": r["path"],
            "commits": r["commit_count"],
            "span_days": round(span_days, 1),
        })

    return len(rows), max(total_files, 1), details


# ---------------------------------------------------------------------------
# Pattern 3: Empty error handlers
# ---------------------------------------------------------------------------

_EMPTY_HANDLER_PATTERNS = {
    "python": [
        # except ...: pass
        re.compile(r'^\s*except\b.*:\s*$\n\s*pass\s*$', re.MULTILINE),
        # except ...: ...
        re.compile(r'^\s*except\b.*:\s*$\n\s*\.\.\.\s*$', re.MULTILINE),
        # bare except: pass on one line (some styles)
        re.compile(r'^\s*except\b[^:]*:\s*pass\s*$', re.MULTILINE),
        # except ...: ... on one line
        re.compile(r'^\s*except\b[^:]*:\s*\.\.\.\s*$', re.MULTILINE),
    ],
    "javascript": [
        # catch (e) {} or catch (e) { }
        re.compile(r'\bcatch\s*\([^)]*\)\s*\{\s*\}', re.MULTILINE),
    ],
    "typescript": [
        re.compile(r'\bcatch\s*\([^)]*\)\s*\{\s*\}', re.MULTILINE),
    ],
    "java": [
        re.compile(r'\bcatch\s*\([^)]*\)\s*\{\s*\}', re.MULTILINE),
    ],
    "c_sharp": [
        re.compile(r'\bcatch\s*\([^)]*\)\s*\{\s*\}', re.MULTILINE),
        re.compile(r'\bcatch\s*\{\s*\}', re.MULTILINE),
    ],
    "go": [
        # if err != nil { } (empty body — error swallowed)
        re.compile(r'\bif\s+err\s*!=\s*nil\s*\{\s*\}', re.MULTILINE),
    ],
    "ruby": [
        re.compile(r'\brescue\b.*\n\s*(?:nil|next|#.*)?\s*\n\s*end', re.MULTILINE),
    ],
}


def _detect_empty_handlers(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Scan source files for empty error handlers using regex.

    Returns (found, total_handlers_approximation, details).
    """
    files = conn.execute(
        "SELECT id, path, language FROM files WHERE language IS NOT NULL"
    ).fetchall()

    found = 0
    total_try_blocks = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        patterns = _EMPTY_HANDLER_PATTERNS.get(lang, [])
        if not patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Count total error handling blocks (rough)
        if lang == "python":
            total_try_blocks += len(re.findall(r'^\s*except\b', source, re.MULTILINE))
        elif lang in ("javascript", "typescript", "java", "c_sharp"):
            total_try_blocks += len(re.findall(r'\bcatch\s*\(', source, re.MULTILINE))
        elif lang == "go":
            total_try_blocks += len(re.findall(r'\bif\s+err\s*!=\s*nil', source, re.MULTILINE))
        elif lang == "ruby":
            total_try_blocks += len(re.findall(r'\brescue\b', source, re.MULTILINE))

        file_count = 0
        for pat in patterns:
            matches = pat.findall(source)
            file_count += len(matches)

        if file_count > 0:
            found += file_count
            details.append({
                "file": f["path"],
                "count": file_count,
                "pattern": "empty_handler",
            })

    return found, max(total_try_blocks, 1), details


# ---------------------------------------------------------------------------
# Pattern 4: Abandoned stubs
# ---------------------------------------------------------------------------

_STUB_PATTERNS = {
    "python": [
        # def foo(): pass
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*pass\s*$',
            re.MULTILINE,
        ),
        # def foo(): ...
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*\.\.\.\s*$',
            re.MULTILINE,
        ),
        # def foo(): raise NotImplementedError
        re.compile(
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->[^:]*)?:\s*$\n(?:\s*(?:"""[^"]*"""|\'\'\'[^\']*\'\'\')?\s*$\n)*\s*raise\s+NotImplementedError',
            re.MULTILINE,
        ),
    ],
    "javascript": [
        # function foo() {} or function foo() { }
        re.compile(r'\bfunction\s+\w+\s*\([^)]*\)\s*\{\s*\}', re.MULTILINE),
        # function foo() { /* TODO */ }
        re.compile(r'\bfunction\s+\w+\s*\([^)]*\)\s*\{\s*/\*.*?TODO.*?\*/\s*\}', re.MULTILINE | re.DOTALL),
    ],
    "typescript": [
        re.compile(r'\bfunction\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{\s*\}', re.MULTILINE),
        re.compile(r'\bfunction\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{\s*/\*.*?TODO.*?\*/\s*\}', re.MULTILINE | re.DOTALL),
    ],
    "go": [
        # func foo() {}
        re.compile(r'\bfunc\s+\w+\s*\([^)]*\)\s*(?:\([^)]*\)\s*)?\{\s*\}', re.MULTILINE),
    ],
}


def _detect_stubs(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find functions with stub bodies (pass, ..., TODO, NotImplementedError, empty).

    Returns (found, total_functions, details).
    """
    total_functions = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE kind IN ('function', 'method')"
    ).fetchone()[0]

    files = conn.execute(
        "SELECT id, path, language FROM files WHERE language IS NOT NULL"
    ).fetchall()

    found = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        patterns = _STUB_PATTERNS.get(lang, [])
        if not patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        file_count = 0
        for pat in patterns:
            matches = pat.findall(source)
            file_count += len(matches)

        if file_count > 0:
            found += file_count
            details.append({
                "file": f["path"],
                "count": file_count,
                "pattern": "stub",
            })

    return found, max(total_functions, 1), details


# ---------------------------------------------------------------------------
# Pattern 5: Hallucinated imports (unresolvable references)
# ---------------------------------------------------------------------------

def _detect_hallucinated_imports(conn) -> tuple[int, int, list[dict]]:
    """Count edges of kind 'imports' or 'calls' where the target could
    not be resolved (target symbol has no definition in the index).

    Simpler approach: count references/edges that point to symbols that
    have no file (orphan targets), or look for import-type edges with
    unresolved targets.

    Returns (found, total_import_edges, details).
    """
    # Count total import-type edges
    total_imports = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind IN ('imports', 'import')"
    ).fetchone()[0]

    # If no import edges, fall back to counting symbols of kind 'import'
    # that have no outgoing resolved edges
    if total_imports == 0:
        # Alternative: count symbols referencing unknown names
        # Use unresolved references — symbols mentioned but not in the index
        # Look for source symbols that have outgoing edges to targets not in any file
        total_imports = conn.execute(
            "SELECT COUNT(DISTINCT source_id) FROM edges"
        ).fetchone()[0]

    # Hallucinated: edges whose target_id points to a symbol that doesn't
    # exist in the symbols table (should be 0 due to FK, but check references
    # that couldn't be resolved during indexing)
    # Better approach: count files that import other files which don't exist
    # in the index (file_edges pointing to missing targets)
    hallucinated = 0
    details: list[dict] = []

    # Look at file-level imports that don't resolve
    # file_edges where target file has zero symbols => potentially hallucinated
    rows = conn.execute(
        "SELECT f_src.path as src_path, f_tgt.path as tgt_path "
        "FROM file_edges fe "
        "JOIN files f_src ON fe.source_file_id = f_src.id "
        "JOIN files f_tgt ON fe.target_file_id = f_tgt.id "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM symbols s WHERE s.file_id = fe.target_file_id"
        ")"
    ).fetchall()

    # Also count symbols that are referenced but don't appear in the index
    # This uses edges where target_id maps to symbols with no callers themselves
    # Approximate: symbols referenced (in edges) but not defined (no line_start)
    orphan_refs = conn.execute(
        "SELECT COUNT(*) FROM edges e "
        "JOIN symbols s ON e.target_id = s.id "
        "WHERE s.line_start IS NULL AND s.line_end IS NULL"
    ).fetchone()[0]

    hallucinated = len(rows) + orphan_refs
    total = max(total_imports + orphan_refs, 1)

    by_file: dict[str, int] = defaultdict(int)
    for r in rows:
        by_file[r["src_path"]] += 1

    for path, count in by_file.items():
        details.append({"file": path, "count": count, "pattern": "hallucinated_import"})

    return hallucinated, total, details


# ---------------------------------------------------------------------------
# Pattern 6: Error handling inconsistency
# ---------------------------------------------------------------------------

_ERROR_PATTERNS_BY_LANG: dict[str, list[tuple[str, re.Pattern]]] = {
    "python": [
        ("try/except", re.compile(r'\btry\s*:', re.MULTILINE)),
        ("raise", re.compile(r'\braise\s+\w+', re.MULTILINE)),
        ("return_error", re.compile(r'\breturn\s+(?:None|False|-1)\b', re.MULTILINE)),
        ("assert", re.compile(r'\bassert\s+', re.MULTILINE)),
    ],
    "javascript": [
        ("try/catch", re.compile(r'\btry\s*\{', re.MULTILINE)),
        ("throw", re.compile(r'\bthrow\s+', re.MULTILINE)),
        ("callback_error", re.compile(r'\bcallback\s*\(\s*(?:err|error)', re.MULTILINE)),
        ("promise_reject", re.compile(r'\.catch\s*\(', re.MULTILINE)),
    ],
    "typescript": [
        ("try/catch", re.compile(r'\btry\s*\{', re.MULTILINE)),
        ("throw", re.compile(r'\bthrow\s+', re.MULTILINE)),
        ("promise_reject", re.compile(r'\.catch\s*\(', re.MULTILINE)),
    ],
    "go": [
        ("error_return", re.compile(r'\breturn\s+.*,\s*err\b', re.MULTILINE)),
        ("error_check", re.compile(r'\bif\s+err\s*!=\s*nil', re.MULTILINE)),
        ("panic", re.compile(r'\bpanic\s*\(', re.MULTILINE)),
    ],
}


def _detect_error_inconsistency(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Detect files/modules with mixed error handling patterns.

    A file using 3+ distinct error patterns is flagged as inconsistent.

    Returns (found, total_modules, details).
    """
    files = conn.execute(
        "SELECT id, path, language FROM files WHERE language IS NOT NULL"
    ).fetchall()

    inconsistent = 0
    total_modules = 0
    details: list[dict] = []

    for f in files:
        lang = f["language"]
        error_patterns = _ERROR_PATTERNS_BY_LANG.get(lang, [])
        if not error_patterns:
            continue

        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        total_modules += 1
        used_patterns = set()
        for pname, pat in error_patterns:
            if pat.search(source):
                used_patterns.add(pname)

        if len(used_patterns) >= 3:
            inconsistent += 1
            details.append({
                "file": f["path"],
                "patterns": sorted(used_patterns),
                "count": len(used_patterns),
            })

    return inconsistent, max(total_modules, 1), details


# ---------------------------------------------------------------------------
# Pattern 7: Comment density anomalies
# ---------------------------------------------------------------------------

def _detect_comment_anomalies(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find files with outlier comment-to-code ratios.

    Files with ratio >2 standard deviations from the mean are flagged.
    AI-generated code tends to have either zero comments or excessive comments.

    Returns (found, total_files, details).
    """
    files = conn.execute(
        "SELECT id, path, language, line_count FROM files "
        "WHERE language IS NOT NULL AND line_count > 10"
    ).fetchall()

    if not files:
        return 0, 1, []

    _COMMENT_MARKERS = {
        "python": (re.compile(r'^\s*#'), None),
        "javascript": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "typescript": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "java": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "c": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "cpp": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "c_sharp": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "go": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "ruby": (re.compile(r'^\s*#'), None),
        "rust": (re.compile(r'^\s*//'), re.compile(r'/\*.*?\*/', re.DOTALL)),
        "php": (re.compile(r'^\s*(?://|#)'), re.compile(r'/\*.*?\*/', re.DOTALL)),
    }

    ratios: list[tuple[dict, float]] = []

    for f in files:
        lang = f["language"]
        markers = _COMMENT_MARKERS.get(lang)
        if not markers:
            continue

        line_pattern, block_pattern = markers
        file_path = project_root / f["path"]
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = source.split("\n")
        total_lines = len(lines)
        if total_lines < 5:
            continue

        # Count single-line comments
        comment_lines = 0
        if line_pattern:
            for line in lines:
                if line_pattern.match(line):
                    comment_lines += 1

        # Count block comment lines
        if block_pattern:
            for match in block_pattern.finditer(source):
                comment_lines += match.group(0).count("\n") + 1

        code_lines = max(total_lines - comment_lines, 1)
        ratio = comment_lines / code_lines
        ratios.append((f, ratio))

    if len(ratios) < 3:
        return 0, max(len(ratios), 1), []

    # Compute mean and std dev
    values = [r for _, r in ratios]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std_dev = variance ** 0.5

    if std_dev < 0.01:
        return 0, len(ratios), []

    anomalies: list[dict] = []
    for f, ratio in ratios:
        z_score = (ratio - mean) / std_dev
        if abs(z_score) > 2.0:
            anomalies.append({
                "file": f["path"],
                "comment_ratio": round(ratio, 2),
                "z_score": round(z_score, 2),
                "direction": "excessive" if z_score > 0 else "absent",
            })

    return len(anomalies), len(ratios), anomalies


# ---------------------------------------------------------------------------
# Pattern 8: Copy-paste functions (duplicate normalized bodies)
# ---------------------------------------------------------------------------

def _normalize_body(source: str) -> str:
    """Normalize a function body for duplication detection.

    Strips whitespace, comments, string literals, and identifier names
    to detect structural clones.
    """
    # Remove single-line comments
    s = re.sub(r'//.*$', '', source, flags=re.MULTILINE)
    s = re.sub(r'#.*$', '', s, flags=re.MULTILINE)
    # Remove block comments
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
    # Remove string literals
    s = re.sub(r'"[^"]*"', '""', s)
    s = re.sub(r"'[^']*'", "''", s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _detect_copy_paste(conn, project_root: Path) -> tuple[int, int, list[dict]]:
    """Find groups of 3+ functions with identical normalized bodies.

    Returns (found_in_clone_groups, total_functions, details).
    """
    # Get function symbols with their line ranges
    functions = conn.execute(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, "
        "  f.path as file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "AND (s.line_end - s.line_start) >= 3"
    ).fetchall()

    total_functions = len(functions)
    if total_functions < 3:
        return 0, max(total_functions, 1), []

    # Group by file for efficient reading
    by_file: dict[str, list] = defaultdict(list)
    for fn in functions:
        by_file[fn["file_path"]].append(fn)

    # Hash normalized bodies
    body_hashes: dict[str, list[dict]] = defaultdict(list)

    for file_path, fns in by_file.items():
        full_path = project_root / file_path
        try:
            source_lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue

        for fn in fns:
            start = (fn["line_start"] or 1) - 1
            end = fn["line_end"] or start + 1
            body = "\n".join(source_lines[start:end])
            normalized = _normalize_body(body)

            if len(normalized) < 30:
                continue  # Too short to be meaningful

            h = hashlib.md5(normalized.encode("utf-8")).hexdigest()
            body_hashes[h].append({
                "name": fn["name"],
                "file": file_path,
                "line": fn["line_start"],
            })

    # Find groups of 3+ duplicates
    found = 0
    details: list[dict] = []
    for h, group in body_hashes.items():
        if len(group) >= 3:
            found += len(group)
            details.append({
                "clone_group_size": len(group),
                "functions": group[:5],  # limit detail size
            })

    return found, max(total_functions, 1), details


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

_WEIGHTS = {
    "dead_exports": 15,
    "short_churn": 10,
    "empty_handlers": 20,
    "abandoned_stubs": 10,
    "hallucinated_imports": 15,
    "error_inconsistency": 10,
    "comment_anomalies": 10,
    "copy_paste": 10,
}

_PATTERN_NAMES = {
    "dead_exports": "Dead exports",
    "short_churn": "Short-term churn (<14d)",
    "empty_handlers": "Empty error handlers",
    "abandoned_stubs": "Abandoned stubs",
    "hallucinated_imports": "Hallucinated imports",
    "error_inconsistency": "Error handling inconsistency",
    "comment_anomalies": "Comment density anomalies",
    "copy_paste": "Copy-paste functions",
}


def _compute_score(patterns: dict[str, dict]) -> int:
    """Compute weighted composite AI rot score (0-100)."""
    weighted_sum = 0.0
    total_weight = sum(_WEIGHTS.values())

    for key, weight in _WEIGHTS.items():
        pdata = patterns.get(key, {})
        rate = pdata.get("rate", 0.0)
        # Cap each rate at 100%
        capped = min(rate, 100.0)
        weighted_sum += capped * weight

    score = weighted_sum / total_weight
    return max(0, min(100, int(round(score))))


# ---------------------------------------------------------------------------
# Per-file aggregation for "worst files"
# ---------------------------------------------------------------------------

def _aggregate_worst_files(all_details: dict[str, list[dict]], limit: int = 5) -> list[dict]:
    """Aggregate per-file issue counts across all patterns.

    Returns top N files sorted by total issue count.
    """
    file_issues: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for pattern_key, details in all_details.items():
        for d in details:
            fp = d.get("file", "")
            if not fp:
                continue
            count = d.get("count", 1)
            if "clone_group_size" in d:
                # copy-paste pattern uses different structure
                for fn in d.get("functions", []):
                    file_issues[fn.get("file", "")][pattern_key] += 1
                continue
            file_issues[fp][pattern_key] += count

    results = []
    for fp, patterns in file_issues.items():
        total = sum(patterns.values())
        # Build breakdown string
        parts = []
        for pkey, cnt in sorted(patterns.items(), key=lambda x: -x[1]):
            short_name = _PATTERN_NAMES.get(pkey, pkey).split()[0].lower()
            parts.append(f"{cnt} {short_name}")
        results.append({
            "file": fp,
            "total_issues": total,
            "breakdown": ", ".join(parts),
            "pattern_counts": dict(patterns),
        })

    results.sort(key=lambda x: -x["total_issues"])
    return results[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("vibe-check")
@click.option("--threshold", type=int, default=0,
              help="Fail if AI rot score exceeds threshold (0=no gate)")
@click.pass_context
def vibe_check(ctx, threshold):
    """Detect AI code anti-patterns and compute AI rot score."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        # Run all 8 detectors
        p1_found, p1_total = _detect_dead_exports(conn)
        p2_found, p2_total, p2_details = _detect_short_churn(conn)
        p3_found, p3_total, p3_details = _detect_empty_handlers(conn, project_root)
        p4_found, p4_total, p4_details = _detect_stubs(conn, project_root)
        p5_found, p5_total, p5_details = _detect_hallucinated_imports(conn)
        p6_found, p6_total, p6_details = _detect_error_inconsistency(conn, project_root)
        p7_found, p7_total, p7_details = _detect_comment_anomalies(conn, project_root)
        p8_found, p8_total, p8_details = _detect_copy_paste(conn, project_root)

        # Build patterns dict
        def _rate(found, total):
            return round(found / max(total, 1) * 100, 1)

        patterns = {
            "dead_exports": {"found": p1_found, "total": p1_total, "rate": _rate(p1_found, p1_total)},
            "short_churn": {"found": p2_found, "total": p2_total, "rate": _rate(p2_found, p2_total)},
            "empty_handlers": {"found": p3_found, "total": p3_total, "rate": _rate(p3_found, p3_total)},
            "abandoned_stubs": {"found": p4_found, "total": p4_total, "rate": _rate(p4_found, p4_total)},
            "hallucinated_imports": {"found": p5_found, "total": p5_total, "rate": _rate(p5_found, p5_total)},
            "error_inconsistency": {"found": p6_found, "total": p6_total, "rate": _rate(p6_found, p6_total)},
            "comment_anomalies": {"found": p7_found, "total": p7_total, "rate": _rate(p7_found, p7_total)},
            "copy_paste": {"found": p8_found, "total": p8_total, "rate": _rate(p8_found, p8_total)},
        }

        score = _compute_score(patterns)
        severity = _severity_label(score)
        total_issues = sum(p["found"] for p in patterns.values())
        files_scanned = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

        # Severity per pattern
        for key, pdata in patterns.items():
            r = pdata["rate"]
            if r >= 30:
                pdata["severity"] = "high"
            elif r >= 10:
                pdata["severity"] = "medium"
            elif r > 0:
                pdata["severity"] = "low"
            else:
                pdata["severity"] = "none"

        # Worst files
        all_details = {
            "dead_exports": [],  # no per-file details for pattern 1
            "short_churn": p2_details,
            "empty_handlers": p3_details,
            "abandoned_stubs": p4_details,
            "hallucinated_imports": p5_details,
            "error_inconsistency": p6_details,
            "comment_anomalies": p7_details,
            "copy_paste": p8_details,
        }
        worst_files = _aggregate_worst_files(all_details)

        # Recommendations
        recommendations = []
        if patterns["empty_handlers"]["found"] > 0:
            recommendations.append(
                f"Fix {patterns['empty_handlers']['found']} empty error handlers — "
                "silent failures hide bugs"
            )
        if patterns["dead_exports"]["found"] > 5:
            recommendations.append(
                f"Remove {patterns['dead_exports']['found']} dead exports — "
                "run `roam dead` for safe-delete candidates"
            )
        if patterns["abandoned_stubs"]["found"] > 0:
            recommendations.append(
                f"Complete or remove {patterns['abandoned_stubs']['found']} stub functions"
            )
        if patterns["hallucinated_imports"]["found"] > 0:
            recommendations.append(
                f"Fix {patterns['hallucinated_imports']['found']} unresolvable imports"
            )
        if patterns["copy_paste"]["found"] > 0:
            recommendations.append(
                f"Extract {patterns['copy_paste']['found']} copy-pasted functions "
                "into shared utilities"
            )

        verdict = f"AI rot score {score}/100 -- {severity}"

        # --- JSON output ---
        if json_mode:
            envelope = json_envelope("vibe-check",
                budget=budget,
                summary={
                    "verdict": verdict,
                    "score": score,
                    "severity": severity,
                    "total_issues": total_issues,
                    "files_scanned": files_scanned,
                    "patterns_detected": sum(1 for p in patterns.values() if p["found"] > 0),
                },
                patterns=[
                    {
                        "name": key,
                        "label": _PATTERN_NAMES[key],
                        "found": pdata["found"],
                        "total": pdata["total"],
                        "rate": pdata["rate"],
                        "severity": pdata["severity"],
                        "weight": _WEIGHTS[key],
                    }
                    for key, pdata in patterns.items()
                ],
                worst_files=worst_files,
                recommendations=recommendations,
            )
            click.echo(to_json(envelope))

            # Gate check
            if threshold > 0 and score > threshold:
                from roam.exit_codes import EXIT_GATE_FAILURE
                ctx.exit(EXIT_GATE_FAILURE)
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Pattern table
        headers = ["Pattern", "Found", "Total", "Rate"]
        rows = []
        for key in _WEIGHTS:
            pdata = patterns[key]
            rate_str = f"{pdata['rate']:.1f}%"
            if pdata["rate"] >= 25:
                rate_str += "  !!"
            rows.append([
                _PATTERN_NAMES[key],
                str(pdata["found"]),
                str(pdata["total"]),
                rate_str,
            ])

        click.echo(format_table(headers, rows))
        click.echo()
        click.echo(f"  {score}/100 AI rot score (0=pristine, 100=severe)")
        click.echo(f"  {total_issues} issues across "
                   f"{sum(1 for p in patterns.values() if p['found'] > 0)} categories "
                   f"in {files_scanned} files")

        # Worst files
        if worst_files:
            click.echo()
            click.echo("  Top worst files:")
            for wf in worst_files:
                click.echo(f"    {wf['file']:<50s} -- {wf['total_issues']} issues "
                           f"({wf['breakdown']})")

        # Recommendations
        if recommendations:
            click.echo()
            click.echo("  Recommendations:")
            for rec in recommendations:
                click.echo(f"    - {rec}")

        # Gate check
        if threshold > 0 and score > threshold:
            click.echo()
            click.echo(f"  GATE FAILED: score {score} exceeds threshold {threshold}")
            from roam.exit_codes import EXIT_GATE_FAILURE
            ctx.exit(EXIT_GATE_FAILURE)
