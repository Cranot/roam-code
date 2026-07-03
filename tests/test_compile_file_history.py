"""W-HIST — file_history procedure (2026-06-09).

Telemetry-driven: "what changed in src/roam/cli.py recently / last week"
leaked to freeform_explore@0.45 with a 15KB envelope of skeleton+grep noise.
The dedicated procedure routes at 0.85+ and embeds ONLY the git log (or an
explicit no-history answer), so the agent answers without running git.
"""

from __future__ import annotations

import subprocess

import pytest

from roam.plan.compiler import (
    _classifier_confidence,
    _classify,
    _is_file_history,
    _probe_file_history,
)


class TestFileHistoryClassification:
    @pytest.mark.parametrize(
        "task",
        [
            "what changed in src/roam/cli.py recently",
            "what changed in src/roam/cli.py last week",
            "what changed in src/roam/plan/compiler.py recently",
            "recent commits to src/roam/output/formatter.py",
            "who last touched src/roam/db/connection.py",
            "commit history of cmd_verify.py",
        ],
    )
    def test_history_prompts_route_to_file_history(self, task):
        assert _classify(task)[0] == "file_history"

    @pytest.mark.parametrize(
        "task",
        [
            # history verb but NO file target → stays freeform
            "what changed recently",
            "who touched the auth code last month",
            # file target but NO history verb → other procedures
            "what does src/roam/cli.py do",
            "blast radius of compile_plan",
        ],
    )
    def test_non_history_prompts_do_not_route(self, task):
        assert _classify(task)[0] != "file_history"

    def test_confidence_clears_specialized_threshold(self):
        task = "what changed in src/roam/cli.py recently"
        conf = _classifier_confidence(task, "file_history")
        assert conf >= 0.80  # _PER_PROCEDURE_CONF_THRESHOLD["file_history"]

    def test_is_file_history_requires_both_signals(self):
        assert _is_file_history("what changed in src/roam/cli.py recently")
        assert not _is_file_history("what changed recently")
        assert not _is_file_history("describe src/roam/cli.py")


class TestFileHistoryProbe:
    def test_probe_embeds_commits_for_tracked_file(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-q", "-m", "seed"],
            cwd=tmp_path,
            check=True,
        )
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        subprocess.run(["git", "add", "mod.py"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add mod"],
            cwd=tmp_path,
            check=True,
        )
        facts = _probe_file_history(["mod.py"], str(tmp_path), task="what changed in mod.py recently")
        assert facts and "file_recent_commits" in facts
        assert any("add mod" in line for line in facts["file_recent_commits"])
        assert "do NOT run `git log`" in facts["file_recent_commits_definition"]

    def test_probe_explicit_unavailable_for_untracked(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        facts = _probe_file_history(["ghost.py"], str(tmp_path), task="what changed in ghost.py")
        assert facts and "file_history_unavailable" in facts

    def test_probe_honors_time_window(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        f = tmp_path / "old.py"
        f.write_text("x = 1\n")
        subprocess.run(["git", "add", "old.py"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-q",
                "-m",
                "ancient",
                "--date",
                "2020-01-01T00:00:00",
            ],
            cwd=tmp_path,
            check=True,
            env={"GIT_COMMITTER_DATE": "2020-01-01T00:00:00", "PATH": "/usr/bin:/bin"},
        )
        facts = _probe_file_history(["old.py"], str(tmp_path), task="what changed in old.py last week")
        # the only commit predates the window → explicit unavailable answer
        assert facts and "file_history_unavailable" in facts
        assert "since 1 week ago" in facts["file_history_unavailable"]

    def test_probe_none_without_named_paths(self):
        assert _probe_file_history([], ".", task="what changed") is None

    @pytest.mark.parametrize(
        "magic",
        [
            "*",  # glob: would match every file without the literal guard
            ":",  # bare magic prefix
            ":/",  # top-of-tree magic
            ":(glob)*.py",  # explicit glob magic
        ],
    )
    def test_probe_treats_magic_pathspec_literally(self, tmp_path, magic):
        """A magic pathspec target must NOT broaden the match to other files.

        Without `--literal-pathspecs`, `git log -- '*'` reports the history of
        every file. The literal guard makes the target a literal filename, so a
        magic target finds no commits and the probe emits
        `file_history_unavailable` instead of leaking other files' history.
        """
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        # Two tracked files with real history the magic target must not surface.
        for name in ("a.py", "b.py"):
            (tmp_path / name).write_text("x = 1\n")
            subprocess.run(["git", "add", name], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", f"add {name}"],
                cwd=tmp_path,
                check=True,
            )
        facts = _probe_file_history([magic], str(tmp_path), task=f"what changed in {magic} recently")
        # The magic target names no literal file → unavailable, never the
        # cross-file history a glob would have leaked.
        assert facts and "file_history_unavailable" in facts
        assert "file_recent_commits" not in facts
