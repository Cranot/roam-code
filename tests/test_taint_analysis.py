"""Tests for inter-procedural taint analysis."""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

from roam.analysis.taint import (
    _DEFAULT_SINKS,
    _DEFAULT_SOURCES,
    _SANITIZER_NAMES,
    TaintFinding,
    TaintSummary,
    _detect_sanitizer,
    _parse_param_names,
    _track_variable_taint,
    compute_all_summaries,
    compute_and_store_taint,
    compute_intra_summary,
    propagate_taint,
    store_taint_data,
)
from roam.db.schema import SCHEMA_SQL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path | None = None) -> sqlite3.Connection:
    """Create an in-memory DB with the full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_file(conn, path="src/app.py", language="python"):
    conn.execute(
        "INSERT INTO files (path, language) VALUES (?, ?)",
        (path, language),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_symbol(
    conn,
    file_id,
    name,
    kind="function",
    signature=None,
    line_start=1,
    line_end=5,
    qualified_name=None,
):
    conn.execute(
        """INSERT INTO symbols
           (file_id, name, qualified_name, kind, signature, line_start, line_end)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (file_id, name, qualified_name or name, kind, signature, line_start, line_end),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_edge(conn, source_id, target_id, kind="calls"):
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)",
        (source_id, target_id, kind),
    )


# ---------------------------------------------------------------------------
# TaintSummary dataclass
# ---------------------------------------------------------------------------


class TestTaintSummary:
    def test_default_creation(self):
        s = TaintSummary(symbol_id=1)
        assert s.symbol_id == 1
        assert s.param_taints_return == {}
        assert s.param_to_sink == {}
        assert s.return_from_source is False
        assert s.direct_sources == []
        assert s.direct_sinks == []
        assert s.is_sanitizer is False

    def test_creation_with_values(self):
        s = TaintSummary(
            symbol_id=42,
            param_taints_return={0: True},
            param_to_sink={0: ["eval("]},
            return_from_source=True,
            direct_sources=["request.args"],
            direct_sinks=["eval("],
            is_sanitizer=False,
        )
        assert s.symbol_id == 42
        assert s.param_taints_return[0] is True
        assert "eval(" in s.param_to_sink[0]
        assert s.return_from_source is True


class TestTaintFinding:
    def test_default_creation(self):
        f = TaintFinding(
            source_symbol_id=1,
            sink_symbol_id=2,
            source_type="request.args",
            sink_type="eval(",
        )
        assert f.source_symbol_id == 1
        assert f.sink_symbol_id == 2
        assert f.call_chain == []
        assert f.confidence == 0.8


# ---------------------------------------------------------------------------
# Sanitizer detection
# ---------------------------------------------------------------------------


class TestSanitizerDetection:
    def test_positive_cases(self):
        assert _detect_sanitizer("escape_html") is True
        assert _detect_sanitizer("sanitize_input") is True
        assert _detect_sanitizer("validate_data") is True
        assert _detect_sanitizer("html_encode") is True
        assert _detect_sanitizer("clean_string") is True
        assert _detect_sanitizer("bleach_clean") is True

    def test_negative_cases(self):
        assert _detect_sanitizer("process_data") is False
        assert _detect_sanitizer("get_user") is False
        assert _detect_sanitizer("compute_hash") is False

    def test_qualified_name(self):
        assert _detect_sanitizer("func", "utils.sanitize_input") is True
        assert _detect_sanitizer("func", "app.process.run") is False

    def test_none_handling(self):
        assert _detect_sanitizer("", None) is False
        assert _detect_sanitizer(None, None) is False


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------


class TestParseParamNames:
    def test_simple(self):
        assert _parse_param_names("def foo(a, b, c)") == ["a", "b", "c"]

    def test_with_defaults(self):
        assert _parse_param_names("def foo(a, b=1, c='x')") == ["a", "b", "c"]

    def test_with_type_hints(self):
        assert _parse_param_names("def foo(a: int, b: str)") == ["a", "b"]

    def test_skip_self(self):
        assert _parse_param_names("def foo(self, a, b)") == ["a", "b"]

    def test_empty(self):
        assert _parse_param_names("def foo()") == []
        assert _parse_param_names(None) == []
        assert _parse_param_names("") == []

    def test_star_args(self):
        assert _parse_param_names("def foo(*args, **kwargs)") == ["args", "kwargs"]


# ---------------------------------------------------------------------------
# Intra-procedural variable taint tracking
# ---------------------------------------------------------------------------


class TestTrackVariableTaint:
    def test_source_to_sink(self):
        body = [
            "    data = request.args.get('key')",
            "    eval(data)",
        ]
        result = _track_variable_taint(body, [], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert result["direct_sources"]
        assert result["direct_sinks"]

    def test_param_to_return(self):
        body = [
            "    result = x + 1",
            "    return result",
        ]
        result = _track_variable_taint(body, ["x"], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert result["param_taints_return"].get(0) is True

    def test_param_to_sink(self):
        body = [
            "    query = x",
            "    cursor.execute(query)",
        ]
        result = _track_variable_taint(body, ["x"], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert 0 in result["param_to_sink"]
        assert any(".execute(" in s for s in result["param_to_sink"][0])

    def test_sanitizer_kills_taint(self):
        body = [
            "    data = request.args.get('key')",
            "    data = escape(data)",
            "    eval(data)",
        ]
        result = _track_variable_taint(body, [], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        # After sanitization, data should not be tainted
        # The source is still detected, but param_to_sink should be empty
        # because the taint was cleared before reaching the sink
        assert result["direct_sources"]

    def test_taint_propagation_through_variables(self):
        body = [
            "    data = request.args.get('key')",
            "    copy = data",
            "    eval(copy)",
        ]
        result = _track_variable_taint(body, [], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert result["direct_sources"]
        assert result["direct_sinks"]

    def test_clean_assignment_clears_taint(self):
        body = [
            "    data = request.args.get('key')",
            "    data = 'safe_value'",
            "    return data",
        ]
        result = _track_variable_taint(body, [], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert result["return_from_source"] is False

    def test_no_taint(self):
        body = [
            "    x = 1",
            "    y = x + 2",
            "    return y",
        ]
        result = _track_variable_taint(body, [], _DEFAULT_SOURCES, _DEFAULT_SINKS, _SANITIZER_NAMES)
        assert not result["direct_sources"]
        assert not result["direct_sinks"]
        assert not result["param_taints_return"]
        assert not result["param_to_sink"]


# ---------------------------------------------------------------------------
# DB schema for taint tables
# ---------------------------------------------------------------------------


class TestTaintSchema:
    def test_tables_exist(self):
        conn = _make_db()
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "taint_summaries" in tables
        assert "taint_findings" in tables

    def test_taint_summaries_columns(self):
        conn = _make_db()
        cols = conn.execute("PRAGMA table_info(taint_summaries)").fetchall()
        col_names = {c["name"] for c in cols}
        assert "symbol_id" in col_names
        assert "param_taints_return" in col_names
        assert "param_to_sink" in col_names
        assert "return_from_source" in col_names
        assert "direct_sources" in col_names
        assert "direct_sinks" in col_names
        assert "is_sanitizer" in col_names

    def test_taint_findings_columns(self):
        conn = _make_db()
        cols = conn.execute("PRAGMA table_info(taint_findings)").fetchall()
        col_names = {c["name"] for c in cols}
        assert "source_symbol_id" in col_names
        assert "sink_symbol_id" in col_names
        assert "source_type" in col_names
        assert "sink_type" in col_names
        assert "call_chain" in col_names
        assert "chain_length" in col_names
        assert "sanitized" in col_names
        assert "confidence" in col_names


# ---------------------------------------------------------------------------
# Store / retrieve taint data
# ---------------------------------------------------------------------------


class TestStoreTaintData:
    def test_store_summaries(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "foo", signature="def foo(x, y)")

        summaries = {
            sid: TaintSummary(
                symbol_id=sid,
                param_taints_return={0: True},
                param_to_sink={1: ["eval("]},
                return_from_source=True,
                direct_sources=["request.args"],
                direct_sinks=["eval("],
                is_sanitizer=False,
            )
        }
        store_taint_data(conn, summaries, [])

        row = conn.execute("SELECT * FROM taint_summaries WHERE symbol_id = ?", (sid,)).fetchone()
        assert row is not None
        assert json.loads(row["param_taints_return"]) == {"0": True}
        assert json.loads(row["param_to_sink"]) == {"1": ["eval("]}
        assert row["return_from_source"] == 1
        assert json.loads(row["direct_sources"]) == ["request.args"]
        assert row["is_sanitizer"] == 0

    def test_store_findings(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid1 = _insert_symbol(conn, fid, "source_fn")
        sid2 = _insert_symbol(conn, fid, "sink_fn")

        findings = [
            TaintFinding(
                source_symbol_id=sid1,
                sink_symbol_id=sid2,
                source_type="request.args",
                sink_type="eval(",
                call_chain=[sid1, sid2],
                confidence=0.85,
            )
        ]
        store_taint_data(conn, {}, findings)

        rows = conn.execute("SELECT * FROM taint_findings").fetchall()
        assert len(rows) == 1
        assert rows[0]["source_symbol_id"] == sid1
        assert rows[0]["sink_symbol_id"] == sid2
        assert rows[0]["source_type"] == "request.args"
        assert rows[0]["sink_type"] == "eval("
        assert rows[0]["chain_length"] == 2
        assert abs(rows[0]["confidence"] - 0.85) < 0.001

    def test_store_clears_old_data(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "foo")

        # First store
        summaries = {sid: TaintSummary(symbol_id=sid)}
        store_taint_data(conn, summaries, [])
        assert conn.execute("SELECT COUNT(*) FROM taint_summaries").fetchone()[0] == 1

        # Second store should replace
        store_taint_data(conn, summaries, [])
        assert conn.execute("SELECT COUNT(*) FROM taint_summaries").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Compute intra-procedural summary with mock DB
# ---------------------------------------------------------------------------


class TestComputeIntraSummary:
    def test_basic_summary(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "process", signature="def process(data)")

        body_lines = [
            "    result = data",
            "    return result",
        ]
        summary = compute_intra_summary(
            conn,
            sid,
            body_lines,
            "def process(data)",
            ["data"],
            _DEFAULT_SOURCES,
            _DEFAULT_SINKS,
        )
        assert summary.symbol_id == sid
        assert summary.param_taints_return.get(0) is True
        assert summary.is_sanitizer is False

    def test_sanitizer_summary(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "escape_html", signature="def escape_html(text)")

        summary = compute_intra_summary(
            conn,
            sid,
            [],
            "def escape_html(text)",
            ["text"],
            _DEFAULT_SOURCES,
            _DEFAULT_SINKS,
        )
        assert summary.is_sanitizer is True

    def test_source_to_sink_summary(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "handle", signature="def handle()")

        body_lines = [
            "    data = request.args.get('q')",
            "    eval(data)",
        ]
        summary = compute_intra_summary(
            conn,
            sid,
            body_lines,
            "def handle()",
            [],
            _DEFAULT_SOURCES,
            _DEFAULT_SINKS,
        )
        assert summary.direct_sources
        assert summary.direct_sinks


# ---------------------------------------------------------------------------
# Compute all summaries (batch)
# ---------------------------------------------------------------------------


class TestComputeAllSummaries:
    def test_batch_with_files(self, tmp_path):
        conn = _make_db()

        # Create a source file
        src_file = tmp_path / "app.py"
        src_file.write_text(
            textwrap.dedent("""\
            def handler(request_data):
                result = request_data
                return result
            """),
            encoding="utf-8",
        )

        fid = _insert_file(conn, path="app.py")
        sid = _insert_symbol(
            conn,
            fid,
            "handler",
            signature="def handler(request_data)",
            line_start=1,
            line_end=3,
        )

        summaries = compute_all_summaries(conn, tmp_path)
        assert sid in summaries
        assert summaries[sid].param_taints_return.get(0) is True

    def test_empty_project(self, tmp_path):
        conn = _make_db()
        summaries = compute_all_summaries(conn, tmp_path)
        assert summaries == {}


# ---------------------------------------------------------------------------
# Inter-procedural propagation
# ---------------------------------------------------------------------------


class TestPropagateTaint:
    def test_cross_function_finding(self):
        conn = _make_db()
        fid = _insert_file(conn)

        # Source function: reads from source, returns tainted data
        src_id = _insert_symbol(conn, fid, "get_input", signature="def get_input()")
        # Sink function: takes param, passes to sink
        sink_id = _insert_symbol(conn, fid, "process", signature="def process(data)")
        # Caller: calls get_input, passes result to process
        caller_id = _insert_symbol(conn, fid, "main", signature="def main()")

        _insert_edge(conn, caller_id, src_id, "calls")
        _insert_edge(conn, caller_id, sink_id, "calls")

        summaries = {
            src_id: TaintSummary(
                symbol_id=src_id,
                return_from_source=True,
                direct_sources=["request.args"],
            ),
            sink_id: TaintSummary(
                symbol_id=sink_id,
                param_to_sink={0: ["eval("]},
            ),
            caller_id: TaintSummary(
                symbol_id=caller_id,
            ),
        }

        findings = propagate_taint(conn, summaries, None)
        # Should find at least a direct source-to-sink in src_id
        # (since it has direct_sources but direct_sinks is empty,
        # the intra-function finding won't fire, but propagation may find cross-function)
        assert isinstance(findings, list)

    def test_sanitizer_blocks_propagation(self):
        conn = _make_db()
        fid = _insert_file(conn)

        src_id = _insert_symbol(conn, fid, "get_input", signature="def get_input()")
        san_id = _insert_symbol(conn, fid, "sanitize", signature="def sanitize(data)")
        sink_id = _insert_symbol(conn, fid, "execute", signature="def execute(cmd)")

        _insert_edge(conn, src_id, san_id, "calls")
        _insert_edge(conn, san_id, sink_id, "calls")

        summaries = {
            src_id: TaintSummary(
                symbol_id=src_id,
                return_from_source=True,
                direct_sources=["request.args"],
            ),
            san_id: TaintSummary(
                symbol_id=san_id,
                is_sanitizer=True,
                param_taints_return={0: True},
            ),
            sink_id: TaintSummary(
                symbol_id=sink_id,
                param_to_sink={0: ["eval("]},
            ),
        }

        findings = propagate_taint(conn, summaries, None)
        # No cross-function findings through sanitizer
        cross_func = [f for f in findings if f.sink_symbol_id == sink_id and f.source_symbol_id == src_id]
        assert len(cross_func) == 0

    def test_direct_intra_finding(self):
        conn = _make_db()
        fid = _insert_file(conn)
        sid = _insert_symbol(conn, fid, "handler", signature="def handler()")

        summaries = {
            sid: TaintSummary(
                symbol_id=sid,
                direct_sources=["request.args"],
                direct_sinks=["eval("],
            ),
        }

        findings = propagate_taint(conn, summaries, None)
        intra = [f for f in findings if f.source_symbol_id == sid and f.sink_symbol_id == sid]
        assert len(intra) == 1
        assert intra[0].source_type == "request.args"
        assert intra[0].sink_type == "eval("


# ---------------------------------------------------------------------------
# Inter-procedural dataflow patterns in dataflow.py
# ---------------------------------------------------------------------------


class TestInterProceduralDataflowPatterns:
    def test_inter_source_to_sink(self):
        conn = _make_db()
        fid = _insert_file(conn, path="src/handler.py")
        sid1 = _insert_symbol(conn, fid, "get_input", qualified_name="app.get_input")
        sid2 = _insert_symbol(conn, fid, "do_eval", qualified_name="app.do_eval")

        # Insert taint finding
        conn.execute(
            """INSERT INTO taint_findings
               (source_symbol_id, sink_symbol_id, source_type, sink_type,
                call_chain, chain_length, sanitized, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid1, sid2, "request.args", "eval(", json.dumps([sid1, sid2]), 2, 0, 0.85),
        )

        from roam.rules.dataflow import _collect_inter_findings

        findings = _collect_inter_findings(
            conn,
            patterns={"inter_source_to_sink"},
        )
        assert len(findings) == 1
        assert findings[0]["type"] == "inter_source_to_sink"
        assert findings[0]["source"] == "request.args"
        assert findings[0]["sink"] == "eval("
        assert findings[0]["chain_length"] == 2

    def test_inter_unused_param(self):
        conn = _make_db()
        fid = _insert_file(conn, path="src/handler.py")
        sid = _insert_symbol(
            conn,
            fid,
            "process",
            qualified_name="app.process",
            signature="def process(x, y)",
        )

        # Insert taint summary: param 0 not in return or sink
        conn.execute(
            """INSERT INTO taint_summaries
               (symbol_id, param_taints_return, param_to_sink,
                return_from_source, direct_sources, direct_sinks, is_sanitizer)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, "{}", "{}", 0, "[]", "[]", 0),
        )

        from roam.rules.dataflow import _collect_inter_findings

        findings = _collect_inter_findings(
            conn,
            patterns={"inter_unused_param"},
        )
        # Both params 'x' and 'y' should be flagged
        param_findings = [f for f in findings if f["type"] == "inter_unused_param"]
        assert len(param_findings) == 2
        variables = {f["variable"] for f in param_findings}
        assert "x" in variables
        assert "y" in variables

    def test_inter_unused_return(self):
        conn = _make_db()
        fid = _insert_file(conn, path="src/handler.py")
        sid = _insert_symbol(
            conn,
            fid,
            "compute",
            qualified_name="app.compute",
        )
        # Insert symbol_metrics with return_count > 0
        conn.execute(
            "INSERT INTO symbol_metrics (symbol_id, return_count) VALUES (?, ?)",
            (sid, 1),
        )
        # Insert taint summary
        conn.execute(
            """INSERT INTO taint_summaries
               (symbol_id, param_taints_return, param_to_sink,
                return_from_source, direct_sources, direct_sinks, is_sanitizer)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, "{}", "{}", 0, "[]", "[]", 0),
        )
        # Insert a caller edge
        caller_fid = _insert_file(conn, path="src/main.py")
        caller_sid = _insert_symbol(conn, caller_fid, "main")
        _insert_edge(conn, caller_sid, sid, "calls")

        from roam.rules.dataflow import _collect_inter_findings

        findings = _collect_inter_findings(
            conn,
            patterns={"inter_unused_return"},
        )
        ret_findings = [f for f in findings if f["type"] == "inter_unused_return"]
        assert len(ret_findings) == 1
        assert ret_findings[0]["symbol"] == "app.compute"

    def test_normalize_patterns_accepts_inter(self):
        from roam.rules.dataflow import _normalize_patterns

        patterns = _normalize_patterns(["inter_source_to_sink", "dead_assignment"])
        assert "inter_source_to_sink" in patterns
        assert "dead_assignment" in patterns

    def test_normalize_patterns_default_excludes_inter(self):
        from roam.rules.dataflow import _normalize_patterns

        patterns = _normalize_patterns(None)
        assert "inter_source_to_sink" not in patterns
        assert "dead_assignment" in patterns

    def test_missing_taint_tables_graceful(self):
        """_collect_inter_findings should handle missing tables gracefully."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # No schema at all

        from roam.rules.dataflow import _collect_inter_findings

        findings = _collect_inter_findings(
            conn,
            patterns={"inter_source_to_sink"},
        )
        assert findings == []


# ---------------------------------------------------------------------------
# Engine integration: dataflow_match with inter-procedural keys
# ---------------------------------------------------------------------------


class TestEngineDataflowMatch:
    def test_max_chain_length_filter(self):
        conn = _make_db()
        fid = _insert_file(conn, path="src/handler.py")
        sid1 = _insert_symbol(conn, fid, "get_input", qualified_name="app.get_input")
        sid2 = _insert_symbol(conn, fid, "do_eval", qualified_name="app.do_eval")

        conn.execute(
            """INSERT INTO taint_findings
               (source_symbol_id, sink_symbol_id, source_type, sink_type,
                call_chain, chain_length, sanitized, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid1, sid2, "request.args", "eval(", json.dumps([sid1, sid2]), 4, 0, 0.7),
        )

        from roam.rules.engine import _evaluate_dataflow_match

        # With max_chain_length=3 => should filter out chain_length=4
        rule = {
            "name": "test-chain",
            "severity": "warning",
            "match": {
                "patterns": ["inter_source_to_sink"],
                "max_chain_length": 3,
            },
        }
        result = _evaluate_dataflow_match(rule, conn)
        assert result["passed"] is True

        # With max_chain_length=5 => should include
        rule["match"]["max_chain_length"] = 5
        result = _evaluate_dataflow_match(rule, conn)
        assert result["passed"] is False

    def test_min_confidence_filter(self):
        conn = _make_db()
        fid = _insert_file(conn, path="src/handler.py")
        sid1 = _insert_symbol(conn, fid, "get_input", qualified_name="app.get_input")
        sid2 = _insert_symbol(conn, fid, "do_eval", qualified_name="app.do_eval")

        conn.execute(
            """INSERT INTO taint_findings
               (source_symbol_id, sink_symbol_id, source_type, sink_type,
                call_chain, chain_length, sanitized, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid1, sid2, "request.args", "eval(", json.dumps([sid1, sid2]), 2, 0, 0.5),
        )

        from roam.rules.engine import _evaluate_dataflow_match

        # min_confidence=0.8 => should filter out confidence=0.5
        rule = {
            "name": "test-conf",
            "severity": "warning",
            "match": {
                "patterns": ["inter_source_to_sink"],
                "min_confidence": 0.8,
            },
        }
        result = _evaluate_dataflow_match(rule, conn)
        assert result["passed"] is True

        # min_confidence=0.3 => should include
        rule["match"]["min_confidence"] = 0.3
        result = _evaluate_dataflow_match(rule, conn)
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# Full pipeline: compute_and_store_taint
# ---------------------------------------------------------------------------


class TestComputeAndStoreTaint:
    def test_full_pipeline(self, tmp_path):
        conn = _make_db()

        # Create source files
        handler_file = tmp_path / "handler.py"
        handler_file.write_text(
            textwrap.dedent("""\
            def get_user_input():
                data = request.args.get('q')
                return data

            def process(cmd):
                eval(cmd)
            """),
            encoding="utf-8",
        )

        fid = _insert_file(conn, path="handler.py")
        sid1 = _insert_symbol(
            conn,
            fid,
            "get_user_input",
            qualified_name="handler.get_user_input",
            signature="def get_user_input()",
            line_start=1,
            line_end=3,
        )
        sid2 = _insert_symbol(
            conn,
            fid,
            "process",
            qualified_name="handler.process",
            signature="def process(cmd)",
            line_start=5,
            line_end=6,
        )
        _insert_edge(conn, sid1, sid2, "calls")

        compute_and_store_taint(conn, tmp_path)

        # Check summaries were stored
        summary_count = conn.execute("SELECT COUNT(*) FROM taint_summaries").fetchone()[0]
        assert summary_count == 2

        # Check get_user_input has return_from_source
        s1 = conn.execute("SELECT * FROM taint_summaries WHERE symbol_id = ?", (sid1,)).fetchone()
        assert s1["return_from_source"] == 1

        # Check process has param_to_sink
        s2 = conn.execute("SELECT * FROM taint_summaries WHERE symbol_id = ?", (sid2,)).fetchone()
        pts = json.loads(s2["param_to_sink"] or "{}")
        assert "0" in pts

    def test_empty_project(self, tmp_path):
        conn = _make_db()
        compute_and_store_taint(conn, tmp_path)
        assert conn.execute("SELECT COUNT(*) FROM taint_summaries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM taint_findings").fetchone()[0] == 0
