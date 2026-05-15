"""ROADMAP A1 / W108: ``_NON_READ_ONLY_TOOLS`` is a derived view of
``_TOOL_METADATA``.

Continues the dict-collapse ladder established by W74
(``_DESTRUCTIVE_TOOLS``), W99 (``_TASK_REQUIRED_TOOLS``), and W105
(``_TASK_OPTIONAL_TOOLS``). Before this collapse, ``mcp_server.py``
carried a hand-maintained 6-member ``_NON_READ_ONLY_TOOLS`` set in
parallel with ``_TOOL_METADATA`` — exactly the split-brain pattern the
ladder is dismantling.

The new source of truth is the ``read_only=False`` kwarg on the
``@_tool`` decorator. ``_NON_READ_ONLY_TOOLS`` is rebuilt at module-load
after every decorator has populated ``_TOOL_METADATA``.

These tests pin the migration so the legacy name keeps working AND the
derivation invariant is enforced — drift between the two would mean the
collapse has regressed.
"""

from __future__ import annotations


# The pre-collapse hardcoded set, copied verbatim from mcp_server.py line
# 294-301 (now replaced by the derived view at module-load finalization).
# Treat this as the regression floor.
PRE_COLLAPSE_NON_READ_ONLY = {
    "roam_annotate_symbol",
    "roam_ingest_trace",
    "roam_vuln_map",
    "roam_mutate",
    "roam_init",
    "roam_reindex",
}


def test_non_read_only_tools_is_frozenset() -> None:
    """The derived view is a ``frozenset`` so callers cannot mutate the
    classification post-load. Mutation of the legacy ``set`` form would
    have silently broken the derivation invariant.
    """
    from roam.mcp_server import _NON_READ_ONLY_TOOLS

    assert isinstance(_NON_READ_ONLY_TOOLS, frozenset), (
        f"_NON_READ_ONLY_TOOLS must be a frozenset, got "
        f"{type(_NON_READ_ONLY_TOOLS).__name__}"
    )


def test_non_read_only_tools_derived_from_metadata() -> None:
    """``_NON_READ_ONLY_TOOLS`` content == names where
    ``_TOOL_METADATA[name]['read_only']`` is False. Every name in one
    must appear in the other; no drift is allowed.
    """
    from roam.mcp_server import _NON_READ_ONLY_TOOLS, _TOOL_METADATA

    expected = frozenset(
        name for name, meta in _TOOL_METADATA.items() if not meta.get("read_only", True)
    )
    assert _NON_READ_ONLY_TOOLS == expected, (
        "_NON_READ_ONLY_TOOLS is no longer derived from _TOOL_METADATA. "
        "If you added a non-read-only tool, set read_only=False on its "
        "@_tool decorator — do NOT add to a separate set."
    )

    # And the bi-conditional, name-by-name, to catch single-tool drift
    # that a frozenset equality might silently mask if a name on one side
    # spelled differently happened to balance another mismatch.
    for name, meta in _TOOL_METADATA.items():
        in_set = name in _NON_READ_ONLY_TOOLS
        is_non_read_only = not meta.get("read_only", True)
        assert in_set == is_non_read_only, (
            f"Drift on {name!r}: in _NON_READ_ONLY_TOOLS={in_set}, "
            f"metadata read_only=False is {is_non_read_only}. The derived "
            f"view has diverged from _TOOL_METADATA."
        )


def test_non_read_only_tools_membership_unchanged() -> None:
    """Pin the pre-collapse hardcoded 6-member set is still present in
    the derived view. This catches regressions where a collapse refactor
    accidentally drops a read_only=False marker.
    """
    from roam.mcp_server import _NON_READ_ONLY_TOOLS

    missing = PRE_COLLAPSE_NON_READ_ONLY - _NON_READ_ONLY_TOOLS
    assert not missing, (
        f"Non-read-only markers lost during collapse: {sorted(missing)}. "
        f"These tools were marked non-read-only before ROADMAP A1 W108 but "
        f"the derived view does not include them now. Either mark their "
        f"@_tool decorator with read_only=False, or update this pin to "
        f"reflect an intentional change."
    )

    # And the reverse — if the derived view has GROWN, that's also worth
    # surfacing (so a new non-read-only tool gets a deliberate test
    # update rather than silently widening the set).
    extra = _NON_READ_ONLY_TOOLS - PRE_COLLAPSE_NON_READ_ONLY
    assert not extra, (
        f"_NON_READ_ONLY_TOOLS gained unexpected members: {sorted(extra)}. "
        f"If a new tool was intentionally marked read_only=False on its "
        f"@_tool decorator, update PRE_COLLAPSE_NON_READ_ONLY in this test "
        f"file to reflect the new floor."
    )


def test_default_read_only_is_true() -> None:
    """A tool declared without an explicit ``read_only`` kwarg gets the
    default ``True``. This pins the W108 contract: the decorator default
    is "safe", and only the 6 side-effectful tools opt out.
    """
    import roam.mcp_server as mcp

    # Verify the kwarg signature exposes read_only with default True.
    import inspect

    sig = inspect.signature(mcp._tool)
    param = sig.parameters.get("read_only")
    assert param is not None, (
        "_tool decorator factory is missing the read_only kwarg. "
        "Re-check the signature near line 832 of mcp_server.py."
    )
    assert param.default is True, (
        f"_tool(read_only=...) default must be True (the safe default — "
        f"only the 6 side-effectful tools opt out). Got {param.default!r}."
    )

    # Sample a well-known read-only tool and assert its metadata reflects
    # the default. ``roam_health`` was not in PRE_COLLAPSE_NON_READ_ONLY,
    # so it must metadata-read-only=True via the decorator default.
    meta = mcp._TOOL_METADATA.get("roam_health")
    assert meta is not None, (
        "_TOOL_METADATA missing roam_health — decorator did not register it. "
        "_TOOL_METADATA is populated unconditionally (even without fastmcp), "
        "so a missing entry indicates the decorator path is broken."
    )
    assert meta.get("read_only") is True, (
        "roam_health metadata lacks read_only=True. The @_tool default "
        "kwarg failed to propagate to _TOOL_METADATA."
    )


def test_read_only_decorator_kwarg_propagates_to_metadata() -> None:
    """The ``read_only=False`` kwarg on ``@_tool`` populates the
    metadata flag, which in turn drives the derived view. A regression
    here would mean the decorator-level marker has been disconnected
    from the source of truth.
    """
    import roam.mcp_server as mcp

    for name in PRE_COLLAPSE_NON_READ_ONLY:
        meta = mcp._TOOL_METADATA.get(name)
        assert meta is not None, (
            f"_TOOL_METADATA missing {name!r} — decorator did not register it."
        )
        assert meta.get("read_only") is False, (
            f"{name} metadata has read_only={meta.get('read_only')!r}, "
            f"expected False. The @_tool(read_only=False) kwarg failed to "
            f"propagate to _TOOL_METADATA."
        )


def test_read_only_hint_in_annotations_matches_derived_view() -> None:
    """The MCP tool annotations expose ``readOnlyHint`` on a per-tool
    basis. That hint must be False for every name in the derived view
    and True for every other tool. Drift here would mean the
    protocol-facing annotation has diverged from the catalog.
    """
    from roam.mcp_server import _NON_READ_ONLY_TOOLS, _tool_annotations

    # Test the live non-read-only tools see readOnlyHint=False
    for name in _NON_READ_ONLY_TOOLS:
        ann = _tool_annotations(name)
        assert ann.get("readOnlyHint") is False, (
            f"_tool_annotations({name!r}) returned readOnlyHint="
            f"{ann.get('readOnlyHint')!r} — expected False. The annotation "
            f"builder is no longer reading from the same source as "
            f"_NON_READ_ONLY_TOOLS."
        )

    # Test a definitively read-only tool sees readOnlyHint=True
    read_only_ann = _tool_annotations("roam_health")
    assert read_only_ann.get("readOnlyHint") is True, (
        "roam_health is read-only but _tool_annotations marked it "
        "readOnlyHint=False. The annotation builder has misclassified it."
    )
