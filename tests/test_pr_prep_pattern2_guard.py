"""Regression test for ``roam pr-prep`` Pattern-2 silent-fallback guard.

Pre-fix bug: ``pr-prep`` chained ``diff`` + ``critique`` + ``pr-risk``
and computed ``ready = high_severity == 0 and pr_risk_score < 70``. If
any of those subcommands returned a non-JSON error payload
(``{"error": ..., "exit_code": N}`` from ``_capture_json_subcommand``),
their summary dict was absent, ``high_severity`` and ``pr_risk_score``
silently defaulted to 0, and the verdict became ``READY`` — the
canonical Pattern-2 (silent fallback) shape called out in CLAUDE.md.

The guard now inspects each subcommand payload for a parseable
``summary`` block; when any are missing it sets
``partial_success: true``, lists the failed subcommands, and emits a
``PARTIAL —`` verdict instead of fabricating ``READY``.

This test exercises the verdict-builder shape directly by patching
``_capture_json_subcommand`` to return error envelopes. It does NOT
need an indexed corpus.
"""

from __future__ import annotations

import json as _json

from click.testing import CliRunner

from roam.cli import cli


def _invoke_with_failed_subcommands(monkeypatch):
    """Run ``roam --json pr-prep`` with every subcommand returning an error envelope."""

    def fake_capture(args):
        return {"error": f"could not parse JSON from `roam {' '.join(args)}`", "exit_code": 1}

    def fake_git_diff(_commit_range):
        # Empty diff → critique step short-circuits to a synthetic
        # "no diff to critique" summary (so critique is NOT counted as
        # failed). Diff + pr-risk both fail via the patched
        # _capture_json_subcommand, which is enough to trigger the
        # Pattern-2 guard.
        return ""

    # Patch the three helpers cmd_pr_prep uses to gather subcommand
    # output. Returning the error shape on every call simulates the
    # cascade-failure case the guard now covers.
    import roam.commands.cmd_pr_prep as mod

    monkeypatch.setattr(mod, "_capture_json_subcommand", fake_capture)
    monkeypatch.setattr(mod, "_git_diff_text", fake_git_diff)

    # ensure_index is a no-op when the workspace already has an index;
    # patch it so the test doesn't need one.
    monkeypatch.setattr(mod, "ensure_index", lambda: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-prep"], catch_exceptions=False)
    return result


def test_pr_prep_pattern2_partial_when_subcommands_fail(monkeypatch):
    """Failing subcommands must not yield a READY verdict.

    Before the guard, ``ready_to_open`` would be ``True`` and the
    verdict would read ``READY — diff: 0 files / 0 affected; ...``
    despite all three subcommands having failed. After the guard:
    ``partial_success: true``, ``ready_to_open: false``, and the
    verdict names the failed subcommands.
    """
    result = _invoke_with_failed_subcommands(monkeypatch)
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output)
    summary = payload["summary"]

    assert summary["partial_success"] is True
    assert summary["ready_to_open"] is False
    failed = summary["failed_subcommands"]
    # ``diff`` and ``pr-risk`` go through ``_capture_json_subcommand``
    # (patched to return an error envelope), so both must be flagged.
    # ``critique`` takes the empty-diff short-circuit and produces a
    # synthetic summary — NOT counted as failed (intentional: empty
    # diff is not a critique failure).
    assert "diff" in failed
    assert "pr-risk" in failed
    assert summary["verdict"].startswith("PARTIAL")
    # The verdict must name at least one failed subcommand so an
    # agent reading only ``verdict`` (LAW 6) sees the cascade.
    assert any(name in summary["verdict"] for name in ("diff", "pr-risk"))


def test_pr_prep_clean_path_unchanged(monkeypatch):
    """The clean-path verdict is unchanged when every subcommand parses.

    Guard must not regress the happy path: when every subcommand
    returns a parseable ``summary`` block, ``partial_success`` is
    ``False`` and the verdict goes through the normal READY /
    NOT-READY branch.
    """

    def fake_capture(args):
        if args[0] == "diff":
            return {"summary": {"verdict": "no changes", "changed_files": 0, "affected_symbols": 0}}
        if args[0] == "pr-risk":
            return {"summary": {"verdict": "low", "risk_score": 12}}
        # Defensive default for any other subcommand.
        return {"summary": {"verdict": "ok"}}

    import roam.commands.cmd_pr_prep as mod

    monkeypatch.setattr(mod, "_capture_json_subcommand", fake_capture)
    monkeypatch.setattr(mod, "_git_diff_text", lambda _r: "")  # empty diff → critique skipped
    monkeypatch.setattr(mod, "ensure_index", lambda: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "pr-prep"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output)
    summary = payload["summary"]
    assert summary["partial_success"] is False
    assert summary["failed_subcommands"] == []
    assert summary["ready_to_open"] is True
    assert summary["verdict"].startswith("READY")
