"""W812 — Empty-corpus smoke for ``roam orphan-imports`` (W805 sweep).

Pins the behavior of ``roam orphan-imports --json`` on an empty corpus
(one empty .py file, no real imports, no symbols). Asserts that:

* The command exits 0 (no crash on zero indexed modules).
* The envelope is structured — i.e. ``summary.verdict`` is present and
  references the empty / zero-file state rather than emitting a generic
  "completed" verdict (LAW 4 anchor: ``findings`` / ``imports`` /
  ``modules`` / ``markers``).
* ``agent_contract.facts`` (when present) is a non-empty list.
* ``summary.partial_success`` is present as a bool. The orphan-imports
  command does not currently set this field on the empty-corpus path, so
  the assertion is wrapped in an xfail-strict marker (W802 pattern) — the
  test will start passing the day the field is added, and the strict
  flag will then convert the surprise pass into a hard failure that
  forces removal of the xfail.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli


def _make_empty_repo(tmp: Path) -> None:
    """Initialise a git repo with one EMPTY .py file and run ``roam init``."""
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


def test_w812_orphan_imports_empty_corpus_structured_envelope(tmp_path):
    """Empty corpus → structured envelope, exit 0, empty-aware verdict."""
    _make_empty_repo(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "orphan-imports"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, (
        f"orphan-imports crashed on empty corpus: exit={result.exit_code} "
        f"output={result.output!r}"
    )

    # Envelope must be parseable JSON.
    try:
        envelope = json.loads(result.output)
    except json.JSONDecodeError as exc:
        pytest.fail(f"orphan-imports stdout not JSON: {exc} output={result.output!r}")

    assert isinstance(envelope, dict), f"envelope not dict: {envelope!r}"

    summary = envelope.get("summary")
    assert isinstance(summary, dict), f"summary missing or not dict: {summary!r}"

    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, (
        f"verdict missing/empty: summary={summary!r}"
    )

    # Empty-corpus verdict must reference the empty / zero-file state —
    # not a generic "completed" / "non-conformant" string (Pattern 2).
    verdict_lower = verdict.lower()
    empty_markers = ("0 file", "no orphan", "no imports", "empty", "zero")
    assert any(marker in verdict_lower for marker in empty_markers), (
        f"verdict does not disclose empty state — expected one of "
        f"{empty_markers!r} in {verdict!r}"
    )

    # Counts: zero findings (the .py file is empty / has no imports).
    # files_scanned MAY be 0 or 1 depending on whether the empty stub
    # gets discovered by the indexer; either is structurally fine.
    assert summary.get("count", 0) == 0, f"summary.count != 0: {summary!r}"
    files_scanned = summary.get("files_scanned", 0)
    assert isinstance(files_scanned, int) and files_scanned >= 0, (
        f"files_scanned must be a non-negative int: {summary!r}"
    )

    # agent_contract.facts (when present) must be a non-empty list.
    agent_contract = envelope.get("agent_contract")
    if agent_contract is not None:
        facts = agent_contract.get("facts")
        assert isinstance(facts, list) and len(facts) > 0, (
            f"agent_contract.facts must be a non-empty list: {agent_contract!r}"
        )


def test_w812_orphan_imports_empty_corpus_partial_success_bool(tmp_path):
    """``summary.partial_success`` must be a bool on the empty-corpus path."""
    _make_empty_repo(tmp_path)
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(cli, ["--json", "orphan-imports"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    assert "partial_success" in summary, f"missing partial_success: {summary!r}"
    assert isinstance(summary["partial_success"], bool), (
        f"partial_success not bool: {summary['partial_success']!r}"
    )
