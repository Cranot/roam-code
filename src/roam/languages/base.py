
from __future__ import annotations
from abc import ABC, abstractmethod


class LanguageExtractor(ABC):
    """Base class for language-specific symbol extraction."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        ...

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        ...

    @abstractmethod
    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Extract symbols from a parsed tree.

        Each dict must contain:
            name, qualified_name, kind, signature, line_start, line_end,
            docstring, visibility, is_exported, parent_name
        """
        ...

    @abstractmethod
    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Extract references (imports, calls) from a parsed tree.

        Each dict must contain:
            source_name, target_name, kind, line, import_path
        """
        ...

    def get_signature(self, node, source: bytes) -> str | None:
        """Get a function/class signature (first line, no body)."""
        text = self.node_text(node, source)
        first_line = text.split("\n")[0].rstrip()
        # Strip trailing colon/brace for cleaner signatures
        if first_line.endswith(("{", ":")):
            first_line = first_line[:-1].rstrip()
        return first_line if first_line else None

    def get_docstring(self, node, source: bytes) -> str | None:
        """Get docstring if present. Override per language."""
        return None

    def node_text(self, node, source: bytes) -> str:
        if node is None:
            return ""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _params_text(self, node, source: bytes) -> str:
        """Get parameter list text, stripping outer parens if present."""
        if node is None:
            return ""
        text = self.node_text(node, source)
        if text.startswith("(") and text.endswith(")"):
            return text[1:-1]
        return text

    def _make_symbol(
        self,
        name: str,
        kind: str,
        line_start: int,
        line_end: int,
        *,
        qualified_name: str | None = None,
        signature: str | None = None,
        docstring: str | None = None,
        visibility: str = "public",
        is_exported: bool = False,
        parent_name: str | None = None,
        default_value: str | None = None,
    ) -> dict:
        return {
            "name": name,
            "qualified_name": qualified_name or name,
            "kind": kind,
            "signature": signature,
            "line_start": line_start,
            "line_end": line_end,
            "docstring": docstring,
            "visibility": visibility,
            "is_exported": is_exported,
            "parent_name": parent_name,
            "default_value": default_value,
        }

    def _make_reference(
        self,
        target_name: str,
        kind: str,
        line: int,
        *,
        source_name: str | None = None,
        import_path: str | None = None,
    ) -> dict:
        return {
            "source_name": source_name,
            "target_name": target_name,
            "kind": kind,
            "line": line,
            "import_path": import_path,
        }

    def _walk_children(self, node, source: bytes, file_path: str, parent_name: str | None = None) -> list[dict]:
        """Helper to recursively walk tree and extract symbols. Override in subclass."""
        return []
