"""Abstract base class for cross-language bridges."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LanguageBridge(ABC):
    """Base class for cross-language symbol resolution bridges.

    A bridge resolves symbols that cross language boundaries:
    - Protobuf .proto -> generated Go/Java/Python stubs
    - Salesforce Apex -> Aura/LWC/Visualforce
    - GraphQL schema -> TypeScript/Python codegen
    - OpenAPI spec -> client SDKs
    """

    # Cohesion REVIEW (low-cohesion detector: "5 methods, 0 internal
    # edges", threshold 2): structural for an ABC interface contract,
    # not a fixable smell. 3 of the 5 members (``name`` /
    # ``source_extensions`` / ``target_extensions``) are ``@property``
    # accessors, and property reads (``self.name``) create NO call-graph
    # edge here -- verified: ``self.<property>`` accesses across every
    # concrete bridge yield 0 edges; only real ``self.method()`` calls
    # do (positive control: ``TestHighAIRatio``). The other 2
    # (``detect`` / ``resolve``) are independent abstract contract
    # methods that never call each other -- ``resolve`` runs per-file
    # AFTER ``detect`` has gated relevance in ``cmd_xlang``. Clearing
    # the metric would need an artificial multi-level call chain among
    # unrelated helpers -- metric-gaming forbidden by Pattern 2
    # (AGENTS.md). Expected by design, like the A6 dead-code disclosures
    # below.

    # Audit A6: every bridge stamps a version on its inference logic.
    # When the resolver changes (e.g. learning to follow ``through=`` on
    # Django M2M), the index built with the older bridge version may
    # carry stale edges marked ``bridge='django'`` — version mismatch
    # tells consumers to rebuild. Bump in subclasses when the
    # resolution algorithm changes; default ``1.0.0`` covers the
    # initial implementation of each bridge.
    VERSION: str = "1.0.0"

    @property
    @abstractmethod
    def name(self) -> str:
        """ABI-1 bridge identity contract (e.g. 'protobuf', 'salesforce')."""

    # Audit A6 / dead-code REVIEW: this base abstract definition looks
    # "unreferenced" to static export analysis because every call site
    # dispatches dynamically through a bridge instance
    # (``bridge.source_extensions`` in ``cmd_xlang.py:_resolve_bridge`` /
    # ``_bridge_files_count``), which resolves to the concrete subclass
    # override -- not to ``LanguageBridge.source_extensions`` itself. It is
    # the load-bearing interface contract (every bridge must declare its
    # source extensions), not dead code. Sibling abstract methods
    # (``name`` / ``target_extensions`` / ``detect`` / ``resolve``) share
    # the same dispatch profile.
    @property
    @abstractmethod
    def source_extensions(self) -> frozenset[str]:
        """File extensions this bridge reads from (e.g. frozenset({'.proto'})).

        See reference: bridge interface contract consumed through dynamic
        dispatch in ``cmd_xlang.py`` and implemented by every concrete bridge.
        """

    # Audit A6 / dead-code REVIEW: static export analysis sees no direct
    # call to this abstract property on ``LanguageBridge`` itself because
    # x-lang resolution consumes it through concrete bridge instances:
    # ``cmd_xlang.py:_resolve_bridge`` builds target-file candidates from
    # ``bridge.target_extensions`` and ``_bridge_files_count`` uses the
    # same contract for scope warnings. Keeping this abstract preserves
    # the invariant that every bridge declares the generated/linked file
    # extensions it can resolve to.
    @property
    @abstractmethod
    def target_extensions(self) -> frozenset[str]:
        """File extensions this bridge generates/links to.

        See reference: bridge interface contract consumed through dynamic
        dispatch by ``cmd_xlang.py`` and implemented by every concrete bridge.
        """

    @abstractmethod
    def detect(self, file_paths: list[str]) -> bool:
        """Return True if this bridge is relevant for the given file set."""

    @abstractmethod
    def resolve(self, source_path: str, source_symbols: list[dict], target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve cross-language symbol links.

        Args:
            source_path: Path of the source file (e.g. foo.proto)
            source_symbols: Symbols extracted from source_path
            target_files: {path: [symbols]} for candidate target files

        Returns:
            List of edge dicts: [{"source": qualified_name, "target": qualified_name,
                                   "kind": "x-lang", "bridge": self.name}]
        """
