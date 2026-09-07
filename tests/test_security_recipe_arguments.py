"""Exercise the security recipe's real CLI arguments and declared scope."""

from __future__ import annotations

import shlex

import pytest

from tests import test_critique as fixtures

critique_project = fixtures.critique_project


@pytest.mark.parametrize("symbol", ["", "refresh_session"])
def test_security_recipe_preserves_inventory_and_scope(critique_project, monkeypatch, symbol):
    from roam.mcp_server import for_security_review

    monkeypatch.setenv("ROAM_MCP_HANDLE_KB", "0")
    result = for_security_review(symbol=symbol, root=str(critique_project))
    assert "vulns" in result, result.get("_errors")
    assert result["vulns"]["command"] == "vulns"
    assert "adversarial" in result, result.get("_errors")
    if symbol:
        assert result["summary"]["partial_success"] is True
        assert result["summary"]["resolution"] == "unsupported_symbol_scope"
        assert "symbol scope was not applied" in result["summary"]["verdict"]
    else:
        assert result["summary"]["resolution"] == "repository_and_changeset"
    assert result["scope"]["adversarial"] == "working_tree_changes"


def test_retrieval_bench_hint_is_one_executable_roam_command():
    from roam.cli import cli
    from roam.commands.cmd_critique import _BENCH_RELEVANCE_RULES

    hint = _BENCH_RELEVANCE_RULES[0][1]
    argv = shlex.split(hint)
    assert argv[:2] == ["roam", "eval-retrieve"]
    # Parse the actual command's arguments without launching an expensive bench.
    with cli.make_context("roam", argv[1:]) as parent:
        command = cli.get_command(parent, argv[1])
        with command.make_context(argv[1], argv[2:], parent=parent):
            pass


@pytest.mark.parametrize("partial", [False, True])
def test_security_recipe_keeps_partial_child_evidence(critique_project, monkeypatch, partial):
    from roam import mcp_server as mcp

    def child(args, root):
        return {
            "command": args[0],
            "summary": {"verdict": "observations", "partial_success": partial and args[0] == "taint"},
        }

    monkeypatch.setattr(mcp, "_safe_run", child)
    monkeypatch.setattr(mcp, "_session_partial_success_count", 0)
    result = mcp.for_security_review(root=str(critique_project))
    assert result["summary"]["partial_success"] is partial
    assert result["summary"]["check_status"]["taint"] == ("incomplete" if partial else "completed")
    assert ("incomplete" in result["summary"]["verdict"]) is partial
    assert mcp._session_partial_success_count == int(partial)
