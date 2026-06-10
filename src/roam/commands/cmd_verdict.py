"""`roam verdict` — closed-enum verdict from a proof bundle (Roam Guard MVP).

SARIF is deliberately NOT emitted: this is a pure judgment layer that
returns a single closed-enum value + machine reasons — SARIF ships from
`roam proof-bundle --format sarif` which has the full file context.

Reads a pr-bundle JSON file (or stdin), computes the verdict via the
closed-enum verdict engine, and emits the verdict + machine-reasons.

Exit codes:
    0 = pass / pass_with_warnings (non-blocking)
    4 = needs_review (human required)
    5 = blocked (hard gate failed)

Per project_pivot_to_roam_guard memo, this is the CI-facing standalone
verdict tool. The same logic is also called inline from pr-bundle emit
to populate the AgentChangeProofBundle's `verdict` field.

Usage:
    roam verdict --bundle .roam/pr-bundles/main.json
    cat .roam/pr-bundles/main.json | roam verdict --
    roam --json verdict --bundle .roam/pr-bundles/main.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.guard_errors import guard_error_envelope
from roam.output.formatter import json_envelope, to_json
from roam.verdict import compute_verdict, verdict_exit_code


def _load_bundle(bundle: str | None) -> dict:
    if bundle in (None, "-"):
        text = sys.stdin.read()
    else:
        text = Path(bundle).read_text()
    return json.loads(text)


def _extract_contract_inputs(bundle: dict) -> dict:
    """Pull verdict inputs from a proof bundle, tolerant to nested shapes."""
    # AgentChangeProofBundle v1 shape (preferred):
    if "verification_contract" in bundle:
        return {
            "verification_contract": bundle.get("verification_contract") or {"required": [], "skipped": []},
            "executed_checks": bundle.get("executed_checks", []),
            "missing_checks": bundle.get("missing_checks", []),
            "optimizer_findings": bundle.get("optimizer_findings", []),
            "scope_findings": bundle.get("scope_findings", []),
            "mcp_tool_findings": bundle.get("mcp_tool_findings", []),
            "risk": bundle.get("risk") or {},
            "ledger": bundle.get("ledger") or {},
        }
    # Legacy pr-bundle shape — best-effort mapping
    body = bundle.get("body") or bundle.get("bundle") or bundle
    return {
        "verification_contract": body.get("verification_contract") or {"required": [], "skipped": []},
        "executed_checks": body.get("tests_run") or body.get("executed_checks") or [],
        "missing_checks": body.get("missing_checks") or [],
        "optimizer_findings": body.get("optimizer_findings") or [],
        "scope_findings": body.get("scope_findings") or [],
        "mcp_tool_findings": body.get("mcp_tool_findings") or [],
        "risk": body.get("risks_considered_block") or body.get("risk") or {},
        "ledger": body.get("ledger") or {},
    }


@click.command(name="verdict")
@click.option("--bundle", "-b", type=str, default=None, help="Path to pr-bundle JSON, or '-' for stdin.")
@click.option("--strict", is_flag=True, default=False, help="Treat pass_with_warnings as non-zero exit (CI gate).")
@click.pass_context
@roam_capability(
    name="verdict",
    category="planning",
    summary="Compute closed-enum verdict (pass/pass_with_warnings/needs_review/blocked)",
    inputs=("pr_bundle",),
    outputs=("verdict",),
    examples=(
        "roam verdict --bundle .roam/pr-bundles/main.json",
        "roam --json verdict --bundle bundle.json --strict",
    ),
    tags=("planning", "proof-bundle", "ci", "verdict"),
)
def verdict(ctx: click.Context, bundle: str | None, strict: bool) -> None:
    """Compute the closed-enum verdict for a proof bundle."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    try:
        bundle_dict = _load_bundle(bundle)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        code = "bundle_load_failed" if isinstance(e, FileNotFoundError) else "bundle_parse_error"
        msg = "failed to load bundle"
        fix = "Check the --bundle path; or omit --bundle and pipe via stdin (use '-')."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "verdict",
                        code,
                        msg,
                        fix=fix,
                        context={"bundle_arg": bundle, "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"{msg}: {e}", err=True)
        ctx.exit(2)
        return

    inputs = _extract_contract_inputs(bundle_dict)
    result = compute_verdict(**inputs)
    exit_code = verdict_exit_code(result["value"])

    if strict and result["value"] == "pass_with_warnings":
        exit_code = 4

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "verdict",
                    summary={
                        "verdict": result["value"],
                        "reason_count": len(result["reasons"]),
                        "exit_code": exit_code,
                        "partial_success": False,
                    },
                    agent_contract={
                        "facts": [
                            f"verdict: {result['value']}",
                            f"{len(result['reasons'])} reason objects",
                            f"exit code {exit_code}",
                        ],
                        "next_commands": [
                            "roam pr-bundle emit",
                        ],
                        "risks": [
                            r for r in result["reasons"] if r["code"] in {"required_check_failed", "high_risk_path"}
                        ],
                    },
                    verdict=result,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {result['value']}")
        for r in result["reasons"][:10]:
            click.echo(f"  - {r['code']}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "code"))
        if len(result["reasons"]) > 10:
            click.echo(f"  ... and {len(result['reasons']) - 10} more")

    ctx.exit(exit_code)
