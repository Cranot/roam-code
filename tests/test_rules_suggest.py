"""CLI tests for ``roam rules-suggest``.

``rules-suggest`` promotes the review-suggestion heuristics that used to be
buried inside ``roam pr-replay`` into a first-class advisory command. It runs
``roam postmortem`` over a commit range, aggregates by detector, and — for the
detector classes that recur — suggests a ``.roam/rules.yml`` body plus CI
gates.

These tests exercise the command end-to-end through the Click runner:

* the empty/no-recurring case (Pattern 1: JSON-on-empty must not crash),
* the JSON envelope contract, and
* the ``--write`` clobber-guard (refuses to overwrite without ``--force``).
"""

from __future__ import annotations

import json
import os
import subprocess

from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_rules_suggest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_repo(tmp_path):
    """Create a tiny git-tracked repo with a couple of commits, indexed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True)

    # A second commit so a range like HEAD~1..HEAD is valid.
    (src / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add b"], cwd=str(proj), capture_output=True)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed: {result.output}"
    finally:
        os.chdir(old_cwd)
    return proj


def _run(args, cwd):
    """Invoke the CLI in-process at *cwd*; return (result, parsed_json_or_None)."""
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    parsed = None
    if "--json" in args and result.exit_code in (0, 2):
        raw = getattr(result, "stdout", None) or result.output
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    return result, parsed


# ---------------------------------------------------------------------------
# Registration / importability
# ---------------------------------------------------------------------------


def test_command_is_registered():
    """``rules-suggest`` is wired into the root CLI group."""
    assert "rules-suggest" in cli.list_commands(None)
    assert callable(cmd_rules_suggest.rules_suggest)


def test_help_invokes(tmp_path):
    result, _ = _run(["rules-suggest", "--help"], cwd=tmp_path)
    assert result.exit_code == 0, result.output
    assert "rules-suggest" in result.output


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------


def test_text_mode_on_range_no_crash(tmp_path):
    """Runs over a real range and prints a VERDICT line without crashing.

    A trivial repo has no recurring detector findings, so this exercises the
    clean 'no suggestions' path.
    """
    proj = _seed_repo(tmp_path)
    result, _ = _run(["rules-suggest", "--tier", "sample"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "VERDICT:" in result.output


def test_text_mode_no_recurring_findings_message(tmp_path):
    """The empty case prints a clean 'no suggestions' line, never a traceback."""
    proj = _seed_repo(tmp_path)
    result, _ = _run(["rules-suggest", "--range", "HEAD~1..HEAD"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "no suggestions" in result.output.lower()


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def test_json_envelope_shape(tmp_path):
    """``--json`` emits a well-formed envelope with the advisory keys (Pattern 1)."""
    proj = _seed_repo(tmp_path)
    result, parsed = _run(["--json", "rules-suggest", "--tier", "sample"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None, f"non-JSON output: {result.output!r}"
    assert parsed["command"] == "rules-suggest"
    summary = parsed["summary"]
    assert "verdict" in summary
    assert summary["tier"] == "sample"
    assert "range" in summary
    # Advisory list keys default to [] on the empty path.
    assert parsed["suggested_ci_gates"] == []
    assert parsed["recurring_risk_classes"] == []
    assert parsed["suggested_rules_cover_detectors"] == []
    # No rules matched → explicit-null preview.
    assert parsed["suggested_roam_rules_yml"] is None


def test_bad_range_is_rejected(tmp_path):
    """A ``--range`` starting with ``-`` is rejected (argv-injection guard)."""
    proj = _seed_repo(tmp_path)
    result, _ = _run(["rules-suggest", "--range", "--upload-pack=evil"], cwd=proj)
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# --write clobber guard (the core safety requirement)
# ---------------------------------------------------------------------------


def test_write_refuses_to_clobber_without_force(tmp_path, monkeypatch):
    """--write must NOT overwrite an existing .roam/rules.yml without --force."""
    proj = _seed_repo(tmp_path)

    # Force a concrete rules preview so the write path is deterministic.
    rules_body = "rules:\n  - id: demo\n    detector: fan-out\n"
    monkeypatch.setattr(
        cmd_rules_suggest,
        "_gather_suggestions",
        lambda tier, commit_range: {
            "suggested_roam_rules_yml": rules_body,
            "suggested_ci_gates": [],
            "recurring_risk_classes": [{"class": "fan-out"}],
            "suggested_rules_cover_detectors": ["fan-out"],
            "replay_tier": tier,
        },
    )

    target = proj / ".roam" / "rules.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "# pre-existing user rules — must NOT be clobbered\n"
    target.write_text(sentinel, encoding="utf-8")

    # Without --force: refuse.
    result, _ = _run(["rules-suggest", "--write"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert "not writing" in result.output.lower()
    assert target.read_text(encoding="utf-8") == sentinel  # untouched

    # With --force: overwrite.
    result, _ = _run(["rules-suggest", "--write", "--force"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == rules_body


def test_write_creates_file_when_absent(tmp_path, monkeypatch):
    """--write creates .roam/rules.yml when it does not yet exist."""
    proj = _seed_repo(tmp_path)
    rules_body = "rules:\n  - id: demo\n    detector: fan-out\n"
    monkeypatch.setattr(
        cmd_rules_suggest,
        "_gather_suggestions",
        lambda tier, commit_range: {
            "suggested_roam_rules_yml": rules_body,
            "suggested_ci_gates": [],
            "recurring_risk_classes": [{"class": "fan-out"}],
            "suggested_rules_cover_detectors": ["fan-out"],
            "replay_tier": tier,
        },
    )
    target = proj / ".roam" / "rules.yml"
    # .roam exists (index) but rules.yml should not.
    if target.exists():
        target.unlink()
    result, _ = _run(["rules-suggest", "--write"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert target.read_text(encoding="utf-8") == rules_body
