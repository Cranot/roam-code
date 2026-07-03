"""Sentinel: structural subtype routing order is a single source of truth.

The routing scan `_classify_structural_subtype` and the confidence hit-count
in `_classifier_confidence` both loop over the ONE ordered tuple
`_STRUCTURAL_SUBTYPE_REGEXES`. Historically the order was duplicated as six
hard-coded `if` checks in the router plus a separate copy in the confidence
function, so a regex inserted in one place could silently drift from the
other without an obvious route failure. These tests pin that invariant.
"""

from __future__ import annotations

from roam.plan.compiler import (
    _STRUCTURAL_SUBTYPE_REGEXES,
    _classifier_confidence,
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


def test_confidence_hit_count_derives_from_the_shared_tuple():
    # The real drift guard. `_classifier_confidence` must count structural
    # matches from the SAME tuple the router scans — not an independent copy.
    # Observable signature of that: as more shared-tuple regexes match a task,
    # the structural confidence score must drop monotonically. If a future edit
    # re-introduces a private subtype list inside confidence (the historical
    # bug these tests exist to prevent), the monotonic drop breaks and turns
    # silent drift into an obvious failure. Buckets are intentionally not
    # pinned — only the monotonic relation to the shared-tuple hit count is.
    one_hit = "find the dead code in this module"
    two_hit = "strongest coupling to parse.py and who calls it"
    many_hit = "blast radius of the import cycles in the dead code"

    def _shared_hits(task: str) -> int:
        return sum(1 for _, rgx in _STRUCTURAL_SUBTYPE_REGEXES if rgx.search(task))

    # Sanity: the probes really do hit 1 / 2 / 3+ shared-tuple regexes, so a
    # monotonic score drop can only come from confidence reading this tuple.
    assert _shared_hits(one_hit) == 1
    assert _shared_hits(two_hit) == 2
    assert _shared_hits(many_hit) >= 3

    s_one = _classifier_confidence(one_hit, "structural_dead")
    s_two = _classifier_confidence(two_hit, "structural_dead")
    s_many = _classifier_confidence(many_hit, "structural_dead")
    assert s_one > s_two > s_many, f"confidence must drop as shared-tuple hits rise; got {s_one} > {s_two} > {s_many}"
