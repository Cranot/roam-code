from __future__ import annotations

from .generic_lang import GenericExtractor


class SwiftExtractor(GenericExtractor):
    """Swift dedicated extractor (Tier 1)."""

    def __init__(self):
        super().__init__(language="swift")

    @property
    def language_name(self) -> str:
        return "swift"

    @property
    def file_extensions(self) -> list[str]:
        return [".swift"]

    def _classify_node(self, node) -> str | None:
        if node.type == "protocol_declaration":
            return "interface"
        if node.type == "class_declaration":
            token_types = {c.type for c in node.children if not c.is_named}
            if "struct" in token_types:
                return "struct"
            if "enum" in token_types or any(
                c.type == "enum_class_body" for c in node.children if c.is_named
            ):
                return "enum"
            return "class"
        if node.type == "init_declaration":
            return "constructor"
        if node.type == "function_declaration":
            return "method" if self._in_type_context(node) else "function"
        return super()._classify_node(node)

    def _in_type_context(self, node) -> bool:
        cur = node.parent
        while cur is not None:
            if cur.type in ("class_body", "protocol_body", "enum_class_body"):
                return True
            cur = cur.parent
        return False

    def _get_name(self, node, source) -> str | None:
        if node.type == "init_declaration":
            return "init"
        name = super()._get_name(node, source)
        if name:
            return name
        for child in node.children:
            if child.type in ("simple_identifier", "identifier", "type_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text
        return None

    def _extract_properties(self, body_node, source, symbols, class_name):
        """Extract Swift class/struct properties."""
        if body_node is None:
            return

        for child in body_node.children:
            if child.type != "property_declaration":
                continue
            name = self._swift_property_name(child, source)
            if not name:
                continue
            binding = self._find_child_text(child, source, "value_binding_pattern") or ""
            default_value = self._swift_default_value(child, source)
            visibility = self._swift_visibility(child, source)
            qualified = f"{class_name}.{name}" if class_name else name
            sig = f"{binding} {name}".strip() if binding else name

            symbols.append(
                self._make_symbol(
                    name=name,
                    kind="property",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    qualified_name=qualified,
                    signature=sig,
                    parent_name=class_name,
                    visibility=visibility,
                    is_exported=visibility in ("public", "open"),
                    default_value=default_value,
                )
            )

    def _swift_default_value(self, node, source: bytes) -> str | None:
        value = self._find_literal_value(node, source, max_depth=3)
        if value is not None:
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                return value[1:-1]
            return value

        for child in self._iter_all_descendants(node):
            if child.type == "line_str_text":
                text = self.node_text(child, source).strip()
                if text:
                    return text
            if child.type in ("line_string_literal", "multiline_string_literal"):
                text = self.node_text(child, source).strip()
                if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
                    return text[1:-1]
                if text:
                    return text
        return None

    def _swift_property_name(self, prop_node, source: bytes) -> str | None:
        pattern = self._find_child_text(prop_node, source, "pattern")
        if pattern:
            return pattern.strip()
        for child in self._iter_all_descendants(prop_node):
            if child.type in ("simple_identifier", "identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text
        return None

    def _swift_visibility(self, node, source: bytes) -> str:
        for child in node.children:
            if child.type == "modifiers":
                text = self.node_text(child, source)
                if "private" in text:
                    return "private"
                if "fileprivate" in text:
                    return "private"
                if "internal" in text:
                    return "internal"
                if "public" in text:
                    return "public"
                if "open" in text:
                    return "open"
        return "internal"

    def _extract_inheritance_refs(self, class_node, source, refs, class_name):
        """Swift inheritance_specifier: class first inherits, rest protocols."""
        kind = self._classify_node(class_node)
        if kind not in ("class", "struct", "interface"):
            return super()._extract_inheritance_refs(class_node, source, refs, class_name)

        specs = [c for c in class_node.children if c.type == "inheritance_specifier"]
        for i, spec in enumerate(specs):
            target = self._swift_inheritance_target(spec, source)
            if not target:
                continue

            if kind == "class":
                ref_kind = "inherits" if i == 0 else "implements"
            elif kind == "interface":
                ref_kind = "inherits"
            else:
                ref_kind = "implements"

            refs.append(
                self._make_reference(
                    target_name=target,
                    kind=ref_kind,
                    line=class_node.start_point[0] + 1,
                    source_name=class_name,
                )
            )

    def _swift_inheritance_target(self, spec_node, source: bytes) -> str | None:
        for child in self._iter_all_descendants(spec_node):
            if child.type in ("type_identifier", "simple_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text
        text = self.node_text(spec_node, source).strip()
        if "<" in text:
            text = text.split("<", 1)[0].strip()
        return text or None
