"""Tests for ``roam hooks`` command group.

Covers:
1. install creates hook files (post-merge, post-checkout, post-rewrite)
2. Hook files contain the roam init command
3. Hook files are executable on non-Windows
4. Uninstall removes hooks (or the roam section)
5. Status shows installed state
6. Append mode does not clobber existing hook content
7. Force mode overwrites existing roam section
8. Works when .git/hooks directory does not exist yet
9. JSON output on install/uninstall/status
10. No git repo produces a graceful error
11. Idempotent: re-installing without --force skips already-installed hooks
12. Uninstall when roam was not installed returns not-installed
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from roam.cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOOK_NAMES = ("post-merge", "post-checkout", "post-rewrite")

_ROAM_MARKER = "roam-code auto-indexing"


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo structure (just the .git/hooks dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "hooks").mkdir()
    return repo


def _invoke(repo: Path, *args: str, json_mode: bool = False):
    """Run `roam hooks <args>` inside *repo* via CliRunner."""
    runner = CliRunner()
    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("hooks")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# 1. Install creates hook files
# ---------------------------------------------------------------------------


class TestInstallCreatesHooks:
    def test_all_three_hooks_created(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "install")
        assert result.exit_code == 0

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            assert (hooks_dir / name).exists(), f"Hook {name} was not created"

    def test_verdict_mentions_installed(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "install")
        assert "VERDICT:" in result.output
        # Should mention installed hooks
        assert "installed" in result.output.lower() or "Install" in result.output


# ---------------------------------------------------------------------------
# 2. Hook files contain the roam command
# ---------------------------------------------------------------------------


class TestHookContent:
    def test_hooks_contain_roam_command(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert "roam" in content, f"Hook {name} does not contain 'roam'"

    def test_hooks_contain_marker(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert _ROAM_MARKER in content, (
                f"Hook {name} does not contain roam marker"
            )

    def test_hooks_have_shebang(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert content.startswith("#!/bin/sh"), (
                f"Hook {name} does not start with #!/bin/sh"
            )

    def test_hooks_run_in_background(self, tmp_path):
        """Hook command should be backgrounded (ends with &) so git isn't blocked."""
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert "&" in content, (
                f"Hook {name} does not background the roam command"
            )

    def test_hooks_check_roam_available(self, tmp_path):
        """Hook should guard with 'command -v roam' for graceful degradation."""
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert "command -v roam" in content, (
                f"Hook {name} does not check for roam availability"
            )


# ---------------------------------------------------------------------------
# 3. Hook files are executable (non-Windows only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
class TestHookExecutable:
    def test_hooks_are_executable(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            hook_path = hooks_dir / name
            mode = hook_path.stat().st_mode
            assert mode & stat.S_IXUSR, (
                f"Hook {name} is not executable by owner"
            )


# ---------------------------------------------------------------------------
# 4. Uninstall removes hooks (or the roam section)
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_standalone_hooks(self, tmp_path):
        """Hooks that were purely created by roam should be deleted on uninstall."""
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "uninstall")
        assert result.exit_code == 0

        hooks_dir = repo / ".git" / "hooks"
        for name in _HOOK_NAMES:
            hook_path = hooks_dir / name
            # Either the file was deleted, or if it still exists, the marker is gone
            if hook_path.exists():
                content = hook_path.read_text(encoding="utf-8")
                assert _ROAM_MARKER not in content, (
                    f"Roam marker still present in {name} after uninstall"
                )

    def test_uninstall_removes_roam_section_from_shared_hook(self, tmp_path):
        """When a hook already existed, uninstall only strips the roam section."""
        repo = _make_git_repo(tmp_path)
        hooks_dir = repo / ".git" / "hooks"

        # Write a pre-existing hook
        existing_content = "#!/bin/sh\n# My existing hook\necho 'existing'\n"
        (hooks_dir / "post-merge").write_text(existing_content, encoding="utf-8")

        # Install appends roam section
        _invoke(repo, "install")
        content_after_install = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        assert "existing" in content_after_install
        assert _ROAM_MARKER in content_after_install

        # Uninstall
        _invoke(repo, "uninstall")
        content_after_uninstall = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        assert "existing" in content_after_uninstall, "Existing hook content was destroyed"
        assert _ROAM_MARKER not in content_after_uninstall

    def test_uninstall_verdict(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "uninstall")
        assert "VERDICT:" in result.output
        assert "removed" in result.output.lower() or "Removed" in result.output

    def test_uninstall_when_not_installed(self, tmp_path):
        """Uninstalling without installing first should not error."""
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "uninstall")
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Should say nothing was found to remove
        assert (
            "not" in result.output.lower()
            or "no roam" in result.output.lower()
            or "not-installed" in result.output.lower()
        )


# ---------------------------------------------------------------------------
# 5. Status shows installed state
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_before_install(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "status")
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Nothing installed yet
        assert "0/" in result.output or "No roam hooks" in result.output or "not installed" in result.output.lower()

    def test_status_after_install(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "status")
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # All installed
        assert (
            "3/3" in result.output
            or "All 3" in result.output
            or "installed" in result.output.lower()
        )

    def test_status_shows_hook_names(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "status")
        assert result.exit_code == 0
        for name in _HOOK_NAMES:
            assert name in result.output, f"Hook name '{name}' not shown in status"

    def test_status_after_uninstall(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        _invoke(repo, "uninstall")
        result = _invoke(repo, "status")
        assert result.exit_code == 0
        # Should reflect no hooks installed
        assert (
            "0/" in result.output
            or "No roam hooks" in result.output
            or "not installed" in result.output.lower()
            or "missing" in result.output.lower()
        )


# ---------------------------------------------------------------------------
# 6. Append mode does not clobber existing hooks
# ---------------------------------------------------------------------------


class TestAppendMode:
    def test_existing_hook_content_preserved(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        hooks_dir = repo / ".git" / "hooks"

        for name in _HOOK_NAMES:
            (hooks_dir / name).write_text(
                f"#!/bin/sh\n# custom hook for {name}\necho 'custom'\n",
                encoding="utf-8",
            )

        result = _invoke(repo, "install")
        assert result.exit_code == 0

        for name in _HOOK_NAMES:
            content = (hooks_dir / name).read_text(encoding="utf-8")
            assert "custom" in content, f"Custom content lost in {name}"
            assert _ROAM_MARKER in content, f"Roam section not added to {name}"
            assert "echo 'custom'" in content, f"Custom echo lost in {name}"

    def test_append_reported_as_appended(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        hooks_dir = repo / ".git" / "hooks"
        (hooks_dir / "post-merge").write_text("#!/bin/sh\necho 'hi'\n", encoding="utf-8")

        result = _invoke(repo, "install", json_mode=True)
        data = json.loads(result.output)
        # "post-merge" should be in installed (appended)
        assert "post-merge" in data["summary"]["installed"]

    def test_second_install_skips_without_force(self, tmp_path):
        """Re-running install without --force should skip already-installed hooks."""
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "install")
        assert result.exit_code == 0
        data_text = result.output
        # Should say skipped or already installed
        assert "skipped" in data_text.lower() or "already" in data_text.lower()

    def test_second_install_skips_in_json(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "install", json_mode=True)
        data = json.loads(result.output)
        # All 3 should be in skipped
        assert len(data["summary"]["skipped"]) == len(_HOOK_NAMES)
        assert len(data["summary"]["installed"]) == 0


# ---------------------------------------------------------------------------
# 7. Force mode overwrites existing roam section
# ---------------------------------------------------------------------------


class TestForceMode:
    def test_force_overwrites_existing_section(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")

        # Manually corrupt one hook's roam section
        hooks_dir = repo / ".git" / "hooks"
        content = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        # Simulate stale/wrong content in the section
        corrupted = content.replace("roam index", "roam WRONG_COMMAND")
        (hooks_dir / "post-merge").write_text(corrupted, encoding="utf-8")

        result = _invoke(repo, "install", "--force")
        assert result.exit_code == 0

        updated = (hooks_dir / "post-merge").read_text(encoding="utf-8")
        assert "WRONG_COMMAND" not in updated, "Force did not overwrite stale section"
        assert "roam index" in updated

    def test_force_reported_as_overwritten(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "install", "--force", json_mode=True)
        data = json.loads(result.output)
        # All 3 should be in installed (overwritten)
        assert len(data["summary"]["installed"]) == len(_HOOK_NAMES)
        assert len(data["summary"]["skipped"]) == 0


# ---------------------------------------------------------------------------
# 8. Works when .git/hooks does not exist yet
# ---------------------------------------------------------------------------


class TestHooksDirMissing:
    def test_install_creates_hooks_dir(self, tmp_path):
        """If .git/hooks doesn't exist, install should create it."""
        repo = tmp_path / "repo2"
        repo.mkdir()
        (repo / ".git").mkdir()
        # No hooks dir

        result = _invoke(repo, "install")
        assert result.exit_code == 0
        hooks_dir = repo / ".git" / "hooks"
        assert hooks_dir.is_dir(), ".git/hooks was not created"
        for name in _HOOK_NAMES:
            assert (hooks_dir / name).exists(), f"Hook {name} not created"


# ---------------------------------------------------------------------------
# 9. JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_install_json_envelope(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "install", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Standard envelope fields
        assert data["command"] == "hooks"
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "installed" in data["summary"]
        assert "skipped" in data["summary"]
        assert "errors" in data["summary"]
        assert "schema" in data
        assert "schema_version" in data
        assert "version" in data

    def test_uninstall_json_envelope(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "uninstall", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "hooks"
        assert "verdict" in data["summary"]
        assert "removed" in data["summary"]
        assert "not_installed" in data["summary"]
        assert "errors" in data["summary"]

    def test_status_json_envelope(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "status", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["command"] == "hooks"
        assert "verdict" in data["summary"]
        assert "installed_count" in data["summary"]
        assert "total_hooks" in data["summary"]
        assert "all_installed" in data["summary"]
        # hooks array should have one entry per hook
        assert "hooks" in data
        assert len(data["hooks"]) == len(_HOOK_NAMES)

    def test_install_json_installed_list(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "install", json_mode=True)
        data = json.loads(result.output)
        # 3 hooks installed on first run
        assert len(data["summary"]["installed"]) == len(_HOOK_NAMES)
        assert len(data["summary"]["skipped"]) == 0
        assert len(data["summary"]["errors"]) == 0

    def test_status_json_installed_false_before_install(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "status", json_mode=True)
        data = json.loads(result.output)
        for hook_entry in data["hooks"]:
            assert hook_entry["installed"] is False

    def test_status_json_installed_true_after_install(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        _invoke(repo, "install")
        result = _invoke(repo, "status", json_mode=True)
        data = json.loads(result.output)
        for hook_entry in data["hooks"]:
            assert hook_entry["installed"] is True
        assert data["summary"]["all_installed"] is True
        assert data["summary"]["installed_count"] == len(_HOOK_NAMES)

    def test_status_json_has_hook_names(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = _invoke(repo, "status", json_mode=True)
        data = json.loads(result.output)
        hook_names_in_output = {h["hook"] for h in data["hooks"]}
        assert set(_HOOK_NAMES) == hook_names_in_output


# ---------------------------------------------------------------------------
# 10. No git repo produces graceful error
# ---------------------------------------------------------------------------


class TestNoGitRepo:
    def test_install_no_git_repo(self, tmp_path):
        """install in a non-git directory should exit non-zero with a message."""
        empty = tmp_path / "norepo"
        empty.mkdir()
        # Patch _find_git_hooks_dir to return None (no git repo)
        from roam.commands import cmd_hooks

        with patch.object(cmd_hooks, "_find_git_hooks_dir", return_value=None):
            result = _invoke(empty, "install")
        assert result.exit_code != 0
        assert "VERDICT:" in result.output
        assert "git" in result.output.lower() or "repository" in result.output.lower()

    def test_install_no_git_repo_json(self, tmp_path):
        empty = tmp_path / "norepo2"
        empty.mkdir()
        from roam.commands import cmd_hooks

        with patch.object(cmd_hooks, "_find_git_hooks_dir", return_value=None):
            result = _invoke(empty, "install", json_mode=True)
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "verdict" in data["summary"]

    def test_status_no_git_repo(self, tmp_path):
        empty = tmp_path / "norepo3"
        empty.mkdir()
        from roam.commands import cmd_hooks

        with patch.object(cmd_hooks, "_find_git_hooks_dir", return_value=None):
            result = _invoke(empty, "status")
        assert result.exit_code == 0  # status is informational, not an error
        assert "VERDICT:" in result.output


# ---------------------------------------------------------------------------
# 11. Internal helper unit tests
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_roam_section_present_true(self):
        from roam.commands.cmd_hooks import _roam_section_present, _MARKER_BEGIN, _MARKER_END

        content = f"#!/bin/sh\n{_MARKER_BEGIN}\nroam index\n{_MARKER_END}\n"
        assert _roam_section_present(content) is True

    def test_roam_section_present_false(self):
        from roam.commands.cmd_hooks import _roam_section_present

        content = "#!/bin/sh\necho 'hello'\n"
        assert _roam_section_present(content) is False

    def test_remove_roam_section_removes_block(self):
        from roam.commands.cmd_hooks import (
            _remove_roam_section, _MARKER_BEGIN, _MARKER_END, _ROAM_HOOK_BODY
        )

        original = (
            "#!/bin/sh\n"
            "# my custom hook\n"
            f"\n{_MARKER_BEGIN}\n{_ROAM_HOOK_BODY}\n{_MARKER_END}\n"
        )
        result = _remove_roam_section(original)
        assert _MARKER_BEGIN not in result
        assert _MARKER_END not in result
        assert "my custom hook" in result

    def test_remove_roam_section_noop_if_not_present(self):
        from roam.commands.cmd_hooks import _remove_roam_section

        content = "#!/bin/sh\necho 'hi'\n"
        result = _remove_roam_section(content)
        assert result == content

    def test_insert_roam_section_adds_markers(self):
        from roam.commands.cmd_hooks import (
            _insert_roam_section, _MARKER_BEGIN, _MARKER_END
        )

        content = "#!/bin/sh\necho 'existing'\n"
        result = _insert_roam_section(content)
        assert _MARKER_BEGIN in result
        assert _MARKER_END in result
        assert "existing" in result

    def test_roundtrip_insert_then_remove(self):
        """Insert then remove should return content close to the original."""
        from roam.commands.cmd_hooks import _insert_roam_section, _remove_roam_section

        original = "#!/bin/sh\n# custom\necho 'hi'\n"
        with_section = _insert_roam_section(original)
        restored = _remove_roam_section(with_section)
        # Original content should be present after removal
        assert "#!/bin/sh" in restored
        assert "custom" in restored
        assert "echo 'hi'" in restored

    def test_install_hook_creates_file(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook, _MARKER_BEGIN

        hook_path = tmp_path / "post-merge"
        action, error = _install_hook(hook_path, force=False)
        assert error is None
        assert action == "created"
        assert hook_path.exists()
        content = hook_path.read_text(encoding="utf-8")
        assert _MARKER_BEGIN in content

    def test_install_hook_appends_to_existing(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook, _MARKER_BEGIN

        hook_path = tmp_path / "post-merge"
        hook_path.write_text("#!/bin/sh\necho 'custom'\n", encoding="utf-8")
        action, error = _install_hook(hook_path, force=False)
        assert error is None
        assert action == "appended"
        content = hook_path.read_text(encoding="utf-8")
        assert "custom" in content
        assert _MARKER_BEGIN in content

    def test_install_hook_skips_if_already_installed(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook

        hook_path = tmp_path / "post-merge"
        _install_hook(hook_path, force=False)
        action, error = _install_hook(hook_path, force=False)
        assert error is None
        assert action == "skipped"

    def test_install_hook_force_overwrites(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook

        hook_path = tmp_path / "post-merge"
        _install_hook(hook_path, force=False)
        action, error = _install_hook(hook_path, force=True)
        assert error is None
        assert action == "overwritten"

    def test_uninstall_hook_deletes_standalone(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook, _uninstall_hook

        hook_path = tmp_path / "post-merge"
        _install_hook(hook_path, force=False)
        action, error = _uninstall_hook(hook_path)
        assert error is None
        assert action in ("removed", "deleted")

    def test_uninstall_hook_not_installed(self, tmp_path):
        from roam.commands.cmd_hooks import _uninstall_hook

        hook_path = tmp_path / "post-merge"
        action, error = _uninstall_hook(hook_path)
        assert error is None
        assert action == "not-installed"

    def test_uninstall_hook_preserves_other_content(self, tmp_path):
        from roam.commands.cmd_hooks import _install_hook, _uninstall_hook, _MARKER_BEGIN

        hook_path = tmp_path / "post-merge"
        hook_path.write_text("#!/bin/sh\n# keep me\necho 'preserve'\n", encoding="utf-8")
        _install_hook(hook_path, force=False)
        action, error = _uninstall_hook(hook_path)
        assert error is None
        assert hook_path.exists(), "File should not be deleted when other content exists"
        content = hook_path.read_text(encoding="utf-8")
        assert "keep me" in content
        assert "preserve" in content
        assert _MARKER_BEGIN not in content
