"""Apex (Salesforce) symbol and reference extractor.

Reuses the Java tree-sitter grammar via grammar aliasing.
Extends JavaExtractor with Apex-specific concepts:
- Sharing modifiers (with sharing, without sharing, inherited sharing)
- Salesforce annotations (@AuraEnabled, @IsTest, @InvocableMethod, etc.)
- SOQL queries (FROM clauses → object references)
- DML operations (insert, update, delete, upsert, merge, undelete)
- Trigger declarations
- System.Label references
"""

import re

from .java_lang import JavaExtractor


# Salesforce-specific annotations worth tracking
_SF_ANNOTATIONS = frozenset({
    "@AuraEnabled", "@IsTest", "@InvocableMethod", "@InvocableVariable",
    "@Future", "@Queueable", "@RemoteAction", "@TestSetup", "@TestVisible",
    "@SuppressWarnings", "@ReadOnly", "@RestResource", "@HttpGet",
    "@HttpPost", "@HttpPut", "@HttpDelete", "@HttpPatch",
    "@NamespaceAccessible", "@JsonAccess",
})

# Sharing keywords that modify class declarations in Apex
_SHARING_RE = re.compile(r'\b(with\s+sharing|without\s+sharing|inherited\s+sharing)\b')

# SOQL: extract object names from FROM clauses
_SOQL_FROM_RE = re.compile(r'\bFROM\s+([A-Z]\w+(?:__c|__r|__mdt|__e)?)', re.IGNORECASE)

# DML operations
_DML_RE = re.compile(
    r'\b(insert|update|delete|upsert|merge|undelete)\s+',
    re.IGNORECASE,
)

# System.Label references
_LABEL_RE = re.compile(r'System\.Label\.(\w+)')

# Trigger declaration pattern
_TRIGGER_RE = re.compile(
    r'trigger\s+(\w+)\s+on\s+(\w+)',
    re.IGNORECASE,
)


class ApexExtractor(JavaExtractor):
    """Apex extractor — extends Java with Salesforce-specific features."""

    @property
    def language_name(self) -> str:
        return "apex"

    @property
    def file_extensions(self) -> list[str]:
        return [".cls", ".trigger"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        self._pending_inherits = []
        text = source.decode("utf-8", errors="replace")

        # Check for trigger declaration (triggers use different syntax)
        if file_path.endswith(".trigger"):
            self._extract_trigger(text, symbols)

        self._walk_symbols(tree.root_node, source, symbols, parent_name=None)
        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = super().extract_references(tree, source, file_path)
        text = source.decode("utf-8", errors="replace")

        # Extract SOQL object references
        for m in _SOQL_FROM_RE.finditer(text):
            obj_name = m.group(1)
            line = text[:m.start()].count("\n") + 1
            refs.append(self._make_reference(
                target_name=obj_name,
                kind="soql",
                line=line,
            ))

        # Extract System.Label references
        for m in _LABEL_RE.finditer(text):
            label_name = m.group(1)
            line = text[:m.start()].count("\n") + 1
            refs.append(self._make_reference(
                target_name=f"Label.{label_name}",
                kind="label",
                line=line,
            ))

        return refs

    def _extract_trigger(self, text: str, symbols: list):
        """Extract trigger declaration as a symbol."""
        m = _TRIGGER_RE.search(text)
        if m:
            trigger_name = m.group(1)
            sobject_name = m.group(2)
            line = text[:m.start()].count("\n") + 1
            symbols.append(self._make_symbol(
                name=trigger_name,
                kind="trigger",
                line_start=line,
                line_end=line,
                qualified_name=trigger_name,
                signature=f"trigger {trigger_name} on {sobject_name}",
                is_exported=True,
            ))

    def _get_visibility(self, node, source) -> str:
        """Apex visibility — same as Java but also recognizes 'global'."""
        for child in node.children:
            if child.type == "modifiers":
                text = self.node_text(child, source)
                if "global" in text:
                    return "public"  # global ≈ public for our purposes
                if "private" in text:
                    return "private"
                if "protected" in text:
                    return "protected"
                if "public" in text:
                    return "public"
        return "package"

    def _extract_class(self, node, source, symbols, parent_name, kind="class"):
        """Override to detect Apex visibility and sharing modifiers.

        The Java grammar doesn't understand 'with sharing', so Apex declarations
        like 'public with sharing class Foo' get split: 'public with sharing'
        becomes a local_variable_declaration, and the class has no modifiers.
        We look backward in the source to find the actual visibility.
        """
        pre_count = len(symbols)
        super()._extract_class(node, source, symbols, parent_name, kind)

        if len(symbols) <= pre_count:
            return

        # The class symbol is at index pre_count (methods come after it)
        class_sym = symbols[pre_count]
        if class_sym["kind"] not in ("class", "interface"):
            return

        # Look at the source text preceding the class keyword for visibility
        # and sharing modifiers (up to 80 chars back from the class node start)
        lookback_start = max(0, node.start_byte - 80)
        context = source[lookback_start:node.start_byte + 50].decode("utf-8", errors="replace")

        # Fix visibility: check if public/global appears before 'class'
        vis = class_sym.get("visibility", "package")
        if vis == "package":
            ctx_lower = context.lower()
            if "global " in ctx_lower:
                class_sym["visibility"] = "public"
                class_sym["is_exported"] = True
            elif "public " in ctx_lower:
                class_sym["visibility"] = "public"
                class_sym["is_exported"] = True

        # Add sharing info to signature
        sharing_m = _SHARING_RE.search(context)
        if sharing_m:
            sharing = sharing_m.group(1).replace("\n", " ").strip()
            if class_sym["signature"] and sharing not in class_sym["signature"]:
                class_sym["signature"] = f"{sharing} {class_sym['signature']}"
