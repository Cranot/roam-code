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
# that import it from `roam.verdict`.
from roam.guard_enums import exit_code_for
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
) -> dict[str, Any]:
    """Compute the proof-bundle verdict from collected evidence.

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
                lambda: _collect_blockers_that_invalidate_proof(
                    verification_contract=verification_contract,
                    executed_checks=executed_checks,
                    mcp_tool_findings=mcp_tool_findings,
                    ledger=ledger,
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
                lambda: _collect_warnings_that_keep_proof_passable(
                    optimizer_findings=optimizer_findings,
                    scope_findings=scope_findings,
                    mcp_tool_findings=mcp_tool_findings,
                ),
            ),
        )
    )


def _select_verdict_that_preserves_gate_precedence(
    reason_collectors: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...],
) -> dict[str, Any]:
    """Return the first verdict tier with evidence, preserving hard-gate order."""
    for value, collect_reasons in reason_collectors:
        reasons = collect_reasons()
        if reasons:
            return {"value": value, "reasons": aggregate_reasons(reasons)}
    return {"value": "pass", "reasons": aggregate_reasons([{"code": "all_required_passed"}])}


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
    executed_names = {c.get("command") for c in executed_checks}
    for req in required:
        cmd = req.get("command")
        if cmd not in executed_names:
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
                    f"review high-risk paths ({len(paths)} files); accept via "
                    f"`roam permit <path>` after human review"
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
