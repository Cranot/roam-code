"""Pattern-1 machine-gate drift guard.

Asserts that every command whose primary argless error branch emits a JSON
envelope also carries the two machine-gate fields the Pattern-1 family
mandates: ``isError: true`` and a ``status`` drawn from the closed enum.
Without these fields an MCP wrapper cannot branch on failure without parsing
free-form text (CLAUDE.md §"Pattern-1 family — the canonical failure
envelope").

The 7-member status enum is hand-mirrored as a literal below (NOT imported
from source) so this lint stays decoupled from the producer and would catch
a producer-side enum widening that drifts from the documented contract.

The two CONDITIONAL commands (``fitness`` / ``check-rules``) are deliberately
EXCLUDED from the parametrized list: whether they emit ``isError`` depends on
the corpus (failed-rule count / error-severity exit code), so an argless
invocation does not deterministically trip the gate. See the comment at the
bottom of this module.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli

# Hand-mirrored closed status enum (CLAUDE.md §"Pattern-1 family"). Do NOT
# import from source — drift between this literal and the producer is exactly
# what this guard is here to catch.
_PATTERN1_STATUS_ENUM = frozenset(
    {
        "index_not_built",
        "advisory_warnings",
        "partial_failure",
        "hard_failure",
        "usage_error",
        "rate_limited",
        "stale_index",
    }
)

# The 15 UNCONDITIONAL commands whose primary argless error branch always
# emits a usage-error envelope. Each is invoked as ``roam --json <command>``
# with empty stdin; the branch fires when no target/required-arg is supplied.
_UNCONDITIONAL_COMMANDS = [
    "affected-tests",
    "context",
    "coverage-gaps",
    "file",
    "grep",
    "history-grep",
    "ingest-trace",
    "intent-check",
    "invariants",
    "plan",
    "preflight",
    "refs-text",
    "relate",
    "report",
    "reset",
]


@pytest.mark.parametrize("command", _UNCONDITIONAL_COMMANDS)
def test_argless_error_branch_carries_machine_gate_fields(command: str) -> None:
    """The argless error branch must set isError + a closed-enum status."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", command], input="")

    # The argless path is a failure path — it must NOT exit 0.
    assert result.exit_code != 0, (
        f"`roam --json {command}` (argless) exited 0; expected a non-zero usage/error exit. Output:\n{result.output}"
    )

    # stdout must be a single parseable JSON envelope (Pattern-1C: never
    # emit Click help text or empty stdout in --json mode).
    try:
        envelope = json.loads(result.output)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure detail
        raise AssertionError(
            f"`roam --json {command}` (argless) did not emit parseable JSON on stdout: {exc}\nOutput:\n{result.output}"
        ) from exc

    assert envelope.get("isError") is True, (
        f"`roam --json {command}` (argless) envelope is missing `isError: true`. Envelope keys: {sorted(envelope)}"
    )

    status = envelope.get("status")
    assert status in _PATTERN1_STATUS_ENUM, (
        f"`roam --json {command}` (argless) status={status!r} is not in the "
        f"closed Pattern-1 status enum {sorted(_PATTERN1_STATUS_ENUM)}"
    )


# CONDITIONAL commands (NOT parametrized above):
#   - fitness    : emits isError/status only when summary.failed > 0.
#   - check-rules: emits isError/status only when exit_code != 0 (FAIL, i.e.
#                  an error-severity violation). PASS/WARN stay clean.
# Their pass/fail behaviour depends on the live corpus, so an argless
# invocation does not deterministically trip the machine-gate. The producer-
# side gating logic for both is unit-covered by the conditional branches in
# their respective cmd_*.py modules; this lint intentionally scopes to the 15
# deterministic usage-error branches.
