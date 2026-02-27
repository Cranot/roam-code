"""Tests for the Scala Tier 1 extractor."""

from __future__ import annotations

import pytest

try:
    from tree_sitter_language_pack import get_parser

    _parser = get_parser("scala")
    HAS_SCALA = True
except Exception:
    HAS_SCALA = False

pytestmark = pytest.mark.skipif(not HAS_SCALA, reason="tree-sitter scala grammar unavailable")


def _parse(code: str):
    from roam.languages.scala_lang import ScalaExtractor

    ext = ScalaExtractor()
    source = code.encode()
    tree = _parser.parse(source)
    symbols = ext.extract_symbols(tree, source, "test.scala")
    refs = ext.extract_references(tree, source, "test.scala")
    return symbols, refs


def _sym_names(symbols, kind=None):
    if kind:
        return [s["name"] for s in symbols if s["kind"] == kind]
    return [s["name"] for s in symbols]


def _ref_names(refs, kind=None):
    if kind:
        return [r["target_name"] for r in refs if r["kind"] == kind]
    return [r["target_name"] for r in refs]


# ---- Package ----


class TestPackage:
    def test_package(self):
        symbols, _ = _parse("package com.example")
        assert "com.example" in _sym_names(symbols, "module")


# ---- Classes ----


class TestClasses:
    def test_basic_class(self):
        symbols, _ = _parse("class Foo")
        assert "Foo" in _sym_names(symbols, "class")

    def test_case_class(self):
        symbols, _ = _parse("case class Point(x: Int, y: Int)")
        names = _sym_names(symbols)
        assert "Point" in names
        # Case class params are implicitly val → properties
        assert "x" in names
        assert "y" in names

    def test_generic_class(self):
        symbols, _ = _parse("class Container[T](val item: T)")
        s = [s for s in symbols if s["name"] == "Container"][0]
        assert "[T]" in s["signature"]

    def test_abstract_class(self):
        symbols, _ = _parse("abstract class Animal(val name: String)")
        assert "Animal" in _sym_names(symbols, "class")
        assert "name" in _sym_names(symbols, "property")

    def test_sealed_class(self):
        symbols, _ = _parse("sealed class Base")
        s = [s for s in symbols if s["name"] == "Base"][0]
        assert "sealed" in s["signature"]

    def test_class_with_body(self):
        code = """class Dog {
          def bark(): String = "Woof"
          val age: Int = 5
        }"""
        symbols, _ = _parse(code)
        assert "Dog" in _sym_names(symbols, "class")
        assert "bark" in _sym_names(symbols, "method")
        assert "age" in _sym_names(symbols, "property")

    def test_class_val_param(self):
        symbols, _ = _parse("class Cat(val name: String, age: Int)")
        props = _sym_names(symbols, "property")
        # val name should be property, age without val should not
        assert "name" in props
        assert "age" not in props

    def test_nested_class(self):
        code = """class Outer {
          class Inner {
            def foo(): Unit = {}
          }
        }"""
        symbols, _ = _parse(code)
        inner = [s for s in symbols if s["name"] == "Inner"][0]
        assert inner["qualified_name"] == "Outer.Inner"
        foo = [s for s in symbols if s["name"] == "foo"][0]
        assert foo["qualified_name"] == "Outer.Inner.foo"


# ---- Traits ----


class TestTraits:
    def test_trait(self):
        code = """trait Logger {
          def log(msg: String): Unit
        }"""
        symbols, _ = _parse(code)
        assert "Logger" in _sym_names(symbols, "interface")
        assert "log" in _sym_names(symbols, "method")

    def test_sealed_trait(self):
        symbols, _ = _parse("sealed trait Shape")
        s = [s for s in symbols if s["name"] == "Shape"][0]
        assert s["kind"] == "interface"
        assert "sealed" in s["signature"]


# ---- Objects ----


class TestObjects:
    def test_object(self):
        code = """object Factory {
          def create(): Unit = {}
          val DEFAULT: String = "x"
        }"""
        symbols, _ = _parse(code)
        assert "Factory" in _sym_names(symbols, "class")
        assert "create" in _sym_names(symbols, "method")
        assert "DEFAULT" in _sym_names(symbols, "property")

    def test_case_object(self):
        symbols, _ = _parse("case object Red")
        s = [s for s in symbols if s["name"] == "Red"][0]
        assert "case object" in s["signature"]


# ---- Functions ----


class TestFunctions:
    def test_top_level_function(self):
        symbols, _ = _parse("def greet(name: String): String = name")
        s = [s for s in symbols if s["name"] == "greet"][0]
        assert s["kind"] == "function"
        assert "(name: String)" in s["signature"]
        assert ": String" in s["signature"]

    def test_generic_function(self):
        symbols, _ = _parse("def transform[A, B](f: A => B): B = f(???)")
        s = [s for s in symbols if s["name"] == "transform"][0]
        assert "[A, B]" in s["signature"]

    def test_method_in_class(self):
        code = """class Svc {
          def run(): Unit = {}
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "run"][0]
        assert s["kind"] == "method"
        assert s["parent_name"] == "Svc"

    def test_abstract_method(self):
        code = """trait Animal {
          def speak(): String
        }"""
        symbols, _ = _parse(code)
        assert "speak" in _sym_names(symbols, "method")

    def test_private_method(self):
        code = """class Foo {
          private def helper(): Unit = {}
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "helper"][0]
        assert s["visibility"] == "private"

    def test_override_method(self):
        code = """class Bar {
          override def toString(): String = "bar"
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "toString"][0]
        assert "override" in s["signature"]


# ---- Vals / Vars ----


class TestVals:
    def test_top_level_val(self):
        symbols, _ = _parse('val VERSION: String = "1.0"')
        s = [s for s in symbols if s["name"] == "VERSION"][0]
        assert s["kind"] == "constant"

    def test_top_level_var(self):
        symbols, _ = _parse("var counter: Int = 0")
        s = [s for s in symbols if s["name"] == "counter"][0]
        assert s["kind"] == "variable"

    def test_val_in_class(self):
        code = """class Cfg {
          val timeout: Int = 30
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "timeout"][0]
        assert s["kind"] == "property"
        assert s["parent_name"] == "Cfg"


# ---- Type aliases ----


class TestTypeAliases:
    def test_type_alias(self):
        symbols, _ = _parse("type StringMap = Map[String, String]")
        s = [s for s in symbols if s["name"] == "StringMap"][0]
        assert s["kind"] == "type_alias"


# ---- Inheritance ----


class TestInheritance:
    def test_extends(self):
        _, refs = _parse("class Dog extends Animal")
        inherits = _ref_names(refs, "inherits")
        assert "Animal" in inherits

    def test_extends_with_trait(self):
        code = "class Dog extends Animal with Logger with Serializable"
        _, refs = _parse(code)
        inherits = _ref_names(refs, "inherits")
        implements = _ref_names(refs, "implements")
        assert "Animal" in inherits
        assert "Logger" in implements
        assert "Serializable" in implements

    def test_trait_extends_trait(self):
        _, refs = _parse("trait SpecialLogger extends Logger")
        inherits = _ref_names(refs, "inherits")
        assert "Logger" in inherits

    def test_generic_extends(self):
        _, refs = _parse("class MyList extends Comparable[Int]")
        inherits = _ref_names(refs, "inherits")
        assert "Comparable" in inherits

    def test_generic_with_trait(self):
        _, refs = _parse("class Foo extends Bar[String] with Ordered[Int]")
        inherits = _ref_names(refs, "inherits")
        implements = _ref_names(refs, "implements")
        assert "Bar" in inherits
        assert "Ordered" in implements

    def test_case_class_extends(self):
        _, refs = _parse("case class Circle(r: Double) extends Shape")
        inherits = _ref_names(refs, "inherits")
        assert "Shape" in inherits


# ---- Imports ----


class TestImports:
    def test_simple_import(self):
        _, refs = _parse("import scala.collection.mutable")
        imports = _ref_names(refs, "import")
        assert "mutable" in imports

    def test_grouped_import(self):
        _, refs = _parse("import java.util.{List, Map}")
        imports = _ref_names(refs, "import")
        assert "List" in imports
        assert "Map" in imports

    def test_import_path(self):
        _, refs = _parse("import scala.collection.mutable")
        imp = [r for r in refs if r["kind"] == "import"][0]
        assert imp["import_path"] == "scala.collection.mutable"


# ---- Calls ----


class TestCalls:
    def test_function_call(self):
        code = """object Main {
          def main(): Unit = {
            println("hello")
          }
        }"""
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "println" in calls

    def test_new_expression(self):
        code = """object Factory {
          def create(): Unit = {
            val d = new Dog("Rex", 3)
          }
        }"""
        _, refs = _parse(code)
        calls = _ref_names(refs, "call")
        assert "Dog" in calls


# ---- Visibility ----


class TestVisibility:
    def test_public_by_default(self):
        symbols, _ = _parse("class Foo")
        s = [s for s in symbols if s["name"] == "Foo"][0]
        assert s["visibility"] == "public"
        assert s["is_exported"] is True

    def test_private(self):
        code = """class Outer {
          private class Inner
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "Inner"][0]
        assert s["visibility"] == "private"
        assert s["is_exported"] is False

    def test_protected(self):
        code = """class Base {
          protected def helper(): Unit = {}
        }"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "helper"][0]
        assert s["visibility"] == "protected"


# ---- Docstrings ----


class TestDocstrings:
    def test_scaladoc(self):
        code = """/** The main entry point. */
class App"""
        symbols, _ = _parse(code)
        s = [s for s in symbols if s["name"] == "App"][0]
        assert s["docstring"] is not None
        assert "main entry point" in s["docstring"]


# ---- Integration: registry ----


class TestRegistry:
    def test_scala_has_dedicated_extractor(self):
        from roam.languages.registry import _DEDICATED_EXTRACTORS, get_extractor

        assert "scala" in _DEDICATED_EXTRACTORS
        ext = get_extractor("scala")
        assert ext.__class__.__name__ == "ScalaExtractor"

    def test_file_extension_mapping(self):
        from roam.languages.registry import get_language_for_file

        assert get_language_for_file("Main.scala") == "scala"
        assert get_language_for_file("build.sc") == "scala"
