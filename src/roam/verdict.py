"""verdict — closed-enum verdict engine for AgentChangeProofBundle.

Per the proof-bundle schema (`internal/planning/PROOF-BUNDLE-SCHEMA-2026-05-27.md`):

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

from typing import Any

# Centralized closed enums — single source of truth lives in guard_enums.
# `VERDICTS` is re-exported here (explicit `as` alias) for external consumers
# that import it from `roam.verdict`; the others are used internally.
from roam.guard_enums import (
    VERDICT_PRECEDENCE,
    exit_code_for,
)
from roam.guard_enums import (
    VERDICTS as VERDICTS,
)


def _merge(current: str | None, candidate: str) -> str:
    """Pick the more-severe verdict."""
    if current is None:
        return candidate
    return current if VERDICT_PRECEDENCE[current] >= VERDICT_PRECEDENCE[candidate] else candidate


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
    missing_checks = missing_checks or []
    optimizer_findings = optimizer_findings or []
    scope_findings = scope_findings or []
    mcp_tool_findings = mcp_tool_findings or []
    risk = risk or {}

    verdict: str | None = None
    reasons: list[dict[str, Any]] = []

    # ---- blocked: hard gates ----

    # 1. Required check not run.
    required = verification_contract.get("required", []) if verification_contract else []
    executed_names = {c.get("command") for c in executed_checks}
    for req in required:
        cmd = req.get("command")
        if cmd not in executed_names:
            verdict = _merge(verdict, "blocked")
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

    # 2. Required check failed.
    for c in executed_checks:
        if c.get("status") in ("fail", "error"):
            verdict = _merge(verdict, "blocked")
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

    # 3. Ledger integrity failure (if a ledger is referenced).
    if ledger and ledger.get("verified") is False:
        verdict = _merge(verdict, "blocked")
        reasons.append(
            {
                "code": "ledger_integrity_failure",
                "ledger": ledger.get("receipt_sha"),
                "suggested_command": "roam runs verify --strict",
            }
        )

    # 4. MCP redaction required but not applied.
    for finding in mcp_tool_findings:
        if finding.get("policy_decision") in ("deny", "fail") and finding.get("severity") == "high":
            verdict = _merge(verdict, "blocked")
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

    # ---- needs_review: judgment gates ----

    if not _is_blocked(verdict):
        risk_level = risk.get("level") or ""
        if risk_level == "high":
            verdict = _merge(verdict, "needs_review")
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
                verdict = _merge(verdict, "needs_review")
                reasons.append(
                    {
                        "code": "out_of_scope_edit",
                        "path": finding.get("path"),
                        "detail": finding.get("detail"),
                        "suggested_command": (f"split commit OR expand scope to include {finding.get('path')}"),
                    }
                )

    # ---- pass_with_warnings: soft findings ----

    if not _is_blocked(verdict) and not _is_needs_review(verdict):
        for finding in optimizer_findings:
            if finding.get("severity") in ("medium", "low"):
                verdict = _merge(verdict, "pass_with_warnings")
                reasons.append(
                    {
                        "code": "optimizer_warning",
                        "task": finding.get("task") or finding.get("kind"),
                        "subject": finding.get("subject") or finding.get("symbol"),
                    }
                )

        for finding in scope_findings:
            if finding.get("severity") in ("medium", "low"):
                verdict = _merge(verdict, "pass_with_warnings")
                reasons.append(
                    {
                        "code": "scope_finding",
                        "path": finding.get("path"),
                    }
                )

        for finding in mcp_tool_findings:
            if finding.get("severity") in ("medium", "low"):
                verdict = _merge(verdict, "pass_with_warnings")
                reasons.append(
                    {
                        "code": "mcp_tool_finding",
                        "tool": finding.get("tool"),
                        "kind": finding.get("kind"),
                    }
                )

    # ---- pass (default) ----

    if verdict is None:
        verdict = "pass"
        reasons.append({"code": "all_required_passed"})

    # Collapse redundant reasons (4 missing checks with the same cause → one record).
    return {"value": verdict, "reasons": aggregate_reasons(reasons)}


def _is_blocked(verdict: str | None) -> bool:
    return verdict == "blocked"


def _is_needs_review(verdict: str | None) -> bool:
    return verdict == "needs_review"


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
