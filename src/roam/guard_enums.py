"""Closed-enum vocabulary shared across the Roam Guard substrate.

Single source of truth for the verdict / mode / risk / policy / check-status
values that flow through:

  * verdict engine (`roam.verdict.compute_verdict`)
  * verification contract (`roam.verification_contract.build_verification_contract`)
  * AgentChangeProofBundle v1 schema (`schemas/agent_change_proof_bundle.v1.json`)
  * GitHub Check Run mapping (`roam.github_check.VERDICT_TO_CONCLUSION`)
  * markdown render (`roam.proof_bundle.render_markdown`)
  * CLI choice constraints (Click `--mode`, `--policy-profile`, etc.)

Before this module these enums lived in 4 different files. Drift led to
desync — e.g. adding a new verdict required touching verdict.py + the
schema JSON + the github_check mapping separately. Now everything imports
from here.

The schema JSON STILL hard-codes the values (JSON Schema doesn't run
Python); the lint `test_guard_enums_match_schema` keeps them in sync.
"""

from __future__ import annotations

# ---- verdict closed enum + precedence ----

VERDICTS: tuple[str, ...] = (
    "pass",
    "pass_with_warnings",
    "needs_review",
    "blocked",
)
VERDICT_PRECEDENCE: dict[str, int] = {v: i for i, v in enumerate(VERDICTS)}

# Higher index = more severe. blocked > needs_review > pass_with_warnings > pass.


def is_more_severe(a: str, b: str) -> bool:
    """Return True if verdict `a` is more severe than `b`."""
    return VERDICT_PRECEDENCE.get(a, -1) > VERDICT_PRECEDENCE.get(b, -1)


# ---- modes ----

MODES: tuple[str, ...] = (
    "read_only",
    "safe_edit",
    "migration",
    "autonomous_pr",
)


# ---- policy profiles ----

POLICY_PROFILES: tuple[str, ...] = (
    "startup",
    "regulated",
)


# ---- risk levels ----

RISK_LEVELS: tuple[str, ...] = (
    "low",
    "medium",
    "high",
)


# ---- executed check statuses ----

CHECK_STATUSES: tuple[str, ...] = (
    "pass",
    "fail",
    "error",
)


# ---- reason codes (verdict.reasons[].code) ----

REASON_CODES: frozenset[str] = frozenset(
    {
        # blocked
        "required_check_not_run",
        "required_checks_not_run",  # aggregated form
        "required_check_failed",
        "required_checks_failed",
        "policy_violation",
        "ledger_integrity_failure",
        "mcp_redaction_required",
        # needs_review
        "high_risk_path",
        "out_of_scope_edit",
        "missing_test_for_high_risk",
        # pass_with_warnings
        "optimizer_warning",
        "optimizer_warnings",
        "scope_finding",
        "scope_findings",
        "mcp_tool_finding",
        # pass
        "all_required_passed",
    }
)


# ---- verdict UX surfaces (icons + display titles) ----

VERDICT_ICONS: dict[str, str] = {
    "pass": "✅",
    "pass_with_warnings": "⚠️",
    "needs_review": "👀",
    "blocked": "🛑",
}

VERDICT_TITLES: dict[str, str] = {
    "pass": "Roam Guard — pass",
    "pass_with_warnings": "Roam Guard — warnings",
    "needs_review": "Roam Guard — review required",
    "blocked": "Roam Guard — blocked",
}

# GitHub Check Run conclusion mapping.
VERDICT_TO_GH_CONCLUSION: dict[str, str] = {
    "pass": "success",
    "pass_with_warnings": "neutral",
    "needs_review": "action_required",
    "blocked": "failure",
}

# Compact ASCII icons for text-mode tables (avoid emoji width issues).
VERDICT_ICONS_ASCII: dict[str, str] = {
    "pass": "✓",
    "pass_with_warnings": "⚠",
    "needs_review": "?",
    "blocked": "✗",
}


# ---- CI exit codes ----

VERDICT_EXIT_CODES: dict[str, int] = {
    "pass": 0,
    "pass_with_warnings": 0,  # non-blocking in non-strict mode
    "needs_review": 4,
    "blocked": 5,
}


def exit_code_for(verdict_value: str) -> int:
    """Map verdict → CI-friendly exit code."""
    return VERDICT_EXIT_CODES.get(verdict_value, 1)
