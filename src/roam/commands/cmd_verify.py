"""Pre-commit consistency verification against established codebase patterns."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root, batched_in
from roam.output.formatter import to_json, json_envelope, loc
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db
from roam.commands.cmd_conventions import (
    classify_case, _group_for_kind, _KIND_GROUPS, _SKIP_NAMES, _MIN_NAME_LEN,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXIT_GATE_FAILURE = 5

_CATEGORY_WEIGHTS = {
    "naming": 0.25,
    "imports": 0.20,
    "error_handling": 0.20,
    "duplicates": 0.20,
    "syntax": 0.15,
}

# Severity levels for violations
SEVERITY_FAIL = "FAIL"
SEVERITY_WARN = "WARN"
SEVERITY_INFO = "INFO"


# ---------------------------------------------------------------------------
# Naming consistency check
# ---------------------------------------------------------------------------

def _check_naming(conn, file_ids: list[int]) -> dict:
    """Check naming consistency of symbols in changed files.

    Compares new/changed symbol names against the codebase's dominant
    naming convention per kind-group (functions, classes, variables, etc.).
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get the dominant style per kind-group from ALL symbols
    all_symbols = conn.execute("""
        SELECT s.name, s.kind
        FROM symbols s
        WHERE s.kind IN ('function', 'method', 'class', 'interface',
                         'struct', 'trait', 'enum', 'variable',
                         'constant', 'property', 'field', 'type_alias')
    """).fetchall()

    group_cases: dict[str, Counter] = defaultdict(Counter)
    for sym in all_symbols:
        group = _group_for_kind(sym["kind"])
        style = classify_case(sym["name"])
        if style:
            group_cases[group][style] += 1

    # Dominant style per group with percentage
    dominant: dict[str, tuple[str, float]] = {}
    for group, counter in group_cases.items():
        total = sum(counter.values())
        if total > 0:
            best_style, best_count = counter.most_common(1)[0]
            dominant[group] = (best_style, round(100 * best_count / total, 1))

    # 2. Check symbols in changed files
    changed_symbols = batched_in(
        conn,
        """SELECT s.name, s.kind, s.line_start, f.path as file_path
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method', 'class', 'interface',
                            'struct', 'trait', 'enum', 'variable',
                            'constant', 'property', 'field', 'type_alias')""",
        file_ids,
    )

    violations = []
    checked = 0
    for sym in changed_symbols:
        name = sym["name"]
        if len(name) < _MIN_NAME_LEN or name in _SKIP_NAMES:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue

        group = _group_for_kind(sym["kind"])
        style = classify_case(name)
        if not style:
            continue

        checked += 1
        if group in dominant:
            expected_style, pct = dominant[group]
            if style != expected_style and pct >= 60:
                violations.append({
                    "category": "naming",
                    "severity": SEVERITY_WARN if pct < 90 else SEVERITY_FAIL,
                    "file": sym["file_path"],
                    "line": sym["line_start"],
                    "message": (
                        f"fn `{name}` uses {style} "
                        f"(codebase: {expected_style} {pct}%)"
                        if group == "functions"
                        else f"{group[:-1]} `{name}` uses {style} "
                             f"(codebase: {expected_style} {pct}%)"
                    ),
                    "symbol": name,
                    "actual_style": style,
                    "expected_style": expected_style,
                    "codebase_pct": pct,
                    "fix": f"Rename `{name}` to match {expected_style} convention",
                })

    # Score: fraction of checked symbols that are consistent
    if checked == 0:
        score = 100
    else:
        score = round(100 * (checked - len(violations)) / checked)
        score = max(0, min(100, score))

    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Import pattern consistency check
# ---------------------------------------------------------------------------

def _check_imports(conn, file_ids: list[int]) -> dict:
    """Check import patterns in changed files against codebase norms.

    Detects whether changed files follow the project's dominant import style
    (absolute vs relative) based on file_edges data.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Determine the codebase import style from ALL file_edges
    all_edges = conn.execute("""
        SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()

    if not all_edges:
        return {"score": 100, "violations": []}

    # Classify each import edge
    absolute_count = 0
    relative_count = 0
    for edge in all_edges:
        src_dir = edge["source_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in edge["source_path"].replace("\\", "/") else ""
        tgt_dir = edge["target_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in edge["target_path"].replace("\\", "/") else ""
        if src_dir and tgt_dir and (
            src_dir == tgt_dir
            or src_dir.startswith(tgt_dir + "/")
            or tgt_dir.startswith(src_dir + "/")
        ):
            relative_count += 1
        else:
            absolute_count += 1

    total_imports = absolute_count + relative_count
    if total_imports == 0:
        return {"score": 100, "violations": []}

    abs_pct = round(100 * absolute_count / total_imports, 1)
    dominant_style = "absolute" if abs_pct >= 60 else "relative" if abs_pct <= 40 else "mixed"
    dominant_pct = abs_pct if dominant_style == "absolute" else round(100 - abs_pct, 1) if dominant_style == "relative" else 50.0

    if dominant_style == "mixed":
        return {"score": 100, "violations": []}

    # 2. Check changed files' import edges
    changed_edges = batched_in(
        conn,
        """SELECT fe.source_file_id, sf.path as source_path, tf.path as target_path
           FROM file_edges fe
           JOIN files sf ON fe.source_file_id = sf.id
           JOIN files tf ON fe.target_file_id = tf.id
           WHERE fe.kind = 'imports' AND fe.source_file_id IN ({ph})""",
        file_ids,
    )

    violations = []
    checked = 0
    for edge in changed_edges:
        checked += 1
        src_dir = edge["source_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in edge["source_path"].replace("\\", "/") else ""
        tgt_dir = edge["target_path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in edge["target_path"].replace("\\", "/") else ""

        is_same_dir = src_dir and tgt_dir and (
            src_dir == tgt_dir
            or src_dir.startswith(tgt_dir + "/")
            or tgt_dir.startswith(src_dir + "/")
        )

        # If dominant is absolute but this is same-directory (relative-style)
        if dominant_style == "absolute" and is_same_dir:
            pass  # same-dir imports are fine even in absolute codebases
        elif dominant_style == "relative" and not is_same_dir:
            violations.append({
                "category": "imports",
                "severity": SEVERITY_WARN,
                "file": edge["source_path"],
                "line": None,
                "message": (
                    f"cross-directory import from `{edge['source_path']}` "
                    f"to `{edge['target_path']}` "
                    f"(codebase prefers same-directory imports {dominant_pct}%)"
                ),
                "fix": "Consider restructuring to keep imports within the same package",
            })

    if checked == 0:
        score = 100
    else:
        score = round(100 * (checked - len(violations)) / checked)
        score = max(0, min(100, score))

    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Error handling consistency check
# ---------------------------------------------------------------------------

_BARE_EXCEPT_RE = re.compile(r'^\s*except\s*:', re.MULTILINE)
_BROAD_EXCEPT_RE = re.compile(r'^\s*except\s+Exception\s*:', re.MULTILINE)
_SILENT_EXCEPT_RE = re.compile(
    r'except[^:]*:\s*\n(\s*)(pass|\.\.\.)\s*$', re.MULTILINE
)
_SPECIFIC_EXCEPT_RE = re.compile(
    r'^\s*except\s+(?!Exception\b)\w+', re.MULTILINE
)

_ERROR_NAME_RE = re.compile(
    r'(Error|Exception|Err|Fault|Failure|Panic)$', re.IGNORECASE
)


def _check_error_handling(conn, file_ids: list[int], root: Path) -> dict:
    """Check error handling patterns in changed files.

    Looks for:
    - Bare except: clauses
    - Broad Exception catches
    - Silent exception swallowing (except: pass)

    Compares against the codebase's use of custom error classes.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Detect codebase error patterns: how many specific exception classes exist?
    error_candidates = conn.execute("""
        SELECT s.name, s.kind
        FROM symbols s
        WHERE (s.name LIKE '%Error%'
            OR s.name LIKE '%Exception%'
            OR s.name LIKE '%Failure%')
          AND s.kind IN ('class', 'struct', 'interface')
    """).fetchall()

    custom_error_count = sum(
        1 for r in error_candidates
        if _ERROR_NAME_RE.search(r["name"])
    )
    has_custom_errors = custom_error_count > 0

    # 2. Read changed files and check for bad patterns
    changed_files = batched_in(
        conn,
        "SELECT id, path FROM files WHERE id IN ({ph})",
        file_ids,
    )

    violations = []
    files_checked = 0
    issues_found = 0

    for frow in changed_files:
        fpath = root / frow["path"]
        if not fpath.exists():
            continue
        # Only check Python files for error handling (other languages have
        # different patterns)
        if not frow["path"].endswith(".py"):
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        files_checked += 1
        lines = content.splitlines()

        # Bare except
        for m in _BARE_EXCEPT_RE.finditer(content):
            line_num = content[:m.start()].count('\n') + 1
            issues_found += 1
            violations.append({
                "category": "error_handling",
                "severity": SEVERITY_FAIL,
                "file": frow["path"],
                "line": line_num,
                "message": (
                    "bare `except:` "
                    + (f"(codebase has {custom_error_count} custom exception classes)"
                       if has_custom_errors
                       else "(use specific exceptions)")
                ),
                "fix": "Replace bare `except:` with a specific exception type",
            })

        # Broad Exception catch
        for m in _BROAD_EXCEPT_RE.finditer(content):
            line_num = content[:m.start()].count('\n') + 1
            issues_found += 1
            violations.append({
                "category": "error_handling",
                "severity": SEVERITY_WARN,
                "file": frow["path"],
                "line": line_num,
                "message": (
                    "broad `except Exception:` "
                    + (f"(codebase has {custom_error_count} specific exception classes)"
                       if has_custom_errors
                       else "(consider catching specific exceptions)")
                ),
                "fix": "Narrow the exception type to catch only expected errors",
            })

        # Silent exception swallowing
        for m in _SILENT_EXCEPT_RE.finditer(content):
            line_num = content[:m.start()].count('\n') + 1
            issues_found += 1
            violations.append({
                "category": "error_handling",
                "severity": SEVERITY_WARN,
                "file": frow["path"],
                "line": line_num,
                "message": "silent exception swallow (no logging/re-raise)",
                "fix": "Add logging or re-raise the exception instead of silently swallowing",
            })

    # Score: based on ratio of issues to files checked
    if files_checked == 0:
        score = 100
    elif issues_found == 0:
        score = 100
    else:
        # Each issue deducts points; more issues = lower score
        penalty = min(issues_found * 15, 100)
        score = max(0, 100 - penalty)

    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Duplicate logic detection
# ---------------------------------------------------------------------------

def _check_duplicates(conn, file_ids: list[int]) -> dict:
    """Detect potential duplicate functions by comparing new symbols to existing ones.

    Uses name similarity (SequenceMatcher) and signature comparison to find
    symbols in changed files that may duplicate existing functionality.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    # 1. Get symbols from changed files
    new_symbols = batched_in(
        conn,
        """SELECT s.id, s.name, s.kind, s.signature, s.line_start,
                  f.path as file_path
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           WHERE s.file_id IN ({ph})
             AND s.kind IN ('function', 'method')""",
        file_ids,
    )

    if not new_symbols:
        return {"score": 100, "violations": []}

    # 2. Get all other functions/methods NOT in changed files
    existing_symbols = conn.execute("""
        SELECT s.id, s.name, s.kind, s.signature, s.line_start,
               f.path as file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method')
    """).fetchall()

    # Build lookup by name for fast filtering
    existing_by_name: dict[str, list] = defaultdict(list)
    for sym in existing_symbols:
        existing_by_name[sym["name"].lower()].append(sym)

    violations = []
    checked = 0

    new_ids = {s["id"] for s in new_symbols}

    for new_sym in new_symbols:
        name = new_sym["name"]
        if len(name) < 4:
            continue
        if name.startswith("_"):
            continue
        checked += 1

        # Check for exact name matches in different files
        lower_name = name.lower()
        for existing in existing_by_name.get(lower_name, []):
            if existing["id"] in new_ids:
                continue
            if existing["file_path"] == new_sym["file_path"]:
                continue
            violations.append({
                "category": "duplicates",
                "severity": SEVERITY_WARN,
                "file": new_sym["file_path"],
                "line": new_sym["line_start"],
                "message": (
                    f"fn `{name}` has same name as "
                    f"`{existing['name']}` at {loc(existing['file_path'], existing['line_start'])}"
                ),
                "fix": f"Consider reusing `{existing['name']}` from {existing['file_path']}",
            })
            break  # one match per new symbol is enough

        # Check for similar names (ratio > 0.8) in existing symbols
        if not any(v["symbol"] == name if "symbol" in v else False for v in violations):
            name_lower = name.lower()
            # Only check a subset to avoid O(n^2) explosion
            candidates = []
            for existing_name, existing_list in existing_by_name.items():
                if abs(len(existing_name) - len(name_lower)) > 5:
                    continue
                ratio = SequenceMatcher(None, name_lower, existing_name).ratio()
                if ratio >= 0.8 and ratio < 1.0:
                    for ex in existing_list:
                        if ex["id"] not in new_ids and ex["file_path"] != new_sym["file_path"]:
                            candidates.append((ex, ratio))
                            break

            if candidates:
                best = max(candidates, key=lambda x: x[1])
                existing, ratio = best
                violations.append({
                    "category": "duplicates",
                    "severity": SEVERITY_INFO,
                    "file": new_sym["file_path"],
                    "line": new_sym["line_start"],
                    "message": (
                        f"fn `{name}` is similar to "
                        f"`{existing['name']}` at "
                        f"{loc(existing['file_path'], existing['line_start'])} "
                        f"({round(ratio * 100)}% match)"
                    ),
                    "fix": f"Check if `{existing['name']}` in {existing['file_path']} provides the same functionality",
                })

    if checked == 0:
        score = 100
    else:
        # Each duplicate deducts points
        fail_count = sum(1 for v in violations if v["severity"] == SEVERITY_FAIL)
        warn_count = sum(1 for v in violations if v["severity"] == SEVERITY_WARN)
        info_count = sum(1 for v in violations if v["severity"] == SEVERITY_INFO)
        penalty = fail_count * 20 + warn_count * 10 + info_count * 5
        score = max(0, 100 - penalty)

    return {"score": score, "violations": violations}


# ---------------------------------------------------------------------------
# Syntax integrity check
# ---------------------------------------------------------------------------

def _check_syntax(conn, file_ids: list[int], root: Path) -> dict:
    """Check for syntax errors via tree-sitter ERROR nodes.

    Uses tree-sitter to parse changed files and reports any ERROR nodes found.
    """
    if not file_ids:
        return {"score": 100, "violations": []}

    changed_files = batched_in(
        conn,
        "SELECT id, path, language FROM files WHERE id IN ({ph})",
        file_ids,
    )

    violations = []
    files_checked = 0
    files_with_errors = 0

    try:
        from roam.index.parser import parse_file
    except ImportError:
        # If tree-sitter is not available, skip syntax check
        return {"score": 100, "violations": []}

    for frow in changed_files:
        fpath = root / frow["path"]
        if not fpath.exists():
            continue

        lang = frow["language"]
        if not lang:
            continue

        files_checked += 1

        try:
            result = parse_file(fpath, lang)
        except Exception:
            continue

        # parse_file returns (tree, source_bytes, language) or (None, None, None)
        if result is None or result[0] is None:
            continue
        tree = result[0]

        error_nodes = _find_error_nodes(tree.root_node)
        if error_nodes:
            files_with_errors += 1
            for node in error_nodes[:5]:  # Cap per-file error reports
                line_num = node.start_point[0] + 1
                violations.append({
                    "category": "syntax",
                    "severity": SEVERITY_FAIL,
                    "file": frow["path"],
                    "line": line_num,
                    "message": f"syntax error at line {line_num}",
                    "fix": "Fix the syntax error indicated by the parser",
                })

    if files_checked == 0:
        score = 100
    elif files_with_errors == 0:
        score = 100
    else:
        score = round(100 * (files_checked - files_with_errors) / files_checked)
        score = max(0, min(100, score))

    return {"score": score, "violations": violations}


def _find_error_nodes(node) -> list:
    """Recursively find ERROR nodes in a tree-sitter AST."""
    errors = []
    if node.type == "ERROR":
        errors.append(node)
    for child in node.children:
        errors.extend(_find_error_nodes(child))
    return errors


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_verdict(score: int) -> str:
    """Compute verdict string from composite score."""
    if score >= 80:
        return "PASS"
    if score >= 60:
        return "WARN"
    return "FAIL"


def _compute_composite(categories: dict[str, dict]) -> int:
    """Compute weighted composite score from category results."""
    total = 0.0
    for cat_name, weight in _CATEGORY_WEIGHTS.items():
        cat_score = categories.get(cat_name, {}).get("score", 100)
        total += weight * cat_score
    return round(total)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@click.command()
@click.option('--changed', is_flag=True, default=False,
              help='Use git diff to get changed files (default if no files given)')
@click.option('--threshold', type=int, default=70,
              help='Fail if score drops below this (default 70)')
@click.option('--fix-suggestions', is_flag=True, default=False,
              help='Show concrete fix suggestions for each violation')
@click.argument('files', nargs=-1, type=click.Path())
@click.pass_context
def verify(ctx, changed, threshold, fix_suggestions, files):
    """Verify changed files follow codebase conventions.

    Checks naming, import patterns, error handling, duplicate logic,
    and syntax integrity against established codebase patterns.

    If no files are specified, defaults to git-changed files.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    root = find_project_root()

    # Resolve target files
    if files:
        target_paths = [f.replace("\\", "/") for f in files]
    elif changed or True:
        # Default behavior: use git diff changed files
        target_paths = get_changed_files(root)

    if not target_paths:
        # No changed files
        score = 100
        verdict = "PASS"
        if json_mode:
            click.echo(to_json(json_envelope("verify",
                summary={
                    "verdict": verdict,
                    "score": score,
                    "threshold": threshold,
                    "files_checked": 0,
                    "violation_count": 0,
                },
                categories={cat: {"score": 100, "violations": []}
                            for cat in _CATEGORY_WEIGHTS},
                violations=[],
            )))
            return
        click.echo(f"VERDICT: {verdict} (score {score}/100) -- no changed files")
        return

    with open_db(readonly=True) as conn:
        # Map paths to file IDs
        file_map = resolve_changed_to_db(conn, target_paths)
        file_ids = list(file_map.values())

        # Run all checks
        naming_result = _check_naming(conn, file_ids)
        imports_result = _check_imports(conn, file_ids)
        error_result = _check_error_handling(conn, file_ids, root)
        duplicates_result = _check_duplicates(conn, file_ids)
        syntax_result = _check_syntax(conn, file_ids, root)

        categories = {
            "naming": naming_result,
            "imports": imports_result,
            "error_handling": error_result,
            "duplicates": duplicates_result,
            "syntax": syntax_result,
        }

        # Composite score
        score = _compute_composite(categories)
        verdict = _compute_verdict(score)

        # Flatten all violations
        all_violations = []
        for cat_result in categories.values():
            all_violations.extend(cat_result.get("violations", []))

        violation_count = len(all_violations)
        files_checked = len(file_map)

        # JSON output
        if json_mode:
            # Build category summary for JSON
            cat_summary = {}
            for cat_name, cat_result in categories.items():
                cat_summary[cat_name] = {
                    "score": cat_result["score"],
                    "violation_count": len(cat_result.get("violations", [])),
                    "violations": cat_result.get("violations", []),
                }

            click.echo(to_json(json_envelope("verify",
                summary={
                    "verdict": verdict,
                    "score": score,
                    "threshold": threshold,
                    "files_checked": files_checked,
                    "violation_count": violation_count,
                },
                categories=cat_summary,
                violations=all_violations,
            )))

            if score < threshold:
                ctx.exit(EXIT_GATE_FAILURE)
            return

        # Text output
        click.echo(
            f"VERDICT: {verdict} (score {score}/100) "
            f"-- {violation_count} issue{'s' if violation_count != 1 else ''} "
            f"in {files_checked} changed file{'s' if files_checked != 1 else ''}"
        )
        click.echo("")

        # Naming
        _print_category("NAMING", naming_result, fix_suggestions)

        # Imports
        _print_category("IMPORTS", imports_result, fix_suggestions)

        # Error handling
        _print_category("ERROR HANDLING", error_result, fix_suggestions)

        # Duplicates
        _print_category("DUPLICATES", duplicates_result, fix_suggestions)

        # Syntax
        _print_category("SYNTAX", syntax_result, fix_suggestions)

        # Summary line
        gate_result = "PASS" if score >= threshold else "FAIL"
        click.echo(
            f"\nOverall: {score}/100 (threshold: {threshold}) -- {gate_result}"
        )

        if score < threshold:
            ctx.exit(EXIT_GATE_FAILURE)


def _print_category(label: str, result: dict, fix_suggestions: bool):
    """Print a single category's results in text format."""
    score = result["score"]
    violations = result.get("violations", [])

    click.echo(f"{label} ({score}/100):")
    if not violations:
        click.echo("  OK -- all checks passed")
    else:
        for v in violations:
            sev = v.get("severity", "WARN")
            file_loc = loc(v["file"], v.get("line"))
            click.echo(f"  {sev}: {file_loc} -- {v['message']}")
            if fix_suggestions and v.get("fix"):
                click.echo(f"    FIX: {v['fix']}")
    click.echo("")
