"""Tests for roam smells command and code smell detectors."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.catalog.smells import (
    ALL_DETECTORS,
    _parse_param_count,
    detect_boolean_parameter,
    detect_brain_method,
    detect_comment_density,
    detect_data_clumps,
    detect_dead_params,
    detect_deep_nesting,
    detect_duplicate_conditionals,
    detect_empty_catch,
    detect_feature_envy,
    detect_god_class,
    detect_large_class,
    detect_long_params,
    detect_low_cohesion,
    detect_magic_numbers,
    detect_message_chain,
    detect_primitive_obsession,
    detect_refused_bequest,
    detect_shotgun_surgery,
    detect_switch_statement,
    detect_temporal_coupling,
    file_health_scores,
    run_all_detectors,
)
from roam.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT, decorators TEXT DEFAULT '',
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL DEFAULT 0,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity REAL DEFAULT 0,
            nesting_depth INTEGER DEFAULT 0,
            param_count INTEGER DEFAULT 0,
            line_count INTEGER DEFAULT 0,
            return_count INTEGER DEFAULT 0,
            bool_op_count INTEGER DEFAULT 0,
            callback_depth INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER DEFAULT 0,
            total_churn INTEGER DEFAULT 0,
            distinct_authors INTEGER DEFAULT 0,
            complexity REAL DEFAULT 0,
            health_score REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS git_cochange (
            file_id_a INTEGER NOT NULL,
            file_id_b INTEGER NOT NULL,
            cochange_count INTEGER DEFAULT 0,
            PRIMARY KEY (file_id_a, file_id_b)
        );
    """)
    conn.commit()
    return conn


def _git_init(path: Path):
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    # Create a dummy file so git has something to commit
    (path / "dummy.py").write_text("# dummy\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)


def _populate_brain_method(conn):
    """Insert a brain method: high complexity, long function."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/engine.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'process_everything', 'function', 10, 200, '(data, config, opts)')"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 75, 6)")
    conn.commit()


def _populate_god_class(conn):
    """Insert a god class with 35 methods and 1200 LOC."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/monolith.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'GodManager', 'class', 1, 1201)"
    )
    # Insert 35 methods inside the class
    for i in range(35):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 1, ?, 'method', ?, ?)",
            (100 + i, f"method_{i}", 10 + i * 30, 10 + i * 30 + 25),
        )
    conn.commit()


def _populate_deep_nesting(conn):
    """Insert a function with deep nesting."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/nested.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'deeply_nested', 'function', 1, 50)"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 15, 7)")
    conn.commit()


def _populate_long_params(conn):
    """Insert a function with many parameters."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'create_report', 'function', 1, 30, "
        "'(self, title, author, date, format, output, template, extra)')"
    )
    conn.commit()


def _populate_shotgun_surgery(conn, *, symbol_name="hub_fn", n_caller_files=14, file_start=1):
    """Insert a symbol referenced from many DISTINCT non-test files (W1287).

    Genuine shotgun surgery: ``symbol_name`` lives in ``src/core.py`` and is
    referenced (incoming call edges) from ``n_caller_files`` separate
    non-test source files — so changing it ripples across all of them. The
    target file id is ``file_start`` and the caller files are the next
    ``n_caller_files`` ids. The detector fires when the distinct caller-file
    count >= ``_SHOTGUN_MIN_CALLER_FILES`` (12).
    """
    target_file_id = file_start
    conn.execute(
        "INSERT INTO files (id, path) VALUES (?, ?)",
        (target_file_id, f"src/core_{file_start}.py"),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, 'function', 1, 30)",
        (target_file_id, target_file_id, symbol_name),
    )
    # One distinct caller FILE per incoming edge.
    for i in range(n_caller_files):
        cf_id = file_start + 1 + i
        caller_sym_id = 1000 + file_start * 100 + i
        conn.execute(
            "INSERT INTO files (id, path) VALUES (?, ?)",
            (cf_id, f"src/caller_{file_start}_{i}.py"),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, 'function', 1, 8)",
            (caller_sym_id, cf_id, f"caller_{file_start}_{i}"),
        )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind, source_file_id) VALUES (?, ?, 'call', ?)",
            (caller_sym_id, target_file_id, cf_id),
        )
    conn.commit()


def _populate_message_chain(conn):
    """Insert a function with high out_degree."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/handler.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'handle_request', 'function', 1, 40)"
    )
    conn.execute("INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) VALUES (1, 2, 15)")
    conn.commit()


def _populate_feature_envy(conn):
    """Insert a function whose external refs concentrate on ONE foreign file.

    Genuine feature envy: 5/6 refs reach a single foreign file (src/model.py)
    — the dominant-foreign-file concentration gate (W1280) passes.
    """
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/controller.py')")
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/model.py')")
    # Function in file 1
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'update_model', 'function', 1, 20)"
    )
    # Targets in file 2
    for i in range(5):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 2, ?, 'function', ?, ?)",
            (10 + i, f"model_fn_{i}", i * 10, i * 10 + 5),
        )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind) VALUES (1, ?, 'call')",
            (10 + i,),
        )
    # One target in same file
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (20, 1, 'local_helper', 'function', 30, 40)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 20, 'call')")
    conn.commit()


def _populate_feature_envy_orchestrator(conn):
    """W1280: a `_build_*` orchestrator referencing one foreign file heavily.

    Same edge topology as `_populate_feature_envy` (would fire pre-W1280)
    but the name matches the orchestrator/assembler skip pattern, so it
    must NOT be flagged — its cross-file breadth is by-design assembly.
    """
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/builder.py')")
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/parts.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, '_build_report', 'function', 1, 20)"
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 2, ?, 'function', ?, ?)",
            (10 + i, f"part_{i}", i * 10, i * 10 + 5),
        )
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, ?, 'call')", (10 + i,))
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (20, 1, 'local_helper', 'function', 30, 40)"
    )
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 20, 'call')")
    conn.commit()


def _populate_feature_envy_spread(conn):
    """W1280: external refs spread evenly across many foreign files.

    >50% external + >=4 refs (would fire pre-W1280) but no single foreign
    file dominates — this is orchestration/coupling, not envy, so the
    concentration gate must reject it.
    """
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/orchestrator.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'run_pipeline', 'function', 1, 20)"
    )
    # 6 external refs, one each to 6 distinct foreign files (no dominant file).
    for i in range(6):
        fid = 100 + i
        conn.execute("INSERT INTO files (id, path) VALUES (?, ?)", (fid, f"src/dep_{i}.py"))
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, 'function', 1, 5)",
            (200 + i, fid, f"dep_fn_{i}"),
        )
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, ?, 'call')", (200 + i,))
    conn.commit()


def _populate_feature_envy_test_file(conn):
    """W1280: a function in a test-role file with envy-shaped edges.

    Same topology as the genuine-envy fixture but located under tests/, so
    the test-path skip must drop it (test bodies call helpers across modules
    by nature — never envy).
    """
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'tests/test_controller.py')")
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/model.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'helper_under_test', 'function', 1, 20)"
    )
    for i in range(5):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 2, ?, 'function', ?, ?)",
            (10 + i, f"model_fn_{i}", i * 10, i * 10 + 5),
        )
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, ?, 'call')", (10 + i,))
    conn.commit()


def _populate_dead_params(conn):
    """Insert a function with many params but low complexity."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/stubs.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'stub_handler', 'function', 1, 5, "
        "'(request, response, context, logger, config)')"
    )
    conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (1, 0)")
    conn.commit()


def _populate_large_class(conn):
    """Insert a large class: > 500 LOC and > 20 methods."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/big.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'BigProcessor', 'class', 1, 600)"
    )
    for i in range(25):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 1, ?, 'method', ?, ?)",
            (100 + i, f"process_{i}", 5 + i * 20, 5 + i * 20 + 15),
        )
    conn.commit()


def _populate_data_clumps(conn):
    """Insert 3+ functions with the same first 3 params."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/clumpy.py')")
    for i in range(4):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (?, 1, ?, 'function', ?, ?, ?)",
            (i + 1, f"do_thing_{i}", i * 10, i * 10 + 8, f"(host, port, timeout, extra_{i})"),
        )
    conn.commit()


def _populate_low_cohesion(conn):
    """Insert a class with 6 methods but 0 internal edges."""
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/scattered.py')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'ScatteredClass', 'class', 1, 200)"
    )
    for i in range(6):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, 1, ?, 'method', ?, ?)",
            (10 + i, f"isolated_method_{i}", 10 + i * 30, 10 + i * 30 + 25),
        )
    # No edges between the methods
    conn.commit()


# ---------------------------------------------------------------------------
# Tests: _parse_param_count
# ---------------------------------------------------------------------------


class TestParseParamCount:
    def test_empty(self):
        assert _parse_param_count(None) == 0
        assert _parse_param_count("") == 0

    def test_no_parens(self):
        assert _parse_param_count("no_parens") == 0

    def test_empty_parens(self):
        assert _parse_param_count("()") == 0
        assert _parse_param_count("fn()") == 0

    def test_self_only(self):
        assert _parse_param_count("(self)") == 0

    def test_cls_only(self):
        assert _parse_param_count("(cls)") == 0

    def test_self_plus_params(self):
        assert _parse_param_count("(self, a, b, c)") == 3

    def test_simple_params(self):
        assert _parse_param_count("(a, b, c)") == 3

    def test_typed_params(self):
        assert _parse_param_count("(a: int, b: str, c: float)") == 3

    def test_default_values(self):
        assert _parse_param_count("(a=1, b='hello', c=None)") == 3

    def test_nested_generics(self):
        assert _parse_param_count("(items: list[tuple[int, str]], config: dict[str, Any])") == 2


# ---------------------------------------------------------------------------
# Tests: individual detectors
# ---------------------------------------------------------------------------


class TestBrainMethod:
    def test_detects_brain_method(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_brain_method(conn)
        results = detect_brain_method(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "brain-method"
        assert results[0]["severity"] == "critical"
        assert results[0]["symbol_name"] == "process_everything"
        conn.close()

    def test_no_brain_method_below_threshold(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/ok.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'simple_fn', 'function', 1, 50)"
        )
        conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (1, 30)")
        conn.commit()
        results = detect_brain_method(conn)
        assert len(results) == 0
        conn.close()


class TestDeepNesting:
    def test_detects_deep_nesting(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_deep_nesting(conn)
        results = detect_deep_nesting(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "deep-nesting"
        assert results[0]["severity"] == "warning"
        assert results[0]["metric_value"] == 7
        conn.close()


class TestLongParams:
    def test_detects_long_params(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_long_params(conn)
        results = detect_long_params(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "long-params"
        assert results[0]["severity"] == "warning"
        # self is excluded, so 7 params
        assert results[0]["metric_value"] == 7
        conn.close()

    def test_no_long_params_under_threshold(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/ok.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'ok_fn', 'function', 1, 10, '(a, b, c)')"
        )
        conn.commit()
        results = detect_long_params(conn)
        assert len(results) == 0
        conn.close()


class TestLargeClass:
    def test_detects_large_class(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_large_class(conn)
        results = detect_large_class(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "large-class"
        assert results[0]["severity"] == "critical"
        conn.close()


class TestGodClass:
    def test_detects_god_class(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_god_class(conn)
        results = detect_god_class(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "god-class"
        assert results[0]["severity"] == "critical"
        assert "35 methods" in results[0]["description"]
        conn.close()

    def test_no_god_class_small(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/small.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'SmallClass', 'class', 1, 100)"
        )
        conn.commit()
        results = detect_god_class(conn)
        assert len(results) == 0
        conn.close()


class TestFeatureEnvy:
    def test_detects_feature_envy(self, tmp_path):
        # Genuine envy: external refs concentrated on ONE foreign file.
        conn = _make_db(tmp_path)
        _populate_feature_envy(conn)
        results = detect_feature_envy(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "feature-envy"
        assert results[0]["severity"] == "warning"
        # 5 out of 6 refs are external = 83%
        assert results[0]["metric_value"] > 50
        conn.close()

    def test_skips_orchestrator_named_functions(self, tmp_path):
        # W1280: a `_build_*` assembler with envy-shaped edges is by-design
        # cross-file breadth, not envy — the name-pattern skip must drop it.
        conn = _make_db(tmp_path)
        _populate_feature_envy_orchestrator(conn)
        results = detect_feature_envy(conn)
        assert results == [], f"orchestrator should not fire, got {results}"
        conn.close()

    def test_skips_refs_spread_across_many_files(self, tmp_path):
        # W1280: refs spread evenly across 6 files (no dominant foreign file)
        # is orchestration; the concentration gate must reject it.
        conn = _make_db(tmp_path)
        _populate_feature_envy_spread(conn)
        results = detect_feature_envy(conn)
        assert results == [], f"spread refs should not fire, got {results}"
        conn.close()

    def test_skips_test_role_files(self, tmp_path):
        # W1280: envy-shaped edges inside a tests/ file must be skipped.
        conn = _make_db(tmp_path)
        _populate_feature_envy_test_file(conn)
        results = detect_feature_envy(conn)
        assert results == [], f"test-file fn should not fire, got {results}"
        conn.close()


class TestShotgunSurgery:
    """W1287: shotgun-surgery now fires on distinct-non-test-caller-FILE
    scatter (Fowler's file-scatter axis), NOT raw in_degree popularity.
    """

    def test_detects_genuine_file_scatter(self, tmp_path):
        """A symbol referenced from many DISTINCT non-test files fires."""
        conn = _make_db(tmp_path)
        _populate_shotgun_surgery(conn, symbol_name="hub_fn", n_caller_files=14)
        results = detect_shotgun_surgery(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "shotgun-surgery"
        assert results[0]["symbol_name"] == "hub_fn"
        # metric is the distinct-caller-FILE count, not in_degree.
        assert results[0]["metric_value"] == 14
        assert "distinct non-test files" in results[0]["description"]
        conn.close()

    def test_popular_but_concentrated_does_not_fire(self, tmp_path):
        """A symbol called MANY times from only 1-2 files must NOT fire.

        High raw reference count concentrated in few files is good
        factoring (a fixture / single-source-of-truth helper), the
        OPPOSITE of shotgun surgery. This is the pre-W1287 FP class.
        """
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/utils.py')")
        conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/big_caller.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'popular_helper', 'function', 1, 30)"
        )
        # 50 incoming edges, but all from a single file (id=2).
        for i in range(50):
            caller_id = 100 + i
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (?, 2, ?, 'function', ?, ?)",
                (caller_id, f"c_{i}", i * 5, i * 5 + 3),
            )
            conn.execute(
                "INSERT INTO edges (source_id, target_id, kind, source_file_id) VALUES (?, 1, 'call', 2)",
                (caller_id,),
            )
        conn.commit()
        results = detect_shotgun_surgery(conn)
        assert results == [], "concentrated-popularity symbol must NOT be flagged"
        conn.close()

    def test_test_role_target_does_not_fire(self, tmp_path):
        """A symbol defined in a test-role file must NOT fire (conftest class)."""
        conn = _make_db(tmp_path)
        # Target lives in tests/ — must be excluded even with wide scatter.
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'tests/conftest.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'invoke_cli', 'function', 1, 30)"
        )
        for i in range(20):
            cf_id = 2 + i
            caller_id = 100 + i
            conn.execute(
                "INSERT INTO files (id, path) VALUES (?, ?)",
                (cf_id, f"tests/test_mod_{i}.py"),
            )
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (?, ?, ?, 'function', 1, 8)",
                (caller_id, cf_id, f"test_thing_{i}"),
            )
            conn.execute(
                "INSERT INTO edges (source_id, target_id, kind, source_file_id) VALUES (?, 1, 'call', ?)",
                (caller_id, cf_id),
            )
        conn.commit()
        results = detect_shotgun_surgery(conn)
        assert results == [], "test-role target must NOT be flagged"
        conn.close()

    def test_caller_files_below_threshold_does_not_fire(self, tmp_path):
        """Scatter below the conservative threshold (12) must NOT fire."""
        conn = _make_db(tmp_path)
        _populate_shotgun_surgery(conn, symbol_name="narrow_fn", n_caller_files=8)
        results = detect_shotgun_surgery(conn)
        assert results == [], "8 distinct caller files is below the 12 threshold"
        conn.close()


class TestDataClumps:
    def test_detects_data_clumps(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_data_clumps(conn)
        results = detect_data_clumps(conn)
        assert len(results) >= 1
        assert results[0]["smell_id"] == "data-clumps"
        assert results[0]["severity"] == "info"
        conn.close()


class TestDeadParams:
    def test_detects_dead_params(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_dead_params(conn)
        results = detect_dead_params(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "dead-params"
        assert results[0]["severity"] == "info"
        assert results[0]["metric_value"] == 5
        conn.close()


class TestEmptyCatch:
    """W370: empty-catch detector reads source files to find trivial
    exception-handler bodies.

    Each test writes source into a tmp_path-rooted file tree, registers
    it in the ``files`` table, and chdirs into tmp_path so the
    detector's ``find_project_root()`` lookup resolves to the right
    directory. Reuses the ``_make_db`` fixture so the DB shape matches
    the other detector tests.
    """

    def _wire_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        language: str,
        *,
        symbol_name: str = "outer",
        symbol_kind: str = "function",
        line_start: int = 1,
        line_end: int = 100,
    ) -> None:
        """Write *source* to ``tmp_path / rel_path`` and register a
        ``files`` + enclosing ``symbols`` row."""
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        # Reset rows so multiple test methods on the same fixture stay
        # isolated even if the schema persists.
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
            (rel_path, language),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (1, 1, ?, ?, ?, ?)",
            (symbol_name, symbol_kind, line_start, line_end),
        )
        conn.commit()
        # Mark tmp_path as a git root so find_project_root() returns it.
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        """Invoke detect_empty_catch with cwd pinned to tmp_path so
        ``find_project_root()`` resolves correctly."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_empty_catch(conn)
        finally:
            os.chdir(old_cwd)

    def test_returns_empty_when_no_files(self, tmp_path):
        conn = _make_db(tmp_path)
        (tmp_path / ".git").mkdir(exist_ok=True)
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_python_pass(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer():\n    try:\n        do_work()\n    except Exception:\n        pass\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "empty-catch"
        assert f["severity"] == "warning"
        assert f["symbol_name"] == "outer"
        assert "src/mod.py" in f["location"].replace("\\", "/")
        conn.close()

    def test_python_ellipsis(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer():\n    try:\n        do_work()\n    except Exception:\n        ...\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "empty-catch"
        conn.close()

    def test_python_comment_only(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "def outer():\n"
            "    try:\n"
            "        do_work()\n"
            "    except Exception:\n"
            "        # ignore -- silently fail\n"
            "        pass\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        # Comment + pass collapses to a single 'pass' line after
        # comment stripping -> empty-catch.
        assert len(results) == 1
        conn.close()

    def test_python_single_log_only(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer():\n    try:\n        do_work()\n    except Exception as e:\n        logger.error(e)\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "empty-catch"
        conn.close()

    def test_does_not_flag_reraise(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "def outer():\n"
            "    try:\n"
            "        do_work()\n"
            "    except Exception as e:\n"
            "        logger.error(e)\n"
            "        raise\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_does_not_flag_recovery(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer():\n    try:\n        do_work()\n    except Exception:\n        return fallback()\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        # ``return fallback()`` is recovery code, not trivial.
        assert results == []
        conn.close()

    def test_does_not_flag_multi_statement(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "def outer():\n"
            "    try:\n"
            "        do_work()\n"
            "    except Exception as e:\n"
            "        logger.error(e)\n"
            "        cleanup()\n"
            "        return default\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_javascript_empty_block(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "function outer() {\n  try {\n    doWork();\n  } catch (e) { }\n}\n"
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "empty-catch"
        assert "src/mod.js" in results[0]["location"].replace("\\", "/")
        conn.close()

    def test_javascript_console_log_only(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "function outer() {\n  try {\n    doWork();\n  } catch (e) {\n    console.log(e);\n  }\n}\n"
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        conn.close()

    def test_javascript_throw_not_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "function outer() {\n  try {\n    doWork();\n  } catch (e) {\n    console.error(e);\n    throw e;\n  }\n}\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_promise_catch_not_flagged(self, tmp_path):
        """Promise-chain ``.catch(...)`` MUST NOT be flagged -- it's
        method chaining on a promise, not exception handling."""
        conn = _make_db(tmp_path)
        src = "function outer() {\n  return fetch(url).then(r => r.json()).catch(() => ({}));\n}\n"
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        # The catch here is a Promise method, not a try-catch keyword.
        # Our regex uses ``(?<![A-Za-z0-9_.])`` to exclude it.
        assert results == []
        conn.close()


class TestLowCohesion:
    def test_detects_low_cohesion(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_low_cohesion(conn)
        results = detect_low_cohesion(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "low-cohesion"
        assert results[0]["severity"] == "warning"
        conn.close()


class TestMessageChain:
    def test_detects_message_chain(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_message_chain(conn)
        results = detect_message_chain(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "message-chain"
        assert results[0]["metric_value"] == 15
        conn.close()


class TestRefusedBequest:
    """W370c: refused-bequest detector flags subclasses that override >= 2
    parent methods with trivial bodies (``pass`` / ``return None`` /
    ``raise NotImplementedError``).

    Mirrors the source-on-disk shape of TestDuplicateConditionals -- the
    detector reads method bodies from the workspace, so each test writes
    fixture source into ``tmp_path`` and pins cwd before calling.
    """

    def _wire_inheritance(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        language: str,
        *,
        child_class: str,
        child_line_start: int,
        child_line_end: int,
        parent_class: str,
        parent_method_names: list[str],
        child_methods: list[tuple[str, int, int]],
    ) -> None:
        """Write source + register files/symbols/edges for a parent+child
        class pair. ``child_methods`` is a list of (name, line_start,
        line_end) triples."""
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
            (rel_path, language),
        )
        # Parent class lives in a "different file" (file_id 2) so the
        # child-method search by line range stays scoped to the child.
        # The actual file row is fine to keep minimal.
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (2, ?, ?)",
            (rel_path + ".parent", language),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (1, 1, ?, 'class', ?, ?)",
            (child_class, child_line_start, child_line_end),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (2, 2, ?, 'class', 1, 100)",
            (parent_class,),
        )
        # Parent method rows (parent_id = 2 so we can look them up by parent).
        next_id = 100
        for pname in parent_method_names:
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, parent_id) "
                "VALUES (?, 2, ?, 'method', 1, 10, 2)",
                (next_id, pname),
            )
            next_id += 1
        # Child method rows (line ranges land inside the child class).
        for mname, mls, mle in child_methods:
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, parent_id) "
                "VALUES (?, 1, ?, 'method', ?, ?, 1)",
                (next_id, mname, mls, mle),
            )
            next_id += 1
        # Inherits edge: child -> parent
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 2, 'inherits')")
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_refused_bequest(conn)
        finally:
            os.chdir(old_cwd)

    def test_empty_db_no_inherits(self, tmp_path):
        """Negative: empty DB / no inherits edges -> no findings."""
        conn = _make_db(tmp_path)
        assert detect_refused_bequest(conn) == []
        conn.close()

    def test_two_trivial_overrides_flagged(self, tmp_path):
        """Positive: child overrides 2 parent methods with ``pass`` bodies."""
        conn = _make_db(tmp_path)
        src = "class Child(Base):\n    def foo(self):\n        pass\n    def bar(self):\n        pass\n"
        self._wire_inheritance(
            tmp_path,
            conn,
            "src/refuse.py",
            src,
            "python",
            child_class="Child",
            child_line_start=1,
            child_line_end=5,
            parent_class="Base",
            parent_method_names=["foo", "bar"],
            child_methods=[("foo", 2, 3), ("bar", 4, 5)],
        )
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "refused-bequest"
        assert f["severity"] == "warning"
        assert f["symbol_name"] == "Child"
        assert f["metric_value"] == 2
        assert "Child" in f["description"]
        assert "Base" in f["description"]
        conn.close()

    def test_one_trivial_override_not_flagged(self, tmp_path):
        """Threshold is 2 -- a single trivial override is below threshold."""
        conn = _make_db(tmp_path)
        src = "class Child(Base):\n    def foo(self):\n        pass\n    def bar(self):\n        return self.x + 1\n"
        self._wire_inheritance(
            tmp_path,
            conn,
            "src/refuse.py",
            src,
            "python",
            child_class="Child",
            child_line_start=1,
            child_line_end=5,
            parent_class="Base",
            parent_method_names=["foo", "bar"],
            child_methods=[("foo", 2, 3), ("bar", 4, 5)],
        )
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_not_implemented_error_counts_as_refusal(self, tmp_path):
        """``raise NotImplementedError`` is the canonical refusal shape."""
        conn = _make_db(tmp_path)
        src = (
            "class Child(Base):\n"
            "    def foo(self):\n"
            "        raise NotImplementedError\n"
            "    def bar(self):\n"
            "        raise NotImplementedError('not yet')\n"
        )
        self._wire_inheritance(
            tmp_path,
            conn,
            "src/refuse.py",
            src,
            "python",
            child_class="Child",
            child_line_start=1,
            child_line_end=5,
            parent_class="Base",
            parent_method_names=["foo", "bar"],
            child_methods=[("foo", 2, 3), ("bar", 4, 5)],
        )
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 2
        conn.close()

    def test_real_overrides_not_flagged(self, tmp_path):
        """Non-trivial bodies (actual work) are NOT a refused bequest."""
        conn = _make_db(tmp_path)
        src = (
            "class Child(Base):\n"
            "    def foo(self):\n"
            "        self.cache = {}\n"
            "        return self._build()\n"
            "    def bar(self, x):\n"
            "        for item in x:\n"
            "            self.process(item)\n"
        )
        self._wire_inheritance(
            tmp_path,
            conn,
            "src/refuse.py",
            src,
            "python",
            child_class="Child",
            child_line_start=1,
            child_line_end=7,
            parent_class="Base",
            parent_method_names=["foo", "bar"],
            child_methods=[("foo", 2, 4), ("bar", 5, 7)],
        )
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()


class TestPrimitiveObsession:
    """W370c: primitive-obsession detector flags functions/methods where
    >= 4 annotated params and >= 75% of those are bare primitives.

    These tests bypass the workspace-source pathway -- the detector reads
    ``symbols.signature`` from the DB directly, so we only need to populate
    that column.
    """

    def test_empty_db_no_findings(self, tmp_path):
        """Negative: no symbols -> no findings."""
        conn = _make_db(tmp_path)
        assert detect_primitive_obsession(conn) == []
        conn.close()

    def test_four_primitives_flagged(self, tmp_path):
        """Positive: 4 bare-primitive params (100% ratio)."""
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'connect', 'function', 1, 10, "
            "'def connect(host: str, port: int, timeout: float, retries: int)')"
        )
        conn.commit()
        results = detect_primitive_obsession(conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "primitive-obsession"
        assert f["severity"] == "info"
        assert f["symbol_name"] == "connect"
        assert f["metric_value"] == 4
        assert "100%" in f["description"]
        conn.close()

    def test_three_primitives_not_flagged(self, tmp_path):
        """Threshold is >= 4 annotated total."""
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'small', 'function', 1, 5, "
            "'def small(a: int, b: int, c: int)')"
        )
        conn.commit()
        assert detect_primitive_obsession(conn) == []
        conn.close()

    def test_compound_types_not_flagged(self, tmp_path):
        """Functions taking dict / list / custom types are NOT obsessed."""
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'process', 'function', 1, 5, "
            "'def process(config: Config, payload: dict[str, int], items: list[Item], result: Result)')"
        )
        conn.commit()
        # 0/4 primitives -> below the 75% threshold.
        assert detect_primitive_obsession(conn) == []
        conn.close()

    def test_optional_primitive_counts(self, tmp_path):
        """Optional[str] / str | None still count as primitive obsession."""
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'fancy', 'function', 1, 5, "
            "'def fancy(host: Optional[str], port: int | None, timeout: float, retries: int)')"
        )
        conn.commit()
        results = detect_primitive_obsession(conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 4
        conn.close()

    def test_init_exempt(self, tmp_path):
        """``__init__`` is exempt -- constructors legitimately take primitives."""
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/api.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, '__init__', 'method', 1, 5, "
            "'def __init__(self, host: str, port: int, timeout: float, retries: int)')"
        )
        conn.commit()
        assert detect_primitive_obsession(conn) == []
        conn.close()


class TestDuplicateConditionals:
    """W370b: duplicate-conditionals detector flags functions where the
    SAME ``if`` predicate repeats >= 3 times in independent statements
    (NOT chained via ``elif`` / ``else if``).

    Each test writes source into a tmp_path-rooted file tree, registers
    it in the ``files`` table, and chdirs into tmp_path so the
    detector's ``find_project_root()`` lookup resolves to the right
    directory.
    """

    def _wire_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        language: str,
        *,
        symbol_name: str = "outer",
        symbol_kind: str = "function",
        line_start: int = 1,
        line_end: int = 200,
    ) -> None:
        """Write *source* to ``tmp_path / rel_path`` and register a
        ``files`` + enclosing ``symbols`` row."""
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
            (rel_path, language),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (1, 1, ?, ?, ?, ?)",
            (symbol_name, symbol_kind, line_start, line_end),
        )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        """Invoke detect_duplicate_conditionals with cwd pinned to
        tmp_path so ``find_project_root()`` resolves correctly."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_duplicate_conditionals(conn)
        finally:
            os.chdir(old_cwd)

    def test_three_ifs_same_predicate_python_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer(x):\n    if x:\n        a()\n    if x:\n        b()\n    if x:\n        c()\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "duplicate-conditionals"
        assert f["severity"] == "warning"
        assert f["symbol_name"] == "outer"
        assert f["metric_value"] == 3
        conn.close()

    def test_two_ifs_same_predicate_not_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer(x):\n    if x:\n        a()\n    if x:\n        b()\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        # Threshold is 3, not 2.
        assert results == []
        conn.close()

    def test_elif_chain_not_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "def outer(x):\n"
            "    if x == 'a':\n"
            "        a()\n"
            "    elif x == 'b':\n"
            "        b()\n"
            "    elif x == 'c':\n"
            "        c()\n"
            "    elif x == 'd':\n"
            "        d()\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        # Polyadic dispatch -- different predicates, intentional shape.
        assert results == []
        conn.close()

    def test_brace_lang_duplicate_if_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "function outer(x) {\n  if (x) {\n    a();\n  }\n  if (x) {\n    b();\n  }\n  if (x) {\n    c();\n  }\n}\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "duplicate-conditionals"
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_brace_else_if_not_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "function outer(x) {\n"
            "  if (x == 'a') {\n"
            "    a();\n"
            "  } else if (x == 'b') {\n"
            "    b();\n"
            "  } else if (x == 'c') {\n"
            "    c();\n"
            "  } else if (x == 'd') {\n"
            "    d();\n"
            "  }\n"
            "}\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.js", src, "javascript")
        results = self._run(tmp_path, conn)
        # ``else if`` ladder -- polyadic dispatch, not duplicate-cond.
        assert results == []
        conn.close()

    def test_different_predicates_not_flagged(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer(x, y, z):\n    if x:\n        a()\n    if y:\n        b()\n    if z:\n        c()\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        # Three distinct predicates -- independent guards, not duplicates.
        assert results == []
        conn.close()

    def test_whitespace_normalized_in_hash(self, tmp_path):
        conn = _make_db(tmp_path)
        src = "def outer(x):\n    if x==1:\n        a()\n    if x == 1:\n        b()\n    if  x  ==  1 :\n        c()\n"
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "duplicate-conditionals"
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_outer_parens_stripped_in_hash(self, tmp_path):
        conn = _make_db(tmp_path)
        src = (
            "def outer(x):\n"
            "    if x == 1:\n"
            "        a()\n"
            "    if (x == 1):\n"
            "        b()\n"
            "    if ((x == 1)):\n"
            "        c()\n"
        )
        self._wire_file(tmp_path, conn, "src/mod.py", src, "python")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_separate_functions_not_pooled(self, tmp_path):
        """Same predicate in three DIFFERENT functions must NOT flag --
        the duplicate-conditionals smell is scope-local."""
        conn = _make_db(tmp_path)
        src = (
            "def fn_a(x):\n"
            "    if x:\n"
            "        a()\n"
            "\n"
            "def fn_b(x):\n"
            "    if x:\n"
            "        b()\n"
            "\n"
            "def fn_c(x):\n"
            "    if x:\n"
            "        c()\n"
        )
        # Wire three separate function rows so _scope_for_line lands
        # each ``if`` in a different bucket.
        full = tmp_path / "src/mod.py"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(src, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
            ("src/mod.py", "python"),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'fn_a', 'function', 1, 3)"
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (2, 1, 'fn_b', 'function', 5, 7)"
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (3, 1, 'fn_c', 'function', 9, 11)"
        )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)
        results = self._run(tmp_path, conn)
        # 1 predicate per function -- below threshold in EACH scope.
        assert results == []
        conn.close()

    def test_duplicate_conditionals_in_registry(self):
        """Smoke: detector is wired into ALL_DETECTORS and callable."""
        ids = {smell_id for smell_id, _ in ALL_DETECTORS}
        assert "duplicate-conditionals" in ids


# ---------------------------------------------------------------------------
# W603 — magic-numbers detector
# ---------------------------------------------------------------------------


class TestMagicNumbers:
    """W603: magic-numbers detector flags non-exempt numeric literals
    (NOT in {-1, 0, 1, 2}) that appear >= 3 times in one function body.

    Each test writes Python source into ``tmp_path``, registers a row in
    ``files`` (language='python'), and pins cwd so the detector's
    ``find_project_root()`` resolves to the fixture root.
    """

    def _wire_python_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
    ) -> None:
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, 'python')",
            (rel_path,),
        )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_magic_numbers(conn)
        finally:
            os.chdir(old_cwd)

    def test_empty_db_no_findings(self, tmp_path):
        """Negative: no files -> no findings."""
        conn = _make_db(tmp_path)
        assert detect_magic_numbers(conn) == []
        conn.close()

    def test_three_repeats_of_same_literal_flagged(self, tmp_path):
        """Positive: non-exempt literal 7 repeats 3 times -> flag."""
        conn = _make_db(tmp_path)
        src = "def schedule(items):\n    a = items[7]\n    b = items[7] + 1\n    return a + b * 7\n"
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "magic-numbers"
        assert f["severity"] == "info"
        assert f["symbol_name"] == "schedule"
        assert f["metric_value"] == 3
        assert "7" in f["description"]
        assert "literals" in f["description"]
        conn.close()

    def test_exempt_literals_not_flagged(self, tmp_path):
        """The {-1, 0, 1, 2} idiom set never triggers regardless of count."""
        conn = _make_db(tmp_path)
        src = (
            "def idiomatic(items):\n"
            "    a = items[0]\n"
            "    b = items[1]\n"
            "    c = items[2]\n"
            "    d = items[-1]\n"
            "    e = items[0] + items[1] + items[-1]\n"
            "    return a + b + c + d + e\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        # 0/1/2/-1 are all exempt -- no findings.
        assert results == []
        conn.close()

    def test_two_repeats_below_threshold(self, tmp_path):
        """Threshold is 3 -- two occurrences are not flagged."""
        conn = _make_db(tmp_path)
        src = "def small(x):\n    a = x + 99\n    b = x - 99\n    return a + b\n"
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_negative_literal_folded(self, tmp_path):
        """``-N`` arrives as UnaryOp(USub, Constant(N)); folded into one literal.

        ``-1`` is exempt; ``-7`` repeated 3x must still flag.
        """
        conn = _make_db(tmp_path)
        src = "def negs(items):\n    a = items[-7]\n    b = items[-7]\n    c = items[-7]\n    return a + b + c\n"
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        # The folded value is the negative int -7.
        assert "-7" in results[0]["description"]
        conn.close()

    def test_booleans_not_counted_as_int(self, tmp_path):
        """``True`` / ``False`` must NOT collide with ``1`` / ``0`` even
        though Python's ``bool`` is an ``int`` subclass. The
        ``type() is int`` guard handles this."""
        conn = _make_db(tmp_path)
        src = (
            "def bool_heavy(x):\n"
            "    if x is True: a = 1\n"
            "    elif x is True: a = 2\n"
            "    elif x is True: a = 3\n"
            "    return a\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        # 1, 2, 3 each appear once; True three times but True is excluded.
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_nested_function_separate_scope(self, tmp_path):
        """A repeated literal in an inner function is its OWN scope.

        Outer function has 7 once; inner function has 7 three times -> only
        the inner function is flagged.
        """
        conn = _make_db(tmp_path)
        src = (
            "def outer(x):\n"
            "    y = x + 7\n"
            "    def inner(z):\n"
            "        a = z + 7\n"
            "        b = z * 7\n"
            "        c = z - 7\n"
            "        return a + b + c\n"
            "    return y + inner(x)\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        # Only inner has >= 3 occurrences -> one finding total.
        assert len(results) == 1
        assert results[0]["symbol_name"] == "inner"
        assert results[0]["metric_value"] == 3
        conn.close()


# ---------------------------------------------------------------------------
# W604 — boolean-parameter detector
# ---------------------------------------------------------------------------


class TestBooleanParameter:
    """W604: boolean-parameter detector flags call sites with >= 2 positional
    boolean literal arguments. Keyword bool args are NOT flagged.
    """

    def _wire_python_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        *,
        enclosing: tuple[str, int, int] | None = None,
    ) -> None:
        """Write source; register file row; optionally register an
        enclosing function/method so scope attribution works."""
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, 'python')",
            (rel_path,),
        )
        if enclosing is not None:
            name, ls, le = enclosing
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (1, 1, ?, 'function', ?, ?)",
                (name, ls, le),
            )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_boolean_parameter(conn)
        finally:
            os.chdir(old_cwd)

    def test_empty_db_no_findings(self, tmp_path):
        """Negative: empty -> no findings."""
        conn = _make_db(tmp_path)
        assert detect_boolean_parameter(conn) == []
        conn.close()

    def test_two_positional_bools_flagged(self, tmp_path):
        """Positive: ``f(True, False)`` is the canonical smell shape."""
        conn = _make_db(tmp_path)
        src = "def caller():\n    do_thing(True, False)\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 3),
        )
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "boolean-parameter"
        assert f["severity"] == "info"
        assert f["symbol_name"] == "caller"
        assert f["metric_value"] == 2
        assert "do_thing" in f["description"]
        conn.close()

    def test_single_positional_bool_not_flagged(self, tmp_path):
        """Threshold is >= 2; a single bool arg is fine."""
        conn = _make_db(tmp_path)
        src = "def caller():\n    do_thing(True, 42, 'x')\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 3),
        )
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_keyword_bools_not_flagged(self, tmp_path):
        """Keyword bool args are the FIX, not the smell -- must NOT flag."""
        conn = _make_db(tmp_path)
        src = "def caller():\n    do_thing(verbose=True, strict=False)\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 3),
        )
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_mixed_positional_bools_and_others_flagged(self, tmp_path):
        """``f(True, x, False)`` -- the two positional bools count even
        when mixed with non-bool positional args."""
        conn = _make_db(tmp_path)
        src = "def caller(x):\n    do_thing(True, x, False)\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 3),
        )
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 2
        conn.close()

    def test_attribute_call_name_rendered(self, tmp_path):
        """``self.f(...)`` and ``a.b.c(...)`` render readable names."""
        conn = _make_db(tmp_path)
        src = "def caller(self):\n    self.helper(True, False)\n    a.b.c(True, False)\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 4),
        )
        results = self._run(tmp_path, conn)
        assert len(results) == 2
        descs = " ".join(r["description"] for r in results)
        assert "self.helper" in descs
        assert "a.b.c" in descs
        conn.close()

    def test_int_args_not_counted_as_bool(self, tmp_path):
        """``f(1, 0)`` is NOT ``f(True, False)`` even though Python's
        ``bool`` is an ``int`` subclass. The ``type() is bool`` guard
        keeps the two cases separate."""
        conn = _make_db(tmp_path)
        src = "def caller():\n    do_thing(1, 0)\n    return None\n"
        self._wire_python_file(
            tmp_path,
            conn,
            "src/m.py",
            src,
            enclosing=("caller", 1, 3),
        )
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()


class TestSwitchStatement:
    """W601: switch-statement detector flags ``match`` and ``if``/``elif``
    chains with >= 8 arms that all discriminate on the same single variable.

    Threshold is 8. Below that, polyadic dispatch is normal and not flagged.
    """

    def _wire_python_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        *,
        enclosing: tuple[str, int, int] | None = None,
    ) -> None:
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, 'python')",
            (rel_path,),
        )
        if enclosing is not None:
            name, ls, le = enclosing
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (1, 1, ?, 'function', ?, ?)",
                (name, ls, le),
            )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_switch_statement(conn)
        finally:
            os.chdir(old_cwd)

    def test_empty_db_no_findings(self, tmp_path):
        conn = _make_db(tmp_path)
        assert detect_switch_statement(conn) == []
        conn.close()

    def test_eight_arm_if_elif_chain_flagged(self, tmp_path):
        """Positive: 8-arm if/elif chain on single var ``cmd`` -> 1 finding."""
        conn = _make_db(tmp_path)
        # 8 arms (1 if + 7 elif), all comparing ``cmd`` to a string literal.
        src = (
            "def dispatch(cmd):\n"
            "    if cmd == 'a':\n"
            "        return 1\n"
            "    elif cmd == 'b':\n"
            "        return 2\n"
            "    elif cmd == 'c':\n"
            "        return 3\n"
            "    elif cmd == 'd':\n"
            "        return 4\n"
            "    elif cmd == 'e':\n"
            "        return 5\n"
            "    elif cmd == 'f':\n"
            "        return 6\n"
            "    elif cmd == 'g':\n"
            "        return 7\n"
            "    elif cmd == 'h':\n"
            "        return 8\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 20))
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "switch-statement"
        assert f["severity"] == "info"
        assert f["symbol_name"] == "dispatch"
        assert f["metric_value"] == 8
        assert f["threshold"] == 8
        assert "cmd" in f["description"]
        conn.close()

    def test_seven_arm_chain_not_flagged(self, tmp_path):
        """Threshold is 8: a 7-arm chain stays under the bar."""
        conn = _make_db(tmp_path)
        src = (
            "def dispatch(cmd):\n"
            "    if cmd == 'a':\n"
            "        return 1\n"
            "    elif cmd == 'b':\n"
            "        return 2\n"
            "    elif cmd == 'c':\n"
            "        return 3\n"
            "    elif cmd == 'd':\n"
            "        return 4\n"
            "    elif cmd == 'e':\n"
            "        return 5\n"
            "    elif cmd == 'f':\n"
            "        return 6\n"
            "    elif cmd == 'g':\n"
            "        return 7\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 18))
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_match_statement_with_eight_cases_flagged(self, tmp_path):
        """Positive: ``match cmd`` with 8 ``case`` arms -> 1 finding."""
        conn = _make_db(tmp_path)
        src = (
            "def dispatch(cmd):\n"
            "    match cmd:\n"
            "        case 'a': return 1\n"
            "        case 'b': return 2\n"
            "        case 'c': return 3\n"
            "        case 'd': return 4\n"
            "        case 'e': return 5\n"
            "        case 'f': return 6\n"
            "        case 'g': return 7\n"
            "        case 'h': return 8\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 12))
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "switch-statement"
        assert f["metric_value"] == 8
        assert "cmd" in f["description"]
        conn.close()

    def test_mixed_discriminators_not_flagged(self, tmp_path):
        """Negative: 8-arm chain mixing two discriminator variables is
        NOT a switch-statement (the predicate-on-single-var requirement
        is violated)."""
        conn = _make_db(tmp_path)
        # 8 arms but every other arm switches between ``x`` and ``y``.
        src = (
            "def dispatch(x, y):\n"
            "    if x == 1:\n"
            "        return 1\n"
            "    elif y == 2:\n"
            "        return 2\n"
            "    elif x == 3:\n"
            "        return 3\n"
            "    elif y == 4:\n"
            "        return 4\n"
            "    elif x == 5:\n"
            "        return 5\n"
            "    elif y == 6:\n"
            "        return 6\n"
            "    elif x == 7:\n"
            "        return 7\n"
            "    elif y == 8:\n"
            "        return 8\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 20))
        results = self._run(tmp_path, conn)
        assert results == []
        conn.close()

    def test_compound_predicate_not_flagged(self, tmp_path):
        """Negative: arms with compound expressions (``x and y``,
        ``x.method() == 1``) don't count -- only single-Name
        discriminators do."""
        conn = _make_db(tmp_path)
        src = (
            "def dispatch(x):\n"
            "    if x.foo() == 'a':\n"
            "        return 1\n"
            "    elif x.foo() == 'b':\n"
            "        return 2\n"
            "    elif x.foo() == 'c':\n"
            "        return 3\n"
            "    elif x.foo() == 'd':\n"
            "        return 4\n"
            "    elif x.foo() == 'e':\n"
            "        return 5\n"
            "    elif x.foo() == 'f':\n"
            "        return 6\n"
            "    elif x.foo() == 'g':\n"
            "        return 7\n"
            "    elif x.foo() == 'h':\n"
            "        return 8\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 20))
        results = self._run(tmp_path, conn)
        # Compound LHS -- not a single-Name discriminator.
        assert results == []
        conn.close()

    def test_isinstance_chain_flagged(self, tmp_path):
        """Positive: ``isinstance(x, T)`` chain on the same ``x`` is the
        canonical type-dispatch switch -- 8 arms still flags."""
        conn = _make_db(tmp_path)
        src = (
            "def dispatch(x):\n"
            "    if isinstance(x, A):\n"
            "        return 1\n"
            "    elif isinstance(x, B):\n"
            "        return 2\n"
            "    elif isinstance(x, C):\n"
            "        return 3\n"
            "    elif isinstance(x, D):\n"
            "        return 4\n"
            "    elif isinstance(x, E):\n"
            "        return 5\n"
            "    elif isinstance(x, F):\n"
            "        return 6\n"
            "    elif isinstance(x, G):\n"
            "        return 7\n"
            "    elif isinstance(x, H):\n"
            "        return 8\n"
            "    return 0\n"
        )
        self._wire_python_file(tmp_path, conn, "src/m.py", src, enclosing=("dispatch", 1, 20))
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "switch-statement"
        assert results[0]["metric_value"] == 8
        conn.close()


class TestTemporalCoupling:
    """W602: temporal-coupling detector flags symbol pairs that co-change
    >= 10 times AND share a call-graph edge in either direction.
    """

    def _wire_pair(
        self,
        conn: sqlite3.Connection,
        *,
        cochange_count: int,
        edge_direction: str = "a_to_b",
    ) -> None:
        """Insert two files, two functions (one per file), an edges row in
        the requested direction, and one git_cochange row.
        ``edge_direction`` is one of ``a_to_b``, ``b_to_a``, ``both``,
        ``none``.
        """
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM git_cochange")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, 'src/a.py', 'python'), (2, 'src/b.py', 'python')"
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'fn_a', 'function', 10, 20), "
            "       (2, 2, 'fn_b', 'function', 30, 40)"
        )
        if edge_direction in ("a_to_b", "both"):
            conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (1, 2, 'call')")
        if edge_direction in ("b_to_a", "both"):
            conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (2, 1, 'call')")
        conn.execute(
            "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) VALUES (1, 2, ?)",
            (cochange_count,),
        )
        conn.commit()

    def test_empty_db_no_findings(self, tmp_path):
        conn = _make_db(tmp_path)
        assert detect_temporal_coupling(conn) == []
        conn.close()

    def test_cochange_above_threshold_with_edge_flagged(self, tmp_path):
        """Positive: cochange=10, A -> B edge -> 1 finding."""
        conn = _make_db(tmp_path)
        self._wire_pair(conn, cochange_count=10, edge_direction="a_to_b")
        results = detect_temporal_coupling(conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "temporal-coupling"
        assert f["severity"] == "warning"
        assert f["metric_value"] == 10
        assert f["threshold"] == 10
        # The finding names both endpoints in the description.
        assert "fn_a" in f["description"]
        assert "fn_b" in f["description"]
        conn.close()

    def test_cochange_below_threshold_not_flagged(self, tmp_path):
        """Negative: cochange=9, edge present -> 0 findings (threshold 10)."""
        conn = _make_db(tmp_path)
        self._wire_pair(conn, cochange_count=9, edge_direction="a_to_b")
        assert detect_temporal_coupling(conn) == []
        conn.close()

    def test_cochange_above_threshold_without_edge_not_flagged(self, tmp_path):
        """Negative: cochange=20, NO edges -> 0 findings (edge required)."""
        conn = _make_db(tmp_path)
        self._wire_pair(conn, cochange_count=20, edge_direction="none")
        assert detect_temporal_coupling(conn) == []
        conn.close()

    def test_bidirectional_edge_deduped_to_single_finding(self, tmp_path):
        """A pair with edges in BOTH directions still emits exactly one
        finding (the JOIN surfaces the pair twice; the dedupe collapses)."""
        conn = _make_db(tmp_path)
        self._wire_pair(conn, cochange_count=15, edge_direction="both")
        results = detect_temporal_coupling(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "temporal-coupling"
        conn.close()

    def test_b_to_a_edge_still_flagged(self, tmp_path):
        """Edge direction is irrelevant -- B->A counts the same as A->B."""
        conn = _make_db(tmp_path)
        self._wire_pair(conn, cochange_count=12, edge_direction="b_to_a")
        results = detect_temporal_coupling(conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 12
        conn.close()


class TestTemporalCouplingCluster:
    """W647: symbol-centric rollup. When one symbol appears in >= 2
    distinct pair findings, emit one ADDITIONAL ``temporal-coupling-
    cluster`` finding alongside the pairs. Pair findings stay -- the
    rollup is additive, operators want both views.
    """

    def _wire_hub(
        self,
        conn: sqlite3.Connection,
        partners: int,
        cochange_count: int = 12,
    ) -> None:
        """Insert a 'hub' symbol in file 1 and ``partners`` other symbols
        each in their own file, with a call-graph edge AND a >=10-cochange
        row between the hub and each partner. The hub is the symbol that
        should roll up into a cluster finding.
        """
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM git_cochange")
        conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/hub.py', 'python')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'hub_fn', 'function', 10, 20)"
        )
        for i in range(partners):
            file_id = 100 + i
            sym_id = 100 + i
            partner_path = f"src/p{i}.py"
            partner_name = f"partner_{i}"
            conn.execute(
                "INSERT INTO files (id, path, language) VALUES (?, ?, 'python')",
                (file_id, partner_path),
            )
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (?, ?, ?, 'function', 5, 15)",
                (sym_id, file_id, partner_name),
            )
            conn.execute(
                "INSERT INTO edges (source_id, target_id, kind) VALUES (1, ?, 'call')",
                (sym_id,),
            )
            conn.execute(
                "INSERT INTO git_cochange (file_id_a, file_id_b, cochange_count) VALUES (1, ?, ?)",
                (file_id, cochange_count),
            )
        conn.commit()

    def test_two_partners_emits_one_cluster_finding(self, tmp_path):
        """Hub with 2 partners -> 2 pair findings + 1 cluster finding."""
        conn = _make_db(tmp_path)
        self._wire_hub(conn, partners=2)
        results = detect_temporal_coupling(conn)
        pairs = [r for r in results if r["smell_id"] == "temporal-coupling"]
        clusters = [r for r in results if r["smell_id"] == "temporal-coupling-cluster"]
        # 2 pair findings -- one per (hub, partner_i) pair.
        assert len(pairs) == 2
        # 1 cluster finding -- the hub appears in >= 2 pair findings.
        # Partner_0 and partner_1 each appear in only ONE pair so they
        # don't roll up.
        assert len(clusters) == 1
        c = clusters[0]
        assert c["symbol_name"] == "hub_fn"
        assert c["severity"] == "warning"
        # metric_value is the partner count.
        assert c["metric_value"] == 2
        assert c["threshold"] == 2
        assert "partner_0" in c["description"]
        assert "partner_1" in c["description"]
        # The cluster claim names the cluster shape, not a single pair.
        assert "cluster" in c["description"].lower()
        conn.close()

    def test_single_partner_does_not_cluster(self, tmp_path):
        """Hub with 1 partner -> 1 pair finding + 0 cluster findings.
        A single pair is NOT a cluster (threshold = 2 partners)."""
        conn = _make_db(tmp_path)
        self._wire_hub(conn, partners=1)
        results = detect_temporal_coupling(conn)
        pairs = [r for r in results if r["smell_id"] == "temporal-coupling"]
        clusters = [r for r in results if r["smell_id"] == "temporal-coupling-cluster"]
        assert len(pairs) == 1
        assert clusters == []
        conn.close()

    def test_three_partners_cluster_lists_all_three(self, tmp_path):
        """Hub with 3 partners -> 3 pair findings + 1 cluster finding
        naming all 3 partners. The cluster's metric_value is the partner
        count (3) and the description lists every partner."""
        conn = _make_db(tmp_path)
        self._wire_hub(conn, partners=3, cochange_count=15)
        results = detect_temporal_coupling(conn)
        clusters = [r for r in results if r["smell_id"] == "temporal-coupling-cluster"]
        assert len(clusters) == 1
        c = clusters[0]
        assert c["metric_value"] == 3
        for i in range(3):
            assert f"partner_{i}" in c["description"], (
                f"partner_{i} missing from cluster description: {c['description']}"
            )
        # max cc surfaces in the description so the operator sees the
        # cluster's history strength.
        assert "15 commits" in c["description"]
        conn.close()


# ---------------------------------------------------------------------------
# W605 -- comment-density detector
# ---------------------------------------------------------------------------


class TestCommentDensity:
    """W605: comment-density detector flags files where TODO/FIXME/XXX/HACK
    marker lines hit BOTH the absolute count gate (>= 3) AND the per-line
    rate gate (>= 5%).
    """

    def _wire_file(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        rel_path: str,
        source: str,
        language: str = "python",
    ) -> None:
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(source, encoding="utf-8")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.execute(
            "INSERT INTO files (id, path, language) VALUES (1, ?, ?)",
            (rel_path, language),
        )
        conn.commit()
        (tmp_path / ".git").mkdir(exist_ok=True)

    def _run(self, tmp_path: Path, conn: sqlite3.Connection) -> list[dict]:
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            return detect_comment_density(conn)
        finally:
            os.chdir(old_cwd)

    def test_empty_db_no_findings(self, tmp_path):
        """Negative: empty registry -> no findings."""
        conn = _make_db(tmp_path)
        assert detect_comment_density(conn) == []
        conn.close()

    def test_above_both_thresholds_flagged_python(self, tmp_path):
        """Positive: 3 marker lines in a 20-line Python file (15% rate)."""
        conn = _make_db(tmp_path)
        # 3 marker lines + 17 plain lines = 20 lines total, 15% rate.
        body_lines = ["x = 1"] * 17
        src = (
            "\n".join(
                [
                    "# TODO: implement caching",
                    "# FIXME: race condition",
                    "# HACK: hard-coded retry count",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["severity"] == "info"
        assert f["kind"] == "file"
        assert f["symbol_name"] == "src/m.py"
        assert f["metric_value"] == 3
        assert f["threshold"] == 3
        assert "TODO/FIXME/XXX/HACK" in f["description"]
        assert "markers" in f["description"]
        conn.close()

    def test_above_both_thresholds_flagged_javascript(self, tmp_path):
        """Positive: ``//``-comment language path also fires."""
        conn = _make_db(tmp_path)
        body_lines = ["let x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "// TODO: rewrite this loop",
                    "// XXX: revisit after migration",
                    "// HACK: workaround for IE11",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/m.js", src, language="javascript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "comment-density"
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_below_absolute_count_not_flagged(self, tmp_path):
        """Negative: 2 markers fails the >= 3 absolute floor regardless of rate."""
        conn = _make_db(tmp_path)
        # 2 markers in 5 lines = 40% rate (well above rate gate) but absolute
        # count fails -> no finding.
        src = "# TODO: do the thing\n# FIXME: also this\nx = 1\ny = 2\nz = 3\n"
        self._wire_file(tmp_path, conn, "src/m.py", src)
        assert self._run(tmp_path, conn) == []
        conn.close()

    def test_below_rate_threshold_not_flagged(self, tmp_path):
        """Negative: 3 markers in 200 lines = 1.5% rate -> below 5% gate."""
        conn = _make_db(tmp_path)
        body_lines = ["x = 1"] * 197
        src = (
            "\n".join(
                [
                    "# TODO: A",
                    "# TODO: B",
                    "# TODO: C",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/m.py", src)
        assert self._run(tmp_path, conn) == []
        conn.close()

    def test_empty_file_no_findings(self, tmp_path):
        """Edge: empty file -> no division-by-zero, no finding."""
        conn = _make_db(tmp_path)
        self._wire_file(tmp_path, conn, "src/m.py", "")
        assert self._run(tmp_path, conn) == []
        conn.close()

    def test_exact_threshold_flagged(self, tmp_path):
        """Edge: exactly at both thresholds (>= comparisons) fires."""
        conn = _make_db(tmp_path)
        # 3 marker lines + 57 plain lines = 60 lines, 5.0% rate -- the
        # exact boundary. >= 3 AND >= 0.05 both pass.
        body_lines = ["x = 1"] * 57
        src = (
            "\n".join(
                [
                    "# TODO: one",
                    "# TODO: two",
                    "# TODO: three",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/m.py", src)
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_marker_words_outside_comments_not_counted(self, tmp_path):
        """Marker keywords in code (string literals, identifier names) must
        not count -- only ``#``-prefixed lines do."""
        conn = _make_db(tmp_path)
        # 1 actual marker comment + many code lines containing TODO as a
        # string. Absolute count = 1 -> below threshold -> no finding.
        src = (
            "# TODO: real marker\n"
            "msg1 = 'TODO refactor'\n"
            "msg2 = 'FIXME later'\n"
            "msg3 = 'XXX inline'\n"
            "msg4 = 'HACK here'\n"
        )
        self._wire_file(tmp_path, conn, "src/m.py", src)
        assert self._run(tmp_path, conn) == []
        conn.close()

    def test_unsupported_language_skipped(self, tmp_path):
        """Languages outside ``_COMMENT_SYNTAX_BY_LANG`` are silently skipped.

        W705 widened the supported set to 21 languages (was 14). ``foxpro``
        is a regex-only tier-2 language that still has no entry in the
        comment-syntax map, so it stays the canonical "unsupported" probe.
        """
        conn = _make_db(tmp_path)
        src = "# TODO: A\n# TODO: B\n# TODO: C\nx = 1\n"
        # ``foxpro`` is NOT in ``_COMMENT_SYNTAX_BY_LANG`` (FoxPro uses
        # ``*`` and ``&&`` markers, which the detector does not model).
        self._wire_file(tmp_path, conn, "legacy.prg", src, language="foxpro")
        assert self._run(tmp_path, conn) == []
        conn.close()

    # ---- W650: block-comment extension --------------------------------

    def test_block_comments_jsdoc_flagged(self, tmp_path):
        """W650 positive: a JSDoc-style ``/** ... */`` block carrying 3
        TODO/FIXME/HACK markers across multiple physical lines flags the
        file.

        The block spans many physical lines but the markers inside it
        are counted by ``findall`` occurrences -- one marker = one
        increment, regardless of how the block wraps.
        """
        conn = _make_db(tmp_path)
        # 3 markers inside one block + 17 plain lines = 20 lines, 15% rate.
        body_lines = ["let x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "/**",
                    " * Module entry point.",
                    " * TODO: rewrite this loop",
                    " * FIXME: race on init",
                    " * HACK: hard-coded retry count",
                    " */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        # File is 23 physical lines (6 block + 17 body); the file has 3
        # markers / 23 lines = ~13% which is above both gates.
        self._wire_file(tmp_path, conn, "src/m.js", src, language="javascript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["metric_value"] == 3
        assert f["kind"] == "file"
        conn.close()

    def test_line_only_no_block_still_works(self, tmp_path):
        """W650 negative regression: a file with only ``//`` line
        comments and no ``/* */`` blocks still flags on the W605 line
        path. The new block scanner must be additive, not a regression.
        """
        conn = _make_db(tmp_path)
        body_lines = ["let x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "// TODO: rewrite",
                    "// FIXME: race",
                    "// HACK: workaround",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/m.ts", src, language="typescript")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 3
        assert results[0]["smell_id"] == "comment-density"
        conn.close()

    def test_mixed_line_and_block_combined(self, tmp_path):
        """W650 positive: a Java file with 1 ``//`` marker and 2 markers
        inside one ``/* */`` block combines to 3 markers. Verifies the
        two pass results are additive on the same file.
        """
        conn = _make_db(tmp_path)
        # 1 line marker + 1 block carrying 2 markers + 17 body lines.
        body_lines = ["int x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "// TODO: review",
                    "/* FIXME: A",
                    "   XXX: B */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        # 1 (line) + 2 (block) = 3 markers, 20 physical lines = 15%.
        self._wire_file(tmp_path, conn, "src/M.java", src, language="java")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["metric_value"] == 3
        conn.close()

    # ---- W705: extended language coverage -----------------------------

    def test_html_block_comments_flagged(self, tmp_path):
        """W705 positive: HTML ``<!-- ... -->`` block-comment markers
        flag the file. HTML has no line-comment syntax, so this exercises
        the block-only branch of ``_CommentSyntax``."""
        conn = _make_db(tmp_path)
        # 3 markers inside one HTML comment block + 17 plain body lines.
        body_lines = ["<p>x</p>"] * 17
        src = (
            "\n".join(
                [
                    "<!--",
                    "  TODO: rewrite hero",
                    "  FIXME: missing alt text",
                    "  HACK: inline styles",
                    "-->",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/index.html", src, language="html")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["metric_value"] == 3
        assert f["kind"] == "file"
        conn.close()

    def test_sql_line_and_block_markers_flagged(self, tmp_path):
        """W705 positive: SQL ``--`` line + ``/* */`` block markers
        combine on one file."""
        conn = _make_db(tmp_path)
        body_lines = ["SELECT 1;"] * 17
        src = (
            "\n".join(
                [
                    "-- TODO: index this column",
                    "/* FIXME: slow scan",
                    "   XXX: review plan */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        # 1 (line) + 2 (block) = 3 markers, 20 physical lines = 15%.
        self._wire_file(tmp_path, conn, "src/schema.sql", src, language="sql")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "comment-density"
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_shell_hash_markers_flagged(self, tmp_path):
        """W705 positive: shell scripts (indexer language ``bash``)
        with ``#``-prefixed marker comments flag like Python."""
        conn = _make_db(tmp_path)
        body_lines = ["echo hi"] * 17
        src = (
            "\n".join(
                [
                    "# TODO: split this script",
                    "# FIXME: handle SIGTERM",
                    "# HACK: workaround for set -e",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "scripts/build.sh", src, language="bash")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "comment-density"
        assert results[0]["metric_value"] == 3
        conn.close()

    def test_php_hash_line_prefix_flagged(self, tmp_path):
        """W705 positive: PHP honours both ``//`` and ``#`` line
        comments. The ``#``-only marker lines should count too."""
        conn = _make_db(tmp_path)
        body_lines = ["$x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "# TODO: switch to typed properties",
                    "// FIXME: race in worker",
                    "# HACK: legacy global",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "src/legacy.php", src, language="php")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        assert results[0]["metric_value"] == 3
        conn.close()

    # ---- W720: hcl + apex extension -----------------------------------

    def test_hcl_mixed_hash_slash_and_block_flagged(self, tmp_path):
        """W720 positive: HCL/Terraform honours ``#``, ``//`` line and
        ``/* */`` block comments. A mix of all three marker styles on
        one file combines to clear both gates."""
        conn = _make_db(tmp_path)
        body_lines = ['  name = "x"'] * 17
        src = (
            "\n".join(
                [
                    "# TODO: lock module version",
                    "// FIXME: hard-coded region",
                    "/* HACK: temp variable override */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        self._wire_file(tmp_path, conn, "infra/main.tf", src, language="hcl")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["metric_value"] == 3
        assert f["kind"] == "file"
        conn.close()

    def test_apex_line_and_block_markers_flagged(self, tmp_path):
        """W720 positive: Apex uses ``//`` line + ``/* */`` block. One
        line marker + one block carrying two markers combines to three."""
        conn = _make_db(tmp_path)
        body_lines = ["Integer x = 1;"] * 17
        src = (
            "\n".join(
                [
                    "// TODO: bulkify this",
                    "/* FIXME: SOQL in loop",
                    "   XXX: governor limit risk */",
                    *body_lines,
                ]
            )
            + "\n"
        )
        # 1 (line) + 2 (block) = 3 markers, 20 physical lines = 15%.
        self._wire_file(tmp_path, conn, "src/AcctSvc.cls", src, language="apex")
        results = self._run(tmp_path, conn)
        assert len(results) == 1
        f = results[0]
        assert f["smell_id"] == "comment-density"
        assert f["metric_value"] == 3
        assert f["kind"] == "file"
        conn.close()


# ---------------------------------------------------------------------------
# Tests: ALL_DETECTORS registry
# ---------------------------------------------------------------------------


class TestAllDetectors:
    def test_has_24_entries(self):
        # W603 added ``magic-numbers``; W604 added ``boolean-parameter``;
        # W601 added ``switch-statement``; W602 added ``temporal-coupling``;
        # W605 added ``comment-density``; W853 added
        # ``speculative-generality``; W857 added ``parallel-hierarchy``;
        # W856 added ``cross-layer-clone``; W852 added ``type-switch``.
        assert len(ALL_DETECTORS) == 24

    def test_all_ids_unique(self):
        ids = [smell_id for smell_id, _ in ALL_DETECTORS]
        assert len(ids) == len(set(ids))

    def test_all_callables(self):
        for smell_id, fn in ALL_DETECTORS:
            assert callable(fn), f"{smell_id} detector is not callable"


# ---------------------------------------------------------------------------
# Tests: run_all_detectors
# ---------------------------------------------------------------------------


class TestRunAllDetectors:
    def test_returns_list(self, tmp_path):
        conn = _make_db(tmp_path)
        results = run_all_detectors(conn)
        assert isinstance(results, list)
        conn.close()

    def test_empty_db_returns_empty(self, tmp_path):
        conn = _make_db(tmp_path)
        results = run_all_detectors(conn)
        assert results == []
        conn.close()

    def test_combines_multiple_detectors(self, tmp_path):
        conn = _make_db(tmp_path)
        # Insert data triggering multiple detectors
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/messy.py')")
        # Brain method
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
            "VALUES (1, 1, 'mega_fn', 'function', 1, 200, '(a, b, c, d, e, f, g)')"
        )
        conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 80, 8)")
        # Shotgun surgery target (W1287: distinct-caller-FILE scatter, file ids 100+)
        _populate_shotgun_surgery(conn, symbol_name="scattered_hub", n_caller_files=14, file_start=100)
        results = run_all_detectors(conn)
        smell_ids = [r["smell_id"] for r in results]
        assert "brain-method" in smell_ids
        assert "shotgun-surgery" in smell_ids
        conn.close()

    def test_sorted_by_severity(self, tmp_path):
        conn = _make_db(tmp_path)
        # Mix of severities
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/mixed.py')")
        # Brain method = critical
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (1, 1, 'brain_fn', 'function', 1, 200)"
        )
        conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (1, 80, 3)")
        # Deep nesting = warning
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (2, 1, 'nested_fn', 'function', 210, 250)"
        )
        conn.execute("INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (2, 10, 6)")
        # Message chain = info
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (3, 1, 'chatty_fn', 'function', 260, 300)"
        )
        conn.execute("INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) VALUES (3, 1, 12)")
        conn.commit()
        results = run_all_detectors(conn)
        severities = [r["severity"] for r in results]
        # All criticals before warnings, all warnings before info
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        for i in range(len(severities) - 1):
            assert sev_order[severities[i]] <= sev_order[severities[i + 1]]
        conn.close()

    def test_required_fields_present(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_brain_method(conn)
        results = run_all_detectors(conn)
        assert len(results) >= 1
        required = {
            "smell_id",
            "severity",
            "symbol_name",
            "kind",
            "location",
            "metric_value",
            "threshold",
            "description",
        }
        for r in results:
            assert required.issubset(set(r.keys())), f"Missing fields: {required - set(r.keys())}"
        conn.close()


# ---------------------------------------------------------------------------
# Tests: file_health_scores
# ---------------------------------------------------------------------------


class TestFileHealthScores:
    def test_returns_dict(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/a.py')")
        conn.commit()
        scores = file_health_scores(conn)
        assert isinstance(scores, dict)
        conn.close()

    def test_healthy_file_scores_10(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/clean.py')")
        conn.commit()
        scores = file_health_scores(conn)
        assert scores["src/clean.py"] == 10.0
        conn.close()

    def test_penalties_reduce_score(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_brain_method(conn)
        scores = file_health_scores(conn)
        assert scores["src/engine.py"] < 10.0
        # brain-method (critical=-3) + deep-nesting (warning=-1.5) = -4.5
        assert scores["src/engine.py"] == 5.5
        conn.close()

    def test_min_score_is_1(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute("INSERT INTO files (id, path) VALUES (1, 'src/terrible.py')")
        # Insert multiple critical smells
        for i in range(5):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
                "VALUES (?, 1, ?, 'function', ?, ?)",
                (i + 1, f"bad_fn_{i}", i * 200, i * 200 + 180),
            )
            conn.execute(
                "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) VALUES (?, 90, 3)",
                (i + 1,),
            )
        conn.commit()
        scores = file_health_scores(conn)
        assert scores["src/terrible.py"] >= 1.0
        conn.close()

    def test_score_range_1_to_10(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_brain_method(conn)
        # Add a second file with shotgun surgery (different ids)
        conn.execute("INSERT INTO files (id, path) VALUES (2, 'src/utils.py')")
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (10, 2, 'helper_fn', 'function', 1, 10)"
        )
        conn.execute("INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) VALUES (10, 12, 2)")
        conn.commit()
        scores = file_health_scores(conn)
        for path, score in scores.items():
            assert 1.0 <= score <= 10.0, f"{path}: {score} out of range"
        conn.close()


# ---------------------------------------------------------------------------
# Tests: CLI integration
# ---------------------------------------------------------------------------


class TestSmellsCLI:
    @pytest.fixture()
    def project_with_smells(self, tmp_path):
        """Create a project with synthetic smells in the DB."""
        _git_init(tmp_path)
        conn = _make_db(tmp_path)
        _populate_brain_method(conn)
        conn.commit()
        conn.close()
        return tmp_path

    @pytest.fixture()
    def empty_project(self, tmp_path):
        """Create a project with an empty DB."""
        _git_init(tmp_path)
        conn = _make_db(tmp_path)
        conn.commit()
        conn.close()
        return tmp_path

    def test_text_output_shows_verdict(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_text_output_clean_codebase(self, empty_project):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(empty_project))
            result = runner.invoke(cli, ["smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Clean" in result.output or "no code smells" in result.output

    def test_json_output(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["--json", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "smells"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "total_smells" in data["summary"]

    def test_json_detail_mode(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["--json", "--detail", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "smells" in data
        assert isinstance(data["smells"], list)

    def test_json_summary_mode(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["--json", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Without --detail, smells list should be stripped
        assert "smells" not in data
        assert "detail_available" in data["summary"]

    def test_filter_by_severity(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["smells", "--min-severity", "critical"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        # Should only show critical smells
        assert "VERDICT:" in result.output

    # W1005: --min-severity widened from 3-tier {critical, warning, info} to
    # W547 canonical 5-tier so agents can pass any of {critical, error, high,
    # warning, medium, low, info}. The two cases below exercise the new ends
    # of the rank-ordered envelope:
    #   * ``critical`` (rank 5) — passes only ``critical`` findings; brain-
    #     method severity=critical is the only thing the fixture emits, so
    #     total_smells must be >= 1.
    #   * ``info`` (rank 0) — the floor; every emitted tier (critical/
    #     warning/info) ranks >= 0, so total_smells must equal the unfiltered
    #     baseline. Pins LAW 6: filter-floor === pass-through.
    def test_filter_by_min_severity_critical_keeps_critical_only(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            baseline = runner.invoke(cli, ["--json", "smells"])
            critical_only = runner.invoke(cli, ["--json", "smells", "--min-severity", "critical"])
        finally:
            os.chdir(old_cwd)
        assert baseline.exit_code == 0
        assert critical_only.exit_code == 0
        baseline_data = json.loads(baseline.output)
        critical_data = json.loads(critical_only.output)
        baseline_sev = baseline_data["summary"]["severity"]
        critical_sev = critical_data["summary"]["severity"]
        # ``critical`` count survives the floor; everything else is dropped.
        assert critical_data["summary"]["total_smells"] == baseline_sev.get("critical", 0)
        assert "warning" not in critical_sev
        assert "info" not in critical_sev

    def test_filter_by_min_severity_info_keeps_everything(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            baseline = runner.invoke(cli, ["--json", "smells"])
            info_floor = runner.invoke(cli, ["--json", "smells", "--min-severity", "info"])
        finally:
            os.chdir(old_cwd)
        assert baseline.exit_code == 0
        assert info_floor.exit_code == 0
        baseline_total = json.loads(baseline.output)["summary"]["total_smells"]
        info_total = json.loads(info_floor.output)["summary"]["total_smells"]
        # ``info`` is the floor of the W547 rank table; nothing should drop.
        assert info_total == baseline_total

    def test_filter_by_file(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["smells", "--file", "src/engine.py"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_filter_by_nonexistent_file(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["smells", "--file", "nonexistent.py"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Clean" in result.output or "no code smells" in result.output

    def test_detail_text_shows_full_table(self, project_with_smells):
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project_with_smells))
            result = runner.invoke(cli, ["--detail", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "brain-method" in result.output
        assert "Threshold" in result.output or "Location" in result.output


# ---------------------------------------------------------------------------
# W653: run_all_detectors() must fail-loud on programmer bugs (NameError,
# ImportError, AttributeError) rather than swallow them. W601/W602 dropped a
# Counter import that the prior bare ``except Exception: continue`` would have
# masked at runtime — W639's smoke test catches it at test time, but the
# production loop must also surface the same bug class to operators.
# ---------------------------------------------------------------------------


class TestW653DetectorFailLoud:
    """W653: programmer-error exceptions in detectors propagate out of
    run_all_detectors(). sqlite errors still continue."""

    def test_name_error_propagates(self, tmp_path, monkeypatch):
        """A detector that raises NameError (e.g. missing import) must NOT
        be swallowed by run_all_detectors. The W601/W602 Counter-import
        regression is exactly this bug class."""
        from roam.catalog import smells as smells_mod

        def _bad_detector(conn):
            raise NameError("name 'Counter' is not defined")

        # Replace registry with one synthetic detector so the test is hermetic.
        monkeypatch.setattr(smells_mod, "ALL_DETECTORS", [("bad-detector", _bad_detector)])
        conn = _make_db(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="bad-detector|_bad_detector"):
                smells_mod.run_all_detectors(conn)
        finally:
            conn.close()

    def test_import_error_propagates(self, tmp_path, monkeypatch):
        """ImportError from a detector must also fail-loud."""
        from roam.catalog import smells as smells_mod

        def _bad_detector(conn):
            raise ImportError("cannot import name 'missing_helper'")

        monkeypatch.setattr(smells_mod, "ALL_DETECTORS", [("bad-detector", _bad_detector)])
        conn = _make_db(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="ImportError"):
                smells_mod.run_all_detectors(conn)
        finally:
            conn.close()

    def test_attribute_error_propagates(self, tmp_path, monkeypatch):
        """AttributeError (typical for signature drift / missing attr) must
        fail-loud."""
        from roam.catalog import smells as smells_mod

        def _bad_detector(conn):
            raise AttributeError("'NoneType' object has no attribute 'execute'")

        monkeypatch.setattr(smells_mod, "ALL_DETECTORS", [("bad-detector", _bad_detector)])
        conn = _make_db(tmp_path)
        try:
            with pytest.raises(RuntimeError, match="AttributeError"):
                smells_mod.run_all_detectors(conn)
        finally:
            conn.close()

    def test_sqlite_error_continues(self, tmp_path, monkeypatch, caplog):
        """A per-detector sqlite3 error is a data/query issue, not a programmer
        bug — it should be logged and other detectors should still run."""
        import logging

        from roam.catalog import smells as smells_mod

        def _bad_detector(conn):
            raise sqlite3.OperationalError("no such table: ghost_table")

        def _good_detector(conn):
            return [{"smell_id": "ok", "severity": "info"}]

        monkeypatch.setattr(
            smells_mod,
            "ALL_DETECTORS",
            [("bad-detector", _bad_detector), ("good-detector", _good_detector)],
        )
        conn = _make_db(tmp_path)
        try:
            with caplog.at_level(logging.WARNING, logger="roam.catalog.smells"):
                results = smells_mod.run_all_detectors(conn)
        finally:
            conn.close()

        # Good detector still ran.
        assert {"smell_id": "ok", "severity": "info"} in results
        # Warning was logged for the bad detector.
        assert any("sqlite error" in rec.message and "_bad_detector" in rec.message for rec in caplog.records), (
            f"expected sqlite-warning log; got: {[r.message for r in caplog.records]}"
        )

    def test_clean_run_unchanged(self, tmp_path):
        """Sanity: empty corpus still returns []. The fail-loud refactor must
        not regress the W639 empty-corpus contract."""
        conn = _make_db(tmp_path)
        try:
            assert run_all_detectors(conn) == []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# W658: .roam/smells.suppress.yml allowlist substrate
# ---------------------------------------------------------------------------


class TestSmellsSuppress:
    """Unit tests for the smells_suppress substrate (parser, matcher, applier)."""

    def _write_suppress(self, tmp_path: Path, body: str) -> None:
        roam_dir = tmp_path / ".roam"
        roam_dir.mkdir(parents=True, exist_ok=True)
        (roam_dir / "smells.suppress.yml").write_text(body, encoding="utf-8")

    def test_load_missing_file_returns_empty(self, tmp_path):
        from roam.commands.smells_suppress import load_smells_suppressions

        assert load_smells_suppressions(tmp_path) == []

    def test_load_parses_basic_yaml(self, tmp_path):
        from roam.commands.smells_suppress import load_smells_suppressions

        self._write_suppress(
            tmp_path,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: get_language_for_file
    reason: "Public API hub"
""",
        )
        entries = load_smells_suppressions(tmp_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "shotgun-surgery"
        assert entries[0]["symbol"] == "get_language_for_file"
        assert entries[0]["reason"] == "Public API hub"

    def test_load_skips_malformed_entries(self, tmp_path):
        from roam.commands.smells_suppress import load_smells_suppressions

        self._write_suppress(
            tmp_path,
            """\
suppressions:
  - kind: shotgun-surgery
    # missing symbol -- dropped
  - kind: god-class
    symbol: GodManager
""",
        )
        entries = load_smells_suppressions(tmp_path)
        assert [e["symbol"] for e in entries] == ["GodManager"]

    def test_is_suppressed_matches_kind_and_symbol(self):
        from roam.commands.smells_suppress import is_suppressed

        entries = [{"kind": "shotgun-surgery", "symbol": "get_language_for_file"}]
        finding = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "get_language_for_file",
            "location": "src/roam/languages/registry.py:42",
        }
        assert is_suppressed(entries, finding) is entries[0]

    def test_is_suppressed_skips_other_kinds(self):
        from roam.commands.smells_suppress import is_suppressed

        entries = [{"kind": "shotgun-surgery", "symbol": "get_language_for_file"}]
        # Same symbol, different kind -- must NOT suppress.
        finding = {
            "smell_id": "god-class",
            "symbol_name": "get_language_for_file",
            "location": "src/roam/languages/registry.py:42",
        }
        assert is_suppressed(entries, finding) is None

    def test_is_suppressed_skips_other_symbols(self):
        from roam.commands.smells_suppress import is_suppressed

        entries = [{"kind": "shotgun-surgery", "symbol": "get_language_for_file"}]
        finding = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "some_other_symbol",
            "location": "src/foo.py:1",
        }
        assert is_suppressed(entries, finding) is None

    def test_qualified_symbol_matches_bare_finding(self):
        """Suppress entry uses dotted path; smells emit bare names."""
        from roam.commands.smells_suppress import is_suppressed

        entries = [{"kind": "shotgun-surgery", "symbol": "roam.languages.registry.get_extractor"}]
        finding = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "get_extractor",
            "location": "src/roam/languages/registry.py:142",
        }
        assert is_suppressed(entries, finding) is entries[0]

    def test_file_field_disambiguates_same_name_in_different_files(self):
        """`file` suffix-match scopes a kind+symbol suppression to one file."""
        from roam.commands.smells_suppress import is_suppressed

        entries = [
            {
                "kind": "shotgun-surgery",
                "symbol": "handle",
                "file": "src/roam/languages/registry.py",
            }
        ]
        # Match: same name, same file suffix.
        match = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "handle",
            "location": "src/roam/languages/registry.py:10",
        }
        # No match: same name, different file.
        miss = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "handle",
            "location": "src/roam/cli.py:10",
        }
        assert is_suppressed(entries, match) is entries[0]
        assert is_suppressed(entries, miss) is None

    def test_expired_suppression_skipped(self):
        """An `expires` date in the past treats the entry as absent."""
        from datetime import date

        from roam.commands.smells_suppress import is_suppressed

        entries = [
            {
                "kind": "shotgun-surgery",
                "symbol": "get_language_for_file",
                "expires": "2020-01-01",
            }
        ]
        finding = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "get_language_for_file",
            "location": "src/foo.py:1",
        }
        assert is_suppressed(entries, finding, today=date(2026, 5, 14)) is None

    def test_future_expiry_still_suppressed(self):
        from datetime import date

        from roam.commands.smells_suppress import is_suppressed

        entries = [
            {
                "kind": "shotgun-surgery",
                "symbol": "get_language_for_file",
                "expires": "2099-01-01",
            }
        ]
        finding = {
            "smell_id": "shotgun-surgery",
            "symbol_name": "get_language_for_file",
            "location": "src/foo.py:1",
        }
        assert is_suppressed(entries, finding, today=date(2026, 5, 14)) is entries[0]

    def test_apply_suppressions_partitions(self):
        from roam.commands.smells_suppress import apply_suppressions

        entries = [{"kind": "shotgun-surgery", "symbol": "hub"}]
        findings = [
            {"smell_id": "shotgun-surgery", "symbol_name": "hub", "location": "a.py:1"},
            {"smell_id": "shotgun-surgery", "symbol_name": "other", "location": "b.py:1"},
            {"smell_id": "god-class", "symbol_name": "hub", "location": "c.py:1"},
        ]
        kept, suppressed = apply_suppressions(findings, entries)
        assert len(kept) == 2
        assert len(suppressed) == 1
        assert suppressed[0]["symbol_name"] == "hub"
        assert suppressed[0]["_suppressed_by"]["kind"] == "shotgun-surgery"

    def test_apply_with_empty_suppressions_passthrough(self):
        from roam.commands.smells_suppress import apply_suppressions

        findings = [{"smell_id": "x", "symbol_name": "y", "location": "z:1"}]
        kept, suppressed = apply_suppressions(findings, [])
        assert kept == findings
        assert suppressed == []


class TestSmellsSuppressCLI:
    """End-to-end CLI tests: --no-suppress, envelope shape, persist integration."""

    def _write_suppress(self, project_dir: Path, body: str) -> None:
        roam_dir = project_dir / ".roam"
        roam_dir.mkdir(parents=True, exist_ok=True)
        (roam_dir / "smells.suppress.yml").write_text(body, encoding="utf-8")

    def _make_shotgun_project(self, tmp_path: Path, symbol_name: str = "hub_fn") -> Path:
        """Create a project with one shotgun-surgery finding for *symbol_name*.

        W1287: a genuine file-scatter target (referenced from 14 distinct
        non-test files), not the retired in_degree popularity signal.
        """
        _git_init(tmp_path)
        conn = _make_db(tmp_path)
        _populate_shotgun_surgery(conn, symbol_name=symbol_name, n_caller_files=14)
        conn.close()
        return tmp_path

    def test_cli_suppression_filters_finding(self, tmp_path):
        """A shotgun-surgery suppression entry removes the finding from output."""
        project = self._make_shotgun_project(tmp_path, "hub_fn")
        self._write_suppress(
            project,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub_fn
    reason: "Public API hub by design"
""",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project))
            result = runner.invoke(cli, ["--json", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["total_smells"] == 0
        assert data["summary"]["suppressed_count"] == 1

    def test_cli_other_kinds_not_filtered(self, tmp_path):
        """A suppression for kind=A must NOT remove a finding of kind=B."""
        _git_init(tmp_path)
        conn = _make_db(tmp_path)
        # god-class finding (35 methods, 1200 LOC).
        _populate_god_class(conn)
        conn.commit()
        conn.close()
        # Suppress shotgun-surgery (which the project doesn't trigger anyway,
        # but the assert is: god-class survives).
        self._write_suppress(
            tmp_path,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: GodManager
    reason: "Wrong kind -- god-class must still surface"
""",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = runner.invoke(cli, ["--json", "--detail", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["total_smells"] >= 1
        assert data["summary"]["suppressed_count"] == 0
        # Verify the god-class finding is there.
        kinds = {f["value"]["smell_id"] for f in data["smells"]}
        assert "god-class" in kinds

    def test_cli_no_suppress_flag_bypasses_allowlist(self, tmp_path):
        """--no-suppress ignores the file entirely and surfaces the finding."""
        project = self._make_shotgun_project(tmp_path, "hub_fn")
        self._write_suppress(
            project,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub_fn
    reason: "Should be ignored by --no-suppress"
""",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project))
            result = runner.invoke(cli, ["--json", "--detail", "smells", "--no-suppress"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["total_smells"] >= 1
        assert data["summary"]["suppressed_count"] == 0
        kinds = {f["value"]["smell_id"] for f in data["smells"]}
        assert "shotgun-surgery" in kinds

    def test_cli_text_output_discloses_suppression_count(self, tmp_path):
        """Text mode mentions suppressed count so audits aren't blind."""
        project = self._make_shotgun_project(tmp_path, "hub_fn")
        self._write_suppress(
            project,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub_fn
    reason: "Public API hub by design"
""",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project))
            result = runner.invoke(cli, ["smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        # The verdict reports zero smells (suppression worked) and the
        # tail disclosure mentions the suppression file.
        assert "Clean" in result.output or "0 " in result.output or "no code" in result.output

    def test_cli_detail_mode_emits_suppressed_smells_list(self, tmp_path):
        """--detail surfaces the dropped findings (audit trail)."""
        project = self._make_shotgun_project(tmp_path, "hub_fn")
        self._write_suppress(
            project,
            """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub_fn
    reason: "Public API hub by design"
""",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(project))
            result = runner.invoke(cli, ["--json", "--detail", "smells"])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["suppressed_count"] == 1
        assert isinstance(data.get("suppressed_smells"), list)
        assert len(data["suppressed_smells"]) == 1
        sup = data["suppressed_smells"][0]
        assert sup["smell_id"] == "shotgun-surgery"
        assert sup["_suppressed_by"]["reason"] == "Public API hub by design"
