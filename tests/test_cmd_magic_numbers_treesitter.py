"""Tests for the tree-sitter / clustering additions to
``roam magic-numbers``.

Covers:
- (a) Tree-sitter port — JS / TS / Go / Rust / Java / Ruby / C / C#
  fixtures with repeated magic numbers; assert findings emerge.
- (b) ``--cluster`` flag — fixture with values across multiple semantic
  buckets produces named clusters with counts + suggested constants.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_magic_numbers import (
    _build_clusters,
    _cluster_for_value,
    _cluster_for_value_with_context,
    _parse_number_text,
    _suggest_constant_name,
    magic_numbers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _invoke_json(runner: CliRunner, *args: str):
    return runner.invoke(magic_numbers, list(args), obj={"json": True})


# ---------------------------------------------------------------------------
# Helper-level tests (cluster name / suggestion)
# ---------------------------------------------------------------------------


def test_cluster_for_value_timeout():
    assert _cluster_for_value(30, "") == "timeout_seconds"
    assert _cluster_for_value(3600, "") == "timeout_seconds"
    assert _cluster_for_value(86400, "") == "timeout_seconds"


def test_cluster_for_value_pow2():
    assert _cluster_for_value(1024, "") == "size_power_of_two"
    assert _cluster_for_value(65536, "") == "size_power_of_two"
    # 64 is also a timeout? no -- 64 not in timeout set; it's pow2.
    assert _cluster_for_value(64, "") == "size_power_of_two"


def test_cluster_for_value_port():
    assert _cluster_for_value(8080, "") == "network_port"
    assert _cluster_for_value(5432, "") == "network_port"


def test_cluster_for_value_http_status():
    assert _cluster_for_value(200, "") == "http_status"
    assert _cluster_for_value(404, "") == "http_status"
    assert _cluster_for_value(503, "") == "http_status"


def test_cluster_for_value_percentage_float():
    assert _cluster_for_value(0.5, "") == "percentage"
    assert _cluster_for_value(0.95, "") == "percentage"


def test_cluster_for_value_uncategorized():
    assert _cluster_for_value(42, "") == "uncategorized"
    assert _cluster_for_value(3.14, "") == "uncategorized"


def test_suggest_constant_name_shapes():
    assert _suggest_constant_name("timeout_seconds", 30) == "DEFAULT_TIMEOUT_S=30"
    assert _suggest_constant_name("size_power_of_two", 1024) == "BUFFER_SIZE=1024"
    assert _suggest_constant_name("http_status", 200) == "HTTP_200"
    assert _suggest_constant_name("network_port", 8080) == "PORT_8080"


def test_parse_number_text_handles_various_forms():
    assert _parse_number_text("100") == 100
    assert _parse_number_text("1024") == 1024
    assert _parse_number_text("0x1F") == 31
    assert _parse_number_text("0b1010") == 10
    assert _parse_number_text("1_000_000") == 1000000
    assert _parse_number_text("3.14") == 3.14
    assert _parse_number_text("1e3") == 1000.0
    # Java long suffix
    assert _parse_number_text("100L") == 100
    # Rust integer-typed suffix
    assert _parse_number_text("1024_i32") == 1024
    # JS bigint
    assert _parse_number_text("100n") == 100


# ---------------------------------------------------------------------------
# _build_clusters direct test for the exact spec example
# ---------------------------------------------------------------------------


def test_build_clusters_spec_example():
    """Spec: ``{30, 30, 30, 1024, 200, 0.5}`` should produce 3 named clusters.

    - 30 (×3)         -> timeout_seconds
    - 1024            -> size_power_of_two
    - 200             -> http_status
    - 0.5             -> percentage

    (Threshold-aware aggregation isn't run here; we hand-build the
    pre-clustered finding list.)
    """
    findings = [
        {"value": 30, "occurrences": 3, "sites": [{"file": "a.py", "line": 1, "context_snippet": "x = 30"}]},
        {"value": 1024, "occurrences": 1, "sites": [{"file": "a.py", "line": 2, "context_snippet": "y = 1024"}]},
        {"value": 200, "occurrences": 1, "sites": [{"file": "a.py", "line": 3, "context_snippet": "z = 200"}]},
        {"value": 0.5, "occurrences": 1, "sites": [{"file": "a.py", "line": 4, "context_snippet": "r = 0.5"}]},
    ]
    clusters = _build_clusters(findings)

    # The spec calls out at least these three named clusters.
    assert "timeout_seconds" in clusters
    assert "size_power_of_two" in clusters
    # The remaining two values land in their own clusters too — total
    # cluster count should be 4 (timeout, size, http_status, percentage)
    # but the spec phrasing "3 clusters" allows for collapsing the
    # uncategorized buckets; we assert >=3 named buckets.
    named = set(clusters.keys()) - {"uncategorized"}
    assert len(named) >= 3
    # timeout_seconds suggested constant for the top value (30).
    assert clusters["timeout_seconds"]["suggested_constant"] == "DEFAULT_TIMEOUT_S=30"
    assert clusters["timeout_seconds"]["count"] == 3


# ---------------------------------------------------------------------------
# Per-language fixtures — assert findings emerge from a non-Python file
# ---------------------------------------------------------------------------


def _make_js_project(tmp_path: Path) -> Path:
    root = tmp_path / "js"
    _write(
        root / "a.js",
        """
        const TIMEOUT_A = 3600;
        const TIMEOUT_B = 3600;
        const RATIO = 0.95;
        function f() { return 3600; }
    """,
    )
    _write(
        root / "b.js",
        """
        const SIZE = 1024;
        const SIZE2 = 1024;
    """,
    )
    return root


def _make_ts_project(tmp_path: Path) -> Path:
    root = tmp_path / "ts"
    _write(
        root / "a.ts",
        """
        const TIMEOUT_A: number = 3600;
        const TIMEOUT_B: number = 3600;
        const PORT: number = 8080;
    """,
    )
    _write(
        root / "b.ts",
        """
        function f(): number {
            const x = 3600;
            return x;
        }
    """,
    )
    return root


def _make_go_project(tmp_path: Path) -> Path:
    root = tmp_path / "go"
    _write(
        root / "a.go",
        """
        package main

        const TimeoutA = 3600
        const TimeoutB = 3600

        func main() {
            x := 3600
            _ = x
        }
    """,
    )
    return root


def _make_rust_project(tmp_path: Path) -> Path:
    root = tmp_path / "rs"
    _write(
        root / "a.rs",
        """
        const TIMEOUT_A: u32 = 3600;
        const TIMEOUT_B: u32 = 3600;

        fn f() -> u32 { 3600 }
    """,
    )
    return root


def _make_java_project(tmp_path: Path) -> Path:
    root = tmp_path / "java"
    _write(
        root / "A.java",
        """
        public class A {
            static final int TIMEOUT_A = 3600;
            static final int TIMEOUT_B = 3600;
            int f() { return 3600; }
        }
    """,
    )
    return root


def _make_ruby_project(tmp_path: Path) -> Path:
    root = tmp_path / "rb"
    _write(
        root / "a.rb",
        """
        TIMEOUT_A = 3600
        TIMEOUT_B = 3600
        def f
          3600
        end
    """,
    )
    return root


def _make_c_project(tmp_path: Path) -> Path:
    root = tmp_path / "c"
    _write(
        root / "a.c",
        """
        #define UNUSED 0
        int timeout_a = 3600;
        int timeout_b = 3600;
        int f(void) { return 3600; }
    """,
    )
    return root


def _make_csharp_project(tmp_path: Path) -> Path:
    root = tmp_path / "cs"
    _write(
        root / "A.cs",
        """
        public class A {
            const int TimeoutA = 3600;
            const int TimeoutB = 3600;
            public int F() { return 3600; }
        }
    """,
    )
    return root


def _assert_finds_3600(tmp_path: Path, factory) -> None:
    src = factory(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, str(src))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    values = {f["value"]: f["occurrences"] for f in payload["findings"]}
    assert 3600 in values, f"3600 not in findings for {factory.__name__}: {values}"
    assert values[3600] >= 2


def test_javascript_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_js_project)


def test_typescript_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_ts_project)


def test_go_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_go_project)


def test_rust_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_rust_project)


def test_java_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_java_project)


def test_ruby_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_ruby_project)


def test_c_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_c_project)


def test_csharp_fixture_finds_repeated_numbers(tmp_path):
    _assert_finds_3600(tmp_path, _make_csharp_project)


# ---------------------------------------------------------------------------
# --cluster end-to-end via CLI
# ---------------------------------------------------------------------------


def _make_cluster_project(tmp_path: Path) -> Path:
    """Python fixture with values {30, 30, 30, 1024, 200, 0.5}.

    Threshold 1 (--threshold 1) so every value survives the floor.
    """
    root = tmp_path / "proj"
    _write(
        root / "a.py",
        """
        T1 = 30
        T2 = 30
        T3 = 30
        SIZE = 1024
        STATUS = 200
        RATIO = 0.5
    """,
    )
    return root


def test_cluster_flag_produces_named_clusters_cli(tmp_path):
    src = _make_cluster_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, "--cluster", "--threshold", "1", str(src))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "clusters" in payload
    clusters = payload["clusters"]

    # The spec example calls out at least 3 named clusters.
    assert "timeout_seconds" in clusters
    assert clusters["timeout_seconds"]["count"] == 3
    assert clusters["timeout_seconds"]["suggested_constant"] == "DEFAULT_TIMEOUT_S=30"
    assert "size_power_of_two" in clusters
    # Suggested constant for the size cluster's top value.
    assert clusters["size_power_of_two"]["suggested_constant"] == "BUFFER_SIZE=1024"

    # Verdict must mention clusters.
    assert "cluster" in payload["summary"]["verdict"].lower()

    # cluster_count is surfaced into summary.
    assert payload["summary"]["cluster_count"] >= 3


def test_cluster_flag_verdict_mentions_breakdown(tmp_path):
    src = _make_cluster_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, "--cluster", "--threshold", "1", str(src))
    payload = json.loads(result.output)
    verdict = payload["summary"]["verdict"]
    # The verdict is shaped: "N magic numbers across M files; K clusters: name1 (n1), name2 (n2), ..."
    assert "timeout_seconds" in verdict
    assert "clusters:" in verdict


# ---------------------------------------------------------------------------
# Context-aware classifier — fixes the 2026-06-02 systemic FPs
# ---------------------------------------------------------------------------


def test_context_aware_size_limit_breaks_http_status_fp():
    """`len(s) < 200` is a char-limit, not an HTTP status. Context-aware
    classifier must tag it as size_or_limit."""
    assert _cluster_for_value_with_context(200, "if len(s) < 200:") == "size_or_limit"
    assert _cluster_for_value_with_context(200, "_TASK_PREFIX_LEN_CAP = 200") == "size_or_limit"


def test_context_aware_timeout_seconds_breaks_percentage_fp():
    """`sqlite3.connect(timeout=1.0)` is float seconds, not a percentage."""
    assert _cluster_for_value_with_context(1.0, "sqlite3.connect(path, timeout=1.0)") == "timeout_seconds"


def test_context_aware_network_port_explicit_context_only():
    """`port = 443` is a real port (explicit `port` keyword). The 80
    literal inside `_FILE_EXCERPT_LINES = 80` MUST NOT classify as a
    port (no port-context keyword)."""
    assert _cluster_for_value_with_context(443, "port = 443") == "network_port"
    # Negative case: 80 in a line that talks about lines, not ports.
    assert _cluster_for_value_with_context(80, "_FILE_EXCERPT_LINES = 80") == "size_or_limit"


def test_context_aware_falls_back_to_value_only():
    """When no context cue matches, the value-only classifier wins."""
    # Bare line, value 3600 -> timeout_seconds (via value-only fallback).
    assert _cluster_for_value_with_context(3600, "x = 3600") == "timeout_seconds"
    # Bare line, value 1024 -> size_power_of_two (via value-only fallback).
    assert _cluster_for_value_with_context(1024, "y = 1024") == "size_power_of_two"


def test_context_aware_default_empty_line_matches_value_only():
    """Empty / None line MUST behave like the value-only classifier so
    the helper-test guarantee holds (default behavior preserved when
    context not provided)."""
    assert _cluster_for_value_with_context(200, "") == _cluster_for_value(200, "")
    assert _cluster_for_value_with_context(1.0, "") == _cluster_for_value(1.0, "")


def test_suggest_constant_name_size_or_limit_shape():
    assert _suggest_constant_name("size_or_limit", 200) == "MAX_LIMIT_200"
    assert _suggest_constant_name("size_or_limit", 80) == "MAX_LIMIT_80"


def test_cluster_flag_default_off(tmp_path):
    """Default invocation MUST NOT emit a clusters key."""
    src = _make_cluster_project(tmp_path)
    runner = CliRunner()
    result = _invoke_json(runner, "--threshold", "1", str(src))
    payload = json.loads(result.output)
    assert "clusters" not in payload
    assert "cluster_count" not in payload["summary"]
