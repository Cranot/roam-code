"""Plugin discovery and extension registration for roam-code.

This package is the substrate for ``roam-plugin-*`` third-party
packages (framework analyzers — nextjs, laravel, django, prisma, …).

Two discovery channels are supported:

1) Python entry points under group ``roam.plugins`` (production path —
   pip-installed plugins register themselves declaratively in
   their ``pyproject.toml``)::

       [project.entry-points."roam.plugins"]
       nextjs = "roam_plugin_nextjs:register"

2) Environment variable ``ROAM_PLUGIN_MODULES`` (development /
   testing — comma-separated importable module names).

Each plugin must expose either a top-level ``register(ctx)`` callable
or a module-level attribute named ``register`` that is callable. The
``ctx`` argument is a :class:`RoamPluginContext` — see
``src/roam/plugins/registry.py``.

Backward compatibility
======================

The legacy :class:`PluginAPI` class — used by the v12.x plugin
contract — is preserved as an alias for :class:`RoamPluginContext`.
Existing plugins that did ``def register(api): api.register_command(...)``
keep working unchanged.

New plugins should prefer the typed :class:`RoamPluginContext` name
and the additional hooks it exposes (``register_framework_detector``,
``register_bridge``).
"""

from __future__ import annotations

import importlib
import logging
import os
from importlib import metadata as importlib_metadata
from types import MappingProxyType
from typing import Any, Mapping

from .registry import (
    DetectorSpec,
    Finding,
    FrameworkProfile,
    RoamPlugin,
    RoamPluginContext,
    _registry_state,
    get_framework_profile,
)
from .registry import (
    get_framework_profiles as _registry_get_framework_profiles,
)

# Backward-compatible alias — existing plugins import this name.
PluginAPI = RoamPluginContext

# Legacy module-level CommandTarget type alias kept for callers (catalog
# helpers, cli loader) that imported it directly.
CommandTarget = tuple[str, str]

_LOGGER = logging.getLogger(__name__)


__all__ = [
    "CommandTarget",
    "DetectorSpec",
    "Finding",
    "FrameworkProfile",
    "PluginAPI",
    "RoamPlugin",
    "RoamPluginContext",
    "discover_plugins",
    "get_framework_profile",
    "get_framework_profiles",
    "get_plugin_commands",
    "get_plugin_detectors",
    "get_plugin_errors",
    "get_plugin_framework_detectors",
    "get_plugin_framework_profiles",
    "get_plugin_bridges",
    "get_plugin_language_extensions",
    "get_plugin_language_extractors",
    "get_plugin_language_grammar_aliases",
    "get_plugins",
]


# discover_plugins() is the canonical public loader for this substrate.
_discovered = False


def _plugin_snapshot() -> list[RoamPlugin]:
    """Return a defensive copy of the registry's plugin list."""
    return list(_registry_state().plugins)


def _register_target(target: Any, source_label: str, ctx: RoamPluginContext) -> None:
    """Invoke a plugin's ``register`` hook safely.

    A broken plugin must never crash roam itself — we record the failure
    on the registry and skip the plugin. ``roam plugins doctor`` reads
    those errors and surfaces them to the user.
    """
    state = _registry_state()
    try:
        register_fn = None
        if callable(target):
            register_fn = target
        else:
            attr = getattr(target, "register", None)
            if callable(attr):
                register_fn = attr
        if register_fn is None:
            raise TypeError("plugin target must be callable or expose register(ctx)")

        state.current_source = source_label
        try:
            register_fn(ctx)
        finally:
            state.current_source = None
    except Exception as exc:  # noqa: BLE001 — substrate must be safe
        state.errors.append(f"{source_label}: {exc}")


def _discover_env_modules(ctx: RoamPluginContext) -> None:
    """Load plugins listed in ``ROAM_PLUGIN_MODULES`` (dev/test channel)."""
    modules_raw = os.environ.get("ROAM_PLUGIN_MODULES", "")
    if not modules_raw:
        return

    state = _registry_state()
    for module_name in [m.strip() for m in modules_raw.split(",") if m.strip()]:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 — never break core
            state.errors.append(f"module:{module_name}: import failed: {exc}")
            continue
        _register_target(module, f"module:{module_name}", ctx)


# ``importlib_metadata.entry_points()`` walks every installed
# package's metadata; ~100 ms cold. Cache results for the process
# lifetime so subsequent discovery calls reuse the scan.
_ENTRY_POINT_CACHE: dict[str, list] = {}


def _entry_points_for_group(group: str):
    cached = _ENTRY_POINT_CACHE.get(group)
    if cached is not None:
        return cached
    eps = importlib_metadata.entry_points()
    if hasattr(eps, "select"):
        result = list(eps.select(group=group))
    elif isinstance(eps, dict):
        result = list(eps.get(group, []))
    else:
        result = []
    _ENTRY_POINT_CACHE[group] = result
    return result


def _entry_point_metadata_for_info(ep: Any) -> tuple[str, str]:
    """Return best-effort metadata used by ``roam plugins info``."""
    version = "unknown"
    description = ""
    try:
        dist = ep.dist  # type: ignore[attr-defined]
        if dist is not None:
            version = dist.version
            description = (dist.metadata.get("Summary") or "").strip()
    except (AttributeError, KeyError) as exc:
        # Missing entry-point distribution metadata is expected for
        # path-installed plugins, so keep doctor output clean.
        _LOGGER.debug(
            "plugin metadata lookup skipped for entry point %r: %s",
            ep.name,
            exc,
        )
    return version, description


def _discover_entry_points(ctx: RoamPluginContext) -> None:
    """Load plugins declared via Python entry points (production channel)."""
    state = _registry_state()
    try:
        entries = _entry_points_for_group("roam.plugins")
    except Exception as exc:  # noqa: BLE001
        state.errors.append(f"entry_points: discovery failed: {exc}")
        return

    for ep in entries:
        try:
            target = ep.load()
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"entry_point:{ep.name}: load failed: {exc}")
            continue

        # Capture metadata so ``roam plugins info <name>`` can answer
        # without re-importing every plugin. Distribution lookup is
        # best-effort — entry points loaded from path-installed modules
        # may not carry a distribution.
        version, description = _entry_point_metadata_for_info(ep)

        state.current_plugin_meta = (ep.name, version, description)
        try:
            _register_target(target, f"entry_point:{ep.name}", ctx)
        finally:
            state.current_plugin_meta = None


def discover_plugins() -> list[RoamPlugin]:
    """Discover and register all plugins once per process.

    Discovery is idempotent — the first call walks entry points + env
    modules, subsequent calls return the cached plugin list. Loading
    failures are absorbed onto :func:`get_plugin_errors` rather than
    raised; the plugin substrate must never crash roam itself.

    Returns the list of successfully-loaded plugins.
    """
    global _discovered
    if _discovered:
        return _plugin_snapshot()
    _discovered = True

    ctx = RoamPluginContext()
    _discover_env_modules(ctx)
    _discover_entry_points(ctx)
    return _plugin_snapshot()


def get_plugins() -> list[RoamPlugin]:
    """Return the list of registered plugins (auto-discovers on first call)."""
    discover_plugins()
    return _plugin_snapshot()


def get_plugin_commands() -> dict[str, CommandTarget]:
    discover_plugins()
    return dict(_registry_state().commands)


def get_plugin_detectors() -> list[DetectorSpec]:
    discover_plugins()
    return list(_registry_state().detectors)


def get_plugin_language_extractors() -> dict[str, Any]:
    discover_plugins()
    return dict(_registry_state().language_extractors)


def get_plugin_language_extensions() -> dict[str, str]:
    discover_plugins()
    return dict(_registry_state().language_extensions)


def get_plugin_language_grammar_aliases() -> dict[str, str]:
    discover_plugins()
    return dict(_registry_state().language_grammar_aliases)


def get_plugin_framework_detectors() -> list:
    """Return registered framework detectors (callables ``(Path) -> Optional[str]``)."""
    discover_plugins()
    return list(_registry_state().framework_detectors)


def get_plugin_framework_profiles() -> dict[str, FrameworkProfile]:
    """Return registered framework profiles keyed by framework name (W123).

    Plugins call :meth:`RoamPluginContext.register_framework_profile`
    to declare a richer :class:`FrameworkProfile` than the legacy
    detector-only API. Returns an empty dict on a clean install.
    """
    discover_plugins()
    return dict(_registry_get_framework_profiles())


def _discovered_framework_profile_map() -> Mapping[str, Any]:
    """Return discovered framework profiles as a read-only mapping."""
    return MappingProxyType(get_plugin_framework_profiles())


# Preserve the package-level re-export without shadowing the registry helper.
def __getattr__(name: str) -> Any:
    if name == "get_framework_profiles":
        return _discovered_framework_profile_map
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_plugin_bridges() -> list:
    """Return registered cross-language bridges (LanguageBridge instances)."""
    discover_plugins()
    return list(_registry_state().bridges)


def get_plugin_errors() -> list[str]:
    discover_plugins()
    return list(_registry_state().errors)


def _reset_plugin_state_for_tests() -> None:
    """Reset global plugin state (test-only helper).

    Used by ``tests/test_plugin_discovery.py`` /
    ``tests/test_plugin_substrate.py`` to inject fake plugins between
    runs. Also clears the entry-point cache so tests that monkey-patch
    ``importlib.metadata.entry_points`` see fresh results.
    """
    global _discovered
    _discovered = False
    _ENTRY_POINT_CACHE.clear()
    state = _registry_state()
    try:
        from roam.bridges import registry as bridge_registry
    except ImportError:
        bridge_registry = None  # type: ignore[assignment]
    if bridge_registry is not None:
        for bridge in state.bridges:
            try:
                bridge_registry._BRIDGES.remove(bridge)
            except ValueError as exc:
                _LOGGER.debug(
                    "plugin bridge reset skipped for unregistered bridge %r: %s",
                    bridge,
                    exc,
                )
    state.plugins.clear()
    state.errors.clear()
    state.commands.clear()
    state.detectors.clear()
    state.framework_detectors.clear()
    state.framework_profiles.clear()
    state.bridges.clear()
    state.language_extractors.clear()
    state.language_extensions.clear()
    state.language_grammar_aliases.clear()
    state.current_source = None
    state.current_plugin_meta = None
    state.current_capabilities.clear()
