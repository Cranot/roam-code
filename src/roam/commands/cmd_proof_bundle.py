"""`roam proof-bundle` — compose AgentChangeProofBundle v1 from a pr-bundle.

SARIF is deliberately NOT wired to the global --sarif flag: this command
ships its own native `--format sarif` flag (see verdict_to_sarif in
proof_bundle_render.py) so reviewers can pick SARIF alongside markdown
and text without the global flag's text-vs-JSON polysemy.

The Item-3 deliverable for Roam Guard MVP Phase 1. Reads a legacy pr-bundle
file (`.roam/pr-bundles/<branch>.json`) and emits the v1 schema with
G2 command_graph + G3 verification_contract + closed-enum verdict populated.

Keeps `roam pr-bundle emit` untouched (years of W-series audits) — ships the
v1 schema as a sibling artifact.

Usage:
    roam proof-bundle                              # use current branch's bundle
    roam proof-bundle --bundle path/to/bundle.json
    roam --json proof-bundle --mode autonomous_pr --policy-profile regulated
    roam --json proof-bundle --strict              # exit non-zero on blocked verdict
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.guard_errors import guard_error_envelope
from roam.output.formatter import json_envelope, to_json
from roam.proof_bundle import (
    PROOF_BUNDLE_SCHEMA,
    PROOF_BUNDLE_SCHEMA_VERSION,
    compose_agent_change_proof_bundle,
    load_pr_bundle,
    render_markdown,
    validate_v1,
    verdict_to_sarif,
)
from roam.verdict import verdict_exit_code


def _resolve_bundle_path(bundle_arg: str | None) -> Path | None:
    """Delegate to the canonical helper in pr_bundle_primitives."""
    from roam.pr_bundle_primitives import discover_active_bundle

    root = find_project_root()
    return discover_active_bundle(
        Path(root) if root else None,
        bundle_arg,
    )


@click.command(name="proof-bundle")
@click.option(
    "--bundle", "-b", type=str, default=None, help="Path to pr-bundle JSON. Defaults to current branch's bundle."
)
@click.option(
    "--mode",
    type=click.Choice(["read_only", "safe_edit", "migration", "autonomous_pr"]),
    default=None,
    help="Override agent mode (else read from bundle / safe_edit fallback).",
)
@click.option(
    "--policy-profile",
    type=click.Choice(["startup", "regulated"]),
    default="startup",
    help="Policy floor (regulated = tests on every change).",
)
@click.option(
    "--strict", is_flag=True, default=False, help="Exit non-zero on blocked / needs_review verdict (CI gate)."
)
@click.option("--output", "-o", type=str, default=None, help="Write the v1 bundle to this path instead of stdout.")
@click.option(
    "--validate",
    "do_validate",
    is_flag=True,
    default=False,
    help="Validate the composed v1 bundle against the JSON Schema. "
    "Errors surface in the envelope (and exit non-zero if --strict).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "markdown", "json", "sarif"]),
    default=None,
    help="Output format. Defaults to text (or json with --json). "
    "'markdown' for PR-comment / GH-Check, 'sarif' for "
    "GitHub Code Scanning / GitLab SAST ingestion.",
)
@click.pass_context
@roam_capability(
    name="proof-bundle",
    category="planning",
    summary="Compose AgentChangeProofBundle v1 (G2 + G3 + verdict) from a pr-bundle",
    inputs=("pr_bundle",),
    outputs=("agent_change_proof_bundle",),
    examples=(
        "roam proof-bundle",
        "roam proof-bundle --bundle .roam/pr-bundles/main.json",
        "roam --json proof-bundle --mode autonomous_pr --strict",
    ),
    tags=("planning", "proof-bundle", "g3", "verdict", "roam-guard"),
)
def proof_bundle(
    ctx: click.Context,
    bundle: str | None,
    mode: str | None,
    policy_profile: str,
    strict: bool,
    output: str | None,
    do_validate: bool,
    fmt: str | None,
) -> None:
    """Compose and emit the AgentChangeProofBundle v1 from a pr-bundle."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    bundle_path = _resolve_bundle_path(bundle)
    if bundle_path is None or not bundle_path.is_file():
        msg = f"No pr-bundle found at {bundle_path}."
        fix = "Run `roam pr-bundle init` first or pass --bundle <path>."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "proof-bundle",
                        "no_bundle_found",
                        msg,
                        fix=fix,
                        context={"bundle_arg": bundle, "resolved_path": str(bundle_path) if bundle_path else None},
                    )
                )
            )
        else:
            click.echo(f"{msg} {fix}", err=True)
        ctx.exit(2)
        return

    try:
        bundle_dict = load_pr_bundle(bundle_path)
    except (ValueError, json.JSONDecodeError) as e:
        msg = f"Failed to parse pr-bundle at {bundle_path}"
        fix = f"Inspect / repair the JSON at {bundle_path}."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "proof-bundle",
                        "bundle_parse_error",
                        msg,
                        fix=fix,
                        context={"bundle_path": str(bundle_path), "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"{msg}: {e}", err=True)
        ctx.exit(2)
        return

    root = find_project_root() or Path.cwd()
    v1 = compose_agent_change_proof_bundle(
        bundle_dict,
        repo_root=Path(root),
        mode=mode,
        policy_profile=policy_profile,
    )

    verdict_val = (v1.get("verdict") or {}).get("value", "pass")
    exit_code = verdict_exit_code(verdict_val) if strict else 0

    schema_errors: list[str] = []
    if do_validate:
        schema_errors = validate_v1(v1)
        if schema_errors and strict:
            exit_code = max(exit_code, 5)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "markdown":
            out_path.write_text(render_markdown(v1))
        elif fmt == "sarif":
            out_path.write_text(json.dumps(verdict_to_sarif(v1), indent=2))
        else:
            out_path.write_text(json.dumps(v1, indent=2, default=str))

    # Explicit --format markdown short-circuits JSON / text branches.
    if fmt == "markdown":
        click.echo(render_markdown(v1))
        ctx.exit(exit_code)
        return
    if fmt == "sarif":
        click.echo(json.dumps(verdict_to_sarif(v1), indent=2))
        ctx.exit(exit_code)
        return

    if json_mode:
        n_required = len(v1["verification_contract"]["required"])
        n_executed = len(v1["executed_checks"])
        n_missing = len(v1["missing_checks"])
        click.echo(
            to_json(
                json_envelope(
                    "proof-bundle",
                    summary={
                        "verdict": f"{verdict_val} ({n_executed}/{n_required} checks)",
                        "verdict_value": verdict_val,
                        "schema": PROOF_BUNDLE_SCHEMA,
                        "schema_version": PROOF_BUNDLE_SCHEMA_VERSION,
                        "required_count": n_required,
                        "executed_count": n_executed,
                        "missing_count": n_missing,
                        "exit_code": exit_code,
                        "partial_success": verdict_val in ("blocked", "needs_review"),
                    },
                    agent_contract={
                        "facts": [
                            f"verdict {verdict_val}",
                            f"{n_required} checks required",
                            f"{n_executed} checks executed",
                            f"{n_missing} checks missing",
                        ],
                        "next_commands": [
                            "roam verdict --bundle <path>",
                            "roam pr-bundle emit",
                        ],
                        "risks": [
                            r
                            for r in v1["verdict"]["reasons"]
                            if r.get("code")
                            in {
                                "required_check_failed",
                                "required_check_not_run",
                                "high_risk_path",
                            }
                        ],
                    },
                    agent_change_proof_bundle=v1,
                    schema_errors=schema_errors,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict_val}")
        click.echo(f"  bundle: {bundle_path}")
        click.echo(f"  changed_files: {len(v1['changed_files'])}")
        click.echo(f"  required checks: {len(v1['verification_contract']['required'])}")
        click.echo(f"  executed checks: {len(v1['executed_checks'])}")
        click.echo(f"  missing checks: {len(v1['missing_checks'])}")
        if output:
            click.echo(f"  v1 bundle written: {output}")
        for r in v1["verdict"]["reasons"][:5]:
            click.echo(f"  reason: {r['code']}")

    ctx.exit(exit_code)
