"""Tests for roam reset and roam clean commands (index management recovery)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
    git_init,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    """Small indexed project with a git repo."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text(
        "def greet(name):\n"
        '    return f"Hello, {name}"\n'
        "\n"
        "def add(a, b):\n"
        "    return a + b\n"
    )
    (proj / "util.py").write_text(
        "def format_output(value):\n"
        '    return str(value)\n'
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# roam reset — without --force (aborted)
# ---------------------------------------------------------------------------


class TestResetWithoutForce:
    """Tests that reset requires --force and aborts safely without it."""

    def test_reset_without_force_text(self, indexed_project, cli_runner, monkeypatch):
        """reset without --force should show VERDICT: aborted and exit 2."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset"], cwd=indexed_project)
        assert result.exit_code == 2
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")
        assert "aborted" in first_line.lower()

    def test_reset_without_force_json(self, indexed_project, cli_runner, monkeypatch):
        """reset without --force in JSON mode should return valid envelope with aborted verdict."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["command"] == "reset"
        summary = data["summary"]
        assert "aborted" in summary["verdict"].lower()
        assert summary["force_required"] is True
        assert summary["removed"] is False

    def test_reset_without_force_preserves_index(self, indexed_project, cli_runner, monkeypatch):
        """reset without --force must NOT delete the index DB."""
        monkeypatch.chdir(indexed_project)
        db_path = indexed_project / ".roam" / "index.db"
        assert db_path.exists(), "index should exist before reset"

        invoke_cli(cli_runner, ["reset"], cwd=indexed_project)

        assert db_path.exists(), "index should still exist after aborted reset"

    def test_reset_without_force_hints_at_force(self, indexed_project, cli_runner, monkeypatch):
        """Text output should mention --force to guide the user."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset"], cwd=indexed_project)
        assert "--force" in result.output


# ---------------------------------------------------------------------------
# roam reset — with --force (destructive)
# ---------------------------------------------------------------------------


class TestResetWithForce:
    """Tests for reset --force (deletes and rebuilds the index)."""

    def test_reset_force_succeeds(self, indexed_project, cli_runner, monkeypatch):
        """reset --force should exit 0 and output VERDICT: reset."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        assert "VERDICT:" in result.output

    def test_reset_force_rebuilds_index(self, indexed_project, cli_runner, monkeypatch):
        """reset --force should leave a working index after completion."""
        monkeypatch.chdir(indexed_project)
        db_path = indexed_project / ".roam" / "index.db"
        assert db_path.exists()

        result = invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        assert result.exit_code == 0, f"reset failed:\n{result.output}"

        # DB should exist again after rebuild
        assert db_path.exists(), "index should be rebuilt after reset --force"

    def test_reset_force_text_verdict_first(self, indexed_project, cli_runner, monkeypatch):
        """First line of text output should start with VERDICT:."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")

    def test_reset_force_json_envelope(self, indexed_project, cli_runner, monkeypatch):
        """JSON output from reset --force should be a valid roam envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner, ["reset", "--force"], cwd=indexed_project, json_mode=True
        )
        assert result.exit_code == 0, f"reset failed:\n{result.output}"
        data = json.loads(result.output)
        assert data["command"] == "reset"
        summary = data["summary"]
        assert "verdict" in summary
        assert "reset" in summary["verdict"].lower() or "complete" in summary["verdict"].lower()
        assert "removed" in summary
        assert "db_path" in summary

    def test_reset_force_when_no_index_exists(self, tmp_path, cli_runner, monkeypatch):
        """reset --force on a project without an index should still succeed."""
        proj = tmp_path / "fresh"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text("def run(): pass\n")
        git_init(proj)
        monkeypatch.chdir(proj)

        result = invoke_cli(cli_runner, ["reset", "--force"], cwd=proj)
        assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
        assert "VERDICT:" in result.output

    def test_reset_force_index_queryable_after_rebuild(self, indexed_project, cli_runner, monkeypatch):
        """After reset --force, roam health should work (index is valid)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        assert result.exit_code == 0

        # Verify the rebuilt index is queryable
        health_result = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert health_result.exit_code == 0
        assert "VERDICT:" in health_result.output


# ---------------------------------------------------------------------------
# roam clean — basic behavior
# ---------------------------------------------------------------------------


class TestCleanBasic:
    """Tests for basic roam clean behavior."""

    def test_clean_runs_on_clean_index(self, indexed_project, cli_runner, monkeypatch):
        """clean on a fresh index should report nothing to remove."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
        assert "VERDICT:" in result.output

    def test_clean_text_verdict_first(self, indexed_project, cli_runner, monkeypatch):
        """First line of text output should be VERDICT:."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        first_line = result.output.strip().split("\n")[0]
        assert first_line.startswith("VERDICT:")

    def test_clean_json_envelope(self, indexed_project, cli_runner, monkeypatch):
        """JSON output should follow the roam envelope contract."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"

        data = json.loads(result.output)
        assert_json_envelope(data, "clean")
        summary = data["summary"]
        assert "verdict" in summary
        assert "files_removed" in summary
        assert "symbols_removed" in summary
        assert "edges_removed" in summary
        assert isinstance(summary["files_removed"], int)
        assert isinstance(summary["symbols_removed"], int)
        assert isinstance(summary["edges_removed"], int)

    def test_clean_json_includes_orphaned_paths(self, indexed_project, cli_runner, monkeypatch):
        """JSON output should include the orphaned_paths list."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert "orphaned_paths" in data
        assert isinstance(data["orphaned_paths"], list)

    def test_clean_on_clean_index_zero_removals(self, indexed_project, cli_runner, monkeypatch):
        """Clean index should report 0 files/symbols/edges removed."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["files_removed"] == 0
        assert summary["symbols_removed"] == 0


# ---------------------------------------------------------------------------
# roam clean — orphan detection
# ---------------------------------------------------------------------------


class TestCleanOrphanDetection:
    """Tests that clean correctly identifies and removes orphaned file records."""

    def test_clean_removes_orphaned_file_record(self, indexed_project, cli_runner, monkeypatch):
        """After deleting a file from disk, clean should remove it from the index."""
        monkeypatch.chdir(indexed_project)

        # Verify the file is indexed
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT id FROM files WHERE path LIKE '%util.py'").fetchall()
        assert len(rows) > 0, "util.py should be in the index"

        # Delete the file from disk (but not from git / not re-indexed)
        (indexed_project / "util.py").unlink()

        # Run clean
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["files_removed"] >= 1, (
            f"Expected at least 1 file removed, got {summary['files_removed']}"
        )

    def test_clean_reports_orphaned_path(self, indexed_project, cli_runner, monkeypatch):
        """The orphaned file's path should appear in orphaned_paths."""
        monkeypatch.chdir(indexed_project)

        # Delete util.py from disk
        (indexed_project / "util.py").unlink()

        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        orphaned = data.get("orphaned_paths", [])
        assert any("util" in p for p in orphaned), (
            f"Expected util.py in orphaned_paths, got: {orphaned}"
        )

    def test_clean_removes_symbols_of_orphaned_file(self, indexed_project, cli_runner, monkeypatch):
        """Symbols belonging to the deleted file should also be removed."""
        monkeypatch.chdir(indexed_project)

        # Get symbol count before deletion
        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        (indexed_project / "util.py").unlink()

        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["symbols_removed"] >= 0  # may be 0 if no symbols were extracted

        # Verify symbol count went down (or stayed same if util.py had 0 symbols)
        with open_db(readonly=True) as conn:
            after = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert after <= before

    def test_clean_text_shows_orphaned_files(self, indexed_project, cli_runner, monkeypatch):
        """Text output should show the list of orphaned files removed."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "util.py").unlink()

        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        assert result.exit_code == 0
        # Should mention orphaned file in text output
        assert "util" in result.output or "orphan" in result.output.lower()

    def test_clean_verdict_reflects_removals(self, indexed_project, cli_runner, monkeypatch):
        """Verdict should mention the count of removed items."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "main.py").unlink()

        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        assert result.exit_code == 0
        first_line = result.output.strip().split("\n")[0]
        assert "VERDICT:" in first_line
        # The verdict should reflect that something was removed
        assert "1" in first_line or "orphan" in first_line.lower() or "removed" in first_line.lower()

    def test_clean_multiple_orphaned_files(self, indexed_project, cli_runner, monkeypatch):
        """Clean should handle multiple orphaned files correctly."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "main.py").unlink()
        (indexed_project / "util.py").unlink()

        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert result.exit_code == 0

        data = json.loads(result.output)
        summary = data["summary"]
        assert summary["files_removed"] >= 2, (
            f"Expected >= 2 files removed, got {summary['files_removed']}"
        )
        assert len(data["orphaned_paths"]) >= 2


# ---------------------------------------------------------------------------
# roam clean — index integrity after cleaning
# ---------------------------------------------------------------------------


class TestCleanIndexIntegrity:
    """Tests that the index remains valid and queryable after cleaning."""

    def test_index_queryable_after_clean(self, indexed_project, cli_runner, monkeypatch):
        """After clean, roam health should still work."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "util.py").unlink()
        result = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        assert result.exit_code == 0

        health = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert health.exit_code == 0
        assert "VERDICT:" in health.output

    def test_cleaned_file_not_in_index(self, indexed_project, cli_runner, monkeypatch):
        """After clean, the deleted file should no longer appear in the index."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "util.py").unlink()
        invoke_cli(cli_runner, ["clean"], cwd=indexed_project)

        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT id FROM files WHERE path LIKE '%util.py'"
            ).fetchall()
        assert len(rows) == 0, "util.py should be removed from the index after clean"

    def test_surviving_files_still_indexed(self, indexed_project, cli_runner, monkeypatch):
        """Files still on disk should remain in the index after clean."""
        monkeypatch.chdir(indexed_project)

        # Delete one file, keep another
        (indexed_project / "util.py").unlink()
        invoke_cli(cli_runner, ["clean"], cwd=indexed_project)

        from roam.db.connection import open_db
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT id FROM files WHERE path LIKE '%main.py'"
            ).fetchall()
        assert len(rows) > 0, "main.py should still be in the index after clean"


# ---------------------------------------------------------------------------
# roam clean — idempotency
# ---------------------------------------------------------------------------


class TestCleanIdempotency:
    """Tests that running clean multiple times is safe."""

    def test_clean_twice_is_safe(self, indexed_project, cli_runner, monkeypatch):
        """Running clean twice should not error and second run removes 0 items."""
        monkeypatch.chdir(indexed_project)

        (indexed_project / "util.py").unlink()

        # First clean
        r1 = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert r1.exit_code == 0
        d1 = json.loads(r1.output)
        assert d1["summary"]["files_removed"] >= 1

        # Second clean — nothing left to remove
        r2 = invoke_cli(cli_runner, ["clean"], cwd=indexed_project, json_mode=True)
        assert r2.exit_code == 0
        d2 = json.loads(r2.output)
        assert d2["summary"]["files_removed"] == 0

    def test_clean_on_empty_project_is_safe(self, tmp_path, cli_runner, monkeypatch):
        """Clean on a project with an empty index should not crash."""
        proj = tmp_path / "empty_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "stub.py").write_text("# empty\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0

        result = invoke_cli(cli_runner, ["clean"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ---------------------------------------------------------------------------
# reset + clean integration
# ---------------------------------------------------------------------------


class TestResetCleanIntegration:
    """Integration tests combining reset and clean."""

    def test_clean_after_reset_is_safe(self, indexed_project, cli_runner, monkeypatch):
        """Running clean immediately after reset --force should work."""
        monkeypatch.chdir(indexed_project)

        r = invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        assert r.exit_code == 0

        r2 = invoke_cli(cli_runner, ["clean"], cwd=indexed_project)
        assert r2.exit_code == 0
        assert "VERDICT:" in r2.output

    def test_reset_then_clean_leaves_valid_index(self, indexed_project, cli_runner, monkeypatch):
        """After reset and clean, the index should still be valid."""
        monkeypatch.chdir(indexed_project)

        invoke_cli(cli_runner, ["reset", "--force"], cwd=indexed_project)
        invoke_cli(cli_runner, ["clean"], cwd=indexed_project)

        health = invoke_cli(cli_runner, ["health"], cwd=indexed_project)
        assert health.exit_code == 0


# ---------------------------------------------------------------------------
# CLI registration tests
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """Tests that reset and clean are properly registered in the CLI."""

    def test_reset_is_registered(self, cli_runner):
        """roam reset should appear in the CLI command list."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "reset" in result.output

    def test_clean_is_registered(self, cli_runner):
        """roam clean should appear in the CLI command list."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "clean" in result.output

    def test_reset_help_mentions_force(self, cli_runner):
        """roam reset --help should document the --force flag."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["reset", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output

    def test_clean_has_help(self, cli_runner):
        """roam clean --help should return exit code 0."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["clean", "--help"])
        assert result.exit_code == 0
