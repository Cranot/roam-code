"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam plan``.

``cmd_plan`` walks ``find_symbol`` at TWO callsites: the ``--symbol``
option and the positional ``target`` argument (when it doesn't look
like a file path). The two callsites are mutually exclusive at runtime,
so a single ``resolution_tier`` + ``resolved_target`` pair is threaded
out of ``_resolve_plan_targets`` and merged into the envelope summary
plus top-level.
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


class TestPlanResolution:
    """W1245 — ``roam plan`` resolution disclosure (both callsites)."""

    def test_exact_match_positional_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Positional ``target`` -- exact match -> ``resolution=symbol``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["plan", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_via_symbol_option_emits_fuzzy_resolution(
        self, indexed_project, cli_runner, monkeypatch
    ) -> None:
        """``--symbol`` -- LIKE-fallback substring -> ``resolution=fuzzy`` + suffix.

        Exercises the OTHER find_symbol callsite (line 399 in the
        resolver helper) so we lock both paths.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["plan", "--symbol", "create_us"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW-6 single-line verdict surfaces the degradation.
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_emits_unresolved_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss emits an always-emit envelope with ``resolution=unresolved``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["plan", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        # ``plan`` always emits JSON in this branch (early-return path).
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
