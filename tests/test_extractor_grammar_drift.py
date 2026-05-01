"""Grammar-drift resilience tests for the language extractors.

The bug class these tests guard: a tree-sitter grammar version bump
silently changes a node type or its child shape, the extractor's
hardcoded AST query stops matching, and CI passes anyway because the
test happens to run on the *previous* grammar version. The 2026-05-01
CI session caught this — Linux's ``tree-sitter-language-pack 1.6.x``
shipped a Kotlin grammar where ``object Foo`` and ``enum class Foo``
no longer produced ``object_declaration`` / ``enum_class_body`` node
types, but our Windows wheel still did. Local tests passed; CI didn't.

The defence is a regex-based fallback in the extractor that scans the
source text for the canonical declaration syntax, *independent of the
AST*. These tests assert that fallback always extracts the canonical
top-level decls regardless of what the AST happens to emit.
"""

from __future__ import annotations

import pytest
from tree_sitter_language_pack import get_parser

from roam.index.parser import GRAMMAR_ALIASES
from roam.languages.kotlin_lang import KotlinExtractor
from roam.languages.registry import get_extractor


def _extract_kotlin(source_text: str) -> list[dict]:
    grammar = GRAMMAR_ALIASES.get("kotlin", "kotlin")
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor("kotlin")
    return extractor.extract_symbols(tree, source, "drift.kt")


def _names(symbols: list[dict]) -> set[str]:
    return {s["name"] for s in symbols}


def _kind_of(symbols: list[dict], name: str) -> str | None:
    for s in symbols:
        if s["name"] == name:
            return s["kind"]
    return None


class TestKotlinTopLevelDeclsAlwaysExtracted:
    """The text-scan fallback in ``KotlinExtractor`` must surface
    every canonical top-level declaration regardless of how the AST
    classifies it."""

    def test_object_declaration_always_extracted(self):
        names = _names(_extract_kotlin("object Singleton\n"))
        assert "Singleton" in names

    def test_enum_class_always_extracted(self):
        names = _names(_extract_kotlin("enum class Color { RED, BLUE }\n"))
        assert "Color" in names
        assert _kind_of(_extract_kotlin("enum class Color { RED, BLUE }\n"), "Color") == "enum"

    def test_interface_always_extracted(self):
        symbols = _extract_kotlin("interface Talker { fun speak(): String }\n")
        assert _kind_of(symbols, "Talker") == "interface"

    def test_class_always_extracted(self):
        symbols = _extract_kotlin("class Person(val name: String)\n")
        assert _kind_of(symbols, "Person") == "class"

    def test_modifier_combinations_dont_break_extraction(self):
        """Belt-and-suspenders regex must accept all Kotlin modifier
        permutations the language allows."""
        cases = [
            ("public sealed class Base", "Base", "class"),
            ("internal abstract class Provider", "Provider", "class"),
            ("private data class Money(val amount: Int)", "Money", "class"),
            ("open class Animal", "Animal", "class"),
            ("final class Locked", "Locked", "class"),
            ("data class User(val id: Int)", "User", "class"),
            ("sealed interface Effect", "Effect", "interface"),
            ("public enum class Status", "Status", "enum"),
            ("companion object Factory", "Factory", "class"),
        ]
        for source, expected_name, expected_kind in cases:
            symbols = _extract_kotlin(source + "\n")
            assert expected_name in _names(symbols), f"missed {expected_name!r} in {source!r}"
            assert _kind_of(symbols, expected_name) == expected_kind, (
                f"wrong kind for {expected_name!r} in {source!r}: "
                f"got {_kind_of(symbols, expected_name)!r}, expected {expected_kind!r}"
            )

    def test_object_expression_not_extracted_as_decl(self):
        """``val x = object : T { ... }`` is an *expression*, not a
        top-level declaration. The fallback must NOT pick it up."""
        # Use distinct names so we can assert their absence.
        source = "class Holder {\n  val anonymous = object : Runnable { override fun run() {} }\n}\n"
        symbols = _extract_kotlin(source)
        # Extractor may emit more class-shaped symbols; the contract
        # is just that ``Runnable`` doesn't appear as a class.
        # ``Runnable`` is used as a type, not declared.
        names = _names(symbols)
        assert "Runnable" not in names, f"object-expression's type ref should not be extracted as a class. Got: {names}"

    def test_idempotent_under_AST_dropout(self):
        """If the AST returns no symbols at all (broken grammar), the
        regex fallback alone must still surface the top-level decls.

        Simulated by patching ``GenericExtractor.extract_symbols`` to
        return ``[]`` — equivalent to the worst-case grammar drift."""
        from unittest.mock import patch

        from roam.languages.generic_lang import GenericExtractor

        source = "interface Talker\nclass Base\nobject Singleton\nenum class Color { RED }\n"
        with patch.object(GenericExtractor, "extract_symbols", return_value=[]):
            symbols = _extract_kotlin(source)
        names = _names(symbols)
        # All four must be there from the regex fallback alone.
        for required in ("Talker", "Base", "Singleton", "Color"):
            assert required in names, f"regex fallback missed {required!r} (got {names})"
        # Color must still be classified as enum, even with zero AST
        # signal — the regex captures the kind from the source syntax.
        assert _kind_of(symbols, "Color") == "enum"
        assert _kind_of(symbols, "Talker") == "interface"

    def test_ast_and_regex_dont_double_emit_same_symbol(self):
        """When both the AST and the regex find the same name, only one
        symbol entry survives (the regex-fallback ``promote_or_add``
        merges into the existing entry rather than appending)."""
        symbols = _extract_kotlin("object Singleton\n")
        singleton_count = sum(1 for s in symbols if s["name"] == "Singleton")
        assert singleton_count == 1, f"Singleton should appear exactly once; got {singleton_count} entries"


class TestKotlinExtractorRegistryRouting:
    """Sanity: ``get_extractor('kotlin')`` returns the dedicated
    extractor, not the generic one. A regression here would silently
    drop *all* Kotlin-specific behaviour."""

    def test_registry_routes_kotlin_to_dedicated_extractor(self):
        assert isinstance(get_extractor("kotlin"), KotlinExtractor)


class TestPathTokenBoostStability:
    """The retrieve path-token boost is sensitive to splitting heuristics —
    these property-style cases lock in the behaviour we tuned in iter 4."""

    @pytest.mark.parametrize(
        "path,query,should_boost",
        [
            # Direct token match
            ("src/roam/languages/ruby_lang.py", "Ruby extractor", True),
            # Prefix-tolerant: query "clone" matches path "clones"
            ("src/roam/commands/cmd_clones.py", "AST clone detection", True),
            # Reverse prefix: query "languages" matches path "lang"
            ("src/roam/languages/ruby_lang.py", "Ruby languages", True),
            # No match: distinct domain
            ("src/roam/output/sarif.py", "Ruby extractor", False),
            # Short tokens (<4 chars) on the longer side don't cross-match
            ("src/roam/cli.py", "ABC", False),
        ],
    )
    def test_path_token_boost_examples(self, path, query, should_boost):
        from roam.retrieve.rerank import _path_token_boost

        candidates = [{"symbol_id": 1, "file_path": path}]
        boost = _path_token_boost(candidates, query)
        if should_boost:
            assert boost.get(1, 0.0) > 0, f"expected boost for {path!r} on {query!r}"
        else:
            assert boost.get(1, 0.0) == 0, f"unexpected boost for {path!r} on {query!r}"
