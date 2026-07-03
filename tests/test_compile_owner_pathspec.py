"""W109 — owner probe literal-pathspec guard (2026-06-21).

`_probe_owner_for_task` runs `git shortlog -sne HEAD -- <target>`, which
evaluates *target* as a git PATHSPEC. A directory anchor, a glob, or a magic
pathspec would attribute a broader file set than the single file the task
named — conflating owner counts. The guard skips the probe (returns None)
unless the target is one literal file.
"""

from __future__ import annotations

import subprocess

import pytest

from roam.plan.compiler import _probe_owner_for_task


def _seed_repo(tmp_path):
    """Init a git repo with one committed file and return its path str."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")
    subprocess.run(["git", "add", "mod.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=Owner", "commit", "-q", "-m", "add mod"],
        cwd=tmp_path,
        check=True,
    )
    return str(tmp_path)


class TestOwnerProbeLiteralPathspecGuard:
    def test_literal_file_embeds_owners(self, tmp_path):
        cwd = _seed_repo(tmp_path)
        task = "who owns mod.py"
        facts = _probe_owner_for_task(task, ["mod.py"], cwd)
        assert facts and "owners" in facts
        assert facts["owners"]["path"] == "mod.py"
        assert facts["owners"]["top_authors"], "expected at least one author"

    @pytest.mark.parametrize(
        "target",
        [
            "src/commands/",  # directory anchor (trailing slash) — recursive attribution
            "*.py",  # glob
            "*.{py,ts}",  # brace glob
            "src/[a-z].py",  # character class
            ":(glob)**/*.py",  # git magic pathspec
            ":./mod.py",  # git magic pathspec
        ],
    )
    def test_non_literal_pathspec_skips_probe(self, tmp_path, target):
        cwd = _seed_repo(tmp_path)
        task = "who owns mod.py"
        # A directory/glob/magic target must NOT broaden attribution — the
        # probe returns None rather than embedding skewed owner counts.
        assert _probe_owner_for_task(task, [target], cwd) is None

    def test_shortlog_runs_under_literal_pathspecs_env(self, tmp_path, monkeypatch):
        """Belt-and-suspenders: the shortlog subprocess runs with
        GIT_LITERAL_PATHSPECS=1, so git treats `target` as a literal filename
        even if a magic char slips past the reject check above.
        """
        cwd = _seed_repo(tmp_path)
        real_run = subprocess.run
        seen = {}

        def spy(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "shortlog"]:
                seen["env"] = kwargs.get("env")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        facts = _probe_owner_for_task("who owns mod.py", ["mod.py"], cwd)
        assert facts and "owners" in facts
        assert seen.get("env", {}).get("GIT_LITERAL_PATHSPECS") == "1"

    def test_no_owner_keyword_skips_probe(self, tmp_path):
        cwd = _seed_repo(tmp_path)
        assert _probe_owner_for_task("describe mod.py", ["mod.py"], cwd) is None

    def test_empty_named_paths_skips_probe(self, tmp_path):
        cwd = _seed_repo(tmp_path)
        assert _probe_owner_for_task("who owns mod.py", [], cwd) is None
