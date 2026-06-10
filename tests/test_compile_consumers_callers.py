"""Telemetry-driven classifier fix (2026-06-04).

The compile-runs ledger showed 49% of ``freeform_explore`` compiles delivered an
EMPTY prefetch (no facts → ~0 savings), and in production (agent_mode=compile)
85% were empty-prefetch freeform. Several were mis-routed callers queries phrased
with "consumers of X" / "users of X" — direct synonyms for "callers of X" that
the ``_STRUCTURAL_CALLERS_RE`` regex didn't cover. They now route to
``structural_callers`` so the envelope prefetches ``roam_uses`` instead of nothing.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import _classify

_POSITIVE = [
    "find the consumers of `log_swallowed`",
    "who are the users of `open_db`",
    "list the consumers of resolve_changed_to_db",
    "consumers of compile_plan",
    "users of the _classify function",
]

# Shapes that share surface words but are NOT callers queries — must not get
# dragged into structural_callers by the broadened regex.
_NEGATIVE = [
    "what does the compiler module do",
    "compare _classify vs _classifier_confidence",
    "where is compile_plan defined",
]


@pytest.mark.parametrize("task", _POSITIVE)
def test_consumers_users_route_to_callers(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc == "structural_callers", f"expected structural_callers, got {proc!r} for {task!r}"


@pytest.mark.parametrize("task", _NEGATIVE)
def test_negatives_do_not_misroute_to_callers(task: str) -> None:
    proc, _rejected = _classify(task)
    assert proc != "structural_callers", f"unexpected structural_callers for {task!r}"
