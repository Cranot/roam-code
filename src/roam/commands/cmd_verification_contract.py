"""`roam verification-contract` — emit the G3 contract for THIS diff.

SARIF is deliberately NOT emitted: output is the {required, skipped}
verification contract (what to RUN), not findings about code. SARIF
surfaces verdicts on what ran — that ships from `roam proof-bundle
--format sarif`.

The judgment layer that consumes G2's command_graph and produces
{required: [...], skipped: [...]} based on changed_files × risk × mode × policy.

Per the Roam Guard pivot decision, this is the gap module between
`roam commands` (G2) and `pr-bundle emit` (proof bundle).

Output formats: ``--json`` (default), text.

Usage:
    roam verification-contract --files src/auth/session.py src/auth/login.py
    roam verification-contract --files-from changed.txt --mode autonomous_pr
    roam --json verification-contract --files src/billing/charge.py --risk-level high
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.command_graph import build_command_graph
from roam.exit_codes import EXIT_GATE_FAILURE, gate_should_fail
from roam.guard_errors import guard_error_envelope
from roam.guard_rules import get_active_rules
from roam.output.formatter import json_envelope, to_json
from roam.verification_contract import build_verification_contract


def _resolve_files(files: tuple[str, ...], files_from: str | None) -> list[str]:
    out: list[str] = list(files)
    if files_from:
        path = Path(files_from)
        if path.is_file():
            out.extend(
                line.strip()
                for line in path.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
    return out


@click.command(name="verification-contract")
@click.option("--files", "-f", multiple=True, help="Changed file paths (repeatable).")
@click.option(
    "--files-from", type=click.Path(exists=True, dir_okay=False), help="Read changed files from a file (one per line)."
)
@click.option(
    "--mode",
    type=click.Choice(["read_only", "safe_edit", "migration", "autonomous_pr"]),
    default="safe_edit",
    help="Agent operating mode.",
)
@click.option(
    "--policy-profile",
    type=click.Choice(["startup", "regulated"]),
    default="startup",
    help="Policy profile (regulated = test floor on every change).",
)
@click.option(
    "--risk-level", type=click.Choice(["low", "medium", "high"]), default="low", help="Risk level for this change."
)
@click.option(
    "--risk-path", multiple=True, help="High-risk path (repeatable). Required when --risk-level=high to be specific."
)
@click.option("--risk-reason", multiple=True, help="Risk reason (repeatable, optional).")
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a custom rule pack (YAML). Default: built-in pack.",
)
@click.option(
    "--require-coverage",
    "require_coverage",
    is_flag=True,
    default=False,
    help="Exit 5 when the contract is empty because no rule matched any changed file.",
)
@click.pass_context
@roam_capability(
    name="verification-contract",
    category="planning",
    summary="Emit verification contract for THIS diff — required vs skipped checks",
    inputs=("changed_files", "mode", "policy_profile", "risk"),
    outputs=("verification_contract",),
    examples=(
        "roam verification-contract --files src/auth/session.py",
        "roam --json verification-contract --files-from .diff.txt --mode autonomous_pr --policy-profile regulated",
    ),
    tags=("planning", "proof-bundle", "g3"),
)
def verification_contract(
    ctx: click.Context,
    files: tuple[str, ...],
    files_from: str | None,
    mode: str,
    policy_profile: str,
    risk_level: str,
    risk_path: tuple[str, ...],
    risk_reason: tuple[str, ...],
    rules_path: str | None,
    require_coverage: bool,
) -> None:
    """Emit the G3 verification contract for changed files + mode + policy."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    changed_files = _resolve_files(files, files_from)
    if not changed_files:
        msg = "No changed files provided."
        fix = "Pass --files <path> (repeatable) or --files-from <file>."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "verification-contract",
                        "no_input_files",
                        msg,
                        fix=fix,
                    )
                )
            )
        else:
            click.echo(f"{msg} {fix}")
        ctx.exit(2)

    graph = build_command_graph()
    try:
        rule_pack = get_active_rules(rules_path)
    except ValueError as e:
        msg = f"Rule pack at {rules_path} is invalid"
        fix = "Run `roam guard-rules validate <path>` for details."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "verification-contract",
                        "rule_pack_invalid",
                        msg,
                        fix=fix,
                        context={"rules_path": rules_path, "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"{msg}: {e}", err=True)
        ctx.exit(2)
        return

    contract = build_verification_contract(
        changed_files=changed_files,
        command_graph=graph,
        risk={"level": risk_level, "paths": list(risk_path), "reasons": list(risk_reason)},
        mode=mode,
        policy_profile=policy_profile,
        rule_pack=rule_pack,
    )

    n_required = len(contract["required"])
    n_skipped = len(contract["skipped"])
    unmatched = list(contract["_meta"]["unmatched_changed_files"])
    n_unmatched = len(unmatched)
    # An empty contract over files no rule matched is not "nothing to check":
    # the pack has no opinion about these paths. Report it as partial.
    vacuous = n_required == 0 and n_unmatched > 0
    if vacuous:
        verdict = f"0 required; {n_unmatched} changed file(s) matched no rule"
    else:
        verdict = f"{n_required} required, {n_skipped} skipped"
    fail_coverage = gate_should_fail(require_coverage, findings=False, scan_incomplete=vacuous)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "verification-contract",
                    summary={
                        "verdict": verdict,
                        "required_count": n_required,
                        "skipped_count": n_skipped,
                        "changed_files_count": len(changed_files),
                        "mode": mode,
                        "policy_profile": policy_profile,
                        "risk_level": risk_level,
                        "unmatched_count": n_unmatched,
                        "partial_success": vacuous,
                    },
                    agent_contract={
                        "facts": [
                            f"{n_required} checks required",
                            f"{n_skipped} checks skipped",
                            f"mode {mode}",
                            f"policy {policy_profile}",
                        ],
                        "next_commands": [
                            "roam pr-bundle emit --auto-collect",
                            "roam verdict",
                        ],
                        "risks": [f"{n_unmatched} changed file(s) matched no rule"] if vacuous else [],
                    },
                    contract=contract,
                )
            )
        )
        if fail_coverage:
            ctx.exit(EXIT_GATE_FAILURE)
        return

    click.echo(f"VERDICT: {verdict} (mode={mode}, policy={policy_profile})")
    if vacuous:
        click.echo("\nChanged files matched by no rule:")
        for f in unmatched[:10]:
            click.echo(f"  - {f}")
        if n_unmatched > 10:
            click.echo(f"  ... and {n_unmatched - 10} more")
    if contract["required"]:
        click.echo("\nRequired checks:")
        for r in contract["required"]:
            click.echo(f"  - {r['command']} ({r['kind']}) — {r['reason']}")
    if contract["skipped"]:
        click.echo(f"\nSkipped ({len(contract['skipped'])}):")
        for r in contract["skipped"][:10]:
            click.echo(f"  - {r['command']} — {r['reason']}")
        if len(contract["skipped"]) > 10:
            click.echo(f"  ... and {len(contract['skipped']) - 10} more")
    if fail_coverage:
        ctx.exit(EXIT_GATE_FAILURE)
