"""CLI telemetry must be able to record a non-zero exit code.

The close hook recorded ``exit_code=0`` unconditionally, so the column could
only ever hold one value. Every failed invocation was filed as a success, and
any aggregate over it reported a 100% success rate by construction — a field
that cannot vary is not data, it is a constant with a misleading name.

Surfaced by a 2026-07-26 mining survey, alongside two sibling defects of the
same shape: 94.5% of compile rows sit in an explicitly-mixed
``agent_mode=unknown`` cohort, and 372 of 383 engagement-ledger rows point at
pytest paths while carrying no test provenance.
"""

from __future__ import annotations

import click
import pytest

from roam.cli import _exit_code_in_flight


def test_clean_teardown_is_zero() -> None:
    """Nothing in flight genuinely means success."""
    assert _exit_code_in_flight() == 0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SystemExit(2), 2),
        (SystemExit(0), 0),
        (SystemExit(None), 0),
        (click.UsageError("bad usage"), 2),
        (click.ClickException("failed"), 1),
    ],
)
def test_known_failures_report_their_real_code(exc: BaseException, expected: int) -> None:
    try:
        raise exc
    except BaseException:
        assert _exit_code_in_flight() == expected


@pytest.mark.parametrize("exc", [ValueError("unexpected"), RuntimeError("boom"), SystemExit("non-int")])
def test_unknown_failures_are_never_recorded_as_success(exc: BaseException) -> None:
    """Conservative in the direction that matters.

    An exception we cannot map to a specific code must not become 0. Recording
    a failure as a success is the error that corrupts the metric; recording it
    as a generic 1 merely loses precision.
    """
    try:
        raise exc
    except BaseException:
        assert _exit_code_in_flight() == 1


def test_the_function_can_actually_return_something_other_than_zero() -> None:
    """Guards the original defect directly.

    The previous implementation would pass every test above that expects 0 and
    fail only this one, so assert the column is capable of variation at all.
    """
    observed = {_exit_code_in_flight()}
    for exc in (SystemExit(2), click.ClickException("x")):
        try:
            raise exc
        except BaseException:
            observed.add(_exit_code_in_flight())
    assert len(observed) > 1, "exit_code must be able to hold more than one value"
