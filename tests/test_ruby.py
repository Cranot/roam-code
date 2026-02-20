"""Ruby language extractor tests.

Verifies that the RubyExtractor correctly extracts symbols (classes, modules,
methods, singleton methods, constants, top-level functions) and references
(method calls, require/require_relative, include/extend, ClassName.new).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Helper: parse source text using tree-sitter + Ruby extractor
# ---------------------------------------------------------------------------

def _parse_and_extract(source_text: str, file_path: str = "test.rb"):
    """Parse Ruby source and extract symbols + references.

    Returns (symbols, references) lists.
    """
    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor
    from tree_sitter_language_pack import get_parser

    language = "ruby"
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


# ===========================================================================
# SYMBOL EXTRACTION TESTS
# ===========================================================================

class TestRubySymbolExtraction:
    """Tests for Ruby symbol extraction."""

    def test_extract_class(self):
        """Class definition should be extracted with kind='class'."""
        source = (
            "class MyClass\n"
            "  def initialize\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        class_syms = [s for s in symbols if s["kind"] == "class"]
        assert len(class_syms) == 1
        assert class_syms[0]["name"] == "MyClass"
        assert class_syms[0]["is_exported"] is True
        assert "class MyClass" in class_syms[0]["signature"]

    def test_extract_module(self):
        """Module definition should be extracted with kind='module'."""
        source = (
            "module Utilities\n"
            "  def self.helper\n"
            "    42\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        mod_syms = [s for s in symbols if s["kind"] == "module"]
        assert len(mod_syms) == 1
        assert mod_syms[0]["name"] == "Utilities"
        assert mod_syms[0]["signature"] == "module Utilities"

    def test_extract_method(self):
        """Instance method inside a class should be extracted with kind='method'."""
        source = (
            "class Dog\n"
            "  def bark\n"
            "    puts 'Woof!'\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 1
        assert methods[0]["name"] == "bark"
        assert methods[0]["parent_name"] == "Dog"

    def test_extract_singleton_method(self):
        """Singleton method (def self.xxx) should be extracted as method."""
        source = (
            "class Factory\n"
            "  def self.create(name)\n"
            "    new(name)\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 1
        assert methods[0]["name"] == "create"
        assert methods[0]["is_exported"] is True
        assert "self.create" in methods[0]["signature"]

    def test_extract_top_level_function(self):
        """Top-level def (no enclosing class/module) should be kind='function'."""
        source = (
            "def greet(name)\n"
            "  puts \"Hello, #{name}\"\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        funcs = [s for s in symbols if s["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "greet"
        assert funcs[0]["parent_name"] is None

    def test_extract_constant(self):
        """CONSTANT = value assignment should be extracted as kind='constant'."""
        source = (
            "class Config\n"
            "  MAX_RETRIES = 5\n"
            "  TIMEOUT = 30\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        consts = [s for s in symbols if s["kind"] == "constant"]
        names = [c["name"] for c in consts]
        assert "MAX_RETRIES" in names
        assert "TIMEOUT" in names

    def test_method_has_parent(self):
        """Methods inside a class should have parent_name set to the class."""
        source = (
            "class Calculator\n"
            "  def add(a, b)\n"
            "    a + b\n"
            "  end\n"
            "\n"
            "  def subtract(a, b)\n"
            "    a - b\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 2
        for m in methods:
            assert m["parent_name"] == "Calculator"

    def test_exported_detection(self):
        """Public methods should be marked as exported."""
        source = (
            "class Service\n"
            "  def process(data)\n"
            "    transform(data)\n"
            "  end\n"
            "\n"
            "  def self.build\n"
            "    new\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 2
        for m in methods:
            assert m["is_exported"] is True

    def test_signature_extraction(self):
        """Method parameters should be captured in the signature."""
        source = (
            "class User\n"
            "  def initialize(name, email, age = 0)\n"
            "    @name = name\n"
            "    @email = email\n"
            "    @age = age\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        init_sym = next(s for s in symbols if s["name"] == "initialize")
        assert "name" in init_sym["signature"]
        assert "email" in init_sym["signature"]
        assert "age" in init_sym["signature"]

    def test_multiline_class(self):
        """Class spanning multiple lines should be correctly extracted."""
        source = (
            "class LargeClass < BaseClass\n"
            "  LIMIT = 100\n"
            "\n"
            "  def initialize(x)\n"
            "    @x = x\n"
            "  end\n"
            "\n"
            "  def compute\n"
            "    @x * 2\n"
            "  end\n"
            "\n"
            "  def self.create(x)\n"
            "    new(x)\n"
            "  end\n"
            "end\n"
        )
        symbols, _ = _parse_and_extract(source)
        cls = next(s for s in symbols if s["kind"] == "class")
        assert cls["name"] == "LargeClass"
        assert cls["line_start"] == 1
        assert cls["line_end"] == 15
        # Should have 2 instance methods + 1 singleton method + 1 constant
        methods = [s for s in symbols if s["kind"] == "method"]
        assert len(methods) == 3  # initialize, compute, create
        consts = [s for s in symbols if s["kind"] == "constant"]
        assert len(consts) == 1


# ===========================================================================
# REFERENCE EXTRACTION TESTS
# ===========================================================================

class TestRubyReferenceExtraction:
    """Tests for Ruby reference extraction."""

    def test_reference_method_call(self):
        """obj.method_name should be detected as a call reference."""
        source = (
            "class Runner\n"
            "  def run\n"
            "    data = fetch_data\n"
            "    data.process\n"
            "  end\n"
            "end\n"
        )
        _, refs = _parse_and_extract(source)
        call_refs = [r for r in refs if r["kind"] == "call"]
        targets = [r["target_name"] for r in call_refs]
        assert "process" in targets

    def test_reference_require(self):
        """require 'lib' should be detected as an import reference."""
        source = (
            "require 'json'\n"
            "require 'net/http'\n"
        )
        _, refs = _parse_and_extract(source)
        import_refs = [r for r in refs if r["kind"] == "import"]
        targets = [r["target_name"] for r in import_refs]
        assert "json" in targets
        assert "http" in targets  # last segment of 'net/http'

    def test_reference_require_relative(self):
        """require_relative 'path' should be detected as an import reference."""
        source = (
            "require_relative 'helpers/string_utils'\n"
        )
        _, refs = _parse_and_extract(source)
        import_refs = [r for r in refs if r["kind"] == "import"]
        assert len(import_refs) >= 1
        target = import_refs[0]
        assert target["target_name"] == "string_utils"
        assert target["import_path"] == "helpers/string_utils"

    def test_reference_include(self):
        """include ModuleName should be detected as an import reference."""
        source = (
            "class MyClass\n"
            "  include Enumerable\n"
            "  include Comparable\n"
            "end\n"
        )
        _, refs = _parse_and_extract(source)
        import_refs = [r for r in refs if r["kind"] == "import"]
        targets = [r["target_name"] for r in import_refs]
        assert "Enumerable" in targets
        assert "Comparable" in targets

    def test_reference_class_new(self):
        """ClassName.new should be detected as a call to the class."""
        source = (
            "class Builder\n"
            "  def build\n"
            "    Widget.new('large')\n"
            "  end\n"
            "end\n"
        )
        _, refs = _parse_and_extract(source)
        call_refs = [r for r in refs if r["kind"] == "call"]
        targets = [r["target_name"] for r in call_refs]
        assert "Widget" in targets
