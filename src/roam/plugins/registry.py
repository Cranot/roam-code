"""Plugin registry — the typed contract for roam plugin authors.

This module defines the **public API surface** every roam plugin sees:

- :class:`RoamPluginContext` — the object passed to your plugin's
  ``register(ctx)`` function. It exposes typed ``register_*`` methods
  for the four extension points roam supports today (commands,
  detectors, language extractors, framework detectors, bridges).
- :class:`RoamPlugin` — the metadata record produced once your plugin
  has been loaded. ``roam plugins list --json`` returns a list of
  these.
- :class:`Finding` — the contract every detector returns. Matches the
  shape produced by built-in detectors in
  ``src/roam/catalog/detectors.py`` so plugin findings flow through
  the same downstream pipelines (``roam algo``, ``roam recommend``).

Substrate contract
==================

The registry intentionally does NOT enforce ordering, plugin
precedence, or conflict resolution beyond "first-write-wins on
command-name collisions". Plugin authors who need stricter semantics
should namespace their commands (``nextjs-routes`` rather than
``routes``).

A broken plugin is recorded as an error string on the registry —
never propagated. ``roam plugins doctor`` surfaces those errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from roam.bridges.base import LanguageBridge
    from roam.languages.base import LanguageExtractor


# A detector returns a list of finding dicts. We keep ``Finding`` as a
# permissive ``dict`` alias rather than a dataclass to match the
# in-repo built-in detector contract — converting to a strict
# dataclass would force every existing detector to migrate.
Finding = dict[str, Any]

# Legacy detector tuple shape: ``(task_id, way_id, detect_fn)``.
DetectorSpec = tuple[str, str, Callable[[Any], list[Finding]]]


@dataclass
class RoamPlugin:
    """Metadata for a successfully-loaded plugin.

    Populated by the discovery loop in ``plugins/__init__.py`` once a
    plugin's ``register(ctx)`` call has returned successfully.
    """

    name: str
    version: str = "unknown"
    description: str = ""
    source: str = ""  # "entry_point:nextjs" or "module:my_dev_plugin"
    capabilities: list[str] = field(default_factory=list)


@dataclass
class _RegistryState:
    """Process-wide state populated as plugins call back into the context.

    Internal — plugin authors never see this directly. The public
    surface is :class:`RoamPluginContext`.
    """

    plugins: list[RoamPlugin] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: dict[str, tuple[str, str]] = field(default_factory=dict)
    detectors: list[DetectorSpec] = field(default_factory=list)
    framework_detectors: list[Callable[[Path], "str | None"]] = field(default_factory=list)
    bridges: list["LanguageBridge"] = field(default_factory=list)
    language_extractors: dict[str, Callable[[], "LanguageExtractor"]] = field(default_factory=dict)
    language_extensions: dict[str, str] = field(default_factory=dict)
    language_grammar_aliases: dict[str, str] = field(default_factory=dict)

    # Scratchpad — populated by the discovery loop just before calling
    # a plugin's ``register()``, read by RoamPluginContext to attribute
    # contributions to the right RoamPlugin.
    current_source: str | None = None
    current_plugin_meta: tuple[str, str, str] | None = None  # (name, version, description)
    current_capabilities: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.plugins.clear()
        self.errors.clear()
        self.commands.clear()
        self.detectors.clear()
        self.framework_detectors.clear()
        self.bridges.clear()
        self.language_extractors.clear()
        self.language_extensions.clear()
        self.language_grammar_aliases.clear()
        self.current_source = None
        self.current_plugin_meta = None
        self.current_capabilities.clear()


_STATE: _RegistryState | None = None


def _registry_state() -> _RegistryState:
    """Return the process-wide registry state (lazy-init singleton)."""
    global _STATE
    if _STATE is None:
        _STATE = _RegistryState()
    return _STATE


def _normalize_extension(ext: str) -> str:
    ext = (ext or "").strip().lower()
    if not ext:
        return ""
    if not ext.startswith("."):
        return f".{ext}"
    return ext


class RoamPluginContext:
    """The object passed to your plugin's ``register(ctx)`` callable.

    Each ``register_*`` method wires one extension point into roam.
    Calls are recorded against the currently-loading plugin so
    ``roam plugins info <name>`` can list what each plugin
    contributes.

    Example::

        def register(ctx: RoamPluginContext) -> None:
            ctx.declare(
                name="example",
                version="0.1.0",
                description="Reference roam plugin",
            )
            ctx.register_framework_detector(detect_example)
            ctx.register_detector("my-task", "my-way", run_detector)
            ctx.register_language_extractor(
                "qml", QmlExtractorFactory, extensions=[".qml"]
            )
    """

    # ---- declaration ----------------------------------------------------

    def declare(
        self,
        *,
        name: str,
        version: str = "0.1.0",
        description: str = "",
    ) -> None:
        """Declare this plugin's identity.

        Optional — if a plugin doesn't call ``declare()``, we infer the
        name from the entry-point key. Calling ``declare()`` explicitly
        is recommended because it lets a single plugin package expose
        multiple entry points (e.g. ``nextjs`` and ``nextjs-edge``)
        with distinct metadata.
        """
        state = _registry_state()
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("plugin name must be non-empty")
        plugin = RoamPlugin(
            name=clean_name,
            version=(version or "unknown").strip() or "unknown",
            description=(description or "").strip(),
            source=state.current_source or "",
        )
        state.plugins.append(plugin)

    # ---- command registration -------------------------------------------

    def register_command(self, name: str, module_path: str, attr_name: str) -> None:
        """Register a new CLI subcommand.

        The Click command lives in your plugin package; roam imports it
        lazily on first invocation, mirroring how core commands are
        loaded via ``LazyGroup``.

        Args:
            name: Subcommand name (``roam <name>``). Must be unique across
                all plugins.
            module_path: Importable module containing the command.
            attr_name: Attribute on the module that is the Click command
                object.
        """
        cmd = (name or "").strip()
        mod = (module_path or "").strip()
        attr = (attr_name or "").strip()
        if not cmd:
            raise ValueError("command name must be non-empty")
        if not mod:
            raise ValueError("module_path must be non-empty")
        if not attr:
            raise ValueError("attr_name must be non-empty")

        state = _registry_state()
        if cmd in state.commands:
            raise ValueError(f"duplicate plugin command: {cmd}")
        state.commands[cmd] = (mod, attr)
        self._note_capability("command")

    # ---- detector registration ------------------------------------------

    def register_detector(
        self,
        task_id: str,
        way_id: str,
        detect_fn: Callable[[Any], list[Finding]],
    ) -> None:
        """Register an algorithm-catalog detector.

        Detectors are called by ``roam algo`` / ``roam recommend`` to
        find suboptimal patterns. Your ``detect_fn`` receives a
        ``sqlite3.Connection`` and returns a list of finding dicts in
        the shape documented at the top of
        ``src/roam/catalog/detectors.py``.
        """
        task = (task_id or "").strip()
        way = (way_id or "").strip()
        if not task:
            raise ValueError("task_id must be non-empty")
        if not way:
            raise ValueError("way_id must be non-empty")
        if not callable(detect_fn):
            raise TypeError("detect_fn must be callable")

        state = _registry_state()
        state.detectors.append((task, way, detect_fn))
        self._note_capability("detector")

    # ---- language extractor registration --------------------------------

    def register_language_extractor(
        self,
        language: str,
        extractor_factory: Callable[[], "LanguageExtractor"],
        *,
        extensions: list[str] | tuple[str, ...] | None = None,
        grammar_alias: str | None = None,
    ) -> None:
        """Register a per-language symbol/reference extractor.

        Args:
            language: Lowercase language identifier (e.g. ``"nextjs-mdx"``).
            extractor_factory: Zero-arg callable returning a
                ``LanguageExtractor`` instance.
            extensions: File extensions this extractor handles
                (with or without leading dot — both normalised).
            grammar_alias: If your language reuses an existing
                tree-sitter grammar (e.g. ``"nextjs-mdx"`` reuses
                ``"markdown"``), name that grammar here.
        """
        lang = (language or "").strip().lower()
        if not lang:
            raise ValueError("language must be non-empty")
        if not callable(extractor_factory):
            raise TypeError("extractor_factory must be callable")

        state = _registry_state()
        state.language_extractors[lang] = extractor_factory

        if extensions:
            for ext in extensions:
                norm = _normalize_extension(ext)
                if norm:
                    state.language_extensions[norm] = lang

        if grammar_alias:
            grammar = (grammar_alias or "").strip()
            if grammar:
                state.language_grammar_aliases[lang] = grammar
        self._note_capability("extractor")

    # ---- framework detector registration --------------------------------

    def register_framework_detector(
        self,
        detect_fn: Callable[[Path], "str | None"],
    ) -> None:
        """Register a framework detector callable.

        ``detect_fn(project_root: Path) -> Optional[str]`` returns a
        framework slug (e.g. ``"nextjs"``) when the project at
        ``project_root`` matches your plugin's framework, otherwise
        ``None``. Detectors must be cheap (<10 ms) — they run on every
        ``roam framework-detect`` invocation.
        """
        if not callable(detect_fn):
            raise TypeError("detect_fn must be callable")
        state = _registry_state()
        state.framework_detectors.append(detect_fn)
        self._note_capability("framework_detection")

    # ---- bridge registration --------------------------------------------

    def register_bridge(self, bridge: "LanguageBridge") -> None:
        """Register a cross-language bridge.

        A bridge resolves references that span language boundaries
        (Protobuf → Go stubs, Apex → Aura/LWC, Next.js API route →
        client fetch). The instance must implement
        ``roam.bridges.base.LanguageBridge``.
        """
        state = _registry_state()
        # Duck-type rather than isinstance() — the abstract base lives
        # in ``roam.bridges.base`` and importing it eagerly here would
        # pull the bridge module on every plugin load.
        required = ("name", "detect", "resolve")
        for attr in required:
            if not hasattr(bridge, attr):
                raise TypeError(f"bridge missing required attribute: {attr}")
        state.bridges.append(bridge)
        self._note_capability("bridge")

    # ---- internal -------------------------------------------------------

    def _note_capability(self, capability: str) -> None:
        """Record a capability against the most-recently-declared plugin.

        If the plugin never called ``declare()`` we synthesise a
        :class:`RoamPlugin` from the discovery scratchpad so plugins
        that only register hooks still appear in
        ``roam plugins list``.
        """
        state = _registry_state()
        if not state.plugins or (state.current_source and state.plugins[-1].source != state.current_source):
            # Either no declared plugin yet, or the current loading
            # source has not declared one — synthesise from metadata.
            meta = state.current_plugin_meta
            if meta:
                name, version, description = meta
            else:
                # Module-channel plugin with no entry-point metadata
                # and no declare(): name after the loading source.
                src = state.current_source or "anonymous"
                name = src.split(":", 1)[-1] if ":" in src else src
                version = "unknown"
                description = ""
            state.plugins.append(
                RoamPlugin(
                    name=name,
                    version=version,
                    description=description,
                    source=state.current_source or "",
                )
            )
        plugin = state.plugins[-1]
        if capability not in plugin.capabilities:
            plugin.capabilities.append(capability)
