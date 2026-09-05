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

import json
import math
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.guard_errors import guard_error_envelope
from roam.output.formatter import json_envelope, to_json
from roam.proof_bundle import _extract_executed_checks
from roam.verdict import compute_verdict, verdict_exit_code


def _load_bundle(bundle: str | None) -> dict:
    if bundle in (None, "-"):
        text = sys.stdin.read()
    else:
        text = Path(bundle).read_text(encoding="utf-8")
    parsed = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )
    _validate_input_shape(parsed)
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Refuse ambiguous JSON before last-key-wins can erase a blocker."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("bundle contains a duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("bundle numbers must be finite JSON numbers")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _invalid_constant(value)
    return parsed


def _validate_input_shape(bundle: object) -> None:
    """Validate consumed field types without requiring modern bundle metadata.

    Missing/null optional fields retain legacy meaning. Present wrong-shaped
    evidence is not absence: refuse it before the tolerant mapper drops it.
    This is input validation, not signature, freshness, or policy verification.
    """
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a JSON object")
    for key in ("body", "bundle"):
        if bundle.get(key) is not None:
            _validate_input_shape(bundle[key])
    mapping_keys = (
        "verification_contract",
        "review_evidence",
        "orchestration_contract",
        "risk",
        "risks_considered_block",
        "ledger",
    )
    for key in mapping_keys:
        if bundle.get(key) is not None and not isinstance(bundle[key], dict):
            raise ValueError(f"{key} must be an object or null")
    for key in ("change_set_unanalyzable",):
        if bundle.get(key) is not None and not isinstance(bundle[key], str):
            raise ValueError(f"{key} must be a string or null")
    _validate_record_lists(
        bundle,
        (
            "executed_checks",
            "missing_checks",
            "optimizer_findings",
            "scope_findings",
            "mcp_tool_findings",
            "tests_run",
        ),
    )
    _validate_contract_shape(bundle)
    _validate_evidence_state(bundle)


def _validate_contract_shape(bundle: dict) -> None:
    """Check contract containers before reading obligations or path metadata."""
    contract = bundle.get("verification_contract") or {}
    if "required" in contract and not isinstance(contract["required"], list):
        raise ValueError("verification_contract.required must be a list")
    _validate_record_lists(contract, ("required",))
    orchestration = bundle.get("orchestration_contract") or {}
    if "obligations" in orchestration and not isinstance(orchestration["obligations"], list):
        raise ValueError("orchestration_contract.obligations must be a list")
    meta = contract.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("verification_contract._meta must be an object")
        if meta.get("rule_pack") is not None and not isinstance(meta["rule_pack"], dict):
            raise ValueError("verification_contract._meta.rule_pack must be an object")
    for record in (bundle, meta or {}, bundle.get("risk") or {}, bundle.get("risks_considered_block") or {}):
        _validate_path_lists(record)


def _validate_path_lists(record: dict) -> None:
    for key in ("changed_files", "unmatched_changed_files", "paths"):
        value = record.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            raise ValueError(f"{key} must be a list of strings")


def _validate_evidence_state(bundle: dict) -> None:
    """Refuse malformed state without changing which valid statuses block."""
    verified = (bundle.get("ledger") or {}).get("verified")
    if verified is not None and not isinstance(verified, bool):
        raise ValueError("ledger.verified must be a boolean or null")
    for result in (bundle.get("review_evidence") or {}).values():
        if result is not None and not isinstance(result, dict):
            raise ValueError("review_evidence entries must be objects or null")
        if isinstance(result, dict) and "status" in result and not isinstance(result["status"], str):
            raise ValueError("review_evidence status must be a string")


def _validate_record_lists(source: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        records = source.get(key)
        if records is None:
            continue
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError(f"{key} must be a list of objects or null")
        for record in records:
            _validate_check_record(record, key)


def _validate_check_record(record: dict, key: str) -> None:
    if key in ("required", "executed_checks", "tests_run"):
        command = record.get("command")
        if key == "tests_run":
            command = command or record.get("name") or record.get("test")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{key} records must name a non-empty command")
    fields = ("command", "status", "name", "test", "result") if key == "tests_run" else ("command", "status")
    for field in fields:
        if record.get(field) is not None and not isinstance(record[field], str):
            raise ValueError(f"{key}.{field} must be a string or null")


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
