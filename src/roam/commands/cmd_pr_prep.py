"""``roam pr-prep`` — pre-PR fitness gate that bundles diff + critique + pr-risk.

replaces calling four commands sequentially before opening a PR
with a single envelope. Designed for agents and CI: returns a clear
``ready_to_open`` boolean plus a per-section summary of findings.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cmd_pr_prep is a recipe-composer (chains diff +
critique + pr-risk into a single ``ready_to_open`` envelope). The
composed sub-commands emit their own ``--sarif`` when applicable;
cmd_pr_prep rolls them up into an invocation-scoped pre-PR fitness
gate — not per-location violations. See ``cmd_report`` /
``cmd_workflow`` for the parallel composer disclosure pattern (W1221
/ W1224) + action.yml _SUPPORTED_SARIF allowlist + W1145 / W1085
composer audit + W1224-audit memo.
"""

from __future__ import annotations

import subprocess

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


def _capture_json_subcommand(args: list[str]) -> dict:
    """Invoke a roam subcommand in --json mode and parse its envelope.

    Uses Click's CliRunner for in-process execution — same pattern the
    MCP server uses. Returns ``{"error": ..., "exit_code": N}`` on any
    failure path so the meta-command never crashes the whole envelope.
    """
    import json as _json

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", *args])
    try:
        return _json.loads(result.output)
    except Exception as exc:
        return {
            "error": f"could not parse JSON from `roam {' '.join(args)}`: {exc}",
            "exit_code": result.exit_code,
            "stderr": (result.stderr_bytes.decode("utf-8") if result.stderr_bytes else "")
            if hasattr(result, "stderr_bytes")
            else "",
        }


def _git_diff_text(commit_range: str | None) -> str:
    args = ["git", "diff"]
    if commit_range:
        args.append(commit_range)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


@roam_capability(
    name="pr-prep",
    category="workflow",
    summary="One-shot pre-PR fitness check: diff + critique + pr-risk",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "review"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("pr-prep")
@click.argument("commit_range", required=False, default=None)
@click.option(
    "--high-callers",
    type=int,
    default=10,
    show_default=True,
    help="Direct-caller threshold passed to `critique`.",
)
@click.pass_context
def pr_prep(ctx, commit_range, high_callers) -> None:
    """One-shot pre-PR fitness check: diff + critique + pr-risk.

    \b
    Examples:
      roam pr-prep                  # uncommitted changes
      roam pr-prep main..HEAD       # whole branch
      roam pr-prep HEAD~3           # last 3 commits

    Output bundles three sections:
      - diff blast radius (changed files / affected symbols / blast files)
      - critique (clones-not-edited, blast-radius warnings, intent)
      - pr-risk (composite risk score)
    Plus a top-level ``ready_to_open`` verdict that's ``True`` when no
    section reports a high-severity finding.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    diff_args = ["diff"]
    if commit_range:
        diff_args.append(commit_range)
    diff_payload = _capture_json_subcommand(diff_args)

    diff_text = _git_diff_text(commit_range)
    critique_payload: dict
    if not diff_text.strip():
        critique_payload = {"summary": {"verdict": "no diff to critique", "high_severity": 0}}
    else:
        runner = CliRunner()
        from roam.cli import cli as _cli

        result = runner.invoke(
            _cli,
            ["--json", "critique", "--high-callers", str(high_callers)],
            input=diff_text,
        )
        try:
            import json as _json

            critique_payload = _json.loads(result.output)
        except Exception:
            critique_payload = {
                "error": "critique returned non-JSON output",
                "exit_code": result.exit_code,
            }

    pr_risk_payload = _capture_json_subcommand(["pr-risk"])

    high_severity = (critique_payload.get("summary") or {}).get("high_severity", 0) or 0
    pr_risk_score = (pr_risk_payload.get("summary") or {}).get("risk_score") or 0
    diff_summary = diff_payload.get("summary") or {}

    ready = high_severity == 0 and pr_risk_score < 70
    if ready:
        verdict = (
            f"READY — diff: {diff_summary.get('changed_files', 0)} files / "
            f"{diff_summary.get('affected_symbols', 0)} affected; "
            f"critique: clean; pr-risk: {pr_risk_score}"
        )
    else:
        reasons = []
        if high_severity > 0:
            reasons.append(f"{high_severity} high-severity finding(s)")
        if pr_risk_score >= 70:
            reasons.append(f"pr-risk score {pr_risk_score} ≥ 70")
        verdict = "NOT READY — " + ", ".join(reasons or ["see sections"])

    bundle = {
        "summary": {
            "verdict": verdict,
            "ready_to_open": ready,
            "high_severity_findings": high_severity,
            "pr_risk_score": pr_risk_score,
            "changed_files": diff_summary.get("changed_files"),
            "affected_symbols": diff_summary.get("affected_symbols"),
        },
        "diff": diff_payload,
        "critique": critique_payload,
        "pr_risk": pr_risk_payload,
    }
    pr_prep_envelope = json_envelope("pr-prep", **bundle)
    auto_log(pr_prep_envelope, action="pr-prep", target=commit_range or "")
    if json_mode:
        click.echo(to_json(pr_prep_envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"  changed files:    {diff_summary.get('changed_files', '?')}")
    click.echo(f"  affected symbols: {diff_summary.get('affected_symbols', '?')}")
    click.echo(f"  high-severity:    {high_severity}")
    click.echo(f"  pr-risk score:    {pr_risk_score}")
    if not ready:
        click.echo()
        click.echo("Run individual sections for details:")
        if commit_range:
            click.echo(f"  roam diff {commit_range}")
        else:
            click.echo("  roam diff")
        click.echo("  git diff | roam critique")
        click.echo("  roam pr-risk")
