"""Shared helper functions for the Roam Guard test suites.

This file is explicitly importable (unlike conftest.py which pytest
manages); use it for module-level helpers callers want to invoke directly.
"""

from __future__ import annotations


def make_pr_bundle(*, files=None, tests_run=None, risks=None, affected=None, intent="Test"):
    """Build a synthetic pr-bundle dict.

    Centralized so proof_bundle / verdict / guard-pr test suites don't
    duplicate the schema shape. Mirrors `_empty_bundle()` from
    cmd_pr_bundle.py with only the keys tests populate.
    """
    return {
        "schema_version": "1.6",
        "intent": intent,
        "context_read": {
            "symbols_inspected": [],
            "files_inspected": files or [],
            "commands_run": [],
        },
        "affected_symbols": affected or [{"name": "refresh_token", "file": "src/auth/session.py"}],
        "risks": risks or [],
        "tests_required": [],
        "tests_run": tests_run or [],
        "known_non_goals": [],
        "roam_verdict": {},
        "approvals": [],
        "accepted_risks": [],
        "created_at": "2026-05-29T00:00:00Z",
        "updated_at": "2026-05-29T00:00:00Z",
    }
