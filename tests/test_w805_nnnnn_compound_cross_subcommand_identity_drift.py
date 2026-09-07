"""Security-recipe scope and child-failure regression controls.

The original W805 probe demonstrated two invalid positional arguments:
``vulns list`` and ``adversarial <symbol>``. The recipe now calls the valid
CLI surfaces and discloses that symbol filtering was not applied. Keep real
single/ambiguous-corpus controls, plus injected genuine child failures, instead
of asserting that the repaired usage errors must still occur.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from roam import mcp_server as mcp
from tests.conftest import index_in_process


@pytest.fixture(autouse=True)
def isolated_compound_state(monkeypatch):
    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    mcp._reset_error_storm()
    yield
    mcp._reset_error_storm()


@pytest.fixture(params=[1, 3], ids=["single-symbol", "ambiguous-symbol"])
def symbol_corpus(tmp_path, monkeypatch, request):
    repo = tmp_path / "security-corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for index in range(request.param):
        (repo / f"auth_{index}.py").write_text("def handleAuth(user):\n    return user\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    for args in (["init", "-q"], ["add", "."], ["-c", f"core.hooksPath={os.devnull}", "commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=True)
    monkeypatch.chdir(repo)
    output, code = index_in_process(repo, "--force")
    assert code == 0, output
    return repo


@pytest.mark.parametrize("symbol", ["", "handleAuth"])
def test_real_children_run_and_disclose_their_actual_scope(symbol_corpus, symbol):
    """Valid CLI invocations stay valid; a symbol request cannot narrow them."""
    result = mcp.for_security_review(symbol=symbol, root=str(symbol_corpus))
    assert result["command"] == "for-security-review"
    summary = result["summary"]
    assert summary["target"] == (symbol or "(full repo)")
    for name in ("vulns", "adversarial"):
        assert name in summary["sections"], result.get("_errors")
        assert name not in summary.get("failed_subcommands", [])
        assert result[name]["command"] == name
        assert not result[name].get("isError")
        assert not result[name].get("error")
    # This fixture has no working changes: critique must remain a genuine
    # EMPTY_INPUT failure, not be made green to satisfy the successful children.
    assert "critique" in summary["failed_subcommands"]
    assert summary["check_status"]["critique"] == "failed"
    assert summary["partial_success"] is True
    assert result["scope"] == {
        "requested_symbol": symbol or None,
        "symbol_scope_applied": False,
        "taint": "repository",
        "vulns": "saved_inventory",
        "critique": "tracked_changes_against_HEAD",
        "adversarial": "working_tree_changes",
    }
    if symbol:
        assert summary["resolution"] == "unsupported_symbol_scope"
        assert "symbol scope was not applied" in summary["verdict"]
    else:
        assert summary["resolution"] == "repository_and_changeset"
        assert "symbol scope was not applied" not in summary["verdict"]
    # No child resolves the requested symbol, even when three definitions share
    # its name. This is scope disclosure, not qualified symbol-id resolution.
    for name in ("taint", "vulns", "critique", "adversarial"):
        child_summary = (result.get(name) or {}).get("summary") or {}
        assert not any(
            child_summary.get(key) for key in ("resolved_symbol", "resolved_file", "target_id", "symbol_id", "file_id")
        )


@pytest.mark.parametrize("shape", ["legacy", "structured", "trimmed"])
def test_genuine_child_errors_still_reach_failed_subcommands(symbol_corpus, monkeypatch, shape):
    """The repair must not weaken legacy, structured or coalesced error handling."""
    calls = []

    def child(args, root):
        calls.append(args)
        if args[0] in {"vulns", "adversarial"}:
            if shape == "legacy":
                return {"error": "injected usage failure"}
            result = {"isError": True, "error_code": "USAGE_ERROR"}
            if shape == "structured":
                result["error"] = "injected usage failure"
            else:
                result["first_error_message"] = "injected usage failure"
            return result
        return {"command": args[0], "summary": {"verdict": "checked"}}

    monkeypatch.setattr(mcp, "_safe_run", child)
    result = mcp.for_security_review(symbol="handleAuth", root=str(symbol_corpus))
    assert sorted(calls) == sorted([["taint"], ["vulns"], ["critique", "--working-tree"], ["adversarial"]])
    summary = result["summary"]
    assert set(summary["failed_subcommands"]) == {"vulns", "adversarial"}
    assert set(summary["sections"]) == {"taint", "critique"}
    assert summary["partial_success"] is True
    for name in ("vulns", "adversarial"):
        assert summary["check_status"][name] == "failed"
        assert name not in result
