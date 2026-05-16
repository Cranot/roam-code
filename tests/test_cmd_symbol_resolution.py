"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam symbol``.

Locks in the W1245-batch-2 contract for ``cmd_symbol``: the envelope MUST
carry ``resolution`` + ``partial_success`` + ``target`` in BOTH the
``summary`` block and the top-level envelope, and a fuzzy-LIKE-fallback
match MUST suffix the verdict with ``[fuzzy resolution]`` so LAW-6
single-line consumers still see the degradation.
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


class TestSymbolResolution:
    """W1245 — ``roam symbol`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact name match -> ``resolution=symbol``, no fuzzy suffix."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        # Disclosure mirrored across summary AND top-level.
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        # Exact match -- no fuzzy-resolution suffix on the verdict.
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback (substring) match -> ``resolution=fuzzy`` + suffix."""
        monkeypatch.chdir(indexed_project)
        # ``create_us`` is not an exact name; resolver lands on the LIKE
        # tier and returns ``create_user`` as the best match.
        result = invoke_cli(cli_runner, ["symbol", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW-6 single-line verdict alone must signal the degradation.
        assert "[fuzzy resolution" in summary["verdict"]

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss exits nonzero via the pre-existing symbol_not_found path."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["symbol", "definitely_no_such_symbol_zzz"])
        assert result.exit_code != 0 or "not found" in result.output.lower()
