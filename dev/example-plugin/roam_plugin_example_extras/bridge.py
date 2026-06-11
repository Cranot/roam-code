"""Synthetic bridge demonstrating ``register_bridge``.

A :class:`LanguageBridge` resolves symbol references that cross
language boundaries. Real bridges in core:

- ``bridge_protobuf`` — ``.proto`` -> generated Go/Java/Python stubs.
- ``bridge_salesforce`` — Apex -> Aura/LWC/Visualforce.
- ``bridge_rest_api`` — frontend HTTP fetch -> backend route.
- ``bridge_config`` — env var read -> ``.env`` / ``.yml`` definition.

This bridge is intentionally synthetic: it pretends ``.example`` files
declare symbols that ``.example_target`` files consume, and emits one
edge per (source_symbol, target_file) pair. Replace the body with real
resolution logic in a production plugin.
"""

from __future__ import annotations

import os

from roam.bridges.base import LanguageBridge


class ExampleBridge(LanguageBridge):
    """Maps ``.example`` source files to ``.example_target`` consumers."""

    VERSION = "0.1.0"  # bump when resolution logic changes (Audit A6)

    _SOURCE_EXTS = frozenset({".example"})
    _TARGET_EXTS = frozenset({".example_target"})

    @property
    def name(self) -> str:
        return "example-bridge"

    @property
    def source_extensions(self) -> frozenset[str]:
        return self._SOURCE_EXTS

    @property
    def target_extensions(self) -> frozenset[str]:
        return self._TARGET_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Cheap pre-check — return True iff the project carries a source file."""
        return any(os.path.splitext(fp)[1].lower() in self._SOURCE_EXTS for fp in file_paths)

    def resolve(
        self,
        source_path: str,
        source_symbols: list[dict],
        target_files: dict[str, list[dict]],
    ) -> list[dict]:
        """Emit one cross-language edge per (source_symbol, target_file) pair.

        Real bridges parse the source file's references and match them
        against target-file symbols by qualified name, import path, or
        framework-specific naming conventions. This stub emits
        deterministic synthetic edges so consumers see the shape.
        """
        edges: list[dict] = []
        for sym in source_symbols:
            sym_name = sym.get("qualified_name") or sym.get("name") or "unknown"
            for target_path in target_files:
                if os.path.splitext(target_path)[1].lower() not in self._TARGET_EXTS:
                    continue
                edges.append(
                    {
                        "source": sym_name,
                        "target": f"{target_path}:imported",
                        "kind": "x-lang",
                        "bridge": self.name,
                    }
                )
        return edges
