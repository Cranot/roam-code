"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam invariants``.

``cmd_invariants`` has TWO ``find_symbol(conn, target)`` callsites that
are mutually exclusive at runtime:

1. The file-fallback rung inside the ``looks_like_file`` branch (the
   target had a known extension but no matching file row, so the
   command tries it as a symbol instead).
2. The bare-symbol branch when the target lacks a known file extension.

Because exactly one callsite fires per invocation, a single combined
disclosure (``resolution_tier`` + ``resolved_target``) is sufficient
(per the cmd_plan W1245-batch-2 single-disclosure pattern). The
``--public-api`` / ``--breaking-risk`` batch modes don't walk the
resolver and so don't emit a disclosure block.
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


class TestInvariantsResolution:
    """W1245 — ``roam invariants <target>`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Bare-symbol exact match -> ``resolution=symbol`` (callsite 2)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["invariants", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Bare-symbol fuzzy match -> ``resolution=fuzzy`` (callsite 2 LIKE rung)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["invariants", "create_us"], json_mode=True)
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
        """Total-miss emits ``resolution=unresolved`` on the empty-results envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["invariants", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
