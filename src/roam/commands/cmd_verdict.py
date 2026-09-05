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

Per the Roam Guard pivot decision, this is the CI-facing standalone
verdict tool. The same logic is also called inline from pr-bundle emit
to populate the AgentChangeProofBundle's `verdict` field.

Usage:
    roam verdict --bundle .roam/pr-bundles/main.json
    cat .roam/pr-bundles/main.json | roam verdict --
    roam --json verdict --bundle .roam/pr-bundles/main.json
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.guard_errors import guard_error_envelope
from roam.output.formatter import json_envelope, to_json
from roam.proof_bundle import _extract_executed_checks
from roam.proof_input import parse_proof_json
from roam.verdict import compute_verdict, verdict_exit_code


def _load_bundle(bundle: str | None) -> dict:
    if bundle in (None, "-"):
        text = sys.stdin.read()
    else:
        text = Path(bundle).read_text(encoding="utf-8")
    return parse_proof_json(text)


def _dict_or_none(value: object) -> dict | None:
    """Forward a mapping, or None. Mirrors proof_bundle.py's own type guard.

    ``compute_verdict`` distinguishes "no review evidence was supplied" (None,
    legacy path) from "evidence was supplied and is empty" ({}), so a
    non-mapping must become None, not {}.
    """
    return value if isinstance(value, dict) else None


def _extract_contract_inputs(bundle: dict) -> dict:
    """Pull verdict inputs from a proof bundle, tolerant to nested shapes.

    ``compute_verdict`` takes 11 inputs. This function used to return 8 on
    BOTH branches, so ``review_evidence``, ``orchestration_contract`` and
    ``change_set_unanalyzable`` defaulted to None and every review-obligation
    blocker plus the unanalyzable-change-set blocker was UNREACHABLE from
    ``roam verdict`` -- measured: the same bundle `guard-pr` called blocked at
    exit 5, including one whose reviews were explicitly ``rejected``, this
    command called pass at exit 0, and ``--strict`` did not help because the
    blockers were never computed rather than suppressed.
    """
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
            "review_evidence": _dict_or_none(bundle.get("review_evidence")),
            "orchestration_contract": _dict_or_none(bundle.get("orchestration_contract")),
            # Persisted by compose_agent_change_proof_bundle. It cannot be
            # recomputed from a static file -- it is the record of whether git
            # ANSWERED at emit time -- so it is read back, never inferred.
            "change_set_unanalyzable": (
                bundle.get("change_set_unanalyzable")
                if isinstance(bundle.get("change_set_unanalyzable"), str)
                else None
            ),
        }
    # Legacy pr-bundle shape — best-effort mapping
    body = bundle.get("body") or bundle.get("bundle") or bundle
    # W1447 — `tests_run` records go through the SAME normaliser
    # `build_proof_bundle` uses, so a status-less record becomes
    # "unverified" here exactly as it does there. Reading `tests_run` raw
    # meant this entry point and the bundle builder disagreed about the
    # same input: W1441 hardened the normaliser, and this path skipped it.
    return {
        "verification_contract": body.get("verification_contract") or {"required": [], "skipped": []},
        "executed_checks": (
            _extract_executed_checks(body) if body.get("tests_run") else body.get("executed_checks") or []
        ),
        "missing_checks": body.get("missing_checks") or [],
        "optimizer_findings": body.get("optimizer_findings") or [],
        "scope_findings": body.get("scope_findings") or [],
        "mcp_tool_findings": body.get("mcp_tool_findings") or [],
        "risk": body.get("risks_considered_block") or body.get("risk") or {},
        "ledger": body.get("ledger") or {},
        "review_evidence": _dict_or_none(body.get("review_evidence")),
        "orchestration_contract": _dict_or_none(body.get("orchestration_contract")),
        # DELIBERATELY not inferred. "the bundle lists no changed files" and
        # "the change set was never measured" are different facts, and a
        # legacy bundle does not distinguish them. Manufacturing a blocker
        # here would exit 5 on every `cat bundle | roam verdict --` over the
        # many legacy bundles that never carried a file list. The gap is
        # published as `scan_incomplete` instead -- see `_scan_incomplete`.
        "change_set_unanalyzable": None,
    }


# Keys any bundle shape may use to declare its change set. Present-and-empty
# still counts as DECLARED: a bundle that says "no files changed" measured
# something. Absent entirely is what cannot be told apart from unmeasured.
_CHANGE_SET_KEYS = ("changed_files", "files_touched", "affected_symbols")


def _scan_incomplete(bundle: dict, inputs: dict) -> bool:
    """True when this file cannot say whether the change set was measured.

    Not a blocker and not an exit-code change: the verdict engine already
    refuses on a KNOWN-unanalyzable change set (``change_set_unanalyzable``).
    This flag covers the weaker case -- a bundle that carries neither a change
    set nor the provenance field, so "declared empty" and "never measured"
    are indistinguishable. Saying UNKNOWN in the envelope is the honest
    answer; inventing a blocker would be an outage.
    """
    if inputs.get("change_set_unanalyzable"):
        return False
    body = bundle.get("body") or bundle.get("bundle") or bundle
    for source in (bundle, body):
        if not isinstance(source, dict):
            continue
        if "change_set_unanalyzable" in source:
            return False
        if any(key in source for key in _CHANGE_SET_KEYS):
            return False
    return True


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
    except (OSError, ValueError, RecursionError) as e:
        code = "bundle_load_failed" if isinstance(e, OSError) else "bundle_parse_error"
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
    scan_incomplete = _scan_incomplete(bundle_dict, inputs)
    try:
        result = compute_verdict(**inputs)
    except ValueError as e:
        # compute_verdict RAISES on a review status outside its closed enum --
        # deliberately, so a new failure mode cannot pass silently. Forwarding
        # review_evidence makes that reachable from CI, and a traceback out of
        # a CI job is not a verdict. Refuse in the published vocabulary.
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "verdict",
                        "unmapped_review_status",
                        "the bundle carries a review status this build cannot classify",
                        fix="Upgrade roam, or correct the review_evidence status to a known value.",
                        context={"bundle_arg": bundle, "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"verdict could not be computed: {e}", err=True)
        ctx.exit(2)
        return

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
                        # Was hardcoded False -- a positive claim of
                        # completeness from an envelope that had dropped three
                        # of compute_verdict's eleven inputs. Same vocabulary
                        # `guard-pr` already publishes.
                        "scan_incomplete": scan_incomplete,
                        "partial_success": scan_incomplete,
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
        if scan_incomplete:
            click.echo(
                "  NOTE: this bundle declares no change set and no "
                "change-set provenance -- 'nothing changed' and 'never "
                "measured' cannot be told apart from this file."
            )
        for r in result["reasons"][:10]:
            click.echo(f"  - {r['code']}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "code"))
        if len(result["reasons"]) > 10:
            click.echo(f"  ... and {len(result['reasons']) - 10} more")

    ctx.exit(exit_code)
