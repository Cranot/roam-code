"""Tests for language-aware tips in roam math.

Covers: get_tip() lookup, language-specific tip selection in CLI text
and JSON output, fallback to default tips for unknown languages.
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
# Unit tests for get_tip()
# ============================================================================

class TestGetTip:
    """Validate the get_tip() function returns language-specific tips."""

    def test_python_tip_for_sorting(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "python")
        assert "sorted()" in tip or "list.sort()" in tip

    def test_javascript_tip_for_sorting(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "javascript")
        assert "Array" in tip or "sort()" in tip

    def test_go_tip_for_sorting(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "go")
        assert "sort.Slice" in tip or "slices.Sort" in tip

    def test_java_tip_for_sorting(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "java")
        assert "Arrays.sort" in tip or "Collections.sort" in tip

    def test_rust_tip_for_membership(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("membership", "set-lookup", "rust")
        assert "HashSet" in tip or "HashMap" in tip

    def test_default_fallback_for_unknown_language(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "cobol")
        # Should get the default tip, not empty
        assert tip != ""
        assert "built-in sort" in tip.lower()

    def test_empty_language_returns_default(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", "")
        assert tip != ""

    def test_none_language_returns_default(self):
        from roam.catalog.tasks import get_tip
        tip = get_tip("sorting", "builtin-sort", None)
        assert tip != ""

    def test_alias_ts_resolves_to_typescript(self):
        from roam.catalog.tasks import get_tip
        tip_ts = get_tip("sorting", "builtin-sort", "ts")
        tip_full = get_tip("sorting", "builtin-sort", "typescript")
        assert tip_ts == tip_full

    def test_alias_js_resolves_to_javascript(self):
        from roam.catalog.tasks import get_tip
        tip_js = get_tip("sorting", "builtin-sort", "js")
        tip_full = get_tip("sorting", "builtin-sort", "javascript")
        assert tip_js == tip_full


# ============================================================================
# Coverage: all task entries have at least a default tip
# ============================================================================

class TestAllTasksHaveTips:
    """Every rank-1 way across all tasks should produce a non-empty tip."""

    def test_all_rank1_ways_have_default_tip(self):
        from roam.catalog.tasks import CATALOG, get_tip
        for task_id, task in CATALOG.items():
            for way in task["ways"]:
                if way["rank"] == 1:
                    tip = get_tip(task_id, way["id"], None)
                    assert tip != "", (
                        f"Task {task_id} / way {way['id']} has no default tip"
                    )

    def test_python_tips_exist_for_all_language_tip_entries(self):
        from roam.catalog.tasks import _LANGUAGE_TIPS, get_tip
        for (task_id, way_id), tips in _LANGUAGE_TIPS.items():
            if "python" in tips:
                tip = get_tip(task_id, way_id, "python")
                assert tip != "", (
                    f"Python tip for {task_id}/{way_id} is empty"
                )


# ============================================================================
# Language-specific tips across multiple concepts
# ============================================================================

class TestLanguageTipVariety:
    """Test that different languages produce different tips for key tasks."""

    def test_memoization_tips_differ_by_language(self):
        from roam.catalog.tasks import get_tip
        py_tip = get_tip("branching-recursion", "memoized", "python")
        js_tip = get_tip("branching-recursion", "memoized", "javascript")
        go_tip = get_tip("branching-recursion", "memoized", "go")
        assert py_tip != js_tip, "Python and JS memoization tips should differ"
        assert py_tip != go_tip, "Python and Go memoization tips should differ"
        assert "lru_cache" in py_tip or "functools" in py_tip
        assert "Map" in js_tip
        assert "map[" in go_tip.lower() or "map[K]V" in go_tip

    def test_deque_tips_differ_by_language(self):
        from roam.catalog.tasks import get_tip
        py_tip = get_tip("list-prepend", "deque-ops", "python")
        java_tip = get_tip("list-prepend", "deque-ops", "java")
        assert "collections.deque" in py_tip
        assert "ArrayDeque" in java_tip or "LinkedList" in java_tip

    def test_event_wait_tips_differ_by_language(self):
        from roam.catalog.tasks import get_tip
        py_tip = get_tip("busy-wait", "event-wait", "python")
        go_tip = get_tip("busy-wait", "event-wait", "go")
        js_tip = get_tip("busy-wait", "event-wait", "javascript")
        assert "threading.Event" in py_tip or "asyncio.Event" in py_tip
        assert "chan" in go_tip or "sync.Cond" in go_tip
        assert "Promise" in js_tip or "EventEmitter" in js_tip

    def test_binary_search_tips_differ_by_language(self):
        from roam.catalog.tasks import get_tip
        py_tip = get_tip("search-sorted", "binary-search", "python")
        java_tip = get_tip("search-sorted", "binary-search", "java")
        assert "bisect" in py_tip
        assert "binarySearch" in java_tip or "Arrays" in java_tip

    def test_string_concat_tips_differ_by_language(self):
        from roam.catalog.tasks import get_tip
        py_tip = get_tip("quadratic-string", "join-parts", "python")
        go_tip = get_tip("quadratic-string", "join-parts", "go")
        java_tip = get_tip("quadratic-string", "join-parts", "java")
        assert "join" in py_tip.lower()
        assert "strings.Builder" in go_tip
        assert "StringBuilder" in java_tip


# ============================================================================
# Integration: CLI output with language-specific tips
# ============================================================================

class TestMathCLILanguageTips:
    """Test that `roam math` uses language-aware tips in output."""

    def test_python_file_gets_python_tip_json(self, project_factory, monkeypatch):
        """Python file should get Python-specific tip in JSON output."""
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
        findings = data.get("findings", [])
        sort_findings = [f for f in findings if f["task_id"] == "sorting"]
        assert len(sort_findings) >= 1, "Expected sorting finding"
        tip = sort_findings[0].get("tip", "")
        assert "sorted()" in tip or "list.sort()" in tip, (
            f"Expected Python-specific tip, got: {tip}"
        )
        assert sort_findings[0].get("language") == "python"

    def test_python_file_gets_python_tip_text(self, project_factory, monkeypatch):
        """Python file should get Python-specific tip in text output."""
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
        result = invoke_cli(runner, ["math"], cwd=proj)
        assert result.exit_code == 0
        # Text output should contain the Python-specific tip
        assert "sorted()" in result.output or "list.sort()" in result.output, (
            f"Expected Python tip in text output, got:\n{result.output}"
        )

    def test_json_findings_have_language_and_tip_fields(self, project_factory, monkeypatch):
        """Every finding in JSON output should have language and tip fields."""
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
        runner = CliRunner()
        result = invoke_cli(runner, ["math"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "math")
        for f in data.get("findings", []):
            assert "language" in f, f"Finding missing 'language' field: {f}"
            assert "tip" in f, f"Finding missing 'tip' field: {f}"
            # Every finding from a .py file should have language=python
            assert f["language"] == "python", (
                f"Expected language=python, got: {f['language']}"
            )
            # Tip should be non-empty for rank-1 suggestions
            assert f["tip"] != "", (
                f"Expected non-empty tip for {f['task_id']}/{f['suggested_way']}"
            )
