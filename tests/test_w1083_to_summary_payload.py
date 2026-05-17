"""W1083 — ergonomic refinement: ``to_summary_payload`` extracts the
summary-ready subset of a ``structured_unknown_filter`` fragment.

Phase 3 of the W1077 helper. The 5 callsites that adopted the helper in
W1080/W1082 (``cmd_findings``, ``cmd_search``, ``cmd_endpoints`` x2,
``cmd_test_scaffold``) previously hand-stamped the same 4-5 keys into
their ``summary={...}`` literal. ``to_summary_payload`` centralises the
splice. These tests pin the contract.
"""

from __future__ import annotations

from roam.output.structured_unknowns import (
    structured_unknown_filter,
    to_summary_payload,
)

# ---------------------------------------------------------------------------
# Shape stability across the three scenarios callsites care about.
# ---------------------------------------------------------------------------


def test_to_summary_payload_unknown_with_close_match():
    """Typo within difflib cutoff: the payload carries the four shared
    fields PLUS ``did_you_mean`` (helper defaults).
    """
    frag = structured_unknown_filter(
        requested="clonez",  # typo of "clones"
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert frag is not None
    payload = to_summary_payload(frag)
    assert set(payload.keys()) == {
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
        "did_you_mean",
    }
    assert payload["state"] == "unknown_detector"
    assert payload["partial_success"] is True
    assert payload["requested_detector"] == "clonez"
    assert payload["known_detectors"] == ["clones", "complexity", "dead"]
    assert "clones" in payload["did_you_mean"]


def test_to_summary_payload_unknown_no_close_match_empty_did_you_mean_present():
    """Unknown value with NO close match (helper default): the
    ``did_you_mean`` field is present but empty. ``facts`` and
    ``verdict_suffix`` are NOT in the payload (they belong on
    ``agent_contract`` and the verdict, not on summary).
    """
    frag = structured_unknown_filter(
        requested="garblargleXYZ123",
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert frag is not None
    payload = to_summary_payload(frag)
    assert "did_you_mean" in payload
    assert payload["did_you_mean"] == []
    # Helper-owned fields stay OUT of the summary payload.
    assert "facts" not in payload
    assert "verdict_suffix" not in payload


def test_to_summary_payload_did_you_mean_omit_when_empty_kwarg_propagates():
    """When the fragment itself omits ``did_you_mean`` (W1081
    ``did_you_mean_omit_when_empty=True``), the payload omits it too —
    no synthetic empty list is added back."""
    frag = structured_unknown_filter(
        requested="garblargleXYZ123",
        known={"clones", "dead", "complexity"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
        did_you_mean_omit_when_empty=True,
    )
    assert frag is not None
    payload = to_summary_payload(frag)
    assert "did_you_mean" not in payload
    assert set(payload.keys()) == {
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
    }


# ---------------------------------------------------------------------------
# ``include_did_you_mean=False`` matches the cmd_search / cmd_test_scaffold
# choice of routing close-matches into the verdict suffix only.
# ---------------------------------------------------------------------------


def test_to_summary_payload_include_did_you_mean_false_drops_field():
    """``include_did_you_mean=False`` unconditionally drops the field
    even when the fragment carried it. Matches ``cmd_search`` /
    ``cmd_test_scaffold`` summary shape (suggestion lives in verdict
    suffix only)."""
    frag = structured_unknown_filter(
        requested="clonez",  # typo, would otherwise produce ``did_you_mean``
        known={"clones", "dead"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert frag is not None
    assert "did_you_mean" in frag  # sanity: helper produced the field
    payload = to_summary_payload(frag, include_did_you_mean=False)
    assert "did_you_mean" not in payload
    # Shared subset still present.
    assert set(payload.keys()) == {
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
    }


# ---------------------------------------------------------------------------
# Dynamic field-name discovery: the payload carries whatever pair of
# ``<requested_field>`` / ``<known_field>`` the caller named.
# ---------------------------------------------------------------------------


def test_to_summary_payload_carries_caller_named_dynamic_fields():
    """The payload preserves the caller-named field pair verbatim
    (``requested_method`` / ``observed_methods``, not the generic
    ``requested`` / ``known``)."""
    frag = structured_unknown_filter(
        requested="GARBAGE",
        known={"GET", "POST", "PUT"},
        state="unknown_method_filter",
        requested_field="requested_method",
        known_field="observed_methods",
        fact_anchor="methods",
        did_you_mean_omit_when_empty=True,
    )
    assert frag is not None
    payload = to_summary_payload(frag)
    assert "requested_method" in payload
    assert "observed_methods" in payload
    assert payload["requested_method"] == "GARBAGE"
    assert payload["observed_methods"] == ["GET", "POST", "PUT"]
    # Generic names never appear.
    assert "requested" not in payload
    assert "known" not in payload


# ---------------------------------------------------------------------------
# Insertion-order stability — fragment iterates in known order so the
# spliced summary stays byte-stable for JSON serialization.
# ---------------------------------------------------------------------------


def test_to_summary_payload_preserves_fragment_insertion_order():
    """``state`` always lands first, then ``partial_success``, then
    the dynamic pair, then ``did_you_mean`` (when present). Mirrors
    the fragment's own insertion-order discipline so JSON output is
    byte-stable across the migration."""
    frag = structured_unknown_filter(
        requested="clonez",
        known={"clones", "dead"},
        state="unknown_detector",
        requested_field="requested_detector",
        known_field="known_detectors",
        fact_anchor="detectors",
    )
    assert frag is not None
    payload = to_summary_payload(frag)
    keys = list(payload.keys())
    assert keys == [
        "state",
        "partial_success",
        "requested_detector",
        "known_detectors",
        "did_you_mean",
    ]


# ---------------------------------------------------------------------------
# ``partial_success`` derivation — always ``True`` on a fragment.
# ---------------------------------------------------------------------------


def test_to_summary_payload_partial_success_always_true_on_fragment():
    """The helper only returns a fragment for the unknown-filter
    branch, so ``partial_success`` is invariantly ``True``. The
    payload echoes it for the summary-level Pattern-1D disclosure
    contract."""
    for known, requested in [
        ({"a", "b"}, "zz"),  # no match
        ({"alpha", "beta"}, "alph"),  # close match
    ]:
        frag = structured_unknown_filter(
            requested=requested,
            known=known,
            state="unknown_x",
            requested_field="requested_x",
            known_field="known_xs",
            fact_anchor="symbols",
        )
        assert frag is not None
        payload = to_summary_payload(frag)
        assert payload["partial_success"] is True
