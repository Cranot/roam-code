"""ROADMAP A1 / W113: ``_NON_IDEMPOTENT_TOOLS`` is a derived view of
``_TOOL_METADATA``.

Continues the dict-collapse ladder established by W74
(``_DESTRUCTIVE_TOOLS``), W99 (``_TASK_REQUIRED_TOOLS``), W105
(``_TASK_OPTIONAL_TOOLS``), and W108 (``_NON_READ_ONLY_TOOLS``). Before
this collapse, ``_NON_IDEMPOTENT_TOOLS`` was derived FROM
``_NON_READ_ONLY_TOOLS`` (literally ``_NON_READ_ONLY_TOOLS.copy()``)
rather than from its own first-class decorator kwarg. That hid the
semantic distinction between "read-only" and "idempotent": in current
data they coincide (every non-read-only tool is also non-idempotent),
but the axes are logically independent — a read-only tool can be
non-idempotent if it returns a fresh UUID or timestamp per call.

The new source of truth is the ``idempotent=False`` kwarg on the
``@_tool`` decorator (``True`` default — the safe assumption).
``_NON_IDEMPOTENT_TOOLS`` is rebuilt at module-load after every
decorator has populated ``_TOOL_METADATA``.

These tests pin the migration so the legacy name keeps working AND the
derivation invariant is enforced — drift between the two would mean the
collapse has regressed.
"""

from __future__ import annotations

# The pre-collapse hardcoded set. Before W113, ``_NON_IDEMPOTENT_TOOLS``
# was a ``.copy()`` of ``_NON_READ_ONLY_TOOLS`` (W108) — same 6 tools.
# Treat this as the regression floor.
PRE_COLLAPSE_NON_IDEMPOTENT = {
    "roam_annotate_symbol",
    "roam_ingest_trace",
    "roam_vuln_map",
    "roam_mutate",
    "roam_init",
    "roam_reindex",
}


def test_non_idempotent_tools_is_frozenset() -> None:
    """The derived view is a ``frozenset`` so callers cannot mutate the
    classification post-load. Mutation of the legacy ``set`` form would
    have silently broken the derivation invariant.
    """
    from roam.mcp_server import _NON_IDEMPOTENT_TOOLS

    assert isinstance(_NON_IDEMPOTENT_TOOLS, frozenset), (
        f"_NON_IDEMPOTENT_TOOLS must be a frozenset, got {type(_NON_IDEMPOTENT_TOOLS).__name__}"
    )


def test_non_idempotent_tools_derived_from_metadata() -> None:
    """``_NON_IDEMPOTENT_TOOLS`` content == names where
    ``_TOOL_METADATA[name]['idempotent']`` is False. Every name in one
    must appear in the other; no drift is allowed.
    """
    from roam.mcp_server import _NON_IDEMPOTENT_TOOLS, _TOOL_METADATA

    expected = frozenset(name for name, meta in _TOOL_METADATA.items() if not meta.get("idempotent", True))
    assert _NON_IDEMPOTENT_TOOLS == expected, (
        "_NON_IDEMPOTENT_TOOLS is no longer derived from _TOOL_METADATA. "
        "If you added a non-idempotent tool, set idempotent=False on its "
        "@_tool decorator — do NOT add to a separate set."
    )

    # And the bi-conditional, name-by-name, to catch single-tool drift
    # that a frozenset equality might silently mask if a name on one side
    # spelled differently happened to balance another mismatch.
    for name, meta in _TOOL_METADATA.items():
        in_set = name in _NON_IDEMPOTENT_TOOLS
        is_non_idempotent = not meta.get("idempotent", True)
        assert in_set == is_non_idempotent, (
            f"Drift on {name!r}: in _NON_IDEMPOTENT_TOOLS={in_set}, "
            f"metadata idempotent=False is {is_non_idempotent}. The derived "
            f"view has diverged from _TOOL_METADATA."
        )


def test_non_idempotent_tools_membership_pinned() -> None:
    """Pin the pre-collapse hardcoded 6-member set is still present in
    the derived view. This catches regressions where a collapse refactor
    accidentally drops an idempotent=False marker.
    """
    from roam.mcp_server import _NON_IDEMPOTENT_TOOLS

    missing = PRE_COLLAPSE_NON_IDEMPOTENT - _NON_IDEMPOTENT_TOOLS
    assert not missing, (
        f"Non-idempotent markers lost during collapse: {sorted(missing)}. "
        f"These tools were marked non-idempotent before ROADMAP A1 W113 but "
        f"the derived view does not include them now. Either mark their "
        f"@_tool decorator with idempotent=False, or update this pin to "
        f"reflect an intentional change."
    )

    # And the reverse — if the derived view has GROWN, that's also worth
    # surfacing (so a new non-idempotent tool gets a deliberate test
    # update rather than silently widening the set).
    extra = _NON_IDEMPOTENT_TOOLS - PRE_COLLAPSE_NON_IDEMPOTENT
    assert not extra, (
        f"_NON_IDEMPOTENT_TOOLS gained unexpected members: {sorted(extra)}. "
        f"If a new tool was intentionally marked idempotent=False on its "
        f"@_tool decorator, update PRE_COLLAPSE_NON_IDEMPOTENT in this test "
        f"file to reflect the new floor."
    )


def test_default_idempotent_is_true() -> None:
    """A tool declared without an explicit ``idempotent`` kwarg gets the
    default ``True``. This pins the W113 contract: the decorator default
    is "safe" (idempotent), and only side-effectful tools opt out.
    """
    import inspect

    import roam.mcp_server as mcp

    # Verify the kwarg signature exposes idempotent with default True.
    sig = inspect.signature(mcp._tool)
    param = sig.parameters.get("idempotent")
    assert param is not None, (
        "_tool decorator factory is missing the idempotent kwarg. "
        "Re-check the signature near line 833 of mcp_server.py."
    )
    assert param.default is True, (
        f"_tool(idempotent=...) default must be True (the safe default — "
        f"only the 6 side-effectful tools opt out). Got {param.default!r}."
    )

    # Sample a well-known idempotent tool and assert its metadata reflects
    # the default. ``roam_health`` is not in PRE_COLLAPSE_NON_IDEMPOTENT,
    # so it must have metadata idempotent=True via the decorator default.
    meta = mcp._TOOL_METADATA.get("roam_health")
    assert meta is not None, (
        "_TOOL_METADATA missing roam_health — decorator did not register it. "
        "_TOOL_METADATA is populated unconditionally (even without fastmcp), "
        "so a missing entry indicates the decorator path is broken."
    )
    assert meta.get("idempotent") is True, (
        "roam_health metadata lacks idempotent=True. The @_tool default kwarg failed to propagate to _TOOL_METADATA."
    )


def test_idempotent_decorator_kwarg_propagates_to_metadata() -> None:
    """The ``idempotent=False`` kwarg on ``@_tool`` populates the
    metadata flag, which in turn drives the derived view. A regression
    here would mean the decorator-level marker has been disconnected
    from the source of truth.
    """
    import roam.mcp_server as mcp

    for name in PRE_COLLAPSE_NON_IDEMPOTENT:
        meta = mcp._TOOL_METADATA.get(name)
        assert meta is not None, f"_TOOL_METADATA missing {name!r} — decorator did not register it."
        assert meta.get("idempotent") is False, (
            f"{name} metadata has idempotent={meta.get('idempotent')!r}, "
            f"expected False. The @_tool(idempotent=False) kwarg failed to "
            f"propagate to _TOOL_METADATA."
        )


def test_idempotent_hint_in_annotations_matches_derived_view() -> None:
    """The MCP tool annotations expose ``idempotentHint`` on a per-tool
    basis. That hint must be False for every name in the derived view
    and True for every other tool. Drift here would mean the
    protocol-facing annotation has diverged from the catalog.
    """
    from roam.mcp_server import _NON_IDEMPOTENT_TOOLS, _tool_annotations

    # Test the live non-idempotent tools see idempotentHint=False
    for name in _NON_IDEMPOTENT_TOOLS:
        ann = _tool_annotations(name)
        assert ann.get("idempotentHint") is False, (
            f"_tool_annotations({name!r}) returned idempotentHint="
            f"{ann.get('idempotentHint')!r} — expected False. The annotation "
            f"builder is no longer reading from the same source as "
            f"_NON_IDEMPOTENT_TOOLS."
        )

    # Test a definitively idempotent tool sees idempotentHint=True
    idempotent_ann = _tool_annotations("roam_health")
    assert idempotent_ann.get("idempotentHint") is True, (
        "roam_health is idempotent but _tool_annotations marked it "
        "idempotentHint=False. The annotation builder has misclassified it."
    )


def test_idempotent_axis_independent_of_read_only() -> None:
    """W113 decoupled ``idempotent`` from ``read_only``: each is a
    separate first-class kwarg on ``@_tool`` and populates its own slot
    in ``_TOOL_METADATA``. In the CURRENT data the two axes coincide
    (every non-read-only tool happens to be non-idempotent), but the
    decorator surface MUST expose them independently so a future tool
    can be ``read_only=True, idempotent=False`` (e.g. a read-only tool
    that returns a fresh UUID per call) without re-introducing a
    split-brain set.

    This test asserts the decorator surface is independent — not that
    the data diverges. It serves as a TODO-anchor for future tools that
    need the axes to differ.
    """
    import inspect

    import roam.mcp_server as mcp

    sig = inspect.signature(mcp._tool)
    assert "read_only" in sig.parameters, "_tool decorator is missing the read_only kwarg — W108 regressed."
    assert "idempotent" in sig.parameters, "_tool decorator is missing the idempotent kwarg — W113 regressed."

    # Both kwargs default to True (the safe assumption). The defaults
    # being identical is incidental — the kwargs are still independent.
    assert sig.parameters["read_only"].default is True
    assert sig.parameters["idempotent"].default is True

    # Confirm both flags are stored as independent slots in _TOOL_METADATA.
    # We pick a sample tool and assert both keys exist (regardless of value).
    meta = mcp._TOOL_METADATA.get("roam_health")
    assert meta is not None
    assert "read_only" in meta, "_TOOL_METADATA missing read_only slot — W108 regressed."
    assert "idempotent" in meta, "_TOOL_METADATA missing idempotent slot — W113 regressed."

    # Document the current divergence between the two derived views.
    # Before W365, the two coincided perfectly. W365 introduced the first
    # deliberate divergence: ``roam_reset`` and ``roam_clean`` are
    # non-read-only (they mutate the index DB) but ARE idempotent in the
    # MCP-spec sense: per
    # https://modelcontextprotocol.io/specification/2025-11-25/server/tools
    # "idempotent = repeating with same arguments has no additional effect
    # beyond the initial call". A second roam_reset finds nothing to
    # delete; a second roam_clean finds no orphans. These are textbook
    # idempotent destructive operations.
    expected_divergence = {"roam_reset", "roam_clean"}
    actual_divergence = mcp._NON_READ_ONLY_TOOLS - mcp._NON_IDEMPOTENT_TOOLS
    assert actual_divergence == expected_divergence, (
        f"Unexpected divergence between _NON_READ_ONLY_TOOLS and "
        f"_NON_IDEMPOTENT_TOOLS:\n"
        f"  expected non-read-only-but-idempotent: {sorted(expected_divergence)}\n"
        f"  actual non-read-only-but-idempotent:   {sorted(actual_divergence)}\n\n"
        f"This is INTENTIONALLY allowed by W113 (the axes are independent). "
        f"W365 introduced the first divergence (roam_reset / roam_clean). "
        f"If you added a new tool where read_only=False and idempotent=True "
        f"(or vice versa), update ``expected_divergence`` above to reflect "
        f"the new design intent — do NOT re-couple the two axes."
    )
