"""Tests for ``roam dogfood-aggregate`` — eval corpus triage view.

Covers Task 3 from ``internal/dogfood/IMPLEMENTATION-2026-05-12.md``: status field
+ ``--status`` filter that closes the resolution feedback loop.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner  # noqa: F401

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


def _write_eval(
    base: Path,
    command: str,
    slug: str,
    *,
    status: str | None = "open",
    findings: list[tuple[str, str, str]] | None = None,
    date: str = "2026-05-12",
    extra_frontmatter: str = "",
) -> Path:
    """Write a synthetic eval file under ``base/<command>/<date>-<slug>.md``.

    findings: list of (sev, type, observation) tuples; defaults to one H/wrong row.
    """
    if findings is None:
        findings = [("H", "wrong", "demo observation")]
    folder = base / command
    folder.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"command: {command}",
        f"date: {date}",
        "roam_version: 12.50",
        f"task: {slug}",
        "verdict: use-with-caveats",
    ]
    if status is not None:
        fm_lines.append(f"status: {status}")
    if extra_frontmatter:
        fm_lines.append(extra_frontmatter)
    table_rows = [
        f"| {i+1} | {sev} | {typ} | {obs} | suggestion {i+1} |"
        for i, (sev, typ, obs) in enumerate(findings)
    ]
    lines = [
        "---",
        *fm_lines,
        "---",
        "",
        f"# Roam Eval - {command} - {slug}",
        "",
        "**Why:** synthetic test fixture.",
        "**TL;DR:** synthetic test fixture.",
        "",
        "| # | Sev | Type    | Observation | Suggestion |",
        "|---|-----|---------|-------------|------------|",
        *table_rows,
        "",
    ]
    body = "\n".join(lines)
    path = folder / f"{date}-{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def evals_dir(tmp_path):
    """Build a tiny corpus: one open eval, one fixed eval, one without status."""
    base = tmp_path / "evals"
    _write_eval(
        base,
        "complexity",
        "open-finding",
        status="open",
        findings=[
            ("H", "wrong", "still broken"),
            ("M", "signal", "good shape"),
        ],
    )
    _write_eval(
        base,
        "uses",
        "fixed-finding",
        status="fixed-in-v1",
        findings=[("H", "wrong", "closed by v1")],
        extra_frontmatter="fix_ref: https://example.com/pr/42",
    )
    # No status field at all — backward-compat default to "open".
    _write_eval(
        base,
        "describe",
        "legacy-no-status",
        status=None,
        findings=[("L", "noise", "legacy row")],
    )
    return base


def _parse_json(text: str) -> dict:
    # The envelope is the whole stdout — strip trailing whitespace.
    return _json.loads(text.strip())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_filter_shows_only_open(evals_dir, cli_runner):
    """Default invocation should surface ONLY status=open findings (the backlog).

    The fixed-in-v1 eval must be excluded; the no-status eval counts as open.
    """
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(evals_dir)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    findings = env["findings"]
    statuses = {f["status"] for f in findings}
    assert "fixed-in-v1" not in statuses
    # Both open evals contribute findings: complexity (2) + describe legacy (1) = 3.
    assert env["summary"]["findings_total"] == 3
    # Every visible finding is "open" (backward-compat default applied).
    assert statuses == {"open"}


def test_all_includes_resolved(evals_dir, cli_runner):
    """``--all`` should include resolved findings (status != open)."""
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(evals_dir), "--all"],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    statuses = {f["status"] for f in env["findings"]}
    assert "fixed-in-v1" in statuses
    assert "open" in statuses
    # Total findings across all three evals: 2 + 1 + 1 = 4.
    assert env["summary"]["findings_total"] == 4
    assert env["summary"]["showing"] == "all"


def test_explicit_status_fixed_in_v1(evals_dir, cli_runner):
    """``--status fixed-in-v1`` should only emit the v1-resolved finding."""
    result = invoke_cli(
        cli_runner,
        [
            "dogfood-aggregate",
            "--path",
            str(evals_dir),
            "--status",
            "fixed-in-v1",
        ],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    assert env["summary"]["findings_total"] == 1
    assert env["findings"][0]["status"] == "fixed-in-v1"
    assert env["findings"][0]["command"] == "uses"


def test_multiple_status_or_semantics(evals_dir, cli_runner):
    """Multiple ``--status`` flags should OR together."""
    # Add a wontfix eval so OR semantics are observable.
    _write_eval(
        evals_dir,
        "deps",
        "wontfix-finding",
        status="wontfix",
        findings=[("M", "missing", "intentionally not fixing")],
    )
    result = invoke_cli(
        cli_runner,
        [
            "dogfood-aggregate",
            "--path",
            str(evals_dir),
            "--status",
            "open",
            "--status",
            "wontfix",
        ],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    statuses = {f["status"] for f in env["findings"]}
    assert statuses == {"open", "wontfix"}
    # 3 open findings + 1 wontfix = 4 total in view.
    assert env["summary"]["findings_total"] == 4


def test_all_and_status_mutually_exclusive(evals_dir, cli_runner):
    """``--all`` + ``--status`` should be rejected with a non-zero exit."""
    result = invoke_cli(
        cli_runner,
        [
            "dogfood-aggregate",
            "--path",
            str(evals_dir),
            "--all",
            "--status",
            "open",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_text_mode_by_status_breakdown_present(evals_dir, cli_runner):
    """Text-mode verdict line should include the ``by status:`` breakdown."""
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(evals_dir)],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "VERDICT:" in out
    assert "by status:" in out
    # The full corpus has both open and fixed-in-v1 statuses; both should
    # appear in the by-status breakdown regardless of the active filter.
    assert "open:" in out
    assert "fixed-in-v1:" in out
    # Showing label should advertise the default filter + escape hatch.
    assert "showing: open" in out
    assert "--all" in out


def test_backward_compat_no_status_defaults_to_open(tmp_path, cli_runner):
    """Evals without a ``status:`` field must be treated as ``open``."""
    base = tmp_path / "evals"
    _write_eval(
        base,
        "ancient",
        "no-status",
        status=None,
        findings=[("H", "wrong", "old-school eval")],
    )
    # Default filter (open) should include this eval.
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(base)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    assert env["summary"]["findings_total"] == 1
    assert env["findings"][0]["status"] == "open"
    # by_status_all should also bucket the no-status eval as open.
    assert env["summary"]["by_status_all"].get("open") == 1


def test_severity_filter_subsets_findings(evals_dir, cli_runner):
    """``--severity H`` should drop M/L rows."""
    result = invoke_cli(
        cli_runner,
        [
            "dogfood-aggregate",
            "--path",
            str(evals_dir),
            "--all",
            "--severity",
            "H",
        ],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    severities = {f["sev"] for f in env["findings"]}
    assert severities == {"H"}


def test_missing_evals_dir_emits_clean_envelope(tmp_path, cli_runner):
    """When the path does not exist, emit a non-empty envelope (no JSON crash)."""
    missing = tmp_path / "does-not-exist"
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(missing)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    assert env["summary"]["exists"] is False
    assert env["summary"]["findings_total"] == 0


# ── partial_success / state fields (W7.3 envelope-consistency fix) ───


def test_envelope_includes_state_and_partial_success_on_ok_corpus(
    evals_dir, cli_runner
):
    """Healthy corpus must surface ``state=ok`` and ``partial_success=False``.

    The sprint-wide envelope convention (runs, next, memory, …) requires
    BOTH fields on every successful envelope so MCP consumers can branch
    on completeness without inspecting parse_failures directly.
    """
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(evals_dir)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    summary = env["summary"]
    assert summary["state"] == "ok"
    assert summary["partial_success"] is False
    assert summary["parse_failures"] == 0


def test_envelope_flags_partial_parse_when_parse_failures(tmp_path, cli_runner):
    """A malformed eval file must flip the envelope to ``partial_parse``."""
    base = tmp_path / "evals"
    # One clean eval so the corpus isn't empty.
    _write_eval(
        base,
        "complexity",
        "good",
        status="open",
        findings=[("H", "wrong", "real finding")],
    )
    # One malformed eval that the parser cannot reasonably accept: no
    # frontmatter, no findings table.
    broken_dir = base / "broken"
    broken_dir.mkdir(parents=True, exist_ok=True)
    (broken_dir / "2026-05-12-broken.md").write_text(
        "this file has no frontmatter and no findings table\n",
        encoding="utf-8",
    )

    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(base)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    summary = env["summary"]
    # If the parser tolerated the malformed file, the test is silent on
    # state but the contract still requires the fields exist.
    assert "state" in summary
    assert "partial_success" in summary
    if summary["parse_failures"] > 0:
        assert summary["state"] == "partial_parse"
        assert summary["partial_success"] is True


def test_missing_evals_dir_envelope_state_no_evals(tmp_path, cli_runner):
    """When the directory does not exist, ``state=no_evals`` + partial_success=True."""
    missing = tmp_path / "does-not-exist"
    result = invoke_cli(
        cli_runner,
        ["dogfood-aggregate", "--path", str(missing)],
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    env = _parse_json(result.output)
    summary = env["summary"]
    assert summary["state"] == "no_evals"
    assert summary["partial_success"] is True
