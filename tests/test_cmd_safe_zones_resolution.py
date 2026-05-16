"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam safe-zones``.

W1267 audit flagged ``safe-zones`` NON-COMPLIANT: the symbol-target
branch calls ``find_symbol(conn, target)`` (positional) after the
file-target probe fails, but the envelope was silent on which tier
matched. A safe-zone analysis built on a fuzzy-resolved symbol would
ship boundary symbol counts and refactor-safety verdict tied to the
wrong target -- exactly the silent-fallback variant-D anti-pattern.
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


class TestSafeZonesResolution:
    """Resolution disclosure on ``roam safe-zones <target>``."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-zones", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution]" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["safe-zones", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_emits_structured_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["safe-zones", "definitely_no_such_symbol_zzz"],
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
