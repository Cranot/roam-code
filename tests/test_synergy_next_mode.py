"""W14.2 Synergy 1 — ``roam next`` consults a recent intent-check BLOCKED event.

When the active run logs an ``intent-check`` event with a ``BLOCKED`` verdict
and the intent-check command emitted an upgrade-mode hint in
``signals.next_commands``, ``roam next`` should surface a "Run `roam mode
<upgrade>` to enable <command>" verdict.

Conversely, when there's no such event (clean state, no active run, or no
recent BLOCKED), the suggestion must NOT mention a mode upgrade.

These two tests exercise the new ``mode_upgrade_needed`` decision branch
in :func:`roam.commands.cmd_next._select_suggestion`.
"""

from __future__ import annotations

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

from roam.runs.ledger import log_event, start_run  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_clean_indexed_project(tmp_path: Path, name: str = "synergy1_proj") -> Path:
    """Return a git-init'd, indexed, freshened-DB project root.

    The DB mtime is bumped 60s into the future so the stale-index branch
    cannot fire and short-circuit ``_select_suggestion`` before the
    mode-upgrade branch is reached. Working tree is committed so the
    uncommitted-changes branch is also inert.
    """
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)

    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"

    db_path = proj / ".roam" / "index.db"
    assert db_path.exists()
    future = time.time() + 60
    os.utime(db_path, (future, future))

    # Commit any index by-products so the tree is clean.
    subprocess.run(["git", "add", "-A"], cwd=proj, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "post-index", "--allow-empty"],
        cwd=proj,
        capture_output=True,
    )
    return proj


# ---------------------------------------------------------------------------
# 1. Recent intent-check BLOCKED event surfaces a mode upgrade suggestion
# ---------------------------------------------------------------------------


def test_next_suggests_mode_upgrade_after_blocked_intent_check(cli_runner, tmp_path, monkeypatch):
    """A logged ``intent-check`` BLOCKED event drives the new branch.

    We seed:
      - an active in-progress run
      - one ``intent-check`` event with summary_verdict starting "BLOCKED"
        AND signals.next_commands == ["roam mode autonomous_pr  # ..."]
        (matches the exact shape cmd_intent_check.py emits)

    Then ``roam next`` should suggest ``roam mode autonomous_pr`` and
    name the blocked command (``attest``) in its verdict.
    """
    proj = _make_clean_indexed_project(tmp_path, "block_proj")
    monkeypatch.chdir(proj)

    # Open a run so get_active_run_id() resolves.
    run = start_run(proj, agent="claude-code")
    # Bind the command to the run created by this test. An ambient
    # ROAM_RUN_ID intentionally has precedence over disk discovery; allowing a
    # developer/CI value to leak here made the event appear to vanish.
    monkeypatch.setenv("ROAM_RUN_ID", run.run_id)

    # Log an intent-check BLOCKED event. The shape mirrors what
    # cmd_intent_check.py's auto_log call emits in production.
    log_event(
        proj,
        run.run_id,
        action="intent-check",
        target="attest",
        envelope_command="intent-check",
        summary_verdict="BLOCKED — 'attest' not allowed in safe_edit mode; run `roam mode autonomous_pr` to enable it",
        partial_success=True,
        signals={
            "facts": [
                "active mode is safe_edit",
                "'attest' is BLOCKED",
                "'attest' is allowed in: autonomous_pr",
            ],
            "next_commands": ["roam mode autonomous_pr  # to unlock 'attest'"],
        },
    )

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    # The verdict should propose the upgrade AND name the blocked cmd.
    assert "roam mode autonomous_pr" in result.output, result.output
    assert "attest" in result.output, result.output


# ---------------------------------------------------------------------------
# 2. Clean state does not surface a mode upgrade
# ---------------------------------------------------------------------------


def test_next_no_mode_suggestion_when_clean(cli_runner, tmp_path, monkeypatch):
    """Clean repo + no intent-check trail: idle branch, no mode hint.

    No active run, no BLOCKED event -> the new branch does NOT fire and
    the verdict falls through to ``idle`` (``roam tour``).
    """
    proj = _make_clean_indexed_project(tmp_path, "clean_proj")
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["next"], cwd=proj)
    assert result.exit_code == 0, result.output
    # Idle branch is the catch-all; verifies the new branch did NOT fire.
    assert "roam tour" in result.output, result.output
    # And no mode-upgrade phrasing leaked in:
    assert "roam mode " not in result.output, result.output
    assert "BLOCKED" not in result.output, result.output
