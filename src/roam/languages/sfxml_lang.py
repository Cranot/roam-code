"""Salesforce Metadata XML extractor.

Parses *-meta.xml files using the HTML tree-sitter grammar.
Extracts metadata type definitions and references to Apex classes,
custom objects, fields, and other metadata components.
"""

from __future__ import annotations

import re

from .base import LanguageExtractor


# Map XML root tags to symbol kinds
_TAG_TO_KIND = {
    "apexclass": "class",
    "apextrigger": "trigger",
    "apexpage": "page",
    "apexcomponent": "component",
    "customobject": "object",
    "customfield": "field",
    "custommetadata": "metadata",
    "customlabel": "label",
    "customtab": "tab",
    "layout": "layout",
    "profile": "profile",
    "permissionset": "permission_set",
    "flow": "flow",
    "flexipage": "page",
    "lightningcomponentbundle": "component",
    "staticresource": "resource",
    "remotesitesetting": "config",
    "connectedapp": "config",
    "customapplication": "application",
    "assignmentrule": "rule",
    "autoresponserule": "rule",
    "escalationrule": "rule",
    "sharingrule": "rule",
    "validationrule": "rule",
    "workflowrule": "rule",
    "emailtemplate": "template",
    "report": "report",
    "dashboard": "dashboard",
}

# Tags that contain references to other metadata
_REF_TAGS = frozenset({
    "apexclass", "controller", "extensions", "template",
    "field", "referenceto", "lookupfilter", "customobject",
    "targetobject", "relatedlist", "sobjecttype",
})

# Formula field pattern
_FORMULA_IDENT_RE = re.compile(r'\b([A-Z]\w+(?:__c|__r)?)\b')


class SfxmlExtractor(LanguageExtractor):
    """SF Metadata XML extractor using HTML grammar."""

    @property
    def language_name(self) -> str:
        return "sfxml"

    @property
    def file_extensions(self) -> list[str]:
        return []  # detected via -meta.xml suffix

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Extract metadata type as a symbol from the XML structure."""
        symbols = []
        text = source.decode("utf-8", errors="replace")

        # Derive metadata name from filename
        # e.g. "AccountService.cls-meta.xml" → "AccountService"
        # e.g. "Account.object-meta.xml" → "Account"
        import os
        basename = os.path.basename(file_path)
        # Strip -meta.xml suffix, then get the name before the extension
        name = basename.replace("-meta.xml", "")
        name_parts = name.rsplit(".", 1)
        meta_name = name_parts[0]
        meta_type = name_parts[1] if len(name_parts) > 1 else "unknown"

        kind = _TAG_TO_KIND.get(meta_type.lower(), "metadata")

        # Skip sidecar files for types that have their own primary file
        # (e.g. AccountService.cls-meta.xml is a sidecar for AccountService.cls)
        if meta_type.lower() in ("cls", "trigger", "page", "component"):
            return symbols

        symbols.append(self._make_symbol(
            name=meta_name,
            kind=kind,
            line_start=1,
            line_end=text.count("\n") + 1,
            qualified_name=f"{meta_type}.{meta_name}",
            signature=f"{meta_type} {meta_name}",
            is_exported=True,
        ))

        # Extract child elements as nested symbols (fields, rules, etc.)
        self._walk_xml_symbols(tree.root_node, source, symbols, meta_name)

        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        """Extract references to other metadata components."""
        refs = []
        text = source.decode("utf-8", errors="replace")

        # Walk XML elements looking for reference tags
        self._walk_xml_refs(tree.root_node, source, refs)

        # Scan formula fields for identifiers
        self._scan_formulas(text, refs)

        return refs

    def _walk_xml_symbols(self, node, source, symbols, parent_name, _depth=0):
        """Walk HTML/XML tree to find meaningful metadata elements."""
        if _depth > 10:
            return
        for child in node.children:
            if child.type == "element":
                tag = self._get_tag_name(child, source)
                if tag and tag.lower() in ("fields", "validationrules",
                                           "fieldsets", "listviews",
                                           "weblinks", "recordtypes"):
                    # Find the <fullName> child for the element name
                    full_name = self._find_child_element_text(child, source, "fullname")
                    if full_name:
                        kind = "field" if tag.lower() == "fields" else "rule"
                        symbols.append(self._make_symbol(
                            name=full_name,
                            kind=kind,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            qualified_name=f"{parent_name}.{full_name}",
                            parent_name=parent_name,
                            is_exported=True,
                        ))
            self._walk_xml_symbols(child, source, symbols, parent_name, _depth + 1)

    def _walk_xml_refs(self, node, source, refs, _depth=0):
        """Walk HTML/XML tree to find reference elements."""
        if _depth > 10:
            return
        for child in node.children:
            if child.type == "element":
                tag = self._get_tag_name(child, source)
                if tag and tag.lower() in _REF_TAGS:
                    text = self._get_element_text(child, source)
                    if text and len(text) < 200 and not text.startswith("<"):
                        refs.append(self._make_reference(
                            target_name=text.strip(),
                            kind="metadata_ref",
                            line=child.start_point[0] + 1,
                        ))
            self._walk_xml_refs(child, source, refs, _depth + 1)

    def _scan_formulas(self, text: str, refs: list):
        """Scan for formula field references."""
        # Look for <formula> elements
        formula_re = re.compile(r'<formula>(.*?)</formula>', re.DOTALL | re.IGNORECASE)
        for m in formula_re.finditer(text):
            formula = m.group(1)
            line = text[:m.start()].count("\n") + 1
            for ident_m in _FORMULA_IDENT_RE.finditer(formula):
                name = ident_m.group(1)
                if name not in ("IF", "AND", "OR", "NOT", "TRUE", "FALSE",
                                "NULL", "ISBLANK", "TEXT", "VALUE", "TODAY",
                                "NOW", "YEAR", "MONTH", "DAY"):
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="formula_ref",
                        line=line,
                    ))

    def _get_tag_name(self, element_node, source) -> str | None:
        """Get the tag name from an HTML element node."""
        for child in element_node.children:
            if child.type in ("start_tag", "self_closing_tag"):
                for sub in child.children:
                    if sub.type == "tag_name":
                        return self.node_text(sub, source)
        return None

    def _get_element_text(self, element_node, source) -> str | None:
        """Get the text content of an element (between tags)."""
        for child in element_node.children:
            if child.type == "text":
                return self.node_text(child, source).strip()
        return None

    def _find_child_element_text(self, parent_node, source, tag_name: str) -> str | None:
        """Find a child element by tag name and return its text content."""
        for child in parent_node.children:
            if child.type == "element":
                tag = self._get_tag_name(child, source)
                if tag and tag.lower() == tag_name.lower():
                    return self._get_element_text(child, source)
            # Recurse one level for nested structures
            for sub in child.children:
                if sub.type == "element":
                    tag = self._get_tag_name(sub, source)
                    if tag and tag.lower() == tag_name.lower():
                        return self._get_element_text(sub, source)
        return None
