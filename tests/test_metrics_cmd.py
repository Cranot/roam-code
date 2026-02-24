"""Tests for roam metrics -- unified per-file/per-symbol metrics command."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.conftest import (
    invoke_cli,
    index_in_process,
    git_init,
    git_commit,
    parse_json_output,
    assert_json_envelope,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def metrics_project(project_factory):
    """A project with symbols suitable for metrics analysis."""
    return project_factory({
        "src/models.py": (
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def validate(self):\n"
            "        if not self.name:\n"
            "            return False\n"
            "        return True\n"
            "\n"
            "    def display(self):\n"
            "        return self.name.title()\n"
        ),
        "src/service.py": (
            "from models import User\n"
            "\n"
            "def create_user(name):\n"
            "    user = User(name)\n"
            "    if not user.validate():\n"
            "        raise ValueError('bad')\n"
            "    return user\n"
            "\n"
            "def get_display(user):\n"
            "    return user.display()\n"
            "\n"
            "def orphan_helper():\n"
            "    return 42\n"
        ),
        "src/utils.py": (
            "def format_name(first, last):\n"
            "    return f'{first} {last}'\n"
        ),
        "tests/test_models.py": (
            "def test_user(): pass\n"
            "def test_validate(): pass\n"
        ),
    })


@pytest.fixture
def single_file_project(project_factory):
    """A minimal single-file project."""
    return project_factory({
        "app.py": (
            "def main():\n"
            "    return 'hello'\n"
            "\n"
            "def helper():\n"
            "    return main()\n"
        ),
    })


# ---------------------------------------------------------------------------
# Unit tests: _resolve_target
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_resolve_file_exact(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                target_type, tid, row = _resolve_target(conn, "src/models.py")
                assert target_type == "file"
                assert tid is not None
        finally:
            os.chdir(old_cwd)

    def test_resolve_file_partial(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                target_type, tid, row = _resolve_target(conn, "models.py")
                assert target_type == "file"
                assert tid is not None
        finally:
            os.chdir(old_cwd)

    def test_resolve_symbol(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                target_type, tid, row = _resolve_target(conn, "create_user")
                assert target_type == "symbol"
                assert tid is not None
        finally:
            os.chdir(old_cwd)

    def test_resolve_unknown(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                target_type, tid, row = _resolve_target(conn, "nonexistent_xyz_999")
                assert target_type == "unknown"
                assert tid is None
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: collect_symbol_metrics
# ---------------------------------------------------------------------------

class TestCollectSymbolMetrics:
    def test_returns_all_keys(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_symbol_metrics, _resolve_target

        expected_keys = {
            "complexity", "fan_in", "fan_out", "pagerank", "betweenness",
            "closeness", "eigenvector", "clustering_coefficient", "debt_score",
            "churn", "commits", "test_files", "layer_depth",
            "dead_code_risk", "loc", "co_change_count",
            "information_scatter", "working_set_size", "comprehension_difficulty",
        }
        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, sid, _ = _resolve_target(conn, "create_user")
                if sid is None:
                    pytest.skip("create_user symbol not found")
                m = collect_symbol_metrics(conn, sid)
                assert expected_keys.issubset(set(m.keys())), (
                    f"Missing keys: {expected_keys - set(m.keys())}"
                )
        finally:
            os.chdir(old_cwd)

    def test_complexity_non_negative(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_symbol_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, sid, _ = _resolve_target(conn, "create_user")
                if sid is None:
                    pytest.skip("create_user symbol not found")
                m = collect_symbol_metrics(conn, sid)
                assert m["complexity"] >= 0
        finally:
            os.chdir(old_cwd)

    def test_pagerank_is_float(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_symbol_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, sid, _ = _resolve_target(conn, "create_user")
                if sid is None:
                    pytest.skip("create_user symbol not found")
                m = collect_symbol_metrics(conn, sid)
                assert isinstance(m["pagerank"], (int, float))
        finally:
            os.chdir(old_cwd)

    def test_sna_v2_metrics_types(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_symbol_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, sid, _ = _resolve_target(conn, "create_user")
                if sid is None:
                    pytest.skip("create_user symbol not found")
                m = collect_symbol_metrics(conn, sid)
                assert isinstance(m["closeness"], (int, float))
                assert isinstance(m["eigenvector"], (int, float))
                assert isinstance(m["clustering_coefficient"], (int, float))
                assert isinstance(m["debt_score"], (int, float))
                assert 0 <= m["debt_score"] <= 100
        finally:
            os.chdir(old_cwd)

    def test_comprehension_metrics_present(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_symbol_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, sid, _ = _resolve_target(conn, "create_user")
                if sid is None:
                    pytest.skip("create_user symbol not found")
                m = collect_symbol_metrics(conn, sid)
                assert isinstance(m["information_scatter"], int)
                assert isinstance(m["working_set_size"], int)
                assert isinstance(m["comprehension_difficulty"], (int, float))
                assert m["information_scatter"] >= 0
                assert m["working_set_size"] >= 0
                assert 0 <= m["comprehension_difficulty"] <= 100
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: collect_file_metrics
# ---------------------------------------------------------------------------

class TestCollectFileMetrics:
    def test_returns_expected_structure(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_file_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, fid, _ = _resolve_target(conn, "src/models.py")
                if fid is None:
                    pytest.skip("src/models.py not found")
                data = collect_file_metrics(conn, fid)
                assert "file" in data
                assert "metrics" in data
                assert "symbols" in data
                assert data["file"] == "src/models.py"
        finally:
            os.chdir(old_cwd)

    def test_file_metrics_keys(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_file_metrics, _resolve_target

        expected_keys = {
            "complexity", "fan_in", "fan_out", "max_pagerank",
            "churn", "commits", "test_files", "dead_symbols",
            "loc", "symbol_count", "co_change_count",
        }
        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, fid, _ = _resolve_target(conn, "src/models.py")
                if fid is None:
                    pytest.skip("src/models.py not found")
                data = collect_file_metrics(conn, fid)
                fm = data["metrics"]
                assert expected_keys.issubset(set(fm.keys())), (
                    f"Missing: {expected_keys - set(fm.keys())}"
                )
        finally:
            os.chdir(old_cwd)

    def test_symbols_list_not_empty(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_file_metrics, _resolve_target

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                _, fid, _ = _resolve_target(conn, "src/models.py")
                if fid is None:
                    pytest.skip("src/models.py not found")
                data = collect_file_metrics(conn, fid)
                assert len(data["symbols"]) > 0
        finally:
            os.chdir(old_cwd)

    def test_nonexistent_file_returns_empty(self, metrics_project):
        from roam.db.connection import open_db
        from roam.commands.cmd_metrics import collect_file_metrics

        old_cwd = os.getcwd()
        try:
            os.chdir(str(metrics_project))
            with open_db(readonly=True) as conn:
                data = collect_file_metrics(conn, 999999)
                assert data == {}
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Unit tests: _health_label
# ---------------------------------------------------------------------------

class TestHealthLabel:
    def test_good(self):
        from roam.commands.cmd_metrics import _health_label
        assert _health_label({"complexity": 5, "fan_out": 3, "churn": 2, "dead_code_risk": False}) == "good"

    def test_fair(self):
        from roam.commands.cmd_metrics import _health_label
        assert _health_label({"complexity": 20, "fan_out": 5, "churn": 2, "dead_code_risk": False}) == "fair"

    def test_poor(self):
        from roam.commands.cmd_metrics import _health_label
        assert _health_label({"complexity": 30, "fan_out": 20, "churn": 100, "dead_code_risk": True}) == "poor"

    def test_dead_code_risk_bumps_score(self):
        from roam.commands.cmd_metrics import _health_label
        # Without dead code risk: good
        assert _health_label({"complexity": 5, "fan_out": 3, "churn": 2, "dead_code_risk": False}) == "good"
        # With dead code risk: fair
        assert _health_label({"complexity": 5, "fan_out": 3, "churn": 2, "dead_code_risk": True}) == "fair"


# ---------------------------------------------------------------------------
# Helper: invoke metrics command directly (not registered in cli.py yet)
# ---------------------------------------------------------------------------

def _invoke_metrics(runner, args, cwd, json_mode=False):
    """Invoke the metrics Click command directly."""
    from roam.commands.cmd_metrics import metrics

    obj = {}
    if json_mode:
        obj["json"] = True

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(metrics, args, obj=obj, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# CLI command tests: text output
# ---------------------------------------------------------------------------

class TestMetricsCommandText:
    def test_symbol_text_output(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["create_user"], cwd=metrics_project
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "VERDICT:" in result.output
        assert "health=" in result.output

    def test_file_text_output(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["src/models.py"], cwd=metrics_project
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"
        assert "VERDICT:" in result.output
        assert "health=" in result.output

    def test_file_output_includes_breakdown(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["src/models.py"], cwd=metrics_project
        )
        assert result.exit_code == 0
        assert "Symbol Breakdown" in result.output

    def test_unknown_target_fails(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["nonexistent_xyz_999"], cwd=metrics_project
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI command tests: JSON output
# ---------------------------------------------------------------------------

class TestMetricsCommandJSON:
    def test_symbol_json_envelope(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["create_user"],
            cwd=metrics_project, json_mode=True,
        )
        data = parse_json_output(result, command="metrics")
        assert_json_envelope(data, command="metrics")
        assert data["summary"]["target_type"] == "symbol"
        assert "metrics" in data
        assert "health" in data["summary"]

    def test_file_json_envelope(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["src/models.py"],
            cwd=metrics_project, json_mode=True,
        )
        data = parse_json_output(result, command="metrics")
        assert_json_envelope(data, command="metrics")
        assert data["summary"]["target_type"] == "file"
        assert "metrics" in data
        assert "symbols" in data

    def test_file_json_has_symbol_list(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["src/models.py"],
            cwd=metrics_project, json_mode=True,
        )
        data = parse_json_output(result, command="metrics")
        assert isinstance(data["symbols"], list)
        assert len(data["symbols"]) > 0
        # Each symbol entry has expected fields
        for s in data["symbols"]:
            assert "name" in s
            assert "kind" in s
            assert "complexity" in s
            assert "fan_in" in s
            assert "fan_out" in s

    def test_unknown_target_json(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["nonexistent_xyz_999"],
            cwd=metrics_project, json_mode=True,
        )
        # Should exit non-zero but still produce valid JSON
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data or "not found" in data.get("summary", {}).get("verdict", "")

    def test_symbol_json_metrics_keys(self, metrics_project, cli_runner):
        result = _invoke_metrics(
            cli_runner, ["create_user"],
            cwd=metrics_project, json_mode=True,
        )
        data = parse_json_output(result, command="metrics")
        m = data["metrics"]
        expected = {"complexity", "fan_in", "fan_out", "pagerank", "churn", "commits"}
        assert expected.issubset(set(m.keys())), (
            f"Missing: {expected - set(m.keys())}"
        )


# ---------------------------------------------------------------------------
# CLI edge cases
# ---------------------------------------------------------------------------

class TestMetricsEdgeCases:
    def test_partial_file_path(self, metrics_project, cli_runner):
        """Partial path should still resolve to the file."""
        result = _invoke_metrics(
            cli_runner, ["utils.py"], cwd=metrics_project
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_single_file_project(self, single_file_project, cli_runner):
        """Works on a minimal project."""
        result = _invoke_metrics(
            cli_runner, ["app.py"], cwd=single_file_project
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_help(self, cli_runner):
        from roam.commands.cmd_metrics import metrics
        result = cli_runner.invoke(metrics, ["--help"])
        assert result.exit_code == 0
        assert "TARGET" in result.output
