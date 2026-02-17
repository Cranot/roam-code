"""Universal algorithm catalog: task -> ranked solution approaches.

Pure data — no code dependencies.  Each task describes a computational
problem and lists known solution approaches ordered from best (rank 1)
to worst (highest rank).  Detectors in ``detectors.py`` match running
code to the *detected_way* and suggest the top-ranked alternative.
"""

from __future__ import annotations

CATALOG: dict[str, dict] = {
    "sorting": {
        "name": "Sorting",
        "category": "ordering",
        "kind": "algorithm",
        "ways": [
            {"id": "builtin-sort",  "name": "Built-in sort",              "time": "O(n log n)", "space": "O(n)",   "rank": 1, "tip": "Use sorted() / list.sort() / Arrays.sort()"},
            {"id": "manual-sort",   "name": "Manual bubble/selection sort", "time": "O(n^2)",     "space": "O(1)",   "rank": 10, "tip": ""},
        ],
    },
    "search-sorted": {
        "name": "Search in sorted data",
        "category": "searching",
        "kind": "algorithm",
        "ways": [
            {"id": "binary-search", "name": "Binary search",    "time": "O(log n)", "space": "O(1)", "rank": 1, "tip": "Use bisect / binary_search / Arrays.binarySearch()"},
            {"id": "linear-scan",   "name": "Linear scan",      "time": "O(n)",     "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "membership": {
        "name": "Collection membership test",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "set-lookup",  "name": "Set/hash lookup",  "time": "O(1) amortized",  "space": "O(n)", "rank": 1, "tip": "Convert to set for repeated lookups"},
            {"id": "list-scan",   "name": "List linear scan",  "time": "O(n) per lookup",  "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "string-concat": {
        "name": "String building",
        "category": "string",
        "kind": "algorithm",
        "ways": [
            {"id": "join-builder",  "name": "Join / StringBuilder",  "time": "O(n)",   "space": "O(n)", "rank": 1, "tip": "Collect parts in a list, join once at the end"},
            {"id": "loop-concat",   "name": "Loop concatenation",    "time": "O(n^2)", "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "unique": {
        "name": "Deduplication",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "set-dedup",     "name": "Set-based dedup",     "time": "O(n)",   "space": "O(n)", "rank": 1, "tip": "Use set() / dict.fromkeys() / [...new Set(arr)]"},
            {"id": "nested-dedup",  "name": "Nested loop dedup",   "time": "O(n^2)", "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "max-min": {
        "name": "Find max/min",
        "category": "searching",
        "kind": "idiom",
        "ways": [
            {"id": "builtin-minmax", "name": "Built-in max()/min()", "time": "O(n)", "space": "O(1)", "rank": 1, "tip": "Use max() / min() / Math.max() / Collections.max()"},
            {"id": "manual-loop",    "name": "Manual tracking loop", "time": "O(n)", "space": "O(1)", "rank": 5, "tip": ""},
        ],
    },
    "accumulation": {
        "name": "Summation / reduction",
        "category": "math",
        "kind": "idiom",
        "ways": [
            {"id": "builtin-sum",  "name": "Built-in sum/reduce", "time": "O(n)", "space": "O(1)", "rank": 1, "tip": "Use sum() / math.prod() / reduce() / stream().reduce()"},
            {"id": "manual-sum",   "name": "Manual accumulator",  "time": "O(n)", "space": "O(1)", "rank": 5, "tip": ""},
        ],
    },
    "fibonacci": {
        "name": "Fibonacci computation",
        "category": "math",
        "kind": "algorithm",
        "ways": [
            {"id": "iterative-fib",     "name": "Iterative / memoized", "time": "O(n)",    "space": "O(1)", "rank": 1, "tip": "Use iterative loop or @lru_cache / @cache"},
            {"id": "naive-recursive",    "name": "Naive recursion",     "time": "O(2^n)",  "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "manual-power": {
        "name": "Exponentiation",
        "category": "math",
        "kind": "algorithm",
        "ways": [
            {"id": "builtin-pow",  "name": "Built-in pow()",        "time": "O(log n)", "space": "O(1)", "rank": 1, "tip": "Use pow(base, exp, mod) / ** / Math.pow()"},
            {"id": "loop-multiply", "name": "Loop multiplication",  "time": "O(n)",     "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "manual-gcd": {
        "name": "GCD computation",
        "category": "math",
        "kind": "idiom",
        "ways": [
            {"id": "builtin-gcd",  "name": "Built-in math.gcd",  "time": "O(log n)", "space": "O(1)", "rank": 1, "tip": "Use math.gcd() / BigInteger.gcd() / __gcd()"},
            {"id": "manual-gcd",   "name": "Manual loop",        "time": "O(n) worst case", "space": "O(1)", "rank": 5, "tip": ""},
        ],
    },
    "groupby": {
        "name": "Group by key",
        "category": "collections",
        "kind": "idiom",
        "ways": [
            {"id": "defaultdict-groupby", "name": "defaultdict / Collectors.groupingBy", "time": "O(n)", "space": "O(n)", "rank": 1, "tip": "Use defaultdict(list) / setdefault() / Collectors.groupingBy()"},
            {"id": "manual-check",        "name": "Manual key-existence check",      "time": "O(n)", "space": "O(n)", "rank": 5, "tip": ""},
        ],
    },
    "nested-lookup": {
        "name": "Nested loop lookup",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "hash-join",         "name": "Hash-map join",     "time": "O(n+m)",   "space": "O(n)", "rank": 1, "tip": "Build a dict/set from one collection, iterate the other"},
            {"id": "nested-iteration",   "name": "Nested iteration",  "time": "O(n*m)", "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "string-reverse": {
        "name": "String reversal",
        "category": "string",
        "kind": "idiom",
        "ways": [
            {"id": "builtin-reverse", "name": "Built-in reverse/slice", "time": "O(n)", "space": "O(n)", "rank": 1, "tip": "Use s[::-1] / StringBuilder.reverse() / strings.Reverse()"},
            {"id": "manual-reverse",  "name": "Manual char loop",      "time": "O(n)", "space": "O(n)", "rank": 5, "tip": ""},
        ],
    },
    "matrix-mult": {
        "name": "Matrix multiplication",
        "category": "math",
        "kind": "algorithm",
        "ways": [
            {"id": "blas-mult",    "name": "NumPy / BLAS (optimized)",  "time": "O(n^3)", "space": "O(n^2)", "rank": 1, "tip": "Use numpy.dot() / np.matmul() / @ operator (100-1000x faster via SIMD+cache)"},
            {"id": "naive-triple",  "name": "Naive triple loop",        "time": "O(n^3)",    "space": "O(n^2)", "rank": 10, "tip": ""},
        ],
    },
    "busy-wait": {
        "name": "Polling / busy wait",
        "category": "concurrency",
        "kind": "algorithm",
        "ways": [
            {"id": "event-wait",  "name": "Event / condition variable", "time": "O(1) wake", "space": "O(1)", "rank": 1, "tip": "Use threading.Event / asyncio.Event / select() / Promise"},
            {"id": "sleep-loop",  "name": "Sleep-in-loop polling",     "time": "O(k) polls",  "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "regex-in-loop": {
        "name": "Regex compilation in loop",
        "category": "string",
        "kind": "algorithm",
        "ways": [
            {"id": "precompiled",   "name": "Pre-compiled regex",      "time": "O(p + n*m)", "space": "O(p)", "rank": 1, "tip": "Compile once outside the loop: re.compile() / new RegExp() / Pattern.compile()"},
            {"id": "compile-per-iter", "name": "Compile per iteration", "time": "O(n*(p+m))", "space": "O(p)", "rank": 10, "tip": ""},
        ],
    },
    "io-in-loop": {
        "name": "I/O call in loop (N+1 query)",
        "category": "concurrency",
        "kind": "algorithm",
        "ways": [
            {"id": "batch-query",   "name": "Batch query / bulk I/O", "time": "O(1) round trips", "space": "O(n)", "rank": 1, "tip": "Use WHERE IN (...) / bulk API / batch fetch instead of per-item queries"},
            {"id": "loop-query",    "name": "Per-item query in loop",  "time": "O(n) round trips", "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "list-prepend": {
        "name": "List prepend / front-removal in loop",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "deque-ops",     "name": "Deque / append+reverse", "time": "O(1) per op", "space": "O(n)", "rank": 1, "tip": "Use collections.deque / ArrayDeque / append+reverse instead of insert(0,x)"},
            {"id": "insert-front",  "name": "Insert/remove at front",  "time": "O(n) per op", "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "sort-to-select": {
        "name": "Sort to select min/max/top-k",
        "category": "ordering",
        "kind": "algorithm",
        "ways": [
            {"id": "direct-select", "name": "Direct min/max or heap",   "time": "O(n) or O(n log k)", "space": "O(1) or O(k)", "rank": 1, "tip": "Use min()/max() for extremes, heapq.nsmallest/nlargest for top-k"},
            {"id": "full-sort",     "name": "Full sort then subscript", "time": "O(n log n)",          "space": "O(n)",         "rank": 10, "tip": ""},
        ],
    },
    "loop-lookup": {
        "name": "Repeated collection lookup in loop",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "set-prebuilt",  "name": "Pre-built set/dict",        "time": "O(1) per lookup",  "space": "O(m)", "rank": 1, "tip": "Build a set/dict from the lookup collection once, then use O(1) membership"},
            {"id": "method-scan",   "name": ".index()/.contains() in loop", "time": "O(m) per lookup", "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
    "branching-recursion": {
        "name": "Branching recursion without memoization",
        "category": "math",
        "kind": "algorithm",
        "ways": [
            {"id": "memoized",        "name": "Memoized / iterative DP", "time": "O(n)",   "space": "O(n)", "rank": 1, "tip": "Add @cache / @lru_cache, or convert to iterative with a table"},
            {"id": "naive-branching",  "name": "Naive branching recursion", "time": "O(2^n)", "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "quadratic-string": {
        "name": "Quadratic string building in loop",
        "category": "string",
        "kind": "algorithm",
        "ways": [
            {"id": "join-parts",      "name": "Collect + join",            "time": "O(n)", "space": "O(n)", "rank": 1, "tip": "Append parts to a list, then ''.join(parts) at the end"},
            {"id": "augment-concat",   "name": "str += in loop",           "time": "O(n^2)", "space": "O(n)", "rank": 10, "tip": ""},
        ],
    },
    "loop-invariant-call": {
        "name": "Loop-invariant call inside loop",
        "category": "collections",
        "kind": "algorithm",
        "ways": [
            {"id": "hoisted",         "name": "Call hoisted before loop",  "time": "O(1) per iter", "space": "O(1)", "rank": 1, "tip": "Move the call before the loop and store the result in a variable"},
            {"id": "repeated-call",    "name": "Repeated call per iteration", "time": "O(f(x)) per iter", "space": "O(1)", "rank": 10, "tip": ""},
        ],
    },
}


def get_task(task_id: str) -> dict | None:
    """Return a catalog entry by ID, or None."""
    return CATALOG.get(task_id)


def get_way(task_id: str, way_id: str) -> dict | None:
    """Return a specific way from a task."""
    task = CATALOG.get(task_id)
    if not task:
        return None
    for w in task["ways"]:
        if w["id"] == way_id:
            return w
    return None


def best_way(task_id: str) -> dict | None:
    """Return the rank-1 way for a task."""
    task = CATALOG.get(task_id)
    if not task:
        return None
    for w in task["ways"]:
        if w["rank"] == 1:
            return w
    return task["ways"][0] if task["ways"] else None
