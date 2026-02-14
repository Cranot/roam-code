"""Tests for the dead code aging, decay, and effort estimation features.

Covers:
- Unit tests for _decay_score(), _estimate_removal_minutes(), _decay_tier()
- CLI tests for --aging, --effort, --decay, --sort-by-age, --sort-by-effort,
  --sort-by-decay flags
- JSON output tests for extended dead code data
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output

from roam.commands.cmd_dead import _decay_score, _estimate_removal_minutes, _decay_tier


# ============================================================================
# Unit tests for internal functions
# ============================================================================


class TestDecayScore:
    """Tests for _decay_score() internal function."""

    def test_fresh_code_low_score(self):
        """Fresh code with no complexity, single cluster, active author => low score."""
        score = _decay_score(
            age_days=0,
            cognitive_complexity=0,
            cluster_size=1,
            importing_files=0,
            author_active=True,
            dead_loc=5,
        )
        assert score >= 0
        assert score <= 25, f"Fresh code should have low decay score, got {score}"

    def test_old_complex_code_high_score(self):
        """Year-old code with high complexity, large cluster, inactive author => high score."""
        score = _decay_score(
            age_days=365,
            cognitive_complexity=20,
            cluster_size=5,
            importing_files=3,
            author_active=False,
            dead_loc=100,
        )
        assert score >= 50, f"Old complex code should have high decay score, got {score}"

    def test_score_bounded_0_to_100(self):
        """Score is always in [0, 100] even with extreme inputs."""
        low = _decay_score(0, 0, 1, 0, True, 1)
        assert 0 <= low <= 100

        high = _decay_score(10000, 100, 50, 100, False, 10000)
        assert 0 <= high <= 100

    def test_inactive_author_adds_points(self):
        """Inactive author should increase the decay score vs. active author."""
        score_active = _decay_score(
            age_days=100, cognitive_complexity=5, cluster_size=1,
            importing_files=0, author_active=True, dead_loc=10,
        )
        score_inactive = _decay_score(
            age_days=100, cognitive_complexity=5, cluster_size=1,
            importing_files=0, author_active=False, dead_loc=10,
        )
        assert score_inactive > score_active

    def test_higher_complexity_increases_score(self):
        """Higher cognitive complexity should increase the decay score."""
        score_low_cc = _decay_score(
            age_days=100, cognitive_complexity=0, cluster_size=1,
            importing_files=0, author_active=True, dead_loc=10,
        )
        score_high_cc = _decay_score(
            age_days=100, cognitive_complexity=20, cluster_size=1,
            importing_files=0, author_active=True, dead_loc=10,
        )
        assert score_high_cc > score_low_cc

    def test_older_code_increases_score(self):
        """Older code should have a higher decay score than newer code."""
        score_new = _decay_score(
            age_days=1, cognitive_complexity=5, cluster_size=1,
            importing_files=0, author_active=True, dead_loc=10,
        )
        score_old = _decay_score(
            age_days=1000, cognitive_complexity=5, cluster_size=1,
            importing_files=0, author_active=True, dead_loc=10,
        )
        assert score_old > score_new

    def test_score_is_integer(self):
        """_decay_score should always return an integer."""
        score = _decay_score(180, 10, 3, 2, False, 50)
        assert isinstance(score, int)


class TestDecayTier:
    """Tests for _decay_tier() internal function."""

    def test_fresh_tier(self):
        assert _decay_tier(0) == "Fresh"
        assert _decay_tier(10) == "Fresh"
        assert _decay_tier(25) == "Fresh"

    def test_stale_tier(self):
        assert _decay_tier(26) == "Stale"
        assert _decay_tier(30) == "Stale"
        assert _decay_tier(50) == "Stale"

    def test_decayed_tier(self):
        assert _decay_tier(51) == "Decayed"
        assert _decay_tier(60) == "Decayed"
        assert _decay_tier(75) == "Decayed"

    def test_fossilized_tier(self):
        assert _decay_tier(76) == "Fossilized"
        assert _decay_tier(80) == "Fossilized"
        assert _decay_tier(100) == "Fossilized"

    def test_tier_boundary_values(self):
        """Boundary values at tier transitions."""
        assert _decay_tier(25) == "Fresh"
        assert _decay_tier(26) == "Stale"
        assert _decay_tier(50) == "Stale"
        assert _decay_tier(51) == "Decayed"
        assert _decay_tier(75) == "Decayed"
        assert _decay_tier(76) == "Fossilized"


class TestEstimateRemovalMinutes:
    """Tests for _estimate_removal_minutes() internal function."""

    def test_returns_positive(self):
        """Removal estimate should always be positive."""
        result = _estimate_removal_minutes(
            dead_loc=5, cognitive_complexity=0, importing_files=0,
            cluster_size=1, age_years=0, author_active=True,
        )
        assert result > 0

    def test_increases_with_complexity(self):
        """Higher cognitive complexity should increase removal time."""
        low = _estimate_removal_minutes(
            dead_loc=10, cognitive_complexity=0, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        high = _estimate_removal_minutes(
            dead_loc=10, cognitive_complexity=20, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        assert high > low

    def test_increases_with_loc(self):
        """More lines of dead code should increase removal time."""
        small = _estimate_removal_minutes(
            dead_loc=5, cognitive_complexity=5, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        large = _estimate_removal_minutes(
            dead_loc=100, cognitive_complexity=5, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        assert large > small

    def test_active_author_reduces_effort(self):
        """Active author should reduce the removal effort."""
        active = _estimate_removal_minutes(
            dead_loc=20, cognitive_complexity=5, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        inactive = _estimate_removal_minutes(
            dead_loc=20, cognitive_complexity=5, importing_files=0,
            cluster_size=1, age_years=1, author_active=False,
        )
        assert active < inactive

    def test_increases_with_importing_files(self):
        """More importing files should increase removal time."""
        isolated = _estimate_removal_minutes(
            dead_loc=20, cognitive_complexity=5, importing_files=0,
            cluster_size=1, age_years=1, author_active=True,
        )
        coupled = _estimate_removal_minutes(
            dead_loc=20, cognitive_complexity=5, importing_files=10,
            cluster_size=1, age_years=1, author_active=True,
        )
        assert coupled > isolated

    def test_returns_float(self):
        """_estimate_removal_minutes should return a numeric value."""
        result = _estimate_removal_minutes(
            dead_loc=10, cognitive_complexity=5, importing_files=2,
            cluster_size=3, age_years=2, author_active=False,
        )
        assert isinstance(result, (int, float))


# ============================================================================
# CLI tests
# ============================================================================


class TestDeadAgingCLI:
    """CLI integration tests for dead --aging, --effort, --decay flags."""

    def test_dead_aging_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --aging exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--aging"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_effort_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --effort exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--effort"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_decay_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --decay exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--decay"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_sort_by_age_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --sort-by-age exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--sort-by-age"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_sort_by_effort_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --sort-by-effort exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--sort-by-effort"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_sort_by_decay_runs(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --sort-by-decay exits 0."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--sort-by-decay"], cwd=indexed_project)
        assert result.exit_code == 0

    def test_dead_aging_text_shows_age_columns(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --aging --all text output should include age-related headers."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--aging", "--all"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        # The table headers should include Age(d) or LastMod(d) or Author
        # (unless there are no dead symbols, which is also valid)
        if "Unreferenced" in out and "none" not in out.lower():
            assert "Age" in out or "age" in out.lower() or "Author" in out

    def test_dead_decay_text_shows_decay_info(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --decay text output should include decay info."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["dead", "--decay", "--all"], cwd=indexed_project)
        assert result.exit_code == 0
        out = result.output
        if "Unreferenced" in out and "none" not in out.lower():
            # Should show decay distribution or tier
            assert ("Decay" in out or "decay" in out.lower()
                    or "Fresh" in out or "Stale" in out
                    or "Decayed" in out or "Fossilized" in out)

    def test_dead_combined_flags(self, cli_runner, indexed_project, monkeypatch):
        """roam dead --aging --effort --decay all combined should work."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["dead", "--aging", "--effort", "--decay", "--all"],
            cwd=indexed_project,
        )
        assert result.exit_code == 0


class TestDeadAgingJSON:
    """JSON output tests for dead code aging features."""

    def test_json_dead_aging_includes_aging_data(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --aging includes aging data in symbol dicts."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--aging"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --aging")
        # Check the envelope
        assert "summary" in data
        # If there are dead symbols, they should have aging fields
        all_syms = data.get("high_confidence", []) + data.get("low_confidence", [])
        if all_syms:
            sym = all_syms[0]
            assert "aging" in sym, f"Symbol missing 'aging' key: {list(sym.keys())}"
            aging = sym["aging"]
            assert "age_days" in aging
            assert "dead_loc" in aging
            assert "author_active" in aging

    def test_json_dead_decay_includes_distribution(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --decay includes decay_distribution in summary."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--decay"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --decay")
        summary = data.get("summary", {})
        assert "decay_distribution" in summary, (
            f"Missing 'decay_distribution' in summary: {list(summary.keys())}"
        )
        dist = summary["decay_distribution"]
        for tier in ["fresh", "stale", "decayed", "fossilized"]:
            assert tier in dist, f"Missing '{tier}' in decay_distribution"

    def test_json_dead_effort_includes_total_hours(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --effort includes total_effort_hours in summary."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--effort"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --effort")
        summary = data.get("summary", {})
        assert "total_effort_hours" in summary, (
            f"Missing 'total_effort_hours' in summary: {list(summary.keys())}"
        )
        assert isinstance(summary["total_effort_hours"], (int, float))

    def test_json_dead_effort_symbols_have_effort_data(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --effort individual symbols include effort fields."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--effort"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --effort")
        all_syms = data.get("high_confidence", []) + data.get("low_confidence", [])
        if all_syms:
            sym = all_syms[0]
            assert "effort" in sym, f"Symbol missing 'effort' key: {list(sym.keys())}"
            effort = sym["effort"]
            assert "removal_minutes" in effort
            assert effort["removal_minutes"] >= 0

    def test_json_dead_decay_symbols_have_decay_score(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --decay individual symbols include decay_score."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--decay"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --decay")
        all_syms = data.get("high_confidence", []) + data.get("low_confidence", [])
        if all_syms:
            sym = all_syms[0]
            assert "decay_score" in sym, f"Symbol missing 'decay_score': {list(sym.keys())}"
            assert 0 <= sym["decay_score"] <= 100

    def test_json_dead_summary_has_median_age(self, cli_runner, indexed_project, monkeypatch):
        """roam --json dead --aging summary includes median_age_days."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["dead", "--aging"], cwd=indexed_project, json_mode=True,
        )
        data = parse_json_output(result, "dead --aging")
        summary = data.get("summary", {})
        assert "median_age_days" in summary, (
            f"Missing 'median_age_days' in summary: {list(summary.keys())}"
        )
        assert isinstance(summary["median_age_days"], (int, float))
        assert summary["median_age_days"] >= 0
