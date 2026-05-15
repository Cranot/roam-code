"""W821 — empty-corpus smoke for ``roam duplicates`` (W805 sweep).

Pins the contract that ``roam --json duplicates`` on an empty corpus
emits a structured envelope rather than crashing, empty stdout, or a
generic "COMMAND_FAILED" wrapper (Pattern 1 variant C). The corpus is
intentionally a single empty ``.py`` file so the candidate query returns
zero rows and the command takes its no-duplicates branch.

Asserted contract:
- exit 0
- stdout parses as a JSON envelope with ``command == "duplicates"``
- ``summary.verdict`` mentions "no" / "empty" / "duplicate" (empty state
  is acknowledged in plain English)
- ``summary.partial_success`` key is present (Pattern 2: silent fallback
  detection — the envelope must disclose its partial-success state even
  when False, so downstream tooling never has to guess)
- ``agent_contract.facts`` is non-empty (LAW 4: empty corpus still
  produces a concrete-noun-anchored fact via the verdict + zero counts)
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
)


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def empty_corpus_project(tmp_path, monkeypatch):
    """Indexed project containing a single empty .py file.

    The indexer runs cleanly but produces zero function/method symbols,
    so ``duplicates`` must take its empty-candidate branch.
    """
    proj = tmp_path / "empty-corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # A truly empty file — no functions, no classes, no candidates.
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


def test_duplicates_empty_corpus_emits_envelope(empty_corpus_project, cli_runner):
    """Empty corpus must produce a parseable JSON envelope on exit 0."""
    result = invoke_cli(cli_runner, ["duplicates"], json_mode=True)
    assert result.exit_code == 0, (
        f"duplicates exited {result.exit_code}: {result.output}"
    )
    assert result.output.strip(), "stdout must NOT be empty in --json mode"

    payload = _json.loads(result.output)
    assert isinstance(payload, dict)
    assert payload.get("command") == "duplicates"


def test_duplicates_empty_corpus_verdict_mentions_empty(
    empty_corpus_project, cli_runner
):
    """Verdict must acknowledge the empty / no-duplicates state in plain text."""
    result = invoke_cli(cli_runner, ["duplicates"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    verdict = (summary.get("verdict") or "").lower()
    # LAW 6: the verdict must work without any other field. On an empty
    # corpus it must say so — "no", "empty", or "duplicate" (the
    # canonical "No duplicate candidates found" / "No semantic
    # duplicates detected" branches both contain these tokens).
    assert any(token in verdict for token in ("no ", "empty", "duplicate")), (
        f"verdict does not acknowledge empty corpus: {verdict!r}"
    )
    # Counts must be present and zero on the empty branch.
    assert summary.get("total_clusters", -1) == 0
    assert summary.get("total_functions", -1) == 0


def test_duplicates_empty_corpus_partial_success_present(
    empty_corpus_project, cli_runner
):
    """``summary.partial_success`` key must be present on the empty branch.

    Pattern 2 (CLAUDE.md): empty / degraded outcomes must disclose their
    partial-success state explicitly so downstream tooling never has to
    infer it from absence. The key may be True or False — what matters
    is that it is set.
    """
    result = invoke_cli(cli_runner, ["duplicates"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert "partial_success" in summary, (
        "summary must carry partial_success on the empty-corpus branch "
        "(W821 / Pattern 2 silent-fallback guard); got summary keys: "
        f"{sorted(summary.keys())}"
    )


def test_duplicates_empty_corpus_facts_non_empty(
    empty_corpus_project, cli_runner
):
    """``agent_contract.facts`` must be non-empty even on the empty branch.

    LAW 4: empty corpus still produces a concrete-noun-anchored fact —
    the verdict plus the zero-count fields humanise into at least one
    fact via :func:`_derive_agent_contract`.
    """
    result = invoke_cli(cli_runner, ["duplicates"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    contract = payload.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list)
    assert len(facts) >= 1, (
        f"agent_contract.facts must be non-empty on empty corpus; got: {facts!r}"
    )
