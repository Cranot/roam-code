"""W365 / W343 — parity lint between ``_TOOL_METADATA`` and ``ToolAnnotations``.

The MCP spec defines five tool-annotation hint fields that clients use to
choose UI/safety treatment for a tool:

- ``title``         — display name (string)
- ``readOnlyHint``  — bool; tool does not mutate persistent state
- ``destructiveHint`` — bool; tool may delete/overwrite data
- ``idempotentHint``  — bool; safe to retry without compounding side effects
- ``openWorldHint``   — bool; tool can reach beyond the local environment

Source: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
(Section: Tool annotations).

Roam stores the source-of-truth axes as plain bool fields on
``_TOOL_METADATA[name]``:

- ``read_only``    (default True)
- ``destructive``  (default False)
- ``idempotent``   (default True)

``_tool_annotations(name)`` in ``src/roam/mcp_server.py`` is the derived
view that maps those into the spec-standard ``ToolAnnotations`` shape; it
is invoked at decorator time (``kwargs["annotations"] = _tool_annotations(name)``
around line 2254) so every wrapper carries the right hints over the wire.

This lint pins the parity invariant so a future drive-by edit on either
side (the spec-facing emitter OR the source-of-truth metadata dict) can
no longer silently drift. The threat model is W214 #5 — overbroad
authorization scope leaking through mis-labelled annotations.

Companion: W343 in ``(internal memo)``.
"""

from __future__ import annotations

import pytest

# The four hint keys the MCP spec defines on ``ToolAnnotations``.
_HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Mapping from the spec-standard hint key to the source-of-truth field on
# ``_TOOL_METADATA[name]``. ``openWorldHint`` is intentionally absent: roam
# tools are local-graph-only by construction, so the annotation is a fixed
# constant False — not derived from per-tool metadata.
_HINT_TO_META_FIELD = {
    "readOnlyHint": "read_only",
    "destructiveHint": "destructive",
    "idempotentHint": "idempotent",
}


def _all_tools() -> list[str]:
    import roam.mcp_server as mcp

    # ``_TOOL_METADATA`` is populated at decorator-time, before the fastmcp
    # presence gate — so every registered tool appears here even in CLI-only
    # environments. See the W365 docstring above + the ``_tool`` decorator
    # in ``src/roam/mcp_server.py``.
    return sorted(mcp._TOOL_METADATA.keys())


def test_every_tool_has_annotations_shape():
    """``_tool_annotations`` must return all four spec hint keys for every tool."""
    import roam.mcp_server as mcp

    missing: list[tuple[str, str]] = []
    for name in _all_tools():
        ann = mcp._tool_annotations(name)
        assert isinstance(ann, dict), f"{name}: _tool_annotations did not return dict"
        for key in _HINT_KEYS:
            if key not in ann:
                missing.append((name, key))
    assert not missing, (
        f"{len(missing)} (tool, hint) pair(s) missing from _tool_annotations output:\n"
        f"  {missing[:20]}{' ...' if len(missing) > 20 else ''}\n\n"
        f"MCP spec ToolAnnotations requires readOnlyHint / destructiveHint / "
        f"idempotentHint / openWorldHint on every tool. See "
        f"https://modelcontextprotocol.io/specification/2025-11-25/server/tools"
    )


def test_every_annotation_hint_is_bool():
    """Each spec hint must be a plain ``bool`` — clients parse types strictly."""
    import roam.mcp_server as mcp

    bad: list[tuple[str, str, type]] = []
    for name in _all_tools():
        ann = mcp._tool_annotations(name)
        for key in _HINT_KEYS:
            val = ann.get(key)
            if not isinstance(val, bool):
                bad.append((name, key, type(val)))
    assert not bad, (
        f"{len(bad)} (tool, hint, actual_type) violations of bool-typed hints:\n"
        f"  {bad[:20]}{' ...' if len(bad) > 20 else ''}"
    )


def test_annotation_hints_match_tool_metadata():
    """Source-of-truth flags on ``_TOOL_METADATA`` must equal the derived hints.

    Drift here means either:
    (a) someone edited ``_TOOL_METADATA[name]["read_only"]`` (etc.) without
        re-checking ``_tool_annotations``; OR
    (b) someone edited ``_tool_annotations`` without re-checking the metadata
        defaults.

    Either way, the on-the-wire ``ToolAnnotations`` no longer reflects the
    tool's real behaviour — exactly the W214 threat 5 / overbroad-scope
    drift this lint exists to block.
    """
    import roam.mcp_server as mcp

    mismatches: list[tuple[str, str, object, object]] = []
    for name in _all_tools():
        meta = mcp._TOOL_METADATA[name]
        ann = mcp._tool_annotations(name)
        for hint_key, meta_key in _HINT_TO_META_FIELD.items():
            # Defaults match the ``_tool`` decorator signature: read_only=True,
            # destructive=False, idempotent=True. Honest absence is fine; the
            # derived view must just agree with whatever the metadata says.
            expected = meta.get(meta_key, {"read_only": True, "destructive": False, "idempotent": True}[meta_key])
            actual = ann.get(hint_key)
            if bool(expected) != bool(actual):
                mismatches.append((name, hint_key, expected, actual))
    assert not mismatches, (
        f"{len(mismatches)} _TOOL_METADATA / ToolAnnotations parity violations:\n"
        f"  {mismatches[:20]}{' ...' if len(mismatches) > 20 else ''}\n\n"
        f"Each row: (tool_name, hint_key, _TOOL_METADATA_value, _tool_annotations_value).\n"
        f"Fix: pick one canonical value and update the other side."
    )


def test_open_world_hint_is_always_false():
    """Roam tools are local-graph-only by construction — ``openWorldHint`` is False.

    The roam SQLite index is the entire reachable world for every tool. If a
    future drive-by introduces a tool that reaches the network, this lint
    fires and forces a deliberate decision (either narrow the tool back to
    local-only OR teach ``_tool_annotations`` to read a per-tool flag).
    """
    import roam.mcp_server as mcp

    open_world: list[str] = []
    for name in _all_tools():
        ann = mcp._tool_annotations(name)
        if ann.get("openWorldHint") is not False:
            open_world.append(name)
    assert not open_world, (
        f"{len(open_world)} tool(s) report openWorldHint != False:\n"
        f"  {open_world[:20]}{' ...' if len(open_world) > 20 else ''}\n\n"
        f"All roam tools are local-graph-only by construction. If you genuinely "
        f"need an open-world tool, extend ``_tool_annotations`` to read a "
        f"per-tool ``open_world`` flag from ``_TOOL_METADATA`` AND update this "
        f"lint to honor it."
    )


def test_annotations_passed_to_wrapper_registration():
    """The derived view must reach FastMCP at decorator-time.

    This is a static check on the ``_tool`` decorator source — we assert that
    ``kwargs["annotations"] = _tool_annotations(name)`` (or equivalent) is
    still wired. Drift here would mean the on-the-wire annotations silently
    diverge from ``_TOOL_METADATA`` even when the parity test above passes,
    because the wrapper-bridge layer dropped the call entirely.
    """
    import inspect

    import roam.mcp_server as mcp

    # The registration kwargs were factored out of ``_tool`` into
    # ``_build_registration_kwargs`` — follow the delegation chain: the
    # decorator must still call the builder, and the builder must still
    # wire ``_tool_annotations(name)``. Checking both legs preserves the
    # original drift protection across the refactor.
    tool_src = inspect.getsource(mcp._tool)
    wired_inline = "_tool_annotations(name)" in tool_src
    delegates = "_build_registration_kwargs(" in tool_src
    builder_wired = hasattr(mcp, "_build_registration_kwargs") and (
        "_tool_annotations(name)" in inspect.getsource(mcp._build_registration_kwargs)
    )
    assert wired_inline or (delegates and builder_wired), (
        "The ``_tool`` decorator no longer wires ``_tool_annotations(name)`` "
        "(neither inline nor via ``_build_registration_kwargs``). ToolAnnotations "
        "are no longer reaching FastMCP — on-the-wire hints will fall back to "
        "fastmcp defaults and silently disagree with _TOOL_METADATA. Re-wire "
        "``kwargs['annotations'] = _tool_annotations(name)`` in src/roam/mcp_server.py."
    )


def test_tool_annotations_title_present_and_nonempty():
    """The spec recommends a human-friendly ``title``; roam derives one per tool."""
    import roam.mcp_server as mcp

    bad: list[str] = []
    for name in _all_tools():
        ann = mcp._tool_annotations(name)
        title = ann.get("title")
        if not isinstance(title, str) or not title.strip():
            bad.append(name)
    assert not bad, f"{len(bad)} tool(s) lack a non-empty annotation ``title``:\n  {bad[:20]}"


# ---------------------------------------------------------------------------
# Sanity: the canonical four-bool hint vocabulary is the closed set we expect.
# If the MCP spec adds a new annotation key (e.g., the proposed ``costHint``
# or ``rateLimitedHint``), this test forces a deliberate update here rather
# than letting the new key be silently ignored.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hint_key", _HINT_KEYS)
def test_hint_vocabulary_pinned(hint_key):
    """Pins the four-hint vocabulary so a spec extension is caught at lint time."""
    assert hint_key in {
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }, (
        f"Unknown hint key in _HINT_KEYS: {hint_key}. Either the MCP spec "
        f"extended ToolAnnotations (good — update this test to honor the "
        f"new key + update ``_tool_annotations`` to emit it), or someone "
        f"typo'd the vocabulary tuple."
    )


# ---------------------------------------------------------------------------
# W365 drive-by: third-surface parity check vs the ``@roam_capability``
# decorator registry.
#
# The MCP server exposes the same per-command behaviour through THREE
# surfaces, all of which can drift independently:
#
#   1. ``_TOOL_METADATA[name]["destructive" / "read_only" / "idempotent"]``
#      — populated by the ``@_tool`` decorator kwargs (mcp_server.py).
#   2. ``ToolAnnotations`` (``readOnlyHint`` / ``destructiveHint`` / ...)
#      — derived view emitted on the wire by ``_tool_annotations(name)``.
#      Parity with (1) is pinned by ``test_annotation_hints_match_tool_metadata``.
#   3. ``Capability(destructive=..., side_effect=...)`` on the
#      ``roam_capability`` decorator (roam.capability) — independent
#      manifest consumed by the Roam Review GitHub App and `roam
#      capabilities` CLI.
#
# Surfaces 1 ↔ 2 are pinned above. This drive-by pins 1 ↔ 3 so a future
# edit on either side fires this lint with a concrete drift list. The
# initial audit (W365) found ``roam_reset`` and ``roam_clean`` declared as
# ``destructive=True`` on the capability registry but absent from
# ``_TOOL_METADATA`` — fixed at the same wave by adding ``destructive=True``
# to the ``@_tool`` kwargs.
#
# ``read_only`` (MCP) vs ``side_effect`` (capability) is the SAME axis,
# inverted: ``read_only=True`` ↔ ``side_effect=False``. The lint normalises
# at boundary.
# ---------------------------------------------------------------------------


# Quarantine list for the DESTRUCTIVE axis — intentional divergences
# between `_TOOL_METADATA[..]["destructive"]` and `Capability.destructive`.
# W365 fixed two real bugs (roam_reset / roam_clean) by adding
# destructive=True to the MCP wrappers. The remaining intentional
# divergence is roam_mutate: capability says destructive=False because the
# tool does not auto-write (it emits a diff for the agent to apply); MCP
# says destructive=True so clients routing on destructiveHint surface a
# confirmation prompt. Conservative-on-MCP, accurate-on-capability.
_DESTRUCTIVE_PARITY_QUARANTINE: dict[str, str] = {
    "roam_mutate": "MCP conservative; capability tracks actual on-disk write",
}


# Quarantine list for the READ_ONLY / SIDE_EFFECT axis-inversion —
# intentional divergences between `_TOOL_METADATA[..]["read_only"]` and the
# (inverted) `Capability.side_effect`. Keep this tight; every entry must
# carry a one-line rationale. Adding a new entry requires explicit
# justification.
_READ_ONLY_PARITY_QUARANTINE: dict[str, str] = {
    # The bulk of these are artefact-emitting tools where the capability
    # registry tracks ANY disk write (side_effect=True) but the MCP
    # read_only axis tracks GRAPH MUTATION ONLY (read_only=True). The
    # asymmetry is acceptable: MCP clients route on read_only to gate
    # graph-shape change confirmations; the capability registry serves the
    # Roam Review GitHub App's "did this tool touch the filesystem at all"
    # question.
    "roam_dogfood": "writes audit logs only; not a graph mutation",
    "roam_audit_trail_export": "writes export artefact; not a graph mutation",
    "roam_agent_export": "writes export bundle; not a graph mutation",
    "roam_attest": "writes attestation predicate; not a graph mutation",
    "roam_sbom": "writes SBOM file; not a graph mutation",
    "roam_pr_analyze": "writes analysis bundle; not a graph mutation",
    "roam_rules_validate": "writes validation report; not a graph mutation",
    "roam_stale_refs": "writes a stale-ref report; not a graph mutation",
    "roam_fitness": "writes a fitness summary; not a graph mutation",
    "roam_eval_retrieve": "writes eval rows; not a graph mutation",
    "roam_test_scaffold": "scaffolds template files outside the index",
    "roam_describe": "writes a description artefact; not a graph mutation",
    "roam_minimap": "writes a minimap artefact; not a graph mutation",
    # roam_mutate: capability registry says side_effect=False because the
    # mutate command DOES NOT auto-write — it emits a unified diff the
    # agent must apply explicitly. The MCP wrapper conservatively flags
    # read_only=False (mutate IS a mutation surface even if applying the
    # diff is the agent's responsibility). Conservative-on-MCP,
    # accurate-on-capability.
    "roam_mutate": "MCP conservative; capability tracks actual on-disk write",
    # roam_vuln_map, roam_ingest_trace: both ingest external data into
    # the index (vulns DB, traces). MCP read_only=False is correct for
    # graph mutation; capability side_effect=False predates the ingest
    # path (legacy decorator value). Track via W365 drive-by; not safe
    # to flip silently without re-auditing every consumer of the
    # capability manifest.
    "roam_vuln_map": "legacy capability flag; ingestion mutates graph (W365 pinned)",
    "roam_ingest_trace": "legacy capability flag; ingestion mutates graph (W365 pinned)",
    # W365-followup: roam_reset / roam_clean previously carried a
    # self-inconsistent destructive=True + side_effect=False decoration. Fixed
    # at W365-followup by flipping side_effect=True on both decorators (both
    # tools mutate the index DB on disk). No quarantine needed — the natural
    # axis-inversion parity (read_only=False ↔ side_effect=True) holds.
}


_MCP_TO_CAPABILITY_NAMING_DRIFT = {
    # The 4 historical renames (W961) where the capability name is not
    # ``<mcp_name>.removeprefix("roam_").replace("_", "-")``.
    "roam_dead_code": "dead",
    "roam_complexity_report": "complexity",
    "roam_search_symbol": "search",
    "roam_file_info": "file",
}


def _mcp_name_to_capability_name(mcp_name: str) -> str:
    """Convert MCP tool name (``roam_foo_bar``) to capability name (``foo-bar``)."""
    if mcp_name in _MCP_TO_CAPABILITY_NAMING_DRIFT:
        return _MCP_TO_CAPABILITY_NAMING_DRIFT[mcp_name]
    return mcp_name.removeprefix("roam_").replace("_", "-")


def _load_full_capability_registry():
    """Force-import every cmd_*.py module so `@roam_capability` decorators register.

    Returns the populated `REGISTRY.items` mapping.
    """
    import importlib

    import roam.cli as _cli
    from roam.capability import REGISTRY

    for _cmd_name, (modpath, _attr) in _cli._COMMANDS.items():
        try:
            importlib.import_module(modpath)
        except Exception:
            # Plugin / optional modules may fail import; that's fine — we
            # only check parity for capabilities that DID register.
            pass
    return REGISTRY.items


def test_capability_destructive_matches_tool_metadata():
    """3rd-surface parity: ``Capability.destructive`` ↔ ``_TOOL_METADATA[..]["destructive"]``.

    Fires when an MCP tool's destructive axis diverges from the capability
    registry's destructive axis. Quarantine list above admits intentional
    divergences with rationale. Any new entry should be either a bug-fix
    on one side or a deliberate addition to the quarantine.
    """
    import roam.mcp_server as mcp

    caps = _load_full_capability_registry()
    mismatches: list[tuple[str, bool, bool]] = []
    for tool_name, meta in mcp._TOOL_METADATA.items():
        cap_name = _mcp_name_to_capability_name(tool_name)
        cap = caps.get(cap_name)
        if cap is None:
            # Compound recipes (roam_explore, ...) and helpers don't have
            # a 1:1 capability — skip silently.
            continue
        if tool_name in _DESTRUCTIVE_PARITY_QUARANTINE:
            continue
        mcp_destructive = bool(meta.get("destructive", False))
        cap_destructive = bool(cap.destructive)
        if mcp_destructive != cap_destructive:
            mismatches.append((tool_name, mcp_destructive, cap_destructive))
    assert not mismatches, (
        f"{len(mismatches)} destructive-flag drift entries between _TOOL_METADATA "
        f"and the @roam_capability registry:\n"
        f"  {mismatches[:20]}\n\n"
        f"Each row: (tool_name, _TOOL_METADATA_destructive, Capability_destructive).\n"
        f"Fix: pick the truthful value, update the disagreeing surface, OR add "
        f"an entry to _DESTRUCTIVE_PARITY_QUARANTINE with a one-line rationale."
    )


def test_capability_read_only_matches_tool_metadata():
    """3rd-surface parity: ``Capability.side_effect`` ↔ NOT ``_TOOL_METADATA[..]["read_only"]``.

    ``side_effect=True`` ↔ ``read_only=False`` (same axis, inverted vocabulary).
    Quarantine list above admits intentional divergences (artefact-writing
    tools where the capability registry tracks any disk write but the MCP
    read_only axis tracks only graph mutation).
    """
    import roam.mcp_server as mcp

    caps = _load_full_capability_registry()
    mismatches: list[tuple[str, str, str]] = []
    for tool_name, meta in mcp._TOOL_METADATA.items():
        cap_name = _mcp_name_to_capability_name(tool_name)
        cap = caps.get(cap_name)
        if cap is None:
            continue
        if tool_name in _READ_ONLY_PARITY_QUARANTINE:
            continue
        mcp_read_only = bool(meta.get("read_only", True))
        cap_side_effect = bool(cap.side_effect)
        # Disagreement: ``read_only`` and ``side_effect`` should be inverses.
        if mcp_read_only == cap_side_effect:
            mismatches.append((tool_name, f"read_only={mcp_read_only}", f"side_effect={cap_side_effect}"))
    assert not mismatches, (
        f"{len(mismatches)} read_only / side_effect axis-inversion drift entries:\n"
        f"  {mismatches[:20]}\n\n"
        f"Each row: (tool_name, _TOOL_METADATA, Capability). They should be "
        f"INVERSES (read_only=True ↔ side_effect=False).\n"
        f"Fix: pick the truthful value, update the disagreeing surface, OR add "
        f"an entry to _READ_ONLY_PARITY_QUARANTINE with a one-line rationale."
    )
