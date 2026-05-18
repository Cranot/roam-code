"""W805 - Empty-corpus regression pin for ``roam fingerprint`` (W805 sweep).

Pins the Pattern 2 "always-emit" discipline on ``fingerprint``'s
empty-symbol-graph branch (added in W805-followup-B). The spectral
analysis (Fiedler vector + Louvain clustering) operates on the symbol
graph; with zero symbols the pre-fix path would either crash inside
``build_symbol_graph`` / ``compute_fingerprint`` OR return degenerate
metrics (0 layers, 0.000 fiedler) that look indistinguishable from a
tidy result. The fix short-circuits with a structured envelope.

Contract asserted:
- exit code 0
- stdout parses as a single ``json_envelope`` dict
- ``summary.verdict`` mentions the empty state (corpus / empty / no symbols)
- ``summary.partial_success`` is True (Pattern 2 always-emit)
- ``summary.state`` is ``"no_symbols"``
- ``summary.symbol_count`` is 0
- ``agent_contract.facts`` is a non-empty list
- the envelope does NOT carry a ``fingerprint`` payload (the short-circuit
  must skip the spectral compute)
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_corpus_repo(tmp_path, monkeypatch):
    """A git repo with a single committed empty Python file.

    The committed file has no symbols so ``symbols`` ends up empty and the
    fingerprint short-circuit branch fires.
    """
    repo = tmp_path / "empty-fingerprint-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=str(repo),
        capture_output=True,
        env=env,
    )

    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam init failed:\n{out}"
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fingerprint_empty_corpus_emits_structured_envelope(empty_corpus_repo):
    """``roam --json fingerprint`` on empty corpus must short-circuit
    with ``state: "no_symbols"`` + ``partial_success: True``.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "fingerprint"], catch_exceptions=False)

    assert result.exit_code == 0, f"fingerprint exited {result.exit_code} on empty corpus:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "fingerprint"

    summary = env.get("summary") or {}

    # Pattern 2 always-emit: partial_success MUST be True. The spectral
    # analysis cannot meaningfully execute on zero symbols.
    assert summary.get("partial_success") is True, (
        f"summary.partial_success must be True on empty corpus, got: {summary.get('partial_success')!r}"
    )

    # Closed-enum state pins the lineage so consumers can branch on it.
    assert summary.get("state") == "no_symbols", f"summary.state must be 'no_symbols', got: {summary.get('state')!r}"
    # The short-circuit explicitly stamps symbol_count=0 so downstream
    # consumers can confirm zero-row provenance without re-querying.
    assert summary.get("symbol_count") == 0, f"summary.symbol_count must be 0, got: {summary.get('symbol_count')!r}"

    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got: {verdict!r}"
    verdict_lc = verdict.lower()
    empty_markers = ("empty", "no symbols", "corpus")
    assert any(m in verdict_lc for m in empty_markers), f"verdict must mention empty state, got: {verdict!r}"
    # The short-circuit must NOT emit a degenerate "0 layers, modularity
    # 0.00, fiedler 0.000" verdict that pretends the spectral analysis ran.
    forbidden_markers = ("layers,", "modularity", "fiedler", "tangle")
    assert not any(m in verdict_lc for m in forbidden_markers), (
        f"verdict must not look like a computed result, got: {verdict!r}"
    )

    # The short-circuit skips the spectral compute -- the envelope must NOT
    # carry a ``fingerprint`` payload (which would imply analysis ran).
    assert "fingerprint" not in env, (
        f"envelope must not carry a 'fingerprint' payload on empty corpus, got keys: {sorted(env.keys())}"
    )

    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"
