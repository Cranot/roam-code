"""W1101: ``strip_list_payloads`` emits ``list_counts: {}`` unconditionally.

Symmetric with the W1006 precedent for ``redactions: []``. The asymmetric
emission previously meant an agent could not distinguish "envelope has no
dropped lists" from "envelope wasn't ``strip_list_payloads``-processed at
all". W1101 closes that absence-vs-empty disambiguation gap.

Trade-off: 14-20 bytes of additional envelope cost per stripped envelope
(``"list_counts":{}``). Considered acceptable — the disclosure semantic
is stronger than the byte savings.
"""

from __future__ import annotations

import json

from roam.output.formatter import json_envelope, strip_list_payloads


def _envelope(**extra) -> dict:
    return json_envelope("test", summary={"verdict": "ok"}, **extra)


def test_no_dropped_lists_emits_empty_dict():
    """Baseline: scalar-only envelope -> ``list_counts: {}`` is present.

    This is the case W1008 previously elided. W1101 makes it present.
    """
    env = _envelope(verdict_extra="scalar-only")
    result = strip_list_payloads(env)
    assert "list_counts" in result
    assert result["list_counts"] == {}


def test_one_list_dropped_surfaces_entry():
    """One non-preserved list dropped -> ``list_counts`` carries it."""
    env = _envelope(findings=[{"id": i} for i in range(4)])
    result = strip_list_payloads(env)
    assert result["list_counts"] == {"findings": 4}
    # The list itself is gone.
    assert "findings" not in result


def test_multiple_lists_dropped_all_present():
    """Multiple non-preserved lists -> all entries present in ``list_counts``."""
    env = _envelope(
        findings=[{"id": i} for i in range(8)],
        hotspots=[{"id": i} for i in range(3)],
        cycles=[{"id": i} for i in range(2)],
    )
    result = strip_list_payloads(env)
    assert result["list_counts"] == {"findings": 8, "hotspots": 3, "cycles": 2}


def test_only_preserved_lists_emits_empty_dict():
    """Preserved lists (warnings_out / errors / redactions / agent_contract)
    are NOT counted in ``list_counts``. When the envelope has ONLY
    preserved lists and no non-preserved lists, ``list_counts: {}``
    is still emitted (W1101 symmetry).
    """
    env = _envelope(
        warnings_out=["w1"],
        errors=["e1"],
        redactions=["r1", "r2"],
    )
    result = strip_list_payloads(env)
    assert result["list_counts"] == {}
    # Preserved fields stay.
    assert result.get("warnings_out") == ["w1"]
    assert result.get("errors") == ["e1"]
    assert result.get("redactions") == ["r1", "r2"]


def test_empty_dropped_list_still_counted_as_zero():
    """An empty non-preserved list is counted as 0 (W1008 invariant);
    W1101 adds: the ``list_counts`` key is always present regardless.
    """
    env = _envelope(findings=[])
    result = strip_list_payloads(env)
    assert result["list_counts"] == {"findings": 0}


def test_byte_cost_is_acceptable():
    """W1101: empty ``list_counts: {}`` costs ~16 bytes wire-on.
    Acceptable per the symmetry value calculus.
    """
    env = _envelope(verdict_extra="scalar-only")
    result = strip_list_payloads(env)
    rendered = json.dumps(result, separators=(",", ":"))
    # The substring "list_counts":{} is exactly 16 bytes.
    assert '"list_counts":{}' in rendered
    # Compare against a baseline envelope WITHOUT list_counts: the
    # marginal cost of the W1101 emission must be exactly the 16-byte
    # substring above (plus its leading comma when inserted between keys).
    baseline = dict(result)
    baseline.pop("list_counts")
    baseline_rendered = json.dumps(baseline, separators=(",", ":"))
    delta = len(rendered) - len(baseline_rendered)
    # ',"list_counts":{}' = 17 bytes when appended mid-object.
    assert delta == 17


def test_symmetry_with_redactions_precedent():
    """W1006 set the precedent: ``redactions: []`` is emitted when the
    producer chose to set it explicitly. W1101 mirrors the shape on
    ``list_counts: {}`` -- both are absence-vs-empty disambiguation
    fields. This test asserts they coexist cleanly when one or both
    are empty.
    """
    env = _envelope(redactions=[])
    result = strip_list_payloads(env)
    assert result.get("redactions") == []
    assert result["list_counts"] == {}
