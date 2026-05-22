"""Tests for three hosted-product helper commands.

These commands wrap engines already in roam-code:

* ``roam permit`` — verdict facade over critique + preflight
* ``roam postmortem`` — retroactive detector replay over a commit range
* ``roam article-12-check`` — EU AI Act Article 12 readiness assessment

Tests are minimal-but-real: each exercises the command's happy path
in-process via CliRunner, asserts the verdict shape, and verifies
the JSON envelope is well-formed.
"""

from __future__ import annotations

import json as _json

from click.testing import CliRunner


def test_permit_no_diff_no_symbol_returns_allow():
    """`roam permit` with neither --staged, --input, nor --symbol returns ALLOW."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["permit"], catch_exceptions=False)
    # Exit code 0 = ALLOW
    assert result.exit_code == 0
    assert "VERDICT: ALLOW" in result.output


def test_permit_json_mode_emits_envelope():
    """`roam --json permit` returns a well-formed envelope."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "permit"], catch_exceptions=False)
    assert result.exit_code == 0
    env = _json.loads(result.output)
    assert env["schema"] == "roam-envelope-v1"
    summary = env["summary"]
    assert summary["verdict"] in ("ALLOW", "REVIEW", "BLOCK")
    assert "allowed_actions" in summary
    assert "blocked_actions" in summary


def test_permit_verdict_decision_tree():
    """Direct unit-test of `_verdict_from_signals` for each branch."""
    from roam.commands.cmd_permit import _verdict_from_signals

    # 1. High-severity → BLOCK
    v = _verdict_from_signals({"summary": {"high_severity_findings": 3}}, None)
    assert v["verdict"] == "BLOCK"
    assert "commit" in v["blocked_actions"]

    # 2. High preflight risk + big blast → REVIEW
    v = _verdict_from_signals(
        {"summary": {}},
        {"summary": {"risk_level": "HIGH", "blast_radius": 100}},
    )
    assert v["verdict"] == "REVIEW"

    # 3. High preflight risk alone → REVIEW
    v = _verdict_from_signals({"summary": {}}, {"summary": {"risk_level": "HIGH", "blast_radius": 5}})
    assert v["verdict"] == "REVIEW"

    # 4. Nothing → ALLOW
    v = _verdict_from_signals({"summary": {}}, None)
    assert v["verdict"] == "ALLOW"


def test_postmortem_no_commits_in_range_handles_gracefully():
    """`roam postmortem` on an empty range returns no-commits-matched verdict."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "postmortem", "definitely-not-a-real-ref..also-not-real"],
        catch_exceptions=False,
    )
    # Exit code 0 (no findings is fine), JSON envelope present
    assert result.exit_code == 0
    env = _json.loads(result.output)
    assert env["summary"]["commits_scanned"] == 0


def test_article_12_check_runs_on_roam_repo():
    """`roam article-12-check` runs the 6-item checklist."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "article-12-check"], catch_exceptions=False)
    assert result.exit_code == 0
    env = _json.loads(result.output)
    summary = env["summary"]
    assert "passed" in summary
    assert summary["total"] == 6
    assert 0 <= summary["passed"] <= summary["total"]
    items = env["items"]
    assert len(items) == 6
    # Each item carries the documented schema
    for item in items:
        assert "item" in item
        assert "article" in item
        assert "passed" in item
        assert "evidence" in item


def test_article_12_check_markdown_to_stdout_default():
    """Without --output the report is printed as markdown."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["article-12-check"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "# EU AI Act — Article 12 Readiness Assessment" in result.output
    assert "Article 12" in result.output
    assert "Disclaimer" in result.output


def test_article_12_check_writes_output_file(tmp_path):
    """`--output PATH` writes markdown to PATH and prints a one-line verdict."""
    from roam.cli import cli

    out_path = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(cli, ["article-12-check", "--output", str(out_path)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "# EU AI Act — Article 12 Readiness Assessment" in text


def test_article_12_check_text_output_is_emoji_free():
    """`roam article-12-check` text output must be plain ASCII status markers.

    CLAUDE.md output convention: "No emojis, no colors, no box-drawing".
    The checklist used to print U+2705 / U+26A0 emoji marks; status is
    now rendered as ``[OK] PASS`` / ``[WARN] REVIEW``.
    """
    import re

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["article-12-check"], catch_exceptions=False)
    assert result.exit_code == 0
    # No emoji-class codepoints anywhere in the text report.
    emoji = re.findall(r"[\U0001F000-\U0001FAFF☀-➿️]", result.output)
    assert not emoji, f"text output contains emoji codepoints: {emoji!r}"
    # ASCII status markers are present.
    assert "[OK] PASS" in result.output
    assert "[WARN] REVIEW" in result.output
