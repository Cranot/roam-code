"""Ruby extractor: visibility modifier tracking tests (Wave 5, #23).

Verifies that private/protected/public statements correctly set the
visibility field on extracted method symbols.

Falsification: each test fails against the original extractor
(which hard-codes visibility="public" everywhere).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse_and_extract(source_text: str, file_path: str = "test.rb"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor

    language = "ruby"
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _methods(symbols):
    return {s["name"]: s for s in symbols if s["kind"] in ("method", "function")}


# ---------------------------------------------------------------------------
# RB-V1: method before any modifier is public
# ---------------------------------------------------------------------------


class TestNoModifier:
    def test_no_modifier_is_public(self):
        """Negative control: method with no modifier stays public."""
        src = """\
class Foo
  def bar; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert "bar" in methods
        assert methods["bar"]["visibility"] == "public"
        assert methods["bar"]["is_exported"] is True


# ---------------------------------------------------------------------------
# RB-V2: bare `private` flips subsequent methods to private
# ---------------------------------------------------------------------------


class TestBarePrivate:
    def test_bare_private_marks_following_methods(self):
        """Bare `private` makes subsequent method definitions private."""
        src = """\
class Foo
  def pub_method; end

  private

  def priv_method; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert methods["pub_method"]["visibility"] == "public"
        assert methods["priv_method"]["visibility"] == "private"

    def test_private_method_is_not_exported(self):
        """`is_exported` is False for private methods."""
        src = """\
class Foo
  private
  def secret; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert methods["secret"]["is_exported"] is False

    def test_multiple_private_methods(self):
        """All methods after bare `private` are private."""
        src = """\
class Foo
  private
  def a; end
  def b; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert methods["a"]["visibility"] == "private"
        assert methods["b"]["visibility"] == "private"


# ---------------------------------------------------------------------------
# RB-V3: bare `protected`
# ---------------------------------------------------------------------------


class TestBareProtected:
    def test_bare_protected_marks_following_methods(self):
        """Bare `protected` makes subsequent methods protected."""
        src = """\
class Foo
  protected
  def guarded; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert methods["guarded"]["visibility"] == "protected"
        assert methods["guarded"]["is_exported"] is False


# ---------------------------------------------------------------------------
# RB-V4: `public` resets visibility
# ---------------------------------------------------------------------------


class TestPublicReset:
    def test_public_resets_after_private(self):
        """`public` after `private` resets visibility to public."""
        src = """\
class Foo
  private
  def hidden; end

  public
  def visible; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert methods["hidden"]["visibility"] == "private"
        assert methods["visible"]["visibility"] == "public"
        assert methods["visible"]["is_exported"] is True


# ---------------------------------------------------------------------------
# RB-V5: inline `private def foo; end` form (Ruby 2.1+)
# ---------------------------------------------------------------------------


class TestInlinePrivateDef:
    def test_inline_private_def(self):
        """`private def foo; end` marks foo as private."""
        src = """\
class Foo
  private def secret_inline; end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert "secret_inline" in methods
        assert methods["secret_inline"]["visibility"] == "private"


# ---------------------------------------------------------------------------
# RB-V6: visibility resets at class/module boundaries
# ---------------------------------------------------------------------------


class TestClassBoundaryReset:
    def test_inner_class_starts_public(self):
        """A nested class body starts at visibility=public regardless of outer scope."""
        src = """\
class Outer
  private

  class Inner
    def inner_pub; end
  end
end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        # inner_pub is in Inner, which has a fresh visibility scope
        assert "inner_pub" in methods
        assert methods["inner_pub"]["visibility"] == "public"


# ---------------------------------------------------------------------------
# RB-V7: conservation — existing public symbols still work
# ---------------------------------------------------------------------------


class TestConservation:
    def test_public_method_is_exported(self):
        """Public method has is_exported=True (conservation of old behaviour)."""
        src = """\
def standalone_fn; end
"""
        symbols, _ = _parse_and_extract(src)
        methods = _methods(symbols)
        assert "standalone_fn" in methods
        assert methods["standalone_fn"]["visibility"] == "public"
        assert methods["standalone_fn"]["is_exported"] is True

    def test_class_symbol_unaffected(self):
        """Class-level symbols are unaffected by visibility tracking on methods."""
        src = """\
class Bar
  private
  def hidden; end
end
"""
        symbols, _ = _parse_and_extract(src)
        class_syms = [s for s in symbols if s["kind"] == "class"]
        assert len(class_syms) == 1
        assert class_syms[0]["is_exported"] is True
