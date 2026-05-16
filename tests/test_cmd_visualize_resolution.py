"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam visualize``.

``cmd_visualize`` resolves a single optional ``--focus`` symbol via
``find_symbol``; without ``--focus`` the command runs in overview mode
(top-N by PageRank) and never reaches the resolver, so the envelope
stays at the no-op ``resolution=symbol`` default. The disclosure is
only meaningful on the ``--focus`` branch:

* Exact match -> ``resolution=symbol`` + ``partial_success=False``.
* Fuzzy LIKE-fallback -> ``resolution=fuzzy`` + ``partial_success=True``
  + ``[fuzzy resolution]`` verdict suffix.
* Unresolved focus in --json mode -> structured envelope with
  ``resolution=unresolved`` (text mode keeps the legacy ClickException
  for stderr-shape compatibility).
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


class TestVisualizeResolution:
    """W1245 — ``roam visualize --focus`` resolution disclosure."""

    def test_exact_match_focus_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["visualize", "--focus", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_focus_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """``--focus create_us`` matches ``create_user`` via LIKE-fallback."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["visualize", "--focus", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW-6 single-line verdict surfaces the degradation.
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_focus_emits_unresolved_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss focus in --json mode emits an unresolved envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["visualize", "--focus", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        # The command returns cleanly with an envelope rather than
        # propagating the ClickException (json_mode early-return path).
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
