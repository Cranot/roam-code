"""W1077 — pure-logic tests for ``structured_unknown_filter``.

Phase 1 ships the helper UNUSED (no callsite migrations yet — that's
W1079 Phase 2). These tests pin the shape contract so the migration
wave can rewrite all 5 existing callsites against a known-good
fragment shape.
"""

from __future__ import annotations

import pytest

from roam.output.structured_unknowns import structured_unknown_filter

# ---------------------------------------------------------------------------
# Happy path: ``requested in known`` returns None (no envelope-fragment).
# ---------------------------------------------------------------------------


def test_requested_in_known_returns_none():
    """When the user-supplied value is in the closed vocabulary, the
    helper returns ``None`` — caller proceeds to the normal query path."""
    result = structured_unknown_filter(
        requested="clones",
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert result is None


def test_requested_in_known_list_returns_none():
    """``known`` may be a ``list`` (not just a ``set``)."""
    result = structured_unknown_filter(
        requested="fn",
        known=["fn", "cls", "meth", "function", "class", "method"],
        state="unknown_kind",
        requested_field="requested_kind",
        known_field="known_kinds",
        fact_anchor="kinds",
    )
    assert result is None


# ---------------------------------------------------------------------------
# Unknown value: returns dict fragment with the documented shape.
# ---------------------------------------------------------------------------


def test_unknown_with_close_match_populates_did_you_mean():
    """Typo within difflib cutoff yields a ``did_you_mean`` suggestion."""
    result = structured_unknown_filter(
        requested="clonez",  # typo of "clones"
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert result is not None
    assert result["state"] == "unknown_detector"
    assert result["partial_success"] is True
    assert result["requested_detector"] == "clonez"
    assert result["known_detectors"] == ["clones", "complexity", "dead"]
    assert "clones" in result["did_you_mean"]
    # verdict_suffix carries the human-facing "Did you mean: …?" tail
    assert "clones" in result["verdict_suffix"]
    assert result["verdict_suffix"].startswith(" Did you mean:")


def test_unknown_no_close_match_empty_did_you_mean():
    """A value far from every known entry returns an empty
    ``did_you_mean`` list and an empty ``verdict_suffix``."""
    result = structured_unknown_filter(
        requested="garblargleXYZ123",
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert result is not None
    assert result["did_you_mean"] == []
    assert result["verdict_suffix"] == ""


def test_known_field_is_sorted_deduped():
    """``known`` is sorted + deduped on the way out, regardless of input
    order or duplicates. The disclosure surface is deterministic."""
    result = structured_unknown_filter(
        requested="zzz",
        known=["beta", "alpha", "beta", "gamma", "alpha"],
        state="unknown_x",
        requested_field="requested_x",
        known_field="known_xs",
        fact_anchor="symbols",
    )
    assert result is not None
    assert result["known_xs"] == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# Parameter passthrough: cutoff + n_suggestions.
# ---------------------------------------------------------------------------


def test_cutoff_respected_strict():
    """A very strict cutoff (0.95) suppresses borderline matches that the
    default 0.6 cutoff would surface."""
    # ``cloned`` vs ``clones`` ratio ~0.83 — passes 0.6, fails 0.95.
    lenient = structured_unknown_filter(
        requested="cloned",
        known={"clones", "dead"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
        cutoff=0.6,
    )
    assert lenient is not None
    assert lenient["did_you_mean"] == ["clones"]

    strict = structured_unknown_filter(
        requested="cloned",
        known={"clones", "dead"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
        cutoff=0.95,
    )
    assert strict is not None
    assert strict["did_you_mean"] == []


def test_n_suggestions_caps_did_you_mean_length():
    """``n_suggestions`` upper-bounds the suggestion list."""
    # All four are close-ish to "func"; default n=2 caps at 2.
    result = structured_unknown_filter(
        requested="func",
        known={"funk", "fund", "funcy", "funny"},
        state="unknown_kind",
        requested_field="requested_kind",
        known_field="known_kinds",
        fact_anchor="kinds",
        n_suggestions=2,
        cutoff=0.5,
    )
    assert result is not None
    assert len(result["did_you_mean"]) <= 2

    result3 = structured_unknown_filter(
        requested="func",
        known={"funk", "fund", "funcy", "funny"},
        state="unknown_kind",
        requested_field="requested_kind",
        known_field="known_kinds",
        fact_anchor="kinds",
        n_suggestions=3,
        cutoff=0.5,
    )
    assert result3 is not None
    assert len(result3["did_you_mean"]) <= 3


# ---------------------------------------------------------------------------
# LAW 4: facts terminate on the concrete-noun anchor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("anchor", ["detectors", "kinds", "frameworks", "methods", "symbols"])
def test_facts_terminal_anchored_on_fact_anchor(anchor):
    """Each fact's terminal token (last word, punctuation stripped)
    matches ``fact_anchor``. Mirrors the LAW 4 lint at
    ``tests/test_law4_lint.py``."""
    result = structured_unknown_filter(
        requested="zzz",
        known=["alpha", "beta"],
        state=f"unknown_{anchor[:-1]}",
        requested_field=f"requested_{anchor[:-1]}",
        known_field=f"known_{anchor}",
        fact_anchor=anchor,
    )
    assert result is not None
    facts = result["facts"]
    # First two facts ALWAYS anchor on the fact_anchor terminal.
    for fact in facts[:2]:
        terminal = fact.strip().split()[-1].rstrip(",.;:!?)")
        assert terminal == anchor, f"fact {fact!r} terminal {terminal!r} not anchored on {anchor!r}"


def test_facts_first_two_anchored_even_with_close_match():
    """Even when a close-match third fact is appended, the first two
    facts stay anchored on the LAW 4 terminal — the migration callsites
    rely on this so the lint stays predictable."""
    result = structured_unknown_filter(
        requested="clonez",
        known={"clones", "dead"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert result is not None
    facts = result["facts"]
    assert len(facts) == 3  # base 2 + close-match tail
    for fact in facts[:2]:
        terminal = fact.strip().split()[-1].rstrip(",.;:!?)")
        assert terminal == "detectors"


# ---------------------------------------------------------------------------
# Shape contract: returned dict has exactly the documented keys.
# ---------------------------------------------------------------------------


def test_fragment_keys_are_exactly_the_documented_set():
    """The returned fragment exposes the documented seven keys — no
    more, no less. Callers splice these into a larger summary, so the
    contract must be tight."""
    result = structured_unknown_filter(
        requested="zzz",
        known=["alpha", "beta"],
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert result is not None
    assert set(result.keys()) == {
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
        "did_you_mean",
        "facts",
        "verdict_suffix",
    }


def test_requested_and_known_field_names_are_caller_controlled():
    """The ``requested_field`` and ``known_field`` parameters MUST land
    in the fragment under exactly those names — this is how callsites
    keep their existing per-command vocabulary (``requested_detector``
    vs ``requested_kind`` vs ``requested_framework``)."""
    result = structured_unknown_filter(
        requested="zzz",
        known=["alpha"],
        state="unknown_method_filter",
        requested_field="requested_method",
        known_field="observed_methods",
        fact_anchor="methods",
    )
    assert result is not None
    assert "requested_method" in result
    assert "observed_methods" in result
    # Inverse: the generic names are NOT present.
    assert "requested" not in result
    assert "known" not in result


# ---------------------------------------------------------------------------
# W1081 refinement 1: ``did_you_mean_omit_when_empty`` kwarg.
# ---------------------------------------------------------------------------


def test_did_you_mean_omit_when_empty_no_match_omits_field():
    """With ``did_you_mean_omit_when_empty=True`` AND no close match, the
    ``did_you_mean`` field is ABSENT from the fragment — callers can
    splice the whole fragment without conditional logic (W1081)."""
    result = structured_unknown_filter(
        requested="garblargleXYZ123",
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
        did_you_mean_omit_when_empty=True,
    )
    assert result is not None
    assert "did_you_mean" not in result
    # All other documented fields are still present.
    assert set(result.keys()) == {
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
        "facts",
        "verdict_suffix",
    }


def test_did_you_mean_omit_when_empty_with_match_still_present():
    """With ``did_you_mean_omit_when_empty=True`` AND a close match
    found, the ``did_you_mean`` field IS present (omit-when-empty only
    fires on empty results, never on populated ones)."""
    result = structured_unknown_filter(
        requested="clonez",  # typo of "clones"
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
        did_you_mean_omit_when_empty=True,
    )
    assert result is not None
    assert "did_you_mean" in result
    assert "clones" in result["did_you_mean"]


# ---------------------------------------------------------------------------
# W1081 refinement 2: second-fact override for substring semantics.
# ---------------------------------------------------------------------------


def test_requested_disclosure_verb_overrides_connector():
    """``requested_disclosure_verb`` overrides the connector phrase in
    fact 2 — lets callers disclose substring semantics natively
    (``"'flask' substring not in observed frameworks"``) instead of
    patching ``facts[1]`` in place (W1081)."""
    result = structured_unknown_filter(
        requested="flask",
        known={"django", "fastapi", "express"},
        state="unknown_framework_filter",
        requested_field="requested_framework",
        known_field="observed_frameworks",
        fact_anchor="frameworks",
        requested_disclosure_verb="substring not in observed",
    )
    assert result is not None
    second_fact = result["facts"][1]
    assert "substring not in observed" in second_fact
    assert second_fact.startswith("'flask'")
    # LAW 4 terminal preserved (anchor is still ``frameworks``).
    terminal = second_fact.strip().split()[-1].rstrip(",.;:!?)")
    assert terminal == "frameworks"


def test_known_disclosure_label_overrides_tail_law4_preserved():
    """``known_disclosure_label`` overrides the ``"known {fact_anchor}"``
    tail in fact 2; LAW 4 terminal anchor stays intact when the label
    terminates on a concrete-noun anchor (caller responsibility)."""
    result = structured_unknown_filter(
        requested="flask",
        known={"django", "fastapi"},
        state="unknown_framework_filter",
        requested_field="requested_framework",
        known_field="observed_frameworks",
        fact_anchor="frameworks",
        requested_disclosure_verb="substring not in",
        known_disclosure_label="observed frameworks",
    )
    assert result is not None
    second_fact = result["facts"][1]
    assert second_fact == "'flask' substring not in observed frameworks"
    # First fact still terminates on ``fact_anchor`` for LAW 4.
    first_terminal = result["facts"][0].strip().split()[-1].rstrip(",.;:!?)")
    assert first_terminal == "frameworks"
    # Second fact terminal is the label's terminal — anchor preserved.
    second_terminal = second_fact.strip().split()[-1].rstrip(",.;:!?)")
    assert second_terminal == "frameworks"
