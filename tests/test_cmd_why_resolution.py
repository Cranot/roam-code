"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam why``.

``cmd_why`` accepts one-or-more positional symbol names; each name is
resolved via ``find_symbol`` inside ``_analyze_symbol``. The JSON
envelope:

* Stamps a per-entry ``resolution`` + ``partial_success`` on every
  ``symbols[]`` row (per the W324 cmd_annotate per-finding template).
* Flips top-level ``summary.partial_success`` and top-level
  ``partial_success`` when ANY entry resolved non-exactly.
* Suffixes the single-target verdict with ``[fuzzy resolution]`` so
  LAW-6 consumers reading only the verdict see the degradation.
* Emits ``resolution="unresolved"`` on entries where ``find_symbol``
  returned ``None`` (the entry's ``error`` field stays as before).
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


class TestWhyResolution:
    """W1245 — ``roam why`` resolution disclosure (single + batch)."""

    def test_single_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["why", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        symbols = data["symbols"]
        assert len(symbols) == 1
        assert symbols[0]["resolution"] == "symbol"
        assert symbols[0]["partial_success"] is False
        assert summary["partial_success"] is False
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_single_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Single-target fuzzy -> per-entry + top-level partial_success + LAW-6 suffix."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["why", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        symbols = data["symbols"]
        assert len(symbols) == 1
        assert symbols[0]["resolution"] == "fuzzy"
        assert symbols[0]["partial_success"] is True
        assert summary["partial_success"] is True
        assert data["partial_success"] is True
        # LAW-6 single-line verdict surfaces the degradation.
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_emits_unresolved_entry(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Unresolved name -> entry carries ``resolution=unresolved`` + flag."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["why", "definitely_no_such_symbol_zzz"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        symbols = data["symbols"]
        assert len(symbols) == 1
        assert symbols[0]["resolution"] == "unresolved"
        assert symbols[0]["partial_success"] is True
        assert "error" in symbols[0]
        # Top-level signals the degradation.
        assert data["summary"]["partial_success"] is True
        assert data["partial_success"] is True
