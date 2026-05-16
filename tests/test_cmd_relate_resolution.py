"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam relate``.

``cmd_relate`` accepts ``--path`` plus zero-or-more positional symbols
and resolves each positional via ``find_symbol`` in a loop. The
envelope discloses tier per-input via a ``resolutions`` array AND
aggregates into a single top-level ``resolution`` field
(most-degraded-wins) so LAW-6 single-field consumers still see the
degradation. ``partial_success`` flips when ANY input resolved
non-exactly. Unresolved inputs in ``--json`` mode populate the
``resolutions`` array with a ``tier="unresolved"`` entry rather than
raising.
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


class TestRelateResolution:
    """W1245 — ``roam relate`` resolution disclosure (multi-input loop)."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """All-exact inputs -> top-level ``resolution=symbol`` + ``partial_success=False``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["relate", "create_user", "get_display"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]
        # Per-input tier array
        resolutions = data.get("resolutions") or []
        assert len(resolutions) == 2
        assert all(r["tier"] == "symbol" for r in resolutions)

    def test_fuzzy_match_flips_top_level_partial_success(self, indexed_project, cli_runner, monkeypatch) -> None:
        """One fuzzy + one exact -> top-level ``resolution=fuzzy`` + LAW-6 suffix."""
        monkeypatch.chdir(indexed_project)
        # ``create_us`` matches ``create_user`` via the LIKE-fallback rung.
        result = invoke_cli(cli_runner, ["relate", "create_us", "get_display"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW-6 single-line verdict surfaces the degradation.
        assert "[fuzzy resolution" in summary["verdict"]
        # Per-input array shows the mix.
        resolutions = data.get("resolutions") or []
        tiers = sorted(r["tier"] for r in resolutions)
        assert "fuzzy" in tiers
        assert "symbol" in tiers

    def test_unresolved_input_emits_unresolved_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """An unresolved input in --json mode populates the resolutions array."""
        monkeypatch.chdir(indexed_project)
        # Mix one valid + one unresolved so the command has SOMETHING to
        # analyse (input_ids non-empty) and emits the envelope.
        result = invoke_cli(
            cli_runner,
            ["relate", "create_user", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        # The valid input gives us one symbol; the command proceeds to
        # the envelope branch rather than the SystemExit text-mode path.
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
        # The unresolved entry is recorded with its raw input.
        resolutions = data.get("resolutions") or []
        unresolved_entries = [r for r in resolutions if r["tier"] == "unresolved"]
        assert len(unresolved_entries) == 1
        assert unresolved_entries[0]["input"] == "definitely_no_such_symbol_zzz"
