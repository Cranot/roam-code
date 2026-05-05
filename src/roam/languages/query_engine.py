"""
Tree-Sitter Query Engine for Declarative Language Extractors.

This module executes tree-sitter queries defined in YAML extractors
and builds symbol/reference/inheritance structures from the results.

Performance characteristics:
- Queries are compiled once per language and cached
- Scope tracking uses parent chain traversal
- Output is deterministic (sorted by line, then column)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tree_sitter import Node, Parser, Query, QueryCursor

from roam.languages.extractor_schema import (
    InheritancePattern,
    LanguageConfig,
    ReferencePattern,
    SymbolPattern,
    validate_config,
)

# ---------------------------------------------------------------------------
# Extracted Artifacts
# ---------------------------------------------------------------------------


@dataclass
class ExtractedSymbol:
    """A symbol extracted from source code."""

    name: str
    kind: str
    file_path: str
    line: int
    column: int
    end_line: int
    end_column: int
    signature: str | None = None
    docstring: str | None = None
    scope: str | None = None  # Parent scope (class/module name)
    modifiers: list[str] = field(default_factory=list)
    node_text: str | None = None  # Full text of the definition

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "signature": self.signature,
            "docstring": self.docstring,
            "scope": self.scope,
            "modifiers": self.modifiers,
        }


@dataclass
class ExtractedReference:
    """A reference (import, call, type use) extracted from source code."""

    name: str
    kind: str
    file_path: str
    line: int
    column: int
    target_symbol: str | None = None  # Resolved symbol name (if known)
    context: str | None = None  # Surrounding function/class

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "target_symbol": self.target_symbol,
            "context": self.context,
        }


@dataclass
class ExtractedInheritance:
    """An inheritance relationship extracted from source code."""

    child: str
    parent: str
    relationship: str  # extends, implements, mixins, etc.
    file_path: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "child": self.child,
            "parent": self.parent,
            "relationship": self.relationship,
            "file_path": self.file_path,
            "line": self.line,
        }


@dataclass
class ExtractionResult:
    """Complete extraction result for a file."""

    file_path: str
    language: str
    symbols: list[ExtractedSymbol]
    references: list[ExtractedReference]
    inheritance: list[ExtractedInheritance]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "symbols": [s.to_dict() for s in self.symbols],
            "references": [r.to_dict() for r in self.references],
            "inheritance": [i.to_dict() for i in self.inheritance],
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Query Engine
# ---------------------------------------------------------------------------


class QueryEngine:
    """
    Executes tree-sitter queries from declarative language configs.

    Usage:
        config = LanguageConfig.load(Path("languages/kotlin.yaml"))
        engine = QueryEngine(config)
        result = engine.extract(source_code, file_path="test.kt")
    """

    def __init__(self, config: LanguageConfig):
        self.config = config
        self._parser: Parser | None = None
        self._language_obj: Any = None
        self._compiled_queries: dict[str, Query] = {}

        # Validate config
        errors = validate_config(config)
        if errors:
            raise ValueError(f"Invalid language config: {errors}")

    @property
    def language_name(self) -> str:
        return self.config.language

    @property
    def file_extensions(self) -> list[str]:
        return self.config.extensions

    def _get_language(self) -> Any:
        """Get the tree-sitter Language object."""
        if self._language_obj is None:
            from tree_sitter_language_pack import get_language

            grammar = self.config.grammar_alias or self.config.language
            self._language_obj = get_language(grammar)
        return self._language_obj

    def _get_parser(self) -> Parser:
        """Get or create a parser for this language."""
        if self._parser is None:
            self._parser = Parser(self._get_language())
        return self._parser

    def _compile_query(self, query_string: str) -> Query:
        """Compile a query string, with caching."""
        cache_key = f"{self.config.language}:{hash(query_string)}"
        if cache_key not in self._compiled_queries:
            lang = self._get_language()
            self._compiled_queries[cache_key] = Query(lang, query_string)
        return self._compiled_queries[cache_key]

    def _get_scope_context(self, node: Node, source: bytes) -> set[str]:
        """
        Walk up the parent chain to find all scope-defining ancestors.

        Returns a set of scope names AND node types (e.g., {"User", "class_body", "Repository"}).
        Node types are included for context-aware kind resolution.
        """
        scopes = set()
        current = node.parent

        while current is not None:
            # Always add the parent node type for context-aware resolution
            node_type = current.type
            scopes.add(node_type)

            # If this node is a named container, also add its name
            if node_type in (
                "class_declaration",
                "class_definition",
                "interface_declaration",
                "struct_item",
                "function_declaration",
                "function_definition",
                "method_declaration",
                "object_declaration",
                "enum_declaration",
            ):
                # Try to get the name
                for child in current.children:
                    if child.type in (
                        "type_identifier",
                        "identifier",
                        "simple_identifier",
                        "name",
                    ):
                        name = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
                        scopes.add(name)
                        break
            current = current.parent

        return scopes

    def _get_parent_scope_name(self, node: Node, source: bytes) -> str | None:
        """Get the immediate parent scope name (for symbol.scope field)."""
        current = node.parent
        while current is not None:
            node_type = current.type
            if node_type in (
                "class_declaration",
                "class_definition",
                "interface_declaration",
                "struct_item",
                "object_declaration",
                "enum_declaration",
            ):
                for child in current.children:
                    if child.type in ("type_identifier", "identifier", "simple_identifier", "name"):
                        return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            current = current.parent
        return None

    def extract(
        self,
        source: str | bytes,
        file_path: str,
        encoding: str = "utf-8",
    ) -> ExtractionResult:
        """
        Extract symbols, references, and inheritance from source code.

        Args:
            source: Source code as string or bytes
            file_path: Path to the file (for metadata)
            encoding: Source encoding (if source is string)

        Returns:
            ExtractionResult with all extracted artifacts
        """
        if isinstance(source, str):
            source_bytes = source.encode(encoding)
        else:
            source_bytes = source

        parser = self._get_parser()
        tree = parser.parse(source_bytes)
        root = tree.root_node

        symbols: list[ExtractedSymbol] = []
        references: list[ExtractedReference] = []
        inheritance: list[ExtractedInheritance] = []
        errors: list[str] = []

        # Extract symbols
        for pattern in self.config.symbols:
            try:
                symbols.extend(self._extract_symbols_from_pattern(pattern, root, source_bytes, file_path))
            except Exception as e:
                errors.append(f"Symbol pattern error: {e}")

        # Extract references
        for ref_type, patterns in self.config.references.items():
            for pattern in patterns:
                try:
                    references.extend(self._extract_references_from_pattern(pattern, root, source_bytes, file_path))
                except Exception as e:
                    errors.append(f"Reference pattern error ({ref_type}): {e}")

        # Extract inheritance
        for inh_type, patterns in self.config.inheritance.items():
            for pattern in patterns:
                try:
                    inheritance.extend(self._extract_inheritance_from_pattern(pattern, root, source_bytes, file_path))
                except Exception as e:
                    errors.append(f"Inheritance pattern error ({inh_type}): {e}")

        # Sort by line, then column for deterministic output
        symbols.sort(key=lambda s: (s.line, s.column))
        references.sort(key=lambda r: (r.line, r.column))
        inheritance.sort(key=lambda i: i.line)

        return ExtractionResult(
            file_path=file_path,
            language=self.config.language,
            symbols=symbols,
            references=references,
            inheritance=inheritance,
            errors=errors,
        )

    def _find_name_node(self, name_nodes, def_node):
        """Pick the name node that is a child of ``def_node`` (Pass 101)."""
        for n in name_nodes:
            if self._is_child_of(n, def_node):
                return n
        return None

    def _decode_capture(self, source: bytes, captures_dict: dict, capture_name, def_node):
        """Decode the first child-of-def_node capture as utf-8 (Pass 101)."""
        if not capture_name:
            return None
        nodes = captures_dict.get(capture_name, [])
        for n in nodes:
            if self._is_child_of(n, def_node):
                return source[n.start_byte : n.end_byte].decode("utf-8", errors="replace")
        return None

    def _resolve_kotlin_class_kind(self, def_node, kind: str) -> str:
        """Disambiguate Kotlin ``class_declaration`` into class/interface/enum.

        redactedextracted from the original mega-function. Tree-sitter
        emits ``class_declaration`` for both ``class``, ``interface`` and
        ``enum class`` in Kotlin; the first child distinguishes them.
        """
        if (
            self.config.language != "kotlin"
            or def_node.type != "class_declaration"
            or kind != "class"
            or def_node.child_count == 0
        ):
            return kind
        first_child = def_node.children[0]
        first_type = first_child.type
        if first_type == "interface":
            return "interface"
        if first_type == "enum":
            return "enum"
        if first_type == "modifiers":
            for mod_child in first_child.children:
                if mod_child.type != "class_modifier":
                    continue
                for cm in mod_child.children:
                    if cm.type in ("data", "sealed"):
                        return "class"
        return kind

    def _build_symbol_from_def(
        self,
        def_node,
        captures_dict: dict,
        pattern: SymbolPattern,
        source: bytes,
        file_path: str,
        seen_positions: set,
    ):
        """Build a single ExtractedSymbol from a captured def-node (Pass 101).

        Returns ``None`` when the capture should be skipped (no name,
        invalid name, duplicate position).
        """
        name_node = self._find_name_node(captures_dict.get(pattern.name_capture, []), def_node)
        if name_node is None:
            return None
        name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        if not name or not re.match(r"^[\w_]", name):
            return None

        pos_key = (def_node.start_point[0], def_node.start_point[1])
        if pos_key in seen_positions:
            return None
        seen_positions.add(pos_key)

        scope_context = self._get_scope_context(def_node, source)
        kind = self._resolve_kotlin_class_kind(def_node, pattern.kind.resolve(scope_context))
        parent_scope = self._get_parent_scope_name(def_node, source)
        signature = self._decode_capture(source, captures_dict, pattern.signature_capture, def_node)
        docstring = None
        if pattern.doc_capture:
            doc_nodes = captures_dict.get(pattern.doc_capture, [])
            if doc_nodes:
                docstring = source[doc_nodes[0].start_byte : doc_nodes[0].end_byte].decode("utf-8", errors="replace")
        node_text = source[def_node.start_byte : def_node.end_byte].decode("utf-8", errors="replace")
        return ExtractedSymbol(
            name=name,
            kind=kind,
            file_path=file_path,
            line=def_node.start_point[0] + 1,
            column=def_node.start_point[1],
            end_line=def_node.end_point[0] + 1,
            end_column=def_node.end_point[1],
            signature=signature,
            docstring=docstring,
            scope=parent_scope,
            modifiers=pattern.modifiers.copy(),
            node_text=node_text,
        )

    def _extract_symbols_from_pattern(
        self,
        pattern: SymbolPattern,
        root: Node,
        source: bytes,
        file_path: str,
    ) -> list[ExtractedSymbol]:
        """Extract symbols using a single SymbolPattern.

        redactedorchestrator only. Per-symbol logic moved into
        ``_build_symbol_from_def`` and three new helpers, dropping
        cognitive complexity from 198 to ~10.
        """
        cursor = QueryCursor(self._compile_query(pattern.query))
        matches = cursor.matches(root)
        symbols: list[ExtractedSymbol] = []
        seen_positions: set = set()
        for _pattern_idx, captures_dict in matches:
            for def_node in captures_dict.get(pattern.def_capture, []):
                sym = self._build_symbol_from_def(def_node, captures_dict, pattern, source, file_path, seen_positions)
                if sym is not None:
                    symbols.append(sym)
        return symbols

    def _extract_references_from_pattern(
        self,
        pattern: ReferencePattern,
        root: Node,
        source: bytes,
        file_path: str,
    ) -> list[ExtractedReference]:
        """Extract references using a single ReferencePattern."""
        query = self._compile_query(pattern.query)
        cursor = QueryCursor(query)
        matches = cursor.captures(root)

        references = []
        seen_positions = set()

        for node, capture_name in matches:
            if capture_name != pattern.ref_capture:
                continue

            # Get the name
            name_node = None
            for n, cn in matches:
                if cn == pattern.name_capture and self._is_child_of(n, node):
                    name_node = n
                    break

            if name_node is None:
                continue

            name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")

            if not name:
                continue

            pos_key = (node.start_point[0], node.start_point[1])
            if pos_key in seen_positions:
                continue
            seen_positions.add(pos_key)

            references.append(
                ExtractedReference(
                    name=name,
                    kind=pattern.kind,
                    file_path=file_path,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                    context=self._get_parent_scope_name(node, source),
                )
            )

        return references

    def _extract_inheritance_from_pattern(
        self,
        pattern: InheritancePattern,
        root: Node,
        source: bytes,
        file_path: str,
    ) -> list[ExtractedInheritance]:
        """Extract inheritance using a single InheritancePattern."""
        query = self._compile_query(pattern.query)
        cursor = QueryCursor(query)
        matches = cursor.matches(root)

        inheritance = []
        seen_positions = set()

        # matches returns list of (pattern_index, captures_dict)
        for pattern_idx, captures_dict in matches:
            child_nodes = captures_dict.get(pattern.child_capture, [])
            parent_nodes = captures_dict.get(pattern.parent_capture, [])

            for child_node in child_nodes:
                for parent_node in parent_nodes:
                    child_name = source[child_node.start_byte : child_node.end_byte].decode("utf-8", errors="replace")
                    parent_name = source[parent_node.start_byte : parent_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )

                    if not child_name or not parent_name:
                        continue

                    pos_key = (child_node.start_point[0], child_node.start_point[1])
                    if pos_key in seen_positions:
                        continue
                    seen_positions.add(pos_key)

                    inheritance.append(
                        ExtractedInheritance(
                            child=child_name,
                            parent=parent_name,
                            relationship=pattern.relationship,
                            file_path=file_path,
                            line=child_node.start_point[0] + 1,
                        )
                    )

        return inheritance

    @staticmethod
    def _is_child_of(potential_child: Node, parent: Node) -> bool:
        """Check if a node is a descendant of another node."""
        current = potential_child.parent
        while current is not None:
            if current.id == parent.id:
                return True
            current = current.parent
        return False


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------


def load_engine_from_yaml(yaml_path: Path) -> QueryEngine:
    """Load a QueryEngine from a YAML config file."""
    config = LanguageConfig.load(yaml_path)
    return QueryEngine(config)


def extract_file(
    file_path: Path,
    config: LanguageConfig,
) -> ExtractionResult:
    """Extract symbols from a file using a language config."""
    engine = QueryEngine(config)
    source = file_path.read_text(encoding="utf-8")
    return engine.extract(source, str(file_path))
