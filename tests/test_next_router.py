"""Tests for the R15 agent-router command ``roam next``.

Covers the five decision-tree branches plus the JSON envelope shape:

  1. uninitialized  -> suggests ``roam init``
  2. idle           -> suggests ``roam tour``
  3. uncommitted    -> suggests ``roam diff``
  4. stale_index    -> suggests ``roam index --force``
  5. JSON envelope  -> verdict/command/reason/state + agent_contract.next_commands
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_project(tmp_path: Path, name: str = "router_proj") -> Path:
    """Create a tiny git-initialised project with one Python file."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 42\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# 1. No index — suggests `roam init`
# ---------------------------------------------------------------------------


def test_next_no_index_suggests_init(cli_runner, tmp_path, monkeypatch):
    """An empty tmp dir with no `.roam/` should produce a `roam init` verdict.

    We use a bare directory (no git, no source files) because that's the
    purest "uninitialized" state. ``find_project_root`` falls back to cwd
    in that case.
    """
    proj = tmp_path / "empty"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "roam init" in result.output, result.output


# ---------------------------------------------------------------------------
# 2. Clean tree with index — suggests `roam tour`
# ---------------------------------------------------------------------------


def test_next_with_index_and_clean_tree_suggests_tour(cli_runner, tmp_path, monkeypatch):
    """Indexed project with no uncommitted changes routes to `roam tour`.

    Note: there's a narrow race window — indexing may technically dirty
    the working tree (e.g. via .gitignore'd .roam/). We commit after
    indexing to be safe. Then we touch the DB so it's newer than every
    source file (defeats stale-index check).
    """
    proj = _make_minimal_project(tmp_path, "idle_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    # Touch the DB so its mtime is newer than any source file.
    db_path = proj / ".roam" / "index.db"
    assert db_path.exists()
    future = time.time() + 60
    os.utime(db_path, (future, future))

    # Ensure no uncommitted state — commit anything indexing might
    # have produced (defensive; .roam is in .gitignore so usually clean).
    subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True)
    subprocess.run(["git", "commit", "-m", "post-index", "--allow-empty"], cwd=proj, capture_output=True)

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "roam tour" in result.output, result.output


# ---------------------------------------------------------------------------
# 3. Uncommitted changes — suggests `roam diff`
# ---------------------------------------------------------------------------


def test_next_with_uncommitted_changes_suggests_diff(cli_runner, tmp_path, monkeypatch):
    """Indexed project + a dirty file => router suggests `roam diff`.

    The dirty-file branch fires only when (a) the index exists AND
    (b) it is not stale AND (c) there is no recent saved envelope.
    We touch the DB to defeat the staleness check, and we don't create
    any `.roam/responses/` content so the envelope branch is inert.
    """
    proj = _make_minimal_project(tmp_path, "dirty_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    db_path = proj / ".roam" / "index.db"
    future = time.time() + 60
    os.utime(db_path, (future, future))

    # Modify app.py without committing.
    (proj / "app.py").write_text("def main():\n    return 99  # changed\n")

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "roam diff" in result.output, result.output


# ---------------------------------------------------------------------------
# 4. Stale index — suggests `roam index --force`
# ---------------------------------------------------------------------------


def test_next_with_stale_index_suggests_force(cli_runner, tmp_path, monkeypatch):
    """A source file newer than the DB triggers the stale-index branch.

    We backdate the DB mtime so any post-index file write is "newer".
    """
    proj = _make_minimal_project(tmp_path, "stale_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    db_path = proj / ".roam" / "index.db"
    # Backdate the DB by an hour.
    past = time.time() - 3600
    os.utime(db_path, (past, past))

    # Write a NEW source file so newest_source_mtime > db_mtime.
    new_file = proj / "fresh.py"
    new_file.write_text("def helper():\n    return 1\n")
    future = time.time() + 60
    os.utime(new_file, (future, future))

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    # The stale branch's verdict mentions `roam index --force`. We accept
    # either the full phrase or the shorter `roam index` form to keep the
    # test robust to minor wording tweaks — but `--force` is the load-bearing
    # signal that the stale branch fired and not the dirty / idle branches.
    assert "--force" in result.output, result.output
    assert "roam index" in result.output, result.output


# ---------------------------------------------------------------------------
# 5. JSON envelope shape
# ---------------------------------------------------------------------------


def test_next_json_envelope_shape(cli_runner, tmp_path, monkeypatch):
    """`roam --json next` envelope has all required fields plus a
    populated ``agent_contract.next_commands`` whose first entry's bare
    command matches ``summary.command``.
    """
    proj = tmp_path / "envelope_proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output

    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)

    assert data["command"] == "next"
    summary = data["summary"]
    assert isinstance(summary, dict)
    for key in ("verdict", "command", "reason", "state"):
        assert key in summary, f"summary missing '{key}': {summary}"
    assert isinstance(summary["verdict"], str) and summary["verdict"]
    assert isinstance(summary["command"], str) and summary["command"]
    # The "uninitialized" branch should fire on a bare directory.
    assert summary["state"] == "uninitialized"
    assert summary["command"] == "init"

    # agent_contract.next_commands[0] is auto-derived by json_envelope
    # from the top-level ``next_steps`` payload. Its bare verb must
    # match summary.command.
    contract = data.get("agent_contract")
    assert isinstance(contract, dict), f"missing agent_contract: keys={list(data.keys())}"
    next_commands = contract.get("next_commands")
    assert isinstance(next_commands, list) and next_commands, contract
    first = next_commands[0]
    assert isinstance(first, str) and first
    # Strip the leading 'roam ' if present and confirm verb match.
    stripped = first[5:].strip() if first.startswith("roam ") else first.strip()
    bare = stripped.split()[0] if stripped.split() else stripped
    assert bare == summary["command"], (
        f"agent_contract.next_commands[0]={first!r} bare-verb {bare!r} != summary.command {summary['command']!r}"
    )


# ---------------------------------------------------------------------------
# 6. Recent-envelope branch — suggests from prior next_commands
# ---------------------------------------------------------------------------


def test_next_with_recent_envelope_suggests_from_prior(cli_runner, tmp_path, monkeypatch):
    """A saved envelope under `.roam/responses/` whose
    ``agent_contract.next_commands[0]`` is non-empty should be surfaced
    by ``roam next`` ahead of the uncommitted-changes branch.

    This is an integration check on the from_prior_envelope decision
    branch — independent of the four primary branches.
    """
    proj = _make_minimal_project(tmp_path, "prior_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    db_path = proj / ".roam" / "index.db"
    future = time.time() + 60
    os.utime(db_path, (future, future))

    # Plant a fake response envelope.
    resp_dir = proj / ".roam" / "responses"
    resp_dir.mkdir(parents=True, exist_ok=True)
    fake = {
        "command": "preflight",
        "summary": {"verdict": "ok"},
        "agent_contract": {
            "facts": ["x: 1"],
            "risks": [],
            "next_commands": ["roam impact main"],
            "confidence": None,
        },
    }
    (resp_dir / "fake_handle.json").write_text(json.dumps(fake), encoding="utf-8")

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "roam impact" in result.output, result.output
    assert "from prior envelope" in result.output.lower() or "suggested by last command" in result.output.lower()


# ---------------------------------------------------------------------------
# 6b. W19.1 — stale envelopes (>24h by default) must NOT win the from_prior
#     branch. Otherwise `roam next` happily routes agents to do-nothing work
#     based on a week-old hint.
# ---------------------------------------------------------------------------


def test_next_skips_stale_envelope(cli_runner, tmp_path, monkeypatch):
    """An envelope older than the staleness cutoff is ignored by the router.

    W19.1 regression: before the fix, ``_read_recent_envelope_next_command``
    returned the newest envelope regardless of age, so a week-old
    ``next_commands[0]`` won the branch over the (correct) idle suggestion.
    """
    proj = _make_minimal_project(tmp_path, "stale_envelope_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    # Defeat the staleness branch — touch the DB forward.
    db_path = proj / ".roam" / "index.db"
    future = time.time() + 60
    os.utime(db_path, (future, future))

    # Plant an OLD response envelope (48h ago, > the 24h default cutoff).
    resp_dir = proj / ".roam" / "responses"
    resp_dir.mkdir(parents=True, exist_ok=True)
    fake = {
        "command": "preflight",
        "summary": {"verdict": "ok"},
        "agent_contract": {"next_commands": ["roam impact main"]},
    }
    stale_envelope = resp_dir / "stale_handle.json"
    stale_envelope.write_text(json.dumps(fake), encoding="utf-8")
    two_days_ago = time.time() - (48 * 60 * 60)
    os.utime(stale_envelope, (two_days_ago, two_days_ago))

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    # Working tree is clean (git_init committed everything), so the
    # idle branch should win. The stale envelope must NOT route the
    # agent to `roam impact`.
    assert "roam impact" not in result.output, (
        f"stale (48h-old) envelope leaked into next-step output:\n{result.output}"
    )
    assert "roam tour" in result.output, result.output


def test_next_envelope_cutoff_overridable(cli_runner, tmp_path, monkeypatch):
    """The W19.1 cutoff is opt-out via ``ROAM_NEXT_ENVELOPE_MAX_AGE_SEC=0``.

    Restores pre-fix behavior for users / CI pipelines that prefer the
    old semantics. Validates that the env-var override actually flows
    through ``_read_recent_envelope_next_command``.
    """
    proj = _make_minimal_project(tmp_path, "stale_envelope_optout_proj")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    db_path = proj / ".roam" / "index.db"
    future = time.time() + 60
    os.utime(db_path, (future, future))

    resp_dir = proj / ".roam" / "responses"
    resp_dir.mkdir(parents=True, exist_ok=True)
    fake = {"agent_contract": {"next_commands": ["roam impact main"]}}
    stale_envelope = resp_dir / "stale_handle.json"
    stale_envelope.write_text(json.dumps(fake), encoding="utf-8")
    two_days_ago = time.time() - (48 * 60 * 60)
    os.utime(stale_envelope, (two_days_ago, two_days_ago))

    monkeypatch.setenv("ROAM_NEXT_ENVELOPE_MAX_AGE_SEC", "0")
    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "roam impact" in result.output, result.output
