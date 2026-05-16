"""ROADMAP A1 / W99: ``_TASK_REQUIRED_TOOLS`` is a derived view of
``_TOOL_METADATA``.

This is the second A1 split-brain-dict collapse, following the W74
pattern that consolidated ``_DESTRUCTIVE_TOOLS``. Of the 8 parallel
classification dicts in ``mcp_server.py`` (core / read-only / destructive
/ task-required / task-optional / metadata / deprecated / registered),
``_TASK_REQUIRED_TOOLS`` was picked next because:

1. Small blast radius — 5 members (``roam_init``, ``roam_reindex``,
   ``roam_health``, ``roam_understand``, ``roam_simulate``); one
   in-module consumer (the ``task_mode`` switch inside ``_tool``).
2. Cleanly derivable from a single bool flag on the decorator —
   exactly the shape ``destructive=True`` used.
3. Stable semantic — "must run as an MCP task because >2s on a real
   repo" rarely changes; new entries are rare.

The new source of truth is the ``task_required=True`` kwarg on the
``@_tool`` decorator. ``_TASK_REQUIRED_TOOLS`` is rebuilt at
module-load after every decorator has populated ``_TOOL_METADATA``.

These tests pin the migration so the legacy name keeps working AND the
derivation invariant is enforced — drift between the two would mean the
collapse has regressed.
"""

from __future__ import annotations


def test_task_required_tools_is_derived_from_metadata() -> None:
    """``_TASK_REQUIRED_TOOLS`` content == names where
    ``_TOOL_METADATA[name]['task_required']`` is True. Every name in
    one must appear in the other; no drift is allowed.
    """
    from roam.mcp_server import _TASK_REQUIRED_TOOLS, _TOOL_METADATA

    expected = frozenset(name for name, meta in _TOOL_METADATA.items() if meta.get("task_required", False))
    assert _TASK_REQUIRED_TOOLS == expected, (
        "_TASK_REQUIRED_TOOLS is no longer derived from _TOOL_METADATA. "
        "If you added a required-task tool, set task_required=True on its "
        "@_tool decorator — do NOT add to a separate set."
    )


def test_task_required_tools_is_frozenset() -> None:
    """The derived view is a ``frozenset`` so callers cannot mutate the
    classification post-load. Mutation of the legacy ``set`` form would
    have silently broken the derivation invariant.
    """
    from roam.mcp_server import _TASK_REQUIRED_TOOLS

    assert isinstance(_TASK_REQUIRED_TOOLS, frozenset), (
        f"_TASK_REQUIRED_TOOLS must be a frozenset, got {type(_TASK_REQUIRED_TOOLS).__name__}"
    )


def test_task_required_decorator_kwarg_propagates_to_metadata() -> None:
    """The ``task_required=True`` kwarg on ``@_tool`` populates the
    metadata flag, which in turn drives the derived view. A regression
    here would mean the decorator-level marker has been disconnected
    from the source of truth.
    """
    import roam.mcp_server as mcp

    # All 5 pre-collapse required-task tools must reflect the flag.
    for name in ("roam_init", "roam_reindex", "roam_health", "roam_understand", "roam_simulate"):
        meta = mcp._TOOL_METADATA.get(name)
        assert meta is not None, (
            f"_TOOL_METADATA missing {name!r} — decorator did not register it. "
            f"_TOOL_METADATA is populated unconditionally (even without fastmcp), "
            f"so a missing entry indicates the decorator path is broken."
        )
        assert meta.get("task_required") is True, (
            f"{name!r} metadata lacks task_required=True. The "
            f"@_tool(task_required=True) kwarg failed to propagate to "
            f"_TOOL_METADATA."
        )


def test_pre_collapse_task_required_members_still_present() -> None:
    """Pin the pre-collapse hardcoded set is still present in the
    derived view. This catches regressions where a collapse refactor
    accidentally drops a required-task marker.
    """
    from roam.mcp_server import _TASK_REQUIRED_TOOLS

    # The pre-collapse hardcoded set, copied verbatim from mcp_server.py
    # lines 315-321 (now replaced by the derived view at module-load
    # finalization). Treat this as the regression floor.
    PRE_COLLAPSE_TASK_REQUIRED = {
        "roam_init",
        "roam_reindex",
        "roam_health",
        "roam_understand",
        "roam_simulate",
    }

    missing = PRE_COLLAPSE_TASK_REQUIRED - _TASK_REQUIRED_TOOLS
    assert not missing, (
        f"Required-task markers lost during collapse: {sorted(missing)}. "
        f"These tools were required-task before ROADMAP A1 / W99 but the "
        f"derived view does not include them now. Either mark their "
        f"@_tool decorator with task_required=True, or update this pin "
        f"to reflect an intentional change."
    )


def test_task_required_flag_is_disjoint_from_task_optional() -> None:
    """A tool cannot be both ``task_required`` and ``task_optional`` —
    the dispatch logic in ``_tool`` uses an if/elif chain, so a tool in
    both sets would silently get ``"required"`` mode but the surface-
    consistency test would still pass. This test makes the disjointness
    explicit, mirroring the pre-collapse invariant.
    """
    from roam.mcp_server import _TASK_OPTIONAL_TOOLS, _TASK_REQUIRED_TOOLS

    overlap = _TASK_REQUIRED_TOOLS & _TASK_OPTIONAL_TOOLS
    assert not overlap, (
        f"Tools cannot be both task_required and in _TASK_OPTIONAL_TOOLS: {sorted(overlap)}. Pick one classification."
    )


# ---------------------------------------------------------------------------
# W107: task_required / task_optional bools collapsed into task_mode enum.
# The legacy bool kwargs remain as DEPRECATED back-compat aliases. The block
# below pins the enum-as-source-of-truth semantics so a future refactor that
# revives a bool source-of-truth (or drops the legacy kwargs entirely) fails
# loudly here rather than silently shifting the dispatch surface.
# ---------------------------------------------------------------------------


def test_task_mode_enum_is_canonical_for_required_tools() -> None:
    """W107: ``task_mode`` is the canonical 3-way enum. The legacy
    ``task_required`` boolean field in ``_TOOL_METADATA`` is DERIVED from it.
    Every tool with ``task_required=True`` in metadata must also have
    ``task_mode == "required"`` — they cannot drift.
    """
    from roam.mcp_server import _TOOL_METADATA

    for name, meta in _TOOL_METADATA.items():
        if meta.get("task_required"):
            assert meta.get("task_mode") == "required", (
                f"{name!r}: task_required=True but task_mode={meta.get('task_mode')!r}. "
                f"The bool is supposed to be a derived back-compat field; "
                f"if you see this, the bool was written as a source-of-truth "
                f"and that regresses W107."
            )
        if meta.get("task_mode") == "required":
            assert meta.get("task_required") is True, (
                f"{name!r}: task_mode='required' but task_required="
                f"{meta.get('task_required')!r}. The back-compat derivation "
                f"has drifted from the canonical enum."
            )
