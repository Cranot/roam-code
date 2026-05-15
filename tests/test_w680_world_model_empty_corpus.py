"""W680: empty-corpus smoke tests for world_model R28 classifiers.

W661 drive-by — the four R28 classifiers (``side-effects``, ``idempotency``,
``causal-graph``, ``tx-boundaries``) had no end-to-end test for the case
where the indexer succeeded but found zero classifiable symbols. This file
ships a single shared empty-corpus fixture (a project containing one empty
``.py`` file) and asserts each command emits a clean Pattern-2-compliant
"no symbols" envelope rather than a default-success illusion:

* exit code 0 (empty corpus is a valid state, not an error)
* envelope carries ``command``, ``summary.verdict``, and
  ``summary.partial_success``
* verdict mentions "empty" / "no symbols" / "no function" /
  "0 ..." — never a silent SAFE
* ``agent_contract.facts`` is non-empty and discloses the empty state

CLAUDE.md Pattern 2 (silent fallback discipline) — empty-corpus must
produce an "empty" envelope, never a default-success illusion. The four
world-model commands already implement this:

* ``cmd_side_effects.py:108-110`` → ``"No symbols available to classify..."``
* ``cmd_idempotency.py:98-100`` → ``"No symbols available to classify..."``
* ``cmd_causal_graph.py:142-144`` → ``"No symbols available to classify..."``
* ``cmd_tx_boundaries.py:150-153`` → ``"No symbols available to classify..."``

This test pins that behaviour so a future refactor cannot regress it
silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output  # noqa: E402


# ---------------------------------------------------------------------------
# Shared empty-corpus fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus_project(project_factory):
    """Indexed project containing a single empty ``.py`` file.

    The indexer runs to completion (so the SQLite schema exists and
    ``ensure_index()`` does not re-trigger), but zero functions / methods /
    constructors are extracted. This is the exact state the four R28
    classifiers each guard against with their ``not all_results`` branch.
    """
    return project_factory({"empty.py": ""})


# ---------------------------------------------------------------------------
# Per-command empty-corpus assertions
# ---------------------------------------------------------------------------


def _assert_empty_envelope(data, command):
    """Assert the envelope is shaped like a Pattern-2-compliant empty result.

    Common shape across all four world-model commands:
        - ``command`` matches the expected name
        - ``summary.verdict`` is a non-empty string that mentions empty state
        - ``summary.partial_success`` is True (LAW 6 + Pattern 2)
        - ``agent_contract.facts`` is a non-empty list with at least one
          fact disclosing the empty corpus
    """
    assert isinstance(data, dict), f"Expected dict envelope, got {type(data)}"
    assert data.get("command") == command, (
        f"Expected command={command}, got {data.get('command')}"
    )

    summary = data.get("summary")
    assert isinstance(summary, dict), f"Missing summary dict; got {summary}"

    verdict = summary.get("verdict", "")
    assert isinstance(verdict, str) and verdict, "Verdict must be a non-empty string"

    # Pattern 2: empty-corpus must produce an "empty" verdict — NOT a
    # silent default-success. Accept any of the canonical empty markers.
    verdict_lc = verdict.lower()
    empty_markers = ("empty", "no symbol", "no function", "0 ", "found no", "pure or opaque")
    assert any(marker in verdict_lc for marker in empty_markers), (
        f"{command} verdict failed Pattern-2 empty-state disclosure: "
        f"{verdict!r} (expected one of {empty_markers})"
    )

    # partial_success must signal degraded result so agents don't treat
    # "no findings" as "all clear".
    assert summary.get("partial_success") is True, (
        f"{command} must set summary.partial_success=True on empty corpus; "
        f"got {summary.get('partial_success')!r}"
    )

    # Agent contract: at least one fact must disclose the empty state.
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) >= 1, (
        f"{command} agent_contract.facts must be a non-empty list on empty corpus; "
        f"got {facts!r}"
    )
    # At least one fact must speak to the empty state (a generic "scan
    # found no symbols/findings" line is fine; LAW 4 anchor terminals like
    # "symbols" / "findings" / "verdicts" / "commands" are accepted).
    joined = " ".join(facts).lower()
    fact_markers = ("no symbol", "no function", "found no", "empty", "0 ", "no inputs")
    assert any(marker in joined for marker in fact_markers), (
        f"{command} agent_contract.facts must disclose the empty state; "
        f"got {facts!r}"
    )


def test_side_effects_empty_corpus(cli_runner, empty_corpus_project):
    """``roam side-effects --json`` on a zero-symbols corpus emits the
    Pattern-2 "no symbols" envelope, exit 0, partial_success=True."""
    result = invoke_cli(
        cli_runner, ["side-effects"], cwd=empty_corpus_project, json_mode=True
    )
    assert result.exit_code == 0, (
        f"side-effects exit code != 0 on empty corpus: {result.exit_code}\n{result.output}"
    )
    data = parse_json_output(result, command="side-effects")
    _assert_empty_envelope(data, command="side-effects")


def test_idempotency_empty_corpus(cli_runner, empty_corpus_project):
    """``roam idempotency --json`` on a zero-symbols corpus emits the
    Pattern-2 "no symbols" envelope, exit 0, partial_success=True."""
    result = invoke_cli(
        cli_runner, ["idempotency"], cwd=empty_corpus_project, json_mode=True
    )
    assert result.exit_code == 0, (
        f"idempotency exit code != 0 on empty corpus: {result.exit_code}\n{result.output}"
    )
    data = parse_json_output(result, command="idempotency")
    _assert_empty_envelope(data, command="idempotency")


def test_causal_graph_empty_corpus(cli_runner, empty_corpus_project):
    """``roam causal-graph --json`` on a zero-symbols corpus emits the
    Pattern-2 "no symbols" envelope, exit 0, partial_success=True."""
    result = invoke_cli(
        cli_runner, ["causal-graph"], cwd=empty_corpus_project, json_mode=True
    )
    assert result.exit_code == 0, (
        f"causal-graph exit code != 0 on empty corpus: {result.exit_code}\n{result.output}"
    )
    data = parse_json_output(result, command="causal-graph")
    _assert_empty_envelope(data, command="causal-graph")


def test_tx_boundaries_empty_corpus(cli_runner, empty_corpus_project):
    """``roam tx-boundaries --json`` on a zero-symbols corpus emits the
    Pattern-2 "no symbols" envelope, exit 0, partial_success=True."""
    result = invoke_cli(
        cli_runner, ["tx-boundaries"], cwd=empty_corpus_project, json_mode=True
    )
    assert result.exit_code == 0, (
        f"tx-boundaries exit code != 0 on empty corpus: {result.exit_code}\n{result.output}"
    )
    data = parse_json_output(result, command="tx-boundaries")
    _assert_empty_envelope(data, command="tx-boundaries")
