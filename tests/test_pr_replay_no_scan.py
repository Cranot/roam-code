"""No-scan replay must not classify absent history as low-risk evidence."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_postmortem, cmd_pr_replay


@pytest.fixture
def history(tmp_path, monkeypatch):
    # Real isolated Git distinguishes an invalid revision from a valid empty
    # range. Indexing is irrelevant to this pre-analysis refusal boundary.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cmd_postmortem, "ensure_index", lambda: None)
    monkeypatch.setattr(cmd_pr_replay, "ensure_index", lambda: None)
    for args in (
        ["init", "-q"],
        [
            "-c",
            "user.name=Trial",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=15)
    return tmp_path


@pytest.mark.parametrize("command", ["postmortem", "pr-replay"])
@pytest.mark.parametrize(
    "revision,state", [("HEAD~5..HEAD", "range_unavailable"), ("HEAD..HEAD", "no_commits_in_range")]
)
def test_no_scan_names_input_state(history, command, revision, state):
    args = ["--json", command]
    args += [revision] if command == "postmortem" else ["--tier", "sample", "--range", revision]
    result = CliRunner().invoke(cli, args)
    data = json.loads(result.output)
    assert data["summary"]["state"] == state
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["commits_scanned"] == 0
    if command == "pr-replay":
        assert data["summary"]["risk_level_canonical"] is None
        assert data["summary"]["risk_rank"] is None
        assert "clean window" not in data["report_markdown"].lower()


def test_no_scan_withholds_requested_deliverables(history):
    output = history / "report.md"
    result = CliRunner().invoke(cli, ["--json", "pr-replay", "--tier", "sample", "--output", str(output)])
    data = json.loads(result.output)
    assert not output.exists()
    assert data["summary"]["output_path"] is None
    assert data["summary"]["partial_success"] is True


def test_no_scan_preserves_existing_deliverable(history):
    output = history / "report.md"
    output.write_text("previous reviewed report", encoding="utf-8")
    result = CliRunner().invoke(cli, ["--json", "pr-replay", "--tier", "sample", "--output", str(output)])
    assert json.loads(result.output)["summary"]["partial_success"] is True
    assert output.read_text(encoding="utf-8") == "previous reviewed report"


def test_no_scan_paid_outputs_and_rehearsal_are_withheld(history):
    # Intentional tmp_path writes exercise every advertised output boundary;
    # no PDF renderer, customer file, or external service should be reached.
    output = history / "report.md"
    evidence = history / "evidence.json"
    companion = history / "companion.md"
    pdf = history / "report.pdf"
    bundle = history / "bundle"
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "pr-replay",
            "--tier",
            "team",
            "--client",
            "Synthetic trial",
            "--output",
            str(output),
            "--pdf",
            str(pdf),
            "--evidence",
            str(evidence),
            "--markdown",
            str(companion),
            "--evidence-bundle",
            str(bundle),
        ],
    )
    assert json.loads(result.output)["summary"]["partial_success"] is True
    assert not any(path.exists() for path in (output, evidence, companion, pdf, bundle))
    assert not (history / ".roam/engagements.jsonl").exists()
    rehearsal = CliRunner().invoke(cli, ["--json", "pr-replay", "--tier", "team", "--rehearsal"])
    assert json.loads(rehearsal.output)["summary"]["partial_success"] is True
    assert not (history / "internal/engagements").exists()


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("git", 30)])
def test_git_execution_failure_is_not_empty_history(monkeypatch, failure):
    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(cmd_postmortem.subprocess, "run", unavailable)
    with pytest.raises(RuntimeError, match="enumeration unavailable"):
        cmd_postmortem._git_log_in_range("HEAD")


def test_valid_commit_enumeration_is_preserved(history):
    commits = cmd_postmortem._git_log_in_range("HEAD", limit=5)
    assert len(commits) == 1
    assert commits[0]["subject"] == "baseline"
