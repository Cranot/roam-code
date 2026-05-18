"""W805 - Empty-corpus regression pin for ``roam pr-analyze`` (W805 sweep).

Pins the Pattern 2 silent-fallback discipline on ``pr-analyze``'s
no-changes aggregate state. ``pr-analyze`` is the agent-aware PR risk
verdict that aggregates ``pr-prep`` (diff + critique + pr-risk) into a
single INTENTIONAL / SAFE / REVIEW / BLOCK / NOCHANGES verdict.

Before Fix B (Pattern 2 from
internal/dogfood/SYNTHESIS-2026-05-12.md), ``pr-analyze`` would emit a
default ``SAFE`` verdict even when its internal ``diff`` step reported
no_changes -- pretending the cascade ran on real changes. The fix
inspects ``_inspect_prep_subcommand_failures`` and overrides the
verdict to ``NOCHANGES`` with ``partial_success: True`` +
``state: "no_changes"``.

Contract asserted:
- exit code 0 (non-error completion)
- stdout parses as a single ``json_envelope`` dict
- ``summary.verdict`` starts with ``"NOCHANGES"`` (not SAFE / INTENTIONAL / REVIEW)
- ``summary.risk_level_canonical`` is present for W641 consumers
- ``summary.partial_success`` is True (Pattern 2 always-emit)
- ``summary.state`` is ``"no_changes"``
- ``summary.reasons`` is a non-empty list describing the empty cascade
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
def clean_indexed_repo(tmp_path, monkeypatch):
    """A git repo with one committed Python file and a clean tree.

    pr-analyze needs git history + an index to drive its inner pr-prep
    cascade. The clean tree ensures the ``diff`` substep reports
    no_changes, which propagates up via ``_inspect_prep_subcommand_failures``.
    """
    repo = tmp_path / "clean-pr-analyze-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

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


def test_pr_analyze_no_changes_emits_nochanges_verdict(clean_indexed_repo):
    """``roam --json pr-analyze`` on a clean tree must surface
    ``verdict`` base ``NOCHANGES`` + ``state: "no_changes"`` +
    ``partial_success: True`` rather than fabricating a default SAFE
    verdict on an empty cascade.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-analyze"], catch_exceptions=False)

    # pr-analyze returns exit code 0 on the no-changes branch -- not
    # a gate failure, just nothing to analyse.
    assert result.exit_code == 0, f"pr-analyze exited {result.exit_code} on clean tree:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "pr-analyze"

    summary = env.get("summary") or {}

    # The verdict base must be the explicit closed-enum NOCHANGES, not
    # SAFE / INTENTIONAL / REVIEW. W641 may append the canonical risk
    # level for downstream consumers.
    verdict = summary.get("verdict") or ""
    verdict_base = verdict.split(" ", 1)[0]
    assert verdict_base == "NOCHANGES", (
        f"summary.verdict must start with 'NOCHANGES' on empty cascade, got: {verdict!r}"
    )
    assert summary.get("risk_level_canonical") == "low"

    # Pattern 2 always-emit: partial_success MUST be True because the
    # cascade did not analyse real changes.
    assert summary.get("partial_success") is True, (
        f"summary.partial_success must be True on empty cascade, got: {summary.get('partial_success')!r}"
    )

    # Closed-enum state pins the lineage.
    assert summary.get("state") == "no_changes", f"summary.state must be 'no_changes', got: {summary.get('state')!r}"

    # Reasons must explain WHY (non-empty list -- the renderer needs at
    # least one bullet to surface to the human reviewer).
    reasons = summary.get("reasons") or []
    assert isinstance(reasons, list) and len(reasons) > 0, (
        f"summary.reasons must be a non-empty list on no_changes, got: {reasons!r}"
    )
    # The first reason should reference the empty state.
    reasons_text = " ".join(reasons).lower()
    assert "no changes" in reasons_text or "no diff" in reasons_text or "empty" in reasons_text, (
        f"reasons must mention empty state, got: {reasons!r}"
    )

    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"
