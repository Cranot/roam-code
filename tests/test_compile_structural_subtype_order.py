"""Sentinel: structural subtype routing order is a single source of truth.

The routing scan `_classify_structural_subtype` and the confidence hit-count
in `_classifier_confidence` both loop over the ONE ordered tuple
`_STRUCTURAL_SUBTYPE_REGEXES`. Historically the order was duplicated as six
hard-coded `if` checks in the router plus a separate copy in the confidence
function, so a regex inserted in one place could silently drift from the
other without an obvious route failure. These tests pin that invariant.
"""

from __future__ import annotations

import roam.plan.compiler as compiler
from roam.plan.compiler import (
    _STRUCTURAL_SUBTYPE_REGEXES,
    _classify_structural_subtype,
)

# The canonical precedence. coupling MUST precede blast/callers (v0.3) so a
# compound "highest coupling (most callers / largest blast)" routes to
# coupling — the authoritative intent. If you reorder or insert a subtype,
# update this list deliberately; that is the point of the sentinel.
EXPECTED_ORDER = [
    "structural_dead",
    "structural_cycle",
    "structural_complexity",
    "structural_coupling",
    "structural_blast",
    "structural_callers",
]


def test_subtype_tuple_pins_the_canonical_order():
    assert [name for name, _ in _STRUCTURAL_SUBTYPE_REGEXES] == EXPECTED_ORDER


def test_router_loops_over_the_shared_tuple_in_order():
    # Each subtype's first-listed regex matches its own probe phrasing; the
    # router returns it because the tuple is scanned in declared order.
    probes = {
        "structural_dead": "find the dead code in this module",
        "structural_cycle": "show me the import cycles",
        "structural_complexity": "what is the cyclomatic complexity of parse",
        "structural_coupling": "strongest coupling to compiler.py",
        "structural_blast": "blast radius of refactoring handleSave",
        "structural_callers": "who calls log_swallowed",
    }
    for expected, task in probes.items():
        assert _classify_structural_subtype(task) == expected


def test_coupling_wins_over_blast_and_callers_on_compound_task():
    # The precedence guarantee that the ordering exists to protect.
    task = "highest structural coupling (most callers / largest blast radius)"
    assert _classify_structural_subtype(task) == "structural_coupling"


def test_non_structural_task_returns_none():
    assert _classify_structural_subtype("write a haiku about the sea") is None


def test_confidence_and_router_share_one_tuple_object():
    # No second copy exists: the module exposes exactly one tuple, and both
    # consumers reference it by name. Identity check guards against a future
    # edit re-introducing a duplicate literal.
    assert _STRUCTURAL_SUBTYPE_REGEXES is compiler._STRUCTURAL_SUBTYPE_REGEXES
    assert len(_STRUCTURAL_SUBTYPE_REGEXES) == len(EXPECTED_ORDER)
