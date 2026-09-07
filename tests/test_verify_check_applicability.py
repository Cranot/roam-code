"""Selected checks are not executed evidence when their input scope is empty."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import git_init, index_in_process


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Example\n", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text("enabled: true\n", encoding="utf-8")
    git_init(tmp_path)
    output, code = index_in_process(tmp_path)
    assert code == 0, output
    monkeypatch.chdir(tmp_path)
    return tmp_path


def verify(*args):
    result = CliRunner().invoke(cli, ["--json", "verify", *args])
    assert result.exit_code in (0, 5), (result.output, result.exception)
    return result.exit_code, json.loads(result.stdout)


@pytest.mark.parametrize("target", ["README.md", "settings.yaml"])
@pytest.mark.parametrize("extra", [[], ["--diff-only"], ["--severity", "fail"]])
def test_no_applicable_code_check_cannot_pass(project, target, extra):
    (project / target).write_text("# documentation-only change\n", encoding="utf-8")
    code, data = verify("--changed", "--checks", "naming,imports,syntax", *extra)
    assert code == 5, data["summary"]
    assert data["summary"]["state"] == "no_applicable_checks"
    assert data["summary"]["verification_complete"] is False
    assert data["summary"]["partial_success"] is True
    assert data["summary"]["checks_run"] == []
    assert "no_applicable_checks" in data["summary"]["incomplete_reasons"]


@pytest.mark.parametrize("target", ["README.md", "settings.yaml"])
def test_default_retains_secret_scan_without_claiming_source_checks(project, target):
    code, data = verify(target)
    assert code == 0, data["summary"]
    assert data["summary"]["checks_run"] == ["secrets"]
    assert "non-code" in data["agent_contract"]["facts"][0]
    assert data["check_applicability"]["source_file_count"] == 0
    assert data["categories"]["syntax"]["applicability"]["state"] == "not_applicable"
    assert data["categories"]["secrets"]["applicability"]["state"] == "applicable"


def test_real_source_syntax_pass_and_failure_are_preserved(project):
    code, data = verify("app.py", "--checks", "syntax")
    assert code == 0 and data["summary"]["verification_complete"] is True
    assert data["summary"]["checks_run"] == ["syntax"]
    (project / "app.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    code, data = verify("app.py", "--checks", "syntax")
    assert code == 5 and data["categories"]["syntax"]["violation_count"] > 0


def test_docs_command_checks_remain_useful(project):
    (project / "README.md").write_text("Run `roam verify --definitely-not-a-flag`.\n", encoding="utf-8")
    _, data = verify("README.md", "--checks", "command_examples")
    assert data["summary"]["checks_run"] == ["command_examples"]
    assert data["categories"]["command_examples"]["violation_count"] > 0


def test_mixed_scope_keeps_real_source_findings(project):
    (project / "app.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    code, data = verify("README.md", "app.py", "--checks", "syntax")
    assert code == 5
    assert data["summary"]["checks_run"] == ["syntax"]
    assert data["check_applicability"]["source_file_count"] == 1
    assert data["categories"]["syntax"]["violation_count"] > 0


def test_clean_tree_keeps_no_changes_distinct(project):
    code, data = verify("--changed")
    assert code == 0
    assert data["summary"]["state"] == "no_changes"
    assert data["summary"]["checks_run"] == []


@pytest.mark.parametrize("summary", [False, True])
def test_text_discloses_non_code_scope(project, summary):
    args = ["verify", "README.md"] + (["--summary"] if summary else [])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "non-code checks only" in result.output
    if not summary:
        assert "SYNTAX: skipped (no eligible inputs)" in result.output


def test_mcp_keeps_applicability_and_real_secret_scan(project):
    from roam.mcp_server import verify as mcp_verify

    (project / "README.md").write_text("# changed documentation\n", encoding="utf-8")
    data = mcp_verify(root=str(project))
    assert data["summary"]["checks_run"] == ["secrets"]
    assert data["check_applicability"]["source_file_count"] == 0
    assert data["categories"]["syntax"]["applicability"]["state"] == "not_applicable"


def test_missing_target_is_not_relabelled_as_non_applicability(project):
    code, data = verify("missing.py", "--checks", "syntax")
    assert code == 5
    assert data["summary"]["state"] == "verification_incomplete"
    assert "explicit_target_missing" in data["summary"]["incomplete_reasons"]


def test_unavailable_selected_check_is_not_labelled_not_applicable(project, monkeypatch):
    from roam.commands import cmd_verify

    monkeypatch.setattr(cmd_verify, "_check_syntax", lambda *args: {"score": 100, "available": False})
    code, data = verify("app.py", "--checks", "syntax")
    assert code == 5
    assert data["summary"]["checks_run"] == ["syntax"]
    assert data["categories"]["syntax"]["applicability"]["state"] == "applicable"
    assert "syntax_incomplete" in data["summary"]["incomplete_reasons"]
