"""History commands label evidence from a depth-limited clone."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.test_w1513_agent_review_verdict_contract import _run_validator


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

    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    git("init", "-q", f"--template={hooks}")
    (source / "test_example.py").write_text(
        "from example import example\ndef test_example():\n    assert example() >= 0\n", encoding="utf-8"
    )
    for number in range(2):
        (source / "example.py").write_text(
            f"def example(value=True):\n    if value:\n        return {number}\n    return 0\n", encoding="utf-8"
        )
        git("add", "example.py", "test_example.py")
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
        # Contract change: a full clone previously omitted history_shallow.
        # False now distinguishes a verified full clone from an unknown depth (null).
        assert data["history_shallow"] is shallow, args
        if not shallow:
            assert data["summary"]["partial_success"] is False, args
        assert data["summary"]["verdict"]
        if shallow:
            assert data["summary"]["partial_success"] is True
            assert "shallow" in data["summary"]["verdict"].lower()
        text = runner.invoke(cli, args)
        assert text.exit_code == 0, text.output
        assert text.output.count("Git history is shallow") == int(shallow), args
        assert "Git history depth could not be checked" not in text.output, args


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


@pytest.fixture(params=[True, None], ids=["depth-1", "unknown-depth"])
def limited_checkout(history_checkout, monkeypatch, request):
    from roam import git_utils

    checkout = history_checkout(True)
    if request.param is None:
        monkeypatch.setattr(git_utils, "git_history_shallow", lambda root: None)
    message = "Git history is shallow" if request.param else "Git history depth could not be checked"
    return checkout, request.param, message


def _stage_example_change(checkout):
    source = checkout / "example.py"
    source.write_text(source.read_text(encoding="utf-8").replace("return 1", "return 2"), encoding="utf-8")
    subprocess.run(["git", "add", "example.py"], cwd=checkout, check=True, capture_output=True)


def test_pr_analyze_accepts_history_limited_checkout(limited_checkout):
    checkout, shallow, message = limited_checkout
    _stage_example_change(checkout)

    runner = CliRunner()
    args = ["pr-analyze", "--staged"]
    result = runner.invoke(cli, ["--json", *args], input="")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["summary"].get("state") != "diff_failed", data["summary"]
    assert not data["summary"].get("failed_subcommands")
    assert data["summary"]["partial_success"] is False
    assert data["history_shallow"] is shallow

    status, output, error = _run_validator(checkout, data)
    assert status == 0, error
    assert output.strip() == f"verdict={data['summary']['verdict_code']}"
    notice = data["history_depth_notice"]
    assert notice.startswith(message)

    gated = runner.invoke(cli, [*args, "--gate"], input="")
    assert gated.exit_code == 0, gated.output
    assert gated.stderr.count(notice) == 1


def test_pr_analyze_reviewers_child_keeps_history_depth_advisory(history_checkout):
    checkout = history_checkout(True)
    _stage_example_change(checkout)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-analyze", "--staged", "--with-reviewers"], input="")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["summary"]["partial_success"] is False
    reviewers = data["reviewers"]
    assert reviewers["command"] == "suggest-reviewers"
    assert reviewers["changed_files"] == ["example.py"]
    assert reviewers["history_shallow"] is True
    assert "Git history is shallow" in reviewers["summary"]["verdict"]
    assert reviewers["summary"]["partial_success"] is False

    standalone = runner.invoke(cli, ["--json", "suggest-reviewers", "example.py"])
    assert standalone.exit_code == 0, standalone.output
    assert json.loads(standalone.stdout)["summary"]["partial_success"] is True


def test_audit_accepts_history_limited_checkout(limited_checkout):
    _, shallow, message = limited_checkout
    result = CliRunner().invoke(cli, ["--json", "audit"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "incomplete checks" not in data["summary"]["verdict"]
    assert data["summary"]["partial_success"] is False, data["summary"]
    assert data["history_shallow"] is shallow
    assert message in data["summary"]["verdict"]
