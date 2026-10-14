"""A missing file fails the same way through the CLI and the MCP surface.

``roam deps`` and ``roam file`` exit 1 for an unknown file in every output
mode. The MCP bridge must still hand the agent the structured
``file_not_found`` envelope, annotated ``isError`` with the CLI exit code.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.mcp_server import _run_roam
from tests.conftest import make_src_project as _make_project


@pytest.fixture
def indexed_project(tmp_path):
    proj = _make_project(tmp_path, {"a.py": "x = 1\n"})
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        assert CliRunner().invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


@pytest.mark.parametrize("command", ["deps", "file"])
def test_missing_file_mcp_returns_envelope_with_is_error(indexed_project, command):
    result = _run_roam([command, "no_such_file.py"])
    assert result.get("isError") is True, result
    assert (result.get("summary") or {}).get("error") == "file_not_found", result
    assert result.get("command") == command
    assert (result.get("_meta") or {}).get("cli_exit_code") == 1, result


@pytest.mark.parametrize("command", ["deps", "file"])
def test_missing_file_exit_code_same_in_text_and_json(indexed_project, command):
    runner = CliRunner()
    as_json = runner.invoke(cli, ["--json", command, "no_such_file.py"])
    as_text = runner.invoke(cli, [command, "no_such_file.py"])
    assert as_json.exit_code == as_text.exit_code == 1


@pytest.mark.parametrize("command", ["deps", "file"])
def test_existing_file_still_exits_zero_in_json(indexed_project, command):
    result = CliRunner().invoke(cli, ["--json", command, "a.py"])
    assert result.exit_code == 0, result.output
