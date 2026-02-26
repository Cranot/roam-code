"""Tests for roam uses -- show all consumers of a symbol."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def uses_project(tmp_path):
    """Python project designed to exercise the ``uses`` command.

    Layout:
        base.py      -- BaseProcessor (class + process method), validate() utility
        worker.py    -- Worker(BaseProcessor) that calls validate() and defines run()
        manager.py   -- Manager that calls Worker.run() and validate()
        isolated.py  -- standalone() with no callers and no callees
    """
    proj = tmp_path / "uses_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "base.py").write_text(
        "class BaseProcessor:\n"
        "    def process(self, data):\n"
        "        return data\n"
        "\n"
        "\n"
        "def validate(data):\n"
        "    if data is None:\n"
        "        raise ValueError('empty')\n"
        "    return True\n"
    )

    (proj / "worker.py").write_text(
        "from base import BaseProcessor, validate\n"
        "\n"
        "\n"
        "class Worker(BaseProcessor):\n"
        "    def run(self):\n"
        "        validate(self)\n"
        "        return self.process('work')\n"
    )

    (proj / "manager.py").write_text(
        "from base import validate\n"
        "from worker import Worker\n"
        "\n"
        "\n"
        "class Manager:\n"
        "    def execute(self):\n"
        "        w = Worker()\n"
        "        validate(w)\n"
        "        return w.run()\n"
    )

    (proj / "isolated.py").write_text(
        "def standalone():\n"
        "    return 42\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestUsesSmoke:
    def test_exits_zero(self, cli_runner, uses_project, monkeypatch):
        """uses with a symbol that has callers should exit 0."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project)
        assert result.exit_code == 0

    def test_no_results_exits_zero(self, cli_runner, uses_project, monkeypatch):
        """uses with a symbol that has no callers should still exit 0."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "standalone"], cwd=uses_project)
        assert result.exit_code == 0

    def test_unknown_symbol(self, cli_runner, uses_project, monkeypatch):
        """uses with a symbol not in the index should exit 1."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "nonexistent_xyz"], cwd=uses_project)
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestUsesJSON:
    def test_json_envelope(self, cli_runner, uses_project, monkeypatch):
        """JSON output should follow the standard roam envelope contract."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        assert_json_envelope(data, "uses")

    def test_json_has_callers(self, cli_runner, uses_project, monkeypatch):
        """validate has callers so consumers dict should be non-empty."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        total = sum(len(v) for v in consumers.values())
        assert total > 0, f"Expected at least 1 consumer, got {total}"

    def test_json_caller_fields(self, cli_runner, uses_project, monkeypatch):
        """Each consumer entry should have name, kind, and location."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        required_keys = {"name", "kind", "location"}
        for edge_kind, entries in consumers.items():
            for entry in entries:
                missing = required_keys - set(entry.keys())
                assert not missing, (
                    f"Consumer under edge_kind='{edge_kind}' missing keys: {missing}. "
                    f"Got: {entry}"
                )

    def test_json_no_callers(self, cli_runner, uses_project, monkeypatch):
        """standalone has no callers so consumers dict should be empty."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "standalone"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        total = sum(len(v) for v in consumers.values())
        assert total == 0, f"Expected 0 consumers, got {total}"

    def test_json_summary_total_consumers(self, cli_runner, uses_project, monkeypatch):
        """summary.total_consumers should match the number of consumer entries."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        actual_total = sum(len(v) for v in consumers.values())
        assert data["summary"]["total_consumers"] == actual_total

    def test_json_summary_total_files(self, cli_runner, uses_project, monkeypatch):
        """summary.total_files should be a non-negative integer."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        assert isinstance(data["summary"]["total_files"], int)
        assert data["summary"]["total_files"] >= 0


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestUsesText:
    def test_verdict_line(self, cli_runner, uses_project, monkeypatch):
        """Text output should start with a VERDICT: line."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project)
        assert "VERDICT:" in result.output

    def test_shows_caller_names(self, cli_runner, uses_project, monkeypatch):
        """Consumer names should appear when querying a widely-used symbol."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project)
        out = result.output
        # At least one of the calling symbols should be listed
        has_worker_ref = "Worker" in out or "worker" in out.lower() or "run" in out
        has_manager_ref = "Manager" in out or "manager" in out.lower() or "execute" in out
        assert has_worker_ref or has_manager_ref, (
            f"Expected caller names from worker.py or manager.py in output:\n{out}"
        )

    def test_no_callers_text(self, cli_runner, uses_project, monkeypatch):
        """standalone has no callers -- text should indicate that."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "standalone"], cwd=uses_project)
        out = result.output.lower()
        assert "no consumer" in out or "0 consumer" in out, (
            f"Expected 'no consumers' message for standalone, got:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# Detection / semantic tests
# ---------------------------------------------------------------------------


class TestUsesDetection:
    def test_finds_multiple_callers(self, cli_runner, uses_project, monkeypatch):
        """validate is called from worker.py and manager.py -- at least 2 consumers."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        total = data["summary"]["total_consumers"]
        assert total >= 2, f"Expected >= 2 consumers for validate, got {total}"

    def test_groups_by_edge_kind(self, cli_runner, uses_project, monkeypatch):
        """consumers dict should be keyed by edge kind strings."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        for edge_kind in consumers:
            assert isinstance(edge_kind, str), f"edge kind key should be str, got {type(edge_kind)}"
            # Known edge kinds that roam uses
            known = {"call", "import", "inherits", "implements", "uses_trait", "template"}
            assert edge_kind in known or edge_kind, (
                f"Unexpected empty edge kind key"
            )

    def test_inheritance(self, cli_runner, uses_project, monkeypatch):
        """BaseProcessor should have Worker listed as an inheritor or consumer."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "BaseProcessor"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        # Flatten all consumer names across all edge kinds
        all_names = []
        for entries in consumers.values():
            all_names.extend(e["name"] for e in entries)
        assert "Worker" in all_names, (
            f"Expected Worker as a consumer of BaseProcessor, got: {all_names}"
        )

    def test_total_files_matches_consumer_locations(self, cli_runner, uses_project, monkeypatch):
        """total_files in summary should match distinct files in consumer entries."""
        monkeypatch.chdir(uses_project)
        result = invoke_cli(cli_runner, ["uses", "validate"], cwd=uses_project, json_mode=True)
        data = parse_json_output(result, "uses")
        consumers = data.get("consumers", {})
        files_seen = set()
        for entries in consumers.values():
            for e in entries:
                # location is "path:line" format
                path = e["location"].rsplit(":", 1)[0] if ":" in e["location"] else e["location"]
                files_seen.add(path)
        assert data["summary"]["total_files"] == len(files_seen)
