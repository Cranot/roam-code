"""Tests for `roam trends --cohort-by-author`."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from conftest import git_init, index_in_process


def _make_local_cli():
    from roam.commands.cmd_trends import trends

    @click.group()
    @click.option("--json", "json_out", is_flag=True)
    @click.pass_context
    def _local_cli(ctx, json_out):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_out

    _local_cli.add_command(trends)
    return _local_cli


_LOCAL_CLI = _make_local_cli()


def _invoke(args, cwd=None, json_mode=False):
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


def _git_commit_as(path: Path, msg: str, author_name: str, author_email: str):
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = author_name
    env["GIT_AUTHOR_EMAIL"] = author_email
    env["GIT_COMMITTER_NAME"] = author_name
    env["GIT_COMMITTER_EMAIL"] = author_email
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, capture_output=True, env=env)


@pytest.fixture()
def cohort_project(tmp_path, monkeypatch):
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "src").mkdir()
    (proj / "src" / "ai_module.py").write_text(
        "def ai_logic(x):\n"
        "    return x + 1\n",
        encoding="utf-8",
    )
    (proj / "src" / "human_module.py").write_text(
        "def human_logic(x):\n"
        "    return x - 1\n",
        encoding="utf-8",
    )
    git_init(proj)

    # AI-heavy edits on ai_module.py
    (proj / "src" / "ai_module.py").write_text(
        "def ai_logic(x):\n"
        "    total = 0\n"
        "    for i in range(x):\n"
        "        for j in range(i):\n"
        "            total += i * j\n"
        "    return total\n",
        encoding="utf-8",
    )
    _git_commit_as(proj, "feat: expand ai module", "GitHub Copilot", "copilot@example.com")

    (proj / "src" / "ai_module.py").write_text(
        "def ai_logic(x):\n"
        "    total = 0\n"
        "    for i in range(x):\n"
        "        for j in range(i):\n"
        "            for k in range(j):\n"
        "                total += i * j * k\n"
        "    return total\n",
        encoding="utf-8",
    )
    _git_commit_as(proj, "refactor: generated complexity update", "Cursor Bot", "cursor@example.com")

    # Human edits on human_module.py
    (proj / "src" / "human_module.py").write_text(
        "def human_logic(x):\n"
        "    if x <= 1:\n"
        "        return 1\n"
        "    return x * 2\n",
        encoding="utf-8",
    )
    _git_commit_as(proj, "manual tweak to business logic", "Human Dev", "human@example.com")

    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


def test_trends_cohort_json_shape(cohort_project):
    result = _invoke(
        ["trends", "--cohort-by-author", "--days", "365"],
        cwd=cohort_project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["command"] == "trends"
    assert data["mode"] == "cohort-by-author"
    assert "cohorts" in data
    assert "ai" in data["cohorts"]
    assert "human" in data["cohorts"]

    ai = data["cohorts"]["ai"]
    human = data["cohorts"]["human"]
    assert "sparkline" in ai
    assert "sparkline" in human
    assert isinstance(ai["series"], list)
    assert isinstance(human["series"], list)
    assert ai["trend_direction"] in {"improving", "stable", "worsening"}
    assert human["trend_direction"] in {"improving", "stable", "worsening"}


def test_trends_cohort_text_output(cohort_project):
    result = _invoke(
        ["trends", "--cohort-by-author", "--days", "365"],
        cwd=cohort_project,
        json_mode=False,
    )
    assert result.exit_code == 0, result.output
    assert "VERDICT:" in result.output
    assert "COHORT" in result.output
    assert "AI" in result.output
    assert "HUMAN" in result.output


def test_trends_cohort_disallows_record(cohort_project):
    result = _invoke(
        ["trends", "--record", "--cohort-by-author"],
        cwd=cohort_project,
        json_mode=False,
    )
    assert result.exit_code != 0
    assert "Cannot combine --record with --cohort-by-author" in result.output
