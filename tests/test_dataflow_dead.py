"""Tests for dataflow-enhanced dead code detection.

Covers:
- _analyze_dataflow_dead() returns empty list when tables don't exist
- _analyze_dataflow_dead() finds unused_return findings
- _analyze_dataflow_dead() finds dead_param_chain findings
- _analyze_dataflow_dead() finds side_effect_only findings
- The --dataflow flag is accepted by the CLI command
- JSON output includes dataflow_dead key when --dataflow is used
"""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from click.testing import CliRunner
from conftest import git_init, index_in_process, invoke_cli

from roam.commands.cmd_dead import _analyze_dataflow_dead
from roam.db.schema import SCHEMA_SQL

# ===========================================================================
# Helpers
# ===========================================================================


def _setup_db(tmp_path):
    """Create a temporary SQLite database with the roam schema."""
    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    from roam.db.connection import ensure_schema

    ensure_schema(conn)
    return conn


def _insert_file(conn, path="src/utils.py", language="python"):
    """Insert a file row and return its id."""
    conn.execute("INSERT INTO files (path, language) VALUES (?, ?)", (path, language))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_symbol(conn, file_id, name, kind="function", qname=None, signature=None, line_start=1, line_end=10):
    """Insert a symbol row and return its id."""
    qname = qname or name
    conn.execute(
        "INSERT INTO symbols (file_id, name, qualified_name, kind, signature, "
        "line_start, line_end, is_exported) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (file_id, name, qname, kind, signature, line_start, line_end),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_edge(conn, source_id, target_id, kind="calls", line=None):
    """Insert an edge row."""
    conn.execute(
        "INSERT INTO edges (source_id, target_id, kind, line) VALUES (?, ?, ?, ?)",
        (source_id, target_id, kind, line),
    )


def _insert_symbol_metric(conn, symbol_id, return_count=0, cognitive_complexity=0):
    """Insert a symbol_metrics row."""
    conn.execute(
        "INSERT INTO symbol_metrics (symbol_id, return_count, cognitive_complexity) VALUES (?, ?, ?)",
        (symbol_id, return_count, cognitive_complexity),
    )


def _insert_taint_summary(conn, symbol_id, param_taints_return=None, param_to_sink=None, is_sanitizer=0):
    """Insert a taint_summaries row."""
    conn.execute(
        "INSERT INTO taint_summaries (symbol_id, param_taints_return, param_to_sink, is_sanitizer) VALUES (?, ?, ?, ?)",
        (symbol_id, param_taints_return, param_to_sink, is_sanitizer),
    )


def _insert_symbol_effect(conn, symbol_id, effect_type, source="direct"):
    """Insert a symbol_effects row."""
    conn.execute(
        "INSERT INTO symbol_effects (symbol_id, effect_type, source) VALUES (?, ?, ?)",
        (symbol_id, effect_type, source),
    )


# ===========================================================================
# Tests: _analyze_dataflow_dead() internals
# ===========================================================================


class TestDataflowDeadEmpty:
    """Test that _analyze_dataflow_dead returns empty when tables missing or empty."""

    def test_returns_empty_when_no_taint_table(self, tmp_path):
        """If taint_summaries table does not exist, returns empty list."""
        db_path = tmp_path / "bare.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Only create a minimal schema without taint_summaries
        conn.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT)")
        result = _analyze_dataflow_dead(conn)
        assert result == []
        conn.close()

    def test_returns_empty_when_tables_exist_but_empty(self, tmp_path):
        """When tables exist but have no data, returns empty list."""
        conn = _setup_db(tmp_path)
        result = _analyze_dataflow_dead(conn)
        assert result == []
        conn.close()


class TestUnusedReturn:
    """Test unused_return findings from _analyze_dataflow_dead."""

    def test_finds_unused_return_when_all_callers_discard(self, tmp_path):
        """Function whose return value is discarded by all callers => unused_return."""
        conn = _setup_db(tmp_path)

        # Create a source file on disk so the file cache can read it
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        caller_file = src_dir / "caller.py"
        caller_file.write_text(
            textwrap.dedent("""\
                def main():
                    compute_value()
                    print("done")
            """),
            encoding="utf-8",
        )
        target_file = src_dir / "utils.py"
        target_file.write_text(
            textwrap.dedent("""\
                def compute_value():
                    return 42
            """),
            encoding="utf-8",
        )

        # Insert DB records
        fid_caller = _insert_file(conn, "src/caller.py")
        fid_target = _insert_file(conn, "src/utils.py")
        sid_main = _insert_symbol(conn, fid_caller, "main", qname="main", line_start=1, line_end=3)
        sid_compute = _insert_symbol(conn, fid_target, "compute_value", qname="compute_value", line_start=1, line_end=2)
        _insert_symbol_metric(conn, sid_compute, return_count=1)
        _insert_edge(conn, sid_main, sid_compute, kind="calls", line=2)
        # Insert a taint summary so the table check passes
        _insert_taint_summary(conn, sid_compute)
        conn.commit()

        # Patch find_project_root to return tmp_path
        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        unused_returns = [f for f in findings if f["type"] == "unused_return"]
        assert len(unused_returns) == 1
        assert unused_returns[0]["symbol"] == "compute_value"
        assert unused_returns[0]["confidence"] == 85
        assert "discarded" in unused_returns[0]["reason"]
        conn.close()

    def test_no_finding_when_return_captured(self, tmp_path):
        """Function whose return is captured by caller => no unused_return finding."""
        conn = _setup_db(tmp_path)

        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        caller_file = src_dir / "caller.py"
        caller_file.write_text(
            textwrap.dedent("""\
                def main():
                    result = compute_value()
                    print(result)
            """),
            encoding="utf-8",
        )
        target_file = src_dir / "utils.py"
        target_file.write_text("def compute_value():\n    return 42\n", encoding="utf-8")

        fid_caller = _insert_file(conn, "src/caller.py")
        fid_target = _insert_file(conn, "src/utils.py")
        sid_main = _insert_symbol(conn, fid_caller, "main", qname="main", line_start=1, line_end=3)
        sid_compute = _insert_symbol(conn, fid_target, "compute_value", qname="compute_value", line_start=1, line_end=2)
        _insert_symbol_metric(conn, sid_compute, return_count=1)
        _insert_edge(conn, sid_main, sid_compute, kind="calls", line=2)
        _insert_taint_summary(conn, sid_compute)
        conn.commit()

        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        unused_returns = [f for f in findings if f["type"] == "unused_return"]
        assert len(unused_returns) == 0
        conn.close()

    def test_no_finding_when_no_callers(self, tmp_path):
        """Function with return_count > 0 but no callers => no finding."""
        conn = _setup_db(tmp_path)

        fid = _insert_file(conn, "src/utils.py")
        sid = _insert_symbol(conn, fid, "compute_value", qname="compute_value")
        _insert_symbol_metric(conn, sid, return_count=1)
        _insert_taint_summary(conn, sid)
        conn.commit()

        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        unused_returns = [f for f in findings if f["type"] == "unused_return"]
        assert len(unused_returns) == 0
        conn.close()


class TestDeadParamChain:
    """Test dead_param_chain findings from _analyze_dataflow_dead."""

    def test_finds_dead_param_when_no_dataflow_effect(self, tmp_path):
        """Parameter with no return taint and no sink => dead_param_chain."""
        conn = _setup_db(tmp_path)

        fid = _insert_file(conn, "src/utils.py")
        sid = _insert_symbol(
            conn, fid, "process", qname="process", kind="function", signature="def process(data, flag)"
        )
        # param 'data' (index 0): no return taint, no sink
        # param 'flag' (index 1): taints return
        _insert_taint_summary(
            conn,
            sid,
            param_taints_return=json.dumps({"0": False, "1": True}),
            param_to_sink=json.dumps({"0": "", "1": ""}),
        )
        conn.commit()

        findings = _analyze_dataflow_dead(conn)
        dead_params = [f for f in findings if f["type"] == "dead_param_chain"]
        assert len(dead_params) == 1
        assert dead_params[0]["variable"] == "data"
        assert dead_params[0]["confidence"] == 75
        assert "no dataflow effect" in dead_params[0]["reason"]
        conn.close()

    def test_no_finding_when_param_taints_return(self, tmp_path):
        """Parameter that taints return => no dead_param_chain finding."""
        conn = _setup_db(tmp_path)

        fid = _insert_file(conn, "src/utils.py")
        sid = _insert_symbol(conn, fid, "identity", qname="identity", kind="function", signature="def identity(x)")
        _insert_taint_summary(
            conn,
            sid,
            param_taints_return=json.dumps({"0": True}),
            param_to_sink=json.dumps({}),
        )
        conn.commit()

        findings = _analyze_dataflow_dead(conn)
        dead_params = [f for f in findings if f["type"] == "dead_param_chain"]
        assert len(dead_params) == 0
        conn.close()

    def test_no_finding_when_param_flows_to_sink(self, tmp_path):
        """Parameter that flows to a sink => no dead_param_chain finding."""
        conn = _setup_db(tmp_path)

        fid = _insert_file(conn, "src/utils.py")
        sid = _insert_symbol(conn, fid, "log_it", qname="log_it", kind="function", signature="def log_it(msg)")
        _insert_taint_summary(
            conn,
            sid,
            param_taints_return=json.dumps({"0": False}),
            param_to_sink=json.dumps({"0": "logging"}),
        )
        conn.commit()

        findings = _analyze_dataflow_dead(conn)
        dead_params = [f for f in findings if f["type"] == "dead_param_chain"]
        assert len(dead_params) == 0
        conn.close()

    def test_skips_self_cls_underscore_params(self, tmp_path):
        """Parameters named self, cls, _ are skipped."""
        conn = _setup_db(tmp_path)

        fid = _insert_file(conn, "src/utils.py")
        sid = _insert_symbol(
            conn, fid, "method", qname="MyClass.method", kind="method", signature="def method(self, _, data)"
        )
        # All params have no effect, but self/_ should be skipped
        _insert_taint_summary(
            conn,
            sid,
            param_taints_return=json.dumps({}),
            param_to_sink=json.dumps({}),
        )
        conn.commit()

        findings = _analyze_dataflow_dead(conn)
        dead_params = [f for f in findings if f["type"] == "dead_param_chain"]
        # Only 'data' should appear (self and _ are skipped)
        assert len(dead_params) == 1
        assert dead_params[0]["variable"] == "data"
        conn.close()


class TestSideEffectOnly:
    """Test side_effect_only findings from _analyze_dataflow_dead."""

    def test_finds_side_effect_only_with_logging_effect(self, tmp_path):
        """Function with unused return + only logging effects => side_effect_only."""
        conn = _setup_db(tmp_path)

        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        caller_file = src_dir / "caller.py"
        caller_file.write_text(
            textwrap.dedent("""\
                def main():
                    log_event()
                    print("done")
            """),
            encoding="utf-8",
        )
        target_file = src_dir / "utils.py"
        target_file.write_text("def log_event():\n    return True\n", encoding="utf-8")

        fid_caller = _insert_file(conn, "src/caller.py")
        fid_target = _insert_file(conn, "src/utils.py")
        sid_main = _insert_symbol(conn, fid_caller, "main", qname="main", line_start=1, line_end=3)
        sid_log = _insert_symbol(conn, fid_target, "log_event", qname="log_event", line_start=1, line_end=2)
        _insert_symbol_metric(conn, sid_log, return_count=1)
        _insert_edge(conn, sid_main, sid_log, kind="calls", line=2)
        _insert_taint_summary(conn, sid_log)
        _insert_symbol_effect(conn, sid_log, "logging")
        conn.commit()

        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        side_effect = [f for f in findings if f["type"] == "side_effect_only"]
        assert len(side_effect) == 1
        assert side_effect[0]["symbol"] == "log_event"
        assert side_effect[0]["confidence"] == 70
        assert "logging" in side_effect[0]["reason"]
        conn.close()

    def test_no_side_effect_when_non_benign_effects(self, tmp_path):
        """Function with unused return but non-benign effects => no side_effect_only."""
        conn = _setup_db(tmp_path)

        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        caller_file = src_dir / "caller.py"
        caller_file.write_text("def main():\n    write_data()\n", encoding="utf-8")
        target_file = src_dir / "utils.py"
        target_file.write_text("def write_data():\n    return True\n", encoding="utf-8")

        fid_caller = _insert_file(conn, "src/caller.py")
        fid_target = _insert_file(conn, "src/utils.py")
        sid_main = _insert_symbol(conn, fid_caller, "main", qname="main", line_start=1, line_end=2)
        sid_write = _insert_symbol(conn, fid_target, "write_data", qname="write_data", line_start=1, line_end=2)
        _insert_symbol_metric(conn, sid_write, return_count=1)
        _insert_edge(conn, sid_main, sid_write, kind="calls", line=2)
        _insert_taint_summary(conn, sid_write)
        _insert_symbol_effect(conn, sid_write, "filesystem")
        conn.commit()

        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        side_effect = [f for f in findings if f["type"] == "side_effect_only"]
        assert len(side_effect) == 0
        conn.close()


# ===========================================================================
# Tests: CLI integration
# ===========================================================================


class TestDataflowFlag:
    """Test that --dataflow flag is accepted and works in the CLI."""

    def test_dataflow_flag_accepted(self, tmp_path):
        """The --dataflow flag is accepted without error."""
        # Create a minimal project
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        git_init(str(proj))
        index_in_process(proj)

        runner = CliRunner()
        result = invoke_cli(runner, ["dead", "--dataflow"], cwd=str(proj))
        # Should not error out
        assert result.exit_code == 0

    def test_json_output_includes_dataflow_dead_in_summary(self, tmp_path):
        """JSON summary includes dataflow_dead count when --dataflow is used."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        git_init(str(proj))
        index_in_process(proj)

        runner = CliRunner()
        result = invoke_cli(runner, ["dead", "--dataflow"], cwd=str(proj), json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dataflow_dead" in data["summary"]
        assert isinstance(data["summary"]["dataflow_dead"], int)

    def test_json_detail_includes_dataflow_dead_list(self, tmp_path):
        """JSON --detail output includes full dataflow_dead list when --dataflow is used."""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        git_init(str(proj))
        index_in_process(proj)

        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "dead", "--dataflow"], cwd=str(proj), json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dataflow_dead" in data
        assert isinstance(data["dataflow_dead"], list)


class TestFindingsSorted:
    """Test that findings are sorted by confidence descending."""

    def test_findings_sorted_by_confidence(self, tmp_path):
        """Multiple finding types are sorted by confidence desc."""
        conn = _setup_db(tmp_path)

        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "caller.py").write_text("def main():\n    compute()\n", encoding="utf-8")
        (src_dir / "utils.py").write_text("def compute():\n    return 42\n", encoding="utf-8")

        fid_caller = _insert_file(conn, "src/caller.py")
        fid_target = _insert_file(conn, "src/utils.py")
        sid_main = _insert_symbol(conn, fid_caller, "main", qname="main", line_start=1, line_end=2)
        sid_compute = _insert_symbol(
            conn,
            fid_target,
            "compute",
            qname="compute",
            kind="function",
            signature="def compute(x)",
            line_start=1,
            line_end=2,
        )
        _insert_symbol_metric(conn, sid_compute, return_count=1)
        _insert_edge(conn, sid_main, sid_compute, kind="calls", line=2)

        # Taint summary with dead param
        _insert_taint_summary(
            conn,
            sid_compute,
            param_taints_return=json.dumps({"0": False}),
            param_to_sink=json.dumps({}),
        )

        # Logging effect for side_effect_only
        _insert_symbol_effect(conn, sid_compute, "logging")
        conn.commit()

        import roam.commands.cmd_dead as mod

        orig = mod.find_project_root
        mod.find_project_root = lambda *a, **kw: tmp_path
        try:
            findings = _analyze_dataflow_dead(conn)
        finally:
            mod.find_project_root = orig

        # Should have multiple types, sorted by confidence
        assert len(findings) >= 2
        confidences = [f["confidence"] for f in findings]
        assert confidences == sorted(confidences, reverse=True)
        conn.close()
