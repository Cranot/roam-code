"""Bridge registry -- discovers and manages cross-language bridges."""

from __future__ import annotations

from roam.bridges.base import LanguageBridge

_BRIDGES: list[LanguageBridge] = []


def register_bridge(bridge: LanguageBridge) -> None:
    """Register a bridge instance."""
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
    if _BRIDGES:
        return

    # Import built-in bridges -- each registers itself on import
    try:
        from roam.bridges import bridge_salesforce  # noqa: F401
    except ImportError:
        pass
    try:
        from roam.bridges import bridge_protobuf  # noqa: F401
    except ImportError:
        pass
    try:
        from roam.bridges import bridge_rest_api  # noqa: F401
    except ImportError:
        pass
    try:
        from roam.bridges import bridge_template  # noqa: F401
    except ImportError:
        pass
    try:
        from roam.bridges import bridge_config  # noqa: F401
    except ImportError:
        pass
    try:
        from roam.bridges import bridge_django  # noqa: F401
    except ImportError:
        pass

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
                _BRIDGES.append(bridge)
