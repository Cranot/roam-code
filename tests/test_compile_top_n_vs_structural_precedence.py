"""Behavior-preserving precedence lock: top_n_ranking vs structural subtypes.

WHAT THIS LOCKS
---------------
`roam compile --explain` shows that the broad top-N classifier
(`_TOP_N_RANKING_RE`) can match the SAME task text as a structural subtype
regex (dead/cycle/complexity/coupling/blast/callers). When both match, the
current intended precedence in `_classify` is: **top_n_ranking wins**. The
ranking intent is treated as the more specific signal, and the structural
subtype it shadowed is recorded as a rejected arbitration reason
("structural_subtype: top_n_ranking is more specific").

The overlap surface is REAL, not hypothetical — e.g. the coupling regex
carries an explicit `top\\s+\\d+\\s+(most.)?imported` arm, so
"top 10 most imported files" matches BOTH `_is_top_n_ranking` AND
`structural_coupling`, yet routes to `top_n_ranking`. Other confirmed
overlaps: "top 5 most complex files" (complexity), "top 3 most coupled
files" (coupling), "biggest cycles in the codebase" (cycle).

WHY A FIXTURE, NOT A TIGHTER REGEX
-----------------------------------
A future wave may want to TIGHTEN `_TOP_N_RANKING_RE` so that a compound
prompt whose dominant intent is a structural subtype (e.g. "biggest cycles")
routes to that subtype instead of being swallowed by the broad top-N shape.
That is a *behavior change*, not a bug fix — it would flip the winner on the
overlap cases below. This fixture exists so that any such regex-tightening
edit trips these assertions and forces an EXPLICIT, ACCEPTED behavior change
rather than silently re-routing production prompts. **Do NOT relax these
assertions to make a regex edit pass; that is the regression this guard
exists to catch.** If the precedence is deliberately flipped, update the
expected winners here in the same change and call it out in the commit.

This test makes NO change to `_classify` or to any regex. It is purely a
snapshot of the current intended precedence, pinned from ground truth
captured against the live classifier (2026-06-21).

See also `test_compile_structural_subtype_order.py` (pins the intra-structural
subtype ordering) and `test_compile_probe_families_w11_w12_w13.py` (pins the
single "top 5 most-imported files" overlap case).
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _classify,
    _classify_structural_subtype,
    _is_top_n_ranking,
)

# The overlap lock-set: each prompt matches BOTH a top-N ranking shape AND a
# specific structural subtype regex. The expected_winner is the CURRENT
# intended precedence (top_n_ranking dominates). Subtype is asserted so the
# overlap is proven REAL — a future edit that narrows one regex must not
# silently make these "trivially" pass by removing the overlap entirely.
#
# Format: (prompt, expected_structural_subtype, expected_winner)
OVERLAP_CASES = [
    # The canonical W12 case (already pinned in test_compile_probe_families;
    # restated here so the full overlap surface lives in one lock-set).
    ("top 5 most-imported files", "structural_coupling", "top_n_ranking"),
    # coupling regex has an explicit `top \d+ (most.)?imported` arm, so this
    # is a genuine overlap, not a coincidental keyword hit.
    ("top 10 most imported files", "structural_coupling", "top_n_ranking"),
    ("top 3 most coupled files", "structural_coupling", "top_n_ranking"),
    # "most complex" appears in BOTH the top-N dimension list and the
    # structural_complexity regex.
    ("top 5 most complex files", "structural_complexity", "top_n_ranking"),
    # Shape A "biggest cycles" overlaps structural_cycle ("cycles in").
    ("biggest cycles in the codebase", "structural_cycle", "top_n_ranking"),
    # "callers" is BOTH a recognised top-N dimension and the trigger for the
    # structural_callers regex ("callers of <X>"), so "top N callers of <X>"
    # is a genuine overlap, not a coincidental keyword hit.
    ("top 10 callers of the function", "structural_callers", "top_n_ranking"),
    ("top 5 callers of handleSave", "structural_callers", "top_n_ranking"),
]


@pytest.mark.parametrize("prompt, expected_subtype, expected_winner", OVERLAP_CASES)
def test_overlap_prompt_routes_to_top_n_not_the_structural_subtype(prompt, expected_subtype, expected_winner):
    """The precedence guarantee this module exists to protect.

    On a compound prompt that matches BOTH a top-N shape and a structural
    subtype, `top_n_ranking` wins and the structural subtype is recorded as
    a rejected arbitration reason. If this fails because the winner changed,
    the precedence was flipped — that is an accepted behavior change only if
    the expected_winner in OVERLAP_CASES was deliberately updated.
    """
    # Prove the overlap is real: BOTH predicates independently match.
    assert _is_top_n_ranking(prompt), (
        f"{prompt!r} no longer matches a top-N ranking shape — the overlap "
        "this fixture pins has disappeared; do not silently drop the case."
    )
    assert _classify_structural_subtype(prompt) == expected_subtype, (
        f"{prompt!r} no longer matches structural subtype {expected_subtype!r}; "
        "the overlap has shifted — re-derive the expected subtype from ground truth."
    )
    # The precedence lock itself.
    winner, rejected = _classify(prompt)
    assert winner == expected_winner, (
        f"{prompt!r}: precedence flipped — expected {expected_winner!r}, "
        f"got {winner!r}. If this is a deliberate regex-tightening, update "
        "OVERLAP_CASES in the same change and call it out in the commit."
    )
    # The shadowed structural subtype must be recorded as the arbitration
    # trail, not silently discarded (the "rejected reasons" explain dump).
    assert any("top_n_ranking is more specific" in r for r in rejected), (
        f"{prompt!r}: expected a 'top_n_ranking is more specific' rejection "
        f"reason recording the shadowed subtype; got {rejected!r}."
    )


def test_pure_structural_prompt_without_top_n_shape_routes_to_subtype():
    """Boundary: when there is NO top-N ranking shape, the structural subtype
    wins unchallenged. Establishes that the lock above is not vacuous —
    structural routing still works when top_n_ranking does not fire."""
    prompt = "show me the import cycles"
    assert not _is_top_n_ranking(prompt)
    assert _classify_structural_subtype(prompt) == "structural_cycle"
    winner, rejected = _classify(prompt)
    assert winner == "structural_cycle"
    # No top_n arbitration reason because top_n never fired.
    assert not any("top_n_ranking" in r for r in rejected)


def test_pure_top_n_prompt_without_structural_overlap_routes_to_top_n():
    """Boundary: a top-N ranking whose dimension has NO matching structural
    subtype regex routes to top_n_ranking cleanly. Confirms the winner in the
    overlap cases is a precedence decision, not the only possible route."""
    # "churned" is a recognised top-N dimension but has no structural subtype
    # regex, so this is a pure top-N prompt with no structural overlap.
    prompt = "top 5 most churned files"
    assert _is_top_n_ranking(prompt)
    assert _classify_structural_subtype(prompt) is None
    winner, _rejected = _classify(prompt)
    assert winner == "top_n_ranking"
