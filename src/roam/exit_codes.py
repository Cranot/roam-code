"""Standardized CLI exit codes for roam-code.

Exit code scheme (POSIX + SAST tool conventions):

    0  SUCCESS        -- command completed, no issues found (or info-only output)
    1  GENERAL_ERROR  -- unexpected failure, crash, unhandled exception
    2  USAGE_ERROR    -- invalid arguments, bad flags, unknown command (Click default)
    3  INDEX_MISSING  -- .roam/index.db not found, run `roam init` first
    4  INDEX_STALE    -- index exists but is outdated (mtime check failed)
    5  GATE_FAILURE   -- quality gate check failed (health score below threshold, etc.)
    6  PARTIAL        -- command completed but with warnings/partial results

CI tools (GitHub Actions, etc.) can differentiate between:
  - "analysis found issues" (5 = gate failure)
  - "tool crashed" (1 = general error)
  - "success" (0)
"""

from __future__ import annotations

import sys

import click

# ---------------------------------------------------------------------------
# Exit code constants
# ---------------------------------------------------------------------------

EXIT_SUCCESS: int = 0
EXIT_ERROR: int = 1
EXIT_USAGE: int = 2
EXIT_INDEX_MISSING: int = 3
EXIT_INDEX_STALE: int = 4
EXIT_GATE_FAILURE: int = 5
EXIT_PARTIAL: int = 6

# ---------------------------------------------------------------------------
# Human-readable descriptions (useful for --help, diagnostics, MCP hints)
# ---------------------------------------------------------------------------

DESCRIPTIONS: dict[int, str] = {
    EXIT_SUCCESS: "success",
    EXIT_ERROR: "unexpected error",
    EXIT_USAGE: "invalid usage (bad arguments or flags)",
    EXIT_INDEX_MISSING: "index not found -- run `roam init`",
    EXIT_INDEX_STALE: "index is stale -- run `roam index`",
    EXIT_GATE_FAILURE: "quality gate failed",
    EXIT_PARTIAL: "partial results (completed with warnings)",
}

# ---------------------------------------------------------------------------
# Custom exceptions (caught by CLI error handler)
# ---------------------------------------------------------------------------


class RoamError(click.ClickException):
    """Base class for roam-specific errors with exit codes."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.exit_code = exit_code

    def format_message(self) -> str:
        return self.message


class IndexMissingError(RoamError):
    """Raised when the roam index database does not exist."""

    def __init__(
        self,
        message: str = (
            "No index found. Run `roam init` to create one. "
            "If this looks unexpected, run `roam doctor` to diagnose your install."
        ),
    ):
        super().__init__(message, EXIT_INDEX_MISSING)


class IndexStaleError(RoamError):
    """Raised when the roam index is outdated."""

    def __init__(self, message: str = "Index is stale. Run `roam index` to refresh."):
        super().__init__(message, EXIT_INDEX_STALE)


class GateFailureError(RoamError):
    """Raised when a quality gate check fails."""

    def __init__(self, message: str = "Quality gate failed."):
        super().__init__(message, EXIT_GATE_FAILURE)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def gate_should_fail(
    gate_enabled: bool,
    *,
    findings: object,
    scan_incomplete: bool,
) -> bool:
    """Decide ONCE whether a gate flag must refuse, for every output channel.

    A gate answers one question: "may this change proceed?" There are three
    possible answers -- CLEAN, VIOLATION, UNANALYZABLE -- and only CLEAN
    authorizes. ``scan_incomplete`` is UNANALYZABLE: the check did not run, so
    nothing is proven clean and the gate must refuse. Sharing exit code 5 with
    a measured VIOLATION is correct; sharing exit code 0 with a measured CLEAN
    is the defect this helper exists to prevent.

    The reason this is a function and not an inline expression at each exit
    site: the idiom ``if fail_on_found and violations:`` was written once per
    output channel, by hand, in every gated command. `roam ignore-drift`
    carried two copies and only the ``--json`` copy included the
    ``or scan_incomplete`` term, so the text channel -- the channel both of
    this repo's wired callers use -- printed "This is NOT a clean result" and
    then exited 0. Nothing but copy-paste discipline was holding the invariant.
    Call this once, before the first ``if json_mode:`` branch, and let every
    channel read the single resulting boolean.

    Args:
        gate_enabled: the gate flag itself (``--fail-on-found``, ``--ci``, ...).
            False means the command is reporting, not gating, so it never fails.
        findings: whatever the command counts as a measured violation. Any
            truthy value (a non-empty list, a positive count) means VIOLATION.
        scan_incomplete: True when the measurement did not happen or is known
            to be partial. An absent measurement is UNKNOWN, never a benign
            CLEAN.

    Returns:
        True when the gate must exit non-zero (``EXIT_GATE_FAILURE``).
    """
    if not gate_enabled:
        return False
    return bool(findings) or bool(scan_incomplete)


def exit_with(code: int, message: str | None = None) -> None:
    """Print an optional message to stderr and exit with the given code.

    Uses click.echo(err=True) for consistent output handling.
    """
    if message:
        click.echo(f"Error: {message}", err=True)
    sys.exit(code)
