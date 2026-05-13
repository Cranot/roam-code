"""Tests for Fix E — empty-state framing (Pattern 2: silent fallbacks).

These tests pin the contract that commands report empty/absent-state cleanly
instead of emitting success-shaped envelopes that mislead consumers about
whether the underlying check actually ran.

Reference: internal/dogfood/SYNTHESIS-2026-05-12.md Pattern 2 + Fix E.

The reference for the correct pattern is ``article-12-check`` which frames
"directory exists vs trail empty" as two distinct states.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner


# ---------------------------------------------------------------------------
# audit-trail-verify -- "chain not initialized" vs "chain BROKEN"
# ---------------------------------------------------------------------------


def test_audit_trail_verify_missing_path_emits_uninitialized_state(tmp_path):
    """When the trail file does not exist, the JSON envelope must report
    ``state: "uninitialized"`` and ``partial_success: true`` -- NOT
    ``chain BROKEN`` which falsely implies tamper detection.
    """
    runner = CliRunner()
    from roam.cli import cli

    missing = tmp_path / "no-trail.jsonl"
    result = runner.invoke(
        cli,
        ["--json", "audit-trail-verify", "--input", str(missing)],
    )
    env = _json.loads(result.output)
    summary = env["summary"]

    # Empty-state framing contract
    assert summary["state"] == "uninitialized"
    assert summary["partial_success"] is True
    assert "not initialized" in summary["verdict"]
    # Preserve backward-compat fields
    assert summary["chain_valid"] is False
    assert summary["total_records"] == 0
    # Must NOT use the misleading "BROKEN" framing for absent state
    assert "BROKEN" not in summary["verdict"]


def test_audit_trail_verify_empty_file_emits_uninitialized_state(tmp_path):
    """A trail file that exists but contains no records must also report
    ``uninitialized`` (not BROKEN), since "trail exists but never written
    to" is distinct from "trail corrupted".
    """
    runner = CliRunner()
    from roam.cli import cli

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-verify", "--input", str(empty)],
    )
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary["state"] == "uninitialized"
    assert summary["partial_success"] is True
    assert "not initialized" in summary["verdict"]
    assert summary["total_records"] == 0


def test_audit_trail_verify_corrupt_chain_still_reports_broken(tmp_path):
    """The verdict shape change must NOT affect the non-empty case: a
    populated trail with chain issues must still report ``state: "broken"``.
    """
    runner = CliRunner()
    from roam.cli import cli

    trail = tmp_path / "trail.jsonl"
    # Two records with a deliberately broken chain link (line 2 expects "")
    trail.write_text(
        '{"previous_record_hash":"","timestamp":"2026-01-01T00:00:00Z","actor":"a","verdict":"SAFE"}\n'
        '{"previous_record_hash":"bogus","timestamp":"2026-01-02T00:00:00Z","actor":"a","verdict":"REVIEW"}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-verify", "--input", str(trail)],
    )
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary["state"] == "broken"
    assert summary["partial_success"] is True
    assert summary["total_records"] == 2
    assert summary["chain_valid"] is False


# ---------------------------------------------------------------------------
# audit-trail-conformance-check -- "no_trail" early return
# ---------------------------------------------------------------------------


def test_audit_trail_conformance_missing_emits_no_trail_state(tmp_path):
    """Absent trail must yield ``state: "no_trail"`` + ``verdict: "no audit
    trail to check"`` -- not a NON-conformant 0/6 score, which would
    silently fall back to a "trail scanned and failed" framing.
    """
    runner = CliRunner()
    from roam.cli import cli

    missing = tmp_path / "no-trail.jsonl"
    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(missing)],
    )
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary["state"] == "no_trail"
    assert summary["partial_success"] is True
    assert summary["verdict"] == "no audit trail to check"
    # The score must be NULL (not 0) to distinguish "no scan" from "scan==0"
    assert summary["score"] is None
    # Every check must be marked not_run, not silently FAIL
    assert all(c.get("state") == "not_run" for c in env["checks"])


def test_audit_trail_conformance_empty_file_emits_no_trail_state(tmp_path):
    """A trail file that exists but is empty must also report no_trail."""
    runner = CliRunner()
    from roam.cli import cli

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["--json", "audit-trail-conformance-check", "--input", str(empty)],
    )
    env = _json.loads(result.output)
    assert env["summary"]["state"] == "no_trail"
    assert env["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# missing-index -- "no_migrations" vs "0 found"
# ---------------------------------------------------------------------------


def test_missing_index_no_migrations_emits_no_migrations_state(tmp_path, monkeypatch):
    """When no PHP migration files exist, the JSON envelope must report
    ``state: "no_migrations"`` -- not ``"No missing indexes detected"``
    which would falsely imply a scan ran and passed.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import git_init, index_in_process

    proj = tmp_path / "py_only"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hi():\n    return 1\n")
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "missing-index"])
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary["state"] == "no_migrations"
    assert summary["partial_success"] is True
    assert "no migrations scanned" in summary["verdict"]
    assert summary["migrations_scanned"] == 0
    # Preserve backward-compat
    assert summary["total"] == 0


# ---------------------------------------------------------------------------
# vulns -- "no_scan" vs "0 found"
# ---------------------------------------------------------------------------


def test_vulns_no_scan_emits_no_scan_state(tmp_path, monkeypatch):
    """When no vulnerability scan has been imported, the envelope must
    report ``state: "no_scan"`` -- not the silent-fallback
    ``"No vulnerabilities found"`` which could be read as "this codebase
    is safe" when in fact no scanner has ever touched it.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import git_init, index_in_process

    proj = tmp_path / "no_scan_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hi():\n    return 1\n")
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "vulns"])
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary["state"] == "no_scan"
    assert summary["partial_success"] is True
    assert "no vulnerability scan available" in summary["verdict"]
    assert summary["total"] == 0
