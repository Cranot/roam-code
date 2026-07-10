"""``roam rules-suggest`` — suggest ``.roam/rules.yml`` + CI gates from history.

This promotes the review-suggestion capability that has been hiding inside
``roam pr-replay`` into a first-class advisory command. It runs
``roam postmortem`` over a commit range, aggregates the findings by detector
class, and — for the detector classes that *recur* across the window — emits:

* a preview ``.roam/rules.yml`` body you can drop in to start gating, and
* concrete ``roam <cmd> --ci`` gate invocations for CI.

The heavy lifting is reused verbatim from ``cmd_pr_replay`` — this command is
a thin, buyer-neutral front-end over the same heuristics (no LLM call, no
external lookup). Detector classes with no rule template are silently skipped
rather than mocked.

Output formats: text (default), ``--json``. ``--write`` persists the preview
to ``.roam/rules.yml`` (refuses to clobber an existing file unless ``--force``
is also passed). SARIF is deliberately NOT emitted: this is an advisory
config-suggestion surface, not a per-location violation report.

Usage:

    # Suggest rules from the last 30 commits on the current branch
    roam rules-suggest --tier team

    # Suggest from an explicit range and print the machine envelope
    roam --json rules-suggest --range "v1.0..main"

    # Write the suggested rules to .roam/rules.yml (won't clobber)
    roam rules-suggest --write
    roam rules-suggest --write --force   # overwrite an existing file
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.cmd_pr_replay import (
    _TIERS,
    _aggregate_by_detector,
    _build_review_suggestions,
    _is_safe_commit_range,
    _run_postmortem,
)
from roam.db.connection import find_project_root
from roam.output.formatter import json_envelope, to_json

_RULES_PATH = Path(".roam") / "rules.yml"


def _resolve_range(tier: str, commit_range: str | None) -> str:
    """Mirror ``pr-replay``'s range resolution exactly.

    Absent ``--range``, default to ``HEAD~N..HEAD`` where ``N`` is the tier's
    ``default_count`` (sample=5, team=30, deep=90). An explicit ``--range`` is
    validated against the argv-injection guard and used as-is.
    """
    tier_meta = _TIERS[tier]
    if commit_range is None:
        return f"HEAD~{tier_meta['default_count']}..HEAD"
    if not _is_safe_commit_range(commit_range):
        raise click.UsageError(
            f"--range value must not start with '-' (got {commit_range!r}); "
            "use a git revspec like 'HEAD~30..HEAD', 'v1.0..main', or a branch name."
        )
    return commit_range


def _gather_suggestions(tier: str, commit_range: str) -> dict | None:
    """Run the postmortem, aggregate, and derive review suggestions.

    Returns the suggestion dict (see ``_build_review_suggestions``) or
    ``None`` when there were no recurring detector hits to suggest from.
    """
    tier_meta = _TIERS[tier]
    postmortem = _run_postmortem(commit_range, limit=max(tier_meta["default_count"], 100)) or {}
    commits = postmortem.get("commits") or []
    by_detector = _aggregate_by_detector(commits)
    return _build_review_suggestions(by_detector=by_detector, commits=commits, tier=tier)


@roam_capability(
    name="rules-suggest",
    category="health",
    summary="Suggest .roam/rules.yml rules and CI gates from recurring history findings",
    inputs=["git commit range"],
    outputs=["suggested_roam_rules_yml", "suggested_ci_gates", "verdict"],
    examples=[
        "roam rules-suggest",
        "roam rules-suggest --range v1.0..main",
        "roam --json rules-suggest --tier team",
        "roam rules-suggest --write",
    ],
    tags=["rules", "ci", "history", "advisory"],
    ai_safe=True,
    requires_index=False,
    maturity="beta",
    mcp_expose=True,
    # side_effect is True ONLY because of --write; the default invocation is
    # read-only (runs postmortem over history, prints suggestions).
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("rules-suggest")
@click.option(
    "--tier",
    type=click.Choice(list(_TIERS.keys()), case_sensitive=False),
    default="team",
    show_default=True,
    help=(
        "History window when --range is not given. ``sample`` scans 5 commits, "
        "``team`` 30, ``deep`` 90. --range overrides this."
    ),
)
@click.option(
    "--range",
    "commit_range",
    default=None,
    help=(
        "Explicit git commit range (e.g. ``HEAD~30..HEAD``, ``v1.0..main``). "
        "Overrides the commit count implied by --tier."
    ),
)
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="Write the suggested rules to .roam/rules.yml (refuses to clobber without --force).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Allow --write to overwrite an existing .roam/rules.yml.",
)
@click.pass_context
def rules_suggest(ctx, tier, commit_range, write, force):
    """Suggest ``.roam/rules.yml`` rules and CI gates from recurring findings."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    tier = tier.lower()
    commit_range = _resolve_range(tier, commit_range)
    suggestions = _gather_suggestions(tier, commit_range)

    rules_yaml = suggestions.get("suggested_roam_rules_yml") if suggestions else None
    ci_gates = suggestions.get("suggested_ci_gates") if suggestions else []
    recurring = suggestions.get("recurring_risk_classes") if suggestions else []
    covered = suggestions.get("suggested_rules_cover_detectors") if suggestions else []

    # ------------------------------------------------------------------
    # --write: persist the preview, guarded against clobbering.
    # ------------------------------------------------------------------
    write_result: dict | None = None
    if write:
        write_result = _do_write(rules_yaml, force=force, json_mode=json_mode)
        # In text mode _do_write already echoed its outcome; on refusal / no
        # rules we still fall through to print the (empty) suggestion block
        # in JSON mode so the envelope carries the reason.

    if rules_yaml:
        verdict = (
            f"{len(recurring or [])} recurring detector class(es); {len(covered or [])} covered by a rule template"
        )
    else:
        verdict = "No recurring detector classes with a rule template — no suggestions"

    summary = {
        "verdict": verdict,
        "recurring_class_count": len(recurring or []),
        "covered_detector_count": len(covered or []),
        "ci_gate_count": len(ci_gates or []),
        "range": commit_range,
        "tier": tier,
    }
    if write_result is not None:
        summary["write"] = write_result

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "rules-suggest",
                    summary=summary,
                    suggested_roam_rules_yml=rules_yaml,
                    suggested_ci_gates=ci_gates or [],
                    recurring_risk_classes=recurring or [],
                    suggested_rules_cover_detectors=covered or [],
                    budget=token_budget,
                )
            )
        )
        return

    # ------------------------------------------------------------------
    # Text mode.
    # ------------------------------------------------------------------
    click.echo(f"VERDICT: {verdict}")
    click.echo("")

    if not rules_yaml and not ci_gates:
        click.echo("No suggestions: no detector class recurred with a matching rule template.")
        click.echo(f"(scanned range: {commit_range})")
        return

    click.echo("### Suggested .roam/rules.yml")
    click.echo("")
    if rules_yaml:
        click.echo(rules_yaml.rstrip())
    else:
        click.echo("_No rule templates matched the recurring detectors._")
    click.echo("")

    click.echo("### Suggested CI gates")
    click.echo("")
    if ci_gates:
        for gate in ci_gates[:10]:
            rationale = gate.get("rationale", "")
            if rationale:
                click.echo(f"# {rationale}")
            click.echo(gate.get("gate", ""))
    else:
        click.echo("_No gate suggestions available._")


def _do_write(rules_yaml: str | None, *, force: bool, json_mode: bool) -> dict:
    """Persist ``rules_yaml`` to ``.roam/rules.yml``, guarded against clobber.

    Returns a small result dict describing what happened (``written`` bool +
    ``path`` + ``reason``). In text mode it also echoes a human line.
    """
    root = find_project_root()
    target = root / _RULES_PATH

    if not rules_yaml:
        reason = "no rule templates matched — nothing to write"
        if not json_mode:
            click.echo(f"Not writing {target}: {reason}.")
            click.echo("")
        return {"written": False, "path": str(target), "reason": reason}

    if target.exists() and not force:
        reason = "file exists (pass --force to overwrite)"
        if not json_mode:
            click.echo(f"Not writing {target}: {reason}.")
            click.echo("")
        return {"written": False, "path": str(target), "reason": reason}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rules_yaml, encoding="utf-8")
    if not json_mode:
        click.echo(f"Wrote suggested rules to {target}")
        click.echo("")
    return {"written": True, "path": str(target), "reason": "ok"}
