"""Tests for the Dart Tier-1 extractor."""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from roam.languages.registry import get_extractor, get_ts_language
from tree_sitter import Parser


@pytest.fixture(scope="module")
def parse():
    lang = get_ts_language("dart")
    parser = Parser(lang)

    def _parse(src: str):
        return parser.parse(src.encode("utf-8"))

    return _parse


@pytest.fixture(scope="module")
def extractor():
    return get_extractor("dart")


def _names_by_kind(symbols):
    by_kind: dict[str, list[str]] = {}
    for s in symbols:
        by_kind.setdefault(s["kind"], []).append(s["name"])
    return by_kind


def test_class_with_inheritance(parse, extractor):
    src = """
class Animal {
  void speak() {}
}
class Dog extends Animal {
  void bark() {}
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "a.dart")
    by_kind = _names_by_kind(symbols)
    assert "Animal" in by_kind.get("class", [])
    assert "Dog" in by_kind.get("class", [])
    assert "speak" in by_kind.get("method", [])
    assert "bark" in by_kind.get("method", [])


def test_mixin_classified_as_interface(parse, extractor):
    src = """
mixin Logger {
  void log(String message) {}
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "logger.dart")
    by_kind = _names_by_kind(symbols)
    assert "Logger" in by_kind.get("interface", []), f"Mixin should be interface, got {by_kind}"
    assert "log" in by_kind.get("method", [])


def test_extension_methods(parse, extractor):
    src = """
extension StringX on String {
  int doubleLen() => length * 2;
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "ext.dart")
    by_kind = _names_by_kind(symbols)
    assert "StringX" in by_kind.get("class", [])
    assert "doubleLen" in by_kind.get("method", [])


def test_getters_and_setters(parse, extractor):
    src = """
class Box {
  int _v = 0;
  int get value => _v;
  set value(int v) { _v = v; }
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "box.dart")
    by_kind = _names_by_kind(symbols)
    assert "value" in by_kind.get("getter", [])
    assert "value" in by_kind.get("setter", [])


def test_constructor(parse, extractor):
    src = """
class Point {
  final double x;
  final double y;
  Point(this.x, this.y);
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "point.dart")
    by_kind = _names_by_kind(symbols)
    assert "Point" in by_kind.get("class", [])
    assert "Point" in by_kind.get("constructor", [])


def test_enum(parse, extractor):
    src = """
enum Color { red, green, blue }
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "color.dart")
    by_kind = _names_by_kind(symbols)
    assert "Color" in by_kind.get("enum", [])


def test_typedef(parse, extractor):
    src = """
typedef IntList = List<int>;
typedef Callback = void Function(int);
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "td.dart")
    by_kind = _names_by_kind(symbols)
    assert "IntList" in by_kind.get("typealias", [])
    assert "Callback" in by_kind.get("typealias", [])


def test_top_level_function(parse, extractor):
    src = """
void main() {
  print('Hello');
}

int add(int a, int b) => a + b;
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "main.dart")
    by_kind = _names_by_kind(symbols)
    assert "main" in by_kind.get("function", [])
    assert "add" in by_kind.get("function", [])


def test_abstract_class(parse, extractor):
    src = """
abstract class Shape {
  double area();
  double perimeter();
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "shape.dart")
    by_kind = _names_by_kind(symbols)
    assert "Shape" in by_kind.get("class", [])
    methods = by_kind.get("method", [])
    assert "area" in methods
    assert "perimeter" in methods


def test_methods_inside_class_classified_as_method_not_function(parse, extractor):
    """Regression: top-level fn -> 'function'; method -> 'method'."""
    src = """
void topLevel() {}
class C {
  void inside() {}
}
"""
    symbols = extractor.extract_symbols(parse(src), src.encode(), "c.dart")
    by_kind = _names_by_kind(symbols)
    assert "topLevel" in by_kind.get("function", [])
    assert "inside" in by_kind.get("method", [])
    assert "topLevel" not in by_kind.get("method", [])
    assert "inside" not in by_kind.get("function", [])


def test_dart_extension_is_registered():
    """`.dart` files must resolve to language `dart`."""
    from roam.languages.registry import get_language_for_file

    assert get_language_for_file("lib/main.dart") == "dart"
    assert get_language_for_file("test/widget_test.dart") == "dart"


def test_dart_in_supported_languages():
    """Dart must be recognized as a Tier-1 language."""
    from roam.languages.registry import _create_extractor

    ext = _create_extractor("dart")
    assert ext is not None
    assert ext.language_name == "dart"
    assert ".dart" in ext.file_extensions
