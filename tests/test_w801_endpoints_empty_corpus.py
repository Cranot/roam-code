"""W801 — Empty-corpus smoke test for ``roam endpoints``.

Sibling to W680 (world_model classifiers) and W681 (taint_engine). Validates
the Pattern 2 always-emit discipline: when the indexed corpus contains zero
detectable HTTP/GraphQL/gRPC route definitions, ``roam endpoints`` must emit
a structured envelope that explicitly discloses the empty state — never a
default-success illusion.

Asserts on the JSON envelope:

* Exit code 0 (empty-corpus is a valid state, not a failure).
* ``summary.verdict`` mentions ``no endpoints`` (Pattern 2: explicit, not
  silently default-success).
* ``summary.count == 0`` and ``summary.frameworks == []``.
* ``agent_contract.facts`` is non-empty and surfaces the empty state.
* Anchors on the LAW 4 ``endpoints`` / ``frameworks`` concrete-noun
  terminals.
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
    """Project with a single empty .py file — zero detectable endpoints."""
    repo = tmp_path / "empty_corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    # One empty Python file: indexer has something to discover, but no
    # routes / decorators / handlers will match any framework scanner.
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


def test_endpoints_empty_corpus_emits_pattern2_envelope(cli_runner, empty_corpus):
    """On an empty corpus, ``roam endpoints --json`` must emit a clean
    Pattern 2 envelope: explicit empty-state verdict, ``count: 0``, empty
    ``frameworks`` array, and disclosing ``agent_contract.facts``."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(
            cli, ["--json", "endpoints"], catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)

    # Empty-corpus is a valid state — exit 0, not a failure.
    assert result.exit_code == 0, (
        f"empty-corpus endpoints exited {result.exit_code}: {result.output!r}"
    )

    envelope = json.loads(result.output)

    # --- Required envelope shape ---
    assert envelope.get("command") == "endpoints"
    summary = envelope.get("summary") or {}
    verdict = summary.get("verdict", "")
    assert isinstance(verdict, str) and verdict, (
        f"missing summary.verdict in {envelope!r}"
    )

    # --- Pattern 2: explicit empty disclosure, NOT default-success ---
    verdict_lc = verdict.lower()
    assert any(
        token in verdict_lc
        for token in ("no endpoints", "0 endpoints", "empty", "no routes")
    ), f"verdict does not disclose empty state: {verdict!r}"

    # Forbid default-success markers on an empty corpus.
    forbidden_markers = ("safe", "healthy", "passing", "all good")
    assert not any(m in verdict_lc for m in forbidden_markers), (
        f"verdict reads as default-success on empty corpus: {verdict!r}"
    )

    # --- Count + framework axes are explicit ---
    assert summary.get("count") == 0, (
        f"summary.count must be 0 on empty corpus, got {summary.get('count')!r}"
    )
    assert summary.get("frameworks") == [], (
        f"summary.frameworks must be [] on empty corpus, got "
        f"{summary.get('frameworks')!r}"
    )
    assert summary.get("framework_count") == 0

    # Top-level endpoints array must also be empty (no synthetic rows).
    assert envelope.get("endpoints") == []

    # --- agent_contract.facts: non-empty, discloses empty state ---
    contract = envelope.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) >= 1, (
        f"agent_contract.facts must be non-empty: {contract!r}"
    )
    joined = " ".join(str(f).lower() for f in facts)
    assert "endpoint" in joined or "framework" in joined, (
        f"facts must mention endpoints/frameworks: {facts!r}"
    )
    # The "0 endpoint(s) across 0 framework(s)" fact is explicit.
    assert "0" in joined, (
        f"facts must surface the empty count: {facts!r}"
    )


def test_endpoints_empty_corpus_text_mode(cli_runner, empty_corpus):
    """Text mode mirrors JSON: VERDICT line discloses the empty state."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(empty_corpus))
        result = cli_runner.invoke(cli, ["endpoints"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    assert "no endpoints" in result.output.lower()
    # Supported-frameworks help block prints on empty.
    assert "Supported frameworks:" in result.output
