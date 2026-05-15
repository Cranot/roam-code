"""W809 — Empty-corpus smoke for `roam over-fetch` (W805 sweep).

Validates the LAW 4 + Pattern 1 (variant C) contract for `over-fetch` when
the indexed corpus has no PHP models, no controllers — just an empty .py
stub. The command MUST emit a structured envelope (not crash, not return
empty stdout) and the verdict MUST signal absence of over-fetch findings
rather than defaulting to silent success.

Anchor terminals (LAW 4): fetches, queries, findings, markers.
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
def empty_corpus(tmp_path):
    """A repo with a single empty Python file — no PHP models, no controllers.

    The over-fetch detector is Laravel/Eloquent-scoped; an empty Python-only
    corpus guarantees zero findings from BOTH the model-level pipeline and
    the endpoint 3-state classifier.
    """
    repo = tmp_path / "empty_over_fetch_corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    (repo / "empty.py").write_text("# empty fetches stub — no queries here\n")
    git_init(repo)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        out, rc = index_in_process(repo)
        assert rc == 0, f"index failed: {out}"
    finally:
        os.chdir(old_cwd)
    return repo


def _invoke_over_fetch_json(repo):
    """Invoke `roam --json over-fetch` in the given repo and return parsed JSON."""
    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = runner.invoke(cli, ["--json", "over-fetch"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def test_over_fetch_empty_corpus_exit_zero(empty_corpus):
    """over-fetch on a corpus with zero PHP models exits 0 (no findings != crash)."""
    result = _invoke_over_fetch_json(empty_corpus)
    assert result.exit_code == 0, (
        f"expected exit 0 on empty corpus, got {result.exit_code}; output: {result.output[:500]}"
    )


def test_over_fetch_empty_corpus_envelope_shape(empty_corpus):
    """Pattern 1-variant-C: structured envelope, never empty stdout."""
    result = _invoke_over_fetch_json(empty_corpus)
    assert result.output.strip(), "stdout MUST not be empty (Pattern 1 variant C)"

    envelope = json.loads(result.output)
    assert envelope["command"] == "over-fetch"
    assert "summary" in envelope and isinstance(envelope["summary"], dict)
    assert "findings" in envelope, "envelope MUST carry findings[] (even when empty)"
    assert isinstance(envelope["findings"], list)
    assert envelope["findings"] == [], "empty corpus produces 0 findings"
    assert "endpoint_findings" in envelope
    assert envelope["endpoint_findings"] == []


def test_over_fetch_empty_corpus_verdict_signals_absence(empty_corpus):
    """Verdict MUST name the absence — no default 'success' / 'SAFE' markers."""
    result = _invoke_over_fetch_json(empty_corpus)
    envelope = json.loads(result.output)
    summary = envelope["summary"]

    verdict = summary.get("verdict", "")
    assert verdict, "summary.verdict MUST be non-empty (LAW 6)"
    verdict_lower = verdict.lower()
    # Pattern 2 guardrail — empty corpus must produce an absence verdict,
    # NOT a default "SAFE" / "completed" / "ok" string.
    assert (
        "no over-fetch" in verdict_lower
        or "0 " in verdict_lower
        or "none" in verdict_lower
        or "empty" in verdict_lower
    ), f"verdict should signal absence, got: {verdict!r}"

    # Summary tallies must agree with the absence verdict.
    assert summary.get("total", -1) == 0, "summary.total MUST be 0 on empty corpus"
    assert summary.get("endpoint_total", -1) == 0, "summary.endpoint_total MUST be 0"
    assert summary.get("real_leak_count", -1) == 0, "summary.real_leak_count MUST be 0"
    assert summary.get("state") == "ok", "summary.state MUST be 'ok' when 0 leaks"


def test_over_fetch_empty_corpus_partial_success_bool_present(empty_corpus):
    """summary.partial_success MUST be a present bool — W802 contract.

    Drift-guard: the producer ALWAYS emits `partial_success` on the
    `over-fetch` envelope (currently `bare_count > 0 or
    unguarded_relation_count > 0`). On an empty corpus, both counts
    are zero, so the value MUST be present AND `False`. If a future
    refactor accidentally omits the key on the no-findings path, this
    assertion catches the regression (W802 pattern: never collapse to
    a silent SAFE markers default).
    """
    result = _invoke_over_fetch_json(empty_corpus)
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    assert "partial_success" in summary, (
        "summary.partial_success key MUST be present (always emit, never omit)"
    )
    assert isinstance(summary["partial_success"], bool), (
        "summary.partial_success MUST be a bool, "
        f"got {type(summary['partial_success']).__name__}"
    )
    assert summary["partial_success"] is False, (
        "empty corpus → 0 real leaks → partial_success MUST be False"
    )


def test_over_fetch_empty_corpus_agent_contract_facts_nonempty(empty_corpus):
    """agent_contract.facts MUST be non-empty — LAW 4 concrete-noun anchored."""
    result = _invoke_over_fetch_json(empty_corpus)
    envelope = json.loads(result.output)

    agent_contract = envelope.get("agent_contract")
    assert agent_contract is not None, "envelope MUST carry agent_contract"
    facts = agent_contract.get("facts") or []
    assert isinstance(facts, list), "agent_contract.facts MUST be a list"
    assert len(facts) > 0, (
        "agent_contract.facts MUST be non-empty even on no-findings runs "
        "(operational rule: empty output is itself signal)"
    )
    # Every fact is a plain string (no nested objects polluting the contract).
    for fact in facts:
        assert isinstance(fact, str) and fact.strip(), (
            f"each fact MUST be a non-empty string, got: {fact!r}"
        )
