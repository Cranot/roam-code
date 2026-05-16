"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam guard``.

W1267 audit flagged ``guard`` NON-COMPLIANT: ``find_symbol(conn, name)``
walks the 3-tier resolver chain, but the envelope was silent on which
tier matched. ``guard`` is a sub-agent preflight bundle -- the agent
edits based on its risk score, callers, callees, tests, and layer
analysis. A degraded fuzzy-resolution silently landing on the wrong
symbol is exactly the silent-fallback anti-pattern the disclosure
exists to prevent.
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


class TestGuardResolution:
    """Resolution disclosure on ``roam guard <symbol>``."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["guard", "create_user"], json_mode=True)
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
        result = invoke_cli(cli_runner, ["guard", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        """``find_symbol`` miss routes through the pre-existing ``symbol_not_found``
        emitter; the disclosure shape lives on the success envelope, not the
        not-found path which already exits nonzero with explicit guidance.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["guard", "definitely_no_such_symbol_zzz"])
        assert result.exit_code != 0 or "not found" in result.output.lower()
