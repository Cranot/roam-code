"""W805 - Empty-corpus regression pin for ``roam diff`` (W805 sweep).

Pins the Pattern 1 / Pattern 2 always-emit discipline on ``diff``'s
no_changes branch. The detector ran cleanly but found nothing to act
on; ``partial_success`` is ``False`` here BUT the verdict must
explicitly say "no changes" rather than pretending blast-radius was
computed, and the envelope must carry the standard structural keys
(changed_files / affected_symbols / blast_radius) as zero / empty so
MCP consumers can do uniform field access.

The ``index_stale`` sibling branch (working tree has changes that
don't intersect the index) is exercised indirectly through the
existing W805 sweep on neighbouring commands and is NOT covered here
-- reliably triggering it from a fresh fixture requires a sequence
that the current ``get_changed_files()`` API (untracked=False default)
doesn't expose. The shape-pin tests below cover the most common
empty-state path; the ``index_stale`` branch's logic lives next to
this code in cmd_diff.py and is visible via diff for human review.

The no_changes branch must emit a structured JSON envelope (Pattern 1
fix A from internal/dogfood/SYNTHESIS-2026-05-12.md): MCP wrappers
crash with ``Expecting value: line 1 column 1 (char 0)`` when given
empty output.

Contract asserted:
- exit code 0
- stdout parses as a single ``json_envelope`` dict
- ``summary.verdict`` explicitly mentions the disclosed state
- ``summary.partial_success`` is present (closed-enum value per branch)
- ``summary.state`` is the expected closed-enum string
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

    Returns the repo Path. The committed file has a single function so
    the indexer has something to extract -- this isolates ``no_changes``
    from the no-symbols variant (which is a separate state). The clean
    tree means ``get_changed_files()`` returns an empty list.
    """
    repo = tmp_path / "clean-diff-repo"
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


def test_diff_no_changes_emits_structured_envelope(clean_indexed_repo):
    """``roam --json diff`` on clean tree must emit a structured envelope
    with ``state: "no_changes"`` rather than empty stdout (Pattern 1
    variant C) or a fabricated blast-radius (Pattern 2).
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "diff"], catch_exceptions=False)

    assert result.exit_code == 0, f"diff exited {result.exit_code} on clean tree:\n{result.output}"

    raw = getattr(result, "stdout", None) or result.output
    assert raw.strip(), "stdout must NOT be empty in --json mode"
    env = _json.loads(raw)
    assert isinstance(env, dict)
    assert env.get("command") == "diff"

    summary = env.get("summary") or {}

    # Closed-enum state. ``no_changes`` is a clean-success state: the
    # detector ran and found zero changes; partial_success stays False
    # so consumers can distinguish "ran cleanly, nothing to do" from
    # "couldn't run".
    assert summary.get("state") == "no_changes", f"summary.state must be 'no_changes', got: {summary.get('state')!r}"
    # Pattern 2 always-emit: the key must be present (False here is
    # acceptable -- it means "scanned cleanly").
    assert "partial_success" in summary, (
        f"summary.partial_success must be present on no-changes branch, got summary keys: {sorted(summary.keys())}"
    )
    assert summary.get("partial_success") is False, (
        f"summary.partial_success must be False on clean-tree no_changes "
        f"(scanned cleanly), got: {summary.get('partial_success')!r}"
    )

    # Verdict must explicitly say "no changes" -- not fabricate a
    # "0 symbols affected" blast-radius line that pretends analysis ran.
    verdict = summary.get("verdict") or ""
    assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got: {verdict!r}"
    assert "no changes" in verdict.lower(), f"verdict must say 'no changes' on clean tree, got: {verdict!r}"

    # The blast-radius fields must be present-but-zero rather than absent
    # so MCP consumers can do uniform field access.
    assert summary.get("changed_files") == 0
    assert summary.get("affected_symbols") == 0
    assert summary.get("affected_files") == 0

    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and len(facts) > 0, f"agent_contract.facts must be non-empty, got: {facts!r}"


def test_diff_no_changes_envelope_includes_blast_radius_lineage(clean_indexed_repo):
    """The no-changes envelope must carry the standard blast-radius keys
    as zero values (not absent) so MCP consumers can do uniform field
    access without per-state branching. This is the structural complement
    to the verdict check above -- the envelope shape stays stable across
    the no-changes and has-changes branches.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "diff"], catch_exceptions=False)

    raw = getattr(result, "stdout", None) or result.output
    env = _json.loads(raw)

    # Top-level structural keys present on the has-changes branch must
    # also be present on no-changes (as zero / empty) for consumer
    # parity. Pre-Fix-A, the no-changes branch emitted empty stdout and
    # MCP wrappers crashed on json.loads.
    assert env.get("changed_files") == 0
    assert env.get("affected_symbols") == 0
    assert env.get("affected_files") == 0
    assert env.get("per_file") == []
    assert env.get("blast_radius") == []
    # The ``message`` field carries the human-readable text -- pin its
    # presence so the CLI text path stays parity with JSON.
    assert "message" in env
    assert isinstance(env["message"], str) and env["message"]
