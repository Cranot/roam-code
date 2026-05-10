"""Structured CLI error codes — round 4 feature H.

Agents reading CLI stderr need a stable handle to branch on. The user-
facing message is still a sentence; the machine-readable handle is a
SCREAMING_SNAKE_CASE prefix. Same vocabulary used by the MCP wrappers'
``error_code`` field, so a single dispatch table covers both surfaces.

Usage::

    raise structured_usage_error("INVALID_DIFF", "input is not a unified diff")
    # message: "INVALID_DIFF: input is not a unified diff"
"""

from __future__ import annotations

import click

# Canonical error codes — extend as new structured failures are added.
EMPTY_INPUT = "EMPTY_INPUT"
INVALID_DIFF = "INVALID_DIFF"
INVALID_RANGE = "INVALID_RANGE"
INVALID_FORMAT = "INVALID_FORMAT"
INVALID_OPTIONS = "INVALID_OPTIONS"
MISSING_REQUIRED_ARG = "MISSING_REQUIRED_ARG"
UNKNOWN_OPTION = "UNKNOWN_OPTION"
UNKNOWN_RECIPE = "UNKNOWN_RECIPE"
UNKNOWN_FORMAT = "UNKNOWN_FORMAT"
INDEX_STALE = "INDEX_STALE"
INDEX_MISSING = "INDEX_MISSING"
SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
WORKSPACE_NOT_CONFIGURED = "WORKSPACE_NOT_CONFIGURED"
RATE_LIMITED = "RATE_LIMITED"
RUN_FAILED = "RUN_FAILED"
DIRTY_TREE = "DIRTY_TREE"

# Set of all defined codes — used by tests to enforce the prefix contract.
ALL_CODES = frozenset(
    {
        EMPTY_INPUT,
        INVALID_DIFF,
        INVALID_RANGE,
        INVALID_FORMAT,
        INVALID_OPTIONS,
        MISSING_REQUIRED_ARG,
        UNKNOWN_OPTION,
        UNKNOWN_RECIPE,
        UNKNOWN_FORMAT,
        INDEX_STALE,
        INDEX_MISSING,
        SYMBOL_NOT_FOUND,
        FILE_NOT_FOUND,
        WORKSPACE_NOT_CONFIGURED,
        RATE_LIMITED,
        RUN_FAILED,
        DIRTY_TREE,
    }
)


def parse_code(message: str) -> str | None:
    """Extract the structured code prefix from an error message, if any.

    Returns ``None`` when the message doesn't follow the contract — used
    by the structural test that enforces every UsageError carries a
    code so agents can branch programmatically.
    """
    if not isinstance(message, str):
        return None
    head = message.split(":", 1)[0].strip()
    if head in ALL_CODES:
        return head
    return None


def structured_usage_error(code: str, message: str) -> click.UsageError:
    """Return a click.UsageError whose message starts with a stable code.

    The caller still controls phrasing; this just enforces the prefix
    contract so MCP/CI consumers can parse `output.split(':', 1)[0]`
    to get the code.
    """
    return click.UsageError(f"{code}: {message}")
