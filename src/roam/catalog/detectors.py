"""Algorithm detection: query DB signals to find suboptimal patterns.

Each detector has signature ``(conn) -> list[dict]`` and returns findings
with fields: task_id, detected_way, suggested_way, symbol_id, symbol_name,
kind, location, confidence, reason.

Design principles (informed by research):
- Structural AST patterns (loop depth, accumulator, subscript) are more
  reliable than function-name matching alone.
- Name matching is a confidence booster, not a gate.
- Idiom-only improvements (same Big-O) get ``low`` confidence.
- Genuine complexity-class improvements get ``medium`` or ``high``.
- Suppress known false-positive patterns (grid traversal, intentional polling).
"""

from __future__ import annotations

import json
import os
import re

from roam.catalog.tasks import best_way


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if "tests/" in p or "test/" in p or "__tests__/" in p or "spec/" in p:
        return True
    return False


def _loc(path: str, line) -> str:
    if line is not None:
        return f"{path}:{line}"
    return path


def _finding(
    task_id,
    detected_way,
    sym,
    reason,
    confidence="medium",
    *,
    evidence=None,
    fix=None,
):
    bw = best_way(task_id)
    finding = {
        "task_id": task_id,
        "detected_way": detected_way,
        "suggested_way": bw["id"] if bw else "",
        "symbol_id": sym["id"],
        "symbol_name": sym["qualified_name"] or sym["name"],
        "kind": sym["kind"],
        "location": _loc(sym["file_path"], sym["line_start"]),
        "confidence": confidence,
        "reason": reason,
    }
    if evidence:
        finding["evidence"] = evidence
    if fix:
        finding["fix"] = fix
    return finding


def _row_value(row, key, default=None):
    """Safely read a sqlite row key with a fallback."""
    try:
        return row[key]
    except Exception:
        return default


def _json_list(value) -> list[str]:
    """Parse a JSON-encoded list of strings; return [] on malformed input."""
    if not value:
        return []
    try:
        data = json.loads(value)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str):
            out.append(item)
    return out


def _call_leaf(name: str) -> str:
    """Return the final method/function token from a qualified call name."""
    if not name:
        return ""
    return name.rsplit(".", 1)[-1]


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _read_symbol_source(path: str, line_start: int | None, line_end: int | None) -> str:
    """Best-effort source slice for a symbol location."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    if line_start is None or line_end is None:
        return "\n".join(lines)
    ls = max(1, int(line_start))
    le = max(ls, int(line_end))
    return "\n".join(lines[ls - 1:le])


def _iter_loop_calls(row) -> list[str]:
    """Return combined loop call names (leaf + qualified)."""
    calls = _json_list(_row_value(row, "calls_in_loops", ""))
    qcalls = _json_list(_row_value(row, "calls_in_loops_qualified", ""))
    return _dedupe(calls + qcalls)


def _call_in(calls: list[str], targets: set[str]) -> list[str]:
    """Match calls against targets by exact qualified name or leaf token."""
    hits: list[str] = []
    for c in calls:
        if c in targets or _call_leaf(c) in targets:
            hits.append(c)
    return _dedupe(hits)


_FRAMEWORK_IO_PACKS = {
    "python": [
        {
            "framework": "django-orm",
            "receiver_hints": {"objects", "queryset"},
            "leaves": {"get", "filter", "exclude", "all", "count", "exists"},
            "confidence": "high",
            "fix": (
                "Batch ORM fetches with `id__in` and add "
                "`select_related()/prefetch_related()`."
            ),
        },
        {
            "framework": "sqlalchemy",
            "receiver_hints": {"session"},
            "leaves": {"execute", "query", "scalars", "get"},
            "confidence": "high",
            "fix": (
                "Use one `IN` query and eager loading "
                "(`selectinload`/`joinedload`) before the loop."
            ),
        },
    ],
    "ruby": [
        {
            "framework": "rails-active-record",
            "receiver_hints": {"activerecord", "relation", "model"},
            "leaves": {"find", "find_by", "where", "pluck"},
            "confidence": "high",
            "fix": "Preload associations with `.includes`/`.preload` and fetch in bulk.",
        },
    ],
    "php": [
        {
            "framework": "laravel-eloquent",
            "receiver_hints": {"db", "model", "eloquent", "query"},
            "leaves": {"find", "where", "first", "get", "value"},
            "confidence": "high",
            "fix": "Use `Model::whereIn(...)` or eager load with `with(...)`.",
        },
    ],
    "javascript": [
        {
            "framework": "node-orm",
            "receiver_hints": {"prisma", "sequelize", "knex", "db"},
            "leaves": {"findmany", "findunique", "findone", "query"},
            "confidence": "high",
            "fix": "Replace per-item ORM calls with one batched query.",
        },
        {
            "framework": "http-client",
            "receiver_hints": {"axios", "fetch", "http", "client"},
            "leaves": {"get", "post", "request"},
            "confidence": "medium",
            "fix": "Use a bulk endpoint or bounded parallel batch requests.",
        },
    ],
    "typescript": [
        {
            "framework": "node-orm",
            "receiver_hints": {"prisma", "sequelize", "knex", "db"},
            "leaves": {"findmany", "findunique", "findone", "query"},
            "confidence": "high",
            "fix": "Replace per-item ORM calls with one batched query.",
        },
        {
            "framework": "http-client",
            "receiver_hints": {"axios", "fetch", "http", "client"},
            "leaves": {"get", "post", "request"},
            "confidence": "medium",
            "fix": "Use a bulk endpoint or bounded parallel batch requests.",
        },
    ],
    "java": [
        {
            "framework": "jpa-hibernate",
            "receiver_hints": {"repository", "entitymanager", "jdbc", "template"},
            "leaves": {"findbyid", "findall", "query", "execute", "select"},
            "confidence": "high",
            "fix": (
                "Preload rows with one repository/JPA query "
                "(`IN (...)` + fetch join)."
            ),
        },
    ],
}


def _framework_packs(language: str | None) -> list[dict]:
    if not language:
        return []
    return _FRAMEWORK_IO_PACKS.get(language.lower(), [])


_IO_GUARD_HINTS = {
    "python": {
        "select_related(",
        "prefetch_related(",
        "joinedload(",
        "selectinload(",
        "in_(",
        "__in",
        "executemany(",
    },
    "ruby": {
        ".includes(",
        ".preload(",
        ".eager_load(",
    },
    "php": {
        "->with(",
        "wherein(",
        "loadmissing(",
    },
    "javascript": {
        "findmany(",
        "include:",
        " in: ",
    },
    "typescript": {
        "findmany(",
        "include:",
        " in: ",
    },
    "java": {
        "join fetch",
        "findallbyid(",
        " in (",
    },
}


def _guard_hints_from_source(language: str | None, snippet: str) -> list[str]:
    if not language or not snippet:
        return []
    tokens = _IO_GUARD_HINTS.get((language or "").lower(), set())
    if not tokens:
        return []
    lower = snippet.lower()
    hits: list[str] = []
    for tok in tokens:
        if tok in lower:
            hits.append(tok)
    return _dedupe(hits)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def detect_manual_sort(conn):
    """Symbols named *sort* with nested loops, comparisons, and subscript
    access (swap pattern).  No call to built-in sort."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.calls_in_loops, "
        "ms.loop_with_compare, ms.subscript_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%sort%' OR s.name LIKE '%Sort%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"sort", "sorted", "Arrays.sort", "Collections.sort",
                            "qsort", "std::sort"}):
            continue
        # Subscript access in loops strengthens the signal (swap pattern)
        conf = "high" if r["subscript_in_loops"] else "medium"
        results.append(_finding(
            "sorting", "manual-sort", r,
            "Nested loops with comparisons in sort-named function",
            conf,
        ))
    return results


def detect_linear_search(conn):
    """Functions explicitly named *sorted*/*search_sorted* that loop with
    comparisons but don't call bisect/binarySearch.

    Downgraded to low confidence because we cannot verify from AST alone
    that the data being searched is actually sorted.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_compare, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%search_sorted%' OR s.name LIKE '%searchSorted%' "
        "  OR s.name LIKE '%find_sorted%' OR s.name LIKE '%findSorted%' "
        "  OR s.name LIKE '%find_in_sorted%' OR s.name LIKE '%in_sorted%' "
        "  OR s.name LIKE '%linear_search%' OR s.name LIKE '%linearSearch%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"bisect", "bisect_left", "bisect_right",
                            "binarySearch", "binary_search", "lower_bound",
                            "upper_bound"}):
            continue
        results.append(_finding(
            "search-sorted", "linear-scan", r,
            "Linear scan in function that implies sorted data",
            "low",
        ))
    return results


def detect_list_membership(conn):
    """Nested loops with equality comparisons — structural pattern for
    O(n^2) membership testing regardless of function name."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_with_compare, ms.subscript_in_loops, "
        "ms.has_nested_loops, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1 "
        "AND ms.subscript_in_loops = 1 "
        "AND (s.name LIKE '%contain%' OR s.name LIKE '%member%' "
        "  OR s.name LIKE '%exist%' OR s.name LIKE '%has_%' "
        "  OR s.name LIKE '%in_%' OR s.name LIKE '%check%' "
        "  OR s.name LIKE '%includes%' OR s.name LIKE '%Includes%')"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        results.append(_finding(
            "membership", "list-scan", r,
            "Nested loops with comparisons for membership check",
            "medium",
        ))
    return results


def detect_string_concat_loop(conn):
    """Loops with accumulation patterns and string-related call hints.

    Relies primarily on the structural pattern (loop + accumulator) combined
    with calls to string methods (append/concat) or string-building name hints.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops, ms.loop_with_accumulator "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_accumulator = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        # Structural signal: calls to string concat/append methods
        has_concat_call = bool(_call_in(calls, {"concat", "strcat", "append", "push"}))
        # Name signal: function name suggests string building
        name_lower = (r["name"] or "").lower()
        has_name_hint = any(kw in name_lower for kw in
                           ("concat", "build_str", "build_string",
                            "format", "render", "serialize",
                            "to_string", "tostring", "stringify",
                            "to_csv", "to_html", "to_xml",
                            "generate_report", "join"))
        if has_concat_call or has_name_hint:
            results.append(_finding(
                "string-concat", "loop-concat", r,
                "Loop accumulation in string-building function",
                "medium",
            ))
    return results


def detect_manual_dedup(conn):
    """Nested loops in dedup/unique-named functions without set usage."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.loop_with_compare, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%dedup%' OR s.name LIKE '%unique%' "
        "  OR s.name LIKE '%Dedup%' OR s.name LIKE '%Unique%' "
        "  OR s.name LIKE '%distinct%' OR s.name LIKE '%Distinct%' "
        "  OR s.name LIKE '%remove_dup%' OR s.name LIKE '%removeDup%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Negative check: skip if they already use set/hash
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"set", "Set", "HashSet"}):
            continue
        results.append(_finding(
            "unique", "nested-dedup", r,
            "Nested loops with comparisons in dedup function",
            "high",
        ))
    return results


def detect_manual_maxmin(conn):
    """Loops with comparisons in max/min-named functions.

    Same Big-O (both O(n)) — this is an idiom improvement, flagged at low
    confidence.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_compare, "
        "ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%find_max%' OR s.name LIKE '%find_min%' "
        "  OR s.name LIKE '%findMax%' OR s.name LIKE '%findMin%' "
        "  OR s.name LIKE '%get_max%' OR s.name LIKE '%get_min%' "
        "  OR s.name LIKE '%getMax%' OR s.name LIKE '%getMin%' "
        "  OR s.name LIKE '%find_largest%' OR s.name LIKE '%find_smallest%' "
        "  OR s.name LIKE '%findLargest%' OR s.name LIKE '%findSmallest%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_compare = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"max", "min", "Math.max", "Math.min",
                            "Collections.max", "Collections.min"}):
            continue
        results.append(_finding(
            "max-min", "manual-loop", r,
            "Manual loop with comparisons in max/min function (idiomatic improvement)",
            "low",
        ))
    return results


def detect_manual_accumulation(conn):
    """Loops with accumulator in sum/total-named functions.

    Same Big-O (both O(n)) — idiom improvement, flagged at low confidence.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%_sum%' OR s.name LIKE '%_total%' "
        "  OR s.name LIKE '%Sum%' OR s.name LIKE '%Total%' "
        "  OR s.name LIKE '%accumulate%' OR s.name LIKE '%Accumulate%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1 "
        "AND ms.loop_with_accumulator = 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"sum", "reduce", "aggregate", "prod"}):
            continue
        results.append(_finding(
            "accumulation", "manual-sum", r,
            "Loop with accumulator in sum/total function (idiomatic improvement)",
            "low",
        ))
    return results


def detect_manual_power(conn):
    """Loop-based exponentiation in power/exponent-named functions."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_multiplication, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%pow%' OR s.name LIKE '%Pow%' "
            "  OR s.name LIKE '%power%' OR s.name LIKE '%Power%' "
            "  OR s.name LIKE '%exp%' OR s.name LIKE '%Exponent%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1 "
            "AND ms.loop_with_multiplication = 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, 0 as loop_with_multiplication, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%pow%' OR s.name LIKE '%Pow%' "
            "  OR s.name LIKE '%power%' OR s.name LIKE '%Power%' "
            "  OR s.name LIKE '%exp%' OR s.name LIKE '%Exponent%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1 "
            "AND ms.loop_with_accumulator = 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"pow", "Math.pow", "std::pow", "math.pow", "BigInteger.modPow"}):
            continue
        conf = "high" if _row_value(r, "loop_with_multiplication", 0) else "medium"
        results.append(_finding(
            "manual-power", "loop-multiply", r,
            "Loop multiplication used for exponentiation",
            conf,
        ))
    return results


def detect_manual_gcd(conn):
    """Manual GCD loops where built-in/standard helpers are available."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_modulo, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%gcd%' OR s.name LIKE '%GCD%' "
            "  OR s.name LIKE '%hcf%' OR s.name LIKE '%gcf%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, 0 as loop_with_modulo, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%gcd%' OR s.name LIKE '%GCD%' "
            "  OR s.name LIKE '%hcf%' OR s.name LIKE '%gcf%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"gcd", "math.gcd", "std::gcd", "BigInteger.gcd"}):
            continue
        conf = "medium"
        if _row_value(r, "loop_with_modulo", 0):
            conf = "high"
        results.append(_finding(
            "manual-gcd", "manual-gcd", r,
            "Manual GCD loop can be replaced with standard gcd helper",
            conf,
        ))
    return results


def detect_string_reverse(conn):
    """Manual reversal loops in reverse-named functions."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_accumulator, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%reverse%' OR s.name LIKE '%Reverse%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.loop_with_accumulator, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%reverse%' OR s.name LIKE '%Reverse%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"reverse", "reversed", "std::reverse", "StringBuilder.reverse", "strrev"}):
            continue
        results.append(_finding(
            "string-reverse", "manual-reverse", r,
            "Manual character-loop reversal in reverse-named function",
            "low",
        ))
    return results


def detect_matrix_mult(conn):
    """Naive triple-loop matrix multiplication patterns."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.subscript_in_loops, "
            "ms.loop_with_multiplication, ms.loop_with_accumulator, "
            "ms.calls_in_loops, ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%matrix%' OR s.name LIKE '%matmul%' "
            "  OR s.name LIKE '%multiply_matrix%' OR s.name LIKE '%dot%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 3 "
            "AND ms.subscript_in_loops = 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_depth, ms.subscript_in_loops, "
            "0 as loop_with_multiplication, ms.loop_with_accumulator, "
            "ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE (s.name LIKE '%matrix%' OR s.name LIKE '%matmul%' "
            "  OR s.name LIKE '%multiply_matrix%' OR s.name LIKE '%dot%') "
            "AND s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 3 "
            "AND ms.subscript_in_loops = 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"dot", "matmul", "gemm", "dgemm", "numpy.dot", "np.matmul"}):
            continue
        conf = "high" if (_row_value(r, "loop_with_multiplication", 0)
                          and _row_value(r, "loop_with_accumulator", 0)) else "medium"
        results.append(_finding(
            "matrix-mult", "naive-triple", r,
            "Naive matrix multiplication via nested loops",
            conf,
        ))
    return results


def detect_naive_fibonacci(conn):
    """Recursive functions named *fib* without memoization.

    O(2^n) -> O(n) — one of the strongest algorithmic improvements.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_self_call "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%fib%' OR s.name LIKE '%Fib%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.has_self_call >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Check if there's a memoization decorator (edge to lru_cache/cache)
        memo_edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.name IN ('lru_cache', 'cache', 'memoize', 'memo', "
            "               'functools.lru_cache', 'functools.cache') "
            "LIMIT 1",
            (r["id"],),
        ).fetchone()
        if memo_edge:
            continue
        results.append(_finding(
            "fibonacci", "naive-recursive", r,
            "Recursive fibonacci without memoization (exponential blowup)",
            "high",
        ))
    return results


def detect_nested_lookup(conn):
    """Nested loops with subscript access and comparisons — O(n*m) lookup.

    Suppresses known false positives: grid/matrix traversal (name hints),
    and requires both nested loops AND comparisons (not just nested loops
    with subscript access, which could be matrix operations).
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.has_nested_loops, ms.subscript_in_loops, "
        "ms.loop_with_compare "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "JOIN symbol_metrics sm ON sm.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.has_nested_loops = 1 "
        "AND ms.subscript_in_loops = 1 "
        "AND ms.loop_with_compare = 1 "
        "AND sm.cognitive_complexity >= 8"
    ).fetchall()

    # Names that suggest grid/matrix traversal (suppress these)
    _GRID_NAMES = {"matrix", "grid", "board", "pixel", "cell",
                   "permut", "combin", "cartesian", "product",
                   "transpose", "rotate", "convolv"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Suppress grid/matrix traversal patterns
        name_lower = (r["name"] or "").lower()
        if any(kw in name_lower for kw in _GRID_NAMES):
            continue
        results.append(_finding(
            "nested-lookup", "nested-iteration", r,
            "Nested loops with subscript access and comparisons (potential O(n*m))",
            "medium",
        ))
    return results


def detect_manual_groupby(conn):
    """Loops in group/categorize-named functions without defaultdict/groupby.

    Same Big-O (both O(n)) — idiom improvement.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.loop_with_accumulator, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE (s.name LIKE '%group%' OR s.name LIKE '%Group%' "
        "  OR s.name LIKE '%bucket%' OR s.name LIKE '%Bucket%' "
        "  OR s.name LIKE '%partition%' OR s.name LIKE '%Partition%' "
        "  OR s.name LIKE '%categorize%' OR s.name LIKE '%Categorize%' "
        "  OR s.name LIKE '%classify%' OR s.name LIKE '%Classify%' "
        "  OR s.name LIKE '%bin_by%' OR s.name LIKE '%key_by%' "
        "  OR s.name LIKE '%index_by%') "
        "AND s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"groupby", "group_by", "defaultdict", "setdefault",
                            "groupingBy", "Collectors"}):
            continue
        results.append(_finding(
            "groupby", "manual-check", r,
            "Manual loop in group-by function (idiomatic improvement)",
            "low",
        ))
    return results


def detect_busy_wait(conn):
    """Loops that call sleep — polling / busy-wait pattern.

    Suppresses intentional polling: functions named *poll*, *retry*,
    *health_check*, *monitor*, *wait_for* — these are legitimate patterns.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    # Intentional polling patterns — suppress these
    _POLL_NAMES = {"poll", "retry", "health_check", "healthcheck",
                   "monitor", "wait_for", "wait_until", "watchdog",
                   "ping", "heartbeat", "keepalive", "backoff"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        if not _call_in(calls, {"sleep", "time.sleep", "Thread.sleep", "usleep",
                                "nanosleep", "Sleep"}):
            continue
        # Suppress intentional polling
        name_lower = (r["name"] or "").lower()
        if any(kw in name_lower for kw in _POLL_NAMES):
            continue
        results.append(_finding(
            "busy-wait", "sleep-loop", r,
            "sleep() called inside a loop (busy-wait pattern)",
            "high",
        ))
    return results


# ---------------------------------------------------------------------------
# New detectors: patterns identified by research
# ---------------------------------------------------------------------------

def detect_regex_in_loop(conn):
    """Regex compilation inside a loop — recompiles on every iteration.

    O(n*p) wasted compilation when O(p + n*m) is achievable by compiling
    once outside the loop.  Applies to all languages with regex engines.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    # Note: call target names are extracted as the last identifier in
    # member expressions (e.g. re.compile -> "compile", re.match -> "match")
    _REGEX_COMPILE_CALLS = {"compile", "Compile", "MustCompile"}
    _REGEX_CONVENIENCE_CALLS = {"match", "search", "findall", "sub",
                                "split", "fullmatch", "finditer",
                                "matches", "Replace", "ReplaceAll",
                                "Find", "FindAll", "MatchString"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = _iter_loop_calls(r)
        # Direct compile in loop is always bad
        compile_calls = _call_in(calls, _REGEX_COMPILE_CALLS)
        # Convenience regex calls in loop (re.match, re.search, etc.) also
        # compile each time in most runtimes — flag at medium confidence
        # since these could be on a pre-compiled pattern object
        convenience_calls = _call_in(calls, _REGEX_CONVENIENCE_CALLS)
        if compile_calls:
            results.append(_finding(
                "regex-in-loop", "compile-per-iter", r,
                f"Regex compile ({', '.join(compile_calls[:2])}) inside loop",
                "high",
            ))
        elif convenience_calls:
            results.append(_finding(
                "regex-in-loop", "compile-per-iter", r,
                f"Regex call ({', '.join(convenience_calls[:2])}) inside loop "
                "(may recompile per iteration)",
                "medium",
            ))
    return results


def detect_io_in_loop(conn):
    """Database query, HTTP request, or file I/O inside a loop — N+1 pattern.

    One of the most impactful performance anti-patterns in web applications.
    Each iteration incurs a full I/O round trip.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "f.language as language, s.line_start, s.line_end, ms.loop_depth, ms.calls_in_loops, "
            "ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "f.language as language, s.line_start, s.line_end, ms.loop_depth, ms.calls_in_loops, "
            "'' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    # High-confidence calls: strongly indicative DB/API round trips.
    _HIGH_EXACT = {
        "requests.get", "requests.post", "requests.put", "requests.delete",
        "requests.patch", "urllib.request.urlopen",
        "session.execute", "session.query", "cursor.execute",
        "http.Get", "http.Post",
    }
    _HIGH_EXACT_LOWER = {c.lower() for c in _HIGH_EXACT}
    _HIGH_LEAF = {"execute", "executemany", "query", "urlopen"}
    _AMBIGUOUS_BARE = {"query", "find", "get"}
    _MEDIUM_LEAF = {"fetchone", "fetchall", "fetchmany", "fetch", "find", "get", "open"}
    _IO_RECEIVER_HINTS = {
        "session", "cursor", "db", "conn", "connection", "repo",
        "repository", "queryset", "client", "api", "http", "requests",
        "urllib",
    }

    # Suppress functions that are intentionally batch/migration wrappers.
    _IO_WRAPPER_NAMES = {"batch", "bulk", "migrate", "seed", "import",
                         "export", "sync_all", "backfill"}

    def _receiver_hint(call: str) -> str:
        if "." not in call:
            return ""
        return call.rsplit(".", 1)[0].lower()

    def _receiver_is_ioish(call: str) -> bool:
        recv = _receiver_hint(call)
        if not recv:
            return False
        return any(h in recv for h in _IO_RECEIVER_HINTS)

    def _match_framework_pack(call: str, language: str | None) -> dict | None:
        leaf = _call_leaf(call).lower()
        recv = _receiver_hint(call)
        lower_c = call.lower()
        for pack in _framework_packs(language):
            leaves = pack.get("leaves", set())
            recv_hints = pack.get("receiver_hints", set())
            if lower_c in {c.lower() for c in pack.get("exact", set())}:
                return pack
            if leaf not in leaves:
                continue
            if not recv_hints:
                return pack
            if any(h in recv for h in recv_hints):
                return pack
        return None

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        language = _row_value(r, "language", "")
        snippet = _read_symbol_source(
            r["file_path"],
            _row_value(r, "line_start", None),
            _row_value(r, "line_end", None),
        )
        guard_hints = _guard_hints_from_source(language, snippet)
        calls = _iter_loop_calls(r)
        if not calls:
            continue

        high_calls: list[str] = []
        medium_calls: list[str] = []
        frameworks: set[str] = set()
        fixes: set[str] = set()
        for c in calls:
            pack = _match_framework_pack(c, language)
            if pack:
                frameworks.add(pack["framework"])
                if pack.get("fix"):
                    fixes.add(pack["fix"])
                if pack.get("confidence") == "high":
                    high_calls.append(c)
                else:
                    medium_calls.append(c)
                continue

            lower_c = c.lower()
            leaf = _call_leaf(c).lower()
            if lower_c in _HIGH_EXACT_LOWER:
                high_calls.append(c)
                continue
            if leaf in _HIGH_LEAF and _receiver_is_ioish(c):
                high_calls.append(c)
                continue
            # requests.<verb> style HTTP calls
            if leaf in {"get", "post", "put", "delete", "patch", "request"}:
                recv = _receiver_hint(c)
                if "requests" in recv or recv.endswith("http"):
                    high_calls.append(c)
                    continue
            if leaf in _MEDIUM_LEAF and _receiver_is_ioish(c):
                medium_calls.append(c)
                continue
            # Bare helper names without a receiver are ambiguous: if they
            # resolve to a local helper in the same file, treat as non-I/O.
            if "." not in c and leaf in _AMBIGUOUS_BARE:
                local_helper = conn.execute(
                    "SELECT 1 FROM edges e "
                    "JOIN symbols t ON e.target_id = t.id "
                    "WHERE e.source_id = ? "
                    "AND lower(t.name) = ? "
                    "AND t.file_id = ? "
                    "LIMIT 1",
                    (r["id"], leaf, r["file_id"]),
                ).fetchone()
                if local_helper:
                    continue
                medium_calls.append(c)
                continue
            if leaf == "open":
                medium_calls.append(c)
                continue

        if not high_calls and not medium_calls:
            continue

        name_lower = (r["name"] or "").lower()
        if any(kw in name_lower for kw in _IO_WRAPPER_NAMES):
            continue

        guard_applies = bool(frameworks and guard_hints)
        if high_calls:
            reason_calls = _dedupe(high_calls)[:2]
            reason_suffix = ""
            if frameworks:
                reason_suffix = f"; frameworks: {', '.join(sorted(frameworks))}"
            confidence = "high"
            if guard_applies:
                confidence = _lower_confidence(confidence)
                reason_suffix += f"; eager/batch guards: {', '.join(guard_hints[:2])}"
            results.append(_finding(
                "io-in-loop", "loop-query", r,
                f"I/O call ({', '.join(reason_calls)}) inside loop (N+1 pattern){reason_suffix}",
                confidence,
                evidence={
                    "io_calls": _dedupe(high_calls + medium_calls)[:6],
                    "frameworks": sorted(frameworks),
                    "guard_hints": guard_hints,
                },
                fix="; ".join(sorted(fixes)) if fixes else None,
            ))
        else:
            reason_calls = _dedupe(medium_calls)[:2]
            reason_suffix = ""
            if frameworks:
                reason_suffix = f"; frameworks: {', '.join(sorted(frameworks))}"
            confidence = "medium"
            if guard_applies:
                confidence = _lower_confidence(confidence)
                reason_suffix += f"; eager/batch guards: {', '.join(guard_hints[:2])}"
            results.append(_finding(
                "io-in-loop", "loop-query", r,
                f"I/O-like call ({', '.join(reason_calls)}) inside loop (may be N+1){reason_suffix}",
                confidence,
                evidence={
                    "io_calls": _dedupe(high_calls + medium_calls)[:6],
                    "frameworks": sorted(frameworks),
                    "guard_hints": guard_hints,
                },
                fix="; ".join(sorted(fixes)) if fixes else None,
            ))
    return results


def detect_list_prepend(conn):
    """insert(0, x), unshift(), or pop(0) inside a loop — O(n) per op
    due to array shifting, O(n^2) total."""
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.front_ops_in_loop "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.front_ops_in_loop = 1"
        ).fetchall()
    except Exception:
        # Fallback for older indexes without front_ops_in_loop.
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.calls_in_loops, '' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # New indexes precompute the exact front-op signal.
        if _row_value(r, "front_ops_in_loop", None) == 1:
            results.append(_finding(
                "list-prepend", "insert-front", r,
                "Front insert/remove inside loop (O(n) shift per operation)",
                "high",
            ))
            continue
        # Fallback heuristic (conservative): only explicit front APIs.
        calls = _iter_loop_calls(r)
        if _call_in(calls, {"insert", "unshift", "shift", "appendleft", "popleft"}):
            results.append(_finding(
                "list-prepend", "insert-front", r,
                "Potential front insert/remove inside loop",
                "medium",
            ))
    return results


def detect_sort_to_select(conn):
    """Calling sort on a collection and then only using the first/last element.

    O(n log n) sort when min()/max() is O(n), or heapq.nsmallest is O(n log k).
    Detection: function calls sort AND has subscript access, but does NOT
    iterate the full sorted result.
    """
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, s.line_end "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()

    sorted_index_re = re.compile(
        r"\bsorted\s*\([^)]*\)\s*\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]"
    )
    inplace_sort_index_re = re.compile(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*sort\s*\([^)]*\).*?"
        r"\b\1\s*\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]",
        re.DOTALL,
    )
    generic_sort_index_re = re.compile(
        r"\bsort(?:ed)?\s*\([^)]*\).*?\[\s*(?:-?\d+|:\s*[^]\n]+)\s*\]",
        re.DOTALL,
    )

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        snippet = _read_symbol_source(r["file_path"], r["line_start"], r["line_end"])
        if not snippet:
            continue

        # Strong patterns: sorted(...)[0], sorted(... )[:k], arr.sort(); arr[0]
        if sorted_index_re.search(snippet) or inplace_sort_index_re.search(snippet):
            results.append(_finding(
                "sort-to-select", "full-sort", r,
                "Sort used only for first/last/top-k selection",
                "high",
            ))
            continue

        # Fallback pattern for other languages (sort(...) then index/slice).
        if generic_sort_index_re.search(snippet):
            results.append(_finding(
                "sort-to-select", "full-sort", r,
                "Potential full sort followed by index/slice selection",
                "medium",
            ))
    return results


def detect_loop_lookup(conn):
    """.index(), .indexOf(), .contains(), .includes() called inside a loop.

    Each call is O(m) linear scan on the lookup collection, total O(n*m).
    Pre-building a set gives O(1) per lookup, O(n+m) total.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_lookup_calls, ms.calls_in_loops, "
            "ms.calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, '' as loop_lookup_calls, ms.calls_in_loops, "
            "'' as calls_in_loops_qualified "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_depth >= 1"
        ).fetchall()

    _LOOKUP_CALLS = {"index", "indexOf", "lastIndexOf", "contains",
                     "includes", "Contains", "IndexOf"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        lookup_calls = _json_list(_row_value(r, "loop_lookup_calls", ""))
        if lookup_calls:
            results.append(_finding(
                "loop-lookup", "method-scan", r,
                f"Linear lookup ({', '.join(lookup_calls[:2])}) called on invariant collection",
                "high",
            ))
            continue
        # Fallback for older indexes: conservative matching only.
        calls = _iter_loop_calls(r)
        fallback_hits = _call_in(calls, _LOOKUP_CALLS)
        if fallback_hits:
            results.append(_finding(
                "loop-lookup", "method-scan", r,
                f"Linear lookup ({', '.join(fallback_hits[:2])}) called inside loop",
                "low",
            ))
    return results


# ---------------------------------------------------------------------------
# Tier 2 detectors: enhanced signals
# ---------------------------------------------------------------------------

def detect_branching_recursion(conn):
    """Functions with 2+ self-call sites and no memoization.

    Generalizes fibonacci to any branching recursion: tree traversals,
    divide-and-conquer, DP problems.  O(2^n) -> O(n) with memoization.
    """
    # self_call_count column may not exist in older DBs — fall back safely
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.self_call_count "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.self_call_count >= 2"
        ).fetchall()
    except Exception:
        return []

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Skip fibonacci — already covered by detect_naive_fibonacci
        name_lower = (r["name"] or "").lower()
        if "fib" in name_lower:
            continue
        # Skip tree/AST walkers — recursive traversal of children is
        # intentional and doesn't have overlapping subproblems
        _WALKER_NAMES = {"walk", "visit", "traverse", "search", "scan",
                         "crawl", "descend", "recurse", "dfs", "bfs"}
        if any(kw in name_lower for kw in _WALKER_NAMES):
            continue
        # Check for memoization edge
        memo_edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.name IN ('lru_cache', 'cache', 'memoize', 'memo', "
            "               'functools.lru_cache', 'functools.cache') "
            "LIMIT 1",
            (r["id"],),
        ).fetchone()
        if memo_edge:
            continue
        results.append(_finding(
            "branching-recursion", "naive-branching", r,
            f"Branching recursion ({r['self_call_count']} self-calls) without memoization",
            "high",
        ))
    return results


def detect_quadratic_string(conn):
    """String concatenation via += inside a loop — O(n^2) due to
    immutable string reallocation in Python/Java/Go.
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.str_concat_in_loop "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.str_concat_in_loop = 1"
        ).fetchall()
    except Exception:
        return []

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        results.append(_finding(
            "quadratic-string", "augment-concat", r,
            "String += in loop (O(n^2) due to immutable reallocation)",
            "high",
        ))
    return results


def detect_loop_invariant_call(conn):
    """Calls inside loops whose arguments don't reference the loop variable.

    These can be hoisted before the loop to avoid repeated computation.
    Suppresses common intentional per-iteration calls (logging, metrics, etc.).
    """
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
            "s.line_start, ms.loop_invariant_calls "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "JOIN math_signals ms ON ms.symbol_id = s.id "
            "WHERE s.kind IN ('function', 'method') "
            "AND ms.loop_invariant_calls IS NOT NULL "
            "AND ms.loop_invariant_calls != '[]'"
        ).fetchall()
    except Exception:
        return []

    # Calls that are intentionally per-iteration (suppress)
    _INTENTIONAL_CALLS = {
        # Logging / output
        "print", "log", "debug", "info", "warn", "warning", "error",
        # Collection mutation
        "append", "add", "push", "extend", "write", "send",
        "get", "values", "items", "keys", "update", "pop", "remove",
        "insert", "setdefault", "discard",
        # String methods (inherently per-item)
        "startswith", "endswith", "replace", "format", "strip", "split",
        "join", "lower", "upper", "lstrip", "rstrip", "encode", "decode",
        "ljust", "rjust", "center", "zfill",
        # Event / tracking
        "emit", "track", "record", "increment", "decrement",
        # Iteration helpers / builtins
        "enumerate", "zip", "range", "sorted", "reversed",
        "list", "dict", "set", "tuple", "len", "str", "int", "float",
        "bool", "bytes", "type",
        # Math / comparison builtins (per-item reductions)
        "max", "min", "sum", "abs", "round", "pow",
        "isinstance", "issubclass", "hasattr", "getattr", "setattr",
        # File / IO
        "resolve", "execute", "fetchone", "fetchall", "read_text",
        "read_bytes", "open",
        # Control flow
        "sleep", "yield",
    }

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        inv_calls = json.loads(r["loop_invariant_calls"]) if r["loop_invariant_calls"] else []
        # Filter out intentional per-iteration calls
        flagged = [c for c in inv_calls if c.lower() not in _INTENTIONAL_CALLS]
        if not flagged:
            continue
        results.append(_finding(
            "loop-invariant-call", "repeated-call", r,
            f"Loop-invariant call ({', '.join(flagged[:3])}) can be hoisted before loop",
            "medium",
        ))
    return results


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------

_CONFIDENCE_ORDER = ["low", "medium", "high"]

_DETECTOR_METADATA = {
    "sorting": {"precision": "high", "impact": "medium", "tags": ["ordering", "nested-loop"]},
    "search-sorted": {"precision": "low", "impact": "medium", "tags": ["search", "sorted-data"]},
    "membership": {"precision": "medium", "impact": "high", "tags": ["collections", "n2"]},
    "string-concat": {"precision": "medium", "impact": "medium", "tags": ["string", "quadratic"]},
    "unique": {"precision": "medium", "impact": "high", "tags": ["dedup", "n2"]},
    "max-min": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "accumulation": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "manual-power": {"precision": "high", "impact": "medium", "tags": ["math"]},
    "manual-gcd": {"precision": "high", "impact": "low", "tags": ["math"]},
    "fibonacci": {"precision": "high", "impact": "high", "tags": ["recursion", "exponential"]},
    "nested-lookup": {"precision": "medium", "impact": "high", "tags": ["join", "nxm"]},
    "groupby": {"precision": "medium", "impact": "low", "tags": ["idiom"]},
    "string-reverse": {"precision": "low", "impact": "low", "tags": ["idiom"]},
    "matrix-mult": {"precision": "high", "impact": "high", "tags": ["math", "triple-loop"]},
    "busy-wait": {"precision": "high", "impact": "high", "tags": ["concurrency"]},
    "regex-in-loop": {"precision": "high", "impact": "high", "tags": ["string", "regex"]},
    "io-in-loop": {"precision": "high", "impact": "high", "tags": ["io", "n+1"]},
    "list-prepend": {"precision": "high", "impact": "high", "tags": ["collections", "front-shift"]},
    "sort-to-select": {"precision": "high", "impact": "medium", "tags": ["ordering"]},
    "loop-lookup": {"precision": "high", "impact": "high", "tags": ["collections", "lookup"]},
    "branching-recursion": {"precision": "high", "impact": "high", "tags": ["recursion", "dp"]},
    "quadratic-string": {"precision": "high", "impact": "high", "tags": ["string", "quadratic"]},
    "loop-invariant-call": {"precision": "medium", "impact": "medium", "tags": ["hoisting"]},
}


def _detector_meta(task_id: str) -> dict:
    return _DETECTOR_METADATA.get(
        task_id,
        {"precision": "medium", "impact": "medium", "tags": []},
    )


def _boost_confidence(confidence: str) -> str:
    """Raise confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[min(idx + 1, len(_CONFIDENCE_ORDER) - 1)]


def _lower_confidence(confidence: str) -> str:
    """Lower confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[max(idx - 1, 0)]


_SIGNAL_COLUMNS = [
    ("has_nested_loops", "nested_loops"),
    ("subscript_in_loops", "subscript_in_loops"),
    ("loop_with_compare", "loop_compare"),
    ("loop_with_accumulator", "loop_accumulator"),
    ("loop_with_multiplication", "loop_multiplication"),
    ("loop_with_modulo", "loop_modulo"),
    ("str_concat_in_loop", "string_concat"),
    ("front_ops_in_loop", "front_ops"),
]

_PROFILE_BALANCED = "balanced"
_PROFILE_STRICT = "strict"
_PROFILE_AGGRESSIVE = "aggressive"
_VALID_PROFILES = {
    _PROFILE_BALANCED,
    _PROFILE_STRICT,
    _PROFILE_AGGRESSIVE,
}


def _chunked(values: list[int], size: int = 500):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _symbol_context(conn, symbol_ids: list[int]) -> dict[int, dict]:
    """Load calibration + evidence context for findings in bulk."""
    context: dict[int, dict] = {
        sid: {
            "signals": [],
            "loop_depth": 0,
            "loop_bound_small": 0,
            "caller_count": 0,
            "runtime_call_count": 0,
            "runtime_p99_latency_ms": None,
            "runtime_error_rate": 0.0,
            "runtime_otel_db_system": None,
            "runtime_otel_db_operation": None,
            "runtime_otel_db_statement_type": None,
        }
        for sid in symbol_ids
    }
    if not symbol_ids:
        return context

    # Static AST signals
    for chunk in _chunked(symbol_ids):
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT symbol_id, loop_depth, loop_bound_small, "
            f"has_nested_loops, subscript_in_loops, loop_with_compare, "
            f"loop_with_accumulator, loop_with_multiplication, loop_with_modulo, "
            f"str_concat_in_loop, front_ops_in_loop "
            f"FROM math_signals WHERE symbol_id IN ({ph})",
            chunk,
        ).fetchall()
        for r in rows:
            sid = r["symbol_id"]
            if sid not in context:
                continue
            ctx = context[sid]
            ctx["loop_depth"] = int(r["loop_depth"] or 0)
            ctx["loop_bound_small"] = int(r["loop_bound_small"] or 0)
            signals: list[str] = []
            for col, label in _SIGNAL_COLUMNS:
                if r[col]:
                    signals.append(label)
            ctx["signals"] = signals

    # Caller counts
    for chunk in _chunked(symbol_ids):
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT target_id, COUNT(*) AS cnt "
            f"FROM edges WHERE kind = 'call' AND target_id IN ({ph}) "
            f"GROUP BY target_id",
            chunk,
        ).fetchall()
        for r in rows:
            sid = r["target_id"]
            if sid in context:
                context[sid]["caller_count"] = int(r["cnt"] or 0)

    # Runtime traces (optional table for legacy DB compatibility)
    try:
        for chunk in _chunked(symbol_ids):
            ph = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT symbol_id, SUM(call_count) AS total_calls, "
                f"MAX(p99_latency_ms) AS p99_ms, MAX(error_rate) AS max_err, "
                f"MAX(otel_db_system) AS db_system, "
                f"MAX(otel_db_operation) AS db_operation, "
                f"MAX(otel_db_statement_type) AS db_statement_type "
                f"FROM runtime_stats "
                f"WHERE symbol_id IN ({ph}) "
                f"GROUP BY symbol_id",
                chunk,
            ).fetchall()
            for r in rows:
                sid = r["symbol_id"]
                if sid not in context:
                    continue
                context[sid]["runtime_call_count"] = int(r["total_calls"] or 0)
                context[sid]["runtime_p99_latency_ms"] = r["p99_ms"]
                context[sid]["runtime_error_rate"] = float(r["max_err"] or 0.0)
                context[sid]["runtime_otel_db_system"] = r["db_system"]
                context[sid]["runtime_otel_db_operation"] = r["db_operation"]
                context[sid]["runtime_otel_db_statement_type"] = r["db_statement_type"]
    except Exception:
        pass

    return context


def _merge_evidence(existing: dict | None, enriched: dict) -> dict:
    if not existing:
        return enriched
    merged = dict(existing)
    for k, v in enriched.items():
        if k not in merged:
            merged[k] = v
            continue
        current = merged[k]
        if isinstance(current, list) and isinstance(v, list):
            merged[k] = _dedupe([*current, *v])
        elif isinstance(current, dict) and isinstance(v, dict):
            tmp = dict(current)
            tmp.update(v)
            merged[k] = tmp
        else:
            merged[k] = v
    return merged


def _build_evidence(context: dict) -> dict:
    evidence = {
        "signal_count": len(context.get("signals", [])),
        "signals": context.get("signals", []),
        "loop_depth": int(context.get("loop_depth", 0) or 0),
        "loop_bound_small": bool(context.get("loop_bound_small", 0)),
        "caller_count": int(context.get("caller_count", 0) or 0),
    }
    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_error = float(context.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = context.get("runtime_otel_db_operation")
    runtime_db_statement_type = context.get("runtime_otel_db_statement_type")
    if runtime_calls > 0 or runtime_p99 is not None:
        evidence["runtime"] = {
            "call_count": runtime_calls,
            "p99_latency_ms": runtime_p99,
            "error_rate": runtime_error,
            "db_system": runtime_db_system,
            "db_operation": runtime_db_operation,
            "db_statement_type": runtime_db_statement_type,
        }
    return evidence


def _build_evidence_path(finding: dict, context: dict) -> list[str]:
    path = [f"Observed pattern: {finding['reason']}"]
    signals = context.get("signals", [])
    if signals:
        path.append(f"Static evidence: {', '.join(signals[:4])}")
    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = (
        context.get("runtime_otel_db_operation")
        or context.get("runtime_otel_db_statement_type")
    )
    if runtime_calls > 0:
        if runtime_p99 is not None:
            path.append(f"Runtime impact: {runtime_calls} calls, p99 {runtime_p99:.0f}ms")
        else:
            path.append(f"Runtime impact: {runtime_calls} calls")
        if runtime_db_system or runtime_db_operation:
            path.append(
                "Runtime semantics: db_system={} db_operation={}".format(
                    runtime_db_system or "n/a",
                    runtime_db_operation or "n/a",
                )
            )
    path.append(
        f"Recommendation: replace `{finding['detected_way']}` "
        f"with `{finding['suggested_way']}`."
    )
    return path


def _impact_score(finding: dict, context: dict) -> float:
    """Rank finding urgency using static + runtime evidence."""
    confidence_base = {"high": 60.0, "medium": 35.0, "low": 15.0}
    impact_weight = {"high": 12.0, "medium": 6.0, "low": 2.0}

    score = confidence_base.get(finding.get("confidence", "medium"), 30.0)
    score += impact_weight.get(finding.get("impact", "medium"), 5.0)

    loop_depth = int(context.get("loop_depth", 0) or 0)
    score += min(12.0, loop_depth * 2.0)

    caller_count = int(context.get("caller_count", 0) or 0)
    score += min(10.0, caller_count * 1.5)

    runtime_calls = int(context.get("runtime_call_count", 0) or 0)
    runtime_p99 = context.get("runtime_p99_latency_ms")
    runtime_error = float(context.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = context.get("runtime_otel_db_system")
    runtime_db_operation = (
        context.get("runtime_otel_db_operation")
        or context.get("runtime_otel_db_statement_type")
        or ""
    )
    runtime_db_operation = str(runtime_db_operation).upper()
    runtime_multiplier = 0.65
    if runtime_db_system:
        runtime_multiplier = 1.0
        if runtime_db_operation in {"INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "REPLACE"}:
            runtime_multiplier = 1.25
        elif runtime_db_operation in {"SELECT", "READ", "QUERY", "GET"}:
            runtime_multiplier = 1.0
        else:
            runtime_multiplier = 0.9
    if runtime_calls:
        score += min(20.0, (len(str(max(1, runtime_calls))) - 1) * 5.0) * runtime_multiplier
    if runtime_p99 is not None:
        latency_mult = 1.1 if runtime_db_system else 0.9
        score += min(15.0, float(runtime_p99) / 40.0) * latency_mult
    if runtime_error > 0:
        score += min(8.0, runtime_error * 100.0)
    if runtime_db_system:
        score += 2.5
    if runtime_db_operation in {"INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "REPLACE"}:
        score += 3.5

    if context.get("loop_bound_small", 0):
        score -= 8.0

    return round(max(0.0, score), 2)


def _impact_band(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _calibrate_finding(finding: dict, context: dict | None) -> dict:
    """Adjust confidence using static and runtime context."""
    ctx = context or {}
    caller_count = int(ctx.get("caller_count", 0) or 0)
    loop_bound_small = bool(ctx.get("loop_bound_small", 0))
    runtime_calls = int(ctx.get("runtime_call_count", 0) or 0)
    runtime_p99 = ctx.get("runtime_p99_latency_ms")
    runtime_error = float(ctx.get("runtime_error_rate", 0.0) or 0.0)
    runtime_db_system = ctx.get("runtime_otel_db_system")
    runtime_db_operation = (
        ctx.get("runtime_otel_db_operation")
        or ctx.get("runtime_otel_db_statement_type")
        or ""
    )
    runtime_db_operation = str(runtime_db_operation).upper()

    if caller_count >= 5:
        finding["confidence"] = _boost_confidence(finding["confidence"])
        finding["reason"] += f" (hot: {caller_count} callers)"
    elif caller_count == 0 and runtime_calls == 0:
        finding["confidence"] = _lower_confidence(finding["confidence"])

    runtime_call_threshold = 1500 if not runtime_db_system else 700
    runtime_hot = (
        runtime_calls >= runtime_call_threshold
        or (runtime_p99 is not None and runtime_p99 >= 300)
        or runtime_error >= 0.02
    )
    if runtime_hot:
        finding["confidence"] = _boost_confidence(finding["confidence"])
        rt_bits = []
        if runtime_calls:
            rt_bits.append(f"{runtime_calls} calls")
        if runtime_p99 is not None:
            rt_bits.append(f"p99 {runtime_p99:.0f}ms")
        if runtime_error > 0:
            rt_bits.append(f"err {runtime_error * 100:.1f}%")
        if runtime_db_system:
            rt_bits.append(
                "db {}{}".format(
                    runtime_db_system,
                    f" {runtime_db_operation}" if runtime_db_operation else "",
                )
            )
        if rt_bits:
            finding["reason"] += f" (runtime: {', '.join(rt_bits)})"

    if loop_bound_small:
        finding["confidence"] = _lower_confidence(finding["confidence"])
        finding["reason"] += " (bounded loop)"

    return finding


def _has_strong_runtime(evidence: dict) -> bool:
    runtime = evidence.get("runtime") if isinstance(evidence, dict) else None
    if not isinstance(runtime, dict):
        return False
    calls = int(runtime.get("call_count", 0) or 0)
    p99 = runtime.get("p99_latency_ms")
    return calls >= 200 or (p99 is not None and p99 >= 200)


def _apply_profile(findings: list[dict], profile: str) -> list[dict]:
    if profile == _PROFILE_AGGRESSIVE:
        for f in findings:
            evidence = f.get("evidence", {})
            if (
                f.get("confidence") == "low"
                and (
                    int(evidence.get("signal_count", 0) or 0) >= 2
                    or _has_strong_runtime(evidence)
                )
            ):
                f["confidence"] = "medium"
        return findings

    if profile == _PROFILE_STRICT:
        strict_findings: list[dict] = []
        for f in findings:
            if f.get("confidence") == "low":
                continue
            precision = (f.get("precision") or "medium").lower()
            evidence = f.get("evidence", {})
            runtime_strong = _has_strong_runtime(evidence)
            if precision == "low" and not runtime_strong and f.get("confidence") != "high":
                continue
            if f.get("confidence") == "high":
                strict_findings.append(f)
                continue
            if (
                int(evidence.get("signal_count", 0) or 0) >= 2
                or runtime_strong
            ):
                strict_findings.append(f)
        return strict_findings

    return findings


# ---------------------------------------------------------------------------
# Detector registry
# ---------------------------------------------------------------------------

_MATH_DETECTORS = [
    ("sorting",        "manual-sort",       detect_manual_sort),
    ("search-sorted",  "linear-scan",       detect_linear_search),
    ("membership",     "list-scan",         detect_list_membership),
    ("string-concat",  "loop-concat",       detect_string_concat_loop),
    ("unique",         "nested-dedup",      detect_manual_dedup),
    ("max-min",        "manual-loop",       detect_manual_maxmin),
    ("accumulation",   "manual-sum",        detect_manual_accumulation),
    ("manual-power",   "loop-multiply",     detect_manual_power),
    ("manual-gcd",     "manual-gcd",        detect_manual_gcd),
    ("fibonacci",      "naive-recursive",   detect_naive_fibonacci),
    ("nested-lookup",  "nested-iteration",  detect_nested_lookup),
    ("groupby",        "manual-check",      detect_manual_groupby),
    ("string-reverse", "manual-reverse",    detect_string_reverse),
    ("matrix-mult",    "naive-triple",      detect_matrix_mult),
    ("busy-wait",      "sleep-loop",        detect_busy_wait),
    ("regex-in-loop",  "compile-per-iter",  detect_regex_in_loop),
    ("io-in-loop",     "loop-query",        detect_io_in_loop),
    ("list-prepend",   "insert-front",      detect_list_prepend),
    ("sort-to-select", "full-sort",         detect_sort_to_select),
    ("loop-lookup",    "method-scan",       detect_loop_lookup),
    ("branching-recursion", "naive-branching", detect_branching_recursion),
    ("quadratic-string",    "augment-concat",  detect_quadratic_string),
    ("loop-invariant-call", "repeated-call",   detect_loop_invariant_call),
]


def _iter_registered_detectors():
    """Yield built-in detectors plus plugin-contributed detectors."""
    for det in _MATH_DETECTORS:
        yield det

    try:
        from roam.plugins import get_plugin_detectors

        for task_id, way_id, detect_fn in get_plugin_detectors():
            if callable(detect_fn):
                yield (task_id, way_id, detect_fn)
    except Exception:
        # Plugin loading errors should not impact built-in detection.
        return


def run_detectors(
    conn,
    task_filter=None,
    confidence_filter=None,
    *,
    profile="balanced",
    return_meta=False,
):
    """Run all detectors and return combined findings.

    Parameters
    ----------
    conn : sqlite3.Connection
    task_filter : str or None
        If set, only run the detector for this task_id.
    confidence_filter : str or None
        If set, keep only findings with this confidence level.
    profile : str
        Precision profile: ``balanced`` (default), ``strict``, ``aggressive``.
    return_meta : bool
        When True, returns ``(findings, meta)`` where ``meta`` contains
        detector execution diagnostics (totals + failures).

    Returns list of finding dicts, or ``(findings, meta)`` when
    ``return_meta=True``.
    """
    findings = []
    failed_detectors = []
    executed = 0
    executed_tasks: list[str] = []
    for task_id, _way_id, detect_fn in _iter_registered_detectors():
        if task_filter and task_id != task_filter:
            continue
        executed += 1
        executed_tasks.append(task_id)
        try:
            hits = detect_fn(conn)
        except Exception as exc:
            failed_detectors.append({
                "task_id": task_id,
                "detector": detect_fn.__name__,
                "error": str(exc),
            })
            continue
        dmeta = _detector_meta(task_id)
        for h in hits:
            h.setdefault("precision", dmeta["precision"])
            h.setdefault("impact", dmeta["impact"])
            h.setdefault("tags", list(dmeta["tags"]))
        findings.extend(hits)

    profile_key = (profile or _PROFILE_BALANCED).lower()
    if profile_key not in _VALID_PROFILES:
        profile_key = _PROFILE_BALANCED

    symbol_ids = _dedupe([f["symbol_id"] for f in findings if f.get("symbol_id")])
    context_map = _symbol_context(conn, symbol_ids)

    # Apply calibration and enrich findings with semantic evidence.
    for f in findings:
        ctx = context_map.get(f["symbol_id"], {})
        _calibrate_finding(f, ctx)
        enriched_evidence = _build_evidence(ctx)
        f["evidence"] = _merge_evidence(f.get("evidence"), enriched_evidence)
        f["evidence_path"] = _build_evidence_path(f, ctx)
        score = _impact_score(f, ctx)
        f["impact_score"] = score
        f["impact_band"] = _impact_band(score)

    pre_profile_count = len(findings)
    findings = _apply_profile(findings, profile_key)
    profile_filtered = pre_profile_count - len(findings)

    if confidence_filter:
        findings = [f for f in findings if f["confidence"] == confidence_filter]

    if return_meta:
        meta = {
            "detectors_executed": executed,
            "detectors_failed": len(failed_detectors),
            "failed_detectors": failed_detectors,
            "profile": profile_key,
            "profile_filtered": profile_filtered,
            "detector_metadata": {
                task_id: _detector_meta(task_id) for task_id in executed_tasks
            },
        }
        return findings, meta

    return findings
