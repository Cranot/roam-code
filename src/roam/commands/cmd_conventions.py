"""Auto-detect implicit codebase conventions and patterns."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import (
    format_table, loc, abbrev_kind, to_json, json_envelope,
)
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Case-style detection
# ---------------------------------------------------------------------------

_CASE_PATTERNS = {
    "snake_case":  re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)+$'),
    "camelCase":   re.compile(r'^[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*$'),
    "PascalCase":  re.compile(r'^[A-Z][a-zA-Z0-9]*[a-z][a-zA-Z0-9]*$'),
    "UPPER_SNAKE": re.compile(r'^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$'),
    "kebab-case":  re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)+$'),
}

# Single-word names match multiple conventions; classify them separately.
_SINGLE_LOWER = re.compile(r'^[a-z][a-z0-9]*$')
_SINGLE_UPPER = re.compile(r'^[A-Z][A-Z0-9]*$')
_SINGLE_PASCAL = re.compile(r'^[A-Z][a-z0-9]+$')

# Names that are too short or generic to classify meaningfully.
_MIN_NAME_LEN = 2

# Dunder / framework names to skip when detecting naming conventions.
_SKIP_NAMES = frozenset({
    "__init__", "__str__", "__repr__", "__new__", "__del__",
    "__enter__", "__exit__", "__getattr__", "__setattr__",
    "__getitem__", "__setitem__", "__len__", "__iter__", "__next__",
    "__call__", "__hash__", "__eq__", "__lt__", "__gt__", "__le__",
    "__ge__", "__ne__", "__bool__", "__contains__", "__add__",
    "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__",
    "__pow__", "__and__", "__or__", "__xor__", "__invert__",
    "constructor", "toString", "valueOf",
})


def classify_case(name: str) -> str | None:
    """Return the case style of *name*, or None if unclassifiable."""
    if len(name) < _MIN_NAME_LEN or name in _SKIP_NAMES:
        return None
    if name.startswith("__") and name.endswith("__"):
        return None  # dunder

    for style, pattern in _CASE_PATTERNS.items():
        if pattern.match(name):
            return style

    # Single-word heuristics
    if _SINGLE_PASCAL.match(name):
        return "PascalCase"
    if _SINGLE_LOWER.match(name):
        return "snake_case"  # single lowercase word is compatible with snake
    if _SINGLE_UPPER.match(name):
        return "UPPER_SNAKE"

    return None


# Kind groupings for naming analysis
_KIND_GROUPS = {
    "function": "functions",
    "method":   "functions",
    "class":    "classes",
    "interface": "classes",
    "struct":    "classes",
    "trait":     "classes",
    "enum":      "classes",
    "variable":  "variables",
    "constant":  "constants",
    "property":  "variables",
    "field":     "variables",
}


def _group_for_kind(kind: str) -> str:
    return _KIND_GROUPS.get(kind, "other")


# ---------------------------------------------------------------------------
# Prefix / suffix detection
# ---------------------------------------------------------------------------

def _detect_affixes(names: list[str], min_count: int = 5,
                    min_ratio: float = 0.03) -> dict:
    """Detect common prefixes and suffixes from a list of names."""
    prefix_counter: Counter = Counter()
    suffix_counter: Counter = Counter()

    for name in names:
        # Prefixes: split on _ or case boundary
        parts = re.split(r'[_]', name)
        if len(parts) >= 2 and len(parts[0]) >= 2:
            prefix_counter[parts[0] + "_"] += 1
        # Check camelCase prefix (lowercase start up to first uppercase)
        m = re.match(r'^([a-z]+)[A-Z]', name)
        if m and len(m.group(1)) >= 2:
            prefix_counter[m.group(1)] += 1

        # Suffixes
        if len(parts) >= 2 and len(parts[-1]) >= 2:
            suffix_counter["_" + parts[-1]] += 1
        # PascalCase suffix: last uppercase word
        m = re.search(r'[a-z]([A-Z][a-z]+)$', name)
        if m and len(m.group(1)) >= 3:
            suffix_counter[m.group(1)] += 1

    total = max(len(names), 1)
    threshold = max(min_count, int(total * min_ratio))

    prefixes = [
        {"affix": p, "count": c, "percent": round(100 * c / total, 1)}
        for p, c in prefix_counter.most_common(10)
        if c >= threshold
    ]
    suffixes = [
        {"affix": s, "count": c, "percent": round(100 * c / total, 1)}
        for s, c in suffix_counter.most_common(10)
        if c >= threshold
    ]
    return {"prefixes": prefixes, "suffixes": suffixes}


# ---------------------------------------------------------------------------
# File organization detection
# ---------------------------------------------------------------------------

_TEST_PATTERNS = [
    ("test_*.py",     re.compile(r'(^|/)test_[^/]+\.py$')),
    ("*_test.py",     re.compile(r'(^|/)[^/]+_test\.py$')),
    ("*.test.ts",     re.compile(r'(^|/)[^/]+\.test\.ts$')),
    ("*.test.tsx",    re.compile(r'(^|/)[^/]+\.test\.tsx$')),
    ("*.test.js",     re.compile(r'(^|/)[^/]+\.test\.js$')),
    ("*.test.jsx",    re.compile(r'(^|/)[^/]+\.test\.jsx$')),
    ("*.spec.ts",     re.compile(r'(^|/)[^/]+\.spec\.ts$')),
    ("*.spec.tsx",    re.compile(r'(^|/)[^/]+\.spec\.tsx$')),
    ("*.spec.js",     re.compile(r'(^|/)[^/]+\.spec\.js$')),
    ("*.spec.jsx",    re.compile(r'(^|/)[^/]+\.spec\.jsx$')),
    ("*_test.go",     re.compile(r'(^|/)[^/]+_test\.go$')),
    ("*_test.rs",     re.compile(r'(^|/)[^/]+_test\.rs$')),
    ("Test*.java",    re.compile(r'(^|/)Test[^/]+\.java$')),
    ("*Test.java",    re.compile(r'(^|/)[^/]+Test\.java$')),
]

_BARREL_NAMES = frozenset({
    "index.ts", "index.js", "index.tsx", "index.jsx",
    "index.mjs", "index.cjs",
    "__init__.py",
})


def _analyze_files(paths: list[str]) -> dict:
    """Analyze file paths for directory structure and test conventions."""
    normalized = [p.replace("\\", "/") for p in paths]

    # Top-level directory counts
    dir_counts: Counter = Counter()
    for p in normalized:
        parts = p.split("/")
        if len(parts) > 1:
            dir_counts[parts[0] + "/"] += 1

    top_dirs = [
        {"dir": d, "count": c}
        for d, c in dir_counts.most_common(15)
        if c >= 2
    ]

    # Test file patterns
    test_pattern_counts: Counter = Counter()
    test_dir_counts: Counter = Counter()
    total_test_files = 0

    for p in normalized:
        for pattern_name, regex in _TEST_PATTERNS:
            if regex.search(p):
                test_pattern_counts[pattern_name] += 1
                total_test_files += 1
                # Track which directories contain tests
                parts = p.split("/")
                if len(parts) > 1:
                    test_dir_counts[parts[0] + "/"] += 1
                break

    test_patterns = [
        {"pattern": pat, "count": c}
        for pat, c in test_pattern_counts.most_common(5)
        if c >= 1
    ]

    test_dirs = [
        {"dir": d, "count": c}
        for d, c in test_dir_counts.most_common(5)
        if c >= 1
    ]

    # Barrel files
    barrel_count = 0
    for p in normalized:
        basename = p.rsplit("/", 1)[-1] if "/" in p else p
        if basename in _BARREL_NAMES:
            barrel_count += 1

    return {
        "total_files": len(paths),
        "top_dirs": top_dirs,
        "test_patterns": test_patterns,
        "test_dirs": test_dirs,
        "test_file_count": total_test_files,
        "barrel_files": barrel_count,
        "has_barrels": barrel_count > 0,
    }


# ---------------------------------------------------------------------------
# Import pattern detection
# ---------------------------------------------------------------------------

def _analyze_imports(conn) -> dict:
    """Analyze import edges for absolute vs relative and grouping patterns."""
    # Get edges with kind='imports' joining file paths
    rows = conn.execute("""
        SELECT fe.source_file_id, fe.target_file_id, fe.symbol_count,
               sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()

    if not rows:
        return {
            "total_import_edges": 0,
            "absolute_imports": 0,
            "relative_imports": 0,
            "absolute_pct": 0,
            "style": "unknown",
        }

    total = len(rows)
    relative = 0
    absolute = 0

    for r in rows:
        src = r["source_path"].replace("\\", "/")
        tgt = r["target_path"].replace("\\", "/")

        # Heuristic: if source and target share a common prefix directory,
        # and the target is within 2 levels, it's likely a relative import.
        src_parts = src.rsplit("/", 1)
        tgt_parts = tgt.rsplit("/", 1)

        src_dir = src_parts[0] if len(src_parts) > 1 else ""
        tgt_dir = tgt_parts[0] if len(tgt_parts) > 1 else ""

        if src_dir and tgt_dir and (
            src_dir == tgt_dir or
            src_dir.startswith(tgt_dir + "/") or
            tgt_dir.startswith(src_dir + "/")
        ):
            relative += 1
        else:
            absolute += 1

    abs_pct = round(100 * absolute / total, 1) if total else 0
    style = "absolute" if abs_pct >= 60 else "relative" if abs_pct <= 40 else "mixed"

    return {
        "total_import_edges": total,
        "absolute_imports": absolute,
        "relative_imports": relative,
        "absolute_pct": abs_pct,
        "relative_pct": round(100 * relative / total, 1) if total else 0,
        "style": style,
    }


# ---------------------------------------------------------------------------
# Export pattern detection
# ---------------------------------------------------------------------------

def _analyze_exports(conn) -> dict:
    """Analyze is_exported flag distribution across symbols."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported,
            SUM(CASE WHEN is_exported = 0 THEN 1 ELSE 0 END) as private
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
    """).fetchone()

    total = row["total"] or 0
    exported = row["exported"] or 0
    private = row["private"] or 0
    exported_pct = round(100 * exported / total, 1) if total else 0

    # Per-kind breakdown
    kind_rows = conn.execute("""
        SELECT kind,
               COUNT(*) as total,
               SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
        GROUP BY kind
        ORDER BY total DESC
    """).fetchall()

    by_kind = []
    for kr in kind_rows:
        kt = kr["total"] or 0
        ke = kr["exported"] or 0
        by_kind.append({
            "kind": kr["kind"],
            "total": kt,
            "exported": ke,
            "exported_pct": round(100 * ke / kt, 1) if kt else 0,
        })

    # Detect default-export vs named-export preference for JS/TS
    # Check if files have exactly one exported symbol (likely default export)
    default_style_rows = conn.execute("""
        SELECT f.id, f.path, COUNT(*) as exported_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.is_exported = 1
          AND f.language IN ('typescript', 'javascript', 'tsx', 'jsx')
        GROUP BY f.id
    """).fetchall()

    single_export_files = sum(1 for r in default_style_rows if r["exported_count"] == 1)
    multi_export_files = sum(1 for r in default_style_rows if r["exported_count"] > 1)
    js_ts_total = single_export_files + multi_export_files

    export_style = "unknown"
    if js_ts_total > 0:
        if single_export_files > multi_export_files:
            export_style = "default-export preferred"
        else:
            export_style = "named-exports preferred"

    return {
        "total_symbols": total,
        "exported": exported,
        "private": private,
        "exported_pct": exported_pct,
        "by_kind": by_kind,
        "js_ts_export_style": export_style,
        "js_ts_single_export_files": single_export_files,
        "js_ts_multi_export_files": multi_export_files,
    }


# ---------------------------------------------------------------------------
# Error handling detection
# ---------------------------------------------------------------------------

_ERROR_NAME_RE = re.compile(
    r'(Error|Exception|Err|Fault|Failure|Panic)$', re.IGNORECASE
)


def _analyze_error_handling(conn) -> dict:
    """Detect error/exception patterns from symbols and file complexity."""
    # Count error-related symbols.
    # Use a broad query then filter in Python to avoid LIKE false positives
    # (e.g., DEFAULT matching %Fault%).
    error_candidates = conn.execute("""
        SELECT s.name, s.kind, f.path as file_path, s.line_start
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE '%Error%'
           OR s.name LIKE '%Exception%'
           OR s.name LIKE '%Failure%'
    """).fetchall()
    error_symbols = [
        r for r in error_candidates
        if _ERROR_NAME_RE.search(r["name"])
        or "Error" in r["name"]
        or "Exception" in r["name"]
        or "Failure" in r["name"]
    ]

    error_classes = [r for r in error_symbols if r["kind"] in ("class", "struct", "interface")]
    error_functions = [r for r in error_symbols if r["kind"] in ("function", "method")]

    # Complexity as proxy for error handling density
    complexity_rows = conn.execute("""
        SELECT AVG(complexity) as avg_complexity,
               MAX(complexity) as max_complexity,
               COUNT(*) as file_count
        FROM file_stats
        WHERE complexity > 0
    """).fetchone()

    return {
        "error_symbol_count": len(error_symbols),
        "error_classes": len(error_classes),
        "error_functions": len(error_functions),
        "error_symbols": [
            {"name": r["name"], "kind": r["kind"],
             "file": r["file_path"], "line": r["line_start"]}
            for r in error_symbols[:20]
        ],
        "avg_complexity": round(complexity_rows["avg_complexity"] or 0, 1),
        "max_complexity": round(complexity_rows["max_complexity"] or 0, 1),
        "files_with_complexity": complexity_rows["file_count"] or 0,
    }


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@click.command()
@click.option('-n', 'max_outliers', default=10,
              help='Maximum outliers to display per category')
@click.pass_context
def conventions(ctx, max_outliers):
    """Auto-detect codebase naming, file, import, and export conventions."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        project = find_project_root().name

        # ---- 1. Naming conventions ----
        all_symbols = conn.execute("""
            SELECT s.name, s.kind, s.line_start, f.path as file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.kind IN ('function', 'method', 'class', 'interface',
                             'struct', 'trait', 'enum', 'variable',
                             'constant', 'property', 'field', 'type_alias')
        """).fetchall()

        # Group by kind-group, tally case styles
        group_cases: dict[str, Counter] = defaultdict(Counter)
        group_names: dict[str, list[str]] = defaultdict(list)
        symbol_details: list[dict] = []  # for outlier detection

        for sym in all_symbols:
            group = _group_for_kind(sym["kind"])
            style = classify_case(sym["name"])
            if style:
                group_cases[group][style] += 1
                group_names[group].append(sym["name"])
                symbol_details.append({
                    "name": sym["name"],
                    "kind": sym["kind"],
                    "group": group,
                    "style": style,
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                })

        # Determine dominant style per group
        naming_summary: dict[str, dict] = {}
        for group, counter in sorted(group_cases.items()):
            total = sum(counter.values())
            dominant_style, dominant_count = counter.most_common(1)[0]
            pct = round(100 * dominant_count / total, 1) if total else 0
            naming_summary[group] = {
                "dominant_style": dominant_style,
                "dominant_count": dominant_count,
                "total": total,
                "percent": pct,
                "breakdown": {s: c for s, c in counter.most_common()},
            }

        # Detect outliers (symbols that don't match dominant style)
        outliers: list[dict] = []
        for det in symbol_details:
            grp_info = naming_summary.get(det["group"])
            if grp_info and det["style"] != grp_info["dominant_style"]:
                outliers.append({
                    "name": det["name"],
                    "kind": det["kind"],
                    "actual_style": det["style"],
                    "expected_style": grp_info["dominant_style"],
                    "file": det["file"],
                    "line": det["line"],
                })

        # Affix detection
        all_names = [sym["name"] for sym in all_symbols
                     if len(sym["name"]) >= _MIN_NAME_LEN
                     and sym["name"] not in _SKIP_NAMES]
        affixes = _detect_affixes(all_names)

        # ---- 2. File organization ----
        file_rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        file_paths = [r["path"] for r in file_rows]
        file_info = _analyze_files(file_paths)

        # ---- 3. Import patterns ----
        import_info = _analyze_imports(conn)

        # ---- 4. Error handling ----
        error_info = _analyze_error_handling(conn)

        # ---- 5. Export patterns ----
        export_info = _analyze_exports(conn)

        # ---- JSON output ----
        if json_mode:
            violation_list = [
                {
                    "name": o["name"],
                    "kind": o["kind"],
                    "actual_style": o["actual_style"],
                    "expected_style": o["expected_style"],
                    "file": o["file"],
                    "line": o["line"],
                }
                for o in outliers
            ]
            summary = {
                "total_symbols_analyzed": len(symbol_details),
                "naming_groups": len(naming_summary),
                "outlier_count": len(outliers),
                "total_files": file_info["total_files"],
                "test_files": file_info["test_file_count"],
                "barrel_files": file_info["barrel_files"],
                "import_style": import_info["style"],
                "exported_pct": export_info["exported_pct"],
            }
            click.echo(to_json(json_envelope("conventions",
                summary=summary,
                naming=naming_summary,
                affixes=affixes,
                files=file_info,
                imports=import_info,
                exports=export_info,
                errors=error_info,
                violations=violation_list,
            )))
            return

        # ---- Text output ----
        click.echo(f"Conventions detected in {project}:\n")

        # -- Naming --
        click.echo("=== Naming ===")
        if naming_summary:
            for group, info in sorted(naming_summary.items()):
                click.echo(
                    f"  {group.capitalize()}: {info['dominant_style']} "
                    f"({info['percent']}% of {info['total']} {group})"
                )
                # Show minority styles if present
                for style, count in info["breakdown"].items():
                    if style != info["dominant_style"] and count >= 2:
                        pct = round(100 * count / info["total"], 1)
                        click.echo(f"    also: {style} ({pct}%, {count})")
        else:
            click.echo("  (no classifiable symbols found)")

        if outliers:
            click.echo(f"\n  Outliers ({len(outliers)} total):")
            for o in outliers[:max_outliers]:
                click.echo(
                    f"    {o['name']} ({o['actual_style']}, "
                    f"expected {o['expected_style']}) "
                    f"at {loc(o['file'], o['line'])}"
                )
            if len(outliers) > max_outliers:
                click.echo(f"    (+{len(outliers) - max_outliers} more)")

        if affixes["prefixes"] or affixes["suffixes"]:
            click.echo("\n  Common affixes:")
            for p in affixes["prefixes"][:5]:
                click.echo(f"    prefix {p['affix']}  ({p['count']} symbols, {p['percent']}%)")
            for s in affixes["suffixes"][:5]:
                click.echo(f"    suffix {s['affix']}  ({s['count']} symbols, {s['percent']}%)")

        # -- File organization --
        click.echo(f"\n=== File Organization ({file_info['total_files']} files) ===")
        if file_info["top_dirs"]:
            dir_rows = [[d["dir"], str(d["count"])] for d in file_info["top_dirs"]]
            click.echo(format_table(["Directory", "Files"], dir_rows))
        if file_info["test_patterns"]:
            click.echo(f"\n  Test files: {file_info['test_file_count']} detected")
            for tp in file_info["test_patterns"]:
                click.echo(f"    {tp['pattern']} ({tp['count']} files)")
            if file_info["test_dirs"]:
                dirs = ", ".join(d["dir"] for d in file_info["test_dirs"])
                click.echo(f"    in: {dirs}")
        else:
            click.echo("  Tests: (no standard test file patterns detected)")
        if file_info["has_barrels"]:
            click.echo(f"  Barrel/index files: {file_info['barrel_files']}")

        # -- Import style --
        click.echo(f"\n=== Import Style ({import_info['total_import_edges']} import edges) ===")
        if import_info["total_import_edges"] > 0:
            click.echo(
                f"  {import_info['style'].capitalize()} imports preferred "
                f"({import_info['absolute_pct']}% cross-directory, "
                f"{import_info['relative_pct']}% same-directory)"
            )
        else:
            click.echo("  (no import edges found)")

        # -- Error handling --
        click.echo(f"\n=== Error Handling ===")
        if error_info["error_symbol_count"] > 0:
            click.echo(
                f"  {error_info['error_symbol_count']} error-related symbols "
                f"({error_info['error_classes']} classes, "
                f"{error_info['error_functions']} functions)"
            )
            for es in error_info["error_symbols"][:5]:
                click.echo(
                    f"    {es['name']} ({abbrev_kind(es['kind'])}) "
                    f"at {loc(es['file'], es['line'])}"
                )
            if len(error_info["error_symbols"]) > 5:
                click.echo(f"    (+{len(error_info['error_symbols']) - 5} more)")
        else:
            click.echo("  (no error/exception symbols detected)")
        if error_info["files_with_complexity"] > 0:
            click.echo(
                f"  Avg file complexity: {error_info['avg_complexity']} "
                f"(max {error_info['max_complexity']})"
            )

        # -- Export pattern --
        click.echo(f"\n=== Export Pattern ({export_info['total_symbols']} symbols) ===")
        if export_info["total_symbols"] > 0:
            click.echo(
                f"  Exported: {export_info['exported']} "
                f"({export_info['exported_pct']}%)"
            )
            click.echo(
                f"  Private:  {export_info['private']} "
                f"({round(100 * export_info['private'] / export_info['total_symbols'], 1)}%)"
            )
            if export_info["by_kind"]:
                ek_rows = [
                    [abbrev_kind(k["kind"]), str(k["total"]),
                     str(k["exported"]), f"{k['exported_pct']}%"]
                    for k in export_info["by_kind"]
                ]
                click.echo(format_table(
                    ["Kind", "Total", "Exported", "Rate"], ek_rows
                ))
            if export_info["js_ts_export_style"] != "unknown":
                click.echo(f"  JS/TS: {export_info['js_ts_export_style']}")
        else:
            click.echo("  (no symbols found)")
