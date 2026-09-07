"""An empty pipe cannot authorize reviewing a different change."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_critique
from tests import test_critique as _fixtures

_DIFF_REFRESH_ONLY = _fixtures._DIFF_REFRESH_ONLY
critique_project = _fixtures.critique_project


@pytest.mark.parametrize("payload", ["", "   \n"])
@pytest.mark.parametrize("mode", ["text", "json", "sarif"])
def test_empty_pipe_refuses_before_selecting_a_git_target(monkeypatch, payload, mode):
    monkeypatch.setattr(
        cmd_critique, "_read_implicit_review_diff", lambda **kw: pytest.fail("selected a different change")
    )
    args = ["critique"] if mode == "text" else [f"--{mode}", "critique"]
    result = CliRunner().invoke(cli, args, input=payload)
    assert result.exit_code == 2, (result.output, result.exception)
    if mode == "json":
        data = json.loads(result.stdout)
        assert data["error_code"] == "EMPTY_INPUT"
        assert data["summary"]["partial_success"] is True
    else:
        assert "empty" in result.output.lower()
    assert "--working-tree" in result.output


def test_failed_git_producer_cannot_review_a_dirty_tree(critique_project):
    path = critique_project / "src" / "auth.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# unrelated edit\n", encoding="utf-8")
    failed = subprocess.run(
        ["git", "diff", "roam-intentionally-missing-ref"], cwd=critique_project, capture_output=True, text=True
    )
    assert failed.returncode != 0 and not failed.stdout
    reviewed = subprocess.run(
        [sys.executable, "-m", "roam", "--json", "critique"],
        cwd=critique_project,
        input=failed.stdout,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert reviewed.returncode == 2, reviewed.stdout + reviewed.stderr
    assert json.loads(reviewed.stdout)["error_code"] == "EMPTY_INPUT"


def test_explicit_working_tree_never_falls_back_to_last_commit(critique_project, monkeypatch):
    calls = []

    def diff(root, revision):
        calls.append(revision)
        return ("", None) if revision == "HEAD" else (_DIFF_REFRESH_ONLY, None)

    monkeypatch.setattr(cmd_critique, "_run_git_diff", diff)
    result = CliRunner().invoke(cli, ["--json", "critique", "--working-tree"], input="")
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error_code"] == "EMPTY_INPUT"
    assert calls == ["HEAD"]


@pytest.mark.parametrize("option", ["--input", "--batch"])
def test_explicit_working_tree_rejects_conflicting_source(tmp_path, option):
    path = tmp_path if option == "--batch" else tmp_path / "change.diff"
    if option == "--input":
        path.write_text(_DIFF_REFRESH_ONLY, encoding="utf-8")
    result = CliRunner().invoke(cli, ["--json", "critique", "--working-tree", option, str(path)])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error_code"] == "INVALID_OPTIONS"


def test_valid_pipe_keeps_caller_supplied_diff(critique_project):
    result = CliRunner().invoke(cli, ["--json", "critique", "--intent", "refresh session"], input=_DIFF_REFRESH_ONLY)
    assert result.exit_code in (0, 5), result.output
    data = json.loads(result.stdout)
    assert data["summary"]["review_source"] == "piped_diff"
    assert data["summary"]["intent"] == "refresh session"


def test_mcp_empty_diff_remains_a_structured_refusal(critique_project):
    from roam.mcp_server import critique_patch

    data = critique_patch(diff_text="", root=str(critique_project))
    assert data["isError"] is True
    assert data["error_code"] == "EMPTY_INPUT"


@pytest.mark.parametrize("dirty", [False, True])
def test_security_compound_selects_working_tree_explicitly(critique_project, monkeypatch, dirty):
    from roam.mcp_server import for_security_review

    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    if dirty:
        path = critique_project / "src" / "auth.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# intended working-tree edit\n", encoding="utf-8")
    # Execute the decorated compound's real recipe. Other security detectors
    # retain their own findings/limitations; this asserts the review input only.
    result = for_security_review(root=str(critique_project))
    if dirty:
        assert "critique" in result, result
        review = result["critique"]
        assert review["summary"]["review_source"] == "working_tree"
        assert review["summary"]["changed_files"] == 1
    else:
        assert "critique" in result["summary"]["failed_subcommands"], result
        refusal = next(error for error in result["_errors"] if error["command"] == "critique")
        assert refusal["error"].startswith("EMPTY_INPUT:"), refusal
