"""W828 - Empty-corpus smoke for ``roam pr-risk`` (W805 sweep).

Pins the Pattern 2 "always-emit" discipline on ``pr-risk``'s no-changes
branch: a fresh git repo with a single committed file and no pending
diff must still produce a structured envelope. Specifically, the empty
branch must NOT silently emit a default ``"Low risk (0/100)"`` /
``"SAFE"`` verdict that pretends the analysis ran; it must surface the
empty state explicitly so agents can distinguish "scanned, clean" from
"nothing to scan".

Contract asserted:
- exit code 0
- stdout parses as a single ``json_envelope`` dict
- ``summary.verdict`` mentions the empty state (not the default
  low-risk fallback)
- ``summary.partial_success`` is present (Pattern 2 always-emit)
- ``agent_contract.facts`` is a non-empty list
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
    """A git repo with one committed (empty) Python file and a clean tree.

    ``pr-risk`` needs git history to resolve the active author + per-file
    blame, so an initialised repo with at least one commit is required.
    The committed file has no symbols so the corpus is effectively empty
    from the indexer's point of view.
    """
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    # An empty .py file is enough to give git something to track without
    # introducing any symbols the indexer can extract.
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


def test_pr_risk_empty_corpus_emits_structured_envelope(empty_corpus_repo):
    """`roam --json pr-risk` on an empty/clean corpus must emit a
    structured envelope (not crash, not empty stdout, not a default
    "Low risk" verdict).
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-risk"], catch_exceptions=False)

    # Exit code 0 - the empty branch is a normal success, not an error.
    assert result.exit_code == 0, f"pr-risk exited {result.exit_code} on empty corpus:\n{result.output}"

    # Stdout must be non-empty and JSON-parseable (Pattern 1 variant C).
    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "pr-risk"

    summary = env.get("summary") or {}

    # Verdict mentions empty state (not the default "low risk" fallback).
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got: {verdict!r}"
    verdict_lc = verdict.lower()
    # The empty-state verdict must explicitly disclose the empty corpus
    # rather than re-using the "low risk" / "safe" framing reserved for
    # graded risk scores on real changes.
    empty_markers = ("no-changes", "no changes", "empty", "no diff")
    assert any(m in verdict_lc for m in empty_markers), f"verdict must mention empty state, got: {verdict!r}"
    # Pattern 2 anti-pattern: the empty branch must not silently emit a
    # graded-risk verdict ("Low risk", "SAFE", etc.) that pretends the
    # analysis ran on real changes.
    forbidden_markers = ("low risk", "moderate risk", "high risk", "critical risk", "safe to merge")
    assert not any(m in verdict_lc for m in forbidden_markers), (
        f"verdict must not use graded-risk framing on empty corpus, got: {verdict!r}"
    )

    # Pattern 2 always-emit: partial_success must be present (False is
    # acceptable - it means "scanned cleanly", which is distinguishable
    # from "absent key" = "didn't run").
    assert "partial_success" in summary, (
        f"summary.partial_success must be present on empty corpus, got summary keys: {sorted(summary.keys())}"
    )

    # Auto-derived agent_contract.facts must be non-empty so agents
    # consuming only the bounded contract still have at least one
    # concrete-noun fact to act on.
    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"
