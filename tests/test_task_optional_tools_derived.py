"""ROADMAP A1 / W105: ``_TASK_OPTIONAL_TOOLS`` is a derived view of
``_TOOL_METADATA``.

This is the third A1 split-brain-dict collapse, following the W74 pattern
(``_DESTRUCTIVE_TOOLS``) and the W99/W100 pattern (``_TASK_REQUIRED_TOOLS``).
Of the 8 parallel classification dicts in ``mcp_server.py`` (core / read-only
/ destructive / task-required / task-optional / metadata / deprecated /
registered), ``_TASK_OPTIONAL_TOOLS`` was picked next because W100's report
explicitly named it as the obvious next step:

1. Same shape as ``task_required`` — a single bool flag on each tool's
   decorator, cleanly derivable into a frozenset at module-load.
2. Larger blast radius than W100 (12 members vs 5) but same dispatch path —
   the ``if task_required: ... elif name in _TASK_OPTIONAL_TOOLS: ...``
   chain inside ``_tool`` was already the only in-module consumer.
3. After this wave lands the two flags become structurally identical, which
   sets up a clean follow-up to merge them into a single
   ``task_mode: Literal["required", "optional"] | None`` enum kwarg (the
   ``TODO(post-W105)`` comment in ``_tool`` flags this for a future wave).

The new source of truth is the ``task_optional=True`` kwarg on the
``@_tool`` decorator. ``_TASK_OPTIONAL_TOOLS`` is rebuilt at module-load
after every decorator has populated ``_TOOL_METADATA``.

These tests pin the migration so the legacy name keeps working AND the
derivation invariant is enforced — drift between the two would mean the
collapse has regressed.
"""

from __future__ import annotations


def test_task_optional_tools_is_derived_from_metadata() -> None:
    """``_TASK_OPTIONAL_TOOLS`` content == names where
    ``_TOOL_METADATA[name]['task_optional']`` is True. Every name in
    one must appear in the other; no drift is allowed.
    """
    from roam.mcp_server import _TASK_OPTIONAL_TOOLS, _TOOL_METADATA

    expected = frozenset(
        name for name, meta in _TOOL_METADATA.items() if meta.get("task_optional", False)
    )
    assert _TASK_OPTIONAL_TOOLS == expected, (
        "_TASK_OPTIONAL_TOOLS is no longer derived from _TOOL_METADATA. "
        "If you added an optional-task tool, set task_optional=True on its "
        "@_tool decorator — do NOT add to a separate set."
    )


def test_task_optional_tools_is_frozenset() -> None:
    """The derived view is a ``frozenset`` so callers cannot mutate the
    classification post-load. Mutation of the legacy ``set`` form would
    have silently broken the derivation invariant.
    """
    from roam.mcp_server import _TASK_OPTIONAL_TOOLS

    assert isinstance(_TASK_OPTIONAL_TOOLS, frozenset), (
        f"_TASK_OPTIONAL_TOOLS must be a frozenset, got "
        f"{type(_TASK_OPTIONAL_TOOLS).__name__}"
    )


def test_task_optional_decorator_kwarg_propagates_to_metadata() -> None:
    """The ``task_optional=True`` kwarg on ``@_tool`` populates the
    metadata flag, which in turn drives the derived view. A regression
    here would mean the decorator-level marker has been disconnected
    from the source of truth.
    """
    import roam.mcp_server as mcp

    # All 12 pre-collapse optional-task tools must reflect the flag.
    for name in (
        "roam_orchestrate",
        "roam_mutate",
        "roam_vuln_map",
        "roam_ingest_trace",
        "roam_bisect_blame",
        "roam_forecast",
        "roam_path_coverage",
        "roam_search_semantic",
        "roam_closure",
        "roam_cut_analysis",
        "roam_generate_plan",
        "roam_adversarial_review",
    ):
        meta = mcp._TOOL_METADATA.get(name)
        assert meta is not None, (
            f"_TOOL_METADATA missing {name!r} — decorator did not register it. "
            f"_TOOL_METADATA is populated unconditionally (even without fastmcp), "
            f"so a missing entry indicates the decorator path is broken."
        )
        assert meta.get("task_optional") is True, (
            f"{name!r} metadata lacks task_optional=True. The "
            f"@_tool(task_optional=True) kwarg failed to propagate to "
            f"_TOOL_METADATA."
        )


def test_pre_collapse_task_optional_members_still_present() -> None:
    """Pin the pre-collapse hardcoded set is still present in the
    derived view. This catches regressions where a collapse refactor
    accidentally drops an optional-task marker.
    """
    from roam.mcp_server import _TASK_OPTIONAL_TOOLS

    # The pre-collapse hardcoded set, copied verbatim from mcp_server.py
    # (now replaced by the derived view at module-load finalization).
    # Treat this as the regression floor.
    PRE_COLLAPSE_TASK_OPTIONAL = {
        "roam_orchestrate",
        "roam_mutate",
        "roam_vuln_map",
        "roam_ingest_trace",
        "roam_bisect_blame",
        "roam_forecast",
        "roam_path_coverage",
        "roam_search_semantic",
        "roam_closure",
        "roam_cut_analysis",
        "roam_generate_plan",
        "roam_adversarial_review",
    }

    missing = PRE_COLLAPSE_TASK_OPTIONAL - _TASK_OPTIONAL_TOOLS
    assert not missing, (
        f"Optional-task markers lost during collapse: {sorted(missing)}. "
        f"These tools were optional-task before ROADMAP A1 / W105 but the "
        f"derived view does not include them now. Either mark their "
        f"@_tool decorator with task_optional=True, or update this pin "
        f"to reflect an intentional change."
    )


def test_task_required_and_task_optional_disjoint() -> None:
    """The if/elif chain in ``_tool`` would silently prefer ``"required"``
    if a tool appeared in both flags. Pin disjointness — this is the
    invariant from W100 that W107 made impossible-by-construction via the
    ``task_mode`` enum (but we keep the runtime check for the legacy-bool
    code path).
    """
    from roam.mcp_server import _TASK_OPTIONAL_TOOLS, _TASK_REQUIRED_TOOLS

    overlap = _TASK_REQUIRED_TOOLS & _TASK_OPTIONAL_TOOLS
    assert not overlap, (
        f"Tools cannot be both task_required and task_optional: "
        f"{sorted(overlap)}. Pick one classification. The two-bool shape "
        f"made this possible to assert at module-load; W107's task_mode "
        f"enum makes it impossible by construction."
    )


# ---------------------------------------------------------------------------
# W107: collapsed ``task_required: bool`` + ``task_optional: bool`` into a
# single ``task_mode: Literal["required", "optional"] | None`` enum kwarg
# on the ``@_tool`` decorator. The two legacy bool kwargs are RETAINED as
# DEPRECATED back-compat aliases. The block below pins:
#
#   - the enum is the canonical field in ``_TOOL_METADATA`` (bools derive)
#   - the legacy bool kwargs still resolve onto the enum at decorator time
#   - the enum makes "required AND optional" impossible by construction
#   - mixing the new enum with legacy bools issues a DeprecationWarning
# ---------------------------------------------------------------------------


def test_task_mode_enum_is_canonical_for_optional_tools() -> None:
    """W107: ``task_mode`` is the canonical 3-way enum. The legacy
    ``task_optional`` boolean field in ``_TOOL_METADATA`` is DERIVED from it.
    Every tool with ``task_optional=True`` in metadata must also have
    ``task_mode == "optional"`` — they cannot drift.
    """
    from roam.mcp_server import _TOOL_METADATA

    for name, meta in _TOOL_METADATA.items():
        if meta.get("task_optional"):
            assert meta.get("task_mode") == "optional", (
                f"{name!r}: task_optional=True but task_mode={meta.get('task_mode')!r}. "
                f"The bool is supposed to be a derived back-compat field; "
                f"if you see this, the bool was written as a source-of-truth "
                f"and that regresses W107."
            )
        if meta.get("task_mode") == "optional":
            assert meta.get("task_optional") is True, (
                f"{name!r}: task_mode='optional' but task_optional="
                f"{meta.get('task_optional')!r}. The back-compat derivation "
                f"has drifted from the canonical enum."
            )


def test_task_mode_values_are_constrained() -> None:
    """W107: ``task_mode`` must be one of ``"required"``, ``"optional"``,
    or ``None``. Any other value would crash the dispatch path because
    FastMCP's ``TaskConfig(mode=...)`` rejects unknown modes.
    """
    from roam.mcp_server import _TOOL_METADATA

    valid = {"required", "optional", None}
    for name, meta in _TOOL_METADATA.items():
        mode = meta.get("task_mode")
        assert mode in valid, (
            f"{name!r}: task_mode={mode!r} is not one of {valid}. "
            f"FastMCP's TaskConfig(mode=...) only accepts 'required' or "
            f"'optional'; None means 'no task support'."
        )


def test_task_mode_required_optional_disjoint_by_construction() -> None:
    """W107: a tool can't be both required AND optional. The enum encodes
    this structurally — you simply cannot pass two values to ``task_mode``.
    Pin the invariant at the metadata level so a future refactor that
    re-introduces a parallel source can't silently violate it.
    """
    from roam.mcp_server import _TOOL_METADATA

    for name, meta in _TOOL_METADATA.items():
        if meta.get("task_required") and meta.get("task_optional"):
            raise AssertionError(
                f"{name!r} is BOTH task_required and task_optional. "
                f"task_mode={meta.get('task_mode')!r}. The W107 enum should "
                f"make this impossible by construction; if you see this, the "
                f"derivation code is broken."
            )


def test_legacy_task_required_kwarg_still_resolves_to_enum() -> None:
    """W107: the deprecated ``task_required=True`` kwarg must still resolve
    onto ``task_mode="required"`` so existing third-party tool decorations
    (and any in-flight migrations) don't break. We register a stub tool
    via ``_tool`` and inspect the resulting metadata directly.
    """
    import roam.mcp_server as mcp

    sentinel = "_w107_test_legacy_required_stub"
    try:

        @mcp._tool(name=sentinel, description="W107 legacy bool back-compat probe", task_required=True)
        def _stub() -> dict:  # pragma: no cover - never invoked
            return {}

        meta = mcp._TOOL_METADATA.get(sentinel)
        assert meta is not None, "stub failed to register in _TOOL_METADATA"
        assert meta.get("task_mode") == "required", (
            f"legacy task_required=True did not resolve to task_mode='required'; "
            f"got task_mode={meta.get('task_mode')!r}. The deprecation shim is broken."
        )
        assert meta.get("task_required") is True
        assert meta.get("task_optional") is False
    finally:
        mcp._TOOL_METADATA.pop(sentinel, None)


def test_legacy_task_optional_kwarg_still_resolves_to_enum() -> None:
    """W107: the deprecated ``task_optional=True`` kwarg must still resolve
    onto ``task_mode="optional"``. Same shape as the required-side test.
    """
    import roam.mcp_server as mcp

    sentinel = "_w107_test_legacy_optional_stub"
    try:

        @mcp._tool(name=sentinel, description="W107 legacy bool back-compat probe", task_optional=True)
        def _stub() -> dict:  # pragma: no cover - never invoked
            return {}

        meta = mcp._TOOL_METADATA.get(sentinel)
        assert meta is not None, "stub failed to register in _TOOL_METADATA"
        assert meta.get("task_mode") == "optional", (
            f"legacy task_optional=True did not resolve to task_mode='optional'; "
            f"got task_mode={meta.get('task_mode')!r}. The deprecation shim is broken."
        )
        assert meta.get("task_required") is False
        assert meta.get("task_optional") is True
    finally:
        mcp._TOOL_METADATA.pop(sentinel, None)


def test_mixing_task_mode_with_legacy_bools_warns() -> None:
    """W107: when a caller passes BOTH the new ``task_mode`` and a legacy
    ``task_required`` / ``task_optional`` bool, the enum wins and a
    ``DeprecationWarning`` is issued so the caller knows to drop the legacy
    kwarg. Silent precedence would be a footgun — this test pins the warning.
    """
    import warnings as stdlib_warnings

    import roam.mcp_server as mcp

    sentinel = "_w107_test_mixed_kwargs_stub"
    try:
        with stdlib_warnings.catch_warnings(record=True) as caught:
            stdlib_warnings.simplefilter("always")

            @mcp._tool(
                name=sentinel,
                description="W107 mixed-kwarg probe",
                task_mode="required",
                task_required=True,  # legacy + new together → warn
            )
            def _stub() -> dict:  # pragma: no cover - never invoked
                return {}

        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings, (
            "mixing task_mode=... with legacy task_required/task_optional must "
            "issue a DeprecationWarning; none was raised."
        )
        # And the enum still wins.
        meta = mcp._TOOL_METADATA.get(sentinel)
        assert meta is not None
        assert meta.get("task_mode") == "required"
    finally:
        mcp._TOOL_METADATA.pop(sentinel, None)


def test_default_task_mode_is_none() -> None:
    """W107: when neither ``task_mode`` nor the legacy bools are passed, the
    tool ends up with ``task_mode=None`` (no task support). This is the
    "blocking call only" path — the vast majority of tools.
    """
    import roam.mcp_server as mcp

    sentinel = "_w107_test_default_no_task_stub"
    try:

        @mcp._tool(name=sentinel, description="W107 default-task-mode probe")
        def _stub() -> dict:  # pragma: no cover - never invoked
            return {}

        meta = mcp._TOOL_METADATA.get(sentinel)
        assert meta is not None
        assert meta.get("task_mode") is None, (
            f"default task_mode must be None; got {meta.get('task_mode')!r}"
        )
        assert meta.get("task_required") is False
        assert meta.get("task_optional") is False
    finally:
        mcp._TOOL_METADATA.pop(sentinel, None)
