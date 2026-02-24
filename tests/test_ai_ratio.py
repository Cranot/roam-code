"""Tests for the roam ai-ratio command.

Covers:
- Gini coefficient calculation (unit tests)
- Burst addition detection
- Commit message pattern detection (co-author tags, AI-style messages)
- Confidence levels (LOW, MEDIUM, HIGH)
- JSON output structure and envelope
- Text output with VERDICT line
- --since flag filtering
- --detail flag for extended file list
- Empty/no commits edge case
- Per-file probability ranking
- Temporal signal detection
- Full integration with indexed project and git history
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import click
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    git_init,
    git_commit,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Local CLI shim
# ---------------------------------------------------------------------------

def _make_local_cli():
    """Return a Click group containing only the ai-ratio command."""
    from roam.commands.cmd_ai_ratio import ai_ratio

    @click.group()
    @click.option("--json", "json_out", is_flag=True)
    @click.pass_context
    def _local_cli(ctx, json_out):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_out

    _local_cli.add_command(ai_ratio)
    return _local_cli


_LOCAL_CLI = _make_local_cli()


def _invoke(args, cwd=None, json_mode=False):
    """Invoke the ai-ratio command via the local CLI shim."""
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(_LOCAL_CLI, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(result, cmd="ai-ratio"):
    """Parse JSON from a CliRunner result."""
    assert result.exit_code == 0, (
        f"{cmd} exited {result.exit_code}:\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from {cmd}: {e}\nOutput:\n{result.output[:600]}"
        )


def _assert_envelope(data, cmd="ai-ratio"):
    """Verify standard roam JSON envelope keys."""
    assert isinstance(data, dict)
    assert data.get("command") == cmd
    assert "version" in data
    assert "timestamp" in data or ("_meta" in data and "timestamp" in data["_meta"])
    assert "summary" in data
    assert isinstance(data["summary"], dict)


def _git_commit_with_msg(path, msg, author_name="Test", author_email="t@t.com"):
    """Stage all and commit with a specific message."""
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg,
         "--author", f"{author_name} <{author_email}>"],
        cwd=path, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Unit tests for compute_gini
# ---------------------------------------------------------------------------

class TestGiniCoefficient:
    """Unit tests for the Gini coefficient calculation."""

    def test_empty_list(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        assert compute_gini([]) == 0.0

    def test_single_value(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        assert compute_gini([42.0]) == 0.0

    def test_equal_values(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        result = compute_gini([10.0, 10.0, 10.0, 10.0])
        assert result == pytest.approx(0.0, abs=0.01)

    def test_extreme_inequality(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        # One value dominates
        result = compute_gini([0.0, 0.0, 0.0, 1000.0])
        assert result > 0.7, f"Expected high Gini, got {result}"

    def test_moderate_inequality(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        result = compute_gini([1.0, 2.0, 3.0, 4.0, 100.0])
        assert 0.3 < result < 0.9

    def test_all_zeros(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        assert compute_gini([0.0, 0.0, 0.0]) == 0.0

    def test_two_values_unequal(self):
        from roam.commands.cmd_ai_ratio import compute_gini
        result = compute_gini([1.0, 99.0])
        assert result > 0.4


# ---------------------------------------------------------------------------
# Unit tests for burst detection
# ---------------------------------------------------------------------------

class TestBurstDetection:
    """Unit tests for burst addition detection."""

    def test_not_burst_small_add(self):
        from roam.commands.cmd_ai_ratio import _is_burst_add
        commit = {"files": [{"lines_added": 10, "lines_removed": 2, "path": "a.py"}]}
        assert not _is_burst_add(commit)

    def test_burst_large_add(self):
        from roam.commands.cmd_ai_ratio import _is_burst_add
        commit = {"files": [{"lines_added": 200, "lines_removed": 5, "path": "a.py"}]}
        assert _is_burst_add(commit)

    def test_not_burst_balanced(self):
        from roam.commands.cmd_ai_ratio import _is_burst_add
        # Large change but balanced adds/removes
        commit = {"files": [{"lines_added": 100, "lines_removed": 100, "path": "a.py"}]}
        assert not _is_burst_add(commit)

    def test_burst_multi_file(self):
        from roam.commands.cmd_ai_ratio import _is_burst_add
        commit = {"files": [
            {"lines_added": 80, "lines_removed": 2, "path": "a.py"},
            {"lines_added": 60, "lines_removed": 3, "path": "b.py"},
        ]}
        assert _is_burst_add(commit)

    def test_empty_commit(self):
        from roam.commands.cmd_ai_ratio import _is_burst_add
        commit = {"files": []}
        assert not _is_burst_add(commit)


# ---------------------------------------------------------------------------
# Unit tests for commit message pattern detection
# ---------------------------------------------------------------------------

class TestCommitPatterns:
    """Unit tests for commit message pattern detection."""

    def test_co_author_claude(self):
        from roam.commands.cmd_ai_ratio import _has_co_author_tag
        msg = "feat: add feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
        assert _has_co_author_tag(msg)

    def test_co_author_copilot(self):
        from roam.commands.cmd_ai_ratio import _has_co_author_tag
        msg = "fix bug\n\nCo-Authored-By: GitHub Copilot <noreply@github.com>"
        assert _has_co_author_tag(msg)

    def test_co_author_cursor(self):
        from roam.commands.cmd_ai_ratio import _has_co_author_tag
        msg = "update code\n\nCo-authored-by: Cursor AI <cursor@example.com>"
        assert _has_co_author_tag(msg)

    def test_no_co_author(self):
        from roam.commands.cmd_ai_ratio import _has_co_author_tag
        msg = "fix: resolve login issue"
        assert not _has_co_author_tag(msg)

    def test_human_co_author(self):
        from roam.commands.cmd_ai_ratio import _has_co_author_tag
        msg = "update: feature\n\nCo-Authored-By: John Smith <john@example.com>"
        assert not _has_co_author_tag(msg)

    def test_ai_message_feat(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert _has_ai_message_pattern("feat: add new endpoint")

    def test_ai_message_fix(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert _has_ai_message_pattern("fix(auth): resolve token issue")

    def test_ai_message_implement(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert _has_ai_message_pattern("Implement user authentication")

    def test_ai_message_add(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert _has_ai_message_pattern("Add error handling for edge cases")

    def test_normal_message(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert not _has_ai_message_pattern("WIP checkpoint")

    def test_normal_message_lowercase(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert not _has_ai_message_pattern("fixed that pesky bug")

    def test_auto_generated(self):
        from roam.commands.cmd_ai_ratio import _has_ai_message_pattern
        assert _has_ai_message_pattern("Generated migration for users table")


# ---------------------------------------------------------------------------
# Unit tests for confidence levels
# ---------------------------------------------------------------------------

class TestConfidence:
    """Unit tests for confidence level mapping."""

    def test_low_confidence(self):
        from roam.commands.cmd_ai_ratio import _confidence_label
        assert _confidence_label(10) == "LOW"
        assert _confidence_label(49) == "LOW"

    def test_medium_confidence(self):
        from roam.commands.cmd_ai_ratio import _confidence_label
        assert _confidence_label(50) == "MEDIUM"
        assert _confidence_label(100) == "MEDIUM"
        assert _confidence_label(200) == "MEDIUM"

    def test_high_confidence(self):
        from roam.commands.cmd_ai_ratio import _confidence_label
        assert _confidence_label(201) == "HIGH"
        assert _confidence_label(500) == "HIGH"


# ---------------------------------------------------------------------------
# Unit tests for temporal signal
# ---------------------------------------------------------------------------

class TestTemporalSignal:
    """Unit tests for temporal pattern detection."""

    def test_too_few_commits(self):
        from roam.commands.cmd_ai_ratio import _temporal_signal
        commits = [{"timestamp": 1000}, {"timestamp": 2000}]
        score, sessions = _temporal_signal(commits)
        assert score == 0.0
        assert sessions == 0

    def test_burst_session_detected(self):
        from roam.commands.cmd_ai_ratio import _temporal_signal
        now = int(time.time())
        # 5 commits within 5 minutes = burst session
        commits = [
            {"timestamp": now, "files": []},
            {"timestamp": now + 60, "files": []},
            {"timestamp": now + 120, "files": []},
            {"timestamp": now + 180, "files": []},
            {"timestamp": now + 240, "files": []},
        ]
        score, sessions = _temporal_signal(commits)
        assert sessions >= 1

    def test_spread_commits_no_burst(self):
        from roam.commands.cmd_ai_ratio import _temporal_signal
        now = int(time.time())
        # Commits spread over days
        commits = [
            {"timestamp": now - 86400 * i}
            for i in range(10)
        ]
        score, sessions = _temporal_signal(commits)
        assert sessions == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ai_ratio_project(tmp_path, monkeypatch):
    """Project with mixed human and AI-style commits for testing."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Initial file
    (proj / "app.py").write_text(
        'def main():\n'
        '    print("hello")\n'
    )
    git_init(proj)

    # Human-style small commits
    (proj / "utils.py").write_text(
        'def helper():\n'
        '    return 42\n'
    )
    _git_commit_with_msg(proj, "WIP checkpoint", "Alice", "alice@example.com")

    (proj / "utils.py").write_text(
        'def helper():\n'
        '    """A helper function."""\n'
        '    return 42\n'
        '\n'
        'def another():\n'
        '    pass\n'
    )
    _git_commit_with_msg(proj, "added docstring", "Alice", "alice@example.com")

    # AI-style burst commit with co-author tag
    lines = [f'def func_{i}():\n    """Function {i}."""\n    return {i}\n\n' for i in range(50)]
    (proj / "generated.py").write_text("".join(lines))
    _git_commit_with_msg(
        proj,
        "feat: add generated functions\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        "Bob", "bob@example.com",
    )

    # Another AI-style commit
    lines2 = [f'class Model{i}:\n    """Model {i}."""\n    value = {i}\n\n' for i in range(30)]
    (proj / "models.py").write_text("".join(lines2))
    _git_commit_with_msg(
        proj,
        "Implement data models for API",
        "Bob", "bob@example.com",
    )

    # Index the project
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    return proj


@pytest.fixture
def empty_project(tmp_path, monkeypatch):
    """Project with only the initial commit (minimal git history)."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text('print("hello")\n')
    git_init(proj)

    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestAIRatioCommand:
    """Integration tests for the ai-ratio command."""

    def test_exit_code_zero(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project)
        assert result.exit_code == 0, f"Non-zero exit:\n{result.output}"

    def test_verdict_in_text_output(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "estimated AI-generated code" in result.output

    def test_signals_section(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project)
        assert result.exit_code == 0
        assert "SIGNALS:" in result.output
        assert "Change concentration (Gini):" in result.output
        assert "Burst additions:" in result.output
        assert "Commit patterns:" in result.output

    def test_json_output_structure(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        _assert_envelope(data)

        # Check summary
        summary = data["summary"]
        assert "verdict" in summary
        assert "ai_ratio" in summary
        assert "confidence" in summary
        assert "commits_analyzed" in summary

        # Check top-level fields
        assert "ai_ratio" in data
        assert "confidence" in data
        assert "commits_analyzed" in data
        assert "signals" in data
        assert "top_ai_files" in data
        assert "trend" in data

    def test_json_ai_ratio_is_float(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        assert isinstance(data["ai_ratio"], (int, float))
        assert 0.0 <= data["ai_ratio"] <= 1.0

    def test_json_signals_structure(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        signals = data["signals"]

        assert "gini" in signals
        assert "burst_additions" in signals
        assert "commit_patterns" in signals
        assert "comment_density" in signals
        assert "temporal" in signals

        # Each signal has score and weight
        for key in ["gini", "burst_additions", "commit_patterns",
                     "comment_density", "temporal"]:
            sig = signals[key]
            assert "score" in sig
            assert "weight" in sig

    def test_json_co_author_detected(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        co_author_count = data["signals"]["commit_patterns"]["co_author_count"]
        assert co_author_count >= 1, (
            f"Expected at least 1 co-author tag, got {co_author_count}"
        )

    def test_json_top_files_have_probability(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        files = data["top_ai_files"]
        if files:
            for f in files:
                assert "path" in f
                assert "probability" in f
                assert "reasons" in f
                assert 0.0 <= f["probability"] <= 1.0

    def test_since_flag(self, ai_ratio_project):
        # --since 1 should still find recent commits
        result = _invoke(["ai-ratio", "--since", "1"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        assert data["commits_analyzed"] >= 0

    def test_since_flag_long_range(self, ai_ratio_project):
        result = _invoke(["ai-ratio", "--since", "365"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        # Should find all commits
        assert data["commits_analyzed"] >= 3

    def test_detail_flag_text(self, ai_ratio_project):
        result = _invoke(["ai-ratio", "--detail"], cwd=ai_ratio_project)
        assert result.exit_code == 0
        # --detail should show the TOP AI-LIKELY FILES section
        # (whether there are files depends on detection, but the command should work)
        assert "VERDICT:" in result.output

    def test_trend_present(self, ai_ratio_project):
        result = _invoke(["ai-ratio"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        trend = data["trend"]
        assert "direction" in trend
        assert "data_points" in trend

    def test_confidence_low_few_commits(self, empty_project):
        result = _invoke(["ai-ratio"], cwd=empty_project, json_mode=True)
        data = _parse_json(result)
        assert data["confidence"] == "LOW"

    def test_empty_project_zero_ratio(self, empty_project):
        result = _invoke(["ai-ratio"], cwd=empty_project, json_mode=True)
        data = _parse_json(result)
        # With just an "init" commit, AI ratio should be very low
        assert data["ai_ratio"] <= 0.5

    def test_since_zero_no_commits(self, ai_ratio_project):
        # --since 0 means only commits from today, might be 0 if clock skew
        result = _invoke(["ai-ratio", "--since", "0"], cwd=ai_ratio_project, json_mode=True)
        data = _parse_json(result)
        assert "commits_analyzed" in data

    def test_text_output_no_crash(self, empty_project):
        """Even with minimal data, text output should not crash."""
        result = _invoke(["ai-ratio"], cwd=empty_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ---------------------------------------------------------------------------
# Unit tests for analyse_ai_ratio
# ---------------------------------------------------------------------------

class TestAnalyseFunction:
    """Tests for the analyse_ai_ratio function directly."""

    def test_analyse_with_no_commits(self, empty_project):
        from roam.commands.cmd_ai_ratio import analyse_ai_ratio
        from roam.db.connection import open_db
        with open_db(readonly=False) as conn:
            # Delete all commits from DB to simulate no commits
            conn.execute("DELETE FROM git_file_changes")
            conn.execute("DELETE FROM git_commits")
            conn.commit()
            result = analyse_ai_ratio(conn, since_days=365)
            assert result["ai_ratio"] == 0.0
            assert result["confidence"] == "LOW"
            assert result["commits_analyzed"] == 0

    def test_analyse_returns_all_keys(self, ai_ratio_project):
        from roam.commands.cmd_ai_ratio import analyse_ai_ratio
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            result = analyse_ai_ratio(conn, since_days=365)
            assert "ai_ratio" in result
            assert "confidence" in result
            assert "commits_analyzed" in result
            assert "signals" in result
            assert "top_ai_files" in result
            assert "trend" in result


# ---------------------------------------------------------------------------
# Edge case: project with only AI commits
# ---------------------------------------------------------------------------

class TestHighAIRatio:
    """Test with a project that has predominantly AI-style commits."""

    @pytest.fixture
    def ai_heavy_project(self, tmp_path, monkeypatch):
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "stub.py").write_text("# stub\n")
        git_init(proj)

        # Create several AI-style commits
        for i in range(5):
            lines = [f'def auto_func_{i}_{j}():\n    return {j}\n\n' for j in range(30)]
            (proj / f"module_{i}.py").write_text("".join(lines))
            _git_commit_with_msg(
                proj,
                f"feat: implement module {i}\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                "Dev", "dev@example.com",
            )

        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index failed:\n{out}"
        return proj

    def test_high_ai_ratio_detected(self, ai_heavy_project):
        result = _invoke(["ai-ratio"], cwd=ai_heavy_project, json_mode=True)
        data = _parse_json(result)
        # With mostly AI commits, ratio should be substantial
        assert data["ai_ratio"] > 0.1, (
            f"Expected high AI ratio, got {data['ai_ratio']}"
        )
        assert data["signals"]["commit_patterns"]["co_author_count"] >= 3

    def test_top_files_populated(self, ai_heavy_project):
        result = _invoke(["ai-ratio"], cwd=ai_heavy_project, json_mode=True)
        data = _parse_json(result)
        assert len(data["top_ai_files"]) > 0
        # Files should have co-author tag reason
        reasons = set()
        for f in data["top_ai_files"]:
            reasons.update(f["reasons"])
        assert "co-author tag" in reasons


# ---------------------------------------------------------------------------
# Test pattern signal function in isolation
# ---------------------------------------------------------------------------

class TestPatternSignal:
    """Test _pattern_signal with controlled commit data."""

    def test_all_co_authored(self):
        from roam.commands.cmd_ai_ratio import _pattern_signal
        commits = [
            {"message": "feat: add X\n\nCo-Authored-By: Claude <c@a.com>", "files": []},
            {"message": "fix: bug\n\nCo-Authored-By: Copilot <c@g.com>", "files": []},
            {"message": "refactor: clean\n\nCo-Authored-By: Cursor <c@c.com>", "files": []},
        ]
        score, co_count, pat_count = _pattern_signal(commits)
        assert co_count == 3
        assert score > 0.5

    def test_no_ai_patterns(self):
        from roam.commands.cmd_ai_ratio import _pattern_signal
        commits = [
            {"message": "WIP", "files": []},
            {"message": "checkpoint", "files": []},
            {"message": "stuff", "files": []},
        ]
        score, co_count, pat_count = _pattern_signal(commits)
        assert co_count == 0
        assert pat_count == 0
        assert score == 0.0

    def test_mixed_patterns(self):
        from roam.commands.cmd_ai_ratio import _pattern_signal
        commits = [
            {"message": "feat: add feature\n\nCo-Authored-By: Claude <c@a.com>", "files": []},
            {"message": "WIP", "files": []},
            {"message": "stuff", "files": []},
            {"message": "debug that thing", "files": []},
        ]
        score, co_count, pat_count = _pattern_signal(commits)
        assert co_count == 1
        assert score > 0.0


# ---------------------------------------------------------------------------
# Test burst signal function in isolation
# ---------------------------------------------------------------------------

class TestBurstSignal:
    """Test _burst_signal with controlled commit data."""

    def test_no_burst_commits(self):
        from roam.commands.cmd_ai_ratio import _burst_signal
        commits = [
            {"files": [{"lines_added": 5, "lines_removed": 3, "path": "a.py"}]},
            {"files": [{"lines_added": 10, "lines_removed": 8, "path": "b.py"}]},
        ]
        score, count = _burst_signal(commits)
        assert count == 0
        assert score == 0.0

    def test_all_burst_commits(self):
        from roam.commands.cmd_ai_ratio import _burst_signal
        commits = [
            {"files": [{"lines_added": 200, "lines_removed": 2, "path": "a.py"}]},
            {"files": [{"lines_added": 300, "lines_removed": 5, "path": "b.py"}]},
            {"files": [{"lines_added": 150, "lines_removed": 1, "path": "c.py"}]},
        ]
        score, count = _burst_signal(commits)
        assert count == 3
        assert score > 0.5

    def test_empty_commits(self):
        from roam.commands.cmd_ai_ratio import _burst_signal
        score, count = _burst_signal([])
        assert score == 0.0
        assert count == 0
