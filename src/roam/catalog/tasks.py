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


# ---------------------------------------------------------------------------
# Language-aware tip overrides
# ---------------------------------------------------------------------------
# Key: (task_id, way_id) -> {language: tip_string}
# The "default" key is used when no language-specific tip exists.
# Languages not listed fall back to the way's static ``tip`` field,
# then to the "default" key here.
#
# Supported language keys match the ``files.language`` column values:
#   python, javascript, typescript, go, java, rust, ruby, c, cpp, php
# ---------------------------------------------------------------------------

_LANGUAGE_TIPS: dict[tuple[str, str], dict[str, str]] = {
    # -- sorting --
    ("sorting", "builtin-sort"): {
        "default":    "Use the language's built-in sort",
        "python":     "Use sorted() or list.sort()",
        "javascript": "Use Array.prototype.sort()",
        "typescript": "Use Array.prototype.sort()",
        "go":         "Use sort.Slice() or slices.Sort()",
        "java":       "Use Arrays.sort() or Collections.sort()",
        "rust":       "Use .sort() or .sort_unstable() on slices",
        "ruby":       "Use Array#sort or Array#sort_by",
        "c":          "Use qsort() from <stdlib.h>",
        "cpp":        "Use std::sort() from <algorithm>",
        "php":        "Use sort() / usort() / array_multisort()",
    },
    # -- search-sorted --
    ("search-sorted", "binary-search"): {
        "default":    "Use binary search on sorted data",
        "python":     "Use bisect.bisect_left() / bisect.insort()",
        "javascript": "Implement binary search or use lodash _.sortedIndex()",
        "typescript": "Implement binary search or use lodash _.sortedIndex()",
        "go":         "Use sort.Search() or slices.BinarySearch()",
        "java":       "Use Arrays.binarySearch() or Collections.binarySearch()",
        "rust":       "Use .binary_search() on sorted slices",
        "ruby":       "Use Array#bsearch",
        "c":          "Use bsearch() from <stdlib.h>",
        "cpp":        "Use std::lower_bound() / std::binary_search()",
        "php":        "Implement binary search (no built-in)",
    },
    # -- membership --
    ("membership", "set-lookup"): {
        "default":    "Convert to a set/hash for O(1) lookups",
        "python":     "Convert to set() for repeated lookups",
        "javascript": "Use Set or Map for O(1) lookups",
        "typescript": "Use Set<T> or Map<K,V> for O(1) lookups",
        "go":         "Use map[T]bool or map[T]struct{} for O(1) lookups",
        "java":       "Use HashSet<T> or HashMap<K,V> for O(1) lookups",
        "rust":       "Use HashSet<T> or HashMap<K,V> for O(1) lookups",
        "ruby":       "Use Set or Hash for O(1) lookups",
        "c":          "Use a hash table for O(1) lookups",
        "cpp":        "Use std::unordered_set or std::unordered_map",
        "php":        "Use array keys or SplObjectStorage for O(1) lookups",
    },
    # -- string-concat --
    ("string-concat", "join-builder"): {
        "default":    "Collect parts in a list, join once at the end",
        "python":     "Collect parts in a list, then ''.join(parts)",
        "javascript": "Use Array.push() + Array.join(), or template literals",
        "typescript": "Use Array.push() + Array.join(), or template literals",
        "go":         "Use strings.Builder for efficient concatenation",
        "java":       "Use StringBuilder to collect parts",
        "rust":       "Use String::push_str() or format!() / write!()",
        "ruby":       "Use Array#join or StringIO",
        "c":          "Pre-allocate buffer and use strncat() / memcpy()",
        "cpp":        "Use std::ostringstream or reserve() + append()",
        "php":        "Use implode() on an array of parts",
    },
    # -- unique --
    ("unique", "set-dedup"): {
        "default":    "Use a set-based approach for deduplication",
        "python":     "Use set() or dict.fromkeys() for dedup",
        "javascript": "Use [...new Set(arr)] for dedup",
        "typescript": "Use [...new Set(arr)] or Array.from(new Set(arr))",
        "go":         "Use map[T]bool to track seen values",
        "java":       "Use new ArrayList<>(new LinkedHashSet<>(list))",
        "rust":       "Use HashSet to filter duplicates",
        "ruby":       "Use Array#uniq",
        "c":          "Use a hash set to track seen values",
        "cpp":        "Use std::unordered_set to track seen values",
        "php":        "Use array_unique()",
    },
    # -- max-min --
    ("max-min", "builtin-minmax"): {
        "default":    "Use the language's built-in min/max",
        "python":     "Use min() / max() built-ins",
        "javascript": "Use Math.min(...arr) / Math.max(...arr)",
        "typescript": "Use Math.min(...arr) / Math.max(...arr)",
        "go":         "Use slices.Min() / slices.Max() (Go 1.21+) or a single pass",
        "java":       "Use Collections.min() / Collections.max() or Stream.min()",
        "rust":       "Use .iter().min() / .iter().max()",
        "ruby":       "Use Enumerable#min / Enumerable#max",
        "c":          "Use a single-pass loop (no built-in min/max for arrays)",
        "cpp":        "Use std::min_element() / std::max_element()",
        "php":        "Use min() / max()",
    },
    # -- accumulation --
    ("accumulation", "builtin-sum"): {
        "default":    "Use the language's built-in sum/reduce",
        "python":     "Use sum() / math.prod() / functools.reduce()",
        "javascript": "Use Array.prototype.reduce()",
        "typescript": "Use Array.prototype.reduce()",
        "go":         "Use a single-pass loop (no built-in reduce)",
        "java":       "Use stream().reduce() or stream().mapToInt().sum()",
        "rust":       "Use .iter().sum() or .iter().fold()",
        "ruby":       "Use Enumerable#sum or Enumerable#reduce",
        "c":          "Use a single-pass loop",
        "cpp":        "Use std::accumulate() from <numeric>",
        "php":        "Use array_sum() or array_reduce()",
    },
    # -- fibonacci / memoization --
    ("fibonacci", "iterative-fib"): {
        "default":    "Use iterative loop or add memoization",
        "python":     "Use iterative loop or @functools.lru_cache / @functools.cache",
        "javascript": "Use iterative loop or a Map-based memo wrapper",
        "typescript": "Use iterative loop or a Map-based memo wrapper",
        "go":         "Use iterative loop or a map[int]int memo table",
        "java":       "Use iterative loop or a HashMap<Integer,Long> memo",
        "rust":       "Use iterative loop or a HashMap<u64,u64> memo",
        "ruby":       "Use iterative loop or a Hash-based memo",
        "c":          "Use iterative loop or a memo array",
        "cpp":        "Use iterative loop or std::unordered_map memo",
        "php":        "Use iterative loop or a static array memo",
    },
    # -- manual-power --
    ("manual-power", "builtin-pow"): {
        "default":    "Use the built-in power function",
        "python":     "Use pow(base, exp, mod) or ** operator",
        "javascript": "Use Math.pow(base, exp) or ** operator",
        "typescript": "Use Math.pow(base, exp) or ** operator",
        "go":         "Use math.Pow()",
        "java":       "Use Math.pow() or BigInteger.modPow()",
        "rust":       "Use .pow() or .checked_pow()",
        "ruby":       "Use ** operator or Integer#pow",
        "c":          "Use pow() from <math.h>",
        "cpp":        "Use std::pow() from <cmath>",
        "php":        "Use pow() or ** operator",
    },
    # -- manual-gcd --
    ("manual-gcd", "builtin-gcd"): {
        "default":    "Use the language's built-in GCD",
        "python":     "Use math.gcd()",
        "javascript": "Implement Euclidean algorithm (no built-in)",
        "typescript": "Implement Euclidean algorithm (no built-in)",
        "go":         "Use big.Int.GCD() or implement Euclidean algorithm",
        "java":       "Use BigInteger.gcd() or implement Euclidean algorithm",
        "rust":       "Use num::integer::gcd from the num crate",
        "ruby":       "Use Integer#gcd",
        "c":          "Implement Euclidean algorithm or use __gcd()",
        "cpp":        "Use std::gcd() from <numeric> (C++17)",
        "php":        "Implement Euclidean algorithm (no built-in)",
    },
    # -- groupby --
    ("groupby", "defaultdict-groupby"): {
        "default":    "Use a hash-map with default-list pattern for grouping",
        "python":     "Use collections.defaultdict(list) or dict.setdefault()",
        "javascript": "Use Map or reduce() to group by key",
        "typescript": "Use Map<K, V[]> or reduce() to group by key",
        "go":         "Use map[K][]V to group by key",
        "java":       "Use Collectors.groupingBy() with Stream API",
        "rust":       "Use HashMap<K, Vec<V>> or itertools group_by()",
        "ruby":       "Use Enumerable#group_by",
        "c":          "Use a hash table mapping keys to linked lists",
        "cpp":        "Use std::unordered_map<K, std::vector<V>>",
        "php":        "Use array grouping with foreach or array_reduce()",
    },
    # -- nested-lookup --
    ("nested-lookup", "hash-join"): {
        "default":    "Build a hash map from one collection, iterate the other",
        "python":     "Build a dict/set from one collection, iterate the other",
        "javascript": "Build a Map/Set from one collection, iterate the other",
        "typescript": "Build a Map/Set from one collection, iterate the other",
        "go":         "Build a map from one collection, iterate the other",
        "java":       "Build a HashMap/HashSet from one collection, iterate the other",
        "rust":       "Build a HashMap/HashSet from one collection, iterate the other",
        "ruby":       "Build a Hash/Set from one collection, iterate the other",
        "c":          "Build a hash table from one collection, iterate the other",
        "cpp":        "Build an unordered_map/unordered_set from one collection",
        "php":        "Build an associative array from one collection",
    },
    # -- string-reverse --
    ("string-reverse", "builtin-reverse"): {
        "default":    "Use the language's built-in reverse",
        "python":     "Use s[::-1] or ''.join(reversed(s))",
        "javascript": "Use s.split('').reverse().join('')",
        "typescript": "Use s.split('').reverse().join('')",
        "go":         "Use []rune conversion and reverse loop, or strings.Builder",
        "java":       "Use new StringBuilder(s).reverse().toString()",
        "rust":       "Use s.chars().rev().collect::<String>()",
        "ruby":       "Use String#reverse",
        "c":          "Swap characters in-place from both ends",
        "cpp":        "Use std::reverse() from <algorithm>",
        "php":        "Use strrev()",
    },
    # -- matrix-mult --
    ("matrix-mult", "blas-mult"): {
        "default":    "Use an optimized matrix library (100-1000x faster)",
        "python":     "Use numpy.dot() / np.matmul() / @ operator",
        "javascript": "Use a library like mathjs or gpu.js",
        "typescript": "Use a library like mathjs or gpu.js",
        "go":         "Use gonum/mat for optimized matrix operations",
        "java":       "Use Apache Commons Math or EJML",
        "rust":       "Use nalgebra or ndarray crate",
        "ruby":       "Use numo-narray or matrix gem",
        "c":          "Use BLAS/LAPACK (cblas_dgemm)",
        "cpp":        "Use Eigen or BLAS/LAPACK",
        "php":        "Use a math extension or offload to native code",
    },
    # -- busy-wait --
    ("busy-wait", "event-wait"): {
        "default":    "Use an event/condition variable instead of polling",
        "python":     "Use threading.Event / asyncio.Event / select()",
        "javascript": "Use Promise / EventEmitter / await",
        "typescript": "Use Promise / EventEmitter / await",
        "go":         "Use chan / sync.Cond / select {}",
        "java":       "Use CompletableFuture / CountDownLatch / wait()/notify()",
        "rust":       "Use std::sync::Condvar or tokio::sync::Notify",
        "ruby":       "Use ConditionVariable / Queue",
        "c":          "Use pthread_cond_wait() or select()/poll()",
        "cpp":        "Use std::condition_variable or std::future",
        "php":        "Use pcntl_signal or an event loop library",
    },
    # -- regex-in-loop --
    ("regex-in-loop", "precompiled"): {
        "default":    "Compile the regex once outside the loop",
        "python":     "Compile once with re.compile() outside the loop",
        "javascript": "Create new RegExp() once outside the loop",
        "typescript": "Create new RegExp() once outside the loop",
        "go":         "Use regexp.MustCompile() once outside the loop",
        "java":       "Use Pattern.compile() once outside the loop",
        "rust":       "Use Regex::new() once or lazy_static!/once_cell",
        "ruby":       "Define the Regexp literal once outside the loop",
        "c":          "Use regcomp() once outside the loop",
        "cpp":        "Use std::regex once outside the loop",
        "php":        "Store the pattern in a variable; preg functions auto-cache",
    },
    # -- io-in-loop --
    ("io-in-loop", "batch-query"): {
        "default":    "Use WHERE IN / bulk API / batch fetch instead of per-item queries",
        "python":     "Use WHERE IN (...) / executemany() / bulk API",
        "javascript": "Use Promise.all() / bulk fetch / WHERE IN (...)",
        "typescript": "Use Promise.all() / bulk fetch / WHERE IN (...)",
        "go":         "Use batch query with IN clause / bulk API",
        "java":       "Use batch JDBC / JPA @EntityGraph / WHERE IN (...)",
        "rust":       "Use batch query with IN clause / bulk API",
        "ruby":       "Use .where(id: ids) / includes() / preload()",
        "c":          "Use batch query with IN clause",
        "cpp":        "Use batch query with IN clause / bulk API",
        "php":        "Use whereIn() / eager loading / batch query",
    },
    # -- list-prepend --
    ("list-prepend", "deque-ops"): {
        "default":    "Use a deque or append+reverse instead of insert at front",
        "python":     "Use collections.deque for O(1) appendleft/popleft",
        "javascript": "Consider a linked list or reverse after push (Array.shift is O(n))",
        "typescript": "Consider a linked list or reverse after push (Array.shift is O(n))",
        "go":         "Use container/list for O(1) front operations",
        "java":       "Use ArrayDeque or LinkedList for O(1) front operations",
        "rust":       "Use VecDeque for O(1) push_front/pop_front",
        "ruby":       "Use Array#push + Array#reverse, or a linked list",
        "c":          "Use a doubly-linked list for O(1) front operations",
        "cpp":        "Use std::deque for O(1) front operations",
        "php":        "Use SplDoublyLinkedList for O(1) front operations",
    },
    # -- sort-to-select --
    ("sort-to-select", "direct-select"): {
        "default":    "Use min/max for extremes, heap for top-k",
        "python":     "Use min()/max() for extremes, heapq.nsmallest/nlargest for top-k",
        "javascript": "Use Math.min/max for extremes, maintain a small sorted array for top-k",
        "typescript": "Use Math.min/max for extremes, maintain a small sorted array for top-k",
        "go":         "Use a single pass for min/max, container/heap for top-k",
        "java":       "Use Collections.min/max or PriorityQueue for top-k",
        "rust":       "Use .iter().min/max() or BinaryHeap for top-k",
        "ruby":       "Use Enumerable#min/max or min_by/max_by",
        "c":          "Use a single-pass loop for min/max",
        "cpp":        "Use std::min_element/max_element or std::partial_sort",
        "php":        "Use min()/max() for extremes",
    },
    # -- loop-lookup --
    ("loop-lookup", "set-prebuilt"): {
        "default":    "Build a set/dict from the lookup collection once, then use O(1) membership",
        "python":     "Build a set/dict from the lookup collection for O(1) membership",
        "javascript": "Build a Set/Map from the lookup collection for O(1) membership",
        "typescript": "Build a Set/Map from the lookup collection for O(1) membership",
        "go":         "Build a map from the lookup collection for O(1) membership",
        "java":       "Build a HashSet/HashMap from the lookup collection for O(1) membership",
        "rust":       "Build a HashSet/HashMap from the lookup collection for O(1) membership",
        "ruby":       "Build a Set/Hash from the lookup collection for O(1) membership",
        "c":          "Build a hash table from the lookup collection",
        "cpp":        "Build an unordered_set/unordered_map from the lookup collection",
        "php":        "Use array_flip() + isset() for O(1) membership",
    },
    # -- branching-recursion --
    ("branching-recursion", "memoized"): {
        "default":    "Add memoization or convert to iterative DP",
        "python":     "Add @functools.lru_cache / @functools.cache, or convert to iterative DP",
        "javascript": "Add a Map-based memo wrapper, or convert to iterative DP",
        "typescript": "Add a Map-based memo wrapper, or convert to iterative DP",
        "go":         "Add a map[K]V memo table, or convert to iterative DP",
        "java":       "Add a HashMap memo, or convert to iterative DP",
        "rust":       "Add a HashMap memo, or convert to iterative DP",
        "ruby":       "Add a Hash-based memo, or convert to iterative DP",
        "c":          "Add a memo array/hash table, or convert to iterative DP",
        "cpp":        "Add std::unordered_map memo, or convert to iterative DP",
        "php":        "Add a static array memo, or convert to iterative DP",
    },
    # -- quadratic-string --
    ("quadratic-string", "join-parts"): {
        "default":    "Collect parts in a list, then join at the end",
        "python":     "Append parts to a list, then ''.join(parts)",
        "javascript": "Use Array.push() + Array.join()",
        "typescript": "Use Array.push() + Array.join()",
        "go":         "Use strings.Builder for efficient concatenation",
        "java":       "Use StringBuilder.append() + toString()",
        "rust":       "Use String::push_str() or write!() macro",
        "ruby":       "Use Array#join or StringIO",
        "c":          "Pre-allocate buffer and use memcpy()/strncat()",
        "cpp":        "Use std::ostringstream or std::string::reserve() + append()",
        "php":        "Collect parts in an array, then implode()",
    },
    # -- loop-invariant-call --
    ("loop-invariant-call", "hoisted"): {
        "default":    "Move the call before the loop and store the result in a variable",
        "python":     "Hoist the call above the for/while loop and assign to a local variable",
        "javascript": "Move the call before the loop and store in a const",
        "typescript": "Move the call before the loop and store in a const",
        "go":         "Move the call before the for loop and assign to a local variable",
        "java":       "Move the call before the loop and store in a final local variable",
        "rust":       "Move the call before the loop and bind with let",
        "ruby":       "Move the call before the loop and assign to a local variable",
        "c":          "Move the call before the loop and store in a local variable",
        "cpp":        "Move the call before the loop and store in a const local",
        "php":        "Move the call before the loop and assign to a variable",
    },
    # -- sorting (anti-pattern) --
    ("sorting", "manual-sort"): {
        "default":    "Replace manual sort with the language's built-in sort",
        "python":     "Replace with sorted() or list.sort()",
        "javascript": "Replace with Array.prototype.sort()",
        "typescript": "Replace with Array.prototype.sort()",
        "go":         "Replace with sort.Slice() or slices.Sort()",
        "java":       "Replace with Arrays.sort() or Collections.sort()",
        "rust":       "Replace with .sort() or .sort_unstable()",
        "ruby":       "Replace with Array#sort or Array#sort_by",
        "c":          "Replace with qsort() from <stdlib.h>",
        "cpp":        "Replace with std::sort() from <algorithm>",
        "php":        "Replace with sort() / usort()",
    },
    # -- search-sorted (anti-pattern) --
    ("search-sorted", "linear-scan"): {
        "default":    "Use binary search instead of linear scan on sorted data",
        "python":     "Use bisect.bisect_left() instead of scanning the sorted list",
        "javascript": "Implement binary search or use lodash _.sortedIndex()",
        "typescript": "Implement binary search or use lodash _.sortedIndex()",
        "go":         "Use sort.Search() or slices.BinarySearch()",
        "java":       "Use Arrays.binarySearch() or Collections.binarySearch()",
        "rust":       "Use .binary_search() on the sorted slice",
        "ruby":       "Use Array#bsearch on the sorted array",
        "c":          "Use bsearch() from <stdlib.h>",
        "cpp":        "Use std::lower_bound() or std::binary_search()",
        "php":        "Implement binary search instead of linear scan",
    },
    # -- membership (anti-pattern) --
    ("membership", "list-scan"): {
        "default":    "Convert to a set/hash for O(1) lookups instead of scanning a list",
        "python":     "Convert to set() for O(1) membership instead of scanning a list",
        "javascript": "Use a Set or Map for O(1) lookups instead of Array.includes()",
        "typescript": "Use Set<T> or Map<K,V> instead of Array.includes()",
        "go":         "Use map[T]bool for O(1) lookups instead of ranging over a slice",
        "java":       "Use HashSet<T> for O(1) lookups instead of List.contains()",
        "rust":       "Use HashSet<T> for O(1) lookups instead of .contains() on Vec",
        "ruby":       "Use a Set or Hash for O(1) lookups instead of Array#include?",
        "c":          "Use a hash table for O(1) lookups instead of linear scan",
        "cpp":        "Use std::unordered_set instead of std::find on a vector",
        "php":        "Use array_flip() + isset() instead of in_array()",
    },
    # -- string-concat (anti-pattern) --
    ("string-concat", "loop-concat"): {
        "default":    "Collect parts in a list and join once instead of concatenating in a loop",
        "python":     "Use a list and ''.join(parts) instead of += in a loop",
        "javascript": "Use Array.push() + Array.join() instead of += in a loop",
        "typescript": "Use Array.push() + Array.join() instead of += in a loop",
        "go":         "Use strings.Builder instead of += in a loop",
        "java":       "Use StringBuilder instead of += in a loop",
        "rust":       "Use String::push_str() instead of format!() in a loop",
        "ruby":       "Use Array#join or StringIO instead of += in a loop",
        "c":          "Pre-allocate a buffer and use memcpy() instead of strcat() in a loop",
        "cpp":        "Use std::ostringstream or reserve()+append() instead of += in a loop",
        "php":        "Collect parts in an array and implode() instead of .= in a loop",
    },
    # -- unique (anti-pattern) --
    ("unique", "nested-dedup"): {
        "default":    "Use a set-based approach instead of nested-loop deduplication",
        "python":     "Use set() or dict.fromkeys() instead of nested-loop dedup",
        "javascript": "Use [...new Set(arr)] instead of nested-loop dedup",
        "typescript": "Use Array.from(new Set(arr)) instead of nested-loop dedup",
        "go":         "Use map[T]bool to track seen values instead of nested loops",
        "java":       "Use new LinkedHashSet<>(list) instead of nested-loop dedup",
        "rust":       "Use HashSet to filter duplicates instead of nested loops",
        "ruby":       "Use Array#uniq instead of nested-loop dedup",
        "c":          "Use a hash set to track seen values instead of nested loops",
        "cpp":        "Use std::unordered_set instead of nested-loop dedup",
        "php":        "Use array_unique() instead of nested-loop dedup",
    },
    # -- max-min (anti-pattern) --
    ("max-min", "manual-loop"): {
        "default":    "Use the language's built-in min/max instead of manual tracking",
        "python":     "Use min() / max() built-ins instead of manual tracking",
        "javascript": "Use Math.min(...arr) / Math.max(...arr) instead of manual loop",
        "typescript": "Use Math.min(...arr) / Math.max(...arr) instead of manual loop",
        "go":         "Use slices.Min() / slices.Max() (Go 1.21+) instead of manual loop",
        "java":       "Use Collections.min() / Collections.max() instead of manual loop",
        "rust":       "Use .iter().min() / .iter().max() instead of manual loop",
        "ruby":       "Use Enumerable#min / Enumerable#max instead of manual loop",
        "c":          "Consider a macro or inline helper for min/max",
        "cpp":        "Use std::min_element() / std::max_element() instead of manual loop",
        "php":        "Use min() / max() instead of manual loop",
    },
    # -- accumulation (anti-pattern) --
    ("accumulation", "manual-sum"): {
        "default":    "Use the language's built-in sum/reduce instead of a manual accumulator",
        "python":     "Use sum() or functools.reduce() instead of a manual accumulator",
        "javascript": "Use Array.prototype.reduce() instead of a manual accumulator",
        "typescript": "Use Array.prototype.reduce() instead of a manual accumulator",
        "go":         "A for-range loop is idiomatic in Go; ensure clarity",
        "java":       "Use stream().mapToInt().sum() instead of a manual accumulator",
        "rust":       "Use .iter().sum() or .iter().fold() instead of a manual accumulator",
        "ruby":       "Use Enumerable#sum or Enumerable#reduce instead of manual loop",
        "c":          "A for loop is idiomatic in C; ensure clarity",
        "cpp":        "Use std::accumulate() from <numeric> instead of a manual loop",
        "php":        "Use array_sum() or array_reduce() instead of a manual loop",
    },
    # -- fibonacci (anti-pattern) --
    ("fibonacci", "naive-recursive"): {
        "default":    "Add memoization or convert to an iterative approach",
        "python":     "Add @functools.lru_cache or convert to an iterative loop",
        "javascript": "Add a Map-based memo or convert to an iterative loop",
        "typescript": "Add a Map-based memo or convert to an iterative loop",
        "go":         "Add a map[int]int memo table or convert to an iterative loop",
        "java":       "Add a HashMap<Integer,Long> memo or convert to an iterative loop",
        "rust":       "Add a HashMap<u64,u64> memo or convert to an iterative loop",
        "ruby":       "Add a Hash-based memo or convert to an iterative loop",
        "c":          "Add a memo array or convert to an iterative loop",
        "cpp":        "Add std::unordered_map memo or convert to an iterative loop",
        "php":        "Add a static array memo or convert to an iterative loop",
    },
    # -- manual-power (anti-pattern) --
    ("manual-power", "loop-multiply"): {
        "default":    "Use the built-in power function instead of loop multiplication",
        "python":     "Use pow(base, exp, mod) or ** instead of a multiply loop",
        "javascript": "Use Math.pow() or ** operator instead of a multiply loop",
        "typescript": "Use Math.pow() or ** operator instead of a multiply loop",
        "go":         "Use math.Pow() instead of a multiply loop",
        "java":       "Use Math.pow() or BigInteger.modPow() instead of a multiply loop",
        "rust":       "Use .pow() or .checked_pow() instead of a multiply loop",
        "ruby":       "Use ** operator or Integer#pow instead of a multiply loop",
        "c":          "Use pow() from <math.h> instead of a multiply loop",
        "cpp":        "Use std::pow() from <cmath> instead of a multiply loop",
        "php":        "Use pow() or ** operator instead of a multiply loop",
    },
    # -- manual-gcd (anti-pattern) --
    ("manual-gcd", "manual-gcd"): {
        "default":    "Use the language's built-in or standard GCD function",
        "python":     "Use math.gcd() instead of a manual loop",
        "javascript": "Implement a concise Euclidean helper (no built-in)",
        "typescript": "Implement a concise Euclidean helper (no built-in)",
        "go":         "Use big.Int.GCD() or a concise Euclidean helper",
        "java":       "Use BigInteger.gcd() or a concise Euclidean helper",
        "rust":       "Use num::integer::gcd from the num crate",
        "ruby":       "Use Integer#gcd instead of a manual loop",
        "c":          "Use __gcd() or a concise Euclidean helper",
        "cpp":        "Use std::gcd() from <numeric> (C++17)",
        "php":        "Implement a concise Euclidean helper (no built-in)",
    },
    # -- groupby (anti-pattern) --
    ("groupby", "manual-check"): {
        "default":    "Use a defaultdict/hash-map pattern instead of manual key-existence checks",
        "python":     "Use collections.defaultdict(list) or dict.setdefault()",
        "javascript": "Use a Map or reduce() pattern to group by key",
        "typescript": "Use Map<K, V[]> or reduce() pattern to group by key",
        "go":         "Initialize the slice in the map on first access: m[k] = append(m[k], v)",
        "java":       "Use Collectors.groupingBy() or computeIfAbsent()",
        "rust":       "Use entry().or_insert_with(Vec::new) on a HashMap",
        "ruby":       "Use Enumerable#group_by or Hash.new { |h,k| h[k] = [] }",
        "c":          "Use a hash table mapping keys to linked lists",
        "cpp":        "Use std::unordered_map<K, std::vector<V>> with operator[]",
        "php":        "Use $groups[$key][] = $value pattern",
    },
    # -- nested-lookup (anti-pattern) --
    ("nested-lookup", "nested-iteration"): {
        "default":    "Build a hash map from one collection instead of nested iteration",
        "python":     "Build a dict/set from one collection instead of nested for loops",
        "javascript": "Build a Map/Set from one collection instead of nested loops",
        "typescript": "Build a Map/Set from one collection instead of nested loops",
        "go":         "Build a map from one collection instead of nested range loops",
        "java":       "Build a HashMap/HashSet from one collection instead of nested loops",
        "rust":       "Build a HashMap/HashSet from one collection instead of nested loops",
        "ruby":       "Build a Hash/Set from one collection instead of nested each loops",
        "c":          "Build a hash table from one collection instead of nested loops",
        "cpp":        "Build an unordered_map/unordered_set instead of nested loops",
        "php":        "Build an associative array from one collection instead of nested foreach",
    },
    # -- string-reverse (anti-pattern) --
    ("string-reverse", "manual-reverse"): {
        "default":    "Use the language's built-in reverse instead of a manual char loop",
        "python":     "Use s[::-1] instead of a manual char loop",
        "javascript": "Use s.split('').reverse().join('') instead of a manual loop",
        "typescript": "Use s.split('').reverse().join('') instead of a manual loop",
        "go":         "Use []rune conversion + reverse loop, or strings.Builder",
        "java":       "Use new StringBuilder(s).reverse().toString()",
        "rust":       "Use s.chars().rev().collect::<String>()",
        "ruby":       "Use String#reverse instead of a manual loop",
        "c":          "Swap characters in-place from both ends",
        "cpp":        "Use std::reverse() from <algorithm>",
        "php":        "Use strrev() instead of a manual loop",
    },
    # -- matrix-mult (anti-pattern) --
    ("matrix-mult", "naive-triple"): {
        "default":    "Use an optimized matrix library instead of a naive triple loop",
        "python":     "Use numpy.dot() / np.matmul() / @ operator instead of triple loops",
        "javascript": "Use a library like mathjs or gpu.js instead of triple loops",
        "typescript": "Use a library like mathjs or gpu.js instead of triple loops",
        "go":         "Use gonum/mat instead of triple loops",
        "java":       "Use Apache Commons Math or EJML instead of triple loops",
        "rust":       "Use nalgebra or ndarray crate instead of triple loops",
        "ruby":       "Use numo-narray or the matrix gem instead of triple loops",
        "c":          "Use BLAS/LAPACK (cblas_dgemm) instead of triple loops",
        "cpp":        "Use Eigen or BLAS/LAPACK instead of triple loops",
        "php":        "Use a math extension or offload to native code",
    },
    # -- busy-wait (anti-pattern) --
    ("busy-wait", "sleep-loop"): {
        "default":    "Use an event/condition variable instead of sleep-in-loop polling",
        "python":     "Use threading.Event.wait() or asyncio.Event instead of sleep loop",
        "javascript": "Use Promise / await or EventEmitter instead of setTimeout loop",
        "typescript": "Use Promise / await or EventEmitter instead of setTimeout loop",
        "go":         "Use chan / sync.Cond / select {} instead of time.Sleep loop",
        "java":       "Use CountDownLatch / wait()/notify() instead of Thread.sleep loop",
        "rust":       "Use std::sync::Condvar or tokio::sync::Notify instead of sleep loop",
        "ruby":       "Use ConditionVariable / Queue instead of sleep loop",
        "c":          "Use pthread_cond_wait() or select()/poll() instead of sleep loop",
        "cpp":        "Use std::condition_variable instead of sleep loop",
        "php":        "Use an event loop library instead of sleep() loop",
    },
    # -- regex-in-loop (anti-pattern) --
    ("regex-in-loop", "compile-per-iter"): {
        "default":    "Compile the regex once outside the loop instead of per iteration",
        "python":     "Move re.compile() outside the loop and reuse the compiled pattern",
        "javascript": "Create the RegExp once outside the loop",
        "typescript": "Create the RegExp once outside the loop",
        "go":         "Move regexp.MustCompile() outside the loop",
        "java":       "Move Pattern.compile() outside the loop",
        "rust":       "Move Regex::new() outside the loop or use lazy_static!/once_cell",
        "ruby":       "Define the Regexp literal once outside the loop",
        "c":          "Move regcomp() outside the loop",
        "cpp":        "Construct std::regex once outside the loop",
        "php":        "Store the pattern in a variable; preg functions auto-cache",
    },
    # -- io-in-loop (anti-pattern) --
    ("io-in-loop", "loop-query"): {
        "default":    "Use batch query / bulk I/O instead of per-item queries in a loop",
        "python":     "Use WHERE IN (...) / executemany() instead of per-item queries",
        "javascript": "Use Promise.all() / bulk fetch instead of per-item queries",
        "typescript": "Use Promise.all() / bulk fetch instead of per-item queries",
        "go":         "Use a batch query with IN clause instead of per-item queries",
        "java":       "Use batch JDBC / JPA @EntityGraph instead of per-item queries",
        "rust":       "Use a batch query with IN clause instead of per-item queries",
        "ruby":       "Use .where(id: ids) / includes() instead of per-item queries",
        "c":          "Use a batch query with IN clause instead of per-item queries",
        "cpp":        "Use a batch query with IN clause instead of per-item queries",
        "php":        "Use whereIn() / eager loading instead of per-item queries",
    },
    # -- list-prepend (anti-pattern) --
    ("list-prepend", "insert-front"): {
        "default":    "Use a deque or append+reverse instead of insert/remove at front",
        "python":     "Use collections.deque instead of list.insert(0, x)",
        "javascript": "Avoid Array.unshift(); use push + reverse or a linked list",
        "typescript": "Avoid Array.unshift(); use push + reverse or a linked list",
        "go":         "Use container/list instead of prepending to a slice",
        "java":       "Use ArrayDeque instead of ArrayList.add(0, x)",
        "rust":       "Use VecDeque instead of Vec::insert(0, x)",
        "ruby":       "Use push + reverse instead of Array#unshift in a loop",
        "c":          "Use a doubly-linked list instead of memmove-based insert at front",
        "cpp":        "Use std::deque instead of std::vector::insert at begin",
        "php":        "Use SplDoublyLinkedList instead of array_unshift() in a loop",
    },
    # -- sort-to-select (anti-pattern) --
    ("sort-to-select", "full-sort"): {
        "default":    "Use min/max or a heap instead of sorting the entire collection",
        "python":     "Use min()/max() or heapq.nsmallest/nlargest instead of sorted()[0]",
        "javascript": "Use Math.min/max or a partial sort instead of sort()[0]",
        "typescript": "Use Math.min/max or a partial sort instead of sort()[0]",
        "go":         "Use a single pass or container/heap instead of sort.Slice()[0]",
        "java":       "Use Collections.min/max or PriorityQueue instead of sort + get(0)",
        "rust":       "Use .iter().min/max() or BinaryHeap instead of sort + first",
        "ruby":       "Use Enumerable#min/max instead of sort.first",
        "c":          "Use a single-pass loop instead of qsort + subscript",
        "cpp":        "Use std::min_element/max_element or std::partial_sort",
        "php":        "Use min()/max() instead of sort() + $arr[0]",
    },
    # -- loop-lookup (anti-pattern) --
    ("loop-lookup", "method-scan"): {
        "default":    "Pre-build a set/dict for O(1) lookup instead of scanning per iteration",
        "python":     "Build a set/dict once instead of using .index()/.count() in a loop",
        "javascript": "Build a Set/Map once instead of using .indexOf()/.includes() in a loop",
        "typescript": "Build a Set/Map once instead of using .indexOf()/.includes() in a loop",
        "go":         "Build a map once instead of ranging over the slice each iteration",
        "java":       "Build a HashSet/HashMap once instead of .contains()/.indexOf() in a loop",
        "rust":       "Build a HashSet/HashMap once instead of .contains()/.position() in a loop",
        "ruby":       "Build a Set/Hash once instead of .include?/.index in a loop",
        "c":          "Build a hash table once instead of linear search per iteration",
        "cpp":        "Build an unordered_set/map once instead of std::find in a loop",
        "php":        "Use array_flip()+isset() instead of in_array() in a loop",
    },
    # -- branching-recursion (anti-pattern) --
    ("branching-recursion", "naive-branching"): {
        "default":    "Add memoization or convert to iterative DP",
        "python":     "Add @functools.lru_cache / @functools.cache or convert to iterative DP",
        "javascript": "Add a Map-based memo wrapper or convert to iterative DP",
        "typescript": "Add a Map-based memo wrapper or convert to iterative DP",
        "go":         "Add a map[K]V memo table or convert to iterative DP",
        "java":       "Add a HashMap memo or convert to iterative DP",
        "rust":       "Add a HashMap memo or convert to iterative DP",
        "ruby":       "Add a Hash-based memo or convert to iterative DP",
        "c":          "Add a memo array/hash table or convert to iterative DP",
        "cpp":        "Add std::unordered_map memo or convert to iterative DP",
        "php":        "Add a static array memo or convert to iterative DP",
    },
    # -- quadratic-string (anti-pattern) --
    ("quadratic-string", "augment-concat"): {
        "default":    "Collect parts in a list and join instead of str += in a loop",
        "python":     "Use a list and ''.join(parts) instead of str += in a loop",
        "javascript": "Use Array.push() + Array.join() instead of str += in a loop",
        "typescript": "Use Array.push() + Array.join() instead of str += in a loop",
        "go":         "Use strings.Builder instead of str += in a loop",
        "java":       "Use StringBuilder.append() instead of str += in a loop",
        "rust":       "Use String::push_str() or write!() instead of format!() in a loop",
        "ruby":       "Use Array#join or StringIO instead of str += in a loop",
        "c":          "Pre-allocate a buffer and use memcpy() instead of strcat() in a loop",
        "cpp":        "Use std::ostringstream or reserve()+append() instead of += in a loop",
        "php":        "Collect parts in an array and implode() instead of .= in a loop",
    },
    # -- loop-invariant-call (anti-pattern) --
    ("loop-invariant-call", "repeated-call"): {
        "default":    "Hoist the invariant call before the loop",
        "python":     "Move the invariant call above the for/while loop and cache the result",
        "javascript": "Move the invariant call before the loop and store in a const",
        "typescript": "Move the invariant call before the loop and store in a const",
        "go":         "Move the invariant call before the for loop and assign to a local",
        "java":       "Move the invariant call before the loop and store in a final local",
        "rust":       "Move the invariant call before the loop and bind with let",
        "ruby":       "Move the invariant call before the loop and assign to a local",
        "c":          "Move the invariant call before the loop and store in a local variable",
        "cpp":        "Move the invariant call before the loop and store in a const local",
        "php":        "Move the invariant call before the loop and assign to a variable",
    },
}


def get_tip(task_id: str, way_id: str, language: str | None = None) -> str:
    """Return the best tip for a task/way/language combination.

    Lookup order:
      1. ``_LANGUAGE_TIPS[(task_id, way_id)][language]``
      2. ``_LANGUAGE_TIPS[(task_id, way_id)]["default"]``
      3. The static ``tip`` field on the way entry in CATALOG
      4. ``""``
    """
    lang_key = (language or "").lower().strip()
    # Normalize common aliases
    if lang_key in ("ts", "tsx"):
        lang_key = "typescript"
    elif lang_key in ("js", "jsx"):
        lang_key = "javascript"
    elif lang_key in ("c++",):
        lang_key = "cpp"

    tips = _LANGUAGE_TIPS.get((task_id, way_id))
    if tips:
        if lang_key and lang_key in tips:
            return tips[lang_key]
        if "default" in tips:
            return tips["default"]

    # Fall back to static tip on the way entry
    way = get_way(task_id, way_id)
    if way and way.get("tip"):
        return way["tip"]
    return ""
