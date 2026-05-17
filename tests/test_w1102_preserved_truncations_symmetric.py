"""W1102: ``strip_list_payloads`` emits ``summary.preserved_list_truncations`` unconditionally.

Symmetric with the W1101 precedent for ``list_counts: {}`` and the W1006
precedent for ``redactions: []``. The asymmetric emission previously meant
an agent could not distinguish "envelope had no preserved-list clips" from
"envelope wasn't ``strip_list_payloads``-processed at all". W1102 closes
that absence-vs-empty disambiguation gap for the preserved-truncations
disclosure.

Placement: INSIDE ``summary`` (not top-level), mirroring the per-field
``<field>_truncated`` siblings that already live top-level — the summary
entry is the structured roll-up, the top-level siblings are the
per-field-cardinality byte-cheap form.

Shape: ``{field_name: dropped_count}`` — preserved from the internal
tracker, no semantic change. ``dropped_count`` is the number of entries
removed by the ``_ALWAYS_PRESERVED_LIST_MAX`` cap.
"""

from __future__ import annotations

from roam.output.formatter import json_envelope, strip_list_payloads


def _envelope(**extra) -> dict:
    return json_envelope("test", summary={"verdict": "ok"}, **extra)


def test_no_preservations_emits_empty_dict():
    """Baseline: no preserved-list clips -> empty dict inside summary."""
    env = _envelope(verdict_extra="scalar-only")
    result = strip_list_payloads(env)
    assert "preserved_list_truncations" in result["summary"]
    assert result["summary"]["preserved_list_truncations"] == {}
    # The boolean derivation MUST stay correct.
    assert result["summary"].get("truncated") is not True


def test_short_preserved_list_no_entry():
    """A preserved list under the cap round-trips without a clip entry."""
    env = _envelope(errors=["e1", "e2", "e3"])
    result = strip_list_payloads(env)
    assert result["summary"]["preserved_list_truncations"] == {}
    # And the list itself survives.
    assert result.get("errors") == ["e1", "e2", "e3"]
    assert result["summary"].get("truncated") is not True


def test_one_field_clipped_surfaces_entry():
    """One preserved list past the cap -> entry with dropped count."""
    env = _envelope(errors=[f"e{i}" for i in range(15)])
    result = strip_list_payloads(env)
    assert result["summary"]["preserved_list_truncations"] == {"errors": 5}
    # Per-field sibling still emitted at top-level.
    assert result.get("errors_truncated") == 5
    # And the boolean derivation still flips.
    assert result["summary"]["truncated"] is True


def test_multiple_fields_clipped_all_present():
    """Multiple preserved lists past the cap -> all entries present."""
    env = _envelope(
        errors=[f"e{i}" for i in range(13)],
        warnings_out=[f"w{i}" for i in range(12)],
        redactions=[f"r{i}" for i in range(14)],
    )
    result = strip_list_payloads(env)
    assert result["summary"]["preserved_list_truncations"] == {
        "errors": 3,
        "warnings_out": 2,
        "redactions": 4,
    }
    assert result["summary"]["truncated"] is True


def test_truncated_flag_still_derives_from_preservation_clip():
    """The boolean ``summary.truncated`` flag stays driven by ANY clip
    OR any dropped non-preserved list (W1006/W1007 invariant). W1102
    does not change this derivation.
    """
    # Case A: only a preserved-list clip.
    env_a = _envelope(errors=[f"e{i}" for i in range(11)])
    result_a = strip_list_payloads(env_a)
    assert result_a["summary"]["truncated"] is True
    assert result_a["summary"]["preserved_list_truncations"] == {"errors": 1}

    # Case B: only a non-preserved list dropped.
    env_b = _envelope(findings=[{"id": 1}, {"id": 2}])
    result_b = strip_list_payloads(env_b)
    assert result_b["summary"]["truncated"] is True
    # Preserved-truncations is still emitted, empty.
    assert result_b["summary"]["preserved_list_truncations"] == {}

    # Case C: nothing dropped/clipped.
    env_c = _envelope()
    result_c = strip_list_payloads(env_c)
    assert result_c["summary"].get("truncated") is not True
    assert result_c["summary"]["preserved_list_truncations"] == {}


def test_w1007_preserve_dont_drop_invariant_holds():
    """W1102 must not change the preserve-don't-drop semantics: a clipped
    preserved list still keeps its first N entries AND the top-level
    ``<field>_truncated`` sibling AND the original list is not dropped.
    """
    env = _envelope(errors=[f"e{i}" for i in range(20)])
    result = strip_list_payloads(env)
    # First 10 kept.
    assert result["errors"] == [f"e{i}" for i in range(10)]
    # Top-level sibling.
    assert result["errors_truncated"] == 10
    # W1102 summary entry mirrors.
    assert result["summary"]["preserved_list_truncations"] == {"errors": 10}
