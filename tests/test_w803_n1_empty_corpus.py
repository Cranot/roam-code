"""W803 — Empty-corpus smoke test for ``roam n1``.

Sibling to W680 (world_model classifiers), W681 (taint_engine), and W801
(endpoints). Validates the Pattern 2 always-emit discipline: when the
indexed corpus contains zero detectable ORM model classes with computed
accessors, ``roam n1`` must emit a structured envelope that explicitly
discloses the empty state — never a default-success illusion.

Asserts on the JSON envelope:

* Exit code 0 (empty-corpus is a valid state, not a failure).
* ``summary.verdict`` mentions ``no``/``0``/``empty`` for N+1 patterns
  (Pattern 2: explicit, not silently default-success).
* ``summary.total == 0`` and ``summary.findings_confidence_distribution``
  is the canonical zero-distribution.
* Top-level ``findings`` array is empty (no synthetic rows).
* ``agent_contract.facts`` is non-empty and surfaces the empty state.
* Anchors on the LAW 4 ``patterns`` / ``queries`` / ``findings`` /
  ``markers`` concrete-noun terminal vocabulary.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with a single empty .py file — zero ORM model classes,
    zero computed accessors, zero N+1 candidates."""
    repo = tmp_path / "empty_corpus_n1"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    # One empty Python file: indexer has something to discover, but no
    # model classes / @property accessors / relationship calls will
    # match any of the framework heuristics (Laravel / Django / Rails /
    # SQLAlchemy / JPA).
    (repo / "blank.py").write_text("")
    git_init(repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


def test_n1_empty_corpus_emits_pattern2_envelope(cli_runner, empty_corpus):
    """On an empty corpus, ``roam n1 --json`` must emit a clean Pattern 2
    envelope: explicit empty-state verdict, ``total: 0``, zero confidence
    distribution, and disclosing ``agent_contract.facts``."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(
            cli, ["--json", "n1"], catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)

    # Empty-corpus is a valid state — exit 0, not a failure.
    assert result.exit_code == 0, (
        f"empty-corpus n1 exited {result.exit_code}: {result.output!r}"
    )

    envelope = json.loads(result.output)

    # --- Required envelope shape ---
    assert envelope.get("command") == "n1"
    summary = envelope.get("summary") or {}
    verdict = summary.get("verdict", "")
    assert isinstance(verdict, str) and verdict, (
        f"missing summary.verdict in {envelope!r}"
    )

    # --- Pattern 2: explicit empty disclosure, NOT default-success ---
    verdict_lc = verdict.lower()
    assert any(
        token in verdict_lc
        for token in ("no implicit n+1", "no n+1", "0 patterns", "empty")
    ), f"verdict does not disclose empty state: {verdict!r}"

    # Forbid default-success markers on an empty corpus.
    forbidden_markers = ("safe", "healthy", "passing", "all good")
    assert not any(m in verdict_lc for m in forbidden_markers), (
        f"verdict reads as default-success on empty corpus: {verdict!r}"
    )

    # --- Total + framework + confidence-distribution axes are explicit ---
    assert summary.get("total") == 0, (
        f"summary.total must be 0 on empty corpus, got {summary.get('total')!r}"
    )
    # framework defaults to "generic" when no framework is detected — this
    # is the explicit empty-framework disclosure, not a false-positive.
    assert summary.get("framework") == "generic", (
        f"summary.framework must be 'generic' on empty corpus, got "
        f"{summary.get('framework')!r}"
    )
    assert summary.get("truncated") is False
    assert summary.get("by_confidence") == {}, (
        f"summary.by_confidence must be empty {{}} on empty corpus, got "
        f"{summary.get('by_confidence')!r}"
    )
    # Canonical zero-distribution (W596 confidence helper).
    dist = summary.get("findings_confidence_distribution") or {}
    assert dist == {"high": 0, "medium": 0, "low": 0}, (
        f"summary.findings_confidence_distribution must be the zero "
        f"distribution on empty corpus, got {dist!r}"
    )

    # Top-level findings array must also be empty (no synthetic rows).
    assert envelope.get("findings") == []

    # --- agent_contract.facts: non-empty, discloses empty state ---
    contract = envelope.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) >= 1, (
        f"agent_contract.facts must be non-empty: {contract!r}"
    )
    joined = " ".join(str(f).lower() for f in facts)
    # The fact list must mention the empty state — either via "n+1" /
    # "pattern(s)" anchoring on the LAW 4 terminal, or via "0" count.
    assert ("n+1" in joined) or ("pattern" in joined) or ("0" in joined), (
        f"facts must surface empty state: {facts!r}"
    )
    # The "total 0" / "no N+1 patterns detected" disclosure is explicit.
    assert "0" in joined, (
        f"facts must surface the empty count: {facts!r}"
    )


def test_n1_empty_corpus_text_mode(cli_runner, empty_corpus):
    """Text mode mirrors JSON: VERDICT line discloses the empty state."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["n1"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    out_lc = result.output.lower()
    assert any(
        token in out_lc
        for token in ("no implicit n+1", "no n+1", "0 patterns")
    ), f"text-mode VERDICT does not disclose empty state: {result.output!r}"
