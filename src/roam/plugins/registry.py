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
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping

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


@dataclass(frozen=True)
class FrameworkProfile:
    """Bundles framework-specific knowledge for a plugin to register in one call.

    Wave28.3 (W123) — a richer alternative to
    :meth:`RoamPluginContext.register_framework_detector` that lets a
    plugin declare not just the detector but also the file patterns
    that characterise the framework, the roam commands that produce
    the highest signal on it, and the convention-name mapping that
    downstream surfaces (``roam brief``, ``roam describe``, the
    framework-aware MCP tools) can consult.

    The legacy single-detector API (``register_framework_detector``)
    keeps working unchanged — plugins that only want the detector hook
    do not need to migrate. ``register_framework_profile`` is the
    additive richer surface; it also calls
    ``register_framework_detector`` internally so a profile-registered
    framework is detected by the same downstream consumer
    (``autodetect_framework_profile`` in ``roam.catalog.detectors``).

    Args:
        name: Framework identifier (e.g. ``"nextjs"``, ``"laravel"``,
            ``"django"``). Used as the dictionary key in the profile
            registry and as the slug ``detect_fn`` should return.
        detect_fn: ``Callable[[pathlib.Path], Optional[str]]`` returning
            the framework name when the directory matches this framework,
            otherwise ``None``. Same contract as
            ``register_framework_detector`` — must be cheap (<10 ms) and
            type-hinted with ``pathlib.Path`` per W56.
        file_patterns: Glob patterns characterising this framework
            (e.g. ``("pages/**", "app/**", "next.config.*")`` for
            Next.js). Informational — surfaced by ``roam describe`` /
            ``roam brief`` so agents can prioritise reads.
        recommended_commands: Roam commands that produce the highest
            signal on this framework (e.g. ``("n1", "vulns",
            "over-fetch")`` for Laravel). Informational — consumed by
            ``roam brief``-style command surfaces.
        conventions: Mapping of role -> file pattern (e.g.
            ``{"controller": "app/Http/Controllers/*"}``). Informational
            — feeds conventions-aware surfaces.
    """

    name: str
    detect_fn: Callable[[Path], "str | None"]
    file_patterns: tuple[str, ...] = ()
    recommended_commands: tuple[str, ...] = ()
    conventions: Mapping[str, str] = field(default_factory=dict)


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
    framework_profiles: dict[str, FrameworkProfile] = field(default_factory=dict)
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


def _note_plugin_capability(capability: str) -> None:
    """Record a capability against the most-recently-declared plugin.

    If the plugin never called ``declare()`` we synthesise a
    :class:`RoamPlugin` from the discovery scratchpad so plugins
    that only register hooks still appear in ``roam plugins list``.
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


class _RoamPluginCoreRegistration:
    """Core plugin registration methods shared by RoamPluginContext."""

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
        _note_plugin_capability("command")

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
        _note_plugin_capability("detector")

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
        _note_plugin_capability("extractor")


class _RoamPluginExtensionRegistration:
    """Framework, bridge, and capability methods shared by RoamPluginContext."""

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

        Contract (W56): the argument is always a ``pathlib.Path``, never
        a ``str``. Roam's internal dispatcher coerces ``cwd`` to
        ``Path`` before calling, and plugin authors who invoke their
        own detector in unit tests should do the same. Type-hint
        ``project_root: Path`` so IDEs and ``mypy`` warn callers who
        pass a ``str`` (which crashes at ``project_root / "Gemfile"``).
        """
        if not callable(detect_fn):
            raise TypeError("detect_fn must be callable")
        state = _registry_state()
        state.framework_detectors.append(detect_fn)
        _note_plugin_capability("framework_detection")

    # ---- framework profile registration ---------------------------------

    def register_framework_profile(self, profile: FrameworkProfile) -> None:
        """Register a richer framework profile (W123 / Wave28.3).

        Bundles the framework detector together with the
        framework-specific knowledge a plugin wants to declare:
        characteristic file patterns, the roam commands that produce
        the highest signal on this framework, and the conventions
        mapping.

        Internally calls :meth:`register_framework_detector` with
        ``profile.detect_fn`` so the profile-registered detector flows
        through the same downstream consumer
        (``autodetect_framework_profile`` in
        ``roam.catalog.detectors``). Plugin authors get a single call
        that wires both surfaces; legacy plugins using
        ``register_framework_detector`` directly are unaffected.

        Args:
            profile: A :class:`FrameworkProfile` instance. The dataclass
                is frozen so mis-mutating callers fail loudly.

        Raises:
            TypeError: ``profile`` is not a :class:`FrameworkProfile`.
            ValueError: ``profile.name`` is empty or already registered
                by another plugin (first-write-wins, matching command
                registration semantics).
        """
        if not isinstance(profile, FrameworkProfile):
            raise TypeError(f"profile must be a FrameworkProfile (got {type(profile).__name__})")
        name = (profile.name or "").strip()
        if not name:
            raise ValueError("FrameworkProfile.name must be non-empty")

        if get_framework_profile(name) is not None:
            raise ValueError(f"duplicate framework profile: {name}")
        state = _registry_state()
        state.framework_profiles[name] = profile

        # Also wire the detector so legacy consumers
        # (autodetect_framework_profile) see the profile-registered
        # framework. Capability recording is called inside
        # register_framework_detector — we additionally tag
        # "framework_profile" to make the richer registration visible
        # in ``roam plugins info``.
        self.register_framework_detector(profile.detect_fn)
        _note_plugin_capability("framework_profile")

    # ---- bridge registration --------------------------------------------

    def register_bridge(self, bridge: "LanguageBridge") -> None:
        """Register a cross-language bridge.

        A bridge resolves references that span language boundaries
        (Protobuf → Go stubs, Apex → Aura/LWC, Next.js API route →
        client fetch). The instance must implement
        ``roam.bridges.base.LanguageBridge``.
        """
        from roam.bridges.registry import register_bridge as register_language_bridge

        register_language_bridge(bridge)
        state = _registry_state()
        state.bridges.append(bridge)
        _note_plugin_capability("bridge")


class RoamPluginContext(_RoamPluginCoreRegistration, _RoamPluginExtensionRegistration):
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


_PLUGIN_CONTEXT_PUBLIC_METHODS: Mapping[str, Callable[..., None]] = MappingProxyType(
    {
        "declare": RoamPluginContext.declare,
        "register_command": RoamPluginContext.register_command,
        "register_detector": RoamPluginContext.register_detector,
        "register_language_extractor": RoamPluginContext.register_language_extractor,
        "register_framework_detector": RoamPluginContext.register_framework_detector,
        "register_framework_profile": RoamPluginContext.register_framework_profile,
        "register_bridge": RoamPluginContext.register_bridge,
    }
)


def _assert_plugin_context_contract() -> None:
    """Validate the public plugin-author API surface without mutating state."""
    for method_name, method in _PLUGIN_CONTEXT_PUBLIC_METHODS.items():
        if not callable(method):
            raise AssertionError(f"RoamPluginContext.{method_name} must remain callable")


_assert_plugin_context_contract()


# ---------------------------------------------------------------------------
# Module-level query helpers (framework profile registry — W123)
# ---------------------------------------------------------------------------


def get_framework_profile(name: str) -> "FrameworkProfile | None":
    """Return the registered :class:`FrameworkProfile` for ``name``, else ``None``.

    Consumers that need the richer profile (file_patterns,
    recommended_commands, conventions) look it up by framework slug.
    Falls back to ``None`` when no plugin has registered a profile for
    that framework — the legacy
    :meth:`RoamPluginContext.register_framework_detector` path leaves
    no profile behind, so a detector-only framework returns ``None``
    here.
    """
    if not name:
        return None
    profiles = get_framework_profiles()
    return profiles.get(name)


def get_framework_profiles() -> Mapping[str, FrameworkProfile]:
    """Return a read-only view of every registered framework profile."""
    state = _registry_state()
    return MappingProxyType(state.framework_profiles)
