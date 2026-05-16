"""Synthetic language extractor demonstrating ``register_language_extractor``.

A :class:`LanguageExtractor` subclass tells roam how to pull symbols
and references out of a parsed tree-sitter AST for a given file
extension. Real extractors in core live under ``src/roam/languages/``
(``python_lang.py``, ``go_lang.py``, ``apex_lang.py``, …) and walk
their language's AST node types.

This stub is the shortest faithful extractor: it ignores the AST and
emits one synthetic symbol per file. Real plugins replace
``extract_symbols`` / ``extract_references`` with tree-walking logic
that calls ``self._make_symbol(...)`` and ``self._make_reference(...)``
for each interesting AST node.
"""

from __future__ import annotations

import os

from roam.languages.base import LanguageExtractor


class ExampleExtractor(LanguageExtractor):
    """Trivial extractor for the synthetic ``.example`` extension."""

    VERSION = "0.1.0"  # bump when extraction shape changes (Audit A6)

    @property
    def language_name(self) -> str:
        return "example-lang"

    @property
    def file_extensions(self) -> list[str]:
        return [".example"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Emit one synthetic symbol per file.

        Real extractors walk ``tree.root_node`` and emit one dict per
        function / class / method / variable / etc. The
        ``_make_symbol`` helper on the base class returns the canonical
        13-field shape (``name``, ``qualified_name``, ``kind``,
        ``signature``, ``line_start``, ``line_end``, ``docstring``,
        ``visibility``, ``is_exported``, ``parent_name``,
        ``default_value``, ``is_async``, ``decorators``).
        """
        stem = os.path.splitext(os.path.basename(file_path))[0] or "anonymous"
        line_count = max(1, len(source.splitlines()))
        return [
            self._make_symbol(
                name=stem,
                kind="function",
                line_start=1,
                line_end=line_count,
                qualified_name=f"{stem}.entry",
                signature=f"def {stem}()",
                visibility="public",
                is_exported=True,
            )
        ]

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Emit zero references — real extractors walk call/import nodes.

        Returning an empty list is legitimate: it means "this language
        has no cross-symbol edges to surface". Production extractors
        emit one dict per call/import via ``self._make_reference(...)``.
        """
        return []
