"""Tests for ``roam pr-analyze`` diff-step failure propagation (Fix B / Pattern 2).

Pre-fix bug: when the internal ``diff`` / ``pr-prep`` subcommand crashed
or returned no parseable output, pr-analyze still emitted
``verdict: "SAFE"`` / ``"READY"`` — fabricating a clean verdict on a
broken cascade. After Fix A (Pattern 1) ships, the diff step returns a
structured ``no_changes`` / ``index_stale`` envelope rather than empty
stdout; pr-analyze must now propagate that state to the top-level
verdict instead of silently defaulting to SAFE.

See `the dogfood synthesis notes` Pattern 2 ("Silent
fallback") for context.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
)

from roam.commands.cmd_pr_analyze import (  # noqa: E402
    _inspect_prep_subcommand_failures,
)

# ---------------------------------------------------------------------------
# Pure-helper tests (no CLI / no DB)
# ---------------------------------------------------------------------------


def test_inspect_handles_no_changes_diff():
    """Diff substep with state=no_changes → top-level state=no_changes."""
    prep = {
        "summary": {"verdict": "READY", "ready_to_open": True},
        "diff": {"summary": {"verdict": "no changes", "state": "no_changes"}},
        "critique": {"summary": {"verdict": "no diff to critique"}},
        "pr_risk": {"summary": {"verdict": "no-changes", "risk_score": 0}},
    }
    failed, state, reason = _inspect_prep_subcommand_failures(prep)
    assert state == "no_changes"
    assert failed == []
    assert "no changes" in reason.lower()


def test_inspect_propagates_diff_error():
    """Diff substep error → state=diff_failed and diff in failed_subcommands."""
    prep = {
        "summary": {"verdict": "READY", "ready_to_open": True},
        "diff": {"error": "could not parse JSON from `roam diff`: Expecting value"},
        "critique": {"summary": {"verdict": "ok"}},
        "pr_risk": {"summary": {"risk_score": 5}},
    }
    failed, state, reason = _inspect_prep_subcommand_failures(prep)
    assert state == "diff_failed"
    assert "diff" in failed
    assert "diff step failed" in reason


def test_inspect_propagates_index_stale_diff():
    """Diff substep reporting state=index_stale counts as a failure for diff."""
    prep = {
        "diff": {"summary": {"verdict": "changed files not in index", "state": "index_stale"}},
        "critique": {"summary": {}},
        "pr_risk": {"summary": {}},
    }
    failed, state, reason = _inspect_prep_subcommand_failures(prep)
    assert state == "diff_failed"
    assert "diff" in failed


def test_inspect_clean_prep_is_silent():
    """A healthy pr-prep envelope produces no signal — verdict logic untouched."""
    prep = {
        "summary": {"verdict": "READY", "ready_to_open": True},
        "diff": {"summary": {"verdict": "3 files changed", "changed_files": 3}},
        "critique": {"summary": {"verdict": "clean"}},
        "pr_risk": {"summary": {"risk_score": 12}},
    }
    failed, state, reason = _inspect_prep_subcommand_failures(prep)
    assert state is None
    assert failed == []


def test_inspect_handles_top_level_pr_prep_error():
    """pr-prep itself failing (not its subcommands) is also surfaced."""
    prep = {"error": "pr-prep failed to produce JSON: boom"}
    failed, state, reason = _inspect_prep_subcommand_failures(prep)
    assert state == "subcommand_failed"
    assert "pr-prep" in failed
    assert "boom" in reason


# ---------------------------------------------------------------------------
# End-to-end CLI tests (clean indexed project + monkeypatched cascade)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def clean_indexed_project(tmp_path, monkeypatch):
    """Tiny fully-committed indexed project (clean working tree)."""
    proj = tmp_path / "clean-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hi():\n    return 'hi'\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


def test_pr_analyze_no_changes_does_not_fabricate_safe(clean_indexed_project, cli_runner):
    """End-to-end: clean tree → pr-analyze must NOT report SAFE / READY.

    With Fix A in place, the inner ``diff`` step returns a structured
    ``no_changes`` envelope. With Fix B in place, pr-analyze sees that
    and propagates it to the top-level verdict (NOCHANGES) instead of
    falling through to the default SAFE.
    """
    result = invoke_cli(cli_runner, ["pr-analyze"], json_mode=True)
    # Exit code may be 0 or 5 depending on gate config — both fine here.
    assert result.exit_code in (0, 5), f"unexpected exit: {result.output}"
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    verdict = summary.get("verdict")

    # The exact fabricated-verdict regression: must NOT be SAFE / READY
    # / INTENTIONAL when there is literally nothing to analyse.
    assert verdict not in ("SAFE", "READY", "INTENTIONAL"), (
        f"pr-analyze fabricated a clean verdict {verdict!r} on a clean tree. See the dogfood synthesis notes Pattern 2."
    )
    # Positive assertion: state must reflect the empty cascade.
    assert summary.get("state") == "no_changes"


def test_pr_analyze_propagates_diff_step_failure(monkeypatch, clean_indexed_project, cli_runner):
    """Mock the pr-prep step into a diff-failure shape → top verdict must reflect failure.

    We replace ``_capture_pr_prep`` with a stub that returns the failure
    envelope shape we expect post-Fix-A. pr-analyze must escalate the
    verdict from SAFE → REVIEW (or higher) and expose
    ``failed_subcommands`` in the summary.
    """
    from roam.commands import cmd_pr_analyze as mod

    failed_prep = {
        "summary": {"verdict": "NOT READY — diff failed", "ready_to_open": False},
        "diff": {
            "error": "could not parse JSON from `roam diff HEAD~3..HEAD`: Expecting value",
            "exit_code": 1,
        },
        "critique": {"summary": {"verdict": "no diff to critique", "high_severity": 0}},
        "pr_risk": {"summary": {"verdict": "no-changes", "risk_score": 0}},
    }

    monkeypatch.setattr(mod, "_capture_pr_prep", lambda *args, **kwargs: failed_prep)

    result = invoke_cli(cli_runner, ["pr-analyze"], json_mode=True)
    assert result.exit_code in (0, 5), f"unexpected exit: {result.output}"
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    verdict = summary.get("verdict")

    # Must NOT silently report SAFE despite an internal diff crash.
    assert verdict != "SAFE", "pr-analyze fabricated SAFE despite diff-step failure"
    # Must explicitly carry the failure state.
    assert summary.get("state") == "diff_failed"
    assert summary.get("partial_success") is True


def test_pr_analyze_failed_subcommands_field(monkeypatch, clean_indexed_project, cli_runner):
    """When any subcommand fails, the envelope exposes ``failed_subcommands``.

    Downstream tools (PR bot, CI gate) need to know which inner step
    broke. Pattern 2 ("silent fallback") meant pr-analyze used to hide
    this; the envelope must now surface it.
    """
    from roam.commands import cmd_pr_analyze as mod

    failed_prep = {
        "summary": {"verdict": "NOT READY", "ready_to_open": False},
        "diff": {"error": "boom: indexing busted"},
        "critique": {"summary": {"verdict": "clean", "high_severity": 0}},
        "pr_risk": {"summary": {"risk_score": 4}},
    }

    monkeypatch.setattr(mod, "_capture_pr_prep", lambda *args, **kwargs: failed_prep)

    result = invoke_cli(cli_runner, ["pr-analyze"], json_mode=True)
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}

    # Must include failed_subcommands listing the broken inner step.
    failed = summary.get("failed_subcommands") or payload.get("failed_subcommands") or []
    assert "diff" in failed, f"expected 'diff' in failed_subcommands; got {failed!r}"


def test_pr_analyze_clean_prep_keeps_existing_verdict(monkeypatch, clean_indexed_project, cli_runner):
    """Sanity: a HEALTHY pr-prep envelope still produces the normal verdict path.

    Fix B must not regress the happy path — only the failure-propagation
    branches change.
    """
    from roam.commands import cmd_pr_analyze as mod

    happy_prep = {
        "summary": {
            "verdict": "READY — diff: 3 files / 5 affected; critique: clean; pr-risk: 12",
            "ready_to_open": True,
            "high_severity_findings": 0,
            "pr_risk_score": 12,
            "changed_files": 3,
            "affected_symbols": 5,
        },
        "diff": {"summary": {"verdict": "3 files changed", "changed_files": 3}},
        "critique": {"summary": {"verdict": "clean", "high_severity": 0}},
        "pr_risk": {"summary": {"risk_score": 12}},
    }

    monkeypatch.setattr(mod, "_capture_pr_prep", lambda *args, **kwargs: happy_prep)

    result = invoke_cli(cli_runner, ["pr-analyze"], json_mode=True)
    assert result.exit_code in (0, 5)
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}

    # Healthy cascade → verdict from the normal verdict logic.
    verdict = str(summary.get("verdict") or "").split(" ", 1)[0]
    assert verdict in ("SAFE", "REVIEW", "BLOCK", "INTENTIONAL")
    assert summary.get("risk_level_canonical") in {"low", "medium", "high", "critical"}
    # No state override (no failure / no_changes signal).
    assert summary.get("state") not in ("no_changes", "diff_failed", "subcommand_failed")
    # No failed_subcommands when nothing failed.
    assert not summary.get("failed_subcommands")
