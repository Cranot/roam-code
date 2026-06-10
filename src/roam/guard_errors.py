"""guard_errors — closed-enum error codes + structured error envelopes for Roam Guard CLI.

Standardizes the ad-hoc `{ok: false, error: "..."}` patterns scattered
across Roam Guard CLI commands (guard-pr, proof-bundle, verification-contract,
verdict, guard-doctor, guard-rules) into a single shape:

  {
    "code": "...",         # closed enum, machine-readable
    "detail": "...",       # short human summary
    "fix": "..." | null,   # one-line remediation hint
    "context": {...} | null  # optional structured detail
  }

Distinct from `roam.resilience` (the reliability super-optimizer command,
unrelated namespace).
"""

from __future__ import annotations

from typing import Any

# Closed enum of error codes used across Roam Guard CLI commands.
# When a new error path needs a code, add it here AND the lint will pass.
GUARD_ERROR_CODES: frozenset[str] = frozenset(
    {
        # bundle loading / discovery
        "no_bundle_found",
        "bundle_load_failed",
        "bundle_parse_error",
        # rule pack loading
        "rule_pack_load_failed",
        "rule_pack_invalid",
        # input validation
        "no_input_files",
        "missing_required_field",
        # GitHub Check posting
        "missing_gh_repo_or_sha",
        "gh_repo_must_be_owner_slash_repo",
        "no_github_token",
        # composer-side errors
        "compose_failed",
        "auto_collect_failed",
        "schema_validation_failed",
        # generic
        "unexpected_error",
    }
)

# Map error codes → recommended exit codes for CLI commands.
# 2 = caller input error / setup issue (most common)
# 5 = blocking failure that should fail the build
GUARD_ERROR_EXIT_CODES: dict[str, int] = {
    "no_bundle_found": 2,
    "bundle_load_failed": 2,
    "bundle_parse_error": 2,
    "rule_pack_load_failed": 2,
    "rule_pack_invalid": 2,
    "no_input_files": 2,
    "missing_required_field": 2,
    "missing_gh_repo_or_sha": 2,
    "gh_repo_must_be_owner_slash_repo": 2,
    "no_github_token": 2,
    "compose_failed": 5,
    "auto_collect_failed": 2,
    "schema_validation_failed": 5,
    "unexpected_error": 1,
}


def make_guard_error(
    code: str,
    detail: str,
    *,
    fix: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured error dict for Roam Guard CLI commands.

    Args:
      code: closed-enum error code (must be in GUARD_ERROR_CODES).
      detail: short human-readable summary.
      fix: optional one-line remediation hint.
      context: optional structured detail (e.g. {"path": "...", "expected": ...}).

    Returns:
      Dict with keys {code, detail, fix, context}. Always-present keys
      so consumers can rely on the shape (Pattern 2 — explicit absence
      via None, never missing keys).
    """
    if code not in GUARD_ERROR_CODES:
        # Soft fail — emit unexpected_error instead of raising, so the
        # error path itself can't crash.
        return {
            "code": "unexpected_error",
            "detail": f"unknown error code '{code}': {detail}",
            "fix": fix,
            "context": context,
        }
    return {
        "code": code,
        "detail": detail,
        "fix": fix,
        "context": context,
    }


def exit_code_for_guard_error(code: str) -> int:
    """Return the recommended exit code for a Roam Guard error code."""
    return GUARD_ERROR_EXIT_CODES.get(code, 1)


def guard_error_envelope(
    command: str,
    code: str,
    detail: str,
    *,
    fix: str | None = None,
    context: dict[str, Any] | None = None,
    summary_extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a roam JSON envelope wrapping a structured Roam Guard error.

    Provides a uniform shape for CLI `--json` error output across every
    Roam Guard command. Sets partial_success=True so consumers can scan
    for failure quickly. Surfaces the error code in summary.verdict so
    text-mode + JSON-mode share the failure signal.
    """
    from roam.output.formatter import json_envelope

    err = make_guard_error(code, detail, fix=fix, context=context)
    summary: dict[str, Any] = {
        "verdict": code,
        "error_code": code,
        "error_detail": detail,
        "partial_success": True,
    }
    if summary_extras:
        summary.update(summary_extras)
    facts = [detail]
    if fix:
        facts.append(f"fix: {fix}")
    return json_envelope(
        command,
        summary=summary,
        agent_contract={
            "facts": facts,
            "next_commands": [],
            "risks": [err],
        },
        error=err,
    )
