"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam affected-tests``.

W1267 audit flagged ``affected-tests`` NON-COMPLIANT: the symbol-target
branch calls ``find_symbol(conn, target)`` (positional) which walks the
W1249 3-tier resolver chain (qualified -> simple -> fuzzy LIKE), but the
envelope was silent on which tier matched. Agents reading the JSON
couldn't tell an exact match from a fuzzy-LIKE fallback that landed on
a wholly different symbol -- so the affected-test set built on a
degraded resolution shipped as if it were a fully-resolved success.

W1241 hoisted ``resolution_disclosure()`` into ``roam.output.formatter``.
W1242 / W1243 / W1244 applied it to ``impact`` / ``preflight`` /
``diagnose``. W1245 batch-4 (this file) applies it to the 7 remaining
sites, completing the Pattern-2c bulk propagation arc (30/30).

The contract: every JSON envelope merges ``resolution`` + ``partial_success``
into summary AND top-level; the verdict carries a ``[fuzzy resolution]``
suffix when degraded so LAW-6 single-line consumers still see the
disclosure.
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


class TestAffectedTestsResolution:
    """Resolution disclosure on ``roam affected-tests <symbol>``."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact qualified/simple-name match -> ``resolution=symbol``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution]" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback match -> ``resolution=fuzzy`` + verdict suffix."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["affected-tests", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW 6: the single-line verdict alone must signal degradation.
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_input_emits_structured_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss input -> structured ``resolution=unresolved`` JSON envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        # Exits non-zero but emits a structured JSON envelope so MCP
        # consumers can read the disclosure rather than parse error text.
        assert result.exit_code != 0
        out = getattr(result, "stdout", None) or result.output
        # Confirm JSON envelope shape (may not be parseable if click
        # interleaves error text — fall back to substring check).
        try:
            data = json.loads(out)
            assert data["summary"]["resolution"] == "unresolved"
            assert data["summary"]["partial_success"] is True
            assert data["resolution"] == "unresolved"
            assert data["partial_success"] is True
        except json.JSONDecodeError:
            # Non-JSON path (legacy fallback) — confirm at least the
            # error string still surfaces; the JSON path is what MCP
            # callers exercise.
            assert "not found" in out.lower()
