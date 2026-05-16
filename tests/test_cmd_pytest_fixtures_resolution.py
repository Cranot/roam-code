"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam pytest-fixtures``.

The command resolves a fixture / test name via ``find_symbol`` which
walks a 3-tier chain (qualified-name -> simple-name -> fuzzy LIKE).
An agent walking a fixture dependency chain must know whether the
resolver landed on the symbol it asked for or fell back to a fuzzy
substring match -- a fuzzy fallback typically picks a different
fixture, making the returned chain a chain for the wrong target.
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


class TestPytestFixturesResolution:
    """W1245 — ``roam pytest-fixtures`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact name match -> ``resolution=symbol``, no fuzzy suffix.

        ``create_user`` is a plain function (not a pytest fixture) but
        the command still resolves it via ``find_symbol`` and emits a
        ``"has no fixture dependencies"`` envelope. The disclosure shape
        is the same regardless of whether ``chain`` is empty.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pytest-fixtures", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback substring match -> ``resolution=fuzzy`` + suffix."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["pytest-fixtures", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_input_emits_unresolved_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss emits an always-emit envelope with ``resolution=unresolved``.

        ``cmd_pytest_fixtures`` follows W327 always-emit-on-empty-input
        discipline, so a total-miss DOES land in JSON (not an error
        envelope). The disclosure must shape the failed branch the same
        as the resolved branch so consumers don't have to special-case.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pytest-fixtures", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
