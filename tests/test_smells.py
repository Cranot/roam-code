"""Tests for roam smells command and code smell detectors."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.catalog.smells import (
    ALL_DETECTORS,
    run_all_detectors,
    file_health_scores,
    detect_brain_method,
    detect_deep_nesting,
    detect_long_params,
    detect_large_class,
    detect_god_class,
    detect_feature_envy,
    detect_shotgun_surgery,
    detect_data_clumps,
    detect_dead_params,
    detect_empty_catch,
    detect_low_cohesion,
    detect_message_chain,
    detect_refused_bequest,
    detect_primitive_obsession,
    detect_duplicate_conditionals,
    _parse_param_count,
)


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
            default_value TEXT,
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
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/engine.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'process_everything', 'function', 10, 200, '(data, config, opts)')"
    )
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
        "VALUES (1, 75, 6)"
    )
    conn.commit()


def _populate_god_class(conn):
    """Insert a god class with 35 methods and 1200 LOC."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/monolith.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'GodManager', 'class', 1, 1201)"
    )
    # Insert 35 methods inside the class
    for i in range(35):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, 'method', ?, ?)",
            (100 + i, f"method_{i}", 10 + i * 30, 10 + i * 30 + 25),
        )
    conn.commit()


def _populate_deep_nesting(conn):
    """Insert a function with deep nesting."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/nested.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'deeply_nested', 'function', 1, 50)"
    )
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
        "VALUES (1, 15, 7)"
    )
    conn.commit()


def _populate_long_params(conn):
    """Insert a function with many parameters."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/api.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'create_report', 'function', 1, 30, "
        "'(self, title, author, date, format, output, template, extra)')"
    )
    conn.commit()


def _populate_shotgun_surgery(conn):
    """Insert a symbol with high in_degree."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/utils.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'helper_fn', 'function', 1, 10)"
    )
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) "
        "VALUES (1, 12, 2)"
    )
    conn.commit()


def _populate_message_chain(conn):
    """Insert a function with high out_degree."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/handler.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
        "VALUES (1, 1, 'handle_request', 'function', 1, 40)"
    )
    conn.execute(
        "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) "
        "VALUES (1, 2, 15)"
    )
    conn.commit()


def _populate_feature_envy(conn):
    """Insert a function where most refs are to other files."""
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
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (?, 2, ?, 'function', ?, ?)",
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
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (1, 20, 'call')"
    )
    conn.commit()


def _populate_dead_params(conn):
    """Insert a function with many params but low complexity."""
    conn.execute(
        "INSERT INTO files (id, path) VALUES (1, 'src/stubs.py')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, signature) "
        "VALUES (1, 1, 'stub_handler', 'function', 1, 5, "
        "'(request, response, context, logger, config)')"
    )
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) "
        "VALUES (1, 0)"
    )
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
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, 'method', ?, ?)",
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
            (i + 1, f"do_thing_{i}", i * 10, i * 10 + 8,
             f"(host, port, timeout, extra_{i})"),
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
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, 'method', ?, ?)",
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
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity) VALUES (1, 30)"
        )
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
        conn = _make_db(tmp_path)
        _populate_feature_envy(conn)
        results = detect_feature_envy(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "feature-envy"
        assert results[0]["severity"] == "warning"
        # 5 out of 6 refs are external = 83%
        assert results[0]["metric_value"] > 50
        conn.close()


class TestShotgunSurgery:
    def test_detects_shotgun_surgery(self, tmp_path):
        conn = _make_db(tmp_path)
        _populate_shotgun_surgery(conn)
        results = detect_shotgun_surgery(conn)
        assert len(results) == 1
        assert results[0]["smell_id"] == "shotgun-surgery"
        assert results[0]["metric_value"] == 12
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
    def test_placeholder_returns_empty(self, tmp_path):
        conn = _make_db(tmp_path)
        results = detect_empty_catch(conn)
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


class TestPlaceholders:
    def test_refused_bequest_empty(self, tmp_path):
        conn = _make_db(tmp_path)
        assert detect_refused_bequest(conn) == []
        conn.close()

    def test_primitive_obsession_empty(self, tmp_path):
        conn = _make_db(tmp_path)
        assert detect_primitive_obsession(conn) == []
        conn.close()

    def test_duplicate_conditionals_empty(self, tmp_path):
        conn = _make_db(tmp_path)
        assert detect_duplicate_conditionals(conn) == []
        conn.close()


# ---------------------------------------------------------------------------
# Tests: ALL_DETECTORS registry
# ---------------------------------------------------------------------------

class TestAllDetectors:
    def test_has_15_entries(self):
        assert len(ALL_DETECTORS) == 15

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
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
            "VALUES (1, 80, 8)"
        )
        # Shotgun surgery target
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (2, 1, 'helper', 'function', 210, 220)"
        )
        conn.execute(
            "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) "
            "VALUES (2, 15, 1)"
        )
        conn.commit()
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
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
            "VALUES (1, 80, 3)"
        )
        # Deep nesting = warning
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (2, 1, 'nested_fn', 'function', 210, 250)"
        )
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
            "VALUES (2, 10, 6)"
        )
        # Message chain = info
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) "
            "VALUES (3, 1, 'chatty_fn', 'function', 260, 300)"
        )
        conn.execute(
            "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) "
            "VALUES (3, 1, 12)"
        )
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
        required = {"smell_id", "severity", "symbol_name", "kind", "location",
                     "metric_value", "threshold", "description"}
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
                "INSERT INTO symbol_metrics (symbol_id, cognitive_complexity, nesting_depth) "
                "VALUES (?, 90, 3)",
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
        conn.execute(
            "INSERT INTO graph_metrics (symbol_id, in_degree, out_degree) "
            "VALUES (10, 12, 2)"
        )
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
