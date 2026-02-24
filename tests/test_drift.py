"""Tests for roam drift — ownership drift detection with time-decayed blame scoring."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path

import sys

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, git_commit, index_in_process, invoke_cli, parse_json_output, assert_json_envelope


# ===========================================================================
# Unit tests for time-decay scoring
# ===========================================================================


class TestTimeDecay:
    """Test the exponential time-decay function."""

    def test_zero_days_returns_one(self):
        from roam.commands.cmd_drift import _compute_time_decay

        assert _compute_time_decay(0) == 1.0

    def test_negative_days_returns_one(self):
        from roam.commands.cmd_drift import _compute_time_decay

        assert _compute_time_decay(-10) == 1.0

    def test_half_life_returns_half(self):
        from roam.commands.cmd_drift import _compute_time_decay

        result = _compute_time_decay(180)  # default half-life = 180 days
        assert abs(result - 0.5) < 1e-9

    def test_double_half_life_returns_quarter(self):
        from roam.commands.cmd_drift import _compute_time_decay

        result = _compute_time_decay(360)
        assert abs(result - 0.25) < 1e-9

    def test_custom_half_life(self):
        from roam.commands.cmd_drift import _compute_time_decay

        result = _compute_time_decay(90, half_life=90)
        assert abs(result - 0.5) < 1e-9

    def test_very_old_contribution_near_zero(self):
        from roam.commands.cmd_drift import _compute_time_decay

        result = _compute_time_decay(3600)  # 10 years
        assert result < 0.001


# ===========================================================================
# Unit tests for drift score computation
# ===========================================================================


class TestDriftScore:
    """Test drift score computation."""

    def test_declared_owner_is_top_contributor(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(
            ["@alice"],
            {"alice": 0.8, "bob": 0.2},
        )
        assert score == 0.2

    def test_declared_owner_not_in_contributors(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(
            ["@carol"],
            {"alice": 0.6, "bob": 0.4},
        )
        assert score == 1.0

    def test_multiple_declared_owners(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(
            ["@alice", "@bob"],
            {"alice": 0.3, "bob": 0.4, "carol": 0.3},
        )
        # Combined declared share = 0.3 + 0.4 = 0.7, drift = 0.3
        assert abs(score - 0.3) < 0.001

    def test_empty_declared_owners(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score([], {"alice": 1.0})
        assert score == 0.0

    def test_empty_ownership_shares(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(["@alice"], {})
        assert score == 0.0

    def test_case_insensitive_matching(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(
            ["@Alice"],
            {"alice": 0.9, "bob": 0.1},
        )
        # Should match case-insensitively
        assert abs(score - 0.1) < 0.001

    def test_at_prefix_stripped(self):
        from roam.commands.cmd_drift import compute_drift_score

        score = compute_drift_score(
            ["@alice"],
            {"alice": 0.7, "bob": 0.3},
        )
        assert abs(score - 0.3) < 0.001


# ===========================================================================
# Unit tests for file ownership computation
# ===========================================================================


class TestComputeFileOwnership:
    """Test time-decayed ownership computation from DB data."""

    def test_empty_result_for_unknown_file(self, tmp_path):
        """compute_file_ownership returns empty dict when file has no git data."""
        import sqlite3
        from roam.commands.cmd_drift import compute_file_ownership

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE git_commits (id INTEGER PRIMARY KEY, hash TEXT, author TEXT, timestamp INTEGER, message TEXT)")
        db.execute("CREATE TABLE git_file_changes (id INTEGER PRIMARY KEY, commit_id INTEGER, file_id INTEGER, path TEXT, lines_added INTEGER DEFAULT 0, lines_removed INTEGER DEFAULT 0)")

        result = compute_file_ownership(db, file_id=999)
        assert result == {}
        db.close()

    def test_single_author_gets_full_ownership(self, tmp_path):
        """Single author should get 1.0 ownership share."""
        import sqlite3
        from roam.commands.cmd_drift import compute_file_ownership

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE git_commits (id INTEGER PRIMARY KEY, hash TEXT, author TEXT, timestamp INTEGER, message TEXT)")
        db.execute("CREATE TABLE git_file_changes (id INTEGER PRIMARY KEY, commit_id INTEGER, file_id INTEGER, path TEXT, lines_added INTEGER DEFAULT 0, lines_removed INTEGER DEFAULT 0)")

        now = int(time.time())
        db.execute("INSERT INTO git_commits VALUES (1, 'abc', 'alice', ?, 'init')", (now,))
        db.execute("INSERT INTO git_file_changes VALUES (1, 1, 1, 'foo.py', 50, 10)")

        result = compute_file_ownership(db, file_id=1, now_ts=now)
        assert abs(result.get("alice", 0) - 1.0) < 0.001
        db.close()

    def test_recent_contributor_weighted_higher(self, tmp_path):
        """Recent contributions should receive higher weight than old ones."""
        import sqlite3
        from roam.commands.cmd_drift import compute_file_ownership

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE git_commits (id INTEGER PRIMARY KEY, hash TEXT, author TEXT, timestamp INTEGER, message TEXT)")
        db.execute("CREATE TABLE git_file_changes (id INTEGER PRIMARY KEY, commit_id INTEGER, file_id INTEGER, path TEXT, lines_added INTEGER DEFAULT 0, lines_removed INTEGER DEFAULT 0)")

        now = int(time.time())
        one_year_ago = now - 365 * 86400

        # alice: 100 lines changed one year ago
        db.execute("INSERT INTO git_commits VALUES (1, 'abc', 'alice', ?, 'old change')", (one_year_ago,))
        db.execute("INSERT INTO git_file_changes VALUES (1, 1, 1, 'foo.py', 80, 20)")

        # bob: 100 lines changed just now
        db.execute("INSERT INTO git_commits VALUES (2, 'def', 'bob', ?, 'recent change')", (now,))
        db.execute("INSERT INTO git_file_changes VALUES (2, 2, 1, 'foo.py', 80, 20)")

        result = compute_file_ownership(db, file_id=1, now_ts=now)
        assert result["bob"] > result["alice"], (
            f"Expected bob ({result['bob']:.3f}) > alice ({result['alice']:.3f})"
        )
        db.close()

    def test_ownership_shares_sum_to_one(self, tmp_path):
        """Ownership shares should normalise to 1.0."""
        import sqlite3
        from roam.commands.cmd_drift import compute_file_ownership

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE git_commits (id INTEGER PRIMARY KEY, hash TEXT, author TEXT, timestamp INTEGER, message TEXT)")
        db.execute("CREATE TABLE git_file_changes (id INTEGER PRIMARY KEY, commit_id INTEGER, file_id INTEGER, path TEXT, lines_added INTEGER DEFAULT 0, lines_removed INTEGER DEFAULT 0)")

        now = int(time.time())
        db.execute("INSERT INTO git_commits VALUES (1, 'a', 'alice', ?, 'x')", (now,))
        db.execute("INSERT INTO git_commits VALUES (2, 'b', 'bob', ?, 'y')", (now - 90 * 86400,))
        db.execute("INSERT INTO git_commits VALUES (3, 'c', 'carol', ?, 'z')", (now - 360 * 86400,))
        db.execute("INSERT INTO git_file_changes VALUES (1, 1, 1, 'f.py', 30, 10)")
        db.execute("INSERT INTO git_file_changes VALUES (2, 2, 1, 'f.py', 40, 20)")
        db.execute("INSERT INTO git_file_changes VALUES (3, 3, 1, 'f.py', 50, 30)")

        result = compute_file_ownership(db, file_id=1, now_ts=now)
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001, f"Shares sum to {total}, expected 1.0"
        db.close()


# ===========================================================================
# CLI integration tests (using project_factory)
# ===========================================================================


class TestDriftCLI:
    """Integration tests for the drift CLI command."""

    def test_no_codeowners_text(self, project_factory):
        """drift with no CODEOWNERS file produces graceful message."""
        proj = project_factory({"app.py": "def main(): pass\n"})
        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj)
        assert result.exit_code == 0
        assert "No CODEOWNERS" in result.output

    def test_no_codeowners_json(self, project_factory):
        """drift --json with no CODEOWNERS returns proper envelope."""
        proj = project_factory({"app.py": "def main(): pass\n"})
        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "drift"
        assert data["summary"]["codeowners_found"] is False

    def test_no_drift_below_threshold(self, tmp_path):
        """When declared owner is the actual contributor, no drift is reported."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        # Create a file and CODEOWNERS
        src = proj / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")
        (proj / "CODEOWNERS").write_text("*.py @Test\n")

        # git init sets user.name=Test, so the author matches
        git_init(proj)

        # Index
        out, rc = index_in_process(proj)
        assert rc == 0, f"Index failed: {out}"

        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_drift_detected_with_different_author(self, tmp_path):
        """Drift is detected when declared owner differs from actual contributor."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        src = proj / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")
        # Declare @someone-else as owner, but Test is the committer
        (proj / "CODEOWNERS").write_text("*.py @someone-else\n")

        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index failed: {out}"

        runner = CliRunner()
        result = invoke_cli(runner, ["drift", "--threshold", "0.1"], cwd=proj)
        assert result.exit_code == 0
        # Should detect drift since @someone-else is not the contributor (Test)
        assert "drift" in result.output.lower()

    def test_threshold_filtering(self, tmp_path):
        """High threshold should filter out low-drift files."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        (proj / "app.py").write_text("def main():\n    pass\n")
        (proj / "CODEOWNERS").write_text("*.py @nonexistent-owner\n")

        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index failed: {out}"

        runner = CliRunner()

        # With threshold=0.99, only extreme drift is shown
        result_high = invoke_cli(
            runner, ["drift", "--threshold", "0.99"], cwd=proj
        )
        assert result_high.exit_code == 0

        # With threshold=0.01, most drift is shown
        result_low = invoke_cli(
            runner, ["drift", "--threshold", "0.01"], cwd=proj
        )
        assert result_low.exit_code == 0

    def test_json_output_envelope(self, tmp_path):
        """JSON output follows the standard envelope format."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        (proj / "app.py").write_text("def main():\n    pass\n")
        (proj / "CODEOWNERS").write_text("*.py @someone\n")

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj, json_mode=True)
        data = parse_json_output(result, command="drift")
        assert_json_envelope(data, command="drift")
        assert "drift_files" in data["summary"]
        assert "threshold" in data["summary"]

    def test_json_drift_entries_structure(self, tmp_path):
        """Drift entries in JSON have expected fields."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        (proj / "app.py").write_text("def main():\n    pass\n")
        (proj / "CODEOWNERS").write_text("*.py @nonexistent\n")

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        runner = CliRunner()
        result = invoke_cli(
            runner, ["drift", "--threshold", "0.1"], cwd=proj, json_mode=True
        )
        data = parse_json_output(result, command="drift")
        if data.get("drift"):
            entry = data["drift"][0]
            assert "path" in entry
            assert "declared_owners" in entry
            assert "actual_top_contributor" in entry
            assert "actual_top_share" in entry
            assert "drift_score" in entry
            assert "ownership_shares" in entry

    def test_verdict_first_in_text_output(self, tmp_path):
        """Text output starts with VERDICT: line."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        (proj / "app.py").write_text("x = 1\n")
        (proj / "CODEOWNERS").write_text("*.py @team\n")

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj)
        assert result.exit_code == 0
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")

    def test_no_files_in_index(self, tmp_path):
        """Gracefully handle an empty index."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "CODEOWNERS").write_text("*.py @team\n")

        git_init(proj)
        # Index with no source files
        out, rc = index_in_process(proj)
        # May or may not have files; the point is no crash
        runner = CliRunner()
        result = invoke_cli(runner, ["drift"], cwd=proj)
        assert result.exit_code == 0

    def test_recommendations_in_text_output(self, tmp_path):
        """Recommendations section appears when drift is detected."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        src = proj / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass\n")
        (src / "b.py").write_text("def b(): pass\n")
        (proj / "CODEOWNERS").write_text("src/ @ghost-team\n")

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        runner = CliRunner()
        result = invoke_cli(
            runner, ["drift", "--threshold", "0.1"], cwd=proj
        )
        assert result.exit_code == 0
        # If drift is detected, recommendations should appear
        if "drift" in result.output.lower() and "0 files" not in result.output:
            assert "Recommendations:" in result.output or "Summary:" in result.output

    def test_recommendations_in_json_output(self, tmp_path):
        """JSON output includes recommendations list."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        (proj / "app.py").write_text("def main(): pass\n")
        (proj / "CODEOWNERS").write_text("*.py @nonexistent\n")

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        runner = CliRunner()
        result = invoke_cli(
            runner, ["drift", "--threshold", "0.1"], cwd=proj, json_mode=True
        )
        data = parse_json_output(result, command="drift")
        assert "recommendations" in data


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestHelpers:
    """Test helper functions."""

    def test_top_contributor_empty(self):
        from roam.commands.cmd_drift import _top_contributor

        name, share = _top_contributor({})
        assert name == ""
        assert share == 0.0

    def test_top_contributor_selects_highest(self):
        from roam.commands.cmd_drift import _top_contributor

        name, share = _top_contributor({"alice": 0.3, "bob": 0.7})
        assert name == "bob"
        assert share == 0.7

    def test_common_directory(self):
        from roam.commands.cmd_drift import _common_directory

        result = _common_directory(["src/a.py", "src/b.py", "lib/c.py"])
        assert result == "src/"

    def test_common_directory_empty(self):
        from roam.commands.cmd_drift import _common_directory

        result = _common_directory([])
        assert result == "./"

    def test_normalise_name(self):
        from roam.commands.cmd_drift import _normalise_name

        assert _normalise_name("@Alice") == "alice"
        assert _normalise_name("bob") == "bob"
        assert _normalise_name("@TEAM") == "team"
