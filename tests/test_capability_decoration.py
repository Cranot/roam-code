"""Enforce the Capability Registry decoration contract — auto-derived.

This test used to maintain a hand-curated ``DECORATED_TODAY`` set and a
matching ``_FORCE_IMPORT_MODULES`` tuple. Every new command needed two
bookkeeping touches, and that bookkeeping drifted four times during the
sprint (W4.3, W7.5, W10.4, W14.4, W19.5). Auto-generation eliminates
the entire drift class:

1. The set of modules-to-import is derived from ``roam.cli._COMMANDS``
   itself. No hand list to fall out of sync.
2. The set of commands that legitimately *cannot* carry a
   ``@roam_capability`` decoration (function-sharing aliases) is derived
   from ``roam.cli._DEPRECATED_COMMANDS`` — the same dict the CLI uses
   to drive the deprecation-warning behavior, so the two stay in lockstep
   by construction.
3. The single remaining hand-curated entry is ``"mcp"`` — the top-level
   ``mcp`` CLI command points at ``cmd_mcp.py`` but its semantics live in
   ``roam/mcp_server.py``, which is out of scope for the capability
   sweep. If that ever changes, drop the ``{"mcp"}`` carve-out below.

The contract this test enforces:

* Every command in ``_COMMANDS`` that is not a function-sharing alias
  (and not ``mcp``) must register a ``Capability`` in the global
  ``REGISTRY``. If a new command lands without ``@roam_capability``, the
  assertion fails with the offending names sorted for easy diagnosis.

There is no per-command bookkeeping. Future "new command" PRs don't
need to remember anything — the test catches drift automatically.
"""

from __future__ import annotations

import importlib

import pytest


def _import_all_command_modules() -> None:
    """Force-import every module backing a ``_COMMANDS`` entry.

    The capability registry is populated as a side-effect of importing
    each ``cmd_*.py`` module. The CLI's ``LazyGroup`` deliberately avoids
    those imports at startup, so this test must trigger them itself —
    relying on test-collection order is a recipe for flakes.

    Imports that fail (optional extras, modules removed during a refactor)
    are swallowed silently here; the downstream assertion will surface
    them as "missing from registry" with the offending command names,
    which is a far more actionable failure than a raw ``ImportError``.
    """
    from roam.cli import _COMMANDS

    for _name, (module_path, _func_name) in _COMMANDS.items():
        try:
            importlib.import_module(module_path)
        except ImportError:
            pass


def _function_sharing_aliases() -> set[str]:
    """Return command names that share a function with a canonical command.

    The capability decorator picks ONE name per function (it indexes by
    ``Capability.name`` and a single ``register()`` call wins the slot),
    so these names will never carry an independent decoration. The
    authoritative source is ``cli._DEPRECATED_COMMANDS`` — the dict that
    drives the runtime alias-deprecation warnings — which already lists
    every such alias and its canonical replacement.
    """
    from roam.cli import _DEPRECATED_COMMANDS

    return set(_DEPRECATED_COMMANDS.keys())


# ``mcp`` is a top-level CLI entry point that lives in
# ``src/roam/commands/cmd_mcp.py`` but delegates to ``src/roam/mcp_server.py``,
# which is out of scope for the capability sweep (the file is owned by the
# MCP server itself and tracked separately). If a ``@roam_capability`` ever
# lands on ``cmd_mcp``'s entry function, remove this carve-out.
_MCP_CARVE_OUT: set[str] = {"mcp"}


@pytest.fixture(scope="module", autouse=True)
def _populate_registry():
    """Pin the capability registry state for every test in this module.

    Without this fixture the FIRST run of a fresh pytest sweep can observe
    a partially-populated registry if some upstream fixture imported
    ``roam.cli`` before any ``cmd_*.py`` module was loaded.
    """
    _import_all_command_modules()
    yield


def test_every_command_decorated() -> None:
    """Every CLI command must carry ``@roam_capability`` except aliases.

    The "expected decorated" set is derived as
    ``_COMMANDS - _DEPRECATED_COMMANDS - {"mcp"}``. Any drift from that
    expectation surfaces here with the offending names, so the fix is
    always "add ``@roam_capability(...)`` to the listed cmd_*.py files".
    """
    from roam.capability import REGISTRY
    from roam.cli import _COMMANDS

    never_decorate = _function_sharing_aliases() | _MCP_CARVE_OUT
    expected_decorated = set(_COMMANDS.keys()) - never_decorate
    actually_decorated = set(REGISTRY.items.keys())

    missing = expected_decorated - actually_decorated
    assert not missing, (
        f"{len(missing)} commands lack @roam_capability decoration: "
        f"{sorted(missing)}. Add @roam_capability(...) to the entry "
        f"function in their cmd_*.py module, or — if the command is a "
        f"function-sharing alias — list it in cli._DEPRECATED_COMMANDS."
    )


def test_never_decorate_set_is_internally_consistent() -> None:
    """Sanity-check the auto-derived carve-out.

    Every name in ``NEVER_DECORATE`` must be a real ``_COMMANDS`` entry
    (otherwise the carve-out is silently masking a typo). And every name
    in ``_DEPRECATED_COMMANDS`` must point at a function shared with at
    least one other command in ``_COMMANDS`` (otherwise the alias claim
    is wrong and the command could carry its own decoration).
    """
    from collections import defaultdict

    from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS

    never_decorate = _function_sharing_aliases() | _MCP_CARVE_OUT

    stale = never_decorate - set(_COMMANDS.keys())
    assert not stale, (
        f"NEVER_DECORATE references commands that are not in _COMMANDS: "
        f"{sorted(stale)} — remove the carve-out or fix the typo."
    )

    by_target: dict[tuple, list[str]] = defaultdict(list)
    for name, target in _COMMANDS.items():
        by_target[target].append(name)

    not_actually_shared = [name for name in _DEPRECATED_COMMANDS if len(by_target[_COMMANDS[name]]) < 2]
    assert not not_actually_shared, (
        f"these names are listed as aliases but their _COMMANDS target "
        f"is not shared with any other command — they could and should "
        f"carry their own @roam_capability: {sorted(not_actually_shared)}"
    )


def test_new_capability_fields_have_conservative_defaults() -> None:
    """The control-plane fields added in A1 must default to safe values.

    Verifies that an existing decorator (no new kwargs supplied) still
    produces a Capability with the expected defaults. This protects the
    Phase 0 commands (permit, postmortem, article-12-check, etc.) from
    accidental behavior changes.
    """
    from roam.capability import Capability

    cap = Capability(name="X", category="c", summary="s")
    assert cap.maturity == "stable"
    assert cap.mcp_expose is True
    assert cap.mcp_preset == ("core",)
    assert cap.side_effect is False
    assert cap.task_required is False
    assert cap.destructive is False
    assert cap.stale_sensitive is True
    assert cap.displaces == ()
