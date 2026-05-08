"""Tests for roam algo (formerly math) -- algorithmic improvement detection.

Covers: catalog structure, AST signal extraction, detector functions,
CLI text/JSON output, filtering options, and backward compat alias.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output

# ============================================================================
# Catalog tests
# ============================================================================


class TestCatalog:
    """Validate the universal catalog data structure."""

    def test_catalog_has_entries(self):
        from roam.catalog.tasks import CATALOG

        # T3 added serial-await-loop; X1-X5 added async-blocking-sleep,
        # broad-except-swallow, spread-accumulator, defer-in-loop;
        # X13 added chained-collection-walk; Z1/Z2/Z5 added useeffect-
        # missing-deps, dangerous-eval, unremoved-event-listener → 32.
        # added async-fire-and-forget-task,
        # async-nested-run → 34.
        assert len(CATALOG) == 34, f"Expected 34 tasks, got {len(CATALOG)}"

    def test_detector_registry_covers_catalog(self):
        from roam.catalog.detectors import _MATH_DETECTORS
        from roam.catalog.tasks import CATALOG

        detector_tasks = {task_id for task_id, _way_id, _fn in _MATH_DETECTORS}
        assert detector_tasks == set(CATALOG.keys())

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
                    assert key in way, f"{task_id}/{way.get('id', '?')} missing {key}"

    def test_each_task_has_rank_1(self):
        from roam.catalog.tasks import CATALOG

        for task_id, task in CATALOG.items():
            ranks = [w["rank"] for w in task["ways"]]
            assert 1 in ranks, f"{task_id} has no rank-1 way"

    def test_categories_are_valid(self):
        from roam.catalog.tasks import CATALOG

        # X2 added "error-handling" category (broad-except-swallow).
        valid = {"searching", "ordering", "collections", "string", "math", "concurrency", "error-handling"}
        for task_id, task in CATALOG.items():
            assert task["category"] in valid, f"{task_id} has invalid category: {task['category']}"

    def test_kinds_are_valid(self):
        from roam.catalog.tasks import CATALOG

        valid_kinds = {"algorithm", "idiom"}
        for task_id, task in CATALOG.items():
            assert "kind" in task, f"{task_id} missing kind"
            assert task["kind"] in valid_kinds, f"{task_id} has invalid kind: {task['kind']}"

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
                        fname = source[child.start_byte : child.end_byte].decode()
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

        code = "def fib(n):\n    if n <= 1:\n        return n\n    return fib(n-1) + fib(n-2)\n"
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "fib")
        assert func is not None
        sig = _extract_math_signals(func, src, "fib")
        assert sig["has_self_call"] == 1

    def test_accumulator_in_loop(self):
        from roam.index.complexity import _extract_math_signals

        code = "def manual_sum(items):\n    total = 0\n    for x in items:\n        total += x\n    return total\n"
        tree, src = self._parse_python(code)
        func = self._find_func(tree, src, "manual_sum")
        assert func is not None
        sig = _extract_math_signals(func, src, "manual_sum")
        assert sig["loop_with_accumulator"] == 1
        assert sig["loop_depth"] >= 1

    def test_calls_in_loops(self):
        from roam.index.complexity import _extract_math_signals

        code = "def poll(event):\n    while True:\n        sleep(1)\n        if event.is_set():\n            break\n"
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
        proj = project_factory(
            {
                "algo.py": (
                    "def bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                ),
            }
        )
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            rows = conn.execute(
                "SELECT ms.* FROM math_signals ms JOIN symbols s ON ms.symbol_id = s.id WHERE s.name = 'bubble_sort'"
            ).fetchall()
            assert len(rows) >= 1
            row = rows[0]
            assert row["has_nested_loops"] == 1
            assert row["loop_depth"] >= 2

    def test_math_signals_self_call(self, project_factory):
        """Recursive function should have has_self_call = 1."""
        proj = project_factory(
            {
                "fib.py": ("def fib(n):\n    if n <= 1:\n        return n\n    return fib(n-1) + fib(n-2)\n"),
            }
        )
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            rows = conn.execute(
                "SELECT ms.* FROM math_signals ms JOIN symbols s ON ms.symbol_id = s.id WHERE s.name = 'fib'"
            ).fetchall()
            assert len(rows) >= 1
            assert rows[0]["has_self_call"] == 1


# ============================================================================
# CLI command tests
# ============================================================================


class TestAlgoCLI:
    """Tests for `roam algo` CLI output."""

    def test_algo_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam algo should run without error on indexed project."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo"], cwd=indexed_project)
        assert result.exit_code == 0, f"algo failed: {result.output}"

    def test_algo_verdict(self, cli_runner, indexed_project, monkeypatch):
        """roam algo should output a VERDICT line."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_algo_json_envelope(self, cli_runner, indexed_project, monkeypatch):
        """roam --json algo should return valid envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "algo")
        assert_json_envelope(data, "algo")
        assert "verdict" in data["summary"]
        assert "total" in data["summary"]
        assert "findings" in data

    def test_algo_json_findings_structure(self, cli_runner, indexed_project, monkeypatch):
        """Each finding in JSON should have required fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "algo")
        for f in data.get("findings", []):
            for key in (
                "task_id",
                "detected_way",
                "suggested_way",
                "symbol_name",
                "kind",
                "location",
                "confidence",
                "reason",
            ):
                assert key in f, f"Finding missing {key}: {f}"

    def test_algo_with_algorithmic_code(self, project_factory, monkeypatch):
        """roam algo should detect patterns in code with known anti-patterns."""
        proj = project_factory(
            {
                "algo.py": (
                    "def bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                    "    return arr\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(runner, ["algo"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "algo")
        # Should detect the manual sort
        findings = data.get("findings", [])
        sort_findings = [f for f in findings if f["task_id"] == "sorting"]
        assert len(sort_findings) >= 1, f"Expected sorting finding, got: {[f['task_id'] for f in findings]}"

    def test_algo_filter_task(self, cli_runner, indexed_project, monkeypatch):
        """--task filter should limit to a specific task."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo", "--task", "sorting"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "algo")
        for f in data.get("findings", []):
            assert f["task_id"] == "sorting"

    def test_algo_filter_confidence(self, cli_runner, indexed_project, monkeypatch):
        """--confidence filter should limit to a specific level."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo", "--confidence", "high"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "algo")
        for f in data.get("findings", []):
            assert f["confidence"] == "high"

    def test_algo_limit(self, project_factory, monkeypatch):
        """--limit should cap the number of findings."""
        proj = project_factory(
            {
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
            }
        )
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(runner, ["algo", "--limit", "1"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "algo")
        assert len(data.get("findings", [])) <= 1

    def test_algo_max_per_task_diversifies_first_page(self, project_factory, monkeypatch):
        """--max-per-task should prevent one detector from dominating top results."""
        io_funcs = []
        for i in range(1, 8):
            io_funcs.append(
                f"def fetch_{i}(urls):\n"
                f"    out = []\n"
                f"    for url in urls:\n"
                f"        out.append(requests.get(url))\n"
                f"    return out\n"
            )
        proj = project_factory(
            {
                "algo.py": (
                    "import requests\n" + "\n".join(io_funcs) + "\n"
                    "def bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["algo", "--limit", "6", "--max-per-task", "2"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        findings = data.get("findings", [])
        assert len(findings) <= 6

        by_task = {}
        for f in findings:
            by_task[f["task_id"]] = by_task.get(f["task_id"], 0) + 1
        assert by_task, "Expected findings for diversity check"
        assert len(by_task.keys()) >= 2
        first_page_tasks = {f["task_id"] for f in findings[:3]}
        assert len(first_page_tasks) >= 2
        assert data["summary"].get("max_per_task") == 2
        assert data["summary"].get("deferred_by_task_cap", 0) > 0

    def test_math_alias_still_works(self, cli_runner, indexed_project, monkeypatch):
        """roam math should still work as a backward compat alias for algo."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project)
        assert result.exit_code == 0, f"math alias failed: {result.output}"
        assert "VERDICT:" in result.output

    def test_math_alias_json_envelope(self, cli_runner, indexed_project, monkeypatch):
        """roam --json math should return envelope with command='algo'."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["math"], cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "algo")
        assert_json_envelope(data, "algo")


# ============================================================================
# Detector-level tests
# ============================================================================


class TestDetectors:
    """Test individual detector functions against fixture DBs."""

    def test_detect_manual_sort(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "algo.py": (
                    "def bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_manual_sort
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_sort(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "sorting"
            assert hits[0]["confidence"] == "high"

    def test_detect_naive_fibonacci(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "math_funcs.py": ("def fib(n):\n    if n <= 1:\n        return n\n    return fib(n-1) + fib(n-2)\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_naive_fibonacci
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_naive_fibonacci(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "fibonacci"

    def test_detect_busy_wait(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "waiter.py": (
                    "import time\n"
                    # keep the function name free of poll
                    # keywords (poll, wait, watch, _loop, retry, etc.) so the
                    # busy-wait detector isn't suppressed by the name guard.
                    # Sub-second sleep is the actual busy-wait pattern;
                    # `sleep(1)` is now treated as operator-paced polling.
                    "def consume_flag(flag):\n"
                    "    while not flag.value:\n"
                    "        time.sleep(0.01)\n"
                    "    return True\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_busy_wait
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_busy_wait(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "busy-wait"

    def test_busy_wait_suppresses_polling(self, project_factory, monkeypatch):
        """Functions named *poll*/*retry*/*health_check* should be suppressed."""
        proj = project_factory(
            {
                "poller.py": (
                    "import time\n"
                    "def poll_status(url):\n"
                    "    while True:\n"
                    "        sleep(1)\n"
                    "        result = check(url)\n"
                    "        if result:\n"
                    "            return result\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_busy_wait
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_busy_wait(conn)
            # poll_status should be suppressed as intentional polling
            assert len(hits) == 0

    def test_detect_nested_lookup(self, project_factory, monkeypatch):
        proj = project_factory(
            {
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
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_nested_lookup
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_nested_lookup(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "nested-lookup"

    def test_nested_lookup_suppresses_matrix(self, project_factory, monkeypatch):
        """Nested-lookup detector should suppress matrix/grid traversal."""
        proj = project_factory(
            {
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
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_nested_lookup
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_nested_lookup(conn)
            # matrix_multiply should be suppressed due to "matrix" in name
            assert len(hits) == 0

    def test_skips_test_files(self, project_factory, monkeypatch):
        """Detectors should skip test files."""
        proj = project_factory(
            {
                "tests/test_algo.py": (
                    "def test_bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_manual_sort
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_sort(conn)
            # test_bubble_sort is in tests/ so should be skipped
            assert len(hits) == 0

    def test_detect_regex_in_loop(self, project_factory, monkeypatch):
        proj = project_factory(
            {
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
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_regex_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_regex_in_loop(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "regex-in-loop"
            assert hits[0]["confidence"] == "high"

    def test_detect_io_in_loop(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "def fetch_users(ids):\n"
                    "    users = []\n"
                    "    for uid in ids:\n"
                    "        user = query(uid)\n"
                    "        users.append(user)\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "io-in-loop"

    def test_detect_io_in_loop_requests_get(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import requests\n"
                    "def fetch_users(urls):\n"
                    "    users = []\n"
                    "    for url in urls:\n"
                    "        users.append(requests.get(url))\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            assert any("requests.get" in h["reason"] for h in hits)

    def test_detect_io_in_loop_ignores_local_query_helper(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "def query(user_id):\n"
                    "    return user_id\n"
                    "def fetch_users(ids):\n"
                    "    users = []\n"
                    "    for uid in ids:\n"
                    "        users.append(query(uid))\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) == 0

    def test_detect_io_in_loop_ambiguous_bare_get_low_confidence(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "def fetch_users(ids):\n"
                    "    users = []\n"
                    "    for uid in ids:\n"
                    "        users.append(get(uid))\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            hit = hits[0]
            assert hit["confidence"] == "low"
            evidence = hit.get("evidence", {})
            assert evidence.get("ambiguous_io_only") is True
            assert "get" in evidence.get("ambiguous_io_calls", [])

    def test_ambiguous_io_in_loop_runtime_escalates_confidence(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "def fetch_users(ids):\n"
                    "    users = []\n"
                    "    for uid in ids:\n"
                    "        users.append(get(uid))\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=False, project_root=proj) as conn:
            sym = conn.execute("SELECT id FROM symbols WHERE name = 'fetch_users' LIMIT 1").fetchone()
            assert sym is not None
            conn.execute(
                "INSERT INTO runtime_stats "
                "(symbol_id, symbol_name, trace_source, call_count, p99_latency_ms, error_rate) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sym["id"], "fetch_users", "generic", 5000, 450.0, 0.01),
            )
            conn.commit()

            hits = run_detectors(conn, task_filter="io-in-loop")
            assert len(hits) >= 1
            hit = next((h for h in hits if "fetch_users" in h.get("symbol_name", "")), None)
            assert hit is not None
            assert hit["confidence"] in {"medium", "high"}
            assert "runtime:" in hit.get("reason", "")

    def test_detect_io_in_loop_httpx_framework_pack(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import httpx\n"
                    "def fetch_users(urls):\n"
                    "    users = []\n"
                    "    for url in urls:\n"
                    "        users.append(httpx.get(url))\n"
                    "    return users\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            hit = hits[0]
            assert "python-http-client" in hit.get("evidence", {}).get("frameworks", [])
            assert "bounded async batches" in (hit.get("fix") or "")

    def test_detect_loop_lookup(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "checker.py": (
                    "def find_dupes(items, blacklist):\n"
                    "    dupes = []\n"
                    "    for item in items:\n"
                    "        if blacklist.index(item) >= 0:\n"
                    "            dupes.append(item)\n"
                    "    return dupes\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_loop_lookup
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_lookup(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "loop-lookup"

    def test_loop_lookup_avoids_string_find_false_positive(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "checker.py": (
                    "def grep_lines(lines):\n"
                    "    out = []\n"
                    "    for line in lines:\n"
                    "        if line.find('ERR') >= 0:\n"
                    "            out.append(line)\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_loop_lookup
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_lookup(conn)
            assert len(hits) == 0

    def test_list_prepend_avoids_set_add_false_positive(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "dedup.py": (
                    "def dedup(values):\n    seen = set()\n    for v in values:\n        seen.add(v)\n    return seen\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_list_prepend
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_list_prepend(conn)
            assert len(hits) == 0

    def test_detect_sort_to_select_sorted_index(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "ranker.py": ("def top_one(items):\n    return sorted(items)[0]\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_sort_to_select
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_sort_to_select(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "sort-to-select"

    def test_detect_manual_power(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "math_ops.py": (
                    "def power(base, exp):\n"
                    "    out = 1\n"
                    "    for _ in range(exp):\n"
                    "        out *= base\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_manual_power
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_power(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "manual-power"

    def test_detect_manual_gcd(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "math_ops.py": ("def gcd(a, b):\n    while b != 0:\n        a, b = b, a % b\n    return a\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_manual_gcd
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_manual_gcd(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "manual-gcd"

    def test_detect_string_reverse(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "strings.py": (
                    "def reverse_string(s):\n    out = ''\n    for ch in s:\n        out = ch + out\n    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_string_reverse
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_string_reverse(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "string-reverse"

    def test_detect_matrix_mult(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "matrix.py": (
                    "def matrix_multiply(a, b):\n"
                    "    n = len(a)\n"
                    "    out = [[0] * n for _ in range(n)]\n"
                    "    for i in range(n):\n"
                    "        for j in range(n):\n"
                    "            for k in range(n):\n"
                    "                out[i][j] += a[i][k] * b[k][j]\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_matrix_mult
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_matrix_mult(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "matrix-mult"

    def test_run_detectors_combined(self, project_factory, monkeypatch):
        """run_detectors should combine results from all detectors."""
        proj = project_factory(
            {
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
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            findings = run_detectors(conn)
            task_ids = {f["task_id"] for f in findings}
            assert "sorting" in task_ids
            assert "fibonacci" in task_ids

    def test_run_detectors_meta(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "algo.py": (
                    "def bubble_sort(arr):\n"
                    "    for i in range(len(arr)):\n"
                    "        for j in range(len(arr) - 1):\n"
                    "            if arr[j] > arr[j+1]:\n"
                    "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            findings, meta = run_detectors(conn, return_meta=True)
            assert len(findings) >= 1
            assert meta["detectors_executed"] >= 1
            assert "detectors_failed" in meta


# ============================================================================
# T3 / DF2 / U3 — newer detectors and FP guards
# ============================================================================


class TestSerialAwaitLoop:
    """serial-await-loop detector for JS/TS Promise.all opportunities."""

    def test_detects_for_of_with_await(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.ts": (
                    "async function fetchAll(ids: string[]): Promise<User[]> {\n"
                    "  const out: User[] = [];\n"
                    "  for (const id of ids) {\n"
                    "    const u = await getUser(id);\n"
                    "    out.push(u);\n"
                    "  }\n"
                    "  return out;\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_serial_await_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_serial_await_loop(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "serial-await-loop"
            mp = (hits[0].get("evidence") or {}).get("matched_patterns") or []
            assert "for-of header" in mp

    def test_skipped_when_promise_all_used(self, project_factory, monkeypatch):
        """Body that already uses Promise.all is correctly batched — no finding."""
        proj = project_factory(
            {
                "fetcher.ts": (
                    "async function fetchAll(ids: string[]) {\n"
                    "  return await Promise.all(ids.map(id => getUser(id)));\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_serial_await_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_serial_await_loop(conn)
            assert hits == []

    def test_pure_python_files_not_scanned(self, project_factory, monkeypatch):
        """Detector is JS/TS-only — Python for-loops with await must not trigger."""
        proj = project_factory(
            {
                "loop.py": "async def f(ids):\n    for i in ids:\n        await get_one(i)\n",
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_serial_await_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_serial_await_loop(conn)
            assert hits == []


class TestAsyncBlockingSleep:
    """blocking calls inside async function (event-loop stall)."""

    def test_detects_time_sleep_in_async(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": ("import time\nasync def do_work():\n    time.sleep(0.5)\n    return 42\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_async_blocking_sleep
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_async_blocking_sleep(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "async-blocking-sleep"

    def test_skips_when_using_asyncio_sleep(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": ("import asyncio\nasync def do_work():\n    await asyncio.sleep(0.5)\n    return 42\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_async_blocking_sleep
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_async_blocking_sleep(conn)
            assert hits == []

    def test_pure_sync_function_not_flagged(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": ("import time\ndef do_work():\n    time.sleep(0.5)\n    return 42\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_async_blocking_sleep
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_async_blocking_sleep(conn)
            assert hits == []


class TestBroadExceptSwallow:
    """bare `except Exception:` without re-raise is a swallow."""

    def test_detects_swallow(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": ("def fragile():\n    try:\n        do_thing()\n    except Exception:\n        pass\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_broad_except_swallow
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_broad_except_swallow(conn)
            assert len(hits) >= 1

    def test_skips_when_reraises(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": (
                    "def thoughtful():\n"
                    "    try:\n"
                    "        do_thing()\n"
                    "    except Exception:\n"
                    "        log_it()\n"
                    "        raise\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_broad_except_swallow
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_broad_except_swallow(conn)
            assert hits == []

    def test_skips_when_function_name_signals_recovery(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "svc.py": (
                    "def safe_int(x):\n    try:\n        return int(x)\n    except Exception:\n        return 0\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_broad_except_swallow
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_broad_except_swallow(conn)
            assert hits == []


class TestUseEffectMissingDeps:
    """React useEffect without deps array."""

    def test_detects_missing_deps(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "Comp.tsx": (
                    "import { useEffect } from 'react';\n"
                    "export function Comp() {\n"
                    "  useEffect(() => { console.log('hi'); });\n"
                    "  return null;\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_useeffect_missing_deps
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_useeffect_missing_deps(conn)
            assert len(hits) >= 1

    def test_skips_when_deps_present(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "Comp.tsx": (
                    "import { useEffect } from 'react';\n"
                    "export function Comp(props: { id: string }) {\n"
                    "  useEffect(() => { fetch(props.id); }, [props.id]);\n"
                    "  return null;\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_useeffect_missing_deps
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_useeffect_missing_deps(conn)
            assert hits == []


class TestDangerousEval:
    """eval / exec / new Function in production source."""

    def test_detects_eval(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "service.py": ("def evaluate(expr):\n    return eval(expr)\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_dangerous_eval
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_dangerous_eval(conn)
            assert len(hits) >= 1

    def test_skips_test_paths(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "tests/test_eval.py": ("def test_eval():\n    return eval('1+1')\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_dangerous_eval
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_dangerous_eval(conn)
            assert hits == []


class TestBatchIterationGuard:
    """D `for chunk in _chunked(ids):` is not N+1."""

    def test_has_batch_iteration_recognises_chunked(self):
        from roam.catalog.detectors import _has_batch_iteration

        snippet = (
            "for chunk in _chunked(symbol_ids):\n"
            "    ph = ','.join('?' for _ in chunk)\n"
            "    rows = conn.execute(f'SELECT * FROM x WHERE id IN ({ph})', chunk)\n"
        )
        assert _has_batch_iteration(snippet) is True

    def test_has_batch_iteration_recognises_where_in_placeholder(self):
        from roam.catalog.detectors import _has_batch_iteration

        snippet = "rows = conn.execute(f'SELECT * FROM symbols WHERE id IN ({ph})', ids).fetchall()\n"
        assert _has_batch_iteration(snippet) is True

    def test_has_batch_iteration_negative_per_item_loop(self):
        from roam.catalog.detectors import _has_batch_iteration

        snippet = (
            "for fp in test_files:\n    row = conn.execute('SELECT COUNT(*) FROM x WHERE f = ?', (fp,)).fetchone()\n"
        )
        assert _has_batch_iteration(snippet) is False


# ============================================================================
# Tier 2 catalog tests
# ============================================================================


class TestCatalogTier2:
    """Validate new catalog entries added in tier 2."""

    def test_branching_recursion_entry(self):
        from roam.catalog.tasks import best_way, get_task

        task = get_task("branching-recursion")
        assert task is not None
        assert task["kind"] == "algorithm"
        bw = best_way("branching-recursion")
        assert bw["id"] == "memoized"

    def test_quadratic_string_entry(self):
        from roam.catalog.tasks import best_way, get_task

        task = get_task("quadratic-string")
        assert task is not None
        assert task["kind"] == "algorithm"
        bw = best_way("quadratic-string")
        assert bw["id"] == "join-parts"

    def test_loop_invariant_call_entry(self):
        from roam.catalog.tasks import best_way, get_task

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
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

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
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

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
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

        parser = tslp.get_parser("python")
        code = "def sum_values(items):\n    total = 0\n    for x in items:\n        total += x\n    return total\n"
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 5)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "sum_values")
        assert sig["str_concat_in_loop"] == 0

    def test_loop_invariant_calls(self):
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

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
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

        parser = tslp.get_parser("python")
        code = "def small_matrix():\n    for i in range(3):\n        for j in range(3):\n            print(i, j)\n"
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 4)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "small_matrix")
        assert sig["loop_bound_small"] == 1

    def test_calls_in_loops_qualified(self):
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

        parser = tslp.get_parser("python")
        code = "import requests\ndef fetch_all(urls):\n    for u in urls:\n        requests.get(u)\n"
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 2, 4)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "fetch_all")
        assert "requests.get" in sig["calls_in_loops_qualified"]

    def test_front_ops_in_loop_signal(self):
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

        parser = tslp.get_parser("python")
        code = "def build(vals):\n    out = []\n    for v in vals:\n        out.insert(0, v)\n    return out\n"
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 5)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "build")
        assert sig["front_ops_in_loop"] == 1

    def test_loop_lookup_calls_signal(self):
        import tree_sitter_language_pack as tslp

        from roam.index.complexity import _extract_math_signals, _find_function_node

        parser = tslp.get_parser("python")
        code = "def find_dupes(items, blacklist):\n    for item in items:\n        blacklist.index(item)\n"
        tree = parser.parse(code.encode())
        fn = _find_function_node(tree, 1, 3)
        assert fn is not None
        sig = _extract_math_signals(fn, code.encode(), "find_dupes")
        assert any("index" in c for c in sig["loop_lookup_calls"])


# ============================================================================
# Tier 2 detector tests
# ============================================================================


class TestDetectorsTier2:
    """Test new detectors: branching recursion, quadratic string, loop invariant."""

    def test_detect_branching_recursion(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "tree.py": (
                    "def tree_size(node):\n"
                    "    if node is None:\n"
                    "        return 0\n"
                    "    return 1 + tree_size(node.left) + tree_size(node.right)\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_branching_recursion
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_branching_recursion(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "branching-recursion"

    def test_branching_recursion_skips_fib(self, project_factory, monkeypatch):
        """Fibonacci is handled by its own detector — skip here."""
        proj = project_factory(
            {
                "math.py": ("def fib(n):\n    if n <= 1:\n        return n\n    return fib(n - 1) + fib(n - 2)\n"),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_branching_recursion
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_branching_recursion(conn)
            assert len(hits) == 0

    def test_branching_recursion_skips_explicit_depth_guard(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "src/case.ts": (
                    "export function findSnakeCaseKeysDeep(value: any, path = ''): string[] {\n"
                    "  if (!value || typeof value !== 'object') return []\n"
                    "  let keys: string[] = []\n"
                    "  for (const key of Object.keys(value)) {\n"
                    "    const nextPath = path ? `${path}.${key}` : key\n"
                    "    if (path.split('.').length < 5) {\n"
                    "      keys = keys.concat(findSnakeCaseKeysDeep(value[key], nextPath))\n"
                    "      keys = keys.concat(findSnakeCaseKeysDeep({ nested: value[key] }, nextPath))\n"
                    "    }\n"
                    "  }\n"
                    "  return keys\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_branching_recursion
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_branching_recursion(conn)
            assert len(hits) == 0

    def test_io_in_loop_skips_tanstack_query_cache_updates(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "src/query.ts": (
                    "export function updateCache(items: any[], qc: any) {\n"
                    "  for (const item of items) {\n"
                    "    qc.setQueryData(['resource', item.id], item)\n"
                    "  }\n"
                    "}\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) == 0

    def test_detect_quadratic_string(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "builder.py": (
                    "def build_report(lines):\n"
                    "    output = ''\n"
                    "    for line in lines:\n"
                    "        output += line + '\\n'\n"
                    "    return output\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_quadratic_string
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_quadratic_string(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "quadratic-string"
            assert hits[0]["confidence"] == "high"

    def test_detect_loop_invariant_call(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "work.py": (
                    "def process_all(items):\n"
                    "    for item in items:\n"
                    "        cfg = get_config()\n"
                    "        do_work(item, cfg)\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_loop_invariant_call
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_invariant_call(conn)
            assert len(hits) >= 1
            assert hits[0]["task_id"] == "loop-invariant-call"
            assert "get_config" in hits[0]["reason"]

    def test_loop_invariant_suppresses_logging(self, project_factory, monkeypatch):
        """print/log calls are intentionally per-iteration — don't flag."""
        proj = project_factory(
            {
                "work.py": (
                    "def process_all(items):\n    for item in items:\n        print(item)\n        log(item)\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_loop_invariant_call
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_invariant_call(conn)
            assert len(hits) == 0

    def test_loop_invariant_suppresses_qualified_append(self, project_factory, monkeypatch):
        """Qualified intentional calls like out.append(...) should be suppressed."""
        proj = project_factory(
            {
                "work.py": (
                    "def collect(items):\n"
                    "    out = []\n"
                    "    for item in items:\n"
                    "        out.append(item)\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_loop_invariant_call
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_loop_invariant_call(conn)
            assert len(hits) == 0

    def test_bounded_loop_lowers_confidence(self, project_factory, monkeypatch):
        """Nested loops over range(3) should get confidence lowered."""
        proj = project_factory(
            {
                "grid.py": (
                    "def check_grid(data):\n"
                    "    for i in range(3):\n"
                    "        for j in range(3):\n"
                    "            if data[i] > data[j]:\n"
                    "                data[i], data[j] = data[j], data[i]\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            findings = run_detectors(conn)
            # Any findings on this small bounded grid should have lowered confidence
            for f in findings:
                if f["symbol_name"] == "check_grid" or "check_grid" in f.get("symbol_name", ""):
                    assert f["confidence"] != "high", f"Bounded loop finding should not be high confidence: {f}"


# ============================================================================
# Rich evidence / profile tests
# ============================================================================


class TestAlgoRicher:
    """Coverage for richer evidence, framework packs, runtime fusion, and profiles."""

    def test_io_in_loop_framework_fix_hint(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "service.py": (
                    "class User:\n"
                    "    objects = None\n"
                    "\n"
                    "def load_users(ids):\n"
                    "    out = []\n"
                    "    for uid in ids:\n"
                    "        out.append(User.objects.get(uid))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            assert any("django-orm" in (h.get("reason") or "") for h in hits)
            assert any("select_related" in (h.get("fix") or "") for h in hits)

    def test_runtime_evidence_is_attached(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "def fetch_users(ids):\n"
                    "    out = []\n"
                    "    for uid in ids:\n"
                    "        out.append(query(uid))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=False, project_root=proj) as conn:
            sym = conn.execute("SELECT id FROM symbols WHERE name = 'fetch_users' LIMIT 1").fetchone()
            assert sym is not None
            conn.execute(
                "INSERT INTO runtime_stats "
                "(symbol_id, symbol_name, trace_source, call_count, p99_latency_ms, error_rate) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sym["id"], "fetch_users", "generic", 5000, 450.0, 0.01),
            )
            conn.commit()

            hits = run_detectors(conn, task_filter="io-in-loop")
            assert len(hits) >= 1
            hit = hits[0]
            runtime = hit.get("evidence", {}).get("runtime", {})
            assert runtime.get("call_count", 0) >= 5000
            assert "runtime:" in hit.get("reason", "")

    def test_runtime_db_semantics_raise_impact_score(self, project_factory, monkeypatch):
        """OTel DB semantic attributes should increase runtime impact signal quality."""
        proj = project_factory(
            {
                "fetcher.py": (
                    "def fetch_users(ids):\n"
                    "    out = []\n"
                    "    for uid in ids:\n"
                    "        out.append(query(uid))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=False, project_root=proj) as conn:
            sym = conn.execute("SELECT id FROM symbols WHERE name = 'fetch_users' LIMIT 1").fetchone()
            assert sym is not None

            conn.execute("DELETE FROM runtime_stats")
            conn.execute(
                "INSERT INTO runtime_stats "
                "(symbol_id, symbol_name, trace_source, call_count, p99_latency_ms, error_rate) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sym["id"], "fetch_users", "generic", 1200, 160.0, 0.0),
            )
            generic_hits = run_detectors(conn, task_filter="io-in-loop")
            generic_hit = next(
                (h for h in generic_hits if "fetch_users" in h.get("symbol_name", "")),
                None,
            )
            assert generic_hit is not None

            conn.execute("DELETE FROM runtime_stats")
            conn.execute(
                "INSERT INTO runtime_stats "
                "(symbol_id, symbol_name, trace_source, call_count, p99_latency_ms, error_rate, "
                "otel_db_system, otel_db_operation, otel_db_statement_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sym["id"],
                    "fetch_users",
                    "otel",
                    1200,
                    160.0,
                    0.0,
                    "postgresql",
                    "DELETE",
                    "DELETE",
                ),
            )
            otel_hits = run_detectors(conn, task_filter="io-in-loop")
            otel_hit = next(
                (h for h in otel_hits if "fetch_users" in h.get("symbol_name", "")),
                None,
            )
            assert otel_hit is not None
            assert otel_hit["impact_score"] > generic_hit["impact_score"]

            runtime = otel_hit.get("evidence", {}).get("runtime", {})
            assert runtime.get("db_system") == "postgresql"
            assert runtime.get("db_operation") == "DELETE"

    def test_strict_profile_filters_low_confidence(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "search.py": (
                    "def search_sorted(items, target):\n"
                    "    for i, x in enumerate(items):\n"
                    "        if x == target:\n"
                    "            return i\n"
                    "    return -1\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            balanced = run_detectors(conn, task_filter="search-sorted", profile="balanced")
            strict = run_detectors(conn, task_filter="search-sorted", profile="strict")
            assert len(balanced) >= 1
            assert len(strict) == 0

    def test_algo_json_includes_profile_evidence_and_fix(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import requests\n"
                    "def fetch_users(urls):\n"
                    "    out = []\n"
                    "    for url in urls:\n"
                    "        out.append(requests.get(url))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["algo", "--task", "io-in-loop", "--profile", "aggressive"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        assert data["summary"]["profile"] == "aggressive"
        findings = data.get("findings", [])
        assert len(findings) >= 1
        finding = findings[0]
        assert "evidence" in finding
        assert "evidence_path" in finding
        assert finding.get("fix", "") != ""

    def test_detector_metadata_and_impact_score(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import requests\n"
                    "def fetch_users(urls):\n"
                    "    out = []\n"
                    "    for url in urls:\n"
                    "        out.append(requests.get(url))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            findings, meta = run_detectors(conn, task_filter="io-in-loop", return_meta=True)
            assert len(findings) >= 1
            f = findings[0]
            assert f.get("precision") in {"high", "medium", "low"}
            assert f.get("impact") in {"high", "medium", "low"}
            assert isinstance(f.get("impact_score"), float)
            assert f.get("impact_band") in {"high", "medium", "low"}
            assert "io-in-loop" in meta.get("detector_metadata", {})

    def test_complexity_pressure_raises_impact_score(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import requests\n"
                    "def fetch_simple(urls):\n"
                    "    out = []\n"
                    "    for u in urls:\n"
                    "        out.append(requests.get(u))\n"
                    "    return out\n"
                    "\n"
                    "def fetch_complex(urls, a, b, c, d):\n"
                    "    out = []\n"
                    "    if a:\n"
                    "        mode = 'x'\n"
                    "    else:\n"
                    "        mode = 'y'\n"
                    "    if b:\n"
                    "        mode = mode + '1'\n"
                    "    if c:\n"
                    "        mode = mode + '2'\n"
                    "    if d:\n"
                    "        mode = mode + '3'\n"
                    "    for u in urls:\n"
                    "        out.append(requests.get(u))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import run_detectors
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = run_detectors(conn, task_filter="io-in-loop")
            simple_hit = next((h for h in hits if "fetch_simple" in h.get("symbol_name", "")), None)
            complex_hit = next((h for h in hits if "fetch_complex" in h.get("symbol_name", "")), None)
            assert simple_hit is not None
            assert complex_hit is not None
            assert "complexity" in complex_hit.get("evidence", {})
            assert complex_hit["impact_score"] > simple_hit["impact_score"]

    def test_io_in_loop_guard_hints_reduce_confidence(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "service.py": (
                    "class User:\n"
                    "    objects = None\n"
                    "\n"
                    "def load_users(ids):\n"
                    "    users = User.objects.select_related('team').all()\n"
                    "    out = []\n"
                    "    for uid in ids:\n"
                    "        out.append(User.objects.get(uid))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        from roam.catalog.detectors import detect_io_in_loop
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=proj) as conn:
            hits = detect_io_in_loop(conn)
            assert len(hits) >= 1
            hit = hits[0]
            assert hit["confidence"] != "high"
            assert "guard_hints" in hit.get("evidence", {})

    def test_algo_sarif_contains_fingerprint_codeflow_and_fix(self, project_factory, monkeypatch):
        proj = project_factory(
            {
                "fetcher.py": (
                    "import requests\n"
                    "def fetch_users(urls):\n"
                    "    out = []\n"
                    "    for url in urls:\n"
                    "        out.append(requests.get(url))\n"
                    "    return out\n"
                ),
            }
        )
        monkeypatch.chdir(proj)
        runner = CliRunner()
        result = invoke_cli(
            runner,
            ["--sarif", "algo", "--task", "io-in-loop"],
            cwd=proj,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data.get("version") == "2.1.0"
        runs = data.get("runs", [])
        assert runs
        results = runs[0].get("results", [])
        assert results
        res = results[0]
        assert "partialFingerprints" in res
        assert "primaryLocationLineHash" in res["partialFingerprints"]
        assert "codeFlows" in res
        assert "fixes" in res
