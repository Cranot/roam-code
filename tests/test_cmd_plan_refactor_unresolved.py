"""W1280 — Pattern-2c Convention (c) unresolved-path migration for ``plan-refactor``.

The W1278 audit found ``cmd_plan_refactor`` was one of three remaining
``symbol_not_found()`` callers still using Convention (b) (helper +
``SystemExit(1)``). plan-refactor is an analytical pure-analysis
composer -- a missing-symbol input is a recoverable typo, not a
tool/IO failure. W1280 migrates it onto Convention (c) (return 0 with
a ``resolution=unresolved`` + ``partial_success=True`` disclosure on
the JSON envelope; text mode keeps the FTS suggestion list).

Pinned behaviour:

1. JSON-mode unresolved -> exit 0 + envelope with resolution=unresolved
   + partial_success=True at BOTH summary and top-level
2. Text-mode unresolved -> exit 0 + "not found" in output (kept from
   the existing ``symbol_not_found`` helper)
3. Resolved happy path -> exit 0 + no ``resolution=unresolved`` (the
   existing W1245/W1249 variant-D resolution-tier disclosure stays
   intact)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_MISSING_NAME = "definitely_no_such_symbol_w1280_xyz"


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


class TestPlanRefactorUnresolvedConventionC:
    """W1280 Pattern-2c Convention (c) drift guard for plan-refactor."""

    def test_json_unresolved_exits_zero_with_disclosure(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan-refactor", _MISSING_NAME], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        assert data.get("command") == "plan-refactor", data
        # Top-level disclosure (mirrors cmd_dead --extinction shape).
        assert data["resolution"] == "unresolved", data
        assert data["partial_success"] is True, data
        # Summary-level disclosure (LAW-6 readability).
        summary = data["summary"]
        assert summary["resolution"] == "unresolved", summary
        assert summary["partial_success"] is True, summary
        assert summary["state"] == "not_found", summary
        assert "not found" in summary["verdict"].lower(), summary

    def test_text_unresolved_exits_zero_with_suggestions(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan-refactor", _MISSING_NAME])
        assert result.exit_code == 0, result.output
        assert "not found" in result.output.lower(), result.output

    def test_resolved_happy_path_no_unresolved_disclosure(self, indexed_project, cli_runner, monkeypatch) -> None:
        """The W1245/W1249 variant-D resolution-tier disclosure must stay
        intact on the resolved happy path -- this test pins the existing
        behaviour against accidental regressions from the W1280 unresolved
        edit.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan-refactor", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        assert data["resolution"] != "unresolved", data
        summary = data["summary"]
        assert summary["resolution"] != "unresolved", summary
        assert summary.get("partial_success") is not True or summary["resolution"] in {"fuzzy"}, summary
