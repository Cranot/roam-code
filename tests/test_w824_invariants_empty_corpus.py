"""W824 — Empty-corpus smoke for ``roam invariants`` (W805 sweep).

Pins the empty-corpus envelope contract for ``roam invariants``: an
indexed project that contains no extractable symbols must still emit a
structured envelope (never empty stdout, never a bare error string) so
the MCP wrapper can parse it without crashing (Pattern 1 family,
variants B/C from CLAUDE.md).

The command supports three invocation shapes:
  * ``invariants <target>``           -- symbol/file lookup
  * ``invariants --public-api``       -- enumerate exported symbols
  * ``invariants --breaking-risk``    -- enumerate all symbols ranked

Variants (b) and (c) drive into the envelope path even when the
underlying corpus is empty; this test exercises ``--public-api`` because
that is the canonical "list-mode" entrypoint an agent reaches for when
it doesn't yet know which symbol to interrogate.

Anchored on LAW 4 terminals: ``invariants``, ``findings``, ``markers``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    index_in_process,
)


# ---------------------------------------------------------------------------
# Fixture: an indexed project whose only Python source file is empty.
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus_project(tmp_path):
    """Indexed project with a single empty .py file (no symbols)."""
    proj = tmp_path / "empty_inv_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # An empty .py file ensures the indexer runs the Python pipeline but
    # extracts zero symbols / zero edges. This is the canonical empty-
    # corpus shape — the indexer succeeds, the DB is built, but every
    # detector query returns empty rowsets.
    (proj / "empty.py").write_text("")
    git_init(proj)

    old = os.getcwd()
    try:
        os.chdir(str(proj))
        out, rc = index_in_process(proj)
        assert rc == 0, f"index failed on empty corpus: {out}"
    finally:
        os.chdir(old)
    return proj


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_invariants(proj, args, json_mode=True):
    """Invoke ``roam invariants`` in-process and return the Click result."""
    from roam.commands.cmd_invariants import invariants

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(
            invariants,
            list(args),
            obj={"json": json_mode},
            catch_exceptions=False,
        )
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invariants_empty_corpus_exits_clean(empty_corpus_project):
    """Empty corpus + --public-api must exit 0 (no crash, no error path)."""
    result = _run_invariants(empty_corpus_project, ["--public-api"])
    assert result.exit_code == 0, (
        f"Expected exit 0 on empty corpus, got {result.exit_code}:\n{result.output}"
    )


def test_invariants_empty_corpus_emits_structured_envelope(empty_corpus_project):
    """Empty corpus must produce a parseable roam JSON envelope."""
    result = _run_invariants(empty_corpus_project, ["--public-api"])
    assert result.exit_code == 0, result.output
    # Never empty stdout (Pattern 1, variant C):
    assert result.output.strip(), "envelope must not be empty on empty corpus"
    data = json.loads(result.output)
    assert_json_envelope(data, "invariants")
    # ``symbols`` payload must be present as an explicit empty list, not
    # absent — agents check len() not key presence.
    assert "symbols" in data, "envelope must carry 'symbols' key even when empty"
    assert isinstance(data["symbols"], list)
    assert data["symbols"] == [], (
        f"expected empty symbols list on empty corpus, got: {data['symbols']!r}"
    )


def test_invariants_empty_corpus_verdict_mentions_empty(empty_corpus_project):
    """Verdict must signal emptiness — not a generic success line."""
    result = _run_invariants(empty_corpus_project, ["--public-api"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    verdict = data["summary"].get("verdict", "")
    assert isinstance(verdict, str) and verdict, "verdict must be a non-empty string"
    verdict_l = verdict.lower()
    # Accept any explicit empty-state signal: "empty", "no symbols",
    # "0 invariants" all communicate the same fact without claiming
    # silent success (Pattern 2).
    empty_markers = ("empty", "no symbols", "0 invariants", "0 symbols")
    assert any(m in verdict_l for m in empty_markers), (
        f"verdict {verdict!r} must surface empty-state markers, "
        f"one of: {empty_markers}"
    )


def test_invariants_empty_corpus_summary_has_partial_success(empty_corpus_project):
    """Empty-state envelope must expose ``summary.partial_success`` so
    downstream tooling can distinguish "ran cleanly, no findings" from
    "ran on an incomplete index" (Pattern 2, Fix E).
    """
    result = _run_invariants(empty_corpus_project, ["--public-api"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    summary = data["summary"]
    assert "partial_success" in summary, (
        "summary.partial_success must be present on empty-corpus envelopes "
        "to disambiguate 'no findings' from 'silent fallback' (Pattern 2). "
        f"summary keys: {sorted(summary.keys())}"
    )
    assert isinstance(summary["partial_success"], bool)


def test_invariants_empty_corpus_agent_contract_facts_nonempty(empty_corpus_project):
    """Auto-derived agent_contract.facts must carry at least one anchor
    so agents consuming only the contract block see *something*
    actionable. Empty facts on empty corpus is a Pattern 2 silent
    fallback — agents would assume the command never ran.
    """
    result = _run_invariants(empty_corpus_project, ["--public-api"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    contract = data.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list)
    assert len(facts) >= 1, (
        f"agent_contract.facts must be non-empty even on empty corpus, "
        f"got: {facts!r}"
    )
    # Every fact should be a non-empty string anchored on a concrete
    # noun terminal (LAW 4). We don't re-run the full anchor lint here
    # (that's tests/test_law4_lint.py's job) — just ensure the strings
    # are not blank placeholders.
    for f in facts:
        assert isinstance(f, str) and f.strip(), f"fact must be non-blank: {f!r}"
