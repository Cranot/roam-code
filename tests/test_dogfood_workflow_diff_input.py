"""Run CI's actual Bash diff preparation in disposable local Git repositories.

Real Git/Bash is intentional: mocks would miss quoting, exit propagation and
revision selection. No index, network, host Git config or global writes are used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._helpers.repo_root import repo_root


@pytest.fixture
def diff_workflow(tmp_path):
    # Prefer native Git Bash on Windows: the WindowsApps WSL launcher does
    # not preserve this process's environment or Windows path semantics.
    candidates = ["C:/Program Files/Git/bin/bash.exe", shutil.which("bash")]
    bash = next((str(path) for path in candidates if path and Path(path).is_file()), None)
    if bash is None:
        pytest.skip("Bash is unavailable; this test exercises the real CI shell")
    git = shutil.which("git")
    assert git, "Git is required for the workflow diff boundary"
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "runner temp"
    output.mkdir()
    env["RUNNER_TEMP"] = output.as_posix()

    def git_run(*args):
        return subprocess.run(
            [git, *args], cwd=repo, env=env, capture_output=True, text=True, check=True, timeout=15
        ).stdout.strip()

    git_run("init", "--quiet")
    git_run("config", "user.name", "Workflow test")
    git_run("config", "user.email", "workflow@example.invalid")
    git_run("config", "core.autocrlf", "false")
    git_run("config", "commit.gpgsign", "false")
    git_run("config", "core.hooksPath", str(tmp_path / "no-hooks"))
    source = repo / "example.txt"
    source.write_text("first\n", encoding="utf-8")
    git_run("add", "example.txt")
    git_run("commit", "--quiet", "-m", "base")
    base = git_run("rev-parse", "HEAD")
    source.write_text("second\n", encoding="utf-8")
    git_run("commit", "--quiet", "-am", "change")
    head = git_run("rev-parse", "HEAD")
    workflow = yaml.safe_load((repo_root() / ".github/workflows/dogfood.yml").read_text(encoding="utf-8"))
    script = next(
        step["run"]
        for step in workflow["jobs"]["dogfood"]["steps"]
        if step.get("name") == "Prepare immutable dogfood diff"
    )

    def prepare(event, selected_base):
        invocation_env = dict(env, GITHUB_EVENT_NAME=event, EVENT_BASE_SHA=selected_base)
        return subprocess.run(
            [bash, "--noprofile", "--norc", "-c", script],
            cwd=repo,
            env=invocation_env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    return prepare, git_run, repo, output / "roam-dogfood.diff", base, head


@pytest.mark.parametrize("event", ["push", "pull_request", "workflow_dispatch"])
def test_actual_workflow_prepares_selected_commit_diff(diff_workflow, event):
    prepare, git_run, _, artifact, base, head = diff_workflow
    result = prepare(event, "ignored-for-manual" if event == "workflow_dispatch" else base)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = git_run("diff", "--no-ext-diff", "--no-textconv", base, head, "--")
    assert artifact.read_text(encoding="utf-8").strip() == expected
    assert f"Dogfood diff: {base}..{head}" in result.stdout


@pytest.mark.parametrize("base", ["", "0" * 40, "not-a-sha", "f" * 40, "$(touch INJECTED)"])
def test_actual_workflow_refuses_unavailable_or_shell_shaped_base(diff_workflow, base):
    prepare, _, repo, artifact, _, _ = diff_workflow
    result = prepare("push", base)
    assert result.returncode != 0
    assert not (repo / "INJECTED").exists()
    assert not artifact.exists()


def test_actual_workflow_refuses_empty_diff(diff_workflow):
    prepare, _, _, artifact, _, head = diff_workflow
    result = prepare("push", head)
    assert result.returncode != 0
    assert "No diff to analyze" in result.stdout
    assert artifact.read_bytes() == b""


def test_actual_manual_workflow_refuses_root_without_parent(diff_workflow):
    prepare, git_run, _, artifact, base, _ = diff_workflow
    git_run("checkout", "--quiet", "--detach", base)
    result = prepare("workflow_dispatch", "")
    assert result.returncode != 0
    assert not artifact.exists()
