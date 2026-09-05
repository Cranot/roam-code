"""Reject ambiguous proof inputs before collection, composition, or output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.proof_bundle import compose_agent_change_proof_bundle, load_pr_bundle


@pytest.mark.parametrize("key", ["review_evidence", "orchestration_contract"])
@pytest.mark.parametrize("value", [[], False, "invalid"])
def test_composer_refuses_malformed_review_before_collecting(tmp_path, monkeypatch, key, value):
    graph = Mock(side_effect=AssertionError("invalid input reached collection"))
    monkeypatch.setattr("roam.proof_bundle.build_command_graph", graph)
    with pytest.raises(ValueError, match=key):
        compose_agent_change_proof_bundle({"changed_files": ["README.md"], key: value}, repo_root=tmp_path)
    graph.assert_not_called()


INVALID_JSON = [
    '{"review_evidence":false}',
    '{"orchestration_contract":[]}',
    '{"review_evidence":{"1b_plan_critique":{"status":"rejected","status":"declared_accepted"}}}',
    '{"changed_files":["README.md"],"extra":NaN}',
    '{"changed_files":["README.md"],"extra":1e999}',
    '{"tests_run":[{"status":"pass"}]}',
]


@pytest.mark.parametrize("text", INVALID_JSON)
def test_loader_refuses_ambiguous_or_malformed_proof(tmp_path, text):
    path = tmp_path / "proof.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_pr_bundle(path)


@pytest.mark.parametrize("command", ["guard-pr", "proof-bundle"])
@pytest.mark.parametrize("text", INVALID_JSON)
def test_cli_refuses_original_bytes_before_auto_collection(tmp_path, monkeypatch, command, text):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "proof.json"
    path.write_text(text, encoding="utf-8")
    original = path.read_bytes()

    def rewrite(*args):
        path.write_text('{"changed_files":["README.md"]}', encoding="utf-8")
        return {}

    collector = Mock(side_effect=rewrite)
    monkeypatch.setattr("roam.commands.cmd_guard_pr._run_auto_collect_inline", collector)
    result = CliRunner().invoke(cli, ["--json", command, "--bundle", str(path)])
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["error_code"] == data["error"]["code"] == "bundle_parse_error"
    collector.assert_not_called()
    assert path.read_bytes() == original


@pytest.mark.parametrize("command", ["guard-pr", "proof-bundle"])
def test_cli_load_oserror_is_structured(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "proof.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "roam.commands.cmd_" + command.replace("-", "_") + ".load_pr_bundle",
        Mock(side_effect=PermissionError("denied")),
    )
    result = CliRunner().invoke(cli, ["--json", command, "--bundle", str(path)])
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["summary"]["error_code"] == data["error"]["code"] == "bundle_load_failed"


@pytest.mark.parametrize("command", ["guard-pr", "proof-bundle"])
def test_cli_unknown_review_status_is_structured(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "proof.json"
    path.write_text(
        json.dumps(
            {"changed_files": ["README.md"], "review_evidence": {"1b_plan_critique": {"status": "future_status"}}}
        ),
        encoding="utf-8",
    )
    args = ["--json", command, "--bundle", str(path)]
    if command == "guard-pr":
        args.append("--skip-collect")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 5, result.output
    data = json.loads(result.output)
    assert data["summary"]["error_code"] == data["error"]["code"] == "compose_failed"
    assert data["summary"]["partial_success"] is True


@pytest.mark.parametrize("key", ["command", "name", "test"])
def test_legacy_checks_and_null_review_remain_valid(tmp_path, key):
    source = {
        "changed_files": ["README.md"],
        "tests_run": [{key: "pytest", "status": "pass"}],
        "review_evidence": None,
        "orchestration_contract": None,
    }
    result = compose_agent_change_proof_bundle(source, repo_root=tmp_path)
    assert result["executed_checks"][0]["command"] == "pytest"
    assert result["review_evidence"] is None
    assert result["orchestration_contract"] is None
    assert result["verdict"]["value"] == "pass"


def test_loader_uses_explicit_utf8(tmp_path, monkeypatch):
    path = tmp_path / "proof.json"
    path.write_text('{"changed_files":["caf\u00e9.py"]}', encoding="utf-8")
    original = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            assert kwargs.get("encoding") == "utf-8"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert load_pr_bundle(path)["changed_files"] == ["caf\u00e9.py"]


def test_inline_collector_rechecks_input_before_writing(tmp_path, monkeypatch):
    from roam.commands.cmd_guard_pr import _run_auto_collect_inline

    path = tmp_path / "proof.json"
    path.write_text(INVALID_JSON[2], encoding="utf-8")
    original = path.read_bytes()
    collect = Mock()
    monkeypatch.setattr("roam.commands.cmd_guard_pr.auto_collect", collect)
    result = _run_auto_collect_inline(path, tmp_path)
    assert result["error"].startswith("bundle_load_failed:")
    collect.assert_not_called()
    assert path.read_bytes() == original


def test_inline_collector_validates_output_before_writing(tmp_path, monkeypatch):
    from roam.commands.cmd_guard_pr import _run_auto_collect_inline

    path = tmp_path / "proof.json"
    path.write_text('{"changed_files":["README.md"]}', encoding="utf-8")
    original = path.read_bytes()

    def collect(bundle, root):
        bundle["review_evidence"] = False
        return {}

    monkeypatch.setattr("roam.commands.cmd_guard_pr.auto_collect", collect)
    result = _run_auto_collect_inline(path, tmp_path)
    assert result["error"].startswith("auto_collect_failed:")
    assert path.read_bytes() == original


@pytest.mark.parametrize("strict", [False, True])
def test_failed_collection_cannot_produce_a_verdict(tmp_path, monkeypatch, strict):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "proof.json"
    path.write_text('{"changed_files":["README.md"]}', encoding="utf-8")
    monkeypatch.setattr(
        "roam.commands.cmd_guard_pr._run_auto_collect_inline", Mock(return_value={"error": "collection failed"})
    )
    args = ["--json", "guard-pr", "--bundle", str(path)]
    if strict:
        args.append("--strict")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["error_code"] == data["error"]["code"] == "auto_collect_failed"
    assert "agent_change_proof_bundle" not in data
    assert not (tmp_path / ".roam" / "verdict-log.jsonl").exists()
