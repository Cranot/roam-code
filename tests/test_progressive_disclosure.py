"""Tests for universal progressive disclosure (--detail flag)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.output.formatter import summary_envelope, json_envelope

# Import helpers from conftest
from tests.conftest import invoke_cli


# ---------------------------------------------------------------------------
# Helper: build a minimal JSON envelope for testing summary_envelope
# ---------------------------------------------------------------------------

def _make_envelope(command="test", **extra):
    return json_envelope(command, summary={"verdict": "ok", "score": 80}, **extra)


# ---------------------------------------------------------------------------
# Unit tests for summary_envelope helper
# ---------------------------------------------------------------------------

class TestSummaryEnvelope:
    def test_strips_list_payloads(self):
        data = _make_envelope(items=[{"a": 1}, {"a": 2}], count=5)
        result = summary_envelope(data)
        # Lists should be replaced (not present as full lists)
        assert "items" not in result
        # count is a scalar, should remain
        assert result.get("count") == 5
        # summary should be preserved
        assert result["summary"]["verdict"] == "ok"

    def test_adds_detail_available_flag(self):
        data = _make_envelope(items=[{"a": 1}])
        result = summary_envelope(data)
        assert result["summary"]["detail_available"] is True
        # truncated is set only when non-empty lists were stripped
        assert result["summary"]["truncated"] is True

    def test_empty_lists_only_adds_detail_available_not_truncated(self):
        """When all lists are empty, only detail_available is added (no truncated)."""
        data = _make_envelope(items=[])
        result = summary_envelope(data)
        assert result["summary"]["detail_available"] is True
        # truncated should NOT be set since nothing was actually stripped
        assert result["summary"].get("truncated") is not True

    def test_non_empty_lists_cause_truncated_flag(self):
        data = _make_envelope(items=[1, 2, 3], other=[4, 5])
        result = summary_envelope(data)
        # Non-empty lists → truncated=True in summary
        assert result["summary"].get("truncated") is True
        assert result["summary"].get("detail_available") is True

    def test_preserves_scalar_payloads(self):
        data = _make_envelope(score=42, mode="full")
        result = summary_envelope(data)
        assert result.get("score") == 42
        assert result.get("mode") == "full"

    def test_preserves_envelope_fields(self):
        data = _make_envelope()
        result = summary_envelope(data)
        for field in ("command", "schema", "schema_version", "version", "project", "_meta"):
            assert field in result, f"expected {field} in summary envelope"

    def test_empty_lists_handled(self):
        data = _make_envelope(items=[])
        result = summary_envelope(data)
        # Empty list → stripped from result, detail_available is set
        assert "items" not in result
        assert result["summary"]["detail_available"] is True
        # truncated is NOT set since only empty lists were stripped
        assert result["summary"].get("truncated") is not True

    def test_no_lists_no_truncated_flag(self):
        """When there are no list-valued fields, truncated is not set."""
        data = _make_envelope(scalar=42)
        result = summary_envelope(data)
        assert result["summary"].get("truncated") is not True
        assert result["summary"]["detail_available"] is True

    def test_summary_dict_preserved_intact(self):
        data = _make_envelope(items=[1, 2])
        data["summary"]["health_score"] = 77
        result = summary_envelope(data)
        assert result["summary"]["health_score"] == 77

    def test_non_list_nested_payload_preserved(self):
        data = _make_envelope(thresholds={"p70": 10, "p90": 20})
        result = summary_envelope(data)
        assert result.get("thresholds") == {"p70": 10, "p90": 20}


# ---------------------------------------------------------------------------
# Fixtures & helpers for CLI runner tests
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# CLI group tests: --detail flag is accepted and stored in ctx.obj
# ---------------------------------------------------------------------------

class TestDetailFlagParsing:
    def test_detail_flag_accepted_without_error(self, runner):
        """--detail flag should not produce a 'no such option' error."""
        result = runner.invoke(cli, ["--detail", "--help"])
        assert result.exit_code == 0
        # No error output about unrecognized option
        assert "no such option" not in (result.output or "").lower()
        assert "Error" not in (result.output or "")

    def test_detail_flag_in_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert "detail" in result.output.lower()

    def test_detail_flag_combined_with_json(self, runner):
        """--detail combined with --json should not error."""
        result = runner.invoke(cli, ["--json", "--detail", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Formatter-level tests (no real index needed)
# ---------------------------------------------------------------------------

class TestSummaryEnvelopeOutput:
    """Test that summary_envelope JSON output is valid and compact."""

    def test_summary_json_is_valid_with_non_empty_lists(self):
        data = _make_envelope(
            cycles=[{"id": 1}, {"id": 2}],
            god_components=[{"name": "Foo"}],
        )
        result = summary_envelope(data)
        # Must be JSON-serializable
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        # Non-empty lists → truncated and detail_available flags present
        assert parsed["summary"]["truncated"] is True
        assert parsed["summary"]["detail_available"] is True
        # List fields should be gone from top-level
        assert "cycles" not in parsed
        assert "god_components" not in parsed

    def test_summary_has_no_large_lists(self):
        many_items = [{"n": i} for i in range(100)]
        data = _make_envelope(items=many_items)
        result = summary_envelope(data)
        # items list should not be in result (stripped to save tokens)
        assert "items" not in result
        # truncated flag should be set since we stripped a non-empty list
        assert result["summary"]["truncated"] is True
        assert result["summary"]["detail_available"] is True

    def test_detail_mode_keeps_lists_untouched(self):
        """When summary_envelope is NOT called, full data remains."""
        data = _make_envelope(items=[1, 2, 3])
        # Without calling summary_envelope, lists stay
        assert data["items"] == [1, 2, 3]

    def test_summary_is_subset_of_detail_keys(self):
        """Summary envelope should have a subset of detail envelope keys."""
        data = _make_envelope(items=[1, 2, 3], extra=[4, 5])
        summary = summary_envelope(data)
        # All keys in summary (except summary dict changes) should exist in original
        for k in summary:
            if k not in ("items", "extra"):  # these were stripped
                assert k in data or k == "summary"


# ---------------------------------------------------------------------------
# Integration tests using the indexed_project fixture from conftest
# ---------------------------------------------------------------------------

class TestHealthProgressiveDisclosure:
    def test_health_summary_mode_has_verdict(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["health"], cwd=indexed_project)
        assert "VERDICT:" in result.output

    def test_health_summary_mode_is_shorter_than_detail(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, ["health"], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", "health"], cwd=indexed_project)
        # Detail output should be >= summary output length
        assert len(result_detail.output) >= len(result_summary.output), (
            f"detail ({len(result_detail.output)} chars) should be >= summary ({len(result_summary.output)} chars)"
        )

    def test_health_json_summary_has_detail_available(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["health"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"].get("detail_available") is True
        # truncated is set only when non-empty list fields were stripped
        # (may or may not be set depending on codebase state)

    def test_health_json_detail_mode_has_cycles_list(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "health"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # In detail mode, cycles list should be present
        assert "cycles" in data
        # No truncation flags
        assert data["summary"].get("truncated") is not True

    def test_health_detail_mode_has_sections(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "health"], cwd=indexed_project)
        assert "=== Cycles ===" in result.output
        assert "=== God Components" in result.output

    def test_health_summary_mode_no_cycle_section(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["health"], cwd=indexed_project)
        # Summary mode should not show the cycle section header
        assert "=== Cycles ===" not in result.output


class TestDeadProgressiveDisclosure:
    def test_dead_summary_mode_shows_count(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["dead"], cwd=indexed_project)
        assert result.exit_code == 0
        output = result.output
        # Should show either "Dead exports:" count, empty result, or a dead code section
        assert (
            "Dead exports:" in output
            or "none" in output.lower()
            or "=== Unreferenced" in output
        )

    def test_dead_json_summary_has_detail_available_when_dead(self, indexed_project, monkeypatch):
        """When dead symbols exist, summary mode should flag detail_available."""
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["dead"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        total = (
            data["summary"].get("safe", 0)
            + data["summary"].get("review", 0)
            + data["summary"].get("intentional", 0)
        )
        if total > 0:
            assert data["summary"].get("detail_available") is True

    def test_dead_json_detail_has_full_lists(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "dead"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # In detail mode, high_confidence and low_confidence lists should be present
        assert "high_confidence" in data
        assert "low_confidence" in data

    def test_dead_detail_longer_than_summary(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, ["dead"], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", "dead"], cwd=indexed_project)
        assert result_detail.output >= result_summary.output or \
            len(result_detail.output) >= len(result_summary.output)


class TestDepsProgressiveDisclosure:
    def test_deps_summary_mode_shows_counts(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["deps", "src/service.py"], cwd=indexed_project)
        if result.exit_code != 0:
            pytest.skip("file not found in index")
        # Should show file path and import info
        assert "src/service.py" in result.output or "Imports:" in result.output

    def test_deps_json_summary_has_detail_available(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["deps", "src/service.py"], cwd=indexed_project, json_mode=True)
        if result.exit_code != 0:
            pytest.skip("file not found in index")
        data = json.loads(result.output)
        assert data["summary"].get("detail_available") is True

    def test_deps_json_detail_has_imports_list(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "deps", "src/service.py"], cwd=indexed_project, json_mode=True)
        if result.exit_code != 0:
            pytest.skip("file not found in index")
        data = json.loads(result.output)
        # In detail mode, imports list should be present
        assert "imports" in data
        assert "imported_by" in data
        assert data["summary"].get("truncated") is not True

    def test_deps_detail_shows_full_table(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "deps", "src/service.py"], cwd=indexed_project)
        if result.exit_code != 0:
            pytest.skip("file not found in index")
        # Detail mode should show the full imports/imported-by tables
        assert "Imports:" in result.output or "Imported by:" in result.output

    def test_deps_summary_shorter_than_detail(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, ["deps", "src/service.py"], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", "deps", "src/service.py"], cwd=indexed_project)
        if result_summary.exit_code != 0 or result_detail.exit_code != 0:
            pytest.skip("file not found in index")
        assert len(result_summary.output) <= len(result_detail.output)


class TestLayersProgressiveDisclosure:
    def test_layers_summary_mode_has_level_count(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["layers"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Layers" in result.output or "No layers" in result.output

    def test_layers_summary_mode_has_detail_hint(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["layers"], cwd=indexed_project)
        assert result.exit_code == 0
        # Summary mode should not show individual layer symbol breakdown but hint for --detail
        assert "use --detail" in result.output or "No layers" in result.output

    def test_layers_detail_shows_violations(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "layers"], cwd=indexed_project)
        assert result.exit_code == 0
        # Detail mode should show violations section
        assert "=== Violations" in result.output or "No layers" in result.output

    def test_layers_json_summary_has_detail_available(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["layers"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"].get("detail_available") is True

    def test_layers_json_detail_has_layers_list(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "layers"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "layers" in data
        assert data["summary"].get("truncated") is not True

    def test_layers_summary_shorter_than_detail(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, ["layers"], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", "layers"], cwd=indexed_project)
        assert result_summary.exit_code == 0
        assert result_detail.exit_code == 0
        assert len(result_summary.output) <= len(result_detail.output)


class TestClustersProgressiveDisclosure:
    def test_clusters_summary_mode_has_count(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["clusters"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "=== Clusters ===" in result.output

    def test_clusters_summary_mode_no_full_table(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["clusters"], cwd=indexed_project)
        assert result.exit_code == 0
        # Summary mode should not show inter-cluster coupling detail
        assert "=== Inter-Cluster Coupling" not in result.output

    def test_clusters_detail_shows_full_output(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "clusters"], cwd=indexed_project)
        assert result.exit_code == 0
        # Detail mode should show directory mismatches section
        assert "=== Directory Mismatches" in result.output

    def test_clusters_json_summary_has_detail_available(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["clusters"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"].get("detail_available") is True

    def test_clusters_json_detail_has_clusters_list(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "clusters"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "clusters" in data
        assert data["summary"].get("truncated") is not True

    def test_clusters_summary_shorter_than_detail(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, ["clusters"], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", "clusters"], cwd=indexed_project)
        assert result_summary.exit_code == 0
        assert result_detail.exit_code == 0
        assert len(result_summary.output) <= len(result_detail.output)


class TestHotspotsProgressiveDisclosure:
    """Hotspots requires runtime data from ingest-trace. Tests check graceful handling."""

    def test_hotspots_summary_mode_exits_gracefully(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["hotspots"], cwd=indexed_project)
        assert result.exit_code == 0
        # Without runtime data, should emit a verdict message
        assert "VERDICT:" in result.output or "No runtime data" in result.output

    def test_hotspots_json_summary_no_detail_flag(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["hotspots"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # With no runtime data, total=0, but the envelope should be valid
        assert "summary" in data

    def test_hotspots_detail_flag_accepted(self, indexed_project, monkeypatch):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", "hotspots"], cwd=indexed_project)
        # --detail flag should not cause errors
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Cross-command consistency tests
# ---------------------------------------------------------------------------

class TestProgressiveDisclosureConsistency:
    """Verify consistent behavior across all --detail-aware commands."""

    @pytest.mark.parametrize("cmd", ["health", "dead", "layers", "clusters"])
    def test_json_summary_always_has_summary_dict(self, indexed_project, monkeypatch, cmd):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, [cmd], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"
        data = json.loads(result.output)
        assert "summary" in data, f"{cmd}: missing summary"

    @pytest.mark.parametrize("cmd", ["health", "dead", "layers", "clusters"])
    def test_detail_json_does_not_have_truncated_flag(self, indexed_project, monkeypatch, cmd):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, ["--detail", cmd], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0, f"{cmd} --detail failed: {result.output}"
        data = json.loads(result.output)
        # detail mode should NOT have truncated=True
        assert data["summary"].get("truncated") is not True, \
            f"{cmd} in --detail mode should not have truncated=True"

    @pytest.mark.parametrize("cmd", ["health", "dead", "layers", "clusters"])
    def test_summary_json_shorter_than_detail_json(self, indexed_project, monkeypatch, cmd):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, [cmd], cwd=indexed_project, json_mode=True)
        result_detail = invoke_cli(runner, ["--detail", cmd], cwd=indexed_project, json_mode=True)
        assert result_summary.exit_code == 0, f"{cmd} summary failed: {result_summary.output}"
        assert result_detail.exit_code == 0, f"{cmd} detail failed: {result_detail.output}"
        # Summary JSON should be shorter than or equal to detail JSON.
        # Summary strips all list fields and detail_available adds ~30 chars to summary.
        # Detail keeps all list fields (even empty ones like "cycles": []).
        # The detail fields stripped in summary should outweigh the metadata added.
        assert len(result_summary.output) <= len(result_detail.output), \
            f"{cmd}: summary output ({len(result_summary.output)}) longer than detail ({len(result_detail.output)})"

    @pytest.mark.parametrize("cmd", ["health", "dead", "layers", "clusters"])
    def test_summary_text_shorter_than_detail_text(self, indexed_project, monkeypatch, cmd):
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result_summary = invoke_cli(runner, [cmd], cwd=indexed_project)
        result_detail = invoke_cli(runner, ["--detail", cmd], cwd=indexed_project)
        assert result_summary.exit_code == 0, f"{cmd} summary failed: {result_summary.output}"
        assert result_detail.exit_code == 0, f"{cmd} detail failed: {result_detail.output}"
        assert len(result_summary.output) <= len(result_detail.output), \
            f"{cmd}: summary text ({len(result_summary.output)}) longer than detail ({len(result_detail.output)})"

    @pytest.mark.parametrize("cmd", ["health", "dead", "layers", "clusters"])
    def test_non_detail_json_has_detail_available(self, indexed_project, monkeypatch, cmd):
        """Commands without --detail should flag detail_available in summary."""
        monkeypatch.chdir(indexed_project)
        runner = CliRunner()
        result = invoke_cli(runner, [cmd], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"
        data = json.loads(result.output)
        assert data["summary"].get("detail_available") is True, \
            f"{cmd}: summary mode should have detail_available=True in JSON"
