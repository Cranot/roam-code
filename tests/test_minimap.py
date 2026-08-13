"""Tests for roam minimap command."""

from __future__ import annotations

import os
import re
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal Python project and index it."""
    (tmp_path / ".git").mkdir()

    src = tmp_path / "src"
    src.mkdir()

    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "class Config:\n    pass\n\ndef load_config():\n    return Config()\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# My Project\n", encoding="utf-8")

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        pass  # just need the structure

    # Index the project
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(cli, ["index"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests for tree building
# ---------------------------------------------------------------------------


class TestTreeBuilding:
    def test_build_tree_flat(self):
        from roam.commands.cmd_minimap import _build_tree

        paths = ["a.py", "b.py", "c.py"]
        tree = _build_tree(paths)
        assert "a.py" in tree
        assert "b.py" in tree

    def test_build_tree_nested(self):
        from roam.commands.cmd_minimap import _build_tree

        paths = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        tree = _build_tree(paths)
        assert "src" in tree
        assert isinstance(tree["src"], dict)
        assert "main.py" in tree["src"]

    def test_build_tree_backslash_paths(self):
        from roam.commands.cmd_minimap import _build_tree

        paths = ["src\\main.py", "src\\utils.py"]
        tree = _build_tree(paths)
        assert "src" in tree
        assert "main.py" in tree["src"]

    def test_render_tree_basic(self):
        from roam.commands.cmd_minimap import _build_tree, _render_tree

        paths = ["src/main.py", "src/utils.py"]
        tree = _build_tree(paths)
        lines = _render_tree(tree, {})
        assert any("src/" in line for line in lines)
        assert any("main.py" in line for line in lines)

    def test_render_tree_annotations(self):
        from roam.commands.cmd_minimap import _build_tree, _render_tree

        paths = ["src/main.py"]
        annotations = {"src/main.py": "do_thing, helper"}
        tree = _build_tree(paths)
        lines = _render_tree(tree, annotations)
        combined = "\n".join(lines)
        assert "do_thing" in combined or "helper" in combined

    def test_render_tree_collapses_large_dirs(self):
        from roam.commands.cmd_minimap import _build_tree, _render_tree

        # 10 files in a sub-directory → should be collapsed
        paths = [f"commands/cmd_{i}.py" for i in range(10)]
        tree = _build_tree(paths)
        lines = _render_tree(tree, {})
        combined = "\n".join(lines)
        # Should show collapsed form "(10 files)" or show individual files ≤ 6 + "more"
        assert "commands" in combined

    def test_render_tree_caps_file_list(self):
        from roam.commands.cmd_minimap import _build_tree, _render_tree

        # 12 files in root → show first 6 + "... (6 more)"
        paths = [f"file_{i}.py" for i in range(12)]
        tree = _build_tree(paths)
        lines = _render_tree(tree, {})
        assert any("more" in line for line in lines)

    def test_count_files_in_tree(self):
        from roam.commands.cmd_minimap import _build_tree, _count_files_in_tree

        paths = ["a/b/c.py", "a/d.py", "e.py"]
        tree = _build_tree(paths)
        assert _count_files_in_tree(tree) == 3

    def test_best_dir_hint(self):
        from roam.commands.cmd_minimap import _best_dir_hint, _build_tree

        paths = ["src/utils.py", "src/main.py"]
        ann = {"src/main.py": "do_main"}
        tree = _build_tree(paths)
        hint = _best_dir_hint(tree["src"], ann)
        assert hint == "do_main"


# ---------------------------------------------------------------------------
# Unit tests for sentinel helpers
# ---------------------------------------------------------------------------


class TestSentinelHelpers:
    def test_wrap_sentinels_structure(self):
        from roam.commands.cmd_minimap import _wrap_sentinels

        block = _wrap_sentinels("hello world")
        assert "<!-- roam:minimap" in block
        assert "<!-- /roam:minimap -->" in block
        assert "hello world" in block

    def test_wrap_sentinels_has_date(self):
        from datetime import date

        from roam.commands.cmd_minimap import _wrap_sentinels

        block = _wrap_sentinels("content")
        today = date.today().isoformat()
        assert today in block

    def test_upsert_file_creates_new(self, tmp_path):
        from roam.commands.cmd_minimap import _upsert_file

        target = tmp_path / "CLAUDE.md"
        verb = _upsert_file(target, "<!-- roam:minimap -->\ncontent\n<!-- /roam:minimap -->")
        assert verb == "Created"
        assert target.exists()
        assert "content" in target.read_text()

    def test_upsert_file_replaces_sentinel(self, tmp_path):
        from roam.commands.cmd_minimap import _upsert_file

        target = tmp_path / "CLAUDE.md"
        target.write_text(
            "# Header\n\n<!-- roam:minimap generated=2024-01-01 -->\nold content\n<!-- /roam:minimap -->\n\n# Footer\n",
            encoding="utf-8",
        )
        verb = _upsert_file(
            target,
            "<!-- roam:minimap generated=2025-01-01 -->\nnew content\n<!-- /roam:minimap -->",
        )
        assert verb == "Updated"
        text = target.read_text()
        assert "new content" in text
        assert "old content" not in text
        assert "# Header" in text
        assert "# Footer" in text

    def test_upsert_file_appends_when_no_sentinel(self, tmp_path):
        from roam.commands.cmd_minimap import _upsert_file

        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing content\n", encoding="utf-8")
        verb = _upsert_file(target, "<!-- roam:minimap -->\ncontent\n<!-- /roam:minimap -->")
        assert verb == "Appended to"
        text = target.read_text()
        assert "# Existing content" in text
        assert "content" in text


# ---------------------------------------------------------------------------
# Integration tests via CLI
# ---------------------------------------------------------------------------


class TestMinimapCLI:
    def test_init_notes_creates_file(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".roam").mkdir()
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(cli, ["minimap", "--init-notes"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert (tmp_path / ".roam" / "minimap-notes.md").exists()
        assert "Created" in result.output

    def test_init_notes_idempotent(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".roam").mkdir()
        notes = tmp_path / ".roam" / "minimap-notes.md"
        notes.write_text("# existing\n", encoding="utf-8")
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(cli, ["minimap", "--init-notes"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Already exists" in result.output
        # Original content should be preserved
        assert notes.read_text() == "# existing\n"

    def test_stdout_output(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        output = result.output
        # Should have sentinel markers
        assert "<!-- roam:minimap" in output
        assert "<!-- /roam:minimap -->" in output
        # Should have some tree content
        assert "```" in output

    def test_stdout_has_stack(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert "Stack" in result.output or "python" in result.output.lower()

    def test_update_creates_claude_md(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        claude_md = proj / "CLAUDE.md"
        assert claude_md.exists()
        text = claude_md.read_text()
        assert "<!-- roam:minimap" in text
        assert "<!-- /roam:minimap -->" in text

    def test_update_replaces_existing_sentinel(self, tmp_path):
        proj = _make_project(tmp_path)
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n<!-- roam:minimap generated=2020-01-01 -->\nold\n<!-- /roam:minimap -->\n\n# End\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        text = claude_md.read_text()
        assert "old" not in text
        assert "# Project" in text
        assert "# End" in text
        assert "<!-- roam:minimap" in text

    def test_output_flag(self, tmp_path):
        proj = _make_project(tmp_path)
        target = proj / "AGENTS.md"
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "-o", "AGENTS.md"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert target.exists()
        assert "<!-- roam:minimap" in target.read_text()

    def test_json_mode_stdout(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["--json", "minimap"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data["command"] == "minimap"
        # W17.3 (LAW 4): the stdout-mode verdict names the rendered block
        # + size cue + the follow-up command, not bare "ok".
        verdict = data["summary"]["verdict"]
        assert "minimap rendered" in verdict
        assert "--update-claude" in verdict
        assert "content" in data

    def test_json_mode_update(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["--json", "minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        # W17.3 (LAW 4): the --update verdict names the action and the target
        # file ("minimap created/updated in <path>"), not bare "ok".
        verdict = data["summary"]["verdict"]
        assert verdict.startswith("minimap ")
        assert "CLAUDE.md" in verdict
        assert "file" in data

    def test_project_notes_included(self, tmp_path):
        proj = _make_project(tmp_path)
        notes_dir = proj / ".roam"
        notes_dir.mkdir(exist_ok=True)
        (notes_dir / "minimap-notes.md").write_text(
            "# Notes\n\n- Never use raw SQL strings\n- Always call ensure_index first\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Never use raw SQL strings" in result.output
        assert "Always call ensure_index first" in result.output

    def test_conventions_detected(self, tmp_path):
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Conventions" in result.output

    def test_notes_migration_happy_path(self, tmp_path):
        """Pre-mechanism block with hand-written notes: notes file created,
        content survives the update, disclosure emitted."""
        proj = _make_project(tmp_path)
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n"
            "<!-- roam:minimap generated=2020-01-01 -->\n"
            "**Stack:** Python\n\n"
            "**Project notes:**\n"
            "- Gotcha: never call frobnicate() without a lock\n"
            "- The scheduler is UTC-only\n"
            "<!-- /roam:minimap -->\n\n# End\n",
            encoding="utf-8",
        )
        notes_path = proj / ".roam" / "minimap-notes.md"
        assert not notes_path.exists()
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        # Notes file created with the hand-written content
        assert notes_path.exists()
        notes_text = notes_path.read_text(encoding="utf-8")
        assert "never call frobnicate() without a lock" in notes_text
        assert "The scheduler is UTC-only" in notes_text
        # Content survives the update: re-injected into the regenerated block
        text = claude_md.read_text(encoding="utf-8")
        assert "never call frobnicate() without a lock" in text
        assert "The scheduler is UTC-only" in text
        assert "# Project" in text
        assert "# End" in text
        # Disclosure emitted in text output
        assert "Migrated in-block project notes" in result.output

    def test_notes_migration_json_disclosure(self, tmp_path):
        """JSON envelope carries the migration disclosure (summary + top-level)."""
        proj = _make_project(tmp_path)
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text(
            "<!-- roam:minimap generated=2020-01-01 -->\n"
            "**Project notes:**\n"
            "- Hand-written gotcha survives\n"
            "<!-- /roam:minimap -->\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["--json", "minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert data["summary"]["notes_migration"]["action"] == "migrated"
        assert data["notes_migration"]["action"] == "migrated"
        assert data["notes_migration"]["line_count"] == 1
        assert "Hand-written gotcha survives" in claude_md.read_text(encoding="utf-8")

    def test_notes_file_precedence_no_merge(self, tmp_path):
        """Notes file exists with different content: it stays authoritative,
        no guessed merge, in-block section disclosed as superseded."""
        proj = _make_project(tmp_path)
        notes_dir = proj / ".roam"
        notes_dir.mkdir(exist_ok=True)
        notes_path = notes_dir / "minimap-notes.md"
        notes_path.write_text("# Notes\n\n- Authoritative file note\n", encoding="utf-8")
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text(
            "<!-- roam:minimap generated=2020-01-01 -->\n"
            "**Project notes:**\n"
            "- Divergent in-block note\n"
            "<!-- /roam:minimap -->\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        # Notes file untouched (authoritative, no merge)
        assert notes_path.read_text(encoding="utf-8") == "# Notes\n\n- Authoritative file note\n"
        text = claude_md.read_text(encoding="utf-8")
        assert "Authoritative file note" in text
        assert "Divergent in-block note" not in text
        # Superseded disclosure emitted
        assert "superseded" in result.output

    def test_steady_state_no_disclosure(self, tmp_path):
        """Second --update with a notes file: in-block section mirrors the
        file, so no migration/superseded noise and no notes-file rewrite."""
        proj = _make_project(tmp_path)
        notes_dir = proj / ".roam"
        notes_dir.mkdir(exist_ok=True)
        notes_path = notes_dir / "minimap-notes.md"
        notes_path.write_text("# Notes\n\n- Steady note\n", encoding="utf-8")
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Migrated" not in result.output
        assert "superseded" not in result.output
        assert notes_path.read_text(encoding="utf-8") == "# Notes\n\n- Steady note\n"
        assert "Steady note" in (proj / "CLAUDE.md").read_text(encoding="utf-8")

    def test_no_notes_block_no_migration(self, tmp_path):
        """A block without a Project-notes section behaves exactly as before:
        no notes file created, no disclosure."""
        proj = _make_project(tmp_path)
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text(
            "<!-- roam:minimap generated=2020-01-01 -->\nold\n<!-- /roam:minimap -->\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert not (proj / ".roam" / "minimap-notes.md").exists()
        assert "Migrated" not in result.output
        assert "superseded" not in result.output
        assert "old" not in claude_md.read_text(encoding="utf-8")

    def test_migration_failure_refuses_update(self, tmp_path, monkeypatch):
        """If preservation raises, the target is left untouched -- proceeding
        would be exactly the silent loss the migration exists to prevent."""
        proj = _make_project(tmp_path)
        claude_md = proj / "CLAUDE.md"
        original = (
            "<!-- roam:minimap generated=2020-01-01 -->\n"
            "**Project notes:**\n"
            "- Load-bearing gotcha\n"
            "<!-- /roam:minimap -->\n"
        )
        claude_md.write_text(original, encoding="utf-8")

        import roam.commands.cmd_minimap as mod

        def _boom(target, root):
            raise OSError("disk on fire")

        monkeypatch.setattr(mod, "_migrate_inblock_notes", _boom)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            result = runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        # Target byte-identical: nothing destroyed
        assert claude_md.read_text(encoding="utf-8") == original
        assert "Refused" in result.output

    def test_idempotent_update(self, tmp_path):
        """Running --update twice should produce consistent output."""
        proj = _make_project(tmp_path)
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(proj)
        try:
            runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
            first = (proj / "CLAUDE.md").read_text()
            runner.invoke(cli, ["minimap", "--update"], catch_exceptions=False)
            second = (proj / "CLAUDE.md").read_text()
        finally:
            os.chdir(old_cwd)
        # Structure should be identical (date may differ but sentinel markers present)
        first_block = re.search(r"<!-- roam:minimap.*?<!-- /roam:minimap -->", first, re.DOTALL)
        second_block = re.search(r"<!-- roam:minimap.*?<!-- /roam:minimap -->", second, re.DOTALL)
        assert first_block is not None
        assert second_block is not None
        # Only one sentinel block should exist
        assert second.count("<!-- /roam:minimap -->") == 1
