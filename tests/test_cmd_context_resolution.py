"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam context``.

``cmd_context`` accepts multiple positional symbol names. Each name is
resolved via ``find_symbol`` in a loop. Both single-symbol mode and
batch mode must disclose the resolver tier:

* Single mode merges ``resolution`` / ``partial_success`` / ``target``
  into the envelope summary AND top-level; suffixes the verdict with
  ``[fuzzy resolution]`` when degraded so LAW-6 single-line consumers
  still see the degradation.
* Batch mode adds per-entry ``resolution`` + ``partial_success`` to
  each ``symbols[]`` entry (per the W324 cmd_annotate per-finding
  template) AND flips top-level ``summary.partial_success`` true when
  ANY entry resolved non-exactly.
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


class TestContextSingleResolution:
    """W1245 — single-symbol ``roam context <name>`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW-6 single-line verdict surfaces the degradation.
        assert "[fuzzy resolution" in summary["verdict"]


class TestContextBatchResolution:
    """W1245 — batch-mode ``roam context <a> <b> ...`` resolution disclosure."""

    def test_batch_all_exact_no_partial_success(self, indexed_project, cli_runner, monkeypatch) -> None:
        """All-exact batch -> every entry ``resolution=symbol``, top-level partial_success=False."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "create_user", "get_display"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        symbols = data["symbols"]
        assert len(symbols) == 2
        for entry in symbols:
            assert entry["resolution"] == "symbol"
            assert entry["partial_success"] is False
        assert data["summary"]["partial_success"] is False
        assert data["partial_success"] is False

    def test_batch_mixed_flips_top_level_partial_success(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Mix exact + fuzzy -> per-entry disclosure differs AND top-level flips."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["context", "create_user", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        symbols = data["symbols"]
        assert len(symbols) == 2
        # Both entries land on ``create_user`` (the fuzzy match converges).
        # We can't index by input name because the batch payload uses the
        # resolved name; verify the disclosure mix via per-entry tiers.
        tiers = sorted(entry["resolution"] for entry in symbols)
        assert "fuzzy" in tiers, f"expected at least one fuzzy entry, got {tiers}"
        assert "symbol" in tiers, f"expected at least one exact entry, got {tiers}"
        # Top-level signal flips on ANY degraded entry.
        assert data["summary"]["partial_success"] is True
        assert data["partial_success"] is True
