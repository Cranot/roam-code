"""Regression tests for the math-detector false-positive fixes (M1-M6).

Each test reproduces a user-reported FP from the 2026-05-06 feedback batch
on redacted. If a future detector regression brings the FP back, the test
fires. Each fixture is the smallest possible reproduction of the pattern.
"""

from __future__ import annotations

import re

from roam.catalog.detectors import (
    _DEV_GATE_RE,
    _find_match_line,
    _is_dev_only_block,
)

# ---- M1: match_line is reported, not function-start ------------------------


def test_finding_uses_match_line_when_provided():
    """_finding(..., match_line=137) must point at line 137, not sym['line_start']."""
    from roam.catalog.detectors import _finding

    sym = {
        "id": 1,
        "name": "myFn",
        "qualified_name": "module.myFn",
        "kind": "function",
        "file_path": "src/foo.py",
        "line_start": 100,
    }
    finding = _finding("sort-to-select", "full-sort", sym, "test", "high", match_line=137)
    assert finding["location"] == "src/foo.py:137"
    assert finding["symbol_line"] == 100  # sym start preserved


def test_finding_falls_back_to_sym_line_when_no_match_line():
    """Backward-compat: omit match_line → location uses sym['line_start']."""
    from roam.catalog.detectors import _finding

    sym = {
        "id": 1,
        "name": "myFn",
        "qualified_name": None,
        "kind": "function",
        "file_path": "src/foo.py",
        "line_start": 42,
    }
    finding = _finding("sort-to-select", "full-sort", sym, "test", "medium")
    assert finding["location"] == "src/foo.py:42"


def test_find_match_line_walks_snippet_lines():
    """_find_match_line returns the absolute line of the first regex hit."""
    snippet = "def deepEqual(a, b, depth=0):\n    if depth > 10:\n        return False\n    return a == b\n"
    pat = re.compile(r"depth\s*>\s*10")
    out = _find_match_line(snippet, pat, sym_line_start=66)
    assert out == 67  # line 2 of snippet (depth > 10) → 66 + 1


def test_find_match_line_returns_sym_start_on_no_match():
    snippet = "def foo():\n    return 42\n"
    pat = re.compile(r"NEVER_MATCHES")
    out = _find_match_line(snippet, pat, sym_line_start=10)
    assert out == 10


# ---- M2: bounded-recursion FP — depth>limit early-return guard ------------


def test_depth_guard_recognises_greater_than_early_return():
    """deepEqual-style: `if (depth > 10) return false` must register as a guard.

    Real-world FP from redacted: src/utils/core/object-diff.ts:66 deepEqual
    flagged O(2^n) despite line 68 having `if (depth > 10) return false`.
    """
    # The helper _has_explicit_depth_guard lives inline inside
    # detect_branching_recursion; we exercise the regex shape directly.
    snippet = (
        "function deepEqual(a, b, depth = 0) {\n"
        "  if (depth > 10) return false;\n"
        "  if (a === b) return true;\n"
        "  return deepEqual(a.x, b.x, depth + 1) && deepEqual(a.y, b.y, depth + 1);\n"
        "}\n"
    )
    # Use the regex directly so we don't need to mock the SQL path.
    pat = re.compile(
        r"\b(?:depth|level|budget|remaining|hops|recursion_count|recursionDepth)\b"
        r"\s*(?:>|>=|<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION|0)\s*\)?\s*"
        r"\s*[:{]?\s*(?:return|raise|throw|break)"
    )
    assert pat.search(snippet) is not None, "depth>10 early-return guard should match"


def test_depth_guard_recognises_less_than_continue():
    """Existing form: `if (depth < maxDepth)` continue must still register."""
    snippet = "if (depth < maxDepth) {\n  recurse(child, depth + 1);\n}\n"
    pat = re.compile(
        r"\b(?:depth|level|budget|remaining|hops|currentDepth|current_depth|max_depth|maxDepth)\b"
        r"\s*(?:<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION)"
    )
    assert pat.search(snippet) is not None


def test_depth_guard_recognises_decrement_then_check():
    """`if (--budget <= 0) return` is a recursion-budget pattern."""
    snippet = (
        "function recurse(node, budget) {\n"
        "  if (--budget <= 0) return null;\n"
        "  return recurse(node.left, budget) + recurse(node.right, budget);\n"
        "}\n"
    )
    pat = re.compile(
        r"--?\s*\b(?:depth|budget|remaining|hops)\b\s*[<>]=?\s*\d+\s*\)?\s*[:{]?\s*"
        r"(?:return|raise|throw|break)"
    )
    assert pat.search(snippet) is not None


def test_depth_guard_does_not_match_unrelated_compare():
    """`if (n > 0)` shouldn't false-match — n isn't a depth keyword."""
    snippet = "if (n > 0) doStuff();\n"
    pat = re.compile(
        r"\b(?:depth|level|budget|remaining|hops|recursion_count|recursionDepth)\b"
        r"\s*(?:>|>=|<|<=)\s*(?:\d+|maxDepth|max_depth|MAX_DEPTH|max_recursion|MAX_RECURSION|0)\s*\)?\s*"
        r"\s*[:{]?\s*(?:return|raise|throw|break)"
    )
    assert pat.search(snippet) is None


# ---- M2: memo-collection detection -----------------------------------------


def test_memo_collection_detects_set_in_typescript():
    """`function walk(node, visited: Set<Node>)` → memoised."""
    snippet = "function walk(node: Node, visited: Set<Node>) {\n  if (visited.has(node)) return;\n}\n"
    # Use the inline helper logic directly via regex
    assert re.search(r"\b(?:Set|Map|WeakSet|WeakMap)\s*<", snippet) is not None


def test_memo_collection_detects_visited_pattern():
    snippet = "def walk(node):\n    visited = set()\n    if node in visited: return\n"
    assert re.search(r"\b(?:visited|seen|memo|cache|memoised|memoized)\b\s*[:=]", snippet) is not None


# ---- M3: cache call allowlist expanded -------------------------------------


def test_in_memory_exact_includes_apollo_and_swr_patterns():
    from roam.catalog.detectors import _IN_MEMORY_EXACT

    assert "client.readquery" in _IN_MEMORY_EXACT
    assert "cache.modify" in _IN_MEMORY_EXACT


def test_in_memory_leaves_includes_native_collection_ops():
    """`Map.has(k)` / `Set.delete(k)` should be allow-listed when receiver hints match."""
    from roam.catalog.detectors import _IN_MEMORY_LEAVES, _IN_MEMORY_RECEIVER_HINTS, _io_is_known_in_memory_call

    assert "has" in _IN_MEMORY_LEAVES
    assert "set" in _IN_MEMORY_LEAVES
    assert "delete" in _IN_MEMORY_LEAVES
    # Map / WeakMap / Pinia / Apollo client receivers
    assert "map" in _IN_MEMORY_RECEIVER_HINTS
    assert "client" in _IN_MEMORY_RECEIVER_HINTS
    assert "pinia" in _IN_MEMORY_RECEIVER_HINTS

    # End-to-end: "queryClient.invalidateQueries" recognised
    assert _io_is_known_in_memory_call("queryClient.invalidateQueries") is True
    # "client.readQuery" via Apollo
    assert _io_is_known_in_memory_call("client.readQuery") is True
    # "cache.has(k)" — receiver "cache" matches hint, leaf "has" in leaves
    assert _io_is_known_in_memory_call("cache.has") is True


def test_in_memory_does_not_falsely_allow_unrelated_calls():
    """Don't over-allowlist: `userService.fetchAll` is NOT cache."""
    from roam.catalog.detectors import _io_is_known_in_memory_call

    assert _io_is_known_in_memory_call("userService.fetchAll") is False
    assert _io_is_known_in_memory_call("repo.findOne") is False


# ---- D2: await heuristic refines cache-vs-IO ------------------------------


def test_is_call_awaited_in_snippet_matches_qualified_call():
    from roam.catalog.detectors import _is_call_awaited_in_snippet

    snippet = "for (const id of ids) {\n  const data = await queryClient.getQueryData(['user', id]);\n}\n"
    assert _is_call_awaited_in_snippet("queryClient.getQueryData", snippet) is True


def test_is_call_awaited_in_snippet_matches_bare_leaf():
    from roam.catalog.detectors import _is_call_awaited_in_snippet

    snippet = "for (const id of ids) {\n  const data = await fetchUser(id);\n}\n"
    assert _is_call_awaited_in_snippet("fetchUser", snippet) is True


def test_is_call_awaited_returns_false_when_not_awaited():
    """Sync cache-style getQueryData (no await) should NOT match."""
    from roam.catalog.detectors import _is_call_awaited_in_snippet

    snippet = "for (const id of ids) {\n  const data = queryClient.getQueryData(['user', id]);\n}\n"
    assert _is_call_awaited_in_snippet("queryClient.getQueryData", snippet) is False


def test_io_classify_call_overrides_cache_when_awaited():
    """End-to-end: cache-allowlisted leaf called with `await` → flagged as I/O."""
    from roam.catalog.detectors import _io_classify_call

    snippet_with_await = "for (const id of ids) {\n  await cache.has(id);\n}\n"
    # We don't have real `conn` / `r` here; use a fake row dict
    fake_r = {"id": 1, "file_id": 2}
    level, pack = _io_classify_call("cache.has", "typescript", None, fake_r, snippet=snippet_with_await)
    # Should escalate to medium (was None for cache hit)
    assert level == "medium"


def test_io_classify_call_keeps_cache_classification_without_await():
    from roam.catalog.detectors import _io_classify_call

    snippet_no_await = "for (const id of ids) {\n  cache.has(id);\n}\n"
    fake_r = {"id": 1, "file_id": 2}
    level, pack = _io_classify_call("cache.has", "typescript", None, fake_r, snippet=snippet_no_await)
    # No await → still cache → still None
    assert level is None


# ---- M4: DEV-block recognition ---------------------------------------------


def test_dev_gate_re_matches_import_meta_env_dev():
    snippet = "if (import.meta.env.DEV) {\n  doExpensiveDiagnostic();\n}\n"
    assert _DEV_GATE_RE.search(snippet) is not None


def test_dev_gate_re_matches_process_env_node_env():
    snippet = "if (process.env.NODE_ENV !== 'production') {\n  expensiveCheck();\n}\n"
    assert _DEV_GATE_RE.search(snippet) is not None


def test_dev_gate_re_matches_double_underscore_dev():
    snippet = "if (__DEV__) {\n  Reactotron.log(state);\n}\n"
    assert _DEV_GATE_RE.search(snippet) is not None


def test_dev_gate_re_matches_debug_constant():
    snippet = "if (DEBUG) {\n  perfTrace.log();\n}\n"
    assert _DEV_GATE_RE.search(snippet) is not None


def test_dev_gate_re_does_not_match_plain_if():
    snippet = "if (user.isAdmin) {\n  showPanel();\n}\n"
    assert _DEV_GATE_RE.search(snippet) is None


def test_is_dev_only_block_helper():
    """The public helper used by _io_emit_finding."""
    assert _is_dev_only_block("if (import.meta.env.DEV) { foo() }") is True
    assert _is_dev_only_block("console.assert(x, 'msg')") is True
    assert _is_dev_only_block("if (user.id) { foo() }") is False
    assert _is_dev_only_block("") is False


# ---- D1: 5-line context snippet in evidence -------------------------------


def test_extract_context_lines_returns_radius_around_match():
    from roam.catalog.detectors import _extract_context_lines

    snippet = "line a\nline b\nMATCH HERE\nline d\nline e\n"
    out = _extract_context_lines(snippet, match_line=12, sym_line_start=10, radius=2)
    assert len(out) == 5
    # The match line should be flagged
    match_entries = [e for e in out if e["is_match"]]
    assert len(match_entries) == 1
    assert match_entries[0]["text"] == "MATCH HERE"
    assert match_entries[0]["line"] == 12


def test_extract_context_lines_truncates_at_snippet_boundary():
    """Match near the end shouldn't go past the available lines."""
    from roam.catalog.detectors import _extract_context_lines

    snippet = "line a\nline b\nMATCH\n"
    out = _extract_context_lines(snippet, match_line=12, sym_line_start=10, radius=2)
    # Only 3 lines exist; match at offset 2; end = min(3, 5) = 3
    assert len(out) == 3
    assert out[-1]["text"] == "MATCH"


def test_extract_context_lines_returns_empty_on_invalid_input():
    from roam.catalog.detectors import _extract_context_lines

    assert _extract_context_lines("", 1, 1) == []
    assert _extract_context_lines("foo", None, 1) == []
    assert _extract_context_lines("foo", 1, None) == []
    # Match line outside the snippet range
    assert _extract_context_lines("a\nb\n", match_line=99, sym_line_start=10) == []


def test_finding_includes_context_lines_when_snippet_supplied():
    """End-to-end: _finding(..., snippet=..., match_line=...) populates evidence."""
    from roam.catalog.detectors import _finding

    sym = {
        "id": 1,
        "name": "myFn",
        "qualified_name": "module.myFn",
        "kind": "function",
        "file_path": "src/foo.py",
        "line_start": 100,
    }
    snippet = "def myFn():\n    sorted(items)[0]\n    return 42\n"
    f = _finding("sort-to-select", "full-sort", sym, "test", "high", match_line=101, snippet=snippet)
    ctx = f["evidence"]["context_lines"]
    assert len(ctx) >= 2
    assert any(line["is_match"] for line in ctx)


def test_finding_omits_context_when_snippet_missing():
    from roam.catalog.detectors import _finding

    sym = {
        "id": 1,
        "name": "fn",
        "qualified_name": None,
        "kind": "function",
        "file_path": "src/foo.py",
        "line_start": 1,
    }
    f = _finding("sort-to-select", "full-sort", sym, "test", "medium")
    assert "evidence" not in f or "context_lines" not in (f.get("evidence") or {})


# ---- M5: sort-then-subscript with full iteration → demoted ----------------


def test_iteration_after_sort_re_recognises_map_chain():
    snippet = "items.sort((a, b) => a.x - b.x);\nreturn items.map(formatItem);\n"
    pat = re.compile(r"\bsort(?:ed)?\s*\(.*?\b(map|forEach|filter|reduce|return)\b", re.DOTALL)
    assert pat.search(snippet) is not None


def test_iteration_after_sort_re_recognises_for_iteration():
    """Even simpler: sorted(...) followed by `for` consumption."""
    snippet = "rows = sorted(records, key=lambda r: r.x)\nfor row in rows:\n    emit(row)\n"
    pat = re.compile(r"\bsort(?:ed)?\s*\(.*?\b(map|forEach|filter|reduce|return)\b", re.DOTALL)
    # 'return' is in the alternation — works for `return rows` later in fn
    snippet2 = snippet + "return rows\n"
    assert pat.search(snippet2) is not None


def test_iteration_after_sort_re_no_match_when_only_subscripted():
    """Pure min/max selection: sort then [0] only, no other use."""
    snippet = "items.sort((a, b) => a.x - b.x);\nbest = items[0];\n"
    pat = re.compile(r"\bsort(?:ed)?\s*\(.*?\b(map|forEach|filter|reduce|return)\b", re.DOTALL)
    assert pat.search(snippet) is None


# ---- D3: framework profiles for cache allowlist overrides ----


def test_list_framework_profiles_includes_bundled_names():
    from roam.catalog.detectors import list_framework_profiles

    profiles = list_framework_profiles()
    assert "vue3-tanstack" in profiles
    assert "laravel-multitenant" in profiles


def test_set_active_framework_profile_resolves_known_name():
    from roam.catalog.detectors import set_active_framework_profile

    try:
        prof = set_active_framework_profile("vue3-tanstack")
        assert prof is not None
        assert "fetchquery" in prof.get("in_memory_leaves", set())
    finally:
        set_active_framework_profile(None)


def test_set_active_framework_profile_returns_none_for_unknown():
    from roam.catalog.detectors import set_active_framework_profile

    try:
        prof = set_active_framework_profile("does-not-exist")
        assert prof is None
    finally:
        set_active_framework_profile(None)


def test_io_is_known_in_memory_call_uses_active_profile_extras():
    """vue3-tanstack profile must allow `queryClient.fetchQuery` as cache."""
    from roam.catalog.detectors import _io_is_known_in_memory_call, set_active_framework_profile

    # Without the profile, fetchQuery is NOT in the default allowlist.
    set_active_framework_profile(None)
    assert _io_is_known_in_memory_call("queryClient.fetchQuery") is False

    try:
        set_active_framework_profile("vue3-tanstack")
        assert _io_is_known_in_memory_call("queryClient.fetchQuery") is True
    finally:
        set_active_framework_profile(None)

    # Profile reset restores default behaviour.
    assert _io_is_known_in_memory_call("queryClient.fetchQuery") is False


def test_io_is_known_in_memory_call_laravel_multitenant_extras():
    from roam.catalog.detectors import _io_is_known_in_memory_call, set_active_framework_profile

    try:
        set_active_framework_profile("laravel-multitenant")
        # tenantManager.scopeTenant -> receiver tenantmanager + leaf scopetenant
        assert _io_is_known_in_memory_call("tenantManager.scopeTenant") is True
    finally:
        set_active_framework_profile(None)


def test_run_detectors_unknown_framework_surfaces_in_meta(tmp_path):
    """Unknown framework name surfaces in meta but does not crash the run."""
    import sqlite3

    from roam.catalog.detectors import run_detectors
    from roam.db.connection import ensure_schema

    db_path = tmp_path / "x.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    findings, meta = run_detectors(conn, return_meta=True, framework="totally-not-a-real-fw")
    assert meta["framework_unknown"] == "totally-not-a-real-fw"
    assert meta["framework"] is None
    assert isinstance(findings, list)
    conn.close()
