"""W1241 — substrate test for ``resolution_disclosure()`` Pattern-2 variant-D helper.

W324's cmd_annotate established the template; W1241 hoists the disclosure
shape into ``roam.output.formatter`` so flagship (W1242/W1243/W1244) and
bulk (W1245) Pattern-2 variant-D fixes share one source of truth. This
file locks in:

* the closed enumeration ``_RESOLUTION_KINDS`` (drift guard so adding a
  new kind is a deliberate source edit, not a runtime hack);
* each enum value produces the expected shape with the correct
  ``partial_success`` polarity (only ``symbol`` is fully-resolved);
* unknown kinds raise ``ValueError`` (no silent typo path);
* ``target`` and ``detail`` kwargs merge correctly, with reserved keys
  protected from override;
* returned dicts are fresh per call — mutation cannot leak to a future
  invocation.
"""

from __future__ import annotations

import pytest

from roam.output.formatter import _RESOLUTION_KINDS, resolution_disclosure

# ---------------------------------------------------------------------------
# Drift guard — locks the closed enum membership.
# ---------------------------------------------------------------------------


def test_resolution_kinds_membership_locked() -> None:
    """W1241 drift guard: ``_RESOLUTION_KINDS`` must match the explicit set.

    Adding a kind requires editing BOTH ``src/roam/output/formatter.py``
    AND this test in the same commit — prevents silent enum expansion
    that would break downstream consumers reading the closed vocabulary.
    """
    assert _RESOLUTION_KINDS == frozenset({"symbol", "file", "fuzzy", "unresolved"})
    assert isinstance(_RESOLUTION_KINDS, frozenset)


# ---------------------------------------------------------------------------
# Shape per enum value + partial_success polarity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resolution,expected_partial",
    [
        ("symbol", False),
        ("file", True),
        ("fuzzy", True),
        ("unresolved", True),
    ],
)
def test_each_kind_returns_expected_shape(resolution: str, expected_partial: bool) -> None:
    """Every closed-enum kind produces the canonical disclosure shape."""
    out = resolution_disclosure(resolution)  # type: ignore[arg-type]
    assert out == {"resolution": resolution, "partial_success": expected_partial}


def test_partial_success_false_only_for_symbol() -> None:
    """``partial_success=False`` is reserved for the exact-match tier."""
    assert resolution_disclosure("symbol")["partial_success"] is False
    for kind in ("file", "fuzzy", "unresolved"):
        assert resolution_disclosure(kind)["partial_success"] is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unknown-kind guard.
# ---------------------------------------------------------------------------


def test_unknown_kind_raises_value_error() -> None:
    """Unknown resolutions must fail loudly — silent typos are forbidden."""
    with pytest.raises(ValueError, match="resolution must be one of"):
        resolution_disclosure("partial")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        resolution_disclosure("")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        resolution_disclosure("SYMBOL")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# target + detail merge behaviour.
# ---------------------------------------------------------------------------


def test_target_and_detail_merge() -> None:
    """``target`` is echoed; ``detail`` merges non-reserved keys."""
    out = resolution_disclosure(
        "fuzzy",
        target="handleSave",
        detail={"matched_via": "LIKE", "candidates": 3},
    )
    assert out == {
        "resolution": "fuzzy",
        "partial_success": True,
        "target": "handleSave",
        "matched_via": "LIKE",
        "candidates": 3,
    }


def test_detail_cannot_override_reserved_keys() -> None:
    """``resolution`` / ``partial_success`` / ``target`` are write-protected."""
    out = resolution_disclosure(
        "symbol",
        target="handleSave",
        detail={
            "resolution": "fuzzy",  # attempt override — must be ignored
            "partial_success": True,  # attempt override — must be ignored
            "target": "OTHER",  # attempt override — must be ignored
            "note": "kept",  # non-reserved — merged
        },
    )
    assert out["resolution"] == "symbol"
    assert out["partial_success"] is False
    assert out["target"] == "handleSave"
    assert out["note"] == "kept"


def test_omitted_target_and_detail() -> None:
    """``target`` and ``detail`` are optional; minimal call returns 2 keys."""
    out = resolution_disclosure("unresolved")
    assert set(out.keys()) == {"resolution", "partial_success"}


# ---------------------------------------------------------------------------
# Mutation isolation — caller dict cannot leak into future calls.
# ---------------------------------------------------------------------------


def test_returned_dict_is_fresh_per_call() -> None:
    """Mutating a returned dict must not affect the next call's output."""
    first = resolution_disclosure("symbol", target="foo")
    first["mutated"] = "should-not-leak"
    second = resolution_disclosure("symbol", target="foo")
    assert "mutated" not in second
    assert first is not second


# ---------------------------------------------------------------------------
# W1270 — reserved-key collision warning via warnings_out.
# ---------------------------------------------------------------------------


def test_reserved_key_collision_emits_warning_when_opted_in() -> None:
    """W1270: passing a reserved key via ``detail`` while opting into
    ``warnings_out`` must surface a structured warning per dropped key.

    Pre-W1270 the helper silently dropped reserved-key collisions — a
    Pattern-2 silent-fallback violation. With ``warnings_out`` supplied,
    each dropped key produces a canonical warning naming the key and the
    recommended fix (OR-combine BEFORE calling the helper).
    """
    warnings: list[str] = []
    out = resolution_disclosure(
        "fuzzy",
        target="handleSave",
        detail={
            "resolution": "symbol",  # reserved — drop + warn
            "partial_success": False,  # reserved — drop + warn
            "candidates": 3,  # non-reserved — kept silently
        },
        warnings_out=warnings,
    )
    # Behaviour parity: reserved keys still dropped, non-reserved still merged.
    assert out["resolution"] == "fuzzy"
    assert out["partial_success"] is True
    assert out["candidates"] == 3
    # New disclosure: one warning per dropped reserved key.
    assert len(warnings) == 2
    assert any("'resolution'" in w for w in warnings)
    assert any("'partial_success'" in w for w in warnings)
    for w in warnings:
        assert w.startswith("resolution_disclosure: detail contained reserved key ")
        assert "OR-combine BEFORE calling helper" in w


def test_reserved_key_collision_silent_when_warnings_out_none() -> None:
    """W1270: legacy callers (``warnings_out=None``) keep byte-identical
    silent-drop behaviour — no observable change from pre-W1270.
    """
    out = resolution_disclosure(
        "symbol",
        detail={"resolution": "fuzzy", "target": "OTHER", "note": "kept"},
        # warnings_out omitted → defaults to None
    )
    assert out == {
        "resolution": "symbol",
        "partial_success": False,
        "note": "kept",
    }


def test_warnings_out_unused_when_no_reserved_collision() -> None:
    """W1270: opting into ``warnings_out`` is a no-op when ``detail`` is
    clean — the helper only appends when a reserved key is actually dropped.
    """
    warnings: list[str] = []
    resolution_disclosure(
        "file",
        target="src/foo.py",
        detail={"matched_via": "path", "candidates": 1},
        warnings_out=warnings,
    )
    assert warnings == []
