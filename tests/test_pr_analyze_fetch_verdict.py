"""A missing remote diff must never yield an affirmative PR verdict."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_pr_analyze as analyze

pytestmark = pytest.mark.usefixtures("pinned_index_freshness_budget")


@pytest.fixture
def run_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(home))
    # Local prep can be healthy even though the explicitly requested remote
    # diff failed. Keep unrelated repository analysis out of this regression.
    monkeypatch.setattr(analyze, "_capture_pr_prep", lambda *a, **kw: {"summary": {"pr_risk_score": 0}})
    monkeypatch.setattr(analyze, "ensure_index", lambda: None)
    monkeypatch.setattr(analyze, "auto_log", lambda *a, **kw: None)

    def run(intent=""):
        args = ["--json", "pr-analyze", "--diff-from-pr", "https://github.com/example/project/pull/7"]
        if intent:
            args.extend(["--intent", intent])
        result = CliRunner().invoke(cli, args)
        if result.exit_code != 0:
            # A deliberate CLI error is also a valid refusal to pass the PR.
            assert isinstance(result.exception, SystemExit), result.output
            return "ERROR"
        data = json.loads(result.output)
        summary = data["summary"]
        assert not data.get("warnings_out"), data
        assert summary.get("state") in (None, "fetch_failed")
        assert summary.get("diff_source_error") == ("fetch_failed" if summary.get("state") else None)
        return summary["verdict"].split()[0]

    return run


@pytest.mark.parametrize("intent", ["", "[intentional] maintenance"])
@pytest.mark.parametrize("failure", ["nonzero", "missing", "timeout"])
def test_failed_pr_fetch_cannot_report_clean(run_analysis, monkeypatch, intent, failure):
    calls = []

    def gh_run(args, **kwargs):
        if args[:3] != ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        calls.append(args)
        if failure == "missing":
            raise FileNotFoundError("gh unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="fetch denied")

    monkeypatch.setattr(analyze.subprocess, "run", gh_run)
    verdict = run_analysis(intent)
    assert calls, "the remote fetch was not exercised"
    assert verdict not in {"SAFE", "INTENTIONAL"}, verdict


def test_successful_pr_fetch_keeps_normal_verdict(run_analysis, monkeypatch):
    """Control: a fetched diff still permits a clean verdict."""
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"

    def gh_run(args, **kwargs):
        if args[:3] != ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=diff, stderr="")

    monkeypatch.setattr(analyze.subprocess, "run", gh_run)
    assert run_analysis() == "SAFE"


@pytest.mark.parametrize("failure", ["nonzero", "missing", "timeout", "invalid", "healthy"])
@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize("prep_failed", [False, True])
def test_remote_diff_gate_refuses_unavailable_diff(run_analysis, monkeypatch, failure, cache, prep_failed):
    if prep_failed:
        monkeypatch.setattr(
            analyze,
            "_capture_pr_prep",
            lambda *a, **kw: {"summary": {"pr_risk_score": 0}, "diff": {"error": "diff failed"}},
        )

    def fetch(args, **kwargs):
        if args[:3] != ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if failure == "missing":
            raise FileNotFoundError("gh missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        return subprocess.CompletedProcess(
            args, int(failure == "nonzero"), stdout=diff if failure == "healthy" else "", stderr="denied"
        )

    monkeypatch.setattr(analyze.subprocess, "run", fetch)
    url = "invalid" if failure == "invalid" else "https://github.com/example/project/pull/7"
    args = ["--json", "pr-analyze", "--diff-from-pr", url, "--gate"]
    if cache:
        args.append("--cache")
    for _ in range(2):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == (0 if failure == "healthy" and not prep_failed else 5), result.output
        data = json.loads(result.output)
        assert not data.get("warnings_out"), data
        summary = data["summary"]
        expected = "url_invalid" if failure == "invalid" else "fetch_failed" if failure != "healthy" else None
        assert summary.get("diff_source_error") == expected
        assert summary.get("state") == (expected or ("diff_failed" if prep_failed else None))


@pytest.mark.parametrize("gate", [False, True])
def test_current_fetch_failure_cannot_replay_healthy_empty_cache(run_analysis, monkeypatch, gate, project_factory):
    project = project_factory({"app.py": "def sample(): pass\n"})
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        analyze,
        "_capture_pr_prep",
        lambda *a, **kw: {"summary": {"pr_risk_score": 0}, "diff": {"summary": {"state": "no_changes"}}},
    )
    failed = [False]
    run = subprocess.run

    def gh_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, int(failed[0]), stdout="", stderr="")
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", gh_run)
    args = ["--json", "pr-analyze", "--cache", "--diff-from-pr", "https://github.com/example/project/pull/7"]
    if gate:
        args.append("--gate")
    for _ in range(2):
        healthy = CliRunner().invoke(cli, args)
        assert healthy.exit_code == 0, healthy.output
    assert json.loads(healthy.output)["cache_hit"] is True
    failed[0] = True
    result = CliRunner().invoke(cli, args)
    data = json.loads(result.output)
    assert not data.get("warnings_out"), data
    summary = data["summary"]
    assert summary["state"] == "fetch_failed"
    assert summary.get("diff_source_error") == "fetch_failed"
    assert summary["partial_success"] is True
    assert summary["verdict"] != "NOCHANGES"
    assert result.exit_code == (5 if gate else 0)


@pytest.mark.parametrize("state", ["diff_failed", "fetch_failed", "url_invalid"])
def test_cached_diff_failure_refuses_gate(run_analysis, monkeypatch, state):
    monkeypatch.setattr(
        analyze, "_load_cache", lambda *a: {"summary": {"verdict": "REVIEW", "state": state, "partial_success": True}}
    )
    with pytest.raises(SystemExit) as exc:
        analyze._serve_from_cache(
            "", analyze.Path("rules.yml"), 50, None, analyze.Path("cache"), json_mode=True, quiet=False, gate=True
        )
    assert exc.value.code == 5


@pytest.mark.parametrize("source", ["input", "git", "acquire_exception"])
@pytest.mark.parametrize("cache", [False, True])
def test_local_diff_acquisition_failure_refuses_gate(run_analysis, monkeypatch, source, cache):
    monkeypatch.setattr(
        analyze.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(
            args, int(args[:2] == ["git", "diff"]), stdout="", stderr="bad ref"
        ),
    )
    if source == "acquire_exception":

        def broken(*args, **kwargs):
            raise OSError("acquisition failed")

        monkeypatch.setattr(analyze, "_acquire_diff", broken)
    args = ["--json", "pr-analyze", "--gate"]
    args.extend(["--input", "missing.diff"] if source == "input" else ["--staged"])
    if cache:
        args.append("--cache")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0, result.output
