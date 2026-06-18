"""Bridge registry -- discovers and manages cross-language bridges."""

from __future__ import annotations

import logging

from roam.bridges.base import LanguageBridge

log = logging.getLogger(__name__)

_BRIDGES: list[LanguageBridge] = []
_DISCOVERED = False
_REQUIRED_BRIDGE_ATTRS = ("name", "detect", "resolve")


def register_bridge(bridge: LanguageBridge) -> None:
    """Register a bridge instance."""
    for attr in _REQUIRED_BRIDGE_ATTRS:
        if not hasattr(bridge, attr):
            raise TypeError(f"bridge missing required attribute: {attr}")
    _BRIDGES.append(bridge)


def get_bridges() -> list[LanguageBridge]:
    """Return all registered bridges."""
    return list(_BRIDGES)


def detect_bridges(file_paths: list[str]) -> list[LanguageBridge]:
    """Return bridges relevant for the given file set."""
    _auto_discover()
    return [b for b in _BRIDGES if b.detect(file_paths)]


def _auto_discover():
    """Auto-discover built-in + plugin-contributed bridges on first call."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True

    # Import built-in bridges -- each registers itself on import.
    # W907/Pattern-2 discipline: built-in bridges ship inside this package,
    # so ImportError here means a real install / syntax / dependency bug —
    # NOT an optional-feature absence. Emit a WARN so the bridge surface
    # silently degrading is observable (the bridges later return [] to
    # downstream consumers, which is indistinguishable from "no work to do"
    # without this sentinel).
    for _builtin in (
        "bridge_salesforce",
        "bridge_protobuf",
        "bridge_rest_api",
        "bridge_template",
        "bridge_config",
        "bridge_django",
    ):
        try:
            __import__(f"roam.bridges.{_builtin}")
        except ImportError as exc:
            log.warning(
                "built-in bridge roam.bridges.%s failed to import (%s: %s); "
                "cross-language resolution for this bridge will be inactive",
                _builtin,
                type(exc).__name__,
                exc,
            )

    # Plugin-contributed bridges (roam-plugin-* packages).
    #
    # The plugin substrate (``roam.plugins.discover_plugins``) already
    # absorbs broken-plugin exceptions onto ``get_plugin_errors()``
    # — ``get_plugin_bridges`` returns ``[]`` on a fully-broken plugin
    # rather than raising. We narrow this guard to ``ImportError`` so
    # the only legitimate failure (plugin substrate genuinely missing,
    # e.g. partial install) stays absorbed; any other exception now
    # propagates so we hear about it instead of silently degrading the
    # bridge surface (W907/Pattern-2 discipline).
    try:
        from roam.plugins import get_plugin_bridges
    except ImportError:
        get_plugin_bridges = None  # type: ignore[assignment]

    if get_plugin_bridges is not None:
        for bridge in get_plugin_bridges():
            if bridge not in _BRIDGES:
                register_bridge(bridge)
