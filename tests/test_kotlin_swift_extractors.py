"""Focused extraction tests for Kotlin/Swift Tier-1 extractors."""

from __future__ import annotations

from roam.index.parser import GRAMMAR_ALIASES
from roam.languages.registry import get_extractor
from tree_sitter_language_pack import get_parser


def _parse_and_extract(source_text: str, language: str, file_path: str):
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _symbol(symbols: list[dict], name: str) -> dict:
    return next(s for s in symbols if s["name"] == name)


class TestRegistryRouting:
    def test_get_extractor_returns_kotlin_dedicated_extractor(self):
        from roam.languages.kotlin_lang import KotlinExtractor

        extractor = get_extractor("kotlin")
        assert isinstance(extractor, KotlinExtractor)

    def test_get_extractor_returns_swift_dedicated_extractor(self):
        from roam.languages.swift_lang import SwiftExtractor

        extractor = get_extractor("swift")
        assert isinstance(extractor, SwiftExtractor)


class TestKotlinExtractor:
    def test_kotlin_class_interface_enum_and_object_kinds(self):
        source = (
            "interface Talker { fun speak(): String }\n"
            "class Base\n"
            "object Singleton\n"
            "enum class Color { RED }\n"
        )

        symbols, _ = _parse_and_extract(source, "kotlin", "models.kt")

        assert _symbol(symbols, "Talker")["kind"] == "interface"
        assert _symbol(symbols, "Base")["kind"] == "class"
        assert _symbol(symbols, "Singleton")["kind"] == "class"
        assert _symbol(symbols, "Color")["kind"] == "enum"

    def test_kotlin_constructor_properties_and_inheritance_refs(self):
        source = (
            "interface Talker { fun speak(): String }\n"
            "open class Base\n"
            "class Person(val name: String, private var age: Int) : Base(), Talker {\n"
            "  fun speak(): String = name\n"
            "}\n"
        )

        symbols, refs = _parse_and_extract(source, "kotlin", "person.kt")

        name_prop = _symbol(symbols, "name")
        age_prop = _symbol(symbols, "age")
        speak = _symbol(symbols, "speak")

        assert name_prop["kind"] == "property"
        assert name_prop["parent_name"] == "Person"
        assert name_prop["visibility"] == "public"
        assert age_prop["kind"] == "property"
        assert age_prop["visibility"] == "private"
        assert speak["kind"] == "method"

        inherits = {(r["source_name"], r["target_name"]) for r in refs if r["kind"] == "inherits"}
        implements = {(r["source_name"], r["target_name"]) for r in refs if r["kind"] == "implements"}
        assert ("Person", "Base") in inherits
        assert ("Person", "Talker") in implements


class TestSwiftExtractor:
    def test_swift_protocol_struct_enum_constructor_and_properties(self):
        source = (
            "protocol Walkable {}\n"
            "class Person {\n"
            "  var name: String = \"x\"\n"
            "  private let age: Int = 5\n"
            "  init(name: String) { self.name = name }\n"
            "  func speak() -> String { return name }\n"
            "}\n"
            "struct Point { let x: Int }\n"
            "enum Color { case red }\n"
        )

        symbols, _ = _parse_and_extract(source, "swift", "models.swift")

        assert _symbol(symbols, "Walkable")["kind"] == "interface"
        assert _symbol(symbols, "Point")["kind"] == "struct"
        assert _symbol(symbols, "Color")["kind"] == "enum"
        assert _symbol(symbols, "init")["kind"] == "constructor"
        assert _symbol(symbols, "speak")["kind"] == "method"

        name_prop = _symbol(symbols, "name")
        age_prop = _symbol(symbols, "age")
        assert name_prop["kind"] == "property"
        assert name_prop["visibility"] == "internal"
        assert name_prop["default_value"] == "x"
        assert age_prop["kind"] == "property"
        assert age_prop["visibility"] == "private"
        assert age_prop["default_value"] == "5"

    def test_swift_inheritance_reference_class_and_struct(self):
        source = (
            "protocol Walkable {}\n"
            "class Base {}\n"
            "class Person: Base, Walkable {}\n"
            "struct Point: Walkable {}\n"
            "protocol Named: Walkable {}\n"
        )

        _, refs = _parse_and_extract(source, "swift", "types.swift")

        inherits = {(r["source_name"], r["target_name"]) for r in refs if r["kind"] == "inherits"}
        implements = {(r["source_name"], r["target_name"]) for r in refs if r["kind"] == "implements"}

        assert ("Person", "Base") in inherits
        assert ("Person", "Walkable") in implements
        assert ("Point", "Walkable") in implements
        assert ("Named", "Walkable") in inherits

