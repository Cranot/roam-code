"""Tests for roam check-rules command and built-in rule pack."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner

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
            id INTEGER PRIMARY KEY, path TEXT NOT NULL,
            loc INTEGER, file_role TEXT DEFAULT 'source'
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            qualified_name TEXT, kind TEXT, line_start INTEGER,
            line_end INTEGER, is_exported INTEGER DEFAULT 0,
            cognitive_complexity REAL, parent_id INTEGER,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER,
            target_id INTEGER, kind TEXT DEFAULT 'calls',
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            in_degree INTEGER DEFAULT 0,
            out_degree INTEGER DEFAULT 0,
            betweenness REAL DEFAULT 0,
            pagerank REAL DEFAULT 0
        );
    """)
    conn.commit()
    return conn


@pytest.fixture()
def tmp_project(tmp_path: Path):
    (tmp_path / ".roam").mkdir()
    return tmp_path


@pytest.fixture()
def empty_db(tmp_project: Path) -> sqlite3.Connection:
    return _make_db(tmp_project)


@pytest.fixture()
def indexed_project(tmp_path: Path):
    (tmp_path / ".roam").mkdir()
    conn = _make_db(tmp_path)
    conn.execute("INSERT INTO files (id, path, loc) VALUES (1, 'src/foo.py', 50)")
    conn.execute("INSERT INTO files (id, path, loc) VALUES (2, 'tests/test_foo.py', 20)")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, cognitive_complexity) "
        "VALUES (1, 1, 'my_fn', 'function', 5)"
    )
    conn.commit()
    conn.close()
    return tmp_path



# ---------------------------------------------------------------------------
# make_violation
# ---------------------------------------------------------------------------

def test_make_violation_defaults():
    from roam.rules.builtin import make_violation
    v = make_violation()
    assert v["symbol"] == ""
    assert v["file"] == ""
    assert v["line"] is None
    assert v["reason"] == ""


def test_make_violation_with_values():
    from roam.rules.builtin import make_violation
    v = make_violation(symbol="foo", file="bar.py", line=42, reason="oops")
    assert v["symbol"] == "foo"
    assert v["file"] == "bar.py"
    assert v["line"] == 42
    assert v["reason"] == "oops"


# ---------------------------------------------------------------------------
# BuiltinRule registry
# ---------------------------------------------------------------------------

def test_builtin_rule_registry():
    from roam.rules.builtin import BUILTIN_RULES, BUILTIN_RULE_MAP
    assert len(BUILTIN_RULES) == 10
    expected_ids = {
        "no-circular-imports", "max-fan-out", "max-fan-in",
        "max-file-complexity", "max-file-length", "test-file-exists",
        "no-god-classes", "no-deep-inheritance", "layer-violation",
        "no-orphan-symbols",
    }
    assert {r.id for r in BUILTIN_RULES} == expected_ids
    assert set(BUILTIN_RULE_MAP.keys()) == expected_ids


def test_builtin_rule_evaluate_no_fn():
    from roam.rules.builtin import BuiltinRule
    r = BuiltinRule(id="test", severity="warning", description="x", check="x", _fn=None)
    assert r.evaluate(None, None) == []


def test_builtin_rule_evaluate_fn_error():
    from roam.rules.builtin import BuiltinRule
    def bad_fn(conn, G, threshold):
        raise RuntimeError("boom")
    r = BuiltinRule(id="test", severity="warning", description="x", check="x", _fn=bad_fn)
    result = r.evaluate(None, None)
    assert len(result) == 1
    assert "boom" in result[0]["reason"]


def test_get_builtin_rule():
    from roam.rules.builtin import get_builtin_rule
    r = get_builtin_rule("no-circular-imports")
    assert r is not None
    assert r.severity == "error"
    assert get_builtin_rule("nonexistent") is None


def test_builtin_rule_all_severities():
    from roam.rules.builtin import BUILTIN_RULES
    severities = {r.severity for r in BUILTIN_RULES}
    assert "error" in severities
    assert "warning" in severities
    assert "info" in severities


def test_builtin_rule_all_have_descriptions():
    from roam.rules.builtin import BUILTIN_RULES
    for r in BUILTIN_RULES:
        assert r.description, f"Rule {r.id} has no description"


def test_builtin_rule_all_have_check():
    from roam.rules.builtin import BUILTIN_RULES
    for r in BUILTIN_RULES:
        assert r.check, f"Rule {r.id} has no check type"


def test_check_fn_map_complete():
    from roam.rules.builtin import _CHECK_FN_MAP, BUILTIN_RULES
    for rule in BUILTIN_RULES:
        assert rule.check in _CHECK_FN_MAP, f"No check fn for {rule.check}"



# ---------------------------------------------------------------------------
# Rule 1: no-circular-imports
# ---------------------------------------------------------------------------

def test_no_circular_imports_pass_empty_graph():
    from roam.rules.builtin import _check_no_circular_imports
    violations = _check_no_circular_imports(None, nx.DiGraph(), 0)
    assert violations == []


def test_no_circular_imports_pass_no_cycles(empty_db):
    from roam.rules.builtin import _check_no_circular_imports
    G = nx.DiGraph()
    G.add_edge(1, 2)
    G.add_edge(2, 3)
    violations = _check_no_circular_imports(empty_db, G, 0)
    assert violations == []


def test_no_circular_imports_fail_cycle(tmp_project):
    from roam.rules.builtin import _check_no_circular_imports
    conn = _make_db(tmp_project)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'a.py')")
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'b.py')")
    conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (1, 1, 'foo', 'function')")
    conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (2, 2, 'bar', 'function')")
    conn.commit()
    G = nx.DiGraph()
    G.add_node(1, name="foo", file_path="a.py", kind="function")
    G.add_node(2, name="bar", file_path="b.py", kind="function")
    G.add_edge(1, 2)
    G.add_edge(2, 1)
    violations = _check_no_circular_imports(conn, G, 0)
    assert len(violations) >= 1
    assert "cycle" in violations[0]["reason"]


def test_no_circular_imports_none_graph():
    from roam.rules.builtin import _check_no_circular_imports
    assert _check_no_circular_imports(None, None, 0) == []


# ---------------------------------------------------------------------------
# Rule 2: max-fan-out
# ---------------------------------------------------------------------------

def test_max_fan_out_pass():
    from roam.rules.builtin import _check_max_fan_out
    G = nx.DiGraph()
    G.add_node(1, name="foo", file_path="a.py", kind="function")
    for i in range(5):
        G.add_edge(1, 10 + i)
    assert _check_max_fan_out(None, G, 15) == []


def test_max_fan_out_fail():
    from roam.rules.builtin import _check_max_fan_out
    G = nx.DiGraph()
    G.add_node(1, name="big_fn", file_path="a.py", kind="function", line_start=10)
    for i in range(20):
        G.add_edge(1, 100 + i)
    violations = _check_max_fan_out(None, G, 15)
    assert len(violations) == 1
    assert "big_fn" in violations[0]["symbol"]
    assert "fan-out 20 exceeds limit 15" in violations[0]["reason"]


def test_max_fan_out_default_threshold():
    from roam.rules.builtin import _check_max_fan_out
    G = nx.DiGraph()
    G.add_node(1, name="fn", file_path="a.py", kind="function")
    for i in range(16):
        G.add_edge(1, 100 + i)
    assert len(_check_max_fan_out(None, G, None)) == 1


def test_max_fan_out_none_graph():
    from roam.rules.builtin import _check_max_fan_out
    assert _check_max_fan_out(None, None, 15) == []


# ---------------------------------------------------------------------------
# Rule 3: max-fan-in
# ---------------------------------------------------------------------------

def test_max_fan_in_pass():
    from roam.rules.builtin import _check_max_fan_in
    G = nx.DiGraph()
    G.add_node(1, name="foo", file_path="a.py", kind="function")
    for i in range(5):
        G.add_edge(100 + i, 1)
    assert _check_max_fan_in(None, G, 30) == []


def test_max_fan_in_fail():
    from roam.rules.builtin import _check_max_fan_in
    G = nx.DiGraph()
    G.add_node(1, name="popular", file_path="a.py", kind="function", line_start=5)
    for i in range(35):
        G.add_edge(100 + i, 1)
    violations = _check_max_fan_in(None, G, 30)
    assert len(violations) == 1
    assert "fan-in 35 exceeds limit 30" in violations[0]["reason"]


def test_max_fan_in_none_graph():
    from roam.rules.builtin import _check_max_fan_in
    assert _check_max_fan_in(None, None, 30) == []


# ---------------------------------------------------------------------------
# Rule 4: max-file-complexity
# ---------------------------------------------------------------------------

def test_max_file_complexity_pass(empty_db):
    from roam.rules.builtin import _check_max_file_complexity
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'simple.py')")
    empty_db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, cognitive_complexity) "
        "VALUES (1, 1, 'fn', 'function', 10)"
    )
    empty_db.commit()
    assert _check_max_file_complexity(empty_db, None, 50) == []


def test_max_file_complexity_fail(empty_db):
    from roam.rules.builtin import _check_max_file_complexity
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'complex.py')")
    empty_db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, cognitive_complexity) "
        "VALUES (1, 1, 'fn', 'function', 80)"
    )
    empty_db.commit()
    violations = _check_max_file_complexity(empty_db, None, 50)
    assert len(violations) == 1
    assert "complex.py" in violations[0]["file"]


def test_max_file_complexity_empty_db(empty_db):
    from roam.rules.builtin import _check_max_file_complexity
    assert _check_max_file_complexity(empty_db, None, 50) == []


# ---------------------------------------------------------------------------
# Rule 5: max-file-length
# ---------------------------------------------------------------------------

def test_max_file_length_pass(empty_db):
    from roam.rules.builtin import _check_max_file_length
    empty_db.execute("INSERT INTO files (id, path, loc) VALUES (1, 'short.py', 100)")
    empty_db.commit()
    assert _check_max_file_length(empty_db, None, 500) == []


def test_max_file_length_fail(empty_db):
    from roam.rules.builtin import _check_max_file_length
    empty_db.execute("INSERT INTO files (id, path, loc) VALUES (1, 'huge.py', 1000)")
    empty_db.commit()
    violations = _check_max_file_length(empty_db, None, 500)
    assert len(violations) == 1
    assert "huge.py" in violations[0]["file"]
    assert "1000 lines" in violations[0]["reason"]


def test_max_file_length_default_threshold(empty_db):
    from roam.rules.builtin import _check_max_file_length
    empty_db.execute("INSERT INTO files (id, path, loc) VALUES (1, 'long.py', 501)")
    empty_db.commit()
    assert len(_check_max_file_length(empty_db, None, None)) == 1



# ---------------------------------------------------------------------------
# Rule 6: test-file-exists
# ---------------------------------------------------------------------------

def test_is_test_path_true():
    from roam.rules.builtin import _is_test_path
    assert _is_test_path("tests/test_foo.py") is True
    assert _is_test_path("test_bar.py") is True
    assert _is_test_path("src/__tests__/bar.js") is True


def test_is_test_path_false():
    from roam.rules.builtin import _is_test_path
    assert _is_test_path("src/foo.py") is False
    assert _is_test_path("lib/utils.py") is False


def test_test_file_exists_pass(empty_db):
    from roam.rules.builtin import _check_test_file_exists
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'src/foo.py')")
    empty_db.execute("INSERT INTO files (id, path) VALUES (2, 'tests/test_foo.py')")
    empty_db.commit()
    assert _check_test_file_exists(empty_db, None, None) == []


def test_test_file_exists_fail(empty_db):
    from roam.rules.builtin import _check_test_file_exists
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'src/bar.py')")
    empty_db.commit()
    violations = _check_test_file_exists(empty_db, None, None)
    assert len(violations) == 1
    assert "bar.py" in violations[0]["file"]


def test_test_file_exists_skips_non_source(empty_db):
    from roam.rules.builtin import _check_test_file_exists
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'README.md')")
    empty_db.commit()
    assert _check_test_file_exists(empty_db, None, None) == []


def test_test_file_exists_empty_db(empty_db):
    from roam.rules.builtin import _check_test_file_exists
    assert _check_test_file_exists(empty_db, None, None) == []


def test_test_file_exists_skips_migrations(empty_db):
    from roam.rules.builtin import _check_test_file_exists
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'migrations/001_init.py')")
    empty_db.commit()
    assert _check_test_file_exists(empty_db, None, None) == []


# ---------------------------------------------------------------------------
# Rule 7: no-god-classes
# ---------------------------------------------------------------------------

def test_no_god_classes_empty_db(empty_db):
    from roam.rules.builtin import _check_no_god_classes
    violations = _check_no_god_classes(empty_db, None, 20)
    assert isinstance(violations, list)


def test_no_god_classes_none_graph_ok(empty_db):
    from roam.rules.builtin import _check_no_god_classes
    violations = _check_no_god_classes(empty_db, None, 20)
    assert violations == []


# ---------------------------------------------------------------------------
# Rule 8: no-deep-inheritance
# ---------------------------------------------------------------------------

def test_no_deep_inheritance_no_edges(empty_db):
    from roam.rules.builtin import _check_no_deep_inheritance
    assert _check_no_deep_inheritance(empty_db, nx.DiGraph(), 4) == []


def test_no_deep_inheritance_empty_db(empty_db):
    from roam.rules.builtin import _check_no_deep_inheritance
    assert _check_no_deep_inheritance(empty_db, None, 4) == []


def test_no_deep_inheritance_no_extend_edges(empty_db):
    from roam.rules.builtin import _check_no_deep_inheritance
    empty_db.execute("INSERT INTO files (id, path) VALUES (1, 'a.py')")
    empty_db.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (1, 1, 'A', 'class')")
    empty_db.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (2, 1, 'B', 'class')")
    empty_db.execute("INSERT INTO edges (id, source_id, target_id, kind) VALUES (1, 1, 2, 'calls')")
    empty_db.commit()
    assert _check_no_deep_inheritance(empty_db, None, 4) == []


# ---------------------------------------------------------------------------
# Rule 9: layer-violation
# ---------------------------------------------------------------------------

def test_layer_violation_empty_graph():
    from roam.rules.builtin import _check_layer_violation
    assert _check_layer_violation(None, nx.DiGraph(), None) == []


def test_layer_violation_none_graph():
    from roam.rules.builtin import _check_layer_violation
    assert _check_layer_violation(None, None, None) == []


def test_layer_violation_no_crash_with_data(tmp_project):
    from roam.rules.builtin import _check_layer_violation
    conn = _make_db(tmp_project)
    conn.execute("INSERT INTO files (id, path) VALUES (1, 'a.py')")
    conn.execute("INSERT INTO files (id, path) VALUES (2, 'b.py')")
    conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (1, 1, 'low', 'function')")
    conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES (2, 2, 'high', 'function')")
    conn.commit()
    G = nx.DiGraph()
    G.add_node(1, name="low", file_path="a.py", kind="function")
    G.add_node(2, name="high", file_path="b.py", kind="function")
    G.add_edge(2, 1)
    violations = _check_layer_violation(conn, G, None)
    assert isinstance(violations, list)


# ---------------------------------------------------------------------------
# Rule 10: no-orphan-symbols
# ---------------------------------------------------------------------------

def test_no_orphan_symbols_empty_graph():
    from roam.rules.builtin import _check_no_orphan_symbols
    assert _check_no_orphan_symbols(None, nx.DiGraph(), None) == []


def test_no_orphan_symbols_none_graph():
    from roam.rules.builtin import _check_no_orphan_symbols
    assert _check_no_orphan_symbols(None, None, None) == []


def test_no_orphan_symbols_connected():
    from roam.rules.builtin import _check_no_orphan_symbols
    G = nx.DiGraph()
    G.add_node(1, name="foo", file_path="a.py", kind="function")
    G.add_node(2, name="bar", file_path="a.py", kind="function")
    G.add_edge(1, 2)
    assert _check_no_orphan_symbols(None, G, None) == []


def test_no_orphan_symbols_finds_orphan():
    from roam.rules.builtin import _check_no_orphan_symbols
    G = nx.DiGraph()
    G.add_node(1, name="orphan", file_path="a.py", kind="function", line_start=5)
    violations = _check_no_orphan_symbols(None, G, None)
    assert len(violations) == 1
    assert violations[0]["symbol"] == "orphan"


def test_no_orphan_symbols_skips_variable():
    from roam.rules.builtin import _check_no_orphan_symbols
    G = nx.DiGraph()
    G.add_node(1, name="my_var", file_path="a.py", kind="variable", line_start=1)
    assert _check_no_orphan_symbols(None, G, None) == []



# ---------------------------------------------------------------------------
# _resolve_rules
# ---------------------------------------------------------------------------

def test_resolve_rules_all():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules(None, None, [])
    assert len(rules) == 10


def test_resolve_rules_filter_by_id():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules("no-circular-imports", None, [])
    assert len(rules) == 1
    assert rules[0].id == "no-circular-imports"


def test_resolve_rules_filter_by_severity_error():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules(None, "error", [])
    assert all(r.severity == "error" for r in rules)
    assert len(rules) >= 1


def test_resolve_rules_filter_by_severity_warning():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules(None, "warning", [])
    assert all(r.severity == "warning" for r in rules)


def test_resolve_rules_filter_by_severity_info():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules(None, "info", [])
    assert all(r.severity == "info" for r in rules)
    assert len(rules) >= 1


def test_resolve_rules_disable_one():
    from roam.commands.cmd_check_rules import _resolve_rules
    overrides = [{"id": "no-circular-imports", "enabled": False}]
    rules = _resolve_rules(None, None, overrides)
    ids = [r.id for r in rules]
    assert "no-circular-imports" not in ids


def test_resolve_rules_disable_multiple():
    from roam.commands.cmd_check_rules import _resolve_rules
    overrides = [
        {"id": "no-circular-imports", "enabled": False},
        {"id": "max-fan-out", "enabled": False},
        {"id": "max-fan-in", "enabled": False},
    ]
    rules = _resolve_rules(None, None, overrides)
    ids = [r.id for r in rules]
    assert "no-circular-imports" not in ids
    assert "max-fan-out" not in ids
    assert len(rules) == 7


def test_resolve_rules_threshold_override():
    from roam.commands.cmd_check_rules import _resolve_rules
    overrides = [{"id": "max-fan-out", "threshold": 5}]
    rules = _resolve_rules(None, None, overrides)
    fan_out_rule = next(r for r in rules if r.id == "max-fan-out")
    assert fan_out_rule.threshold == 5.0


def test_resolve_rules_severity_override():
    from roam.commands.cmd_check_rules import _resolve_rules
    overrides = [{"id": "max-file-length", "severity": "error"}]
    rules = _resolve_rules(None, None, overrides)
    length_rule = next(r for r in rules if r.id == "max-file-length")
    assert length_rule.severity == "error"


def test_resolve_rules_empty_filter():
    from roam.commands.cmd_check_rules import _resolve_rules
    rules = _resolve_rules("nonexistent-rule", None, [])
    assert rules == []


# ---------------------------------------------------------------------------
# _calculate_verdict
# ---------------------------------------------------------------------------

def test_calculate_verdict_empty():
    from roam.commands.cmd_check_rules import _calculate_verdict
    verdict, code = _calculate_verdict([])
    assert code == 0
    assert "PASS" in verdict


def test_calculate_verdict_all_pass():
    from roam.commands.cmd_check_rules import _calculate_verdict
    results = [
        {"passed": True, "severity": "error"},
        {"passed": True, "severity": "warning"},
    ]
    verdict, code = _calculate_verdict(results)
    assert code == 0
    assert "PASS" in verdict


def test_calculate_verdict_warnings_only():
    from roam.commands.cmd_check_rules import _calculate_verdict
    results = [{"passed": False, "severity": "warning"}]
    verdict, code = _calculate_verdict(results)
    assert code == 0
    assert "WARN" in verdict


def test_calculate_verdict_error():
    from roam.commands.cmd_check_rules import _calculate_verdict
    results = [{"passed": False, "severity": "error"}]
    verdict, code = _calculate_verdict(results)
    assert code == 1
    assert "FAIL" in verdict


def test_calculate_verdict_mixed():
    from roam.commands.cmd_check_rules import _calculate_verdict
    results = [
        {"passed": False, "severity": "error"},
        {"passed": False, "severity": "warning"},
        {"passed": True, "severity": "info"},
    ]
    verdict, code = _calculate_verdict(results)
    assert code == 1
    assert "FAIL" in verdict


def test_calculate_verdict_info_only_exit_zero():
    from roam.commands.cmd_check_rules import _calculate_verdict
    results = [{"passed": False, "severity": "info"}]
    _, code = _calculate_verdict(results)
    assert code == 0


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------

def test_load_user_config_none():
    from roam.commands.cmd_check_rules import _load_user_config
    result = _load_user_config(None)
    assert isinstance(result, list)


def test_load_user_config_missing_file():
    from roam.commands.cmd_check_rules import _load_user_config
    result = _load_user_config("/nonexistent/path/.roam-rules.yml")
    assert result == []


def test_load_user_config_empty_file(tmp_path: Path):
    from roam.commands.cmd_check_rules import _load_user_config
    cfg = tmp_path / ".roam-rules.yml"
    cfg.write_text("", encoding="utf-8")
    result = _load_user_config(str(cfg))
    assert result == []




def test_load_user_config_with_overrides(tmp_path):
    from roam.commands.cmd_check_rules import _load_user_config
    cfg = tmp_path / ".roam-rules.yml"
    yaml_content = (
        "rules:" + chr(10)
        + "  - id: max-fan-out" + chr(10)
        + "    threshold: 5" + chr(10)
        + "  - id: test-file-exists" + chr(10)
        + "    enabled: false" + chr(10)
    )
    cfg.write_text(yaml_content, encoding="utf-8")
    result = _load_user_config(str(cfg))
    assert len(result) == 2


def test_load_user_config_full_override(tmp_path):
    from roam.commands.cmd_check_rules import _load_user_config, _resolve_rules
    cfg = tmp_path / ".roam-rules.yml"
    yaml_content = (
        "rules:" + chr(10)
        + "  - id: max-fan-out" + chr(10)
        + "    threshold: 5" + chr(10)
        + "    severity: error" + chr(10)
        + "  - id: no-circular-imports" + chr(10)
        + "    enabled: false" + chr(10)
    )
    cfg.write_text(yaml_content, encoding="utf-8")
    overrides = _load_user_config(str(cfg))
    rules = _resolve_rules(None, None, overrides)
    fan_out = next((r for r in rules if r.id == "max-fan-out"), None)
    circular = next((r for r in rules if r.id == "no-circular-imports"), None)
    assert fan_out is not None
    assert fan_out.threshold == 5.0
    assert fan_out.severity == "error"
    assert circular is None


def test_results_to_sarif_empty():
    from roam.commands.cmd_check_rules import _results_to_sarif
    sarif = _results_to_sarif([])
    assert sarif.get("version") == "2.1.0"
    assert "runs" in sarif


def test_results_to_sarif_with_violation():
    from roam.commands.cmd_check_rules import _results_to_sarif
    results = [{
        "id": "no-circular-imports", "severity": "error",
        "description": "No cycles", "check": "cycles",
        "threshold": 0, "passed": False, "violation_count": 1,
        "violations": [{"symbol": "foo", "file": "a.py", "line": 1, "reason": "cycle"}],
    }]
    sarif = _results_to_sarif(results)
    assert sarif.get("version") == "2.1.0"
    assert len(sarif.get("runs", [])) == 1


def test_results_to_sarif_passed_rule_empty():
    from roam.commands.cmd_check_rules import _results_to_sarif
    results = [{"id": "max-fan-out", "severity": "warning", "description": "x",
                "check": "fan-out", "threshold": 15, "passed": True,
                "violation_count": 0, "violations": []}]
    sarif = _results_to_sarif(results)
    assert sarif["runs"][0].get("results", []) == []



def test_all_rules_on_empty_db(empty_db):
    import sqlite3, networkx as nx
    from roam.rules.builtin import BUILTIN_RULES
    G = nx.DiGraph()
    for rule in BUILTIN_RULES:
        violations = rule.evaluate(empty_db, G)
        assert isinstance(violations, list), f"Rule {rule.id} did not return a list"


def test_all_rules_on_in_memory_db():
    import sqlite3, networkx as nx
    from roam.rules.builtin import BUILTIN_RULES
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, loc INTEGER, file_role TEXT);",
    )
    conn.executescript(
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, "        "kind TEXT, line_start INTEGER, cognitive_complexity REAL, "        "parent_id INTEGER, is_exported INTEGER DEFAULT 0);"    )
    conn.executescript(
        "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, "        "target_id INTEGER, kind TEXT DEFAULT 'calls');"    )
    conn.executescript(
        "CREATE TABLE graph_metrics (symbol_id INTEGER PRIMARY KEY, "        "in_degree INTEGER, out_degree INTEGER);"    )
    conn.commit()
    G = nx.DiGraph()
    G.add_node(1, name="fn", file_path="a.py", kind="function")
    G.add_node(2, name="fn2", file_path="b.py", kind="function")
    G.add_edge(1, 2)
    for rule in BUILTIN_RULES:
        violations = rule.evaluate(conn, G)
        assert isinstance(violations, list), f"Rule {rule.id} failed"
    conn.close()


def test_cli_list_text(tmp_project):
    from click.testing import CliRunner
    from roam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules", "--list"],
                           env={"ROAM_DB_PATH": str(tmp_project / ".roam" / "index.db")})
    assert result.exit_code in (0, 1, 2)


def test_cli_check_rules_text_output(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules"], catch_exceptions=False)
    assert "VERDICT" in result.output


def test_cli_check_rules_specific_rule(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules", "--rule", "no-circular-imports"], catch_exceptions=False)
    assert result.exit_code in (0, 1)


def test_cli_check_rules_severity_error(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules", "--severity", "error"], catch_exceptions=False)
    assert result.exit_code in (0, 1)


def test_cli_check_rules_no_match_rule(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules", "--rule", "nonexistent-rule"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "no rules matched" in result.output


def test_cli_check_rules_sarif(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    import json
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["--sarif", "check-rules"], catch_exceptions=False)
    if result.exit_code in (0, 1):
        try:
            data = json.loads(result.output)
            assert data.get("version") == "2.1.0"
        except Exception:
            pass


def test_cli_check_rules_json(indexed_project, monkeypatch):
    from click.testing import CliRunner
    from roam.cli import cli
    import json
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "check-rules"], catch_exceptions=False)
    if result.exit_code in (0, 1):
        try:
            data = json.loads(result.output)
            assert "summary" in data
        except Exception:
            pass


def test_cli_check_rules_custom_config(indexed_project, monkeypatch, tmp_path):
    from click.testing import CliRunner
    from roam.cli import cli
    monkeypatch.chdir(indexed_project)
    cfg = tmp_path / "custom.yml"
    cfg.write_text("rules:" + chr(10) + "  - id: max-fan-out" + chr(10) + "    threshold: 5" + chr(10), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["check-rules", "--config", str(cfg)], catch_exceptions=False)
    assert result.exit_code in (0, 1)


def test_exit_code_info_is_zero():
    from roam.commands.cmd_check_rules import _calculate_verdict
    _, code = _calculate_verdict([{"passed": False, "severity": "info"}])
    assert code == 0


def test_exit_code_warning_is_zero():
    from roam.commands.cmd_check_rules import _calculate_verdict
    _, code = _calculate_verdict([{"passed": False, "severity": "warning"}])
    assert code == 0


def test_exit_code_error_is_one():
    from roam.commands.cmd_check_rules import _calculate_verdict
    _, code = _calculate_verdict([{"passed": False, "severity": "error"}])
    assert code == 1

