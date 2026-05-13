"""Tests for ``roam runs end --with-pr-bundle-emit`` (W15.2 followup).

Fuses the natural agent loop step ``open run → do work → close run + ship
bundle`` into one command. The bundle emit is best-effort — the run is
ALWAYS closed first, so a broken / missing bundle can never block the close.

Covered here:
  1. ``runs end`` without the flag preserves existing behaviour
  2. ``runs end --with-pr-bundle-emit`` closes the run AND emits the bundle
  3. ``runs end --with-pr-bundle-emit`` with no bundle reports state
     ``no_active_bundle_to_emit`` + partial_success=True, still closes the run
  4. The fused envelope carries both run + pr-bundle data
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, parse_json_output  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def bundle_run_project(tmp_path, monkeypatch):
    """A minimal git repo so ``find_project_root()`` resolves correctly."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    # Pin branch so the bundle path is deterministic.
    subprocess.run(
        ["git", "checkout", "-B", "feat-w15"], cwd=proj, capture_output=True
    )
    monkeypatch.chdir(proj)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    return proj


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


# ---------------------------------------------------------------------------
# 1. Without --with-pr-bundle-emit, behaviour is unchanged
# ---------------------------------------------------------------------------


def test_runs_end_without_flag_does_not_emit_bundle(cli_runner, bundle_run_project):
    """W15.2 followup — default ``runs end`` must NOT emit a bundle.

    The flag is OPT-IN. Agents that just want to close a run should not get
    bundle emit as a side-effect.
    """
    r = _invoke(cli_runner, ["--json", "runs", "start", "--agent", "test-agent"])
    assert r.exit_code == 0, r.output

    # Init a bundle so we'd notice an inadvertent emit.
    r = _invoke(
        cli_runner, ["--json", "pr-bundle", "init", "--intent", "should not emit"]
    )
    assert r.exit_code == 0, r.output

    r = _invoke(cli_runner, ["--json", "runs", "end"])
    assert r.exit_code == 0, r.output
    data = parse_json_output(r, command="runs-end")
    # No pr_bundle_emitted field; no pr_bundle_state in summary.
    assert "pr_bundle_emitted" not in data, data
    assert "pr_bundle_state" not in data["summary"], data["summary"]
    # Verdict is the plain run-end verdict.
    assert "emitted pr-bundle" not in data["summary"]["verdict"], data["summary"]


# ---------------------------------------------------------------------------
# 2. With --with-pr-bundle-emit + active bundle → both happen
# ---------------------------------------------------------------------------


def test_runs_end_with_pr_bundle_emit(cli_runner, bundle_run_project):
    """W15.2 followup — closing the run also emits the active bundle.

    Walks the end-to-end loop: start run → init bundle → end run with the
    fusion flag → assert both run is closed AND bundle envelope is in the
    response.
    """
    # Open a run.
    r = _invoke(cli_runner, ["--json", "runs", "start", "--agent", "test-agent"])
    assert r.exit_code == 0, r.output
    start_data = parse_json_output(r, command="runs-start")
    run_id = start_data["summary"]["run_id"]
    assert run_id

    # Init a bundle on this branch.
    r = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "init", "--intent", "test the fusion"],
    )
    assert r.exit_code == 0, r.output

    # Close the run with --with-pr-bundle-emit.
    r = _invoke(cli_runner, ["--json", "runs", "end", "--with-pr-bundle-emit"])
    assert r.exit_code == 0, r.output
    data = parse_json_output(r, command="runs-end")

    # Run state stamped.
    assert data["summary"]["ended"] is True
    assert data["summary"]["run_id"] == run_id

    # pr_bundle_emitted is in the envelope.
    assert "pr_bundle_emitted" in data, (
        f"pr_bundle_emitted missing from runs-end envelope: keys={list(data.keys())}"
    )
    bundle_env = data["pr_bundle_emitted"]
    # It's a real envelope shape (summary present).
    assert "summary" in bundle_env, bundle_env

    # The fused verdict mentions the bundle.
    assert "pr-bundle" in data["summary"]["verdict"].lower(), data["summary"]
    # State tag surfaced for grep-ability.
    assert data["summary"].get("pr_bundle_state") == "emitted", data["summary"]


# ---------------------------------------------------------------------------
# 3. No active bundle → clean envelope, still closes the run
# ---------------------------------------------------------------------------


def test_runs_end_with_flag_no_bundle_clean_envelope(cli_runner, bundle_run_project):
    """W15.2 followup — ``--with-pr-bundle-emit`` on a run with NO bundle
    must close the run cleanly and tag the missing bundle explicitly.

    Pattern 2: absent state is explicit, not silent SAFE.
    """
    # Open a run but skip pr-bundle init.
    r = _invoke(cli_runner, ["--json", "runs", "start", "--agent", "test-agent"])
    assert r.exit_code == 0, r.output

    r = _invoke(cli_runner, ["--json", "runs", "end", "--with-pr-bundle-emit"])
    assert r.exit_code == 0, r.output
    data = parse_json_output(r, command="runs-end")

    # Run is closed.
    assert data["summary"]["ended"] is True

    # pr-bundle status is explicit.
    assert data["summary"].get("pr_bundle_state") == "no_active_bundle_to_emit", data["summary"]
    # partial_success is True (Pattern 2 — absence is partial, not SAFE).
    assert data["summary"]["partial_success"] is True, data["summary"]
    # Verdict says what happened.
    assert "no pr-bundle" in data["summary"]["verdict"].lower(), data["summary"]
    # No nested envelope when no bundle existed.
    assert "pr_bundle_emitted" not in data or data.get("pr_bundle_emitted") is None


# ---------------------------------------------------------------------------
# 4. Help text mentions the flag
# ---------------------------------------------------------------------------


def test_runs_end_help_mentions_with_pr_bundle_emit(cli_runner):
    """The flag must be discoverable via ``roam runs end --help``."""
    r = _invoke(cli_runner, ["runs", "end", "--help"])
    assert r.exit_code == 0
    assert "--with-pr-bundle-emit" in r.output, (
        f"--with-pr-bundle-emit not in help text:\n{r.output}"
    )
