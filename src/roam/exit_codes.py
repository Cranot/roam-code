"""Standardized CLI exit codes for roam-code.

Exit code scheme (POSIX + SAST tool conventions):

    0    SUCCESS        -- command completed, no issues found (or info-only output)
    1    GENERAL_ERROR  -- unexpected failure, crash, unhandled exception
    2    USAGE_ERROR    -- invalid arguments, bad flags, unknown command (Click default)
    3    INDEX_MISSING  -- reserved; see the reachability note below
    4    INDEX_STALE    -- reserved name; in the field this integer means NEEDS_REVIEW
    5    GATE_FAILURE   -- quality gate check failed (health score below threshold, etc.)
    6    PARTIAL        -- command completed but with warnings/partial results
    130  INTERRUPTED    -- SIGINT / Ctrl-C (128 + SIGINT, POSIX convention)

REACHABILITY -- what a caller branching on this table will actually receive.
This section exists because the table above was, for two of its rows, a
description of an intention rather than of the program.

* 3 (INDEX_MISSING) is returned by NO shipped command. The only helper that
  raises ``IndexMissingError`` is ``roam.commands.resolve.require_index``,
  which nothing in ``src/roam`` calls: commands auto-index instead of
  refusing, so a repo with no ``.roam/index.db`` gets an index, not exit 3.
  Measured in a fresh repo: ``roam search`` / ``roam health`` / ``roam dead``
  all exit 0.

* 4 (INDEX_STALE) is returned by NO command with that meaning --
  ``IndexStaleError`` is never raised outside tests. The integer 4 IS
  returned, by the guard family (``verdict`` / ``guard-pr`` /
  ``proof-bundle``, via ``guard_enums.VERDICT_EXIT_CODES``), where it means
  ``needs_review``: a human must look. The constant keeps its old NAME for
  import compatibility; treat the VALUE as needs_review. Re-running does not
  change it, so it is not retryable.

* 2 is overloaded. Click uses it for a usage error, and ``roam doctor`` also
  uses it for "a blocking environment check failed" -- a run whose arguments
  were perfectly valid. Distinguish by which command you ran.

* A version-incompatible index (built by an older roam, or by a NEWER roam
  than the running client) refuses at the ``open_db`` readonly gate via
  ``IndexVersionError`` with exit 1 -- deliberately neither 3 (the index
  exists) nor 4 (the guard family owns that integer as needs_review). The
  MCP layer classifies the refusal from its message text as INDEX_STALE
  (retryable); see the class docstring.

* 130 is not optional trivia: an uncaught KeyboardInterrupt exits 130 through
  both the console-script entry point and ``python -m roam``. A caller
  branching only over 0-6 falls through every arm when a user presses Ctrl-C.

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
#: 128 + SIGINT. Produced by an uncaught KeyboardInterrupt through every
#: entry point, and previously absent from this catalogue -- so the artifact
#: roam ships for callers to branch on did not list the code a caller is most
#: likely to meet by accident.
EXIT_INTERRUPTED: int = 130

# ---------------------------------------------------------------------------
# Human-readable descriptions (useful for --help, diagnostics, MCP hints)
# ---------------------------------------------------------------------------

DESCRIPTIONS: dict[int, str] = {
    EXIT_SUCCESS: "success",
    EXIT_ERROR: "unexpected error",
    EXIT_USAGE: "invalid usage (bad arguments or flags)",
    EXIT_INDEX_MISSING: "index not found -- run `roam init` (reserved; no command returns this)",
    EXIT_INDEX_STALE: "needs_review -- a guard verdict requires a human; re-running will not change it",
    EXIT_GATE_FAILURE: "quality gate failed",
    EXIT_PARTIAL: "partial results (completed with warnings)",
    EXIT_INTERRUPTED: "interrupted (SIGINT / Ctrl-C)",
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


class IndexVersionError(RoamError):
    """Raised when the on-disk index schema version does not match this build.

    The read-path gate in ``roam.db.connection.open_db`` (readonly branch)
    raises this instead of silently consuming an index whose ``PRAGMA
    user_version`` differs from ``connection.USER_VERSION`` — the defect
    class where a 13.10-built index consumed under roam 14 produced wrong
    results with zero staleness disclosure.

    Exit code is ``EXIT_ERROR`` (1), deliberately NOT ``EXIT_INDEX_STALE``
    (4): the integer 4 is produced by the guard family and means
    ``needs_review`` in the field — mapping a genuinely-retryable schema
    mismatch onto it re-creates the misclassification the MCP layer
    already fixed (see ``mcp_server._classify_error``). The stale-index
    message text is what the MCP structured/stderr path classifies as
    ``INDEX_STALE`` (retryable), which is the documented channel for a
    schema bump.

    ``found`` / ``expected`` carry the version pair so envelopes, tests,
    and callers can disclose the mismatch without parsing the message.
    """

    def __init__(self, message: str, *, found: int, expected: int):
        super().__init__(message, EXIT_ERROR)
        self.found = found
        self.expected = expected


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
