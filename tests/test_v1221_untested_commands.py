"""redactedbehavioural tests for previously-untested commands.

The audit found ``py-modern``, ``graph-stats``, ``mcp-status``,
``pre-commit``, and ``exit-codes`` had 0–1 references in the test
suite. One JSON-envelope test per command pins their basic contract
so we don't ship breakage on these surfaces silently.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


def test_py_modern_emits_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "py-modern"])
    assert result.exit_code in (0, 5), result.output
    payload = json.loads(result.output)
    assert payload["command"] == "py-modern"
    assert "verdict" in payload["summary"]


def test_py_modern_text_output_runs():
    runner = CliRunner()
    result = runner.invoke(cli, ["py-modern"])
    assert result.exit_code in (0, 5), result.output
    assert "VERDICT" in result.output or "modern" in result.output.lower()


def test_graph_stats_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "graph-stats"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert "nodes" in summary
    assert "edges" in summary


def test_graph_stats_file_scope():
    """``--scope file`` produces a smaller graph than the default symbol scope."""
    runner = CliRunner()
    sym = runner.invoke(cli, ["--json", "graph-stats", "--scope", "symbol"])
    fil = runner.invoke(cli, ["--json", "graph-stats", "--scope", "file"])
    assert sym.exit_code == 0 and fil.exit_code == 0
    sym_p = json.loads(sym.output)
    fil_p = json.loads(fil.output)
    # File graph must have fewer nodes than symbol graph
    assert fil_p["summary"]["nodes"] < sym_p["summary"]["nodes"]


def test_mcp_status_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "mcp-status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    for k in ("preset", "tools_registered", "max_concurrent"):
        assert k in summary


def test_pre_commit_print_emits_hook_script():
    runner = CliRunner()
    result = runner.invoke(cli, ["pre-commit", "--print"])
    assert result.exit_code == 0
    assert "#!/bin/sh" in result.output
    assert "git diff --cached" in result.output
    assert "ROAM_PRECOMMIT_SKIP" in result.output


def test_pre_commit_json_preview():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pre-commit"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "pre-commit"
    assert "script" in payload  # preview includes the script body


def test_exit_codes_lists_canonical_set():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "exit-codes"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    names = {row["name"] for row in payload.get("exit_codes", [])}
    for required in ("EXIT_SUCCESS", "EXIT_USAGE", "EXIT_GATE_FAILURE", "EXIT_INDEX_MISSING"):
        assert required in names


def test_exit_codes_text_format():
    runner = CliRunner()
    result = runner.invoke(cli, ["exit-codes"])
    assert result.exit_code == 0
    # Must show numeric codes
    assert "0" in result.output
    assert "5" in result.output  # gate failure
