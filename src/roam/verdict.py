"""verdict — closed-enum verdict engine for AgentChangeProofBundle.

Per the proof-bundle schema:

Closed verdict enum, every value machine-reason-backed:

  pass               — in scope, required checks ran + passed, no warnings
  pass_with_warnings — passed but optimizer/quality warnings
  needs_review       — touched high-risk path / human judgment needed
  blocked            — a hard gate failed

Precedence (most-severe wins): blocked > needs_review > pass_with_warnings > pass.

Reasons are objects `{code, ...context}` — NEVER prose-only — so CI/dashboards
can act on them programmatically.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Centralized closed enums — single source of truth lives in guard_enums.
# `VERDICTS` is re-exported here (explicit `as` alias) for external consumers
# that import it from `roam.verdict`. `CHECK_STATUSES` is the closed set the
# satisfaction allowlist and `proof_bundle.validate_v1` must both agree with.
from roam.guard_enums import (
    CHECK_STATUSES,
    exit_code_for,
)
from roam.guard_enums import (
    VERDICTS as VERDICTS,
)


def compute_verdict(
    *,
    verification_contract: dict[str, Any],
    executed_checks: list[dict[str, Any]] | None = None,
    missing_checks: list[dict[str, Any]] | None = None,
    optimizer_findings: list[dict[str, Any]] | None = None,
    scope_findings: list[dict[str, Any]] | None = None,
    mcp_tool_findings: list[dict[str, Any]] | None = None,
    risk: dict[str, Any] | None = None,
    ledger: dict[str, Any] | None = None,
    review_evidence: dict[str, Any] | None = None,
    orchestration_contract: dict[str, Any] | None = None,
    change_set_unanalyzable: str | None = None,
) -> dict[str, Any]:
    """Compute the proof-bundle verdict from collected evidence.

    ``review_evidence`` maps a review phase (``1b_plan_critique`` /
    ``4b_done_verdict``) to the OUTPUT of
    :func:`roam.review_receipt.verify_receipt` -- never to fields an agent
    copied out of its own receipt. Absent evidence for a required phase is
    a blocker, not a silent pass.

    ``change_set_unanalyzable`` carries the reason the CHANGE SET itself could
    not be determined (git refused, timed out, or is not present). Every other
    input to this function is computed FROM the change set, so when it is
    unknown the whole proof is unfounded: an empty ``required`` list makes
    "all required checks passed" vacuously true, and the caller receives a
    pass it can not distinguish from a real one. It is therefore a hard gate,
    ranked with the other blockers that invalidate the proof.

    Returns:
      {"value": "pass|pass_with_warnings|needs_review|blocked",
       "reasons": [{"code": str, ...context}, ...]}
    """
    executed_checks = executed_checks or []
    optimizer_findings = optimizer_findings or []
    scope_findings = scope_findings or []
    mcp_tool_findings = mcp_tool_findings or []
    risk = risk or {}

    return _select_verdict_that_preserves_gate_precedence(
        (
            (
                "blocked",
                lambda: (
                    _collect_unanalyzable_change_set_blocker(change_set_unanalyzable)
                    + _collect_blockers_that_invalidate_proof(
                        verification_contract=verification_contract,
                        executed_checks=executed_checks,
                        mcp_tool_findings=mcp_tool_findings,
                        ledger=ledger,
                    )
                    + _collect_review_obligation_blockers(
                        risk=risk,
                        review_evidence=review_evidence,
                        orchestration_contract=orchestration_contract,
                    )
                ),
            ),
            (
                "needs_review",
                lambda: _collect_review_gates_that_preserve_human_judgment(
                    risk=risk,
                    scope_findings=scope_findings,
                ),
            ),
            (
                "pass_with_warnings",
                lambda: (
                    _collect_warnings_that_keep_proof_passable(
                        optimizer_findings=optimizer_findings,
                        scope_findings=scope_findings,
                        mcp_tool_findings=mcp_tool_findings,
                    )
                    + _collect_review_coverage_warnings(review_evidence=review_evidence)
                ),
            ),
        ),
        pass_reasons=_pass_reasons_for_contract(verification_contract),
    )


# Tags that force cross-family review regardless of the assessed level.
REVIEW_REQUIRED_TAGS: frozenset[str] = frozenset(
    {"security", "auth", "credentials", "migration", "external_api", "irreversible"}
)

# TOTAL mapping: every status the verifier can return has exactly one
# blocker code, and no two statuses share one. A status with no entry
# raises rather than passing silently, so a new failure mode can never
# become a quiet green.
# W1445 — ``same_family`` is deliberately ABSENT from this map. Measured
# 2026-08-02 (pre-registered, blind-judged, n=3/arm on one design with known
# ground truth): a same-family review found the decisive architectural defect
# 3/3 -- the same rate as cross-family -- so refusing it rejects a review that
# demonstrably works. It IS weaker on one class (normalization-collision and
# duplicate-key parsing: same-family 0/3, cross-family 2/3), which is why the
# obligation still PREFERS a different family and the status is surfaced as a
# warning. Evidence strength, stated so it can be re-opened: n=3 per arm, one
# artifact, one judge. See docs/design/PREREGISTRATION + the E1 result.
_REVIEW_STATUS_BLOCKERS: dict[str, str] = {
    "receipt_missing": "review_receipt_missing",
    "receipt_malformed": "review_receipt_malformed",
    "wrong_phase": "review_wrong_phase",
    "artifact_stale": "review_artifact_stale",
    "family_unresolved": "review_family_unresolved",
    "rejected": "review_rejected",
    "review_error": "review_errored",
}

# Statuses that are valid outcomes rather than gate failures.
# ``same_family`` is here on measured evidence (W1445), not by default.
_NON_BLOCKING_STATUSES: frozenset[str] = frozenset({"declared_accepted", "same_family"})

# The phase was never attempted at all -- distinct from "a receipt exists
# but is missing/unreadable", which is `review_receipt_missing`.
_PHASE_ABSENT_BLOCKER: dict[str, str] = {
    "1b_plan_critique": "plan_critique_not_run",
    "4b_done_verdict": "done_verdict_not_run",
}


def review_required(risk: dict[str, Any] | None) -> bool:
    """Whether cross-family review is required for this change.

    Exact predicate, no prose: required unless the risk assessment is
    COMPLETE **and** low **and** carries no review-forcing tag. An absent,
    partial, or MALFORMED assessment is therefore not "low risk" -- it is
    unknown, which fails closed. (The shipped default elsewhere is
    ``{"level": "low"}`` when risk is absent, which would otherwise let a
    change with no assessment at all skip review.)

    Disclosed limit: the risk record is supplied by the same agent whose
    work is being judged, so the EXEMPTION is agent-influenceable. That is
    precisely why every ambiguity resolves toward "required" -- an agent
    must make a positive, complete, low-risk, untagged claim to be exempt,
    and that claim is recorded in the bundle where it can be audited.
    """
    if not isinstance(risk, dict):
        return True
    if risk.get("assessment_status") != "complete":
        return True
    if risk.get("level") not in ("low", "medium", "high"):
        return True  # unknown level is not "low"
    if risk.get("level") in ("medium", "high"):
        return True
    tags = risk.get("tags", [])
    # A bare string here would iterate into characters and silently miss
    # every required tag: shape errors force review rather than skipping it.
    if not isinstance(tags, (list, tuple, set, frozenset)):
        return True
    return bool(REVIEW_REQUIRED_TAGS & {str(t) for t in tags})


def obligations_declared(orchestration_contract: dict[str, Any] | None) -> bool:
    """True when an envelope declared 1b/4b review obligations.

    This is the gate's trigger, NOT a caller flag: an agent that simply
    omits ``review_evidence`` must not thereby skip the gate its own
    envelope declared. Where the contract is present, absence of evidence
    is a blocker.
    """
    if not isinstance(orchestration_contract, dict):
        return False
    return bool(orchestration_contract.get("obligations"))


def _collect_review_obligation_blockers(
    *,
    risk: dict[str, Any] | None,
    review_evidence: dict[str, Any] | None,
    orchestration_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Block when a required cross-family review is absent or negative.

    The gate is ACTIVE when EITHER the work declared review obligations
    (``orchestration_contract`` carries them -- the envelope's own demand,
    which the caller cannot waive by passing nothing) OR the caller opted
    in explicitly by passing ``review_evidence``. Work that declares no
    obligations and whose caller passes nothing keeps its legacy verdict:
    every proof-bundle path shipped before this gate existed.

    DISCLOSED LIMIT: the contract reaches this function through the same
    bundle the agent authors, so an agent that strips the contract from
    its own bundle escapes the gate. That is the general shape of
    self-reported evidence and is not closable inside a local library --
    it needs a component outside the agent's write authority (CI
    recomputing the contract from the envelope, or a signing authority).
    Naming it here keeps the gate from reading as stronger than it is.
    """
    if review_evidence is None and not obligations_declared(orchestration_contract):
        return []
    if not review_required(risk):
        return []
    review_evidence = review_evidence or {}
    reasons: list[dict[str, Any]] = []
    for phase, absent_code in _PHASE_ABSENT_BLOCKER.items():
        result = review_evidence.get(phase)
        if not isinstance(result, dict) or "status" not in result:
            reasons.append(
                {
                    "code": absent_code,
                    "phase": phase,
                    "because": "risk assessment requires cross-family review",
                    "detail": (
                        "no verified review result for this phase; results come from "
                        "roam.review_receipt.verify_receipt, never from an assertion"
                    ),
                    "suggested_command": (f"roam compile --json <task>  # see orchestration_contract, phase {phase}"),
                }
            )
            continue
        status = result["status"]
        # Statuses that do not block. Enumerated explicitly rather than
        # falling through, so the totality check below still fires for any
        # genuinely new status: "not a blocker" must be a decision on the
        # record, never an omission.
        if status in _NON_BLOCKING_STATUSES:
            continue
        code = _REVIEW_STATUS_BLOCKERS.get(status)
        if code is None:
            raise ValueError(
                f"unmapped review status {status!r} — every non-accepted status must "
                "map to exactly one blocker so a new failure mode cannot pass silently"
            )
        reasons.append(
            {
                "code": code,
                "phase": phase,
                "status": status,
                "detail": result.get("reason"),
                "suggested_command": f"re-run the {phase} review against the current artifact",
            }
        )
    return reasons


def _select_verdict_that_preserves_gate_precedence(
    reason_collectors: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...],
    pass_reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the first verdict tier with evidence, preserving hard-gate order.

    ``pass_reasons`` is the reason list for the no-evidence tier. It is a
    parameter rather than a literal because "everything required passed" and
    "nothing was required, because no rule matched anything you changed" are
    different facts that used to print the same sentence.
    """
    for value, collect_reasons in reason_collectors:
        reasons = collect_reasons()
        if reasons:
            return {"value": value, "reasons": aggregate_reasons(reasons)}
    return {
        "value": "pass",
        "reasons": aggregate_reasons(pass_reasons or [{"code": "all_required_passed"}]),
    }


def _pass_reasons_for_contract(verification_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Say WHY the pass tier was reached: earned, or vacuous.

    ``all_required_passed`` over an empty ``required`` list is a claim about
    checks that were never demanded. Measured: the same one-line edit blocks
    under ``src/app/api.py`` and passes with ``all_required_passed`` /
    "0 of 0 required checks ran" under ``mypkg/app/api.py``, because the
    default pack's ``public_api_changed`` rule is four literal layout/language
    pairs. ``roam guard-rules test`` was already honest about it
    ("NO MATCH ... 5 rules tried"); the verdict one layer up was not.

    The verdict VALUE stays ``pass`` -- see the note in CHANGELOG. This
    replaces only the false sentence, so no exit code anywhere changes.
    """
    required = verification_contract.get("required") or []
    meta = verification_contract.get("_meta") or {}
    unmatched = meta.get("unmatched_changed_files") or []
    if required or not unmatched:
        return [{"code": "all_required_passed"}]
    return [
        {
            "code": "no_rule_matched_for_changed_files",
            "detail": list(unmatched[:20]),
            "unmatched_count": len(unmatched),
            "rule_pack": (meta.get("rule_pack") or {}).get("name", "default"),
            "suggested_command": "roam guard-rules test <path>   # then: roam guard-pr --rules <pack.yml>",
        }
    ]


def _collect_unanalyzable_change_set_blocker(reason: str | None) -> list[dict[str, Any]]:
    """Return a hard-gate reason when the change set itself could not be read.

    Measured 2026-08-08 against 14.0.0: in a worktree whose ``.git`` had been
    corrupted, with a real uncommitted edit on disk, ``roam guard-pr --ci``
    printed ``✅ Roam Guard verdict: pass`` / "0 of 0 required checks ran" and
    exited 0 -- byte-identical to the same command in a HEALTHY copy of the
    same fixture apart from the head sha. The exit code could not tell "this
    PR required nothing" from "I could not open the repository", so a merge
    gate built on it authorized an unreadable tree. Naming the state is what
    makes the two distinguishable.
    """
    if not reason:
        return []
    return [
        {
            "code": "change_set_unanalyzable",
            "detail": reason,
            "suggested_command": "run inside a readable git worktree, or record the change set with `roam pr-bundle add affected <symbol>`",
        }
    ]


def _collect_blockers_that_invalidate_proof(
    *,
    verification_contract: dict[str, Any],
    executed_checks: list[dict[str, Any]],
    mcp_tool_findings: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return hard-gate reasons that make the proof untrustworthy."""
    reasons: list[dict[str, Any]] = []
    required = verification_contract.get("required", []) if verification_contract else []
    # W1441/W1447 — only a check RECORDED AS PASSING can satisfy a
    # requirement. This is an ALLOWLIST on "pass", and that direction is
    # the whole point.
    #
    # W1441 expressed it as a denylist — `status != "unverified"` — which
    # only refused the one status it had been taught to distrust. Every
    # other value satisfied the requirement, including ones no author
    # intends as evidence: measured against the shipped 13.10.0 binary, a
    # bundle whose only record carried `"skipped"`, `null`, or
    # `"Unverified"` (capitalised) returned exit 0 / "all_required_passed".
    # `validate_v1` already rejects those shapes, so the two validators
    # disagreed about the same bundle; the CLI path (`cmd_verdict`) does
    # not call it, so nothing caught them.
    #
    # An allowlist fails closed on statuses nobody has invented yet, which
    # a denylist cannot do.
    executed_names = {c.get("command") for c in executed_checks if c.get("status") == "pass"}
    unverified_names = {c.get("command") for c in executed_checks if c.get("status") == "unverified"}
    # fail/error are reported precisely by the failure loop below; listing
    # them here too would double-report one defect as two reasons.
    failed_names = {c.get("command") for c in executed_checks if c.get("status") in ("fail", "error")}
    invalid_status_by_name = {
        c.get("command"): c.get("status") for c in executed_checks if c.get("status") not in CHECK_STATUSES
    }
    for req in required:
        cmd = req.get("command")
        if cmd not in executed_names:
            if cmd in failed_names:
                continue
            if cmd in invalid_status_by_name:
                reasons.append(
                    {
                        "code": "required_check_status_invalid",
                        "check": cmd,
                        "status": invalid_status_by_name[cmd],
                        "because": req.get("reason"),
                        "detail": (
                            "this check's record carries a status outside the closed "
                            f"set {CHECK_STATUSES}; only a recorded 'pass' satisfies a "
                            "required check, so an unrecognised status proves nothing"
                        ),
                        "suggested_command": cmd,
                    }
                )
                continue
            if cmd in unverified_names:
                reasons.append(
                    {
                        "code": "required_check_unverified",
                        "check": cmd,
                        "because": req.get("reason"),
                        "detail": "a tests_run record names this check but carries no status; a record without an outcome is a claim, not evidence",
                        "suggested_command": cmd,
                    }
                )
                continue
            reasons.append(
                {
                    "code": "required_check_not_run",
                    "check": cmd,
                    "because": req.get("reason"),
                    "detail": req.get("detail"),
                    # W34d (E6): suggested_command gives the agent a one-step
                    # action per reason. Was: agent had to cross-reference
                    # verification_contract.required to find what to run.
                    "suggested_command": cmd,
                }
            )

    for c in executed_checks:
        if c.get("status") in ("fail", "error"):
            reasons.append(
                {
                    "code": "required_check_failed",
                    "check": c.get("command"),
                    "status": c.get("status"),
                    "evidence": c.get("evidence"),
                    "suggested_command": (
                        f"investigate {c.get('command')} (status={c.get('status')}); re-run after fix"
                    ),
                }
            )

    if ledger and ledger.get("verified") is False:
        reasons.append(
            {
                "code": "ledger_integrity_failure",
                "ledger": ledger.get("receipt_sha"),
                "suggested_command": "roam runs verify --strict",
            }
        )

    for finding in mcp_tool_findings:
        if finding.get("policy_decision") in ("deny", "fail") and finding.get("severity") == "high":
            reasons.append(
                {
                    "code": "mcp_redaction_required",
                    "finding": finding.get("kind"),
                    "tool": finding.get("tool"),
                    "suggested_command": (
                        f"review MCP redaction policy for {finding.get('tool')}; finding kind: {finding.get('kind')}"
                    ),
                }
            )

    return reasons


def _collect_review_gates_that_preserve_human_judgment(
    *,
    risk: dict[str, Any],
    scope_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return reasons that pass the hard gate but still need human judgment."""
    reasons: list[dict[str, Any]] = []
    risk_level = risk.get("level") or ""
    if risk_level == "high":
        paths = risk.get("paths", [])
        reasons.append(
            {
                "code": "high_risk_path",
                "paths": paths,
                "reasons": risk.get("reasons", []),
                "suggested_command": (
                    f"review high-risk paths ({len(paths)} files); accept via `roam permit <path>` after human review"
                ),
            }
        )

    for finding in scope_findings:
        if finding.get("severity") == "high":
            reasons.append(
                {
                    "code": "out_of_scope_edit",
                    "path": finding.get("path"),
                    "detail": finding.get("detail"),
                    "suggested_command": (f"split commit OR expand scope to include {finding.get('path')}"),
                }
            )

    return reasons


def _collect_review_coverage_warnings(
    *,
    review_evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Surface a same-family review as a coverage note, not a failure.

    The review is valid -- measurement puts its decisive-defect rate level
    with cross-family. What it measurably does NOT cover is the
    encoding/parser class. Saying so keeps the honest middle: the proof
    passes, and the narrower coverage is on the record rather than implied
    by silence.
    """
    if not isinstance(review_evidence, dict):
        return []
    reasons: list[dict[str, Any]] = []
    for phase, result in review_evidence.items():
        if isinstance(result, dict) and result.get("status") == "same_family":
            reasons.append(
                {
                    "code": "review_same_family_coverage",
                    "phase": phase,
                    "detail": (
                        "reviewer and builder share a model family; measured coverage is "
                        "narrower on encoding/parser defects (normalization collisions, "
                        "duplicate-key parsing)"
                    ),
                    "suggested_command": "for wider coverage, re-review with a different family",
                }
            )
    return reasons


def _collect_warnings_that_keep_proof_passable(
    *,
    optimizer_findings: list[dict[str, Any]],
    scope_findings: list[dict[str, Any]],
    mcp_tool_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return soft findings that should not block a passing proof."""
    reasons: list[dict[str, Any]] = []
    for finding in optimizer_findings:
        if finding.get("severity") in ("medium", "low"):
            reasons.append(
                {
                    "code": "optimizer_warning",
                    "task": finding.get("task") or finding.get("kind"),
                    "subject": finding.get("subject") or finding.get("symbol"),
                }
            )

    for finding in scope_findings:
        if finding.get("severity") in ("medium", "low"):
            reasons.append(
                {
                    "code": "scope_finding",
                    "path": finding.get("path"),
                }
            )

    for finding in mcp_tool_findings:
        if finding.get("severity") in ("medium", "low"):
            reasons.append(
                {
                    "code": "mcp_tool_finding",
                    "tool": finding.get("tool"),
                    "kind": finding.get("kind"),
                }
            )

    return reasons


def aggregate_reasons(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse redundant reason records into grouped ones.

    Example input: 4 records each `{code: required_check_not_run, check: X, because: auth}`
    where `because` is identical → collapses into ONE record
    `{code: required_checks_not_run, count: 4, because: auth, checks: [X, Y, Z, W]}`.

    Preserves all unique reasons. Only groups when the `code` AND a chosen
    secondary key (e.g. `because`) match across multiple entries.
    """
    # Group key per code (the field to dedupe on).
    GROUP_KEYS: dict[str, str] = {
        "required_check_not_run": "because",
        "required_check_failed": "evidence",
        "optimizer_warning": "task",
        "scope_finding": "path",
    }
    out: list[dict[str, Any]] = []
    # Buckets keyed by (code, group_key_value). Preserves order via dict.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    insertion_order: list[tuple[str, str]] = []
    pass_through: list[dict[str, Any]] = []

    for r in reasons:
        code = r.get("code")
        group_key = GROUP_KEYS.get(code)
        if group_key is None:
            pass_through.append(r)
            continue
        bucket_key = (code, str(r.get(group_key) or ""))
        if bucket_key not in buckets:
            buckets[bucket_key] = []
            insertion_order.append(bucket_key)
        buckets[bucket_key].append(r)

    for key in insertion_order:
        items = buckets[key]
        if len(items) == 1:
            out.append(items[0])
            continue
        code = items[0]["code"]
        # Use plural form for grouped codes that have one.
        grouped_code = {
            "required_check_not_run": "required_checks_not_run",
            "required_check_failed": "required_checks_failed",
            "optimizer_warning": "optimizer_warnings",
            "scope_finding": "scope_findings",
        }.get(code, code)
        group_key = GROUP_KEYS[code]
        combined: dict[str, Any] = {
            "code": grouped_code,
            "count": len(items),
            group_key: items[0].get(group_key),
            "checks": [{k: v for k, v in item.items() if k not in ("code", group_key)} for item in items],
        }
        out.append(combined)

    out.extend(pass_through)
    return out


def verdict_exit_code(verdict_value: str) -> int:
    """Map verdict to CI-friendly exit code (for `--strict` mode).

    Thin wrapper over `guard_enums.exit_code_for` for back-compat.
    """
    return exit_code_for(verdict_value)
