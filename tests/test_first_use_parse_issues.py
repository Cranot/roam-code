"""Indexing tells first-time users which files need attention."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("command", ["init", "index"])
@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("json_mode", [False, True])
def test_first_use_parse_issues(tmp_path, monkeypatch, command, broken, json_mode):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True, cwd=tmp_path)
    (tmp_path / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    if broken:
        for index in range(7):
            (tmp_path / f"bad-{index}.py").write_text("def (\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["--json", command] if json_mode else [command])
    assert result.exit_code == 0, result.output
    if json_mode:
        data = json.loads(result.stdout)
        if broken:
            assert data["parse_issues"] == {
                "files": [f"bad-{index}.py" for index in range(5)],
                "more": 2,
                "detail_command": "roam index --force --verbose",
            }
            assert data["summary"]["partial_success"] is True
        else:
            assert "parse_issues" not in data
    elif broken:
        assert "bad-0.py" in result.stderr
        assert "bad-4.py" in result.stderr
        assert "bad-5.py" not in result.stderr
        assert "+2 more" in result.stderr
        assert "roam index --force --verbose" in result.stderr
        details = CliRunner().invoke(cli, ["index", "--force", "--verbose"])
        assert details.exit_code == 0, details.output
        assert "bad-6.py" in details.stderr
        assert "+2 more" not in details.stderr
    else:
        assert "Parse issues:" not in result.stderr
    if broken:
        assert "dense vectors" not in result.stderr


@pytest.mark.parametrize("command", ["init", "index"])
def test_salesforce_extractors_do_not_report_syntax_issues(tmp_path, monkeypatch, command):
    from roam.db.connection import open_db

    monkeypatch.chdir(tmp_path)
    _init_git(tmp_path, monkeypatch)
    for path, source in {
        "AccountService.cls": "public with sharing class AccountService { public void run() {} }",
        "Demo.cmp": '<aura:component><aura:attribute name="value" type="String"/></aura:component>',
        "Demo.page": "<apex:page><apex:form/></apex:page>",
        "Demo.cls-meta.xml": '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata"><apiVersion>62.0</apiVersion></ApexClass>',
    }.items():
        (tmp_path / path).write_text(source, encoding="utf-8")
    result = CliRunner().invoke(cli, ["--json", command])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    with open_db() as conn:
        languages = {row["language"] for row in conn.execute("SELECT language FROM files")}
        assert {"apex", "aura", "visualforce", "sfxml"} <= languages
    assert "parse_issues" not in data
    assert data["summary"].get("partial_success", False) is False


@pytest.mark.parametrize("command", ["init", "index"])
def test_parse_issue_scope_on_reused_index(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    _init_git(tmp_path, monkeypatch)
    (tmp_path / "ok.py").write_text("def ok(): pass\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def (\n", encoding="utf-8")
    runner = CliRunner()
    initial = runner.invoke(cli, ["--json", "index"])
    assert initial.exit_code == 0, initial.output
    initial_data = json.loads(initial.stdout)
    assert initial_data["parse_issues"]["files"] == ["bad.py"]
    assert initial_data["summary"]["partial_success"] is True
    result = runner.invoke(cli, ["--json", command])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert initial_data["parse_issues_scope"] == "files_parsed_this_run"
    assert data["parse_issues_scope"] == "index_reused"
    assert "parse_issues" not in data
    text = runner.invoke(cli, [command])
    assert text.exit_code == 0, text.output
    assert "Parse issues cover only files parsed in this run" in text.output


def _init_git(path, monkeypatch):
    import os

    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    template = path / "empty-template"
    template.mkdir()
    subprocess.run(["git", "init", "-q", f"--template={template}"], check=True, cwd=path)
