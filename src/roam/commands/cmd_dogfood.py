"""``roam dogfood`` — run the v2 stack on the current repo in one shot.

Bundles ``audit`` + ``pr-analyze`` (against uncommitted diff) + audit-trail
emission + ``audit-trail-conformance-check`` into a single envelope. The
"show me everything roam can do for me" command — equally useful as a
dev's local self-check and as a customer's first-touch demo.

Lives at the top of the funnel: a new user runs ``roam dogfood`` once
and sees the full v2 product surface in 30 seconds. No subscription,
no API key, no upload.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because dogfood outputs are invocation-scoped compound audit
envelopes — not per-location violations. ``dogfood`` delegates SARIF
emission to composed subcommands (``audit``, ``pr-analyze``, etc.) when
their own ``--sarif`` flag fires directly. See action.yml
_SUPPORTED_SARIF allowlist + W1145 / W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import json as _json

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.audit_trail_helpers import DEFAULT_AUDIT_TRAIL_PATH
from roam.commands.git_helpers import git_metadata
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json


def _run_subcommand(args: list[str]) -> dict:
    """Invoke a roam subcommand in-process and return its parsed envelope.

    On JSON-parse failure or non-zero exit, returns a sentinel dict carrying
    ``_subcommand_failed=True`` so the caller's compositor can detect it
    instead of silently treating an error envelope as a success (Pattern 2 —
    silent fallback). The legacy ``error``/``exit_code``/``raw_output_excerpt``
    keys are preserved for callers / tests that pin them.
    """
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, args)
    try:
        return _json.loads(result.output)
    except Exception as exc:  # noqa: BLE001 — defensive
        return {
            "_subcommand_failed": True,
            "error": f"{' '.join(args[:3])} failed to produce JSON: {exc}",
            "exit_code": result.exit_code,
            "raw_output_excerpt": result.output[:300] if result.output else "",
        }


@roam_capability(
    name="dogfood",
    category="health",
    summary="Run the v2 stack on the current repo: audit + pr-analyze + audit-trail.",
    inputs=[],
    outputs=["audit", "pr_analyze", "audit_trail", "verdict"],
    examples=["roam dogfood", "roam --json dogfood", "roam dogfood --no-audit-trail"],
    tags=["health", "demo"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command(name="dogfood")
@click.option(
    "--audit/--no-audit",
    default=True,
    show_default=True,
    help="Include the structured audit (health + debt + dead + danger zones).",
)
@click.option(
    "--pr-analyze/--no-pr-analyze",
    "pr_analyze_on",
    default=True,
    show_default=True,
    help="Include pr-analyze on uncommitted changes (the v2 Agent Review engine).",
)
@click.option(
    "--audit-trail/--no-audit-trail",
    "audit_trail_on",
    default=True,
    show_default=True,
    help="Append a record to .roam/audit-trail.jsonl + check Article 12 conformance.",
)
@click.option(
    "--rules",
    "rules_file",
    type=click.Path(),
    default=None,
    help="Pass-through to pr-analyze (default: auto-detect .roam/rules.yml).",
)
@click.pass_context
def dogfood(
    ctx,
    audit: bool,
    pr_analyze_on: bool,
    audit_trail_on: bool,
    rules_file: str | None,
) -> None:
    """Run the v2 stack on the current repo and emit one combined envelope.

    \b
    Examples:
      roam dogfood                       # text summary of audit + pr-analyze + conformance
      roam --json dogfood                # full envelope for tooling
      roam dogfood --no-audit-trail      # skip the audit-trail record
      roam dogfood --rules .roam/rules.yml

    Designed as the first-touch experience for new users + as a local
    self-check that surfaces everything Roam can show you in one command.
    Reuses the same engines that power Roam Cloud Lite + Roam Agent Review.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    sections: dict = {}
    git_meta = git_metadata()

    # 1. roam audit — health / debt / dead / danger zone
    if audit:
        sections["audit"] = _run_subcommand(["--json", "audit"])

    # 2. roam pr-analyze on uncommitted diff (with audit-trail when requested)
    if pr_analyze_on:
        pr_args = ["--json", "pr-analyze"]
        if rules_file:
            pr_args.extend(["--rules", rules_file])
        if audit_trail_on:
            pr_args.append("--audit-trail")
        sections["pr_analyze"] = _run_subcommand(pr_args)

    # 3. audit-trail-conformance-check (only meaningful if a trail exists)
    if audit_trail_on and DEFAULT_AUDIT_TRAIL_PATH.exists():
        sections["conformance"] = _run_subcommand(["--json", "audit-trail-conformance-check"])

    # ---- Compose the summary line ----
    audit_summary = (sections.get("audit") or {}).get("summary") or {}
    pr_summary = (sections.get("pr_analyze") or {}).get("summary") or {}
    conf_summary = (sections.get("conformance") or {}).get("summary") or {}

    health_score = audit_summary.get("health_score") or audit_summary.get("score")
    pr_verdict = pr_summary.get("verdict")
    conf_score = conf_summary.get("score")

    # Pattern 2 disclosure: any subcommand that failed to produce a parseable
    # envelope is carried as ``_subcommand_failed=True`` by _run_subcommand.
    # Surface those on the compound envelope so the verdict doesn't read as
    # green when an underlying step crashed.
    failed_sections = sorted(k for k, v in sections.items() if isinstance(v, dict) and v.get("_subcommand_failed"))

    parts = []
    if health_score is not None:
        parts.append(f"health {health_score}")
    if pr_verdict:
        parts.append(f"pr-analyze {pr_verdict}")
    if conf_score is not None:
        parts.append(f"conformance {conf_score}/100")
    if failed_sections:
        parts.append(f"{len(failed_sections)} section(s) failed: {', '.join(failed_sections)}")
    verdict_text = " · ".join(parts) if parts else "no sections enabled"

    summary = {
        "verdict": verdict_text,
        "health_score": health_score,
        "pr_verdict": pr_verdict,
        "conformance_score": conf_score,
        "git_sha": git_meta.get("git_sha"),
        "git_branch": git_meta.get("git_branch"),
        "sections_run": sorted(sections.keys()),
    }
    if failed_sections:
        summary["partial_success"] = True
        summary["failed_sections"] = failed_sections

    if json_mode:
        click.echo(to_json(json_envelope("dogfood", summary=summary, sections=sections)))
    else:
        click.echo(f"VERDICT: {verdict_text}")
        click.echo()
        if health_score is not None:
            click.echo(f"  audit health:    {health_score}/100")
        if pr_verdict:
            blast = pr_summary.get("blast_radius", "?")
            ai = pr_summary.get("ai_likelihood", "?")
            rv = pr_summary.get("rule_violations", 0)
            click.echo(f"  pr-analyze:      {pr_verdict}  (blast {blast}, ai {ai}, rules {rv})")
        if conf_score is not None:
            passed = conf_summary.get("checks_passed", "?")
            total = conf_summary.get("checks_total", "?")
            click.echo(f"  conformance:     {conf_score}/100  ({passed}/{total} checks passed)")
        click.echo()
        click.echo("Drill in:")
        if audit:
            click.echo("  roam audit                            # full health / debt / dead breakdown")
        if pr_analyze_on:
            click.echo("  roam pr-analyze --explain             # rationale + concerns")
        if audit_trail_on:
            click.echo("  roam audit-trail-export --aggregate   # procurement summary")
            click.echo("  roam audit-trail-conformance-check    # EU AI Act Article 12 score")
