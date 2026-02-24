"""Plugin discovery and extension registration for roam-code.

Plugins can be discovered in two ways:
1) Python entry points under group ``roam.plugins``
2) Environment variable ``ROAM_PLUGIN_MODULES`` (comma-separated modules)

Each plugin should expose either:
- a callable that accepts ``PluginAPI``, or
- an object/module with a callable ``register(api)`` function.
"""

from __future__ import annotations

import importlib
import os
from importlib import metadata as importlib_metadata
from typing import Any, Callable


CommandTarget = tuple[str, str]
DetectorSpec = tuple[str, str, Callable[[Any], list[dict]]]


_discovered = False
_errors: list[str] = []
_commands: dict[str, CommandTarget] = {}
_detectors: list[DetectorSpec] = []
_language_extractors: dict[str, Callable[[], Any]] = {}
_language_extensions: dict[str, str] = {}
_language_grammar_aliases: dict[str, str] = {}


def _normalize_extension(ext: str) -> str:
    ext = (ext or "").strip().lower()
    if not ext:
        return ""
    if not ext.startswith("."):
        return f".{ext}"
    return ext


class PluginAPI:
    """Registration surface exposed to third-party roam plugins."""

    def register_command(self, name: str, module_path: str, attr_name: str) -> None:
        cmd = (name or "").strip()
        mod = (module_path or "").strip()
        attr = (attr_name or "").strip()
        if not cmd:
            raise ValueError("command name must be non-empty")
        if not mod:
            raise ValueError("module_path must be non-empty")
        if not attr:
            raise ValueError("attr_name must be non-empty")
        if cmd in _commands:
            raise ValueError(f"duplicate plugin command: {cmd}")
        _commands[cmd] = (mod, attr)

    def register_detector(
        self,
        task_id: str,
        way_id: str,
        detect_fn: Callable[[Any], list[dict]],
    ) -> None:
        task = (task_id or "").strip()
        way = (way_id or "").strip()
        if not task:
            raise ValueError("task_id must be non-empty")
        if not way:
            raise ValueError("way_id must be non-empty")
        if not callable(detect_fn):
            raise TypeError("detect_fn must be callable")
        _detectors.append((task, way, detect_fn))

    def register_language_extractor(
        self,
        language: str,
        extractor_factory: Callable[[], Any],
        *,
        extensions: list[str] | tuple[str, ...] | None = None,
        grammar_alias: str | None = None,
    ) -> None:
        lang = (language or "").strip().lower()
        if not lang:
            raise ValueError("language must be non-empty")
        if not callable(extractor_factory):
            raise TypeError("extractor_factory must be callable")

        _language_extractors[lang] = extractor_factory

        if extensions:
            for ext in extensions:
                norm = _normalize_extension(ext)
                if norm:
                    _language_extensions[norm] = lang

        if grammar_alias:
            grammar = (grammar_alias or "").strip()
            if grammar:
                _language_grammar_aliases[lang] = grammar


def _register_target(target: Any, source_label: str, api: PluginAPI) -> None:
    try:
        if callable(target):
            target(api)
            return

        register_fn = getattr(target, "register", None)
        if callable(register_fn):
            register_fn(api)
            return

        raise TypeError(
            "plugin target must be callable or expose register(api)"
        )
    except Exception as exc:
        _errors.append(f"{source_label}: {exc}")


def _discover_env_modules(api: PluginAPI) -> None:
    modules_raw = os.environ.get("ROAM_PLUGIN_MODULES", "")
    if not modules_raw:
        return

    for module_name in [m.strip() for m in modules_raw.split(",") if m.strip()]:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            _errors.append(f"module:{module_name}: import failed: {exc}")
            continue
        _register_target(module, f"module:{module_name}", api)


def _entry_points_for_group(group: str):
    eps = importlib_metadata.entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    if isinstance(eps, dict):
        return list(eps.get(group, []))
    return []


def _discover_entry_points(api: PluginAPI) -> None:
    try:
        entries = _entry_points_for_group("roam.plugins")
    except Exception as exc:
        _errors.append(f"entry_points: discovery failed: {exc}")
        return

    for ep in entries:
        try:
            target = ep.load()
        except Exception as exc:
            _errors.append(f"entry_point:{ep.name}: load failed: {exc}")
            continue
        _register_target(target, f"entry_point:{ep.name}", api)


def discover_plugins() -> None:
    """Discover and register plugins once per process."""
    global _discovered
    if _discovered:
        return
    _discovered = True

    api = PluginAPI()
    _discover_env_modules(api)
    _discover_entry_points(api)


def get_plugin_commands() -> dict[str, CommandTarget]:
    discover_plugins()
    return dict(_commands)


def get_plugin_detectors() -> list[DetectorSpec]:
    discover_plugins()
    return list(_detectors)


def get_plugin_language_extractors() -> dict[str, Callable[[], Any]]:
    discover_plugins()
    return dict(_language_extractors)


def get_plugin_language_extensions() -> dict[str, str]:
    discover_plugins()
    return dict(_language_extensions)


def get_plugin_language_grammar_aliases() -> dict[str, str]:
    discover_plugins()
    return dict(_language_grammar_aliases)


def get_plugin_errors() -> list[str]:
    discover_plugins()
    return list(_errors)


def _reset_plugin_state_for_tests() -> None:
    """Reset global plugin state (test-only helper)."""
    global _discovered
    _discovered = False
    _errors.clear()
    _commands.clear()
    _detectors.clear()
    _language_extractors.clear()
    _language_extensions.clear()
    _language_grammar_aliases.clear()
