"""Aura (Lightning) component extractor.

Parses .cmp, .app, .evt, .intf, .design files using the HTML grammar.
Extracts component structure (attributes, handlers, events) and
references to Apex controllers and other components.
"""

from __future__ import annotations

import re

from .base import _SalesforceMarkupExtractor

# Aura attribute tags that define symbols
_ATTR_TAG = "aura:attribute"
_EVENT_TAG = "aura:registerevent"
_HANDLER_TAG = "aura:handler"
_METHOD_TAG = "aura:method"
_DEPENDENCY_TAG = "aura:dependency"

# Tags that reference Apex controllers
_CONTROLLER_RE = re.compile(r'controller\s*=\s*"([^"]+)"', re.IGNORECASE)
_EXTENDS_RE = re.compile(r'extends\s*=\s*"([^"]+)"', re.IGNORECASE)
_IMPLEMENTS_RE = re.compile(r'implements\s*=\s*"([^"]+)"', re.IGNORECASE)

# Custom component tags: <c:MyComponent> or <namespace:Component>
_CUSTOM_TAG_RE = re.compile(r"<(\w+):(\w+)")

# $Label references: $Label.c.MyLabel -> capture "Label.c.MyLabel" / "Label.MyLabel"
_LABEL_REF_RE = re.compile(r"\$(Label\.(?:\w+\.)?\w+)")

# Expression references: {!v.attribute} or {!c.helperMethod}
_EXPR_RE = re.compile(r"\{!([^}]+)\}")


class AuraExtractor(_SalesforceMarkupExtractor):
    """Aura Lightning component extractor using HTML grammar."""

    _CONTROLLER_RE = _CONTROLLER_RE
    _EXTENDS_RE = _EXTENDS_RE
    _IMPLEMENTS_RE = _IMPLEMENTS_RE
    _CUSTOM_TAG_RE = _CUSTOM_TAG_RE
    _CUSTOM_TAG_STANDARD_NS = frozenset({"aura", "lightning", "ltng", "ui", "force"})
    _LABEL_REF_RE = _LABEL_REF_RE

    @property
    def language_name(self) -> str:
        return "aura"

    @property
    def file_extensions(self) -> list[str]:
        return [".cmp", ".app", ".evt", ".intf", ".design"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        text = source.decode("utf-8", errors="replace")

        import os

        basename = os.path.basename(file_path)
        name = basename.rsplit(".", 1)[0]
        ext = basename.rsplit(".", 1)[1] if "." in basename else ""

        kind_map = {
            "cmp": "component",
            "app": "application",
            "evt": "event",
            "intf": "interface",
            "design": "design",
        }
        kind = kind_map.get(ext, "component")

        symbols.append(
            self._make_symbol(
                name=name,
                kind=kind,
                line_start=1,
                line_end=text.count("\n") + 1,
                qualified_name=name,
                signature=f"aura:{kind} {name}",
                is_exported=True,
            )
        )

        # Extract aura:attribute declarations (deduplicate by name)
        seen_names = set()
        self._extract_attributes(tree.root_node, source, symbols, name, seen_names)

        return symbols

    def _extract_attributes(self, node, source, symbols, parent_name, seen_names, _depth=0):
        """Walk tree to find aura:attribute, aura:method, etc."""
        if _depth > 10:
            return
        for child in node.children:
            if child.type == "element" or child.type == "self_closing_tag":
                tag_node = child if child.type == "self_closing_tag" else None
                if child.type == "element":
                    for sub in child.children:
                        if sub.type in ("start_tag", "self_closing_tag"):
                            tag_node = sub
                            break

                if tag_node:
                    tag_name = self._get_tag_name_from_tag(tag_node, source)
                    if tag_name:
                        tag_lower = tag_name.lower()
                        if tag_lower == _ATTR_TAG:
                            self._extract_aura_attr(tag_node, source, symbols, parent_name, "property", seen_names)
                        elif tag_lower == _METHOD_TAG:
                            self._extract_aura_attr(tag_node, source, symbols, parent_name, "method", seen_names)
                        elif tag_lower == _EVENT_TAG:
                            self._extract_aura_attr(tag_node, source, symbols, parent_name, "event", seen_names)

            self._extract_attributes(child, source, symbols, parent_name, seen_names, _depth + 1)

    def _extract_aura_attr(self, tag_node, source, symbols, parent_name, kind, seen_names):
        """Extract name attribute from an aura tag."""
        name = self._get_attr_value(tag_node, source, "name")
        if name and name not in seen_names:
            seen_names.add(name)
            type_val = self._get_attr_value(tag_node, source, "type")
            sig = f"{kind} {name}"
            if type_val:
                sig += f": {type_val}"

            symbols.append(
                self._make_symbol(
                    name=name,
                    kind=kind,
                    line_start=tag_node.start_point[0] + 1,
                    line_end=tag_node.end_point[0] + 1,
                    qualified_name=f"{parent_name}.{name}",
                    signature=sig,
                    parent_name=parent_name,
                    is_exported=True,
                )
            )

    def _get_tag_name_from_tag(self, tag_node, source) -> str | None:
        """Get tag name from a start_tag or self_closing_tag node."""
        for child in tag_node.children:
            if child.type == "tag_name":
                return self.node_text(child, source)
        return None

    def _get_attr_value(self, tag_node, source, attr_name: str) -> str | None:
        """Get the value of a named attribute from a tag node."""
        for child in tag_node.children:
            if child.type == "attribute":
                name_node = None
                value_node = None
                for sub in child.children:
                    if sub.type == "attribute_name":
                        name_node = sub
                    elif sub.type == "quoted_attribute_value" or sub.type == "attribute_value":
                        value_node = sub
                if name_node and value_node:
                    name = self.node_text(name_node, source)
                    if name.lower() == attr_name.lower():
                        val = self.node_text(value_node, source)
                        # Strip quotes
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1]
                        elif val.startswith("'") and val.endswith("'"):
                            val = val[1:-1]
                        return val
        return None
