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
# inverted: ``read_only=True`` ↔ ``side_effect=False``. The capability
# registry records the maximum effect of the full CLI command, while an MCP
# wrapper can intentionally expose a narrower read-only projection. Those
# projections are admitted only with executable evidence below: the CLI's
# effectful flags must exist and the MCP wrapper must omit them.
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


# Evidence-backed exceptions for the READ_ONLY / SIDE_EFFECT axis-inversion.
#
# Each value is ``(CLI-only effect flags, rationale)``. These are not generic
# suppressions: ``test_read_only_projection_exceptions_are_evidence_backed``
# proves that every flag still exists on the broad CLI command, is absent from
# the executable MCP wrapper, and that the two surfaces still have the exact
# maximum-effect/read-only divergence documented here. Stale entries therefore
# fail instead of silently weakening the parity gate.
_MCP_READ_ONLY_CLI_MAX_EFFECT_PROJECTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "roam_agent_export": (
        ("--output", "--write", "--bundle"),
        "MCP renders one agent format to its result; CLI-only flags write one or more files.",
    ),
    "roam_agent_opt": (
        ("--persist",),
        "MCP returns optimization findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_article_12_check": (
        ("--output", "--pdf"),
        "MCP returns the checklist envelope; CLI-only flags write report artifacts.",
    ),
    "roam_attest": (
        ("--output",),
        "MCP returns the attestation; CLI --output writes it to disk.",
    ),
    "roam_audit_trail_conformance_check": (
        ("--sarif-output", "--persist"),
        "MCP returns conformance evidence; CLI-only flags write SARIF or registry findings.",
    ),
    "roam_audit_trail_export": (
        ("--output", "--finalize"),
        "MCP returns an export; CLI-only flags write an artifact or append a closing trail record.",
    ),
    "roam_audit_trail_verify": (
        ("--persist",),
        "MCP returns verification evidence; CLI --persist writes registry findings.",
    ),
    "roam_auth_gaps": (
        ("--persist",),
        "MCP returns auth-gap findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_bus_factor": (
        ("--persist",),
        "MCP returns ownership-risk findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_clones": (
        ("--persist",),
        "MCP returns clone findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_compatibility": (
        ("--write-baseline",),
        "MCP compares existing snapshots; CLI --write-baseline creates or replaces baseline state.",
    ),
    "roam_complexity_report": (
        ("--persist",),
        "MCP returns complexity findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_conventions": (
        ("--persist",),
        "MCP returns convention findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_coverage_gaps": (
        ("--import-report", "--merge-imported"),
        "MCP may read gate config only; CLI-only coverage import flags update indexed coverage rows.",
    ),
    "roam_critique": (
        ("--persist",),
        "MCP returns patch findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_dark_matter": (
        ("--persist",),
        "MCP returns hidden-coupling findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_dead_code": (
        ("--persist",),
        "MCP returns dead-code findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_describe": (
        ("--write", "--output"),
        "MCP returns generated prose; CLI-only flags create or replace a description file.",
    ),
    "roam_doctor": (
        ("--persist",),
        "MCP returns diagnostics; CLI --persist writes them to the findings registry.",
    ),
    "roam_duplicates": (
        ("--persist",),
        "MCP returns duplicate findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_eval_retrieve": (
        ("--report", "--emit-out"),
        "MCP returns eval metrics; CLI-only flags write reports or benchmark rows.",
    ),
    "roam_fitness": (
        ("--init", "--write-baseline"),
        "MCP evaluates existing rules; CLI-only flags create config or baseline state.",
    ),
    "roam_health": (
        ("--persist",),
        "MCP returns health findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_hotspots": (
        ("--persist",),
        "MCP returns hotspot findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_llm_smells": (
        ("--persist",),
        "MCP returns LLM-smell findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_minimap": (
        ("--update", "--output", "--init-notes"),
        "MCP returns a minimap; CLI-only flags create or update agent-context files.",
    ),
    "roam_missing_index": (
        ("--persist",),
        "MCP returns missing-index findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_n1": (
        ("--persist",),
        "MCP returns N+1 findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_observability_opt": (
        ("--persist",),
        "MCP returns observability findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_orphan_imports": (
        ("--persist",),
        "MCP returns orphan-import findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_over_fetch": (
        ("--persist",),
        "MCP returns over-fetch findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_pr_risk": (
        ("--persist",),
        "MCP returns PR-risk findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_proof_bundle": (
        ("--output",),
        "MCP returns the proof bundle; CLI --output writes the selected representation to disk.",
    ),
    "roam_reachability_triage": (
        ("--write-baseline",),
        "MCP evaluates reachability; CLI --write-baseline creates or replaces baseline state.",
    ),
    "roam_rules_validate": (
        ("--fix",),
        "MCP validates rules; CLI --fix rewrites the rules file.",
    ),
    "roam_sbom": (
        ("--output",),
        "MCP returns the SBOM; CLI --output writes it to disk.",
    ),
    "roam_smells": (
        ("--persist",),
        "MCP returns smell findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_taint": (
        ("--persist",),
        "MCP returns taint findings; CLI --persist writes them to the findings registry.",
    ),
    "roam_tour": (
        ("--write",),
        "MCP returns the onboarding tour; CLI --write creates a tour artifact.",
    ),
    "roam_vibe_check": (
        ("--persist",),
        "MCP returns AI-rot findings; CLI --persist writes them to the findings registry.",
    ),
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


def _mcp_wrapper_executable_literals() -> dict[str, set[str]]:
    """Return executable string literals for each ``@_tool`` wrapper.

    Decorator descriptions and function docstrings are excluded: mentioning a
    CLI-only flag in documentation is safe; forwarding that flag in executable
    wrapper code is what would invalidate a read-only projection.
    """
    import ast
    import inspect

    import roam.mcp_server as mcp

    source = inspect.getsource(mcp)
    module = ast.parse(source)
    result: dict[str, set[str]] = {}
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tool_name = ""
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "_tool"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        tool_name = keyword.value.value
        if not tool_name:
            continue

        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        executable = ast.Module(body=body, type_ignores=[])
        result[tool_name] = {
            item.value
            for item in ast.walk(executable)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    return result


def test_read_only_projection_exceptions_are_evidence_backed():
    """Every CLI-max-effect/MCP-read-only divergence must remain narrowly true."""
    import importlib

    import roam.mcp_server as mcp

    caps = _load_full_capability_registry()
    wrapper_literals = _mcp_wrapper_executable_literals()
    stale_surface: list[tuple[str, object, object]] = []
    missing_cli_flags: list[tuple[str, list[str]]] = []
    forwarded_effect_flags: list[tuple[str, list[str]]] = []
    missing_wrappers: list[str] = []
    bad_rationales: list[str] = []

    for tool_name, (effect_flags, rationale) in _MCP_READ_ONLY_CLI_MAX_EFFECT_PROJECTIONS.items():
        meta = mcp._TOOL_METADATA.get(tool_name)
        cap_name = _mcp_name_to_capability_name(tool_name)
        cap = caps.get(cap_name)
        if meta is None or cap is None:
            stale_surface.append((tool_name, meta, cap))
            continue
        if meta.get("read_only", True) is not True or cap.side_effect is not True:
            stale_surface.append((tool_name, meta.get("read_only", True), cap.side_effect))
        if not effect_flags or not rationale.strip():
            bad_rationales.append(tool_name)

        command = getattr(importlib.import_module(cap.module), cap.func_name)
        cli_flags = {
            option
            for param in command.params
            for option in (*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ()))
        }
        absent = sorted(set(effect_flags) - cli_flags)
        if absent:
            missing_cli_flags.append((tool_name, absent))

        literals = wrapper_literals.get(tool_name)
        if literals is None:
            missing_wrappers.append(tool_name)
            continue
        forwarded = sorted(set(effect_flags) & literals)
        if forwarded:
            forwarded_effect_flags.append((tool_name, forwarded))

    assert not stale_surface, (
        "Stale MCP read-only projection classifications; each entry must still be "
        f"read_only=True against a side_effect=True CLI capability: {stale_surface}"
    )
    assert not bad_rationales, f"Projection entries require effect flags and a rationale: {bad_rationales}"
    assert not missing_cli_flags, f"Documented CLI effect flags no longer exist: {missing_cli_flags}"
    assert not missing_wrappers, f"Documented MCP wrappers no longer exist: {missing_wrappers}"
    assert not forwarded_effect_flags, (
        "Read-only MCP projections now forward CLI effect flags; update metadata or narrow the wrapper: "
        f"{forwarded_effect_flags}"
    )


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
    The evidence-backed map above admits only narrower MCP projections whose
    executable wrappers omit the broad CLI command's effectful flags.
    """
    import roam.mcp_server as mcp

    caps = _load_full_capability_registry()
    mismatches: list[tuple[str, str, str]] = []
    for tool_name, meta in mcp._TOOL_METADATA.items():
        cap_name = _mcp_name_to_capability_name(tool_name)
        cap = caps.get(cap_name)
        if cap is None:
            continue
        if tool_name in _MCP_READ_ONLY_CLI_MAX_EFFECT_PROJECTIONS:
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
        f"an evidence-backed entry to _MCP_READ_ONLY_CLI_MAX_EFFECT_PROJECTIONS."
    )
