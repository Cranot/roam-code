"""
Declarative Language Extractor Schema.

This module defines the schema for YAML-based language extractors.
Each language is defined as a YAML file with tree-sitter queries.

Example (kotlin.yaml):
```yaml
language: kotlin
extensions: [.kt, .kts]
grammar_alias: kotlin  # tree-sitter grammar name (if different)

symbols:
  - query: |
      (class_declaration name: (type_identifier) @name) @def
    kind: class
    scope_query: class_body

  - query: |
      (function_declaration name: (simple_identifier) @name) @def
    kind:
      default: function
      context:
        class_body: method
        object_declaration: method

references:
  imports:
    - query: |
        (import_header (identifier) @name) @ref
    kind: import

inheritance:
  extends:
    - query: |
        (class_declaration
          name: (type_identifier) @child
          (delegation_specifier
            (type_identifier) @parent)) @inherit
    relationship: extends
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Optional dependency: PyYAML. Used only by ``LanguageConfig.load()`` to
# parse the per-language YAML extractor schemas. Hoisted to module scope
# so tests can monkeypatch ``yaml = None`` to exercise the missing-dep
# install-hint path without needing to uninstall the real package.
try:
    import yaml  # type: ignore
except ImportError as _yaml_import_exc:  # pragma: no cover - exercised via test monkeypatch
    yaml = None  # type: ignore[assignment]
    _YAML_IMPORT_ERROR: ImportError | None = _yaml_import_exc
else:
    _YAML_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Schema Data Classes
# ---------------------------------------------------------------------------


@dataclass
class KindMapping:
    """Maps query matches to symbol kinds, with context awareness."""

    default: str
    context: dict[str, str] = field(default_factory=dict)

    def resolve(self, context_scopes: set[str] | None = None) -> str:
        """Resolve the kind given the current scope context."""
        if not self.context or not context_scopes:
            return self.default
        for scope in context_scopes:
            if scope in self.context:
                return self.context[scope]
        return self.default

    @classmethod
    def from_yaml(cls, data: str | dict) -> "KindMapping":
        """Parse from YAML data (string or dict)."""
        if isinstance(data, str):
            return cls(default=data)
        return cls(
            default=data.get("default", "unknown"),
            context=data.get("context", {}),
        )


@dataclass
class SymbolPattern:
    """A tree-sitter query pattern for extracting symbols."""

    query: str
    kind: KindMapping
    scope_query: str | None = None
    name_capture: str = "name"
    def_capture: str = "def"
    doc_capture: str | None = None
    signature_capture: str | None = None
    modifiers: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, data: dict) -> "SymbolPattern":
        """Parse from YAML data."""
        kind_data = data.get("kind", "unknown")
        return cls(
            query=data["query"],
            kind=KindMapping.from_yaml(kind_data),
            scope_query=data.get("scope_query"),
            name_capture=data.get("name_capture", "name"),
            def_capture=data.get("def_capture", "def"),
            doc_capture=data.get("doc_capture"),
            signature_capture=data.get("signature_capture"),
            modifiers=data.get("modifiers", []),
        )


@dataclass
class ReferencePattern:
    """A tree-sitter query pattern for extracting references."""

    query: str
    kind: str = "reference"
    name_capture: str = "name"
    ref_capture: str = "ref"

    @classmethod
    def from_yaml(cls, data: dict | str) -> "ReferencePattern":
        """Parse from YAML data."""
        if isinstance(data, str):
            return cls(query=data)
        return cls(
            query=data["query"],
            kind=data.get("kind", "reference"),
            name_capture=data.get("name_capture", "name"),
            ref_capture=data.get("ref_capture", "ref"),
        )


@dataclass
class InheritancePattern:
    """A tree-sitter query pattern for extracting inheritance relationships."""

    query: str
    relationship: str = "extends"
    child_capture: str = "child"
    parent_capture: str = "parent"

    @classmethod
    def from_yaml(cls, data: dict) -> "InheritancePattern":
        """Parse from YAML data."""
        return cls(
            query=data["query"],
            relationship=data.get("relationship", "extends"),
            child_capture=data.get("child_capture", "child"),
            parent_capture=data.get("parent_capture", "parent"),
        )


@dataclass
class LanguageConfig:
    """Complete language extractor configuration."""

    language: str
    extensions: list[str]
    grammar_alias: str | None = None
    file_patterns: list[str] = field(default_factory=list)
    skip_patterns: list[str] = field(default_factory=list)

    symbols: list[SymbolPattern] = field(default_factory=list)
    references: dict[str, list[ReferencePattern]] = field(default_factory=dict)
    inheritance: dict[str, list[InheritancePattern]] = field(default_factory=dict)

    # Metadata
    version: str = "1.0"
    description: str = ""

    @classmethod
    def load(cls, path: Path) -> "LanguageConfig":
        """Load from a YAML file.

        Requires PyYAML. Raises ``ImportError`` with an install hint when
        PyYAML is missing (PyYAML is not a core dependency; it ships under
        the ``[dev]`` extra and is commonly present via transitive deps,
        but a bare ``pip install roam-code`` does not pull it in).
        """
        if yaml is None:
            raise ImportError(
                "LanguageConfig.load() requires PyYAML. "
                "Install with: pip install 'roam-code[dev]' (or: pip install pyyaml). "
                f"Original error: {_YAML_IMPORT_ERROR!r}"
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        symbols = [SymbolPattern.from_yaml(s) for s in data.get("symbols", [])]

        references = {}
        for ref_type, patterns in data.get("references", {}).items():
            references[ref_type] = [ReferencePattern.from_yaml(p) for p in patterns]

        inheritance = {}
        for inh_type, patterns in data.get("inheritance", {}).items():
            inheritance[inh_type] = [InheritancePattern.from_yaml(p) for p in patterns]

        return cls(
            language=data["language"],
            extensions=data.get("extensions", []),
            grammar_alias=data.get("grammar_alias"),
            file_patterns=data.get("file_patterns", []),
            skip_patterns=data.get("skip_patterns", []),
            symbols=symbols,
            references=references,
            inheritance=inheritance,
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------


def validate_config(config: LanguageConfig) -> list[str]:
    """Validate a language config. Returns list of errors (empty if valid)."""
    errors = []

    if not config.language:
        errors.append("language is required")

    if not config.extensions:
        errors.append("extensions is required (at least one file extension)")

    for i, sym in enumerate(config.symbols):
        if not sym.query:
            errors.append(f"symbols[{i}].query is required")
        if not sym.def_capture:
            errors.append(f"symbols[{i}].def_capture is required")

    for ref_type, patterns in config.references.items():
        for i, pat in enumerate(patterns):
            if not pat.query:
                errors.append(f"references.{ref_type}[{i}].query is required")

    for inh_type, patterns in config.inheritance.items():
        for i, pat in enumerate(patterns):
            if not pat.query:
                errors.append(f"inheritance.{inh_type}[{i}].query is required")

    return errors
