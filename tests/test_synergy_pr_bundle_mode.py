"""W14.2 Synergy 2 — ``roam pr-bundle emit`` respects the active mode.

In ``read_only`` mode, ``pr-bundle emit`` refuses to finalise the bundle
and returns ``state: "mode_restricted"`` with an upgrade-suggesting
verdict. The bundle file on disk is preserved (we never destroy state).
Exit code is 0 by default; ``--strict`` flips ``validate`` to exit 5.

In any higher mode (``safe_edit`` / ``migration`` / ``autonomous_pr``)
emit proceeds as today.
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

from roam.modes import set_active_mode  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """Minimal git repo so find_project_root() resolves correctly."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    subprocess.run(
        ["git", "checkout", "-B", "test-branch"], cwd=proj, capture_output=True
    )
    monkeypatch.chdir(proj)
    # Clear any inherited env mode so the test owns mode state via
    # .roam/active_mode (the policy module's "file" precedence tier).
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    return proj


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


def _init_minimal_bundle(cli_runner) -> None:
    """Open an empty bundle on the current branch for the test to operate on."""
    r = _invoke(cli_runner, ["pr-bundle", "init", "--intent", "test PR"])
    assert r.exit_code == 0, r.output


def _bundle_file(proj: Path, branch: str = "test-branch") -> Path:
    safe = branch.replace("/", "__")
    return proj / ".roam" / "pr-bundles" / f"{safe}.json"


# ---------------------------------------------------------------------------
# 1. read_only mode -> emit returns mode_restricted, does not clobber bundle
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_in_read_only_mode_returns_restricted(
    cli_runner, bundle_project
):
    """read_only mode: emit refuses; state=mode_restricted; bundle untouched."""
    _init_minimal_bundle(cli_runner)
    set_active_mode(bundle_project, "read_only")

    # Snapshot the on-disk bundle BEFORE emit so we can assert it
    # is NOT clobbered.
    bundle_path = _bundle_file(bundle_project)
    assert bundle_path.exists(), "init should have created the bundle"
    before = bundle_path.read_text(encoding="utf-8")

    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    summary = data["summary"]
    assert summary["state"] == "mode_restricted", summary
    assert summary["partial_success"] is True, summary
    # Verdict must name the upgrade target so the agent can copy-paste.
    assert "roam mode safe_edit" in summary["verdict"], summary["verdict"]
    assert "read_only" in summary["verdict"], summary["verdict"]

    # Bundle file content is unchanged.
    after = bundle_path.read_text(encoding="utf-8")
    assert before == after, "bundle was clobbered while in read_only mode"


# ---------------------------------------------------------------------------
# 2. safe_edit mode -> emit proceeds normally
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_in_safe_edit_mode_proceeds(cli_runner, bundle_project):
    """safe_edit allows emit; state is incomplete (no proofs added yet)."""
    _init_minimal_bundle(cli_runner)
    set_active_mode(bundle_project, "safe_edit")

    result = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="pr-bundle")

    summary = data["summary"]
    # safe_edit lets emit through; the bundle is empty so emit reports
    # "incomplete" (NOT "mode_restricted").
    assert summary["state"] != "mode_restricted", summary
    # Either "incomplete" (missing proofs) or "complete"; both are
    # downstream of the soft-gate firing.
    assert summary["state"] in ("incomplete", "complete"), summary
    assert "mode_restricted" not in summary["verdict"]


# ---------------------------------------------------------------------------
# 3. validate --strict + read_only -> exits 5
# ---------------------------------------------------------------------------


def test_pr_bundle_emit_strict_in_read_only_exits_5(cli_runner, bundle_project):
    """validate --strict in read_only mode exits 5 (CI-gate signal).

    The gate replaces the "incomplete proof" exit-5 with a "mode_restricted"
    exit-5: CI still blocks, but the verdict points at the mode fix rather
    than at missing proofs. Without --strict the same call would exit 0
    (state visible but non-blocking).
    """
    _init_minimal_bundle(cli_runner)
    set_active_mode(bundle_project, "read_only")

    result = _invoke(cli_runner, ["--json", "pr-bundle", "validate", "--strict"])
    assert result.exit_code == 5, result.output
    # And without --strict, no exit-5 escalation.
    result_loose = _invoke(cli_runner, ["--json", "pr-bundle", "validate"])
    assert result_loose.exit_code == 0, result_loose.output
    data = parse_json_output(result_loose, command="pr-bundle-validate")
    assert data["summary"]["state"] == "mode_restricted", data["summary"]
