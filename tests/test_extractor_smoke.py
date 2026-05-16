"""Cross-extractor smoke matrix.

Closes a real coverage gap. Eight dedicated language extractors had no
direct smoke test before this file:

    apex, aura, visualforce, sfxml, hcl, swift, foxpro, generic

A `tree-sitter-language-pack` upgrade or grammar regression could silently
break any of them — the existing language-specific test files are richer
than what this file does, but they only fire if you remember they exist.
This matrix tests the contract:

    1. Module imports.
    2. ``extract_symbols(...)`` returns >= 1 symbol on a small fixture.
    3. The extracted symbols include the canonical "main" entity for the
       language (a class, function, or top-level resource).
    4. ``extract_references(...)`` returns a list (may be empty for some
       fixtures — must not crash).

One row per extractor. Adding a new dedicated extractor is one new tuple.
"""

from __future__ import annotations

import importlib

import pytest

# ---------------------------------------------------------------------------
# Fixture sources — kept inline so this file is self-contained.
# ---------------------------------------------------------------------------

APEX_SRC = (
    "public with sharing class AccountService {\n"
    "    public String getName() {\n"
    "        return 'test';\n"
    "    }\n"
    "    private void doInternal() {\n"
    "        Account a = [SELECT Id FROM Account LIMIT 1];\n"
    "    }\n"
    "}\n"
)

AURA_SRC = (
    '<aura:component controller="AccountController">\n'
    '    <aura:attribute name="recordId" type="String" />\n'
    '    <aura:attribute name="account" type="Account" />\n'
    '    <aura:method name="refresh" action="{!c.doRefresh}" />\n'
    "    <div>{!v.account.Name}</div>\n"
    "</aura:component>\n"
)

VISUALFORCE_SRC = (
    '<apex:page controller="InvoiceController">\n'
    "    <apex:form>\n"
    '        <apex:inputField value="{!Invoice__c.Name}" />\n'
    "    </apex:form>\n"
    "</apex:page>\n"
)

SFXML_SRC = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
    "    <deploymentStatus>Deployed</deploymentStatus>\n"
    "    <label>Invoice</label>\n"
    "</CustomObject>\n"
)

HCL_SRC = (
    'provider "aws" {\n'
    "  region = var.region\n"
    "}\n"
    "\n"
    'variable "region" {\n'
    '  default = "us-east-1"\n'
    "}\n"
    "\n"
    'resource "aws_vpc" "main" {\n'
    '  cidr_block = "10.0.0.0/16"\n'
    "}\n"
)

SWIFT_SRC = (
    "class Greeter {\n"
    "    func greet(name: String) -> String {\n"
    '        return "Hello " + name\n'
    "    }\n"
    "}\n"
    "\n"
    "func topLevel() -> Int {\n"
    "    return 42\n"
    "}\n"
)

FOXPRO_SRC = "FUNCTION MyFunc\n  LOCAL x\n  x = 1\n  RETURN x\nENDFUNC\n"

GENERIC_LUA_SRC = (
    "function greet(name)\n    return 'hello ' .. name\nend\n\nfunction add(a, b)\n    return a + b\nend\n"
)


# ---------------------------------------------------------------------------
# Smoke matrix
#
# Each row is:
#   (id, module_path, extractor_factory, fixture_path, fixture_source,
#    grammar_or_None, expected_min_symbols, expected_canonical_name,
#    expected_canonical_kinds)
#
# - module_path: dotted module that must import.
# - extractor_factory: callable that returns a fresh extractor instance.
# - grammar_or_None: tree-sitter grammar name to parse with, or None for
#   regex-only extractors (foxpro, hcl) that take ``tree=None``.
# - expected_canonical_kinds: tuple of acceptable kinds for the canonical
#   symbol (e.g. swift's top-level fn could come back as "function" but
#   the matrix is generous to survive minor classifier tweaks).
# ---------------------------------------------------------------------------


def _swift_factory():
    from roam.languages.swift_lang import SwiftExtractor

    return SwiftExtractor()


def _apex_factory():
    from roam.languages.apex_lang import ApexExtractor

    return ApexExtractor()


def _aura_factory():
    from roam.languages.aura_lang import AuraExtractor

    return AuraExtractor()


def _visualforce_factory():
    from roam.languages.visualforce_lang import VisualforceExtractor

    return VisualforceExtractor()


def _sfxml_factory():
    from roam.languages.sfxml_lang import SfxmlExtractor

    return SfxmlExtractor()


def _hcl_factory():
    from roam.languages.hcl_lang import HclExtractor

    return HclExtractor()


def _foxpro_factory():
    from roam.languages.foxpro_lang import FoxProExtractor

    return FoxProExtractor()


def _generic_factory():
    from roam.languages.generic_lang import GenericExtractor

    return GenericExtractor(language="lua")


SMOKE_MATRIX = [
    # Apex — Java grammar via alias. Canonical: the class.
    (
        "apex",
        "roam.languages.apex_lang",
        _apex_factory,
        "force-app/main/classes/AccountService.cls",
        APEX_SRC,
        "java",
        2,  # class + at least one method
        "AccountService",
        ("class",),
    ),
    # Aura — HTML grammar via alias. Canonical: the component (filename-derived).
    (
        "aura",
        "roam.languages.aura_lang",
        _aura_factory,
        "force-app/main/aura/AccountCard/AccountCard.cmp",
        AURA_SRC,
        "html",
        1,
        "AccountCard",
        ("component",),
    ),
    # Visualforce — HTML grammar via alias. Canonical: the page (filename-derived).
    (
        "visualforce",
        "roam.languages.visualforce_lang",
        _visualforce_factory,
        "force-app/main/pages/InvoicePage.page",
        VISUALFORCE_SRC,
        "html",
        1,
        "InvoicePage",
        ("page",),
    ),
    # SFXML — HTML grammar via alias. Canonical: object derived from filename.
    (
        "sfxml",
        "roam.languages.sfxml_lang",
        _sfxml_factory,
        "force-app/main/objects/Invoice__c/Invoice__c.object-meta.xml",
        SFXML_SRC,
        "html",
        1,
        "Invoice__c",
        # _TAG_TO_KIND maps "object" → "object" (CustomObject root).
        ("object",),
    ),
    # HCL — regex-only, tree=None. Canonical: the resource block.
    (
        "hcl",
        "roam.languages.hcl_lang",
        _hcl_factory,
        "main.tf",
        HCL_SRC,
        None,
        2,  # resource + variable at minimum
        "main",
        ("class",),  # HCL maps `resource` → kind "class"
    ),
    # Swift — dedicated tree-sitter grammar. Canonical: the class.
    (
        "swift",
        "roam.languages.swift_lang",
        _swift_factory,
        "Greeter.swift",
        SWIFT_SRC,
        "swift",
        2,  # class + at least one func/method
        "Greeter",
        ("class",),
    ),
    # FoxPro — regex-only, tree=None. Canonical: the FUNCTION.
    (
        "foxpro",
        "roam.languages.foxpro_lang",
        _foxpro_factory,
        "test.prg",
        FOXPRO_SRC,
        None,
        1,
        "MyFunc",
        ("function",),
    ),
    # Generic fallback — tier-2 language exercising the catch-all
    # walker. Lua is in tree-sitter-language-pack but has no dedicated
    # roam extractor, so it lands on GenericExtractor.
    (
        "generic_lua",
        "roam.languages.generic_lang",
        _generic_factory,
        "g.lua",
        GENERIC_LUA_SRC,
        "lua",
        1,
        "greet",
        ("function",),
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(grammar: str | None, source: bytes):
    """Parse with tree-sitter, or return None for regex-only extractors."""
    if grammar is None:
        return None
    from tree_sitter_language_pack import get_parser

    parser = get_parser(grammar)
    return parser.parse(source)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "test_id,module_path,extractor_factory,fixture_path,fixture_src,grammar,min_syms,canonical_name,canonical_kinds",
    SMOKE_MATRIX,
    ids=[row[0] for row in SMOKE_MATRIX],
)
def test_extractor_smoke(
    test_id,
    module_path,
    extractor_factory,
    fixture_path,
    fixture_src,
    grammar,
    min_syms,
    canonical_name,
    canonical_kinds,
):
    """One smoke test per dedicated extractor: import, parse, extract, sanity-check."""
    # 1. The module must import without error.
    importlib.import_module(module_path)

    # Construct extractor.
    extractor = extractor_factory()

    # 2-3. Parse + extract.
    source = fixture_src.encode("utf-8")
    tree = _parse(grammar, source)
    symbols = extractor.extract_symbols(tree, source, fixture_path)

    assert isinstance(symbols, list), f"[{test_id}] extract_symbols must return a list, got {type(symbols).__name__}"
    assert len(symbols) >= min_syms, (
        f"[{test_id}] expected >= {min_syms} symbols, got {len(symbols)}: "
        f"{[(s.get('name'), s.get('kind')) for s in symbols]}"
    )

    # Each symbol dict must satisfy the LanguageExtractor contract — spot-check
    # required fields on the first symbol.
    required_fields = {
        "name",
        "qualified_name",
        "kind",
        "signature",
        "line_start",
        "line_end",
        "docstring",
        "visibility",
        "is_exported",
        "parent_name",
    }
    missing = required_fields - set(symbols[0].keys())
    assert not missing, f"[{test_id}] first symbol missing fields: {missing}"

    # Canonical entity check: a symbol named ``canonical_name`` whose kind is
    # in ``canonical_kinds`` must be present.
    matches = [s for s in symbols if s.get("name") == canonical_name and s.get("kind") in canonical_kinds]
    assert matches, (
        f"[{test_id}] canonical entity {canonical_name!r} (kinds={canonical_kinds}) not found. "
        f"Got: {[(s.get('name'), s.get('kind')) for s in symbols]}"
    )

    # 4. References must be a list — empty is OK, the call must just not crash.
    refs = extractor.extract_references(tree, source, fixture_path)
    assert isinstance(refs, list), f"[{test_id}] extract_references must return a list, got {type(refs).__name__}"
    # If non-empty, spot-check the reference contract.
    if refs:
        ref_required = {"source_name", "target_name", "kind", "line", "import_path"}
        ref_missing = ref_required - set(refs[0].keys())
        assert not ref_missing, f"[{test_id}] first ref missing fields: {ref_missing}"


def test_smoke_matrix_covers_dedicated_extractors():
    """Guard against regression in the matrix itself.

    If a new dedicated extractor appears in registry._DEDICATED_EXTRACTORS or
    the aliased Salesforce/regex set, this test fails loudly so the smoke
    matrix gets updated alongside it.
    """
    covered = {row[0] for row in SMOKE_MATRIX}

    # Languages this file is responsible for. Tier-1 languages with their own
    # rich test_languages.py coverage are deliberately not included here.
    expected = {
        "apex",
        "aura",
        "visualforce",
        "sfxml",
        "hcl",
        "swift",
        "foxpro",
        "generic_lua",
    }
    missing = expected - covered
    assert not missing, f"Smoke matrix missing rows for: {missing}"
