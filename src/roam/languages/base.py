from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping


class LanguageExtractor(ABC):
    """Base class for language-specific symbol extraction."""

    # Audit A6: stamp the extractor version. When extraction logic
    # changes (e.g. capturing decorator metadata that previous
    # extractors didn't), the index built with the older version
    # carries shape-incompatible symbol rows. Version mismatch tells
    # consumers ``--rebuild`` is needed. Bump in subclasses when the
    # extraction shape changes.
    VERSION: str = "1.0.0"

    # W1456: does this language fold identifier case? Most languages
    # resolve identifiers case-sensitively; Visual FoxPro does not
    # (``MyProc`` and ``myproc`` name the same routine). The relation
    # resolver consults this — via
    # ``registry.is_case_insensitive_language`` — before allowing its
    # case-folded fallback match. Running that fallback on a
    # case-sensitive language binds every name the repo does NOT define
    # (stdlib, third-party, builtins) to whatever indexed symbol happens
    # to case-fold equal, e.g. a ``Path`` import resolving to an
    # unrelated ``PATH`` constant. Declare ``True`` here rather than
    # teaching the resolver a new file extension.
    case_insensitive_identifiers: bool = False

    @property
    @abstractmethod
    def language_name(self) -> str: ...

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]: ...

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
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _params_text(self, node, source: bytes) -> str:
        """Get parameter list text, stripping outer parens if present."""
        if node is None:
            return ""
        text = self.node_text(node, source)
        if text.startswith("(") and text.endswith(")"):
            return text[1:-1]
        return text

    def _extract_class_skeleton(
        self,
        node,
        source: bytes,
        symbols: list[dict],
        *,
        name: str,
        kind: str,
        parent_name: str | None,
        signature: str,
        visibility: str,
        is_exported: bool,
        child_handlers: Mapping[str, Callable[[object, str], str | None]],
        trailing_signature: str = "",
        on_qualified: Callable[[str], None] | None = None,
        after_symbol: Callable[[str], None] | None = None,
        body_walker: Callable[[object, str], None] | None = None,
    ) -> None:
        """Shape a class symbol while language-owned handlers parse its syntax.

        The language modules declare which child node types matter and retain
        their inheritance/modifier semantics. This helper owns only the shared
        iteration, first-match filtering, symbol shaping, and body handoff.
        """
        qualified = f"{parent_name}.{name}" if parent_name else name
        if on_qualified is not None:
            on_qualified(qualified)

        handled_types: set[str] = set()
        for child in node.children:
            handler = child_handlers.get(child.type)
            if handler is None or child.type in handled_types:
                continue
            handled_types.add(child.type)
            suffix = handler(child, qualified)
            if suffix:
                signature += suffix
        signature += trailing_signature

        symbols.append(
            self._make_symbol(
                name=name,
                kind=kind,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                qualified_name=qualified,
                signature=signature,
                docstring=self.get_docstring(node, source),
                visibility=visibility,
                is_exported=is_exported,
                parent_name=parent_name,
            )
        )

        if after_symbol is not None:
            after_symbol(qualified)

        body = node.child_by_field_name("body")
        if body is not None and body_walker is not None:
            body_walker(body, qualified)

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
        is_async: bool = False,
        decorators: str = "",
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
            "is_async": bool(is_async),
            "decorators": decorators or "",
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

    def _append_regex_refs(
        self,
        text: str,
        refs: list[dict],
        pattern,
        kind: str,
        *,
        group: int = 1,
    ) -> None:
        """Append a reference for every regex match, computing line numbers from *text*."""
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            refs.append(
                self._make_reference(
                    target_name=match.group(group),
                    kind=kind,
                    line=line,
                )
            )

    def _append_regex_refs_split(
        self,
        text: str,
        refs: list[dict],
        pattern,
        kind: str,
        *,
        group: int = 1,
        sep: str = ",",
    ) -> None:
        """Append a reference for each comma-separated value captured by a regex match."""
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            for part in match.group(group).split(sep):
                part = part.strip()
                if part:
                    refs.append(
                        self._make_reference(
                            target_name=part,
                            kind=kind,
                            line=line,
                        )
                    )


class _SalesforceMarkupExtractor(LanguageExtractor):
    """Shared base for Salesforce markup extractors (Aura, Visualforce).

    Subclasses declare which regex rules apply by setting the class-level
    pattern attributes. This keeps the mechanical regex-to-reference loop in
    one place while each language keeps its own pattern/kind semantics.
    """

    _CONTROLLER_RE: re.Pattern
    _EXTENDS_RE: re.Pattern | None = None
    _IMPLEMENTS_RE: re.Pattern | None = None
    _EXTENSIONS_RE: re.Pattern | None = None
    _INCLUDE_RE: re.Pattern | None = None
    _CUSTOM_TAG_RE: re.Pattern | None = None
    _CUSTOM_TAG_STANDARD_NS: frozenset[str] = frozenset()
    _LABEL_REF_RE: re.Pattern | None = None
    _MERGE_FIELD_RE: re.Pattern | None = None
    _MERGE_FIELD_IDENT_RE: re.Pattern | None = None
    _MERGE_FIELD_BUILTINS: frozenset[str] = frozenset()

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        text = source.decode("utf-8", errors="replace")

        self._append_regex_refs(text, refs, self._CONTROLLER_RE, "controller")

        if self._EXTENDS_RE is not None:
            self._append_regex_refs(text, refs, self._EXTENDS_RE, "inherits")

        if self._IMPLEMENTS_RE is not None:
            self._append_regex_refs_split(text, refs, self._IMPLEMENTS_RE, "implements")

        if self._EXTENSIONS_RE is not None:
            self._append_regex_refs_split(text, refs, self._EXTENSIONS_RE, "controller")

        if self._INCLUDE_RE is not None:
            self._append_regex_refs(text, refs, self._INCLUDE_RE, "include", group=2)

        if self._CUSTOM_TAG_RE is not None:
            for match in self._CUSTOM_TAG_RE.finditer(text):
                ns, comp = match.group(1), match.group(2)
                if ns.lower() not in self._CUSTOM_TAG_STANDARD_NS:
                    line = text[: match.start()].count("\n") + 1
                    refs.append(
                        self._make_reference(
                            target_name=comp,
                            kind="component_ref",
                            line=line,
                        )
                    )

        if self._LABEL_REF_RE is not None:
            self._append_regex_refs(text, refs, self._LABEL_REF_RE, "label")

        if self._MERGE_FIELD_RE is not None and self._MERGE_FIELD_IDENT_RE is not None:
            seen = set()
            for match in self._MERGE_FIELD_RE.finditer(text):
                expr = match.group(1)
                line = text[: match.start()].count("\n") + 1
                for ident_match in self._MERGE_FIELD_IDENT_RE.finditer(expr):
                    name = ident_match.group(1)
                    if name not in self._MERGE_FIELD_BUILTINS and name not in seen:
                        seen.add(name)
                        refs.append(
                            self._make_reference(
                                target_name=name,
                                kind="merge_field",
                                line=line,
                            )
                        )

        return refs

    def _walk_children(self, node, source: bytes, file_path: str, parent_name: str | None = None) -> list[dict]:
        """Helper to recursively walk tree and extract symbols. Override in subclass."""
        return []
