"""Evidence must survive composition, serialization, and the actual CI reader."""

from __future__ import annotations

import inspect
import json
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verdict import _extract_contract_inputs
from roam.proof_bundle import _git_changed_files_with_provenance, compose_agent_change_proof_bundle
from roam.verdict import compute_verdict


@pytest.mark.parametrize(
    "status,expected",
    [
        (None, "blocked"),
        ("rejected", "blocked"),
        ("artifact_stale", "blocked"),
        ("same_family", "pass_with_warnings"),
        ("declared_accepted", "pass"),
    ],
)
def test_review_verdict_survives_real_composer_and_json_reader(tmp_path, monkeypatch, status, expected):
    monkeypatch.chdir(tmp_path)
    bundle = {
        "changed_files": ["README.md"],
        "orchestration_contract": {"obligations": [{"phase": "1b_plan_critique", "required": True}]},
    }
    if status is not None:
        bundle["review_evidence"] = {phase: {"status": status} for phase in ("1b_plan_critique", "4b_done_verdict")}
    composed = compose_agent_change_proof_bundle(bundle, repo_root=tmp_path)
    assert composed["verdict"]["value"] == expected
    serialized = json.loads(json.dumps(composed))
    reread = compute_verdict(**_extract_contract_inputs(serialized))
    assert reread == composed["verdict"]
    result = CliRunner().invoke(cli, ["--json", "verdict", "--bundle", "-"], input=json.dumps(serialized))
    assert result.exit_code == (5 if expected == "blocked" else 0), result.output
    assert json.loads(result.output)["verdict"] == composed["verdict"]


@pytest.mark.parametrize("review,expected", [(None, "pass"), ({}, "blocked")])
def test_absent_and_explicitly_empty_review_stay_distinct(tmp_path, review, expected):
    composed = compose_agent_change_proof_bundle(
        {"changed_files": ["README.md"], "review_evidence": review}, repo_root=tmp_path
    )
    assert composed["verdict"]["value"] == expected
    assert compute_verdict(**_extract_contract_inputs(json.loads(json.dumps(composed)))) == composed["verdict"]


def test_composer_persists_every_verdict_input(tmp_path):
    composed = compose_agent_change_proof_bundle({"changed_files": ["README.md"]}, repo_root=tmp_path)
    required_inputs = {
        name
        for name, param in inspect.signature(compute_verdict).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert required_inputs <= composed.keys()


@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_untracked_scan_failure_preserves_known_paths_and_blocks(tmp_path, failure):
    def run(args, **kwargs):
        if args[1] == "diff":
            return subprocess.CompletedProcess(args, 0, "README.md\0", "")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        if failure == "oserror":
            raise OSError("scan unavailable")
        return subprocess.CompletedProcess(args, 128, "", "scan unavailable")

    with patch("roam.proof_bundle.subprocess.run", side_effect=run):
        files, reason = _git_changed_files_with_provenance(tmp_path)
    assert files == ["README.md"]
    assert reason
    with patch("roam.proof_bundle._git_changed_files_with_provenance", return_value=(files, reason)):
        composed = compose_agent_change_proof_bundle({}, repo_root=tmp_path)
    assert composed["verdict"]["value"] == "blocked"
    assert compute_verdict(**_extract_contract_inputs(composed))["value"] == "blocked"


def test_git_paths_are_nul_delimited_and_not_stripped(tmp_path):
    paths = ["src/caf\u00e9.py", " space.py ", "line\nbreak.py"]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        names = paths[:2] if args[1] == "diff" else paths[1:]
        return subprocess.CompletedProcess(args, 0, "\0".join(names) + "\0", "")

    with patch("roam.proof_bundle.subprocess.run", side_effect=run):
        files, reason = _git_changed_files_with_provenance(tmp_path)
    assert files == paths
    assert reason is None
    assert all("-z" in args for args in calls)


def test_successfully_measured_empty_git_tree_is_not_unknown(tmp_path):
    with patch("roam.proof_bundle.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
        assert _git_changed_files_with_provenance(tmp_path) == ([], None)


def test_real_git_paths_keep_unicode_and_whitespace(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base")
    name = " leading caf\u00e9.py"
    (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
    files, reason = _git_changed_files_with_provenance(tmp_path)
    assert files == [name]
    assert reason is None


def test_cli_written_bundle_keeps_rejected_review_blocking(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = {
        "changed_files": ["README.md"],
        "review_evidence": {phase: {"status": "rejected"} for phase in ("1b_plan_critique", "4b_done_verdict")},
    }
    source = tmp_path / "in.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    output = tmp_path / "out.json"
    runner = CliRunner()
    produced = runner.invoke(
        cli, ["proof-bundle", "--bundle", str(source), "--output", str(output), "--strict", "--validate"]
    )
    assert produced.exit_code == 5, produced.output
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["verdict"]["value"] == "blocked"
    consumed = runner.invoke(cli, ["--json", "verdict", "--bundle", str(output)])
    assert consumed.exit_code == 5, consumed.output
    assert json.loads(consumed.output)["verdict"] == loaded["verdict"]
