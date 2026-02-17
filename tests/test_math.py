"""Tests for roam math -- algorithmic improvement detection.

Covers: catalog structure, AST signal extraction, detector functions,
CLI text/JSON output, and filtering options.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output, assert_json_envelope


# ============================================================================
# Catalog tests
# ============================================================================

class TestCatalog:
    """Validate the universal catalog data structure."""

    def test_catalog_has_entries(self):
        from roam.catalog.tasks import CATALOG
        assert len(CATALOG) == 23, f"Expected 23 tasks, got {len(CATALOG)}"

    def test_all_tasks_have_required_fields(self):
        from roam.catalog.tasks import CATALOG
        for task_id, task in CATALOG.items():
            assert "name" in task, f"{task_id} missing name"
            assert "category" in task, f"{task_id} missing category"
            assert "ways" in task, f"{task_id} missing ways"
            assert len(task["ways"]) >= 2, f"{task_id} has fewer than 2 ways"

    def test_all_ways_have_required_fields(self):
        from roam.catalog.tasks import CATALOG
        for task_id, task in CATALOG.items():
            for way in task["ways"]:
                for key in ("id", "name", "time", "space", "rank", "tip"):
                    assert key in way, f"{task_id}/{way.get('id','?')} missing {key}"

    def test_each_task_has_rank_1(self):
        from roam.catalog.tasks import CATALOG
        for task_id, task in CATALOG.items():
            ranks = [w["rank"] for w in task["ways"]]
            assert 1 in ranks, f"{task_id} has no rank-1 way"

    def test_categories_are_valid(self):
        from roam.catalog.tasks import CATALOG
        valid = {"searching", "ordering", "collections", "string",
                 "math", "concurrency"}
        for task_id, task in CATALOG.items():
            assert task["category"] in valid, (
                f"{task_id} has invalid category: {task['category']}"
            )

    def test_kinds_are_valid(self):
        from roam.catalog.tasks import CATALOG
        valid_kinds = {"algorithm", "idiom"}
        for task_id, task in CATALOG.items():
            assert "kind" in task, f"{task_id} missing kind"
            assert task["kind"] in valid_kinds, (
                f"{task_id} has invalid kind: {task['kind']}"
            )

    def test_get_task(self):
        from roam.catalog.tasks import get_task
        task = get_task("sorting")
        assert task is not None
        assert task["name"] == "Sorting"

    def test_get_task_missing(self):
        from roam.catalog.tasks import get_task
        assert get_task("nonexistent") is None

    def test_get_way(self):
        from roam.catalog.tasks import get_way
        way = get_way("sorting", "builtin-sort")
        assert way is not None
        assert way["rank"] == 1

    def test_best_way(self):
        from roam.catalog.tasks import best_way
        bw = best_way("sorting")
        assert bw is not None
        assert bw["rank"] == 1
        assert bw["id"] == "builtin-sort"


# ============================================================================
# AST signal extraction tests
# ============================================================================

class TestMathSignals:
    """Test _extract_math_signals on known AST patterns."""

    def _parse_python(self, code):
        """Parse Python code and return (tree, source_bytes)."""
        try:
            import tree_sitter_language_pack as pack
        except ImportError:
            pytest.skip("tree-sitter-language-pack not installed")
        parser = pack.get_parser("python")
        source = code.encode("utf-8")
        tree = parser.parse(source)
        return tree, source

    def _find_func(self, tree, source, name):
        """Find a function node by name."""
        from roam.index.complexity import _FUNCTION_NODES
        root = tree.root_node
        def _search(node):
            if node.type in _FUNCTION_NODES:
                for child in node.children:
                    if child.type == "identifier":
                        fname = source[child.start_byte:child.end_byte].decode()
                        if fname == name:
                            return node
            for child in node.children:
                found = _search(child)
                if found:
                    return found
            return None
        return _search(root)

    def test_simple_loop(self):
        from roam.index.complexity import _extract_math_signals
        code = "def foo(items):\n    for x in items:\n        print(x)\n"
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "foo")
        assert func is not None
        sig = _extract_math_signals(func, src, "foo")
        assert sig["loop_depth"] == 1
        assert sig["has_nested_loops"] == 0

    def test_nested_loops(self):
        from roam.index.complexity import _extract_math_signals
        code = (
            "def bubble_sort(arr):\n"
            "    for i in range(len(arr)):\n"
            "        for j in range(len(arr) - 1):\n"
            "            if arr[j] > arr[j+1]:\n"
            "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
        )
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "bubble_sort")
        assert func is not None
        sig = _extract_math_signals(func, src, "bubble_sort")
        assert sig["loop_depth"] >= 2
        assert sig["has_nested_loops"] == 1
        assert sig["subscript_in_loops"] == 1
        assert sig["loop_with_compare"] == 1

    def test_self_call(self):
        from roam.index.complexity import _extract_math_signals
        code = (
            "def fib(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fib(n-1) + fib(n-2)\n"
        )
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "fib")
        assert func is not None
        sig = _extract_math_signals(func, src, "fib")
        assert sig["has_self_call"] == 1

    def test_accumulator_in_loop(self):
        from roam.index.complexity import _extract_math_signals
        code = (
            "def manual_sum(items):\n"
            "    total = 0\n"
            "    for x in items:\n"
            "        total += x\n"
            "    return total\n"
        )
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "manual_sum")
        assert func is not None
        sig = _extract_math_signals(func, src, "manual_sum")
        assert sig["loop_with_accumulator"] == 1
        assert sig["loop_depth"] >= 1

    def test_calls_in_loops(self):
        from roam.index.complexity import _extract_math_signals
        code = (
            "def poll(event):\n"
            "    while True:\n"
            "        sleep(1)\n"
            "        if event.is_set():\n"
            "            break\n"
        )
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "poll")
        assert func is not None
        sig = _extract_math_signals(func, src, "poll")
        assert "sleep" in sig["calls_in_loops"]

    def test_no_loops(self):
        from roam.index.complexity import _extract_math_signals
        code = "def add(a, b):\n    return a + b\n"
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "add")
        assert func is not None
        sig = _extract_math_signals(func, src, "add")
        assert sig["loop_depth"] == 0
        assert sig["has_nested_loops"] == 0
        assert sig["calls_in_loops"] == []


# ============================================================================
# Integration: math_signals stored in DB after indexing
# ============================================================================

class TestMathSignalsDB:
    """Verify math_signals table is populated after indexing."""

    def test_math_signals_populated(self, project_factory):
        """After indexing a project with loops, math_signals should have rows."""
        proj = project_factory({
            "algo.py": (
                "def bubble_sort(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
            ),
        })
        from roam.db.connection import open_db
        with open_db(readonly=True, project_root=proj) as conn:
            rows = conn.execute(
                "SELECT ms.* FROM math_signals ms "
                "JOIN symbols s ON ms.symbol_id = s.id "
                "WHERE s.name = 'bubble_sort'"
            ).fetchall()
            assert len(rows) >= 1
            row = rows[0]
            assert row["has_nested_loops"] == 1
            assert row["loop_depth"] >= 2

    def test_math_signals_self_call(self, project_factory):
        """Recursive function should have has_self_call = 1."""
        proj = project_factory({
            "fib.py": (
                "def fib(n):\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    return fib(n-1) + fib(n-2)\n"
            ),
        })
        from roam.db.connection import open_db
        with open_db(readonly=True, project_root=proj) as conn:
            rows = conn.execute(
                "SELECT ms.* FROM math_signals ms "
                "JOIN symbols s ON ms.symbol_id = s.id "
                "WHERE s.name = 'fib'"
            ).fetchall()
            assert len(rows) >= 1
            assert rows[0]["has_self_call"] == 1


# ============================================================================
# CLI command tests
# ============================================================================

class TestMathCLI:
    """Tests for `roam math` CLI output."""

    def test_math_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam math should run without error on indexed project."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project)
        assert result.exit_code == 0, f"math failed: {result.output}"

    def test_math_verdict(self, cli_runner, indexed_project, monkeypatch):
        """roam math should output a VERDICT line."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_math_json_envelope(self, cli_runner, indexed_project, monkeypatch):
        """roam --json math should return valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "math")
        assert_json_envelope(data, "math")
        assert "verdict" in data["summary"]
        assert "total" in data["summary"]
        assert "findings" in data

    def test_math_json_findings_structure(self, cli_runner, indexed_project, monkeypatch):
        """Each finding in JSON should have required fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "math")
        for f in data.get("findings", []):
            for key in ("task_id", "detected_way", "suggested_way",
                        "symbol_name", "kind", "location", "confidence", "reason"):
                assert key in f, f"Finding missing {key}: {f}"

    def test_math_with_algorithmic_code(self, project_factory, monkeypatch):
        """roam math should detect patterns in code with known anti-patterns."""
        proj = project_factory({
            "algo.py": (
                "def bubble_sort(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                "    return arr\n"
            ),
        })
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(runner, ["math"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "math")
        # Should detect the manual sort
        findings = data.get("findings", [])
        sort_findings = [f for f in findings if f["task_id"] == "sorting"]
        assert len(sort_findings) >= 1, (
            f"Expected sorting finding, got: {[f['task_id'] for f in findings]}"
        )

    def test_math_filter_task(self, cli_runner, indexed_project, monkeypatch):
        """--task filter should limit to a specific task."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math", "--task", "sorting"],
                            cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "math")
        for f in data.get("findings", []):
            assert f["task_id"] == "sorting"

    def test_math_filter_confidence(self, cli_runner, indexed_project, monkeypatch):
        """--confidence filter should limit to a specific level."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math", "--confidence", "high"],
                            cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "math")
        for f in data.get("findings", []):
            assert f["confidence"] == "high"

    def test_math_limit(self, project_factory, monkeypatch):
        """--limit should cap the number of findings."""
        proj = project_factory({
            "algo.py": (
                "def bubble_sort_a(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                "\n"
                "def bubble_sort_b(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                "\n"
                "def bubble_sort_c(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
            ),
        })
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(runner, ["math", "--limit", "1"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "math")
        assert len(data.get("findings", [])) <= 1


# ============================================================================
# Detector-level tests
# ============================================================================

class TestDetectors:
    """Test individual detector functions against fixture DBs."""

    def test_detect_manual_sort(self, project_factory, monkeypatch):
        proj = project_factory({
            "algo.py": (
                "def bubble_sort(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_manual_sort
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_sort(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "sorting"
            assert hits[0]["confidence"] == "high"

    def test_detect_naive_fibonacci(self, project_factory, monkeypatch):
        proj = project_factory({
            "math_funcs.py": (
                "def fib(n):\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    return fib(n-1) + fib(n-2)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_naive_fibonacci
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_naive_fibonacci(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "fibonacci"

    def test_detect_busy_wait(self, project_factory, monkeypatch):
        proj = project_factory({
            "waiter.py": (
                "import time\n"
                "def check_flag_loop(flag):\n"
                "    while not flag.value:\n"
                "        sleep(1)\n"
                "    return True\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_busy_wait
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_busy_wait(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "busy-wait"

    def test_busy_wait_suppresses_polling(self, project_factory, monkeypatch):
        """Functions named *poll*/*retry*/*health_check* should be suppressed."""
        proj = project_factory({
            "poller.py": (
                "import time\n"
                "def poll_status(url):\n"
                "    while True:\n"
                "        sleep(1)\n"
                "        result = check(url)\n"
                "        if result:\n"
                "            return result\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_busy_wait
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_busy_wait(conn)
            # poll_status should be suppressed as intentional polling
            assert len(hits) == 0

    def test_detect_nested_lookup(self, project_factory, monkeypatch):
        proj = project_factory({
            "matcher.py": (
                "def match_items(list_a, list_b):\n"
                "    results = []\n"
                "    for a in list_a:\n"
                "        for b in list_b:\n"
                "            if a['id'] == b['id']:\n"
                "                if a['type'] != 'skip':\n"
                "                    results.append((a, b))\n"
                "                else:\n"
                "                    continue\n"
                "    return results\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_nested_lookup
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_nested_lookup(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "nested-lookup"

    def test_nested_lookup_suppresses_matrix(self, project_factory, monkeypatch):
        """Nested-lookup detector should suppress matrix/grid traversal."""
        proj = project_factory({
            "grid.py": (
                "def matrix_multiply(a, b, n):\n"
                "    result = [[0]*n for _ in range(n)]\n"
                "    for i in range(n):\n"
                "        for j in range(n):\n"
                "            if a[i][j] > b[i][j]:\n"
                "                result[i][j] = a[i][j]\n"
                "            else:\n"
                "                result[i][j] = b[i][j]\n"
                "    return result\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_nested_lookup
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_nested_lookup(conn)
            # matrix_multiply should be suppressed due to "matrix" in name
            assert len(hits) == 0

    def test_skips_test_files(self, project_factory, monkeypatch):
        """Detectors should skip test files."""
        proj = project_factory({
            "tests/test_algo.py": (
                "def test_bubble_sort(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_manual_sort
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_sort(conn)
            # test_bubble_sort is in tests/ so should be skipped
            assert len(hits) == 0

    def test_detect_regex_in_loop(self, project_factory, monkeypatch):
        proj = project_factory({
            "parser.py": (
                "import re\n"
                "def parse_lines(lines, pattern):\n"
                "    results = []\n"
                "    for line in lines:\n"
                "        pat = re.compile(pattern)\n"
                "        m = pat.match(line)\n"
                "        if m:\n"
                "            results.append(m.group())\n"
                "    return results\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_regex_in_loop
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_regex_in_loop(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "regex-in-loop"
            assert hits[0]["confidence"] == "high"

    def test_detect_io_in_loop(self, project_factory, monkeypatch):
        proj = project_factory({
            "fetcher.py": (
                "def fetch_users(ids):\n"
                "    users = []\n"
                "    for uid in ids:\n"
                "        user = query(uid)\n"
                "        users.append(user)\n"
                "    return users\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_io_in_loop
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "io-in-loop"

    def test_detect_loop_lookup(self, project_factory, monkeypatch):
        proj = project_factory({
            "checker.py": (
                "def find_dupes(items, blacklist):\n"
                "    dupes = []\n"
                "    for item in items:\n"
                "        if blacklist.index(item) >= 0:\n"
                "            dupes.append(item)\n"
                "    return dupes\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_loop_lookup
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_lookup(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "loop-lookup"

    def test_run_detectors_combined(self, project_factory, monkeypatch):
        """run_detectors should combine results from all detectors."""
        proj = project_factory({
            "algo.py": (
                "def bubble_sort(arr):\n"
                "    for i in range(len(arr)):\n"
                "        for j in range(len(arr) - 1):\n"
                "            if arr[j] > arr[j+1]:\n"
                "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                "\n"
                "def fib(n):\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    return fib(n-1) + fib(n-2)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import run_detectors
        with open_db(readonly=True, project_root=proj) as conn:
            findings = run_detectors(conn)
            task_ids = {f["task_id"] for f in findings}
            assert "sorting" in task_ids
            assert "fibonacci" in task_ids


# ============================================================================
# Tier 2 catalog tests
# ============================================================================

class TestCatalogTier2:
    """Validate new catalog entries added in tier 2."""

    def test_branching_recursion_entry(self):
        from roam.catalog.tasks import get_task, best_way
        task = get_task("branching-recursion")
        assert task is not None
        assert task["kind"] == "algorithm"
        bw = best_way("branching-recursion")
        assert bw["id"] == "memoized"

    def test_quadratic_string_entry(self):
        from roam.catalog.tasks import get_task, best_way
        task = get_task("quadratic-string")
        assert task is not None
        assert task["kind"] == "algorithm"
        bw = best_way("quadratic-string")
        assert bw["id"] == "join-parts"

    def test_loop_invariant_call_entry(self):
        from roam.catalog.tasks import get_task, best_way
        task = get_task("loop-invariant-call")
        assert task is not None
        assert task["kind"] == "algorithm"
        bw = best_way("loop-invariant-call")
        assert bw["id"] == "hoisted"


# ============================================================================
# Tier 2 signal extraction tests
# ============================================================================

class TestMathSignalsTier2:
    """Test new signal extraction: self_call_count, str_concat, loop_invariant."""

    def test_self_call_count(self):
        from roam.index.complexity import _find_function_node, _extract_math_signals
        import tree_sitter_language_pack as tslp
        lang = tslp.get_language("python")
        parser = tslp.get_parser("python")
        code = (
            "def tree_sum(node):\n"
            "    if node is None:\n"
            "        return 0\n"
            "    return node.val + tree_sum(node.left) + tree_sum(node.right)\n"
        )
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 4)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "tree_sum")
        assert sig["self_call_count"] == 2
        assert sig["has_self_call"] == 1

    def test_str_concat_in_loop(self):
        from roam.index.complexity import _find_function_node, _extract_math_signals
        import tree_sitter_language_pack as tslp
        parser = tslp.get_parser("python")
        code = (
            "def build_csv(rows):\n"
            "    result = ''\n"
            "    for row in rows:\n"
            "        result += ','.join(row) + '\\n'\n"
            "    return result\n"
        )
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 5)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "build_csv")
        assert sig["str_concat_in_loop"] == 1

    def test_str_concat_not_flagged_for_int(self):
        from roam.index.complexity import _find_function_node, _extract_math_signals
        import tree_sitter_language_pack as tslp
        parser = tslp.get_parser("python")
        code = (
            "def sum_values(items):\n"
            "    total = 0\n"
            "    for x in items:\n"
            "        total += x\n"
            "    return total\n"
        )
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 5)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "sum_values")
        assert sig["str_concat_in_loop"] == 0

    def test_loop_invariant_calls(self):
        from roam.index.complexity import _find_function_node, _extract_math_signals
        import tree_sitter_language_pack as tslp
        parser = tslp.get_parser("python")
        code = (
            "def process(items):\n"
            "    for item in items:\n"
            "        config = load_config()\n"
            "        handle(item, config)\n"
        )
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 4)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "process")
        assert "load_config" in sig["loop_invariant_calls"]
        # handle(item, ...) uses loop var, so not invariant
        assert "handle" not in sig["loop_invariant_calls"]

    def test_bounded_loop_detection(self):
        from roam.index.complexity import _find_function_node, _extract_math_signals
        import tree_sitter_language_pack as tslp
        parser = tslp.get_parser("python")
        code = (
            "def small_matrix():\n"
            "    for i in range(3):\n"
            "        for j in range(3):\n"
            "            print(i, j)\n"
        )
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 4)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "small_matrix")
        assert sig["loop_bound_small"] == 1


# ============================================================================
# Tier 2 detector tests
# ============================================================================

class TestDetectorsTier2:
    """Test new detectors: branching recursion, quadratic string, loop invariant."""

    def test_detect_branching_recursion(self, project_factory, monkeypatch):
        proj = project_factory({
            "tree.py": (
                "def tree_size(node):\n"
                "    if node is None:\n"
                "        return 0\n"
                "    return 1 + tree_size(node.left) + tree_size(node.right)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_branching_recursion
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_branching_recursion(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "branching-recursion"

    def test_branching_recursion_skips_fib(self, project_factory, monkeypatch):
        """Fibonacci is handled by its own detector — skip here."""
        proj = project_factory({
            "math.py": (
                "def fib(n):\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    return fib(n - 1) + fib(n - 2)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_branching_recursion
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_branching_recursion(conn)
            assert len(hits) == 0

    def test_detect_quadratic_string(self, project_factory, monkeypatch):
        proj = project_factory({
            "builder.py": (
                "def build_report(lines):\n"
                "    output = ''\n"
                "    for line in lines:\n"
                "        output += line + '\\n'\n"
                "    return output\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_quadratic_string
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_quadratic_string(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "quadratic-string"
            assert hits[0]["confidence"] == "high"

    def test_detect_loop_invariant_call(self, project_factory, monkeypatch):
        proj = project_factory({
            "work.py": (
                "def process_all(items):\n"
                "    for item in items:\n"
                "        cfg = get_config()\n"
                "        do_work(item, cfg)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_loop_invariant_call
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_invariant_call(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "loop-invariant-call"
            assert "get_config" in hits[0]["reason"]

    def test_loop_invariant_suppresses_logging(self, project_factory, monkeypatch):
        """print/log calls are intentionally per-iteration — don't flag."""
        proj = project_factory({
            "work.py": (
                "def process_all(items):\n"
                "    for item in items:\n"
                "        print(item)\n"
                "        log(item)\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import detect_loop_invariant_call
        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_invariant_call(conn)
            assert len(hits) == 0

    def test_bounded_loop_lowers_confidence(self, project_factory, monkeypatch):
        """Nested loops over range(3) should get confidence lowered."""
        proj = project_factory({
            "grid.py": (
                "def check_grid(data):\n"
                "    for i in range(3):\n"
                "        for j in range(3):\n"
                "            if data[i] > data[j]:\n"
                "                data[i], data[j] = data[j], data[i]\n"
            ),
        })
        monkeypatch.chdir(proj)
        from roam.db.connection import open_db
        from roam.catalog.detectors import run_detectors
        with open_db(readonly=True, project_root=proj) as conn:
            findings = run_detectors(conn)
            # Any findings on this small bounded grid should have lowered confidence
            for f in findings:
                if f["symbol_name"] == "check_grid" or "check_grid" in f.get("symbol_name", ""):
                    assert f["confidence"] != "high", \
                        f"Bounded loop finding should not be high confidence: {f}"
