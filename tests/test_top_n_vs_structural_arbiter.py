"""Behavior-lock: top_n_ranking (W12) vs structural-subtype precedence.

`roam compile --explain` showed that the BROAD W12 ranking regex
(`top|biggest|largest|most|highest|worst|hot|hottest|slow|slowest|deepest`
+ dimension) overlaps the structural-subtype regexes — a prompt like
"most complex functions" or "top 5 most-imported files" matches BOTH
`_is_top_n_ranking` AND `_classify_structural_subtype`. The intended
arbiter (compiler.py `_classify`, W12 block) is: when both fire,
top_n_ranking wins and the override is DISCLOSED in the rejected list.

This fixture pins that precedence as ground truth BEFORE any future
regex tuning. It changes no behavior — it only locks the current one.
If a later regex edit flips an overlap case, this test fails loudly and
the flip becomes an explicit, reviewed decision rather than a silent
drift. (Complements the single coupling case in
`test_compile_probe_families_w11_w12_w13.py::test_w12_precedence_over_structural_coupling`
by covering the full overlap matrix + the inverse direction.)
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _classify,
    _classify_structural_subtype,
    _is_top_n_ranking,
)

# The exact disclosure note the arbiter appends when top_n_ranking
# overrides a structural subtype. Pinned so the rejected[] audit trail
# stays informative (a silent override is a Pattern-1 "structured signal
# lost" regression).
_OVERRIDE_NOTE = "structural_subtype: top_n_ranking is more specific"

# Prompts that match BOTH _is_top_n_ranking AND a structural subtype.
# Captured empirically from the live classifier (2026-06-21). Each is a
# real overlap; the intended winner is top_n_ranking. (overlap_prompt, overridden_subtype)
_OVERLAP_BOTH_FIRE = [
    ("top 5 most-imported files", "structural_coupling"),
    ("most coupled file", "structural_coupling"),
    ("most complex functions", "structural_complexity"),
    ("top 3 most complex files", "structural_complexity"),
    # COMPOUND structural text — the exact precedence the target flagged.
    # This is the SAME prompt that `test_compile_structural_subtype_order.py
    # ::test_coupling_wins_over_blast_and_callers_on_compound_task` pins to
    # `structural_coupling` at the SUBTYPE level. At the full `_classify`
    # arbiter it routes to top_n_ranking instead — the "most callers" clause
    # trips the broad W12 ranking regex, and top_n_ranking is checked first.
    # Pinning both levels makes the cross-layer interaction explicit: the
    # subtype sentinel guarantees coupling wins AMONG structural subtypes,
    # this guarantees top_n_ranking wins OVER any of them when both fire.
    ("highest structural coupling (most callers / largest blast radius)", "structural_coupling"),
    # W12 dimension `callers`/`called` overlaps structural_callers. These read
    # as callers-of-a-symbol queries (compound structural intent — "who calls
    # compile_plan") but the broad W12 ranking regex wins. The exact "broad
    # top-N precedence over compound structural text" the target flagged, on a
    # subtype the original matrix (coupling + complexity) did not pin.
    # Verified empirically 2026-06-21: _is_top_n_ranking AND structural_callers
    # both fire; _classify resolves to top_n_ranking.
    ("most callers of compile_plan", "structural_callers"),
    ("top 5 callers of handleSave", "structural_callers"),
    # W12 dimension `cycles` overlaps structural_cycle. "largest cycles in the
    # imports" reads as a cycle query but routes to top_n_ranking — pin it so a
    # future W12 tightening that drops the `cycles` dimension surfaces as an
    # explicit, reviewed flip rather than silent drift.
    ("largest cycles in the imports", "structural_cycle"),
]


@pytest.mark.parametrize("prompt,overridden", _OVERLAP_BOTH_FIRE)
def test_top_n_wins_when_both_fire(prompt: str, overridden: str) -> None:
    """When the ranking regex and a structural subtype both match, the
    arbiter routes to top_n_ranking — never the structural subtype."""
    # Precondition: this prompt is genuinely an overlap (guards against the
    # fixture silently going stale if a regex stops matching).
    assert _is_top_n_ranking(prompt), f"fixture stale: {prompt!r} no longer top_n_ranking"
    assert _classify_structural_subtype(prompt) == overridden, f"fixture stale: {prompt!r} structural subtype changed"
    proc, rejected = _classify(prompt)
    assert proc == "top_n_ranking", f"expected top_n_ranking, got {proc!r} for {prompt!r}"
    # The override must be disclosed — never a silent drop of the structural signal.
    assert _OVERRIDE_NOTE in rejected, f"override of {overridden} not disclosed for {prompt!r}; rejected={rejected!r}"


def test_arbiter_invariant_over_overlap_corpus() -> None:
    """Invariant: for EVERY prompt where both the ranking helper and a
    structural subtype fire, _classify resolves to top_n_ranking. One
    assertion over the whole overlap corpus so a new overlap added to the
    fixture can never quietly route the wrong way."""
    for prompt, _ in _OVERLAP_BOTH_FIRE:
        assert _is_top_n_ranking(prompt) and _classify_structural_subtype(prompt)
        assert _classify(prompt)[0] == "top_n_ranking", prompt


# Inverse direction: a structural-only prompt (NO ranking dimension) must
# keep its structural route. This pins that the precedence rule does not
# over-fire — top_n_ranking only wins when it actually matches.
_STRUCTURAL_ONLY = [
    ("highest coupling files", "structural_coupling"),
    ("find unused public functions", "structural_dead"),
    ("are there cycles in the imports", "structural_cycle"),
    ("who calls compile_plan", "structural_callers"),
]


@pytest.mark.parametrize("prompt,expected", _STRUCTURAL_ONLY)
def test_structural_only_keeps_structural_route(prompt: str, expected: str) -> None:
    assert not _is_top_n_ranking(prompt), f"fixture stale: {prompt!r} now top_n_ranking"
    proc, _rejected = _classify(prompt)
    assert proc == expected, f"expected {expected!r}, got {proc!r} for {prompt!r}"
