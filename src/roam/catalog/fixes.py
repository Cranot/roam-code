"""Actionable fix templates for algorithm findings."""

from __future__ import annotations


_LANG_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
}


_TASK_GENERIC_FIX = {
    "sorting": "Replace manual nested loops with the language built-in sort.",
    "search-sorted": "Use binary search APIs instead of linear scan on sorted data.",
    "membership": "Prebuild a set/hash once, then do O(1) membership checks.",
    "string-concat": "Accumulate pieces and join once after the loop.",
    "unique": "Use set/hash dedup instead of nested loop checks.",
    "max-min": "Use built-in max/min helpers unless custom tie-breaking is required.",
    "accumulation": "Use sum/reduce style primitives when no side effects are needed.",
    "manual-power": "Use pow/exponentiation operator instead of repeated multiplication.",
    "manual-gcd": "Use math/library gcd implementation.",
    "fibonacci": "Use iterative DP or memoization to avoid exponential recursion.",
    "nested-lookup": "Build a hash map for one side, then join in one pass.",
    "groupby": "Use grouping helpers (defaultdict/groupingBy/collectors).",
    "string-reverse": "Use built-in reverse/slice helpers.",
    "matrix-mult": "Use vectorized/BLAS-backed matrix multiplication APIs.",
    "busy-wait": "Replace sleep polling loops with event/condition synchronization.",
    "regex-in-loop": "Compile regex once before the loop and reuse it.",
    "io-in-loop": "Batch I/O outside the loop and map results by key.",
    "list-prepend": "Use deque or append-then-reverse instead of front insert/remove.",
    "sort-to-select": "Use min/max or top-k heap selection without full sort.",
    "loop-lookup": "Prebuild lookup set/dict outside loop.",
    "branching-recursion": "Add memoization cache or convert to iterative DP.",
    "quadratic-string": "Use builder/list accumulation + final join.",
    "loop-invariant-call": "Move loop-invariant call outside the loop and reuse result.",
}


_TEMPLATES = {
    ("io-in-loop", "python"): (
        "ids = [item.id for item in items]\n"
        "rows = fetch_many(ids)\n"
        "by_id = {r.id: r for r in rows}\n"
        "for item in items:\n"
        "    item.data = by_id.get(item.id)"
    ),
    ("io-in-loop", "javascript"): (
        "const ids = items.map(x => x.id);\n"
        "const rows = await repo.findMany({ where: { id: { in: ids } } });\n"
        "const byId = new Map(rows.map(r => [r.id, r]));"
    ),
    ("io-in-loop", "typescript"): (
        "const ids = items.map(x => x.id);\n"
        "const rows = await repo.findMany({ where: { id: { in: ids } } });\n"
        "const byId = new Map(rows.map(r => [r.id, r]));"
    ),
    ("io-in-loop", "ruby"): (
        "users = User.where(id: ids).includes(:profile)\n"
        "by_id = users.index_by(&:id)"
    ),
    ("io-in-loop", "php"): (
        "$rows = User::whereIn('id', $ids)->get()->keyBy('id');"
    ),
    ("io-in-loop", "java"): (
        "Map<Long, User> byId = repository.findAllById(ids).stream()\n"
        "    .collect(Collectors.toMap(User::getId, u -> u));"
    ),
    ("loop-lookup", "python"): (
        "lookup = set(blacklist)\n"
        "for item in items:\n"
        "    if item in lookup:\n"
        "        ..."
    ),
    ("sort-to-select", "python"): (
        "best = min(items)\n"
        "top_k = heapq.nsmallest(k, items)"
    ),
    ("list-prepend", "python"): (
        "from collections import deque\n"
        "out = deque()\n"
        "for v in vals:\n"
        "    out.appendleft(v)"
    ),
    ("regex-in-loop", "python"): (
        "pat = re.compile(pattern)\n"
        "for line in lines:\n"
        "    m = pat.search(line)"
    ),
    ("branching-recursion", "python"): (
        "@functools.lru_cache(maxsize=None)\n"
        "def solve(n):\n"
        "    ..."
    ),
    ("quadratic-string", "python"): (
        "parts = []\n"
        "for x in values:\n"
        "    parts.append(render(x))\n"
        "out = ''.join(parts)"
    ),
    ("loop-invariant-call", "python"): (
        "cfg = get_config()\n"
        "for item in items:\n"
        "    use(item, cfg)"
    ),
}


def _norm_language(language: str | None) -> str:
    if not language:
        return ""
    norm = language.lower().strip()
    return _LANG_ALIASES.get(norm, norm)


def get_fix(task_id: str, language: str | None = None) -> str:
    """Return an actionable fix template for a task and language."""
    lang = _norm_language(language)
    if lang:
        tpl = _TEMPLATES.get((task_id, lang))
        if tpl:
            return tpl
    return _TASK_GENERIC_FIX.get(task_id, "")

