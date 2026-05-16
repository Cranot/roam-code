"""W1244 — Pattern-2 variant-D resolution disclosure on ``roam diagnose``.

W1233 audit flagged ``diagnose`` NON-COMPLIANT in BOTH single-symbol and
``--batch`` modes: the resolver walks a 3-tier chain
(qualified-name -> simple-name -> fuzzy LIKE) but the envelope was silent
on which tier matched. An agent reading the JSON couldn't tell an exact
match from a fuzzy-LIKE fallback that landed on a wholly different
symbol -- so root-cause rankings derived from a degraded resolution
shipped as if they were fully-resolved successes.

W1241 hoisted ``resolution_disclosure()`` into ``roam.output.formatter``.
W1242 / W1243 applied it to ``impact`` / ``preflight``. W1244 applies it
here, in both single + batch modes:

* single mode merges ``resolution`` + ``partial_success`` into the
  envelope summary AND top-level; suffixes the verdict with
  ``[fuzzy resolution -- ...]`` when degraded so LAW-6 single-line
  consumers still see the disclosure;
* batch mode adds ``resolution`` + ``partial_success`` to EACH per-item
  entry (per the W324 cmd_annotate per-finding template) AND flips the
  top-level ``summary.partial_success`` true when ANY entry resolved
  non-exactly.

This test file locks the contract on both modes via small in-process
indexed projects.
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
    """Provide a Click CliRunner (Click 8.3+ removed mix_stderr)."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Single-mode tests
# ---------------------------------------------------------------------------


class TestDiagnoseSingleResolution:
    """Single-mode ``roam diagnose <name>`` resolution disclosure."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact qualified/simple-name match -> ``resolution=symbol``."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diagnose", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        # The single-mode envelope MUST disclose resolution + partial_success
        # in BOTH the summary block and the top-level envelope so consumers
        # reading either surface get the same signal.
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        # An exact-match verdict must NOT carry the fuzzy-resolution suffix.
        assert "[fuzzy resolution" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """LIKE-fallback match (substring) -> ``resolution=fuzzy`` + suffix."""
        monkeypatch.chdir(indexed_project)
        # The python_project fixture defines ``create_user``; ``create_us``
        # is not an exact name, so the resolver lands on the LIKE-fallback
        # tier (tier 3) and returns ``create_user`` as the best match.
        result = invoke_cli(cli_runner, ["diagnose", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW 6: the single-line verdict alone must signal the degradation.
        assert "[fuzzy resolution" in summary["verdict"]
        # ``target`` echoes the resolved symbol so the agent can confirm
        # which symbol roam actually ranked.
        assert summary["target"] == "create_user" or data["target"] == "create_user"

    def test_unresolved_input_exits_nonzero(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Total-miss input still exits nonzero (no envelope to disclose into).

        The pre-existing ``symbol_not_found`` path returns a stand-alone
        ``error`` envelope (not a ``diagnose`` envelope), so this test
        just confirms the previously-working contract still holds: a
        completely-unmatched name fails fast. Unresolved disclosure on
        the success path is what variant-D guards against; the not-found
        case is already explicit by exit code + error message.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["diagnose", "definitely_no_such_symbol_zzz"])
        # Exit nonzero OR "not found" in output -- matches the legacy
        # test_diagnose_unknown contract.
        assert result.exit_code != 0 or "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Batch-mode tests
# ---------------------------------------------------------------------------


class TestDiagnoseBatchResolution:
    """Batch-mode ``roam diagnose --batch`` resolution disclosure."""

    def test_batch_all_exact_no_partial_success(self, indexed_project, cli_runner, monkeypatch) -> None:
        """All-exact batch -> every entry ``resolution=symbol``, top-level partial_success=False."""
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(
            __import__("roam.cli", fromlist=["cli"]).cli,
            ["--json", "diagnose", "--batch", "-"],
            input="create_user\nget_display\n",
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        assert data["command"] == "diagnose.batch"
        results = data["results"]
        assert len(results) == 2
        for entry in results:
            assert entry["resolution"] == "symbol"
            assert entry["partial_success"] is False
        # Top-level partial_success stays False when nothing was degraded.
        assert data["summary"]["partial_success"] is False
        assert data["partial_success"] is False

    def test_batch_mixed_flips_top_level_partial_success(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Mix of exact + fuzzy -> per-item disclosure correct AND top-level partial_success=True."""
        monkeypatch.chdir(indexed_project)
        # ``create_user`` is exact (tier 1/2 match); ``create_us`` resolves
        # via LIKE-fallback to ``create_user`` (tier 3 -> fuzzy). Result:
        # per-item disclosure differs AND the top-level partial_success
        # MUST flip true so a consumer scanning only the summary still
        # sees the degradation signal.
        result = cli_runner.invoke(
            __import__("roam.cli", fromlist=["cli"]).cli,
            ["--json", "diagnose", "--batch", "-"],
            input="create_user\ncreate_us\n",
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        results = data["results"]
        assert len(results) == 2
        by_name = {entry["name"]: entry for entry in results}
        assert by_name["create_user"]["resolution"] == "symbol"
        assert by_name["create_user"]["partial_success"] is False
        assert by_name["create_us"]["resolution"] == "fuzzy"
        assert by_name["create_us"]["partial_success"] is True
        # Top-level signal flips on ANY degraded entry.
        assert data["summary"]["partial_success"] is True
        assert data["partial_success"] is True

    def test_batch_unresolved_entry_flips_top_level_and_marks_entry(
        self, indexed_project, cli_runner, monkeypatch
    ) -> None:
        """A symbol-not-found entry gets ``resolution=unresolved`` and trips top-level partial_success."""
        monkeypatch.chdir(indexed_project)
        result = cli_runner.invoke(
            __import__("roam.cli", fromlist=["cli"]).cli,
            ["--json", "diagnose", "--batch", "-"],
            input="create_user\n_no_such_symbol_zzz\n",
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        results = data["results"]
        assert len(results) == 2
        by_name = {entry["name"]: entry for entry in results}
        # The resolved entry stays clean.
        assert by_name["create_user"]["resolution"] == "symbol"
        # The unresolved entry MUST carry the variant-D shape so a consumer
        # iterating per-item gets the same disclosure surface as the
        # resolved entries -- not a bare ``error`` string.
        assert by_name["_no_such_symbol_zzz"]["resolution"] == "unresolved"
        assert by_name["_no_such_symbol_zzz"]["partial_success"] is True
        # And the top-level partial_success flips so summary-only consumers
        # still see the degradation.
        assert data["summary"]["partial_success"] is True
        assert data["partial_success"] is True
