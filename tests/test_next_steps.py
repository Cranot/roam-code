"""Tests for next-step suggestions in key roam commands (#45).

Verifies:
- suggest_next_steps() is context-aware (score-based branching)
- Max 3 suggestions per command
- Each command produces next_steps in JSON output
- Text output includes "NEXT STEPS:" section
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Unit tests for the helper itself
# ---------------------------------------------------------------------------

class TestSuggestNextSteps:
    """Unit tests for suggest_next_steps()."""

    def test_health_low_score_gets_hotspots(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("health", {"score": 45, "critical_issues": 2, "cycles": 1})
        texts = "\n".join(steps)
        assert "hotspots" in texts

    def test_health_very_low_score_gets_vibe_check(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("health", {"score": 30, "critical_issues": 3, "cycles": 2})
        texts = "\n".join(steps)
        assert "vibe-check" in texts

    def test_health_critical_issues_gets_debt(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("health", {"score": 65, "critical_issues": 5, "cycles": 0})
        texts = "\n".join(steps)
        assert "debt" in texts

    def test_health_high_score_gets_trends(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("health", {"score": 92, "critical_issues": 0, "cycles": 0})
        texts = "\n".join(steps)
        assert "trends" in texts

    def test_health_max_3_suggestions(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("health", {"score": 10, "critical_issues": 10, "cycles": 5})
        assert len(steps) <= 3

    def test_context_gets_preflight(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("context", {"symbol": "my_func", "callers": 5})
        texts = "\n".join(steps)
        assert "preflight" in texts

    def test_context_with_many_callers_gets_impact(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("context", {
            "symbol": "core_service",
            "callers": 25,
            "blast_radius_symbols": 10,
        })
        texts = "\n".join(steps)
        assert "impact" in texts

    def test_context_no_callers_gets_dead(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("context", {"symbol": "orphan_fn", "callers": 0})
        texts = "\n".join(steps)
        assert "dead" in texts

    def test_context_max_3_suggestions(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("context", {"symbol": "x", "callers": 100, "blast_radius_symbols": 50})
        assert len(steps) <= 3

    def test_hotspots_no_data_suggests_ingest(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("hotspots", {"total": 0, "upgrades": 0})
        texts = "\n".join(steps)
        assert "ingest-trace" in texts

    def test_hotspots_with_upgrades_suggests_impact(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("hotspots", {"total": 5, "upgrades": 3})
        texts = "\n".join(steps)
        assert "impact" in texts

    def test_hotspots_max_3_suggestions(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("hotspots", {"total": 10, "upgrades": 5})
        assert len(steps) <= 3

    def test_diagnose_gets_trace(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("diagnose", {"symbol": "buggy_fn", "top_suspect": "caller_fn"})
        texts = "\n".join(steps)
        assert "trace" in texts

    def test_diagnose_with_suspect_gets_impact(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("diagnose", {"symbol": "buggy_fn", "top_suspect": "caller_fn"})
        texts = "\n".join(steps)
        assert "impact" in texts

    def test_diagnose_max_3_suggestions(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("diagnose", {"symbol": "x", "top_suspect": "y"})
        assert len(steps) <= 3

    def test_dead_safe_count_gets_safe_delete(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("dead", {"safe": 10, "review": 2})
        texts = "\n".join(steps)
        assert "safe-delete" in texts

    def test_dead_review_count_gets_by_directory(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("dead", {"safe": 0, "review": 5})
        texts = "\n".join(steps)
        assert "by-directory" in texts

    def test_dead_max_3_suggestions(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("dead", {"safe": 50, "review": 20})
        assert len(steps) <= 3

    def test_unknown_command_returns_empty(self):
        from roam.commands.next_steps import suggest_next_steps
        steps = suggest_next_steps("nonexistent-command", {})
        assert steps == []

    def test_empty_context_does_not_crash(self):
        from roam.commands.next_steps import suggest_next_steps
        # All commands should handle missing context keys gracefully
        for cmd in ("health", "context", "hotspots", "diagnose", "dead"):
            steps = suggest_next_steps(cmd, {})
            assert isinstance(steps, list)
            assert len(steps) <= 3


class TestFormatNextStepsText:
    """Unit tests for format_next_steps_text()."""

    def test_empty_list_returns_empty_string(self):
        from roam.commands.next_steps import format_next_steps_text
        assert format_next_steps_text([]) == ""

    def test_non_empty_includes_header(self):
        from roam.commands.next_steps import format_next_steps_text
        result = format_next_steps_text(["Run `roam health`"])
        assert "NEXT STEPS:" in result

    def test_numbered_items(self):
        from roam.commands.next_steps import format_next_steps_text
        steps = ["Run `roam health`", "Run `roam hotspots`"]
        result = format_next_steps_text(steps)
        assert "1." in result
        assert "2." in result

    def test_each_step_appears_in_output(self):
        from roam.commands.next_steps import format_next_steps_text
        steps = ["Run `roam health`", "Run `roam vibe-check`"]
        result = format_next_steps_text(steps)
        assert "roam health" in result
        assert "roam vibe-check" in result


# ---------------------------------------------------------------------------
# Integration tests against a real indexed project
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ns_project(tmp_path_factory):
    """Small indexed project for next-steps integration tests."""
    import subprocess
    import sys

    proj = tmp_path_factory.mktemp("ns_project")
    (proj / ".gitignore").write_text(".roam/\n")

    # A few Python files so the indexer has something to chew on
    (proj / "app.py").write_text(
        "from service import process\n"
        "\n"
        "def main():\n"
        "    return process('hello')\n"
    )
    (proj / "service.py").write_text(
        "def process(data):\n"
        "    return transform(data)\n"
        "\n"
        "def transform(data):\n"
        "    return data.upper()\n"
        "\n"
        "def unused_func():\n"
        "    return 'never called'\n"
    )
    (proj / "utils.py").write_text(
        "def helper():\n"
        "    pass\n"
    )

    # Init git
    subprocess.run(["git", "init"], cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=proj, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=proj, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=proj, capture_output=True)

    # Index the project
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = subprocess.run(
            [sys.executable, "-m", "roam", "index"],
            cwd=str(proj),
            capture_output=True,
            text=True,
        )
    finally:
        os.chdir(old_cwd)

    return proj


def _invoke(proj, *args):
    """Helper: invoke roam CLI in-process with cwd set to proj."""
    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, list(args), catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


class TestHealthNextSteps:
    """Integration tests: roam health produces next_steps."""

    def test_json_has_next_steps_key(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "health")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "next_steps" in data, f"'next_steps' missing from health JSON output"

    def test_json_next_steps_is_list(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "health")
        data = json.loads(result.output)
        assert isinstance(data["next_steps"], list)

    def test_json_next_steps_max_3(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "health")
        data = json.loads(result.output)
        assert len(data["next_steps"]) <= 3

    def test_json_next_steps_non_empty(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "health")
        data = json.loads(result.output)
        assert len(data["next_steps"]) >= 1

    def test_text_includes_next_steps_header(self, ns_project):
        result = _invoke(ns_project, "--detail", "health")
        assert result.exit_code == 0
        assert "NEXT STEPS:" in result.output

    def test_text_next_steps_are_numbered(self, ns_project):
        result = _invoke(ns_project, "--detail", "health")
        assert "1." in result.output


class TestContextNextSteps:
    """Integration tests: roam context produces next_steps."""

    def test_json_has_next_steps_key(self, ns_project):
        result = _invoke(ns_project, "--json", "context", "process")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "next_steps" in data

    def test_json_next_steps_is_list(self, ns_project):
        result = _invoke(ns_project, "--json", "context", "process")
        data = json.loads(result.output)
        assert isinstance(data["next_steps"], list)

    def test_json_next_steps_max_3(self, ns_project):
        result = _invoke(ns_project, "--json", "context", "process")
        data = json.loads(result.output)
        assert len(data["next_steps"]) <= 3

    def test_json_next_steps_mention_preflight(self, ns_project):
        result = _invoke(ns_project, "--json", "context", "process")
        data = json.loads(result.output)
        all_text = " ".join(data["next_steps"])
        assert "preflight" in all_text

    def test_text_includes_next_steps_header(self, ns_project):
        result = _invoke(ns_project, "context", "process")
        assert result.exit_code == 0
        assert "NEXT STEPS:" in result.output

    def test_orphan_symbol_suggests_dead(self, ns_project):
        """A symbol with no callers should suggest roam dead."""
        result = _invoke(ns_project, "--json", "context", "unused_func")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        all_text = " ".join(data["next_steps"])
        assert "dead" in all_text


class TestHotspotsNextSteps:
    """Integration tests: roam hotspots produces next_steps."""

    def test_json_has_next_steps_key(self, ns_project):
        # Hotspots requires runtime data; with no data it should still produce next_steps
        result = _invoke(ns_project, "--json", "--detail", "hotspots")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "next_steps" in data

    def test_json_next_steps_is_list(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "hotspots")
        data = json.loads(result.output)
        assert isinstance(data["next_steps"], list)

    def test_json_next_steps_max_3(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "hotspots")
        data = json.loads(result.output)
        assert len(data["next_steps"]) <= 3

    def test_no_runtime_data_suggests_ingest_trace(self, ns_project):
        """Without runtime data the suggestion should mention ingest-trace."""
        result = _invoke(ns_project, "--json", "--detail", "hotspots")
        data = json.loads(result.output)
        all_text = " ".join(data["next_steps"])
        assert "ingest-trace" in all_text

    def test_text_includes_next_steps_header(self, ns_project):
        result = _invoke(ns_project, "--detail", "hotspots")
        assert result.exit_code == 0
        assert "NEXT STEPS:" in result.output


class TestDiagnoseNextSteps:
    """Integration tests: roam diagnose produces next_steps."""

    def test_json_has_next_steps_key(self, ns_project):
        result = _invoke(ns_project, "--json", "diagnose", "process")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "next_steps" in data

    def test_json_next_steps_is_list(self, ns_project):
        result = _invoke(ns_project, "--json", "diagnose", "process")
        data = json.loads(result.output)
        assert isinstance(data["next_steps"], list)

    def test_json_next_steps_max_3(self, ns_project):
        result = _invoke(ns_project, "--json", "diagnose", "process")
        data = json.loads(result.output)
        assert len(data["next_steps"]) <= 3

    def test_json_next_steps_mention_trace(self, ns_project):
        result = _invoke(ns_project, "--json", "diagnose", "process")
        data = json.loads(result.output)
        all_text = " ".join(data["next_steps"])
        assert "trace" in all_text

    def test_text_includes_next_steps_header(self, ns_project):
        result = _invoke(ns_project, "diagnose", "process")
        assert result.exit_code == 0
        assert "NEXT STEPS:" in result.output


class TestDeadNextSteps:
    """Integration tests: roam dead produces next_steps."""

    def test_json_has_next_steps_key(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "dead")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "next_steps" in data

    def test_json_next_steps_is_list(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "dead")
        data = json.loads(result.output)
        assert isinstance(data["next_steps"], list)

    def test_json_next_steps_max_3(self, ns_project):
        result = _invoke(ns_project, "--json", "--detail", "dead")
        data = json.loads(result.output)
        assert len(data["next_steps"]) <= 3

    def test_json_next_steps_mention_safe_delete(self, ns_project):
        """Project has unused_func which should be dead, suggesting safe-delete."""
        result = _invoke(ns_project, "--json", "--detail", "dead")
        data = json.loads(result.output)
        # Only check if there are safe items
        safe_count = data.get("summary", {}).get("safe", 0)
        if safe_count > 0:
            all_text = " ".join(data["next_steps"])
            assert "safe-delete" in all_text

    def test_text_includes_next_steps_header(self, ns_project):
        result = _invoke(ns_project, "--detail", "dead")
        assert result.exit_code == 0
        assert "NEXT STEPS:" in result.output


# ---------------------------------------------------------------------------
# Contextual differentiation tests
# ---------------------------------------------------------------------------

class TestContextualDifferentiation:
    """Verify that different context values produce different suggestions."""

    def test_health_low_vs_high_score_differ(self):
        from roam.commands.next_steps import suggest_next_steps
        low = suggest_next_steps("health", {"score": 20, "critical_issues": 5, "cycles": 3})
        high = suggest_next_steps("health", {"score": 95, "critical_issues": 0, "cycles": 0})
        # Low score should suggest vibe-check, high score should suggest trends
        low_text = " ".join(low)
        high_text = " ".join(high)
        assert "vibe-check" in low_text
        assert "trends" in high_text
        # They should not be identical
        assert low != high

    def test_context_callers_zero_vs_many_differ(self):
        from roam.commands.next_steps import suggest_next_steps
        orphan = suggest_next_steps("context", {"symbol": "x", "callers": 0})
        popular = suggest_next_steps("context", {
            "symbol": "x", "callers": 30, "blast_radius_symbols": 20,
        })
        orphan_text = " ".join(orphan)
        popular_text = " ".join(popular)
        assert "dead" in orphan_text
        assert "impact" in popular_text

    def test_hotspots_zero_vs_nonzero_total_differ(self):
        from roam.commands.next_steps import suggest_next_steps
        no_data = suggest_next_steps("hotspots", {"total": 0, "upgrades": 0})
        has_data = suggest_next_steps("hotspots", {"total": 10, "upgrades": 5})
        no_data_text = " ".join(no_data)
        has_data_text = " ".join(has_data)
        assert "ingest-trace" in no_data_text
        assert "ingest-trace" not in has_data_text
