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

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this bridge (e.g. 'protobuf', 'salesforce')."""

    @property
    @abstractmethod
    def source_extensions(self) -> frozenset[str]:
        """File extensions this bridge reads from (e.g. frozenset({'.proto'}))."""

    @property
    @abstractmethod
    def target_extensions(self) -> frozenset[str]:
        """File extensions this bridge generates/links to."""

    @abstractmethod
    def detect(self, file_paths: list[str]) -> bool:
        """Return True if this bridge is relevant for the given file set."""

    @abstractmethod
    def resolve(self, source_path: str, source_symbols: list[dict],
                target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve cross-language symbol links.

        Args:
            source_path: Path of the source file (e.g. foo.proto)
            source_symbols: Symbols extracted from source_path
            target_files: {path: [symbols]} for candidate target files

        Returns:
            List of edge dicts: [{"source": qualified_name, "target": qualified_name,
                                   "kind": "x-lang", "bridge": self.name}]
        """
