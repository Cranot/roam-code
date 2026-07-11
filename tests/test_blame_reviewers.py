"""Tests for the ``blame-reviewers`` command.

``blame-reviewers`` promotes pr-risk's hidden ``suggested_reviewers``
capability into its own read-only advisory command. It ranks authors by
total ``lines_added`` across the non-test files a diff touches, reusing
``roam.commands.cmd_pr_risk.rank_blame_reviewers`` (single source of truth).

Covers:
- Command is registered + importable.
- Text output on an unstaged diff (VERDICT + ranked table).
- Empty case (no changes) — clean 'no changes' message, no crash.
- --json envelope shape.
- Reuse: the standalone command and pr-risk share one ranking helper.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli

# ===========================================================================
# Helpers
# ===========================================================================


def _make_multi_author_project(tmp_path):
    """Git repo where alice, bob, carol contribute distinct line counts."""
    proj = tmp_path / "blame_reviewers_proj"
    proj.mkdir()
    src = proj / "src"
    src.mkdir()

    (proj / ".gitignore").write_text(".roam/\n")
    (src / "auth.py").write_text('def login(user):\n    return user == "admin"\n')
    (src / "models.py").write_text("class User:\n    pass\n")

    def _git(*args):
        subprocess.run(["git", *args], cwd=proj, capture_output=True)

    _git("init")
    _git("config", "user.email", "alice@company.com")
    _git("config", "user.name", "alice")
    _git("add", ".")
    _git("commit", "-m", "init by alice")

    # bob heavily rewrites auth.py (many lines added)
    _git("config", "user.name", "bob")
    _git("config", "user.email", "bob@company.com")
    (src / "auth.py").write_text(
        "def login(user, password):\n"
        "    if not user:\n"
        "        return False\n"
        "    if not password:\n"
        "        return False\n"
        '    return user == "admin" and password == "secret"\n'
        "\n"
        "def logout(user):\n"
        "    return None\n"
    )
    _git("add", ".")
    _git("commit", "-m", "bob expands auth")

    return proj


def _index_project(proj, monkeypatch):
    from conftest import index_in_process

    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"


def _make_unstaged_change(proj, rel_path, content):
    fp = proj / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)


# ===========================================================================
# Registration / import
# ===========================================================================


class TestBlameReviewersRegistration:
    def test_command_importable(self):
        from roam.commands.cmd_blame_reviewers import blame_reviewers

        assert blame_reviewers.name == "blame-reviewers"

    def test_command_registered(self):
        from roam.cli import _COMMANDS

        assert "blame-reviewers" in _COMMANDS
        assert _COMMANDS["blame-reviewers"] == (
            "roam.commands.cmd_blame_reviewers",
            "blame_reviewers",
        )

    def test_capability_metadata_read_only(self):
        from roam.commands.cmd_blame_reviewers import blame_reviewers

        cap = getattr(blame_reviewers, "__roam_capability__")
        assert cap.side_effect is False
        assert cap.destructive is False
        assert cap.ai_safe is True
        assert cap.requires_index is True

    def test_reuses_pr_risk_helper(self):
        """The command imports pr-risk's ranking helper (no duplication)."""
        import roam.commands.cmd_blame_reviewers as mod
        from roam.commands.cmd_pr_risk import rank_blame_reviewers

        assert mod.rank_blame_reviewers is rank_blame_reviewers


# ===========================================================================
# Empty case
# ===========================================================================


class TestBlameReviewersEmpty:
    def test_no_changes_exits_zero(self, indexed_project, cli_runner, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["blame-reviewers"])
        assert result.exit_code == 0
        assert "No changes found" in result.output

    def test_no_changes_json(self, indexed_project, cli_runner, monkeypatch):
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["blame-reviewers"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="blame-reviewers")
        assert data["reviewers"] == []
        assert data["summary"]["reviewers_suggested"] == 0


# ===========================================================================
# Text output on a diff
# ===========================================================================


class TestBlameReviewersText:
    def test_text_output_ranks_reviewers(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)
        _make_unstaged_change(proj, "src/auth.py", "def login():\n    return True\n")

        result = invoke_cli(cli_runner, ["blame-reviewers"])
        assert result.exit_code == 0
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")
        # bob has the most lines added on auth.py, so a table appears.
        assert "REVIEWER" in result.output
        assert "LINES ADDED" in result.output
        assert "bob" in result.output

    def test_top_flag_limits_rows(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)
        _make_unstaged_change(proj, "src/auth.py", "def login():\n    return True\n")

        result = invoke_cli(cli_runner, ["blame-reviewers", "--top", "1"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["reviewers"]) <= 1


# ===========================================================================
# JSON envelope shape
# ===========================================================================


class TestBlameReviewersJSON:
    def test_json_envelope_shape(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)
        _make_unstaged_change(proj, "src/auth.py", "def login():\n    return True\n")

        result = invoke_cli(cli_runner, ["blame-reviewers"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="blame-reviewers")

        assert "reviewers" in data
        assert "changed_files" in data
        assert "verdict" in data["summary"]
        assert isinstance(data["summary"]["verdict"], str)

        for reviewer in data["reviewers"]:
            # W198 dual-key shape carried over from pr-risk.
            assert "author" in reviewer
            assert "actor" in reviewer
            assert reviewer["author"] == reviewer["actor"]
            assert "lines" in reviewer
            assert isinstance(reviewer["lines"], int)

    def test_reviewers_sorted_by_lines(self, tmp_path, cli_runner, monkeypatch):
        proj = _make_multi_author_project(tmp_path)
        _index_project(proj, monkeypatch)
        _make_unstaged_change(proj, "src/auth.py", "def login():\n    return True\n")

        result = invoke_cli(cli_runner, ["blame-reviewers"], json_mode=True)
        data = json.loads(result.output)
        reviewers = data["reviewers"]
        for i in range(len(reviewers) - 1):
            assert reviewers[i]["lines"] >= reviewers[i + 1]["lines"]


# ===========================================================================
# Helper unit test (test-file exclusion)
# ===========================================================================


class TestRankBlameReviewers:
    def test_excludes_test_files(self, tmp_path, cli_runner, monkeypatch):
        """Ranking skips test files (is_test_file) — empty when only tests change."""
        proj = _make_multi_author_project(tmp_path)
        # add a test file authored by bob
        (proj / "tests").mkdir()
        (proj / "tests" / "test_auth.py").write_text("def test_login():\n    assert True\n")
        subprocess.run(["git", "add", "."], cwd=proj, capture_output=True)
        subprocess.run(["git", "config", "user.name", "bob"], cwd=proj, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bob@company.com"], cwd=proj, capture_output=True)
        subprocess.run(["git", "commit", "-m", "bob adds test"], cwd=proj, capture_output=True)

        _index_project(proj, monkeypatch)
        _make_unstaged_change(proj, "tests/test_auth.py", "def test_login():\n    assert True\n    assert 1\n")

        result = invoke_cli(cli_runner, ["blame-reviewers"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Only a test file changed -> no blame authors from non-test files.
        assert data["reviewers"] == []
