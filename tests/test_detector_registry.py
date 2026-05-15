"""Tests for the A3 detector registry + @detector decorator.

Validates:
- decorated detectors land in `_DETECTOR_REGISTRY` with the required metadata fields
- `roam math --list-detectors` enumerates registered detectors (text + JSON)
- `roam math --only X` restricts the scan to detector X
- `roam math --exclude X` runs all-but-X
- bogus metadata values are rejected at decoration time (fail-fast vs. silent
  envelope corruption)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output  # noqa: E402


# ----------------------------------------------------------------------------
# Registry shape
# ----------------------------------------------------------------------------


class TestRegistryShape:
    """The registry exists, is populated, and entries are well-typed."""

    def test_registry_is_populated(self):
        from roam.catalog.detectors import _DETECTOR_REGISTRY

        # A3 wave 1 seeded the registry with high-leverage detectors; W84
        # completion decorated the remaining tail so the registry covers the
        # entire detector surface (34 entries). The ratchet locks the floor
        # to the current count — if a future PR drops decorators, this
        # number drops with it and the test fails.
        assert len(_DETECTOR_REGISTRY) >= 34, (
            f"registry should hold at least 34 decorated detectors, "
            f"got {len(_DETECTOR_REGISTRY)}"
        )

    def test_high_leverage_detectors_decorated(self):
        """The dogfood-prioritised detectors are all in the registry."""
        from roam.catalog.detectors import _DETECTOR_REGISTRY

        expected = {
            "detect_nested_lookup",
            "detect_naive_fibonacci",
            "detect_busy_wait",
            "detect_regex_in_loop",
            "detect_io_in_loop",
            "detect_serial_await_loop",
            "detect_async_blocking_sleep",
            "detect_broad_except_swallow",
            "detect_useeffect_missing_deps",
            "detect_dangerous_eval",
            "detect_branching_recursion",
        }
        missing = expected - set(_DETECTOR_REGISTRY.keys())
        assert not missing, f"high-leverage detectors not decorated: {missing}"

    def test_entries_have_required_fields(self):
        from roam.catalog.detectors import _DETECTOR_REGISTRY

        required = {"name", "task_id", "languages", "confidence_basis", "query_cost", "version", "function"}
        for name, entry in _DETECTOR_REGISTRY.items():
            missing = required - entry.keys()
            assert not missing, f"{name} missing fields: {missing}"
            assert callable(entry["function"]), f"{name}.function not callable"
            assert isinstance(entry["languages"], tuple), f"{name}.languages must be tuple"

    def test_entries_use_allowed_vocabulary(self):
        """confidence_basis/query_cost must stay inside the declared enums."""
        from roam.catalog.detectors import _CONFIDENCE_BASES, _DETECTOR_REGISTRY, _QUERY_COSTS

        for name, entry in _DETECTOR_REGISTRY.items():
            assert entry["confidence_basis"] in _CONFIDENCE_BASES, (
                f"{name} has invalid confidence_basis {entry['confidence_basis']!r}"
            )
            assert entry["query_cost"] in _QUERY_COSTS, (
                f"{name} has invalid query_cost {entry['query_cost']!r}"
            )

    def test_decorator_rejects_bad_confidence_basis(self):
        from roam.catalog.detectors import detector

        with pytest.raises(ValueError, match="confidence_basis"):

            @detector(task_id="x", confidence_basis="bogus")
            def _bad(conn):  # pragma: no cover - decoration must raise
                return []

    def test_decorator_rejects_bad_query_cost(self):
        from roam.catalog.detectors import detector

        with pytest.raises(ValueError, match="query_cost"):

            @detector(task_id="x", query_cost="ludicrous")
            def _bad(conn):  # pragma: no cover - decoration must raise
                return []

    def test_list_registered_detectors_strips_callable(self):
        """The introspection helper omits the function reference."""
        from roam.catalog.detectors import list_registered_detectors

        for entry in list_registered_detectors():
            assert "function" not in entry, "list_registered_detectors must hide callables"
            assert "name" in entry


# ----------------------------------------------------------------------------
# CLI flags
# ----------------------------------------------------------------------------


class TestMathListDetectorsFlag:
    def test_text_output(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["algo", "--list-detectors"], cwd=indexed_project)
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output
        assert "decorated detectors" in result.output
        assert "detect_nested_lookup" in result.output
        assert "nested-lookup" in result.output

    def test_json_output(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["algo", "--list-detectors"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        assert "detectors" in data
        assert data["summary"]["detector_count"] == len(data["detectors"])
        assert data["summary"]["detector_count"] >= 10
        names = {d["name"] for d in data["detectors"]}
        assert "detect_nested_lookup" in names
        sample = data["detectors"][0]
        for k in ("name", "task_id", "languages", "confidence_basis", "query_cost", "version"):
            assert k in sample, f"detector entry missing {k}: {sample}"


class TestMathOnlyExcludeFlags:
    """`--only` / `--exclude` filter detector execution; behavior unchanged otherwise."""

    def test_only_runs_just_named_detector(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["algo", "--only", "detect_nested_lookup"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        # Only one detector should have run.
        assert data["summary"]["detectors_executed"] == 1
        # Every finding (if any) is from that detector's task.
        for f in data.get("findings", []):
            assert f["task_id"] == "nested-lookup", f

    def test_only_unknown_name_runs_nothing(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["algo", "--only", "detect_does_not_exist"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        assert data["summary"]["detectors_executed"] == 0
        assert data.get("findings", []) == []

    def test_exclude_drops_named_detector(self, cli_runner, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        baseline = invoke_cli(cli_runner, ["algo"], cwd=indexed_project, json_mode=True)
        baseline_data = parse_json_output(baseline, "algo")
        baseline_executed = baseline_data["summary"]["detectors_executed"]

        result = invoke_cli(
            cli_runner,
            ["algo", "--exclude", "detect_nested_lookup"],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        assert data["summary"]["detectors_executed"] == baseline_executed - 1
        for f in data.get("findings", []):
            assert f["task_id"] != "nested-lookup", f

    def test_only_wins_over_exclude_on_same_name(self, cli_runner, indexed_project, monkeypatch):
        """If a name appears in both --only and --exclude, --only wins."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            [
                "algo",
                "--only",
                "detect_nested_lookup",
                "--exclude",
                "detect_nested_lookup",
            ],
            cwd=indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "algo")
        assert data["summary"]["detectors_executed"] == 1
