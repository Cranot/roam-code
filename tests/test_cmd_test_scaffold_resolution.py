"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam test-scaffold``.

W1267 audit flagged ``test-scaffold`` NON-COMPLIANT on the symbol-name
branch: when the input looks neither slashy nor extension-y (so the
file-path probe is skipped), the command calls ``find_symbol(conn,
name)`` (positional). A fuzzy-LIKE-fallback would silently scaffold
tests for the substring-matched symbol's containing scope, not the one
the agent typed -- the test file would land on a wrong target's
location.
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


class TestTestScaffoldResolution:
    """Resolution disclosure on ``roam test-scaffold <symbol>``."""

    def test_exact_match_emits_symbol_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "create_user"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "symbol"
        assert summary["partial_success"] is False
        assert data["resolution"] == "symbol"
        assert data["partial_success"] is False
        assert "[fuzzy resolution]" not in summary["verdict"]

    def test_fuzzy_match_emits_fuzzy_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """``create_us`` is a substring of ``create_user`` -- the file-path
        probe also runs because the name contains an underscore but no
        slash + no ``.`` extension. ``"."`` heuristic in the source means
        names with underscores STILL go through the file branch first;
        when nothing matches, they fall through to ``find_symbol``.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "create_us"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "fuzzy"
        assert summary["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        assert "[fuzzy resolution]" in summary["verdict"]

    def test_unresolved_input_emits_convention_c_envelope(self, indexed_project, cli_runner, monkeypatch) -> None:
        """W1278a / W1280 Pattern-2c Convention (c): unresolved symbol
        exits 0 with ``resolution=unresolved`` + ``partial_success=True``
        + ``state=not_found`` disclosure so agents can distinguish a
        name-typo from a tool/IO failure. Mirrors cmd_guard.py shape.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-scaffold", "definitely_no_such_symbol_zzz"],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "unresolved"
        assert summary["partial_success"] is True
        assert summary["state"] == "not_found"
        assert "not found" in summary["verdict"].lower()
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True

    def test_unresolved_input_text_mode_still_lists_suggestions(self, indexed_project, cli_runner, monkeypatch) -> None:
        """W1278a: text-mode unresolved output keeps the FTS suggestion
        list (the most useful next step for a human staring at a typo).
        Exit 0 per Convention (c)."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "definitely_no_such_symbol_zzz"])
        assert result.exit_code == 0, result.output
        assert "not found" in result.output.lower()


class TestTestScaffoldFilePathResolution:
    """W1309 — file-path tier disclosure (exact vs substring) on the
    file-targeted branch of ``roam test-scaffold``.

    Before W1309 the file branch silently absorbed the LIKE %name% fallback:
    when the user typed ``service.py`` and the index had
    ``src/service.py``, the wrapper would scaffold tests against the
    substring-matched file with a success verdict indistinguishable from
    an exact-path match. With zero testable symbols the verdict
    ``"No testable symbols found in src/service.py"`` looked clean —
    even when the wrong file was selected. W1309 surfaces
    ``resolution: "file"`` vs ``resolution: "file_substring"`` +
    ``partial_success: true`` + a distinct verdict on the substring
    path.
    """

    def test_exact_file_path_emits_file_resolution(self, indexed_project, cli_runner, monkeypatch) -> None:
        """Exact path match -> ``resolution: "file"`` (vs the new
        ``"file_substring"`` LIKE-fallback tier).

        Note: the closed-enum substrate carries ``partial_success: True``
        for ``"file"`` because the canonical contract treats file-path as
        a fallback for symbol-typed targets (W1241). ``test-scaffold``
        accepts ``SYMBOL_OR_PATH`` so a path input is arguably primary,
        but we don't break the substrate-wide contract for one consumer.
        The DISTINCT signal for W1309 is ``"file"`` vs ``"file_substring"``
        within the file branch — partial_success polarity is shared.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "src/service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "file"
        assert data["resolution"] == "file"
        # No substring-match annotation on the exact-path verdict.
        assert "[substring file match]" not in summary["verdict"]
        assert "Substring matched" not in summary["verdict"]

    def test_substring_file_match_emits_file_substring_resolution(
        self, indexed_project, cli_runner, monkeypatch
    ) -> None:
        """Basename-only input -> LIKE %name% fallback -> ``resolution:
        "file_substring"`` + ``partial_success: True`` + verdict suffix.

        ``service.py`` is unique inside the indexed fixture's ``src/``,
        so the substring fallback lands on the correct file — but the
        ENVELOPE must still disclose the substring origin so agents can
        catch the case where two paths share the same basename.
        """
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["test-scaffold", "service.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        assert summary["resolution"] == "file_substring"
        assert summary["partial_success"] is True
        assert data["resolution"] == "file_substring"
        assert "[substring file match]" in summary["verdict"]

    def test_substring_match_with_no_symbols_distinguishes_from_exact(
        self, indexed_project, cli_runner, monkeypatch
    ) -> None:
        """W1309 core fix: when the substring fallback lands on a file
        with ZERO testable symbols, the verdict must name the substring
        origin instead of the bare ``"No testable symbols found"`` string.

        Reproduce by adding an empty-of-functions file to the indexed
        project, re-indexing, and querying its basename.
        """
        empty_path = indexed_project / "src" / "constants_only.py"
        empty_path.write_text(
            "# W1309 fixture: zero functions / classes / methods.\nMAX_RETRIES = 3\nDEFAULT_NAME = 'roam'\n"
        )
        # Re-index so the new file is visible to the resolver.
        monkeypatch.chdir(indexed_project)
        idx = invoke_cli(cli_runner, ["index"])
        assert idx.exit_code == 0, idx.output

        result = invoke_cli(cli_runner, ["test-scaffold", "constants_only.py"], json_mode=True)
        assert result.exit_code == 0, result.output
        data = json.loads(getattr(result, "stdout", None) or result.output)
        summary = data["summary"]
        # Resolution tier surfaces the substring origin.
        assert summary["resolution"] == "file_substring"
        assert summary["partial_success"] is True
        assert summary["state"] == "file_substring_match_no_symbols"
        # Verdict names the substring origin, not the bare empty-set string.
        assert "Substring matched" in summary["verdict"]
        assert "no testable symbols" in summary["verdict"].lower()
        # Envelope root mirror so list-strip consumers still see disclosure.
        assert data["resolution"] == "file_substring"
        assert data["partial_success"] is True
