"""``roam permit`` — structural-permission verdict facade for AI agents.

A single-purpose command that runs ``roam critique`` + ``roam preflight``
+ blast-radius analysis over a staged change (``--staged``), an arbitrary
diff (``--input``), or a target symbol (``--symbol``), then emits a
verdict-shaped JSON envelope:

    {
      "verdict": "ALLOW" | "REVIEW" | "BLOCK",
      "reason": "short human-readable explanation",
      "allowed_actions": ["commit", "merge", "push"],
      "blocked_actions": ["force-push", "auto-merge"],
      "evidence": { "...": ... }
    }

Designed to drop into:

* **Cursor rules** — call `roam permit --staged --json` from a custom
  rule, parse the verdict, gate the agent's next action.
* **Claude Code permission hooks** — wire the JSON output to an
  `allow`/`deny` decision in `~/.claude/settings.json`.
* **pre-commit** — exit code 5 on BLOCK halts the commit.
* **GitHub Actions branch protection** — same exit-code contract.

Exit codes:
* 0 — verdict ALLOW (or no risk surfaced)
* 5 — verdict BLOCK (`EXIT_GATE_FAILURE`)
* 6 — verdict REVIEW (`EXIT_PARTIAL`) — reviewer should look but not blocked

redacted: stand-alone OSS verdict facade
that becomes the engine reused by the Roam Review GitHub App at PR
time. Doing the work here once means the App's PR-comment renderer
can call the same helper.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path

import click
from click.testing import CliRunner

from roam.commands.resolve import ensure_index
from roam.exit_codes import EXIT_GATE_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS
from roam.output.formatter import json_envelope, to_json


def _capture_critique(diff_text: str) -> dict:
    """Invoke roam critique --json on the supplied diff (in-process via CliRunner)."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique"], input=diff_text, catch_exceptions=False)
    if result.exit_code not in (EXIT_SUCCESS, EXIT_GATE_FAILURE):
        return {"error": f"critique exited {result.exit_code}", "output": result.output[:200]}
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError:
        return {"error": "critique produced non-JSON output", "output": result.output[:200]}


def _capture_preflight(symbol: str) -> dict:
    """Invoke roam preflight --json on the supplied symbol."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "preflight", symbol], catch_exceptions=False)
    if result.exit_code not in (EXIT_SUCCESS, EXIT_PARTIAL, EXIT_GATE_FAILURE):
        return {"error": f"preflight exited {result.exit_code}"}
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError:
        return {"error": "preflight produced non-JSON output"}


def _acquire_staged_diff() -> str:
    """Return `git diff --cached` output, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _verdict_from_signals(
    critique_result: dict,
    preflight_result: dict | None,
    *,
    block_on_high_severity: int = 1,
    review_on_blast_radius: int = 60,
) -> dict:
    """Compose verdict + reason + allowed/blocked action lists from raw signals.

    Decision tree (most aggressive first):

    1. Any high-severity critique finding → BLOCK
    2. Preflight risk == HIGH and blast radius > review threshold → REVIEW
    3. Preflight risk == HIGH alone → REVIEW
    4. Otherwise → ALLOW
    """
    high_count = 0
    summary = critique_result.get("summary") or {}
    high_count = int(summary.get("high_severity_findings") or summary.get("high_severity_total") or 0)

    blast_radius = 0
    risk_level = ""
    if preflight_result:
        psumm = preflight_result.get("summary") or {}
        blast_radius = int(psumm.get("blast_radius") or psumm.get("blast_radius_count") or 0)
        risk_level = (psumm.get("risk_level") or psumm.get("overall_risk") or "").upper()

    if high_count >= block_on_high_severity:
        return {
            "verdict": "BLOCK",
            "reason": f"{high_count} high-severity critique finding(s) — fix before merging",
            "allowed_actions": ["edit", "split-pr"],
            "blocked_actions": ["commit", "push", "merge", "auto-merge"],
        }

    if risk_level == "HIGH" and blast_radius >= review_on_blast_radius:
        return {
            "verdict": "REVIEW",
            "reason": f"high blast radius ({blast_radius} symbols affected); reviewer should sign off before merge",
            "allowed_actions": ["commit", "request-review"],
            "blocked_actions": ["auto-merge", "force-push"],
        }

    if risk_level == "HIGH":
        return {
            "verdict": "REVIEW",
            "reason": "high preflight risk — manual review recommended",
            "allowed_actions": ["commit", "request-review"],
            "blocked_actions": ["auto-merge", "force-push"],
        }

    return {
        "verdict": "ALLOW",
        "reason": "no high-severity findings; safe to proceed",
        "allowed_actions": ["commit", "push", "merge"],
        "blocked_actions": [],
    }


def _exit_for_verdict(verdict: str) -> int:
    return {
        "BLOCK": EXIT_GATE_FAILURE,
        "REVIEW": EXIT_PARTIAL,
        "ALLOW": EXIT_SUCCESS,
    }.get(verdict, EXIT_SUCCESS)


@click.command(name="permit")
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Read staged changes via `git diff --cached`. Default mode for pre-commit hooks.",
)
@click.option(
    "--input",
    "input_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read a unified diff from this file instead of git.",
)
@click.option(
    "--symbol",
    default=None,
    help="Run preflight against this symbol name in addition to (or instead of) a diff.",
)
@click.option(
    "--block-on-high-severity",
    type=int,
    default=1,
    help="Number of high-severity critique findings that triggers BLOCK (default 1).",
)
@click.option(
    "--review-on-blast-radius",
    type=int,
    default=60,
    help="Blast-radius threshold (symbols affected) that downgrades to REVIEW (default 60).",
)
@click.pass_context
def permit_cmd(
    ctx,
    staged: bool,
    input_file: str | None,
    symbol: str | None,
    block_on_high_severity: int,
    review_on_blast_radius: int,
):
    """Structural-permission verdict facade for AI agents.

    Returns a verdict (ALLOW / REVIEW / BLOCK) over the staged change,
    a supplied diff, or a target symbol. Wraps `roam critique` and
    `roam preflight` and synthesises a single decision.

    \b
    Workflow examples:
      # pre-commit hook
      roam permit --staged || exit 1

      # Cursor rule
      roam permit --input changes.diff --json

      # Pre-edit safety check on a critical symbol
      roam permit --symbol open_db

      # Both diff + symbol context
      roam permit --staged --symbol open_db

    Exit codes: 0=ALLOW, 5=BLOCK, 6=REVIEW.

    redacted — stand-alone OSS engine that
    the Roam Review GitHub App reuses at PR time.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    diff_text = ""
    if staged:
        diff_text = _acquire_staged_diff()
    elif input_file:
        diff_text = Path(input_file).read_text(encoding="utf-8", errors="replace")

    critique_result: dict = {"summary": {}}
    if diff_text.strip():
        critique_result = _capture_critique(diff_text)

    preflight_result: dict | None = None
    if symbol:
        preflight_result = _capture_preflight(symbol)

    verdict = _verdict_from_signals(
        critique_result,
        preflight_result,
        block_on_high_severity=block_on_high_severity,
        review_on_blast_radius=review_on_blast_radius,
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "permit",
                    summary={
                        "verdict": verdict["verdict"],
                        "reason": verdict["reason"],
                        "allowed_actions": verdict["allowed_actions"],
                        "blocked_actions": verdict["blocked_actions"],
                        "diff_lines": diff_text.count("\n") if diff_text else 0,
                        "symbol": symbol,
                    },
                    critique=critique_result,
                    preflight=preflight_result,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict['verdict']}")
        click.echo(f"  reason:          {verdict['reason']}")
        if verdict["allowed_actions"]:
            click.echo(f"  allowed actions: {', '.join(verdict['allowed_actions'])}")
        if verdict["blocked_actions"]:
            click.echo(f"  blocked actions: {', '.join(verdict['blocked_actions'])}")
        if not diff_text and not symbol:
            click.echo()
            click.echo(
                "(no diff or symbol provided — pass --staged, --input PATH, or --symbol NAME)",
                err=True,
            )

    sys.exit(_exit_for_verdict(verdict["verdict"]))
