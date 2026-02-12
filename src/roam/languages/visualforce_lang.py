"""Visualforce page/component extractor.

Parses .page and .component files using the HTML grammar.
Extracts page structure and references to Apex controllers,
extensions, includes, and merge field expressions.
"""

import re

from .base import LanguageExtractor


# Controller/extensions attribute patterns
_CONTROLLER_RE = re.compile(r'controller\s*=\s*"([^"]+)"', re.IGNORECASE)
_EXTENSIONS_RE = re.compile(r'extensions\s*=\s*"([^"]+)"', re.IGNORECASE)

# <apex:include> and <apex:component> tags
_INCLUDE_RE = re.compile(
    r'<apex:(include|component)\s[^>]*pageName\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)

# Merge fields: {!expression}
_MERGE_FIELD_RE = re.compile(r'\{!([^}]+)\}')

# Identifier pattern for merge field expressions
_IDENT_RE = re.compile(r'\b([A-Z]\w+(?:__c|__r)?)\b')

# Standard VF objects to exclude
_VF_BUILTINS = frozenset({
    "IF", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE",
    "ISBLANK", "ISNULL", "TEXT", "VALUE", "LEN", "TRIM",
    "CONTAINS", "SUBSTITUTE", "CASE", "BLANKVALUE",
    "NULLVALUE", "BEGINS", "INCLUDES",
})


class VisualforceExtractor(LanguageExtractor):
    """Visualforce page/component extractor using HTML grammar."""

    @property
    def language_name(self) -> str:
        return "visualforce"

    @property
    def file_extensions(self) -> list[str]:
        return [".page", ".component"]

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        symbols = []
        text = source.decode("utf-8", errors="replace")

        import os
        basename = os.path.basename(file_path)
        name = basename.rsplit(".", 1)[0]
        ext = basename.rsplit(".", 1)[1] if "." in basename else ""

        kind = "page" if ext == "page" else "component"

        symbols.append(self._make_symbol(
            name=name,
            kind=kind,
            line_start=1,
            line_end=text.count("\n") + 1,
            qualified_name=name,
            signature=f"apex:{kind} {name}",
            is_exported=True,
        ))

        return symbols

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        refs = []
        text = source.decode("utf-8", errors="replace")

        # Controller references
        for m in _CONTROLLER_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            refs.append(self._make_reference(
                target_name=m.group(1),
                kind="controller",
                line=line,
            ))

        # Extension references (comma-separated)
        for m in _EXTENSIONS_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            for ext_name in m.group(1).split(","):
                ext_name = ext_name.strip()
                if ext_name:
                    refs.append(self._make_reference(
                        target_name=ext_name,
                        kind="controller",
                        line=line,
                    ))

        # Include/component references
        for m in _INCLUDE_RE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            refs.append(self._make_reference(
                target_name=m.group(2),
                kind="include",
                line=line,
            ))

        # Merge field references — extract identifiers from {!...} expressions
        seen = set()
        for m in _MERGE_FIELD_RE.finditer(text):
            expr = m.group(1)
            line = text[:m.start()].count("\n") + 1
            for ident_m in _IDENT_RE.finditer(expr):
                name = ident_m.group(1)
                if name not in _VF_BUILTINS and name not in seen:
                    seen.add(name)
                    refs.append(self._make_reference(
                        target_name=name,
                        kind="merge_field",
                        line=line,
                    ))

        return refs
