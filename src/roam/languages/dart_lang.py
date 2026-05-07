"""Dart language extractor (Tier 1, for Flutter teams).

Tree-sitter Dart node shapes (verified 2026-05-07 against
tree-sitter-language-pack 1.6.2):

- class_definition  → name = first identifier child
- mixin_declaration → name = identifier child
- extension_declaration → name = first identifier child (the extension name)
- enum_declaration  → name = identifier child
- type_alias        → name = first type_identifier child
- function_signature (top-level) → name = identifier child
- method_signature  → contains function_signature OR getter_signature OR
                      setter_signature; name lives one level deeper
- constructor_signature → wraps in declaration; first identifier is the
                          class name (constructor)
- declaration       → field; one or more identifiers in
                      initialized_identifier_list
"""

from __future__ import annotations

from .generic_lang import GenericExtractor


class DartExtractor(GenericExtractor):
    """Dart dedicated extractor (Tier 1)."""

    def __init__(self):
        super().__init__(language="dart")

    @property
    def language_name(self) -> str:
        return "dart"

    @property
    def file_extensions(self) -> list[str]:
        return [".dart"]

    def _classify_node(self, node) -> str | None:
        """Map Dart node types to Roam's symbol kinds."""
        if node.type == "class_definition":
            token_types = {c.type for c in node.children if not c.is_named}
            if "abstract" in token_types:
                return "class"
            return "class"
        if node.type == "mixin_declaration":
            return "interface"
        if node.type == "extension_declaration":
            return "class"
        if node.type == "enum_declaration":
            return "enum"
        if node.type == "type_alias":
            return "typealias"
        if node.type == "method_signature":
            inner = self._first_method_inner(node)
            if inner is not None:
                if inner.type == "getter_signature":
                    return "getter"
                if inner.type == "setter_signature":
                    return "setter"
                if inner.type == "constructor_signature":
                    return "constructor"
            return "method"
        if node.type == "function_signature":
            return "method" if self._in_type_context(node) else "function"
        if node.type == "constructor_signature":
            return "constructor"
        return super()._classify_node(node)

    def _in_type_context(self, node) -> bool:
        """Return True if node sits inside a class/mixin/extension body."""
        cur = node.parent
        while cur is not None:
            if cur.type in ("class_body", "extension_body", "enum_body"):
                return True
            cur = cur.parent
        return False

    def _first_method_inner(self, method_node):
        """method_signature wraps function_signature/getter/setter."""
        for child in method_node.children:
            if child.type in (
                "function_signature",
                "getter_signature",
                "setter_signature",
                "constructor_signature",
            ):
                return child
        return None

    def _get_name(self, node, source) -> str | None:
        """Extract the symbol name for Dart node shapes."""
        if node.type == "method_signature":
            inner = self._first_method_inner(node)
            if inner is not None:
                # getter_signature / setter_signature / function_signature /
                # constructor_signature all carry an identifier child.
                for child in inner.children:
                    if child.type == "identifier":
                        return self.node_text(child, source).strip()
        if node.type == "constructor_signature":
            for child in node.children:
                if child.type == "identifier":
                    return self.node_text(child, source).strip()
        if node.type in (
            "class_definition",
            "mixin_declaration",
            "extension_declaration",
            "enum_declaration",
            "function_signature",
        ):
            for child in node.children:
                if child.type == "identifier":
                    return self.node_text(child, source).strip()
        if node.type == "type_alias":
            for child in node.children:
                if child.type == "type_identifier":
                    return self.node_text(child, source).strip()
        return super()._get_name(node, source)
