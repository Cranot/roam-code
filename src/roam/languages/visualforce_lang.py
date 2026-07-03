"""Visualforce page/component extractor.

Parses .page and .component files using the HTML grammar.
Extracts page structure and references to Apex controllers,
extensions, includes, and merge field expressions.
"""

from __future__ import annotations

import re

from .base import _SalesforceMarkupExtractor

# Controller/extensions attribute patterns
_CONTROLLER_RE = re.compile(r'controller\s*=\s*"([^"]+)"', re.IGNORECASE)
_EXTENSIONS_RE = re.compile(r'extensions\s*=\s*"([^"]+)"', re.IGNORECASE)

# <apex:include> and <apex:component> tags
_INCLUDE_RE = re.compile(
    r'<apex:(include|component)\s[^>]*pageName\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)

# Merge fields: {!expression}
_MERGE_FIELD_RE = re.compile(r"\{!([^}]+)\}")

# Identifier pattern for merge field expressions
_IDENT_RE = re.compile(r"\b([A-Z]\w+(?:__c|__r)?)\b")

# Standard VF objects to exclude
_VF_BUILTINS = frozenset(
    {
        "IF",
        "AND",
        "OR",
        "NOT",
        "NULL",
        "TRUE",
        "FALSE",
        "ISBLANK",
        "ISNULL",
        "TEXT",
        "VALUE",
        "LEN",
        "TRIM",
        "CONTAINS",
        "SUBSTITUTE",
        "CASE",
        "BLANKVALUE",
        "NULLVALUE",
        "BEGINS",
        "INCLUDES",
    }
)


class VisualforceExtractor(_SalesforceMarkupExtractor):
    """Visualforce page/component extractor using HTML grammar."""

    _CONTROLLER_RE = _CONTROLLER_RE
    _EXTENSIONS_RE = _EXTENSIONS_RE
    _INCLUDE_RE = _INCLUDE_RE
    _MERGE_FIELD_RE = _MERGE_FIELD_RE
    _MERGE_FIELD_IDENT_RE = _IDENT_RE
    _MERGE_FIELD_BUILTINS = _VF_BUILTINS

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

        symbols.append(
            self._make_symbol(
                name=name,
                kind=kind,
                line_start=1,
                line_end=text.count("\n") + 1,
                qualified_name=name,
                signature=f"apex:{kind} {name}",
                is_exported=True,
            )
        )

        return symbols
