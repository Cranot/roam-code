"""`roam coupling` and `roam fn-coupling` name why no commit history was indexed.

A repository whose commits are all older than the ``ROAM_GIT_SINCE`` window
(365 days by default) indexes no commits. Both commands used to answer "run
`roam index` on a git repository" there, which sends the reader to a step they
already took; the cause sat only in the indexer's info log.
"""

from __future__ import annotations

import json
import os
import subprocess

from tests.conftest import index_in_process, invoke_cli

_OLD = "2024-01-02T10:00:00"


def _old_pair_repo(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (proj / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_DATE": _OLD, "GIT_COMMITTER_DATE": _OLD}

    def git(*args):
        subprocess.run(["git", *args], cwd=proj, env=env, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "Test")
    git("add", ".")
    git("commit", "-m", "init")
    for i in range(4):
        for name in ("a.py", "b.py"):
            with (proj / name).open("a", encoding="utf-8") as fh:
                fh.write(f"# change {i}\n")
        git("commit", "-am", f"pair {i}")
    return proj


def _summary(cli_runner, proj, command):
    result = invoke_cli(cli_runner, [command], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["summary"]


def test_history_outside_the_window_is_named(tmp_path, cli_runner, monkeypatch):
    monkeypatch.delenv("ROAM_GIT_SINCE", raising=False)
    proj = _old_pair_repo(tmp_path)
    out, code = index_in_process(proj)
    assert code == 0, out
    for command in ("coupling", "fn-coupling"):
        summary = _summary(cli_runner, proj, command)
        assert summary["history_state"] == "outside_history_window", (command, summary)
        assert summary["partial_success"] is True
        assert "ROAM_GIT_SINCE=0" in summary["verdict"]


def test_full_history_finds_the_old_pair(tmp_path, cli_runner, monkeypatch):
    monkeypatch.setenv("ROAM_GIT_SINCE", "0")
    proj = _old_pair_repo(tmp_path)
    out, code = index_in_process(proj)
    assert code == 0, out
    summary = _summary(cli_runner, proj, "coupling")
    assert summary.get("pairs") == 1, summary
    assert "history_state" not in summary


def test_a_project_without_git_is_named(tmp_path, cli_runner):
    proj = tmp_path / "plain"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    out, code = index_in_process(proj)
    assert code == 0, out
    summary = _summary(cli_runner, proj, "coupling")
    assert summary["history_state"] == "not_a_git_repository", summary


def test_a_changeset_check_without_history_is_not_a_pass(tmp_path, cli_runner, monkeypatch):
    """`coupling --against` used to answer "complete changeset" with no history."""
    monkeypatch.delenv("ROAM_GIT_SINCE", raising=False)
    proj = _old_pair_repo(tmp_path)
    with (proj / "a.py").open("a", encoding="utf-8") as fh:
        fh.write("# solo\n")
    env = {**os.environ, "GIT_AUTHOR_DATE": _OLD, "GIT_COMMITTER_DATE": _OLD}
    subprocess.run(["git", "commit", "-am", "solo"], cwd=proj, env=env, capture_output=True, check=True)
    out, code = index_in_process(proj)
    assert code == 0, out
    result = invoke_cli(cli_runner, ["coupling", "--against", "HEAD~1..HEAD"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["summary"]
    assert summary["history_state"] == "outside_history_window", summary
    assert summary["partial_success"] is True
    assert "complete changeset" not in summary["verdict"]
