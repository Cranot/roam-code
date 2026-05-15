"""ROADMAP A1 / W74 POC: ``_DESTRUCTIVE_TOOLS`` is a derived view of
``_TOOL_METADATA``.

Before this collapse, ``mcp_server.py`` carried 8 parallel split-brain
dicts that all classified tools by some axis (core / read-only /
destructive / task-required / task-optional / metadata / deprecated /
registered). The first one collapsed is ``_DESTRUCTIVE_TOOLS`` — picked
because:

1. Smallest blast radius (1 member: ``roam_mutate``; 2 consumer lines in
   ``mcp_server.py``; referenced from 1 test).
2. Cleanly derivable from a single bool flag on the decorator.
3. Stable semantic — what counts as "destructive" rarely changes.

The new source of truth is the ``destructive=True`` kwarg on the
``@_tool`` decorator. ``_DESTRUCTIVE_TOOLS`` is rebuilt at module-load
after every decorator has populated ``_TOOL_METADATA``.

These tests pin the migration so the legacy name keeps working AND the
derivation invariant is enforced — drift between the two would mean the
collapse has regressed.
"""

from __future__ import annotations


def test_destructive_tools_is_derived_from_metadata() -> None:
    """``_DESTRUCTIVE_TOOLS`` content == names where
    ``_TOOL_METADATA[name]['destructive']`` is True. Every name in one
    must appear in the other; no drift is allowed.
    """
    from roam.mcp_server import _DESTRUCTIVE_TOOLS, _TOOL_METADATA

    expected = frozenset(
        name for name, meta in _TOOL_METADATA.items() if meta.get("destructive", False)
    )
    assert _DESTRUCTIVE_TOOLS == expected, (
        "_DESTRUCTIVE_TOOLS is no longer derived from _TOOL_METADATA. "
        "If you added a destructive tool, set destructive=True on its "
        "@_tool decorator — do NOT add to a separate set."
    )


def test_destructive_tools_is_frozenset() -> None:
    """The derived view is a ``frozenset`` so callers cannot mutate the
    classification post-load. Mutation of the legacy ``set`` form would
    have silently broken the derivation invariant.
    """
    from roam.mcp_server import _DESTRUCTIVE_TOOLS

    assert isinstance(_DESTRUCTIVE_TOOLS, frozenset), (
        f"_DESTRUCTIVE_TOOLS must be a frozenset, got {type(_DESTRUCTIVE_TOOLS).__name__}"
    )


def test_destructive_decorator_kwarg_propagates_to_metadata() -> None:
    """The ``destructive=True`` kwarg on ``@_tool`` populates the
    metadata flag, which in turn drives the derived view. A regression
    here would mean the decorator-level marker has been disconnected
    from the source of truth.
    """
    import roam.mcp_server as mcp

    # roam_mutate is the pre-collapse destructive tool. Its metadata
    # entry must reflect destructive=True.
    meta = mcp._TOOL_METADATA.get("roam_mutate")
    assert meta is not None, (
        "_TOOL_METADATA missing roam_mutate — decorator did not register it. "
        "_TOOL_METADATA is populated unconditionally (even without fastmcp), "
        "so a missing entry indicates the decorator path is broken."
    )
    assert meta.get("destructive") is True, (
        "roam_mutate metadata lacks destructive=True. The @_tool(destructive=True) "
        "kwarg failed to propagate to _TOOL_METADATA."
    )


def test_pre_collapse_destructive_members_still_present() -> None:
    """Pin the pre-collapse hardcoded set ``{"roam_mutate"}`` is still
    present in the derived view. This catches regressions where a
    collapse refactor accidentally drops a destructive marker.
    """
    from roam.mcp_server import _DESTRUCTIVE_TOOLS

    # The pre-collapse hardcoded set, copied verbatim from mcp_server.py
    # line 301 (now replaced by the derived view at module-load
    # finalization). Treat this as the regression floor.
    PRE_COLLAPSE_DESTRUCTIVE = {"roam_mutate"}

    missing = PRE_COLLAPSE_DESTRUCTIVE - _DESTRUCTIVE_TOOLS
    assert not missing, (
        f"Destructive markers lost during collapse: {sorted(missing)}. "
        f"These tools were destructive before ROADMAP A1 but the derived "
        f"view does not include them now. Either mark their @_tool "
        f"decorator with destructive=True, or update this pin to reflect "
        f"an intentional change."
    )


def test_destructive_hint_in_annotations_matches_derived_view() -> None:
    """The MCP tool annotations expose ``destructiveHint`` on a
    per-tool basis. That hint must be True for every name in the
    derived view and False for every other tool. Drift here would mean
    the protocol-facing annotation has diverged from the catalog.
    """
    from roam.mcp_server import _DESTRUCTIVE_TOOLS, _tool_annotations

    # Test the live destructive tool sees destructiveHint=True
    for name in _DESTRUCTIVE_TOOLS:
        ann = _tool_annotations(name)
        assert ann.get("destructiveHint") is True, (
            f"_tool_annotations({name!r}) returned destructiveHint="
            f"{ann.get('destructiveHint')!r} — expected True. The annotation "
            f"builder is no longer reading from the same source as "
            f"_DESTRUCTIVE_TOOLS."
        )

    # Test a definitively non-destructive read-only tool sees destructiveHint=False
    non_destructive = _tool_annotations("roam_health")
    assert non_destructive.get("destructiveHint") is False, (
        "roam_health is read-only but _tool_annotations marked it "
        "destructiveHint=True. The annotation builder has misclassified it."
    )
