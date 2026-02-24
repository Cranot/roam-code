from __future__ import annotations

from .generic_lang import GenericExtractor


class KotlinExtractor(GenericExtractor):
    """Kotlin dedicated extractor (Tier 1)."""

    def __init__(self):
        super().__init__(language="kotlin")

    @property
    def language_name(self) -> str:
        return "kotlin"

    @property
    def file_extensions(self) -> list[str]:
        return [".kt", ".kts"]

    def _classify_node(self, node) -> str | None:
        if node.type == "object_declaration":
            return "class"
        if node.type == "class_declaration":
            token_types = {c.type for c in node.children if not c.is_named}
            if "interface" in token_types:
                return "interface"
            if "enum" in token_types or any(
                c.type == "enum_class_body" for c in node.children if c.is_named
            ):
                return "enum"
            return "class"
        if node.type == "function_declaration":
            return "method" if self._in_type_context(node) else "function"
        return super()._classify_node(node)

    def _get_name(self, node, source) -> str | None:
        name = super()._get_name(node, source)
        if name:
            return name
        for child in node.children:
            if child.type in ("simple_identifier", "identifier", "type_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text
        return None

    def _in_type_context(self, node) -> bool:
        cur = node.parent
        while cur is not None:
            if cur.type in ("class_body", "enum_class_body"):
                return True
            cur = cur.parent
        return False

    def _extract_properties(self, body_node, source, symbols, class_name):
        """Extract body properties and constructor-bound properties (val/var)."""
        super()._extract_properties(body_node, source, symbols, class_name)

        class_node = body_node.parent if body_node is not None else None
        if class_node is None or class_node.type != "class_declaration":
            return

        for child in class_node.children:
            if child.type != "primary_constructor":
                continue
            for param in child.children:
                if param.type != "class_parameter":
                    continue
                binding = None
                name = None
                visibility = "public"
                for sub in param.children:
                    if not sub.is_named:
                        continue
                    if sub.type == "modifiers":
                        mods = self.node_text(sub, source).lower()
                        if "private" in mods:
                            visibility = "private"
                        elif "protected" in mods:
                            visibility = "protected"
                        elif "internal" in mods:
                            visibility = "internal"
                        elif "public" in mods:
                            visibility = "public"
                    if sub.type == "binding_pattern_kind":
                        binding = self.node_text(sub, source).strip()
                    elif sub.type in ("simple_identifier", "identifier"):
                        name = self.node_text(sub, source).strip()
                if binding not in ("val", "var") or not name:
                    continue
                qualified = f"{class_name}.{name}" if class_name else name
                symbols.append(
                    self._make_symbol(
                        name=name,
                        kind="property",
                        line_start=param.start_point[0] + 1,
                        line_end=param.end_point[0] + 1,
                        qualified_name=qualified,
                        signature=f"{binding} {name}",
                        parent_name=class_name,
                        visibility=visibility,
                        is_exported=visibility == "public",
                    )
                )

    def _extract_inheritance_refs(self, class_node, source, refs, class_name):
        """Kotlin delegation_specifier: first is superclass, rest are interfaces."""
        if class_node.type not in ("class_declaration", "object_declaration"):
            return super()._extract_inheritance_refs(class_node, source, refs, class_name)

        kind = self._classify_node(class_node) or "class"
        specs = [c for c in class_node.children if c.type == "delegation_specifier"]
        for i, spec in enumerate(specs):
            target = self._kotlin_delegation_target(spec, source)
            if not target:
                continue

            if kind == "class":
                has_ctor = any(
                    c.type == "constructor_invocation"
                    for c in self._iter_all_descendants(spec)
                )
                ref_kind = "inherits" if has_ctor else "implements"
            elif kind == "interface":
                ref_kind = "inherits"
            else:
                ref_kind = "inherits"

            refs.append(
                self._make_reference(
                    target_name=target,
                    kind=ref_kind,
                    line=class_node.start_point[0] + 1,
                    source_name=class_name,
                )
            )

    def _kotlin_delegation_target(self, spec_node, source: bytes) -> str | None:
        for child in self._iter_all_descendants(spec_node):
            if child.type in ("type_identifier", "simple_identifier"):
                text = self.node_text(child, source).strip()
                if text:
                    return text

        text = self.node_text(spec_node, source).strip()
        if not text:
            return None
        if " by " in text:
            text = text.split(" by ", 1)[0].strip()
        if "(" in text:
            text = text.split("(", 1)[0].strip()
        return text or None
