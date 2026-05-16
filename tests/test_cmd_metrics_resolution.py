"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam metrics``.

W1267 audit flagged ``metrics`` NON-COMPLIANT: the ``_resolve_target``
helper walks file-path then symbol-name resolvers; the symbol branch
calls ``find_symbol(conn, target)`` (positional) and silently absorbed
fuzzy-LIKE-fallback matches. The unified metrics envelope shipped
``target_type=symbol`` regardless of whether the resolver landed on
the exact name or a substring match -- agents reading per-symbol
complexity/coverage/centrality couldn't tell the difference.

W1245 disclosure: only the symbol path attaches ``resolution`` /
``partial_success`` since the file path is the user's intended primary
type (not a fallback).
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


class TestMetricsResolution:
    """Resolution disclosure on ``roam metrics <target>``."""

    def test_exact_symbol_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["metrics", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution]" not in summary["verdict"]

    def test_fuzzy_symbol_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["metrics", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_emits_structured_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Unknown target -> ``resolution=unresolved`` envelope in JSON mode."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["metrics", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        assert result.exit_code != 0
        out = getattr(result, "stdout", None) or result.output
        try:
            data = json.loads(out)
            assert data["summary"]["resolution"] == "unresolved"
            assert data["summary"]["partial_success"] is True
            assert data["resolution"] == "unresolved"
            assert data["partial_success"] is True
        except json.JSONDecodeError:
            assert "not found" in out.lower()
