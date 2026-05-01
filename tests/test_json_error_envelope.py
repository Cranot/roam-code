"""Lock in the JSON-envelope-on-error contract for cmd_uses / cmd_deps /
cmd_trace / cmd_context.

Pre-v12, these four commands printed plaintext "Symbol not found" or
"File not found" hints to stdout when invoked with ``--json`` and the
target was missing. Downstream parsers (the recipe runner in
``roam ask``, MCP tool wrappers, ``--json | jq`` pipelines) then
crashed on the non-JSON output.

The fix: when ``json_mode`` is set, emit a structured envelope with
``summary.error`` and ``hint`` populated, then exit 1. The exit code
preserves the legacy contract; only the stdout shape changes.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project


@pytest.fixture
def empty_project(tmp_path):
    proj = _make_project(tmp_path, {"a.py": "x = 1\n"})
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


def _assert_envelope(stdout: str, command: str, *, expect_error: str) -> dict:
    """Parse stdout and assert the envelope shape on error."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--json {command} produced non-JSON stdout: {exc}\n{stdout!r}")
    assert data["command"] == command, f"command field is {data.get('command')!r}"
    summary = data.get("summary") or {}
    assert "verdict" in summary
    assert summary.get("error") == expect_error
    assert "hint" in data
    return data


class TestUsesJSONError:
    def test_unknown_symbol_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "uses", "NoSuchSymbol"])
        assert result.exit_code != 0
        _assert_envelope(result.output, "uses", expect_error="symbol_not_found")


class TestDepsJSONError:
    def test_unknown_file_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "deps", "no_such_file.py"])
        assert result.exit_code != 0
        _assert_envelope(result.output, "deps", expect_error="file_not_found")


class TestTraceJSONError:
    def test_unknown_source_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "trace", "Nope", "AlsoNope"])
        assert result.exit_code != 0
        _assert_envelope(result.output, "trace", expect_error="symbol_not_found")


class TestContextJSONError:
    def test_unknown_file_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "context", "--for-file", "no_such_file.py"],
        )
        assert result.exit_code != 0
        _assert_envelope(result.output, "context", expect_error="file_not_found")


class TestFileCmdJSONError:
    def test_unknown_file_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "file", "no_such_file.py"])
        assert result.exit_code != 0
        _assert_envelope(result.output, "file", expect_error="file_not_found")


class TestSplitCmdJSONError:
    def test_unknown_file_emits_envelope(self, empty_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "split", "no_such_file.py"])
        assert result.exit_code != 0
        _assert_envelope(result.output, "split", expect_error="file_not_found")


class TestExitCodePreserved:
    """The contract says: --json on error still exits non-zero. The
    JSON shape changed, the exit code did not."""

    @pytest.mark.parametrize(
        "args",
        [
            ["uses", "NoSuchSymbol"],
            ["deps", "nope.py"],
            ["trace", "Nope", "AlsoNope"],
            ["context", "--for-file", "nope.py"],
            ["file", "nope.py"],
            ["split", "nope.py"],
        ],
    )
    def test_plaintext_path_still_exits_nonzero(self, empty_project, args):
        runner = CliRunner()
        result = runner.invoke(cli, args)  # no --json
        assert result.exit_code != 0
