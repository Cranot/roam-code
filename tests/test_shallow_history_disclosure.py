"""History commands label evidence from a depth-limited clone."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.fixture
def history_checkout(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    config = tmp_path / "gitconfig"
    config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    hooks = tmp_path / "hooks"
    hooks.mkdir()

    def git(*args, cwd=source):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    for number in range(2):
        (source / "example.py").write_text(f"def example():\n    return {number}\n", encoding="utf-8")
        git("add", "example.py")
        git(
            "-c",
            "user.name=Author",
            "-c",
            "user.email=author@example.test",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={hooks}",
            "commit",
            "-qm",
            str(number),
        )

    def checkout(shallow):
        destination = tmp_path / "checkout"
        options = ["--depth", "1"] if shallow else []
        git("clone", "-q", *options, source.as_uri(), str(destination))
        assert git("rev-parse", "--is-shallow-repository", cwd=destination) == str(shallow).lower()
        monkeypatch.chdir(destination)
        indexed = CliRunner().invoke(cli, ["--json", "index"])
        assert indexed.exit_code == 0, indexed.output
        return destination

    return checkout


@pytest.mark.parametrize("shallow", [True, False])
def test_history_commands_disclose_shallow_checkout(history_checkout, shallow):
    history_checkout(shallow)
    runner = CliRunner()
    commands = (
        ["context", "example"],
        ["preflight", "example"],
        ["weather"],
        ["churn"],
        ["coupling"],
        ["owner", "example.py"],
        ["bus-factor"],
    )
    for args in commands:
        result = runner.invoke(cli, ["--json", *args])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data.get("history_shallow") is (True if shallow else None), args
        assert data["summary"]["verdict"]
        if shallow:
            assert data["summary"]["partial_success"] is True
            assert "shallow" in data["summary"]["verdict"].lower()
        text = runner.invoke(cli, args)
        assert text.exit_code == 0, text.output
        assert text.output.count("Git history is shallow") == int(shallow), args


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git"),
        PermissionError("unreadable repository"),
        subprocess.TimeoutExpired("git", 3),
        subprocess.CompletedProcess("git", 128, b"false", b"fatal"),
        subprocess.CompletedProcess("git", 0, b"", b""),
        subprocess.CompletedProcess("git", 0, b"TRUE", b""),
        subprocess.CompletedProcess("git", 0, b"true\n", b""),
    ],
)
def test_history_depth_failure_is_disclosed_once(history_checkout, monkeypatch, failure):
    history_checkout(False)
    calls = []
    run = subprocess.run

    def run_git(args, **kwargs):
        if args[:3] == ["git", "rev-parse", "--is-shallow-repository"]:
            calls.append(args)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_git)
    runner = CliRunner()
    help_result = runner.invoke(cli, ["context", "--help"])
    assert "Usage:" in help_result.stdout
    assert not calls

    result = runner.invoke(cli, ["--json", "context", "example", "example"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["history_shallow"] is None
    assert data["summary"]["partial_success"] is True
    assert "depth could not be checked" in data["summary"]["verdict"]
    assert len(calls) == 1

    result = runner.invoke(cli, ["context", "example", "example"])
    assert result.exit_code == 0, result.output
    assert result.stderr.count("Git history depth could not be checked") == 1
    assert len(calls) == 2


def test_git_false_with_present_shallow_marker_is_unverified(history_checkout, monkeypatch):
    checkout = history_checkout(False)
    (checkout / ".git" / "shallow").touch()
    run = subprocess.run

    def run_git(args, **kwargs):
        if args[:3] == ["git", "rev-parse", "--is-shallow-repository"]:
            # Git may print false when a present marker could not be opened.
            return subprocess.CompletedProcess(args, 0, b"false\n.git/shallow\n", b"")
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_git)
    response = CliRunner().invoke(cli, ["--json", "weather"])
    assert response.exit_code == 0, response.output
    data = json.loads(response.stdout)
    assert data["history_shallow"] is None
    assert "depth could not be checked" in data["summary"]["verdict"]
