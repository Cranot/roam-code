"""W954 — regression-guard snapshot for the _CORE_TOOLS vs @roam_capability drift.

W525 inventory STOPPED at Gate 2: the hand-rolled ``_CORE_TOOLS`` tuple in
``src/roam/mcp_server.py`` (57 entries) does NOT match the
``@roam_capability(category="core")`` derivation (which yields 0 entries
because ``"core"`` is not a real category). Separately, the default
``mcp_preset=("core",)`` boilerplate is inherited by 228 of 230
capabilities — dead metadata that does not control MCP exposure.

This file pins the CURRENT mismatch so any future PR that changes the
shape — intentionally or otherwise — is forced to update the test in
lockstep. It is NOT a fix; the real cleanup ships under W357 (MCP
registry unification). Until W357 lands:

* If you add/remove a core MCP tool, update ``test_core_tools_size``
  AND the ``"core preset"`` references in ``CLAUDE.md`` together.
* If a capability legitimately moves on/off the core preset, expect
  ``test_mcp_preset_inheritance_is_boilerplate`` to drift.
* If W357 (or W951) reduces either gap below the pinned floor, this
  test will fail with an instruction in the assertion message.
"""

from __future__ import annotations

import importlib

import pytest


def _import_all_command_modules() -> None:
    """Force-import every cmd_*.py module so the capability registry is full.

    The CLI's LazyGroup deliberately avoids these imports at startup, so
    the registry only populates as a side-effect of importing each
    backing module. Failures (optional extras, refactor-in-flight) are
    swallowed — downstream assertions will surface the real gap.
    """
    from roam.cli import _COMMANDS

    for _name, (module_path, _func_name) in _COMMANDS.items():
        try:
            importlib.import_module(module_path)
        except ImportError:
            pass


@pytest.fixture(scope="module", autouse=True)
def _populate_registry() -> None:
    _import_all_command_modules()
    yield


# Known MCP-name -> CLI/capability-name aliases where the two diverged
# beyond the uniform ``roam_<dashed>`` convention. Add new entries here
# only when a wrapper deliberately renames the tool relative to its
# backing CLI command. The uniform "strip roam_ prefix + swap _ for -"
# fallback handles every other case (15 such conventional drifts as of
# W954 — see ``_resolve_mcp_to_capability_name`` below).
_NAMING_DRIFT_ALIAS: dict[str, str] = {
    "roam_dead_code": "dead",
    "roam_complexity_report": "complexity",
    "roam_search_symbol": "search",
    "roam_file_info": "file",
}


def _resolve_mcp_to_capability_name(mcp_name: str) -> str:
    """Map an MCP tool name to its capability-registry equivalent.

    1. Explicit alias table wins (4 hand-curated drift cases).
    2. Otherwise strip the ``roam_`` prefix and convert underscores to
       dashes — the uniform "MCP names use underscores, capabilities
       use dashes" convention.
    """
    if mcp_name in _NAMING_DRIFT_ALIAS:
        return _NAMING_DRIFT_ALIAS[mcp_name]
    return mcp_name.removeprefix("roam_").replace("_", "-")


# ---------------------------------------------------------------------------
# Test 1 — _CORE_TOOLS size snapshot.
# ---------------------------------------------------------------------------

# Snapshot of len(_CORE_TOOLS) as of W954. Bump intentionally when adding
# or removing a core MCP tool, and update tests/test_w954 + CLAUDE.md
# 'core preset' references in lockstep.
_CORE_TOOLS_SIZE_SNAPSHOT = 57


def test_core_tools_size() -> None:
    """Pin len(_CORE_TOOLS) so any intentional change is a conscious edit."""
    from roam.mcp_server import _CORE_TOOLS

    assert len(_CORE_TOOLS) == _CORE_TOOLS_SIZE_SNAPSHOT, (
        f"_CORE_TOOLS size drifted from {_CORE_TOOLS_SIZE_SNAPSHOT} to "
        f"{len(_CORE_TOOLS)}. If this is an intentional core-preset edit, "
        f"update _CORE_TOOLS_SIZE_SNAPSHOT here AND every 'core preset' "
        f"reference in CLAUDE.md and dev/ docs in lockstep. If this is "
        f"W357 (registry unification) landing, retire this test."
    )


# ---------------------------------------------------------------------------
# Test 2 — mcp_preset=("core",) is dead metadata (boilerplate inheritance).
# ---------------------------------------------------------------------------

# Lower bound on the ratio of capabilities that inherit the default
# ``mcp_preset=("core",)`` value. Pinned as a floor so any cleanup
# (W951 / W357) will surface the inverse drift via the
# ``test_core_tools_vs_capability_preset_asymmetric_diff`` floor.
# We assert a floor here, not an exact count, because adding a new
# decorated command without overriding the default would push this
# value up by one and we want that to remain a non-event.
_MIN_BOILERPLATE_CORE_PRESET = 220


def test_mcp_preset_inheritance_is_boilerplate() -> None:
    """Document that mcp_preset='core' is inherited boilerplate, not curation.

    Of N total ``@roam_capability`` registrations, M carry ``"core"`` in
    their ``mcp_preset`` tuple — but ``_CORE_TOOLS`` only exposes 57.
    The gap proves the field is dead metadata (inherited from the
    decorator default, never overridden), not a curation signal.
    """
    from roam.capability import REGISTRY

    all_caps = list(REGISTRY.items.values())
    core_preset_caps = [c for c in all_caps if "core" in c.mcp_preset]

    assert len(core_preset_caps) >= _MIN_BOILERPLATE_CORE_PRESET, (
        f"mcp_preset='core' inheritance dropped to {len(core_preset_caps)} "
        f"of {len(all_caps)} capabilities; the floor was "
        f"{_MIN_BOILERPLATE_CORE_PRESET}. If this is W951/W357 cleanup "
        f"(decorators explicitly setting mcp_preset=()), update the floor."
    )
    # And pin that the category 'core' really is empty — the W525
    # "0 entries from category=core derivation" finding.
    category_core = [c for c in all_caps if c.category == "core"]
    assert category_core == [], (
        f"Unexpected capabilities with category='core': "
        f"{[c.name for c in category_core]}. W525 reported zero such "
        f"capabilities; if you've added one, this is W357 progress and "
        f"this test needs updating."
    )


# ---------------------------------------------------------------------------
# Test 3 — asymmetric diff between _CORE_TOOLS and mcp_preset='core'.
# ---------------------------------------------------------------------------

# Floors derived from the W954 snapshot run on roam-code HEAD:
#   in_core_not_cap = 20  (MCP-only constructs — compound wrappers,
#                          oracles, batch ops, diagnose-issue, explore,
#                          fetch-handle, validate-plan, ...)
#   in_cap_not_core = 191 (boilerplate-preset inheritors that are NOT
#                          in _CORE_TOOLS)
# Pinned as >= floors with ~10% headroom so adding a single decorated
# command does not churn the test. Reductions below the floor mean
# real progress and the floor should be lowered along with the work.
_MIN_IN_CORE_NOT_CAPABILITY = 18
_MIN_IN_CAPABILITY_NOT_CORE = 180


def test_core_tools_vs_capability_preset_asymmetric_diff() -> None:
    """Pin the asymmetric diff between the two 'what is core?' sources of truth.

    ``_CORE_TOOLS`` (mcp_server.py) and ``mcp_preset=("core",)`` (per-
    capability decorator metadata) should AGREE on which tools are
    "core". They don't. This test pins both halves of the disagreement
    so any movement — toward unification or further drift — surfaces
    at PR time.
    """
    from roam.capability import REGISTRY
    from roam.mcp_server import _CORE_TOOLS

    core_tools_cli = {_resolve_mcp_to_capability_name(t) for t in _CORE_TOOLS}
    cap_preset_core = {c.name for c in REGISTRY.items.values() if "core" in c.mcp_preset}

    in_core_not_cap = core_tools_cli - cap_preset_core
    in_cap_not_core = cap_preset_core - core_tools_cli

    assert len(in_core_not_cap) >= _MIN_IN_CORE_NOT_CAPABILITY, (
        f"in_core_not_cap shrank from >={_MIN_IN_CORE_NOT_CAPABILITY} to "
        f"{len(in_core_not_cap)}. Either an MCP-only construct grew a "
        f"capability decorator (good — W357 progress; lower the floor) "
        f"or the naming-drift alias table grew (also good — extend "
        f"_NAMING_DRIFT_ALIAS). Current entries: {sorted(in_core_not_cap)}"
    )
    assert len(in_cap_not_core) >= _MIN_IN_CAPABILITY_NOT_CORE, (
        f"in_cap_not_core shrank from >={_MIN_IN_CAPABILITY_NOT_CORE} to "
        f"{len(in_cap_not_core)}. If this is W951 work (decorators "
        f"explicitly opting out of the 'core' preset with mcp_preset=()), "
        f"lower the floor to match the new boilerplate-cleanup baseline. "
        f"Sample of remaining inheritors: "
        f"{sorted(in_cap_not_core)[:10]}"
    )
