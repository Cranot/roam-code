"""W805-followup — 5 additional empty-corpus Pattern-2 migrations.

After the W805 batch closed three detectors (test_hermeticity / llm_smells /
boundary), the followup audit caught five more that emitted success-shaped
envelopes on empty inputs:

- ``cmd_vibe_check`` — silent "AI rot score 0/100" on a 0-file corpus.
- ``cmd_fingerprint`` — spectral analysis on a sym_count==0 graph (degenerate
  metrics indistinguishable from a tidy result).
- ``cmd_fan`` — "no graph metrics available" / "no file edges available" on a
  rows==0 branch, without ``partial_success`` disclosure.
- ``cmd_dark_matter`` — "0 dark-matter couplings found" on a total==0
  envelope branch, without distinguishing "clean" from "no co-change history".
- ``cmd_conventions`` — "consistent naming, no test files" on an
  empty-naming_summary corpus.

Each test below asserts:

- exit code is 0,
- summary.verdict mentions the empty/absent state explicitly,
- summary.partial_success is exposed and equals True (degraded state),
- summary.state carries a closed-enum disclosure
  (``no_files_scanned`` / ``no_symbols`` / ``no_file_edges`` /
  ``no_cochange`` / ``no_symbols_analyzed``).

LAW 4 anchors: ``files``, ``symbols``, ``edges``, ``records``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixture: shared with W805. Builds a git-init'd project with one zero-byte
# Python file, then indexes it via ``roam init``. The result is the canonical
# "empty corpus": files=1, symbols=0, edges=0, file_edges=0, cochange=0.
# ---------------------------------------------------------------------------


def _make_empty_corpus(tmp: Path) -> None:
    (tmp / "empty.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp,
        check=True,
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


def _run_cli_json(tmp_path: Path, *args: str) -> tuple[int, dict]:
    """Build empty corpus, invoke ``roam --json <args>``, return (exit, payload)."""
    _make_empty_corpus(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", *args], catch_exceptions=False)
    finally:
        os.chdir(cwd)
    payload = json.loads(result.output) if result.output.strip() else {}
    return result.exit_code, payload


# ---------------------------------------------------------------------------
# W805-followup-A: cmd_vibe_check
# ---------------------------------------------------------------------------


class TestVibeCheckEmptyCorpus:
    """W805-followup-A — ``roam vibe-check`` on a 0-file corpus."""

    def test_empty_corpus_envelope_discloses_no_files(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "vibe-check")
        assert exit_code == 0, payload
        assert payload.get("command") == "vibe-check"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        assert any(marker in verdict for marker in ("no files scanned", "corpus empty", "no files")), (
            f"verdict {verdict!r} did not disclose empty-files state"
        )

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on empty-vibe-check; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_files_scanned", (
            f"summary.state should be 'no_files_scanned', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# W805-followup-B: cmd_fingerprint
# ---------------------------------------------------------------------------


class TestFingerprintEmptyCorpus:
    """W805-followup-B — ``roam fingerprint`` on a 0-symbol corpus."""

    def test_empty_corpus_envelope_discloses_no_symbols(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "fingerprint")
        assert exit_code == 0, payload
        assert payload.get("command") == "fingerprint"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        assert any(marker in verdict for marker in ("no symbols indexed", "corpus empty", "no symbols")), (
            f"verdict {verdict!r} did not disclose empty-symbols state"
        )

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on empty-fingerprint; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_symbols", (
            f"summary.state should be 'no_symbols', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# W805-followup-C: cmd_fan
# ---------------------------------------------------------------------------


class TestFanEmptyCorpus:
    """W805-followup-C — ``roam fan`` on a 0-symbol corpus (default mode)."""

    def test_empty_corpus_envelope_discloses_no_symbols(self, tmp_path):
        # Default mode is symbol; the rows==0 branch hits via the symbols
        # query (no degree metrics in an empty graph).
        exit_code, payload = _run_cli_json(tmp_path, "fan")
        assert exit_code == 0, payload
        assert payload.get("command") == "fan"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        assert any(marker in verdict for marker in ("no graph metrics", "no file edges", "corpus empty")), (
            f"verdict {verdict!r} did not disclose empty-fan state"
        )

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on empty-fan; got {summary.get('partial_success')!r}"
        )
        # The default symbol-mode branch emits state='no_symbols'; the file
        # mode branch would emit state='no_file_edges'. Accept either since
        # we don't pin the default mode here.
        assert summary.get("state") in {"no_symbols", "no_file_edges"}, (
            f"summary.state should be 'no_symbols' or 'no_file_edges', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# W805-followup-D: cmd_dark_matter
# ---------------------------------------------------------------------------


class TestDarkMatterEmptyCorpus:
    """W805-followup-D — ``roam dark-matter`` on a 0-cochange corpus."""

    def test_empty_corpus_envelope_discloses_no_cochange(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "dark-matter")
        assert exit_code == 0, payload
        assert payload.get("command") == "dark-matter"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        assert any(
            marker in verdict
            for marker in (
                "no co-change history",
                "0 cochange",
                "corpus has 0",
                "corpus empty",
            )
        ), f"verdict {verdict!r} did not disclose empty-cochange state"

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on empty-dark-matter; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_cochange", (
            f"summary.state should be 'no_cochange', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# W805-followup-E: cmd_conventions
# ---------------------------------------------------------------------------


class TestConventionsEmptyCorpus:
    """W805-followup-E — ``roam conventions`` on an empty-naming corpus."""

    def test_empty_corpus_envelope_discloses_no_symbols(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "conventions")
        assert exit_code == 0, payload
        assert payload.get("command") == "conventions"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        assert any(marker in verdict for marker in ("no symbols analyzed", "corpus empty", "no symbols")), (
            f"verdict {verdict!r} did not disclose empty-symbols state"
        )

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on empty-conventions; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_symbols_analyzed", (
            f"summary.state should be 'no_symbols_analyzed', got {summary.get('state')!r}"
        )
