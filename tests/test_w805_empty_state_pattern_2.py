"""W805 — Empty-corpus Pattern-2 sweep for residual detectors.

After the W802→W836 arc closed 21 detectors (dead, n1, missing-index,
complexity, over-fetch, clones, orphan-imports, bus-factor, auth-gaps,
hotspots, smells, duplicates, invariants, vulns, taint, audit-trail-*,
critique, health, doctor, pr-risk), the W805 audit caught three more
detectors that emitted success-shaped envelopes on empty inputs:

- ``cmd_test_hermeticity`` — "no Python test files indexed" without
  ``partial_success`` disclosure.
- ``cmd_llm_smells`` — "0 LLM-API findings in 0 scanned files" without
  ``partial_success`` disclosure.
- ``cmd_boundary`` — "0 boundary findings (scope: all)" on a corpus
  with zero import edges, without distinguishing "clean" from "no
  imports to analyze".

Each test below asserts:

- exit code is 0,
- summary.verdict mentions the empty/absent state explicitly,
- summary.partial_success is exposed and equals True (degraded state),
- summary.state carries a closed-enum disclosure ("no_tests_indexed"
  / "no_llm_files" / "no_imports").

LAW 4 anchors: ``tests``, ``files``, ``imports``, ``findings``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixture: a git-init'd project with one zero-byte Python file. Matches the
# W802 / W833 contract: discovery sees the file, parsing yields zero
# symbols (and zero import edges). This is the canonical "empty corpus".
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
# cmd_test_hermeticity
# ---------------------------------------------------------------------------


class TestHermeticityEmptyCorpus:
    """W805 — ``roam test-hermeticity`` on a corpus with zero test files."""

    def test_empty_corpus_envelope_discloses_no_tests(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "test-hermeticity")
        assert exit_code == 0, payload
        assert payload.get("command") == "test-hermeticity"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        # Verdict must name the empty state.
        assert any(
            marker in verdict for marker in ("no python test files", "no tests", "0 tests")
        ), f"verdict {verdict!r} did not disclose empty test-corpus state"

        # partial_success must be True on the empty-tests degraded path.
        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on no-tests-indexed; got {summary.get('partial_success')!r}"
        )
        # Closed-enum state field.
        assert summary.get("state") == "no_tests_indexed", (
            f"summary.state should be 'no_tests_indexed', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# cmd_llm_smells
# ---------------------------------------------------------------------------


class TestLlmSmellsEmptyCorpus:
    """W805 — ``roam llm-smells`` on a corpus with no LLM-using files."""

    def test_empty_corpus_envelope_discloses_no_llm_files(self, tmp_path):
        exit_code, payload = _run_cli_json(tmp_path, "llm-smells")
        assert exit_code == 0, payload
        assert payload.get("command") == "llm-smells"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        # Verdict must name the empty / absent state.
        assert any(
            marker in verdict for marker in ("no llm", "scan empty", "no llm-using files")
        ), f"verdict {verdict!r} did not disclose empty LLM-files state"

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on no-llm-files; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_llm_files", (
            f"summary.state should be 'no_llm_files', got {summary.get('state')!r}"
        )


# ---------------------------------------------------------------------------
# cmd_boundary
# ---------------------------------------------------------------------------


class TestBoundaryEmptyCorpus:
    """W805 — ``roam boundary`` on a corpus with zero import edges."""

    def test_empty_corpus_envelope_discloses_no_imports(self, tmp_path):
        # boundary defaults to changed_range=pr; switch to --changed-range=all
        # so the check runs against the whole corpus rather than a (likely
        # empty) git changeset.
        exit_code, payload = _run_cli_json(tmp_path, "boundary", "--changed-range", "all")
        assert exit_code == 0, payload
        assert payload.get("command") == "boundary"
        summary = payload.get("summary") or {}

        verdict = (summary.get("verdict") or "").lower()
        # Verdict must name the empty-imports state.
        assert any(
            marker in verdict for marker in ("no imports", "0 import", "corpus has 0")
        ), f"verdict {verdict!r} did not disclose empty-imports state"

        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on no-imports; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_imports", (
            f"summary.state should be 'no_imports', got {summary.get('state')!r}"
        )
