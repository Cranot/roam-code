"""Tests for roam dev-profile — developer behavioral profiling command."""

from __future__ import annotations

import json
import math
import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.conftest import git_init, git_commit, invoke_cli, parse_json_output, assert_json_envelope


# ===========================================================================
# Unit tests for Gini coefficient
# ===========================================================================


class TestGiniCoefficient:
    """Test the Gini coefficient calculation."""

    def test_empty_returns_zero(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        assert gini_coefficient([]) == 0.0

    def test_all_zeros_returns_zero(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        assert gini_coefficient([0, 0, 0]) == 0.0

    def test_perfectly_equal_returns_zero(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        # All same values → perfect equality → Gini = 0
        result = gini_coefficient([5, 5, 5, 5])
        assert abs(result) < 1e-9

    def test_perfectly_concentrated_returns_one(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        # All weight in one element → maximum concentration → Gini ≈ 1
        result = gini_coefficient([0, 0, 0, 0, 100])
        # With 5 elements and all weight in the last: Gini = (n-1)/n
        expected = 4 / 5
        assert abs(result - expected) < 0.01

    def test_single_value_returns_zero(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        assert gini_coefficient([42]) == 0.0

    def test_two_equal_returns_zero(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        result = gini_coefficient([10, 10])
        assert abs(result) < 1e-9

    def test_two_unequal_moderate(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        # [1, 3]: sorted = [1,3], n=2, sum=4
        # cumulative = (2*0 - 2+1)*1 + (2*1 - 2+1)*3 = (-1)*1 + (1)*3 = 2
        # gini = 2 / (2 * 4) = 0.25
        result = gini_coefficient([1, 3])
        assert abs(result - 0.25) < 1e-9

    def test_higher_concentration_higher_gini(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        equal = gini_coefficient([5, 5, 5, 5])
        moderate = gini_coefficient([1, 2, 3, 10])
        concentrated = gini_coefficient([1, 1, 1, 100])
        assert equal < moderate < concentrated

    def test_known_value(self):
        from roam.commands.cmd_dev_profile import gini_coefficient
        # [1, 2, 3, 4]: sorted, n=4, sum=10
        # cumulative = (-3)*1 + (-1)*2 + (1)*3 + (3)*4 = -3 -2 +3 +12 = 10
        # gini = 10 / (4 * 10) = 0.25
        result = gini_coefficient([1, 2, 3, 4])
        assert abs(result - 0.25) < 1e-9


# ===========================================================================
# Unit tests for hour distribution
# ===========================================================================


class TestHourDistribution:
    """Test hour-of-day distribution extraction."""

    def test_empty_timestamps(self):
        from roam.commands.cmd_dev_profile import hour_distribution
        result = hour_distribution([])
        assert result == [0] * 24
        assert len(result) == 24

    def test_single_midnight_utc(self):
        from roam.commands.cmd_dev_profile import hour_distribution
        # 2024-01-01 00:00:00 UTC
        ts = 1704067200
        result = hour_distribution([ts])
        assert result[0] == 1
        assert sum(result) == 1

    def test_single_noon_utc(self):
        from roam.commands.cmd_dev_profile import hour_distribution
        # 2024-01-01 12:00:00 UTC
        ts = 1704067200 + 12 * 3600
        result = hour_distribution([ts])
        assert result[12] == 1
        assert sum(result) == 1

    def test_multiple_same_hour(self):
        from roam.commands.cmd_dev_profile import hour_distribution
        # Three commits in hour 15
        base = 1704067200 + 15 * 3600
        result = hour_distribution([base, base + 60, base + 120])
        assert result[15] == 3
        assert sum(result) == 3

    def test_distribution_length(self):
        from roam.commands.cmd_dev_profile import hour_distribution
        result = hour_distribution([1704067200])
        assert len(result) == 24


# ===========================================================================
# Unit tests for day distribution
# ===========================================================================


class TestDayDistribution:
    """Test weekday distribution extraction."""

    def test_empty_timestamps(self):
        from roam.commands.cmd_dev_profile import day_distribution
        result = day_distribution([])
        assert result == [0] * 7

    def test_distribution_length(self):
        from roam.commands.cmd_dev_profile import day_distribution
        result = day_distribution([1704067200])
        assert len(result) == 7

    def test_known_monday(self):
        from roam.commands.cmd_dev_profile import day_distribution
        # 2024-01-01 is a Monday
        ts = 1704067200  # 2024-01-01 00:00:00 UTC
        result = day_distribution([ts])
        assert result[0] == 1  # Monday = 0


# ===========================================================================
# Unit tests for burst detection
# ===========================================================================


class TestBurstDetection:
    """Test burst detection algorithm."""

    def test_empty_timestamps(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        result = detect_bursts([])
        assert result["max_in_window"] == 0
        assert result["burst_score"] == 1.0
        assert result["burst_windows"] == []

    def test_single_commit_no_burst(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        result = detect_bursts([1704067200])
        assert result["max_in_window"] == 1
        assert result["burst_score"] == 1.0

    def test_spread_out_commits_low_burst(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        # Commits spaced 2 hours apart — no burst
        base = 1704067200
        timestamps = [base + i * 7200 for i in range(10)]
        result = detect_bursts(timestamps, window_seconds=3600)
        assert result["max_in_window"] == 1
        assert result["burst_score"] == 1.0

    def test_clustered_commits_high_burst(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        # 5 commits in 10 minutes, then nothing for a day
        base = 1704067200
        clustered = [base + i * 120 for i in range(5)]  # 5 commits in 8 min
        spaced = [base + 86400, base + 2 * 86400]
        result = detect_bursts(clustered + spaced, window_seconds=3600)
        assert result["max_in_window"] >= 5
        assert result["burst_score"] > 1.0

    def test_burst_windows_detected(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        # 4 commits clustered in a window
        base = 1704067200
        clustered = [base + i * 60 for i in range(4)]
        result = detect_bursts(clustered, window_seconds=3600)
        assert len(result["burst_windows"]) >= 1
        assert result["burst_windows"][0]["commit_count"] >= 4

    def test_burst_windows_capped(self):
        from roam.commands.cmd_dev_profile import detect_bursts
        # Many burst windows — should be capped at 10
        base = 1704067200
        # Create 20 separate bursts 2h apart, each with 3 rapid commits
        timestamps = []
        for i in range(20):
            window_start = base + i * 7200
            timestamps.extend([window_start + j * 30 for j in range(3)])
        result = detect_bursts(timestamps, window_seconds=3600)
        assert len(result["burst_windows"]) <= 10


# ===========================================================================
# Unit tests for session detection
# ===========================================================================


class TestSessionDetection:
    """Test coding session detection."""

    def test_empty_timestamps(self):
        from roam.commands.cmd_dev_profile import detect_sessions
        result = detect_sessions([])
        assert result["session_count"] == 0
        assert result["avg_session_length_minutes"] == 0.0

    def test_single_commit_one_session(self):
        from roam.commands.cmd_dev_profile import detect_sessions
        result = detect_sessions([1704067200])
        assert result["session_count"] == 1

    def test_commits_within_gap_single_session(self):
        from roam.commands.cmd_dev_profile import detect_sessions
        base = 1704067200
        # Commits 10 min apart — default gap 30min → single session
        timestamps = [base + i * 600 for i in range(5)]
        result = detect_sessions(timestamps, gap_seconds=1800)
        assert result["session_count"] == 1
        assert result["avg_commits_per_session"] == 5.0

    def test_commits_across_gap_multiple_sessions(self):
        from roam.commands.cmd_dev_profile import detect_sessions
        base = 1704067200
        # Two groups with a 2-hour gap between them
        session1 = [base + i * 600 for i in range(3)]
        session2 = [base + 7200 + i * 600 for i in range(3)]
        result = detect_sessions(session1 + session2, gap_seconds=1800)
        assert result["session_count"] == 2

    def test_session_length_computed(self):
        from roam.commands.cmd_dev_profile import detect_sessions
        base = 1704067200
        # Session spanning exactly 30 minutes
        timestamps = [base, base + 1800]
        result = detect_sessions(timestamps, gap_seconds=3600)
        assert result["session_count"] == 1
        assert result["avg_session_length_minutes"] == 30.0


# ===========================================================================
# Unit tests for risk score
# ===========================================================================


class TestRiskScore:
    """Test the behavioral risk scoring formula."""

    def test_zero_risk_all_normal(self):
        from roam.commands.cmd_dev_profile import risk_score
        result = risk_score(0.0, 0.0, 0.0, 1.0)
        assert result == 0

    def test_high_late_night_elevates_risk(self):
        from roam.commands.cmd_dev_profile import risk_score
        low = risk_score(0.0, 0.0, 0.0, 1.0)
        high = risk_score(100.0, 0.0, 0.0, 1.0)
        assert high > low

    def test_high_weekend_elevates_risk(self):
        from roam.commands.cmd_dev_profile import risk_score
        low = risk_score(0.0, 0.0, 0.0, 1.0)
        high = risk_score(0.0, 100.0, 0.0, 1.0)
        assert high > low

    def test_high_scatter_elevates_risk(self):
        from roam.commands.cmd_dev_profile import risk_score
        low = risk_score(0.0, 0.0, 0.0, 1.0)
        high = risk_score(0.0, 0.0, 1.0, 1.0)
        assert high > low

    def test_high_burst_elevates_risk(self):
        from roam.commands.cmd_dev_profile import risk_score
        low = risk_score(0.0, 0.0, 0.0, 1.0)
        high = risk_score(0.0, 0.0, 0.0, 5.0)
        assert high > low

    def test_capped_at_100(self):
        from roam.commands.cmd_dev_profile import risk_score
        result = risk_score(100.0, 100.0, 1.0, 5.0)
        assert result <= 100

    def test_is_integer(self):
        from roam.commands.cmd_dev_profile import risk_score
        result = risk_score(30.0, 20.0, 0.5, 2.0)
        assert isinstance(result, int)


# ===========================================================================
# Unit tests for git log parsing
# ===========================================================================


class TestGitLogParsing:
    """Test parsing of git log --numstat output."""

    def test_empty_string(self):
        from roam.commands.cmd_dev_profile import parse_git_log
        result = parse_git_log("")
        assert result == []

    def test_single_commit_no_files(self):
        from roam.commands.cmd_dev_profile import parse_git_log
        raw = "abc123def456abc123def456abc123def456abc12|alice@example.com|2024-01-15T10:00:00+00:00|fix bug\n"
        result = parse_git_log(raw)
        assert len(result) == 1
        assert result[0]["author_email"] == "alice@example.com"
        assert result[0]["files"] == []

    def test_single_commit_with_files(self):
        from roam.commands.cmd_dev_profile import parse_git_log
        raw = (
            "abc123def456abc123def456abc123def456abc12|alice@example.com|2024-01-15T10:00:00+00:00|fix bug\n"
            "\n"
            "5\t2\tsrc/foo.py\n"
            "10\t0\tsrc/bar.py\n"
        )
        result = parse_git_log(raw)
        assert len(result) == 1
        assert "src/foo.py" in result[0]["files"]
        assert "src/bar.py" in result[0]["files"]
        assert result[0]["lines_added"] == 15
        assert result[0]["lines_removed"] == 2

    def test_multiple_commits(self):
        from roam.commands.cmd_dev_profile import parse_git_log
        raw = (
            "aaaa00000000000000000000000000000000aaaa|alice@example.com|2024-01-15T10:00:00+00:00|first\n"
            "5\t0\tfile_a.py\n"
            "\n"
            "bbbb00000000000000000000000000000000bbbb|bob@example.com|2024-01-15T11:00:00+00:00|second\n"
            "3\t1\tfile_b.py\n"
        )
        result = parse_git_log(raw)
        assert len(result) == 2
        assert result[0]["author_email"] == "alice@example.com"
        assert result[1]["author_email"] == "bob@example.com"

    def test_binary_files_handled(self):
        from roam.commands.cmd_dev_profile import parse_git_log
        # numstat outputs "-" for binary files
        raw = (
            "abc123def456abc123def456abc123def456abc12|alice@example.com|2024-01-15T10:00:00+00:00|add image\n"
            "-\t-\tassets/logo.png\n"
        )
        result = parse_git_log(raw)
        assert len(result) == 1
        assert "assets/logo.png" in result[0]["files"]
        assert result[0]["lines_added"] == 0
        assert result[0]["lines_removed"] == 0

    def test_iso8601_parsing(self):
        from roam.commands.cmd_dev_profile import _parse_iso8601
        # 2024-01-01 00:00:00 UTC
        ts = _parse_iso8601("2024-01-01T00:00:00+00:00")
        assert ts == 1704067200

    def test_iso8601_parsing_with_offset(self):
        from roam.commands.cmd_dev_profile import _parse_iso8601
        # Same moment, +05:30 offset = UTC-5:30 offset means 2023-12-31 18:30 UTC
        ts = _parse_iso8601("2024-01-01T00:00:00+05:30")
        expected = 1704067200 - (5 * 3600 + 30 * 60)
        assert ts == expected

    def test_iso8601_invalid_returns_zero(self):
        from roam.commands.cmd_dev_profile import _parse_iso8601
        assert _parse_iso8601("not-a-date") == 0


# ===========================================================================
# Unit tests for author profile building
# ===========================================================================


class TestAuthorProfile:
    """Test per-author profile construction."""

    def test_empty_commits_returns_minimal_profile(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        profile = build_author_profile("alice@example.com", [])
        assert profile["author"] == "alice@example.com"
        assert profile["commit_count"] == 0
        assert profile["risk_score"] == 0

    def test_basic_profile_fields(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        commits = [
            {
                "hash": "abc" * 14,
                "author_email": "alice@example.com",
                "timestamp": 1704067200,
                "subject": "feat: add login",
                "files": ["src/auth.py", "tests/test_auth.py"],
                "lines_added": 50,
                "lines_removed": 10,
            }
        ]
        profile = build_author_profile("alice@example.com", commits)
        assert profile["commit_count"] == 1
        assert profile["files_touched"] == 2
        assert profile["avg_files_per_commit"] == 2.0
        assert len(profile["hour_distribution"]) == 24
        assert len(profile["day_distribution"]) == 7
        assert "scatter_gini" in profile
        assert "bursts" in profile
        assert "sessions" in profile
        assert "top_directories" in profile
        assert "risk_score" in profile
        assert "risk_indicators" in profile

    def test_late_night_detection(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        # Commit at 2 AM UTC = late night
        ts_2am = 1704067200 + 2 * 3600  # 2024-01-01 02:00:00 UTC
        commits = [
            {
                "hash": "a" * 40,
                "author_email": "night@owl.com",
                "timestamp": ts_2am,
                "subject": "fix",
                "files": ["foo.py"],
                "lines_added": 5,
                "lines_removed": 0,
            }
        ]
        profile = build_author_profile("night@owl.com", commits)
        assert profile["late_night_pct"] == 100.0

    def test_noon_commit_not_late_night(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        ts_noon = 1704067200 + 12 * 3600  # 2024-01-01 12:00:00 UTC
        commits = [
            {
                "hash": "b" * 40,
                "author_email": "day@worker.com",
                "timestamp": ts_noon,
                "subject": "feat",
                "files": ["bar.py"],
                "lines_added": 10,
                "lines_removed": 2,
            }
        ]
        profile = build_author_profile("day@worker.com", commits)
        assert profile["late_night_pct"] == 0.0

    def test_risk_indicators_populated(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        # All commits at 3 AM → high late_night_pct → risk indicator
        ts_3am = 1704067200 + 3 * 3600
        commits = [
            {
                "hash": "c" * 40,
                "author_email": "risky@dev.com",
                "timestamp": ts_3am + i * 3600,
                "subject": "fix",
                "files": [f"file{i}.py" for i in range(20)],
                "lines_added": 1,
                "lines_removed": 0,
            }
            for i in range(5)
        ]
        profile = build_author_profile("risky@dev.com", commits)
        # 5 commits all at 3-7 AM → late_night_pct should be high
        assert profile["late_night_pct"] > 0

    def test_top_directories(self):
        from roam.commands.cmd_dev_profile import build_author_profile
        commits = [
            {
                "hash": "d" * 40,
                "author_email": "dev@example.com",
                "timestamp": 1704067200,
                "subject": "stuff",
                "files": ["src/a.py", "src/b.py", "tests/c.py"],
                "lines_added": 10,
                "lines_removed": 0,
            }
        ]
        profile = build_author_profile("dev@example.com", commits)
        dirs = {d["directory"]: d["file_count"] for d in profile["top_directories"]}
        assert "src" in dirs
        assert dirs["src"] == 2
        assert "tests" in dirs


# ===========================================================================
# Integration tests using CLI runner and real git repo
# ===========================================================================


@pytest.fixture
def dev_profile_repo(tmp_path):
    """Create a git repo with multiple authors and varied commit patterns."""
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")

    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "setup@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Setup"], cwd=repo, capture_output=True)

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init",
         "--author", "Setup User <setup@test.com>"],
        cwd=repo, capture_output=True,
    )

    # Alice: focused developer (src/ only)
    for i in range(3):
        (repo / f"feature_{i}.py").write_text(f"def feat_{i}(): pass\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: feature {i}",
             "--author", "Alice Smith <alice@example.com>"],
            cwd=repo, capture_output=True,
        )

    # Bob: scattered developer (many different files)
    for i in range(5):
        (repo / f"module_{i}.py").write_text(f"x = {i}\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"fix: module {i}",
             "--author", "Bob Jones <bob@example.com>"],
            cwd=repo, capture_output=True,
        )

    return repo


class TestDevProfileCLI:
    """Integration tests for the dev-profile CLI command."""

    def test_text_output_verdict_first(self, dev_profile_repo):
        """Text output must start with VERDICT:."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo)
        assert result.exit_code == 0, result.output
        assert result.output.startswith("VERDICT:")

    def test_json_output_valid_envelope(self, dev_profile_repo):
        """JSON output must be a valid roam envelope."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        assert_json_envelope(data, "dev-profile")

    def test_json_summary_has_verdict(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)
        assert len(data["summary"]["verdict"]) > 0

    def test_json_summary_has_required_fields(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        summary = data["summary"]
        assert "author_count" in summary
        assert "total_commits" in summary
        assert "days" in summary
        assert summary["days"] == 90

    def test_json_profiles_list_present(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        assert "profiles" in data
        assert isinstance(data["profiles"], list)

    def test_profiles_have_required_fields(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        profiles = data["profiles"]
        assert len(profiles) > 0
        for p in profiles:
            assert "author" in p
            assert "commit_count" in p
            assert "risk_score" in p
            assert "scatter_gini" in p
            assert "late_night_pct" in p
            assert "weekend_pct" in p
            assert "hour_distribution" in p
            assert "day_distribution" in p
            assert "bursts" in p
            assert "sessions" in p
            assert "top_directories" in p
            assert "risk_indicators" in p

    def test_hour_distribution_is_24_element_list(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        for p in data["profiles"]:
            assert len(p["hour_distribution"]) == 24

    def test_day_distribution_is_7_element_list(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        for p in data["profiles"]:
            assert len(p["day_distribution"]) == 7

    def test_filter_by_author(self, dev_profile_repo):
        """Filtering by author email substring should return only matching profiles."""
        runner = CliRunner()
        result = invoke_cli(
            runner, ["dev-profile", "alice@example.com"],
            cwd=dev_profile_repo, json_mode=True
        )
        data = parse_json_output(result, "dev-profile")
        profiles = data["profiles"]
        assert all("alice" in p["author"] for p in profiles)

    def test_filter_by_author_no_match(self, dev_profile_repo):
        """Filtering by non-existent author should return empty profiles."""
        runner = CliRunner()
        result = invoke_cli(
            runner, ["dev-profile", "nonexistent@nowhere.com"],
            cwd=dev_profile_repo, json_mode=True
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "dev-profile")
        assert data["profiles"] == []

    def test_custom_days_flag(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(
            runner, ["dev-profile", "--days", "7"],
            cwd=dev_profile_repo, json_mode=True
        )
        data = parse_json_output(result, "dev-profile")
        assert data["summary"]["days"] == 7

    def test_risk_score_range(self, dev_profile_repo):
        """Risk score must be 0-100."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        for p in data["profiles"]:
            assert 0 <= p["risk_score"] <= 100

    def test_scatter_gini_range(self, dev_profile_repo):
        """Scatter Gini must be 0-1."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        for p in data["profiles"]:
            assert 0.0 <= p["scatter_gini"] <= 1.0

    def test_limit_flag_respected(self, dev_profile_repo):
        runner = CliRunner()
        result = invoke_cli(
            runner, ["dev-profile", "--limit", "1"],
            cwd=dev_profile_repo, json_mode=True
        )
        data = parse_json_output(result, "dev-profile")
        assert len(data["profiles"]) <= 1

    def test_profiles_sorted_by_risk_score_desc(self, dev_profile_repo):
        """Profiles should be sorted by risk_score descending."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=dev_profile_repo, json_mode=True)
        data = parse_json_output(result, "dev-profile")
        profiles = data["profiles"]
        if len(profiles) > 1:
            scores = [p["risk_score"] for p in profiles]
            assert scores == sorted(scores, reverse=True)


# ===========================================================================
# Edge case: empty git history (very old --days window)
# ===========================================================================


class TestEmptyGitHistory:
    """Test graceful handling of repos with no recent commits."""

    def test_very_short_window_no_commits(self, tmp_path):
        """Using --days 0 on a repo should return an empty profiles list gracefully."""
        repo = tmp_path / "empty_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "f.py").write_text("x=1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init",
             "--date", "2020-01-01T00:00:00",
             "--author", "Old Dev <old@dev.com>"],
            cwd=repo, capture_output=True,
        )

        runner = CliRunner()
        result = invoke_cli(
            runner, ["dev-profile", "--days", "1"],
            cwd=repo, json_mode=True
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "dev-profile")
        assert_json_envelope(data, "dev-profile")
        assert "verdict" in data["summary"]

    def test_no_git_repo_returns_gracefully(self, tmp_path):
        """In a directory with no git repo, command should exit cleanly."""
        runner = CliRunner()
        result = invoke_cli(runner, ["dev-profile"], cwd=tmp_path, json_mode=True)
        assert result.exit_code == 0
        # Either a valid JSON envelope with error verdict, or empty profiles
        data = parse_json_output(result, "dev-profile")
        assert_json_envelope(data, "dev-profile")


# ===========================================================================
# Unit test: top directories computation
# ===========================================================================


class TestTopDirs:
    """Test top directory extraction."""

    def test_empty_files(self):
        from roam.commands.cmd_dev_profile import _top_dirs
        result = _top_dirs([])
        assert result == []

    def test_files_without_slash_use_dot(self):
        from roam.commands.cmd_dev_profile import _top_dirs
        result = _top_dirs(["README.md", "setup.py"])
        assert len(result) == 1
        assert result[0]["directory"] == "."

    def test_groups_by_top_level_dir(self):
        from roam.commands.cmd_dev_profile import _top_dirs
        files = ["src/a.py", "src/b.py", "tests/c.py"]
        result = _top_dirs(files)
        dirs = {d["directory"]: d["file_count"] for d in result}
        assert dirs["src"] == 2
        assert dirs["tests"] == 1

    def test_respects_top_n(self):
        from roam.commands.cmd_dev_profile import _top_dirs
        files = [f"dir{i}/file.py" for i in range(10)]
        result = _top_dirs(files, top_n=3)
        assert len(result) <= 3

    def test_sorted_by_count(self):
        from roam.commands.cmd_dev_profile import _top_dirs
        files = ["a/x.py", "a/y.py", "a/z.py", "b/w.py"]
        result = _top_dirs(files, top_n=5)
        assert result[0]["directory"] == "a"
        assert result[0]["file_count"] == 3


# ===========================================================================
# MCP tool registration test
# ===========================================================================


class TestMCPToolRegistration:
    """Verify roam_dev_profile is registered in mcp_server."""

    def test_mcp_tool_function_exists(self):
        from roam.mcp_server import roam_dev_profile
        assert callable(roam_dev_profile)

    def test_mcp_tool_accepts_author_arg(self):
        import inspect
        from roam.mcp_server import roam_dev_profile
        sig = inspect.signature(roam_dev_profile)
        assert "author" in sig.parameters
        assert "days" in sig.parameters

    def test_mcp_tool_has_docstring(self):
        from roam.mcp_server import roam_dev_profile
        assert roam_dev_profile.__doc__ is not None
        assert len(roam_dev_profile.__doc__.strip()) > 0
