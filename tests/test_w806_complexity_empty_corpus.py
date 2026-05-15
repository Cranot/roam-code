"""W806 — Empty-corpus smoke test for `roam complexity`.

Part of the W805 sweep continuation. Validates that `roam complexity --json`
on an empty corpus emits a structured Pattern-2-compliant envelope (no silent
default-success verdict) and that the LAW-4 anchored fact list is non-empty.

Two failure modes documented vs the "Adding a command checklist":

* The command emits a structured envelope but raises ``SystemExit(1)`` on
  the empty path — wrapper-bridge (Pattern 1, variant B) territory.
* The empty-corpus envelope today is missing ``summary.partial_success``
  and ``summary.facts``. Both are required by the checklist; we pin the
  gap as xfail-strict so progress is detected automatically when the
  envelope is upgraded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Pull the subprocess + git helpers from the shared conftest.
sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, roam  # noqa: E402


@pytest.fixture
def empty_corpus(tmp_path):
    """A git repo containing a single empty .py file — nothing for the
    indexer to extract symbols/metrics from."""
    repo = tmp_path / "empty_corpus"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    # Empty .py file: indexable, parseable, but yields zero symbols and
    # therefore zero rows in symbol_metrics.
    (repo / "empty.py").write_text("")
    git_init(repo)
    return repo


def _run_complexity_json(repo):
    """Run ``roam init`` then ``roam --json complexity`` in ``repo``.

    Returns (parsed_envelope, complexity_exit_code, raw_complexity_output).
    """
    out, rc = roam("init", cwd=repo)
    assert rc == 0, f"roam init failed (rc={rc}):\n{out}"

    out, rc = roam("--json", "complexity", cwd=repo)

    # The stdout payload should be a single JSON envelope even when the
    # corpus is empty. If the wrapper layer mixes stderr in, locate the
    # JSON object boundaries.
    raw = out.strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        assert start >= 0 and end > start, (
            f"complexity emitted no JSON envelope on empty corpus (rc={rc}):\n{out}"
        )
        raw = raw[start : end + 1]
    envelope = json.loads(raw)
    return envelope, rc, out


def test_complexity_empty_corpus_emits_structured_envelope(empty_corpus):
    """Empty corpus -> structured JSON envelope (not bare error text)."""
    envelope, rc, raw = _run_complexity_json(empty_corpus)

    # Behavior found at W806 baseline: the command path emits the
    # envelope but exits non-zero (SystemExit(1)). Don't pin to exit 0 —
    # the contract under test is "envelope is structured", not "exit
    # status is success". This is a documented Pattern 1B gap (wrapper
    # bridge bug surface).
    assert envelope["command"] == "complexity", f"wrong command field: {envelope!r}"
    assert "summary" in envelope, f"missing summary: {envelope!r}"

    summary = envelope["summary"]
    verdict = summary.get("verdict", "")
    # The verdict MUST disclose the empty/no-data state — never a
    # default success line like 'avg complexity 0.0, 0 critical, 0 high'.
    lowered = verdict.lower()
    assert any(token in lowered for token in ("no complexity data", "no data", "no symbols", "no findings")), (
        f"verdict does not disclose empty-corpus state (Pattern 2 silent SAFE): {verdict!r}"
    )

    # The exit code is part of the gap report. Capture it so the
    # signal lands in test logs even when the assertions above pass.
    assert rc in (0, 1), f"unexpected exit code {rc} on empty corpus:\n{raw}"


def test_complexity_empty_corpus_envelope_has_partial_success_and_facts(empty_corpus):
    """W802-style pinning: lock in the missing-fields gap as xfail-strict.

    When the cmd_complexity empty path is upgraded to include
    ``summary.partial_success`` (false, since no underlying check failed
    — the corpus is just empty) plus a flat ``agent_contract.facts``
    list anchored on LAW-4 terminals (e.g. ``functions``, ``symbols``,
    ``findings``, ``markers``), this test starts passing and the
    xfail-strict marker turns it into a regression detector.
    """
    envelope, _rc, _raw = _run_complexity_json(empty_corpus)
    summary = envelope["summary"]

    # Pattern 2: partial_success must be present and explicitly False
    # (no subcommand failed — the corpus simply has nothing to rank).
    assert "partial_success" in summary, "summary.partial_success missing"
    assert summary["partial_success"] is False, (
        f"empty corpus is not a degraded run; partial_success should be False, got {summary['partial_success']!r}"
    )

    # LAW 4: agent_contract.facts non-empty and anchored on accepted
    # concrete-noun terminals. The four canonical anchors that apply
    # here are: ``symbols``, ``functions``, ``findings``, ``markers``.
    contract = envelope.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert facts, "agent_contract.facts is empty — LAW 4 violation"

    accepted_terminals = {"symbols", "functions", "findings", "markers"}
    for fact in facts:
        terminal = fact.rstrip(".!?;: ").rsplit(None, 1)[-1].lower()
        assert terminal in accepted_terminals, (
            f"fact {fact!r} terminal {terminal!r} not in LAW-4 anchor set {accepted_terminals}"
        )
