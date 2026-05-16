"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam dead --extinction``.

W1233 audit identified ``cmd_dead`` among 34 resolver-using commands
that emit a single-symbol envelope but never disclose which rung of the
3-tier ``find_symbol`` chain (qualified-name -> simple-name -> fuzzy LIKE)
actually matched. The ``--extinction`` mode resolves a target symbol via
``find_symbol`` and then BFS-projects the orphan cascade if that symbol
were deleted. A fuzzy-LIKE fallback produces a real cascade, but for a
symbol that may not be the one the caller meant — the agent silently
consumes a degraded plan as if it were exact.

W1241 hoisted ``resolution_disclosure()`` into ``roam.output.formatter``.
W1249 stamps ``_resolution_tier`` on every ``find_symbol`` return.
W1245 batch-1 applies the disclosure to five high-traffic resolver
commands; this file pins the ``dead --extinction`` wiring on the three
tier outcomes:

* exact symbol match  -> ``resolution=symbol``,    partial_success=False
* fuzzy LIKE match    -> ``resolution=fuzzy``,     partial_success=True,
                         verdict carries ``[fuzzy resolution ...]`` suffix
* unresolved (missing)-> ``resolution=unresolved``, partial_success=True;
                         envelope returns rather than exiting non-zero
                         so the structured signal still reaches the agent
                         (variant-D: never collapse to a generic
                         COMMAND_FAILED).

Only the ``--extinction`` mode is affected; the default ``roam dead``
codebase scan does not resolve a target and is out of scope.
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


class TestDeadExtinctionResolution:
    """W1245 — ``roam dead --extinction <name>`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact-name match -> ``resolution=symbol`` + ``partial_success=False``.

        ``unused_helper`` is defined in the fixture's ``src/service.py``;
        the resolver lands on the exact-name rung. The envelope MUST
        disclose ``resolution=symbol`` + ``partial_success=False`` in
        both summary and top level, and the verdict must NOT carry the
        fuzzy-resolution suffix.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["dead", "--extinction", "unused_helper"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution" not in summary["verdict"]
        assert data["mode"] == "extinction"

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback substring -> ``resolution=fuzzy`` + verdict suffix.

        ``unused_help`` is not an exact name; ``find_symbol`` falls
        through the exact-name rungs and lands on the LIKE
        ``%unused_help%`` fallback, resolving to ``unused_helper``.
        Envelope must disclose the degradation and suffix the verdict so
        LAW-6 single-line consumers see it on the verdict alone.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["dead", "--extinction", "unused_help"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution" in summary["verdict"]
        # ``target`` echoes the resolved symbol.
        assert summary.get("target") == "unused_helper" or data.get("target") == "unused_helper"

    def test_unresolved_input_emits_unresolved_disclosure(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss input -> ``resolution=unresolved`` + ``partial_success=True``.

        Unlike ``safe-delete`` / ``closure`` (which exit non-zero via the
        shared ``symbol_not_found`` helper), ``dead --extinction`` returns
        a normal-shape envelope on the not-found path. W1245 ensures that
        envelope carries the unresolved disclosure rather than silently
        emitting ``"error": "Symbol not found"`` with no resolution
        signal — agents reading the structured summary still see the
        Pattern-2 variant-D shape on the failed-resolution branch.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["dead", "--extinction", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]

        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
        # The error message stays for human consumers.
        assert "Symbol not found" in summary.get("error", "")
        # ``target`` echoes the (unresolved) user input.
        assert (
            summary.get("target") == "definitely_no_such_symbol_zzz"
            or data.get("target") == "definitely_no_such_symbol_zzz"
        )
