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

    def __init__(self, message: str = "No index found. Run `roam init` to create one."):
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


def exit_with(code: int, message: str | None = None) -> None:
    """Print an optional message to stderr and exit with the given code.

    Uses click.echo(err=True) for consistent output handling.
    """
    if message:
        click.echo(f"Error: {message}", err=True)
    sys.exit(code)
