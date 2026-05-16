"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam test-scaffold``.

W1267 audit flagged ``test-scaffold`` NON-COMPLIANT on the symbol-name
branch: when the input looks neither slashy nor extension-y (so the
file-path probe is skipped), the command calls ``find_symbol(conn,
name)`` (positional). A fuzzy-LIKE-fallback would silently scaffold
tests for the substring-matched symbol's containing scope, not the one
the agent typed -- the test file would land on a wrong target's
location.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


class TestTestScaffoldResolution:
    """Resolution disclosure on ``roam test-scaffold <symbol>``."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution]" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """``create_us`` is a substring of ``create_user`` -- the file-path
        probe also runs because the name contains an underscore but no
        slash + no ``.`` extension. ``"."`` heuristic in the source means
        names with underscores STILL go through the file branch first;
        when nothing matches, they fall through to ``find_symbol``.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "definitely_no_such_symbol_zzz"])
        assert result.exit_code != 0 or "not found" in result.output.lower()
