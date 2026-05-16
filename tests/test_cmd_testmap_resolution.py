"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam test-map``.

``cmd_testmap`` accepts a single symbol-or-path target. If the target
contains ``/`` or ``.`` and resolves as a file, the file branch is
taken (no resolver involvement, ``resolution=symbol`` default). Else
``find_symbol`` is called once and the matched tier is threaded into
``_test_map_symbol_json``. An unresolved target emits a structured
unresolved envelope rather than only ``found=False``.
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


class TestTestmapResolution:
    """W1245 — ``roam test-map <symbol-or-path>`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-map", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """``create_us`` matches ``create_user`` via the LIKE-fallback rung.

        Note: ``create_us`` contains no ``/`` or ``.``, so the symbol
        branch is taken directly (file-path branch only fires when the
        input looks like a path).
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-map", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_emits_unresolved_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total miss exits non-zero with a structured unresolved envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-map", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        # cmd_testmap raises SystemExit(1) on the not-found path; the
        # envelope is still emitted before the exit.
        assert result.exit_code != 0
        raw = getattr(result, "stdout", None) or result.output
        data = json.loads(raw)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
