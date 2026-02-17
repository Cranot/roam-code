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

from roam.catalog.tasks import CATALOG, best_way


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


def _finding(task_id, detected_way, sym, reason, confidence="medium"):
    bw = best_way(task_id)
    return {
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("sort", "sorted", "Arrays.sort", "Collections.sort",
                      "qsort", "std::sort")
               for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("bisect", "bisect_left", "bisect_right",
                      "binarySearch", "binary_search", "lower_bound",
                      "upper_bound")
               for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        # Structural signal: calls to string concat/append methods
        has_concat_call = any(c in ("concat", "strcat", "append", "push")
                             for c in calls)
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("set", "Set", "HashSet", "add") for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("max", "min", "Math.max", "Math.min",
                      "Collections.max", "Collections.min")
               for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("sum", "reduce", "aggregate", "prod") for c in calls):
            continue
        results.append(_finding(
            "accumulation", "manual-sum", r,
            "Loop with accumulator in sum/total function (idiomatic improvement)",
            "low",
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in ("groupby", "group_by", "defaultdict", "setdefault",
                      "groupingBy", "Collectors")
               for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if not any(c in ("sleep", "time.sleep", "Thread.sleep", "usleep",
                         "nanosleep", "Sleep")
                   for c in calls):
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
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        # Direct compile in loop is always bad
        compile_calls = [c for c in calls if c in _REGEX_COMPILE_CALLS]
        # Convenience regex calls in loop (re.match, re.search, etc.) also
        # compile each time in most runtimes — flag at medium confidence
        # since these could be on a pre-compiled pattern object
        convenience_calls = [c for c in calls if c in _REGEX_CONVENIENCE_CALLS]
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
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    # High-confidence: ORM/HTTP calls that are unambiguously N+1
    _IO_CALLS_HIGH = {
        # ORM / high-level DB (method names specific to ORMs)
        "query", "session.execute",
        "find", "find_one", "find_all", "get_or_create",
        # HTTP
        "requests.get", "requests.post", "requests.put",
        "requests.delete", "http.Get", "http.Post",
        "urllib.request.urlopen",
    }
    # Medium-confidence: could be local DB (SQLite cursor chaining) or remote
    # fetchone/fetchall are ambiguous — ORM N+1 OR just cursor.fetchone()
    _IO_CALLS_MEDIUM = {
        "fetchone", "fetchall", "fetchmany",
        "cursor.execute", "db.query",
        "fetch", "open",
    }

    # Suppress functions that are intentionally per-item I/O wrappers
    _IO_WRAPPER_NAMES = {"batch", "bulk", "migrate", "seed", "import",
                         "export", "load_all", "sync_all", "build",
                         "resolve", "compute", "collect", "analyze"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        high_calls = [c for c in calls if c in _IO_CALLS_HIGH]
        medium_calls = [c for c in calls if c in _IO_CALLS_MEDIUM]
        if not high_calls and not medium_calls:
            continue
        name_lower = (r["name"] or "").lower()
        if any(kw in name_lower for kw in _IO_WRAPPER_NAMES):
            continue
        if high_calls:
            results.append(_finding(
                "io-in-loop", "loop-query", r,
                f"I/O call ({', '.join(high_calls[:2])}) inside loop (N+1 pattern)",
                "high",
            ))
        else:
            results.append(_finding(
                "io-in-loop", "loop-query", r,
                f"DB call ({', '.join(medium_calls[:2])}) inside loop (may be N+1)",
                "medium",
            ))
    return results


def detect_list_prepend(conn):
    """insert(0, x), unshift(), or pop(0) inside a loop — O(n) per op
    due to array shifting, O(n^2) total."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, ms.loop_depth, ms.calls_in_loops "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method') "
        "AND ms.loop_depth >= 1"
    ).fetchall()

    _PREPEND_CALLS = {"insert", "unshift", "add", "Insert", "Add"}
    # Note: insert(0,x) and add(0,x) are the specific anti-pattern;
    # we detect the call name and rely on the loop context for confidence.

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        if any(c in _PREPEND_CALLS for c in calls):
            results.append(_finding(
                "list-prepend", "insert-front", r,
                "List insert/unshift inside loop (O(n) shift per operation)",
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
        "s.line_start, ms.calls_in_loops, ms.subscript_in_loops, "
        "ms.loop_depth "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN math_signals ms ON ms.symbol_id = s.id "
        "WHERE s.kind IN ('function', 'method')"
    ).fetchall()

    # We check for edges to sort functions from this symbol
    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        # Check if this function calls a sort function
        sort_edge = conn.execute(
            "SELECT 1 FROM edges e "
            "JOIN symbols t ON e.target_id = t.id "
            "WHERE e.source_id = ? "
            "AND t.name IN ('sorted', 'sort', 'Sort', 'Arrays.sort', "
            "               'Collections.sort') "
            "LIMIT 1",
            (r["id"],),
        ).fetchone()
        if not sort_edge:
            continue
        # Name hints: functions that sort just to pick extremes
        name_lower = (r["name"] or "").lower()
        has_select_hint = any(kw in name_lower for kw in
                             ("max", "min", "top", "bottom", "best", "worst",
                              "largest", "smallest", "highest", "lowest",
                              "first", "last", "rank", "nth"))
        if has_select_hint:
            results.append(_finding(
                "sort-to-select", "full-sort", r,
                "Sorting entire collection to select min/max/top-k",
                "medium",
            ))
    return results


def detect_loop_lookup(conn):
    """.index(), .indexOf(), .contains(), .includes() called inside a loop.

    Each call is O(m) linear scan on the lookup collection, total O(n*m).
    Pre-building a set gives O(1) per lookup, O(n+m) total.
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

    _LOOKUP_CALLS = {"index", "indexOf", "lastIndexOf", "contains",
                     "includes", "Contains", "IndexOf", "find",
                     "count"}

    results = []
    for r in rows:
        if _is_test_path(r["file_path"]):
            continue
        calls = json.loads(r["calls_in_loops"]) if r["calls_in_loops"] else []
        lookup_calls = [c for c in calls if c in _LOOKUP_CALLS]
        if lookup_calls:
            results.append(_finding(
                "loop-lookup", "method-scan", r,
                f"Linear lookup ({', '.join(lookup_calls[:2])}) called inside loop",
                "medium",
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
        "print", "log", "debug", "info", "warn", "warning", "error",
        "append", "add", "push", "extend", "write", "send",
        "emit", "track", "record", "increment", "decrement",
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


def _boost_confidence(confidence: str) -> str:
    """Raise confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[min(idx + 1, len(_CONFIDENCE_ORDER) - 1)]


def _lower_confidence(confidence: str) -> str:
    """Lower confidence by one level."""
    idx = _CONFIDENCE_ORDER.index(confidence) if confidence in _CONFIDENCE_ORDER else 1
    return _CONFIDENCE_ORDER[max(idx - 1, 0)]


def _calibrate_finding(finding: dict, conn) -> dict:
    """Adjust confidence based on caller count and bounded-loop signals."""
    sym_id = finding["symbol_id"]

    # --- Caller count: high in-degree = hot code, boost confidence ---
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM edges WHERE target_id = ? AND kind = 'call'",
            (sym_id,),
        ).fetchone()
        caller_count = row["cnt"] if row else 0
    except Exception:
        caller_count = 0

    if caller_count >= 5:
        finding["confidence"] = _boost_confidence(finding["confidence"])
        finding["reason"] += f" (hot: {caller_count} callers)"
    elif caller_count == 0:
        finding["confidence"] = _lower_confidence(finding["confidence"])

    # --- Bounded-size loop: suppress if the loop is known-small ---
    try:
        ms_row = conn.execute(
            "SELECT loop_bound_small FROM math_signals WHERE symbol_id = ?",
            (sym_id,),
        ).fetchone()
        if ms_row and ms_row["loop_bound_small"]:
            finding["confidence"] = _lower_confidence(finding["confidence"])
            finding["reason"] += " (bounded loop)"
    except Exception:
        pass

    return finding


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
    ("fibonacci",      "naive-recursive",   detect_naive_fibonacci),
    ("nested-lookup",  "nested-iteration",  detect_nested_lookup),
    ("groupby",        "manual-check",      detect_manual_groupby),
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


def run_detectors(conn, task_filter=None, confidence_filter=None):
    """Run all detectors and return combined findings.

    Parameters
    ----------
    conn : sqlite3.Connection
    task_filter : str or None
        If set, only run the detector for this task_id.
    confidence_filter : str or None
        If set, keep only findings with this confidence level.

    Returns list of finding dicts.
    """
    findings = []
    for task_id, _way_id, detect_fn in _MATH_DETECTORS:
        if task_filter and task_id != task_filter:
            continue
        try:
            hits = detect_fn(conn)
        except Exception:
            continue
        findings.extend(hits)

    # Apply confidence calibration to all findings
    for f in findings:
        _calibrate_finding(f, conn)

    if confidence_filter:
        findings = [f for f in findings if f["confidence"] == confidence_filter]

    return findings
