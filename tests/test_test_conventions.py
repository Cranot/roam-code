"""Tests for pluggable test naming convention adapters (Phase 3).

Covers:
- PythonConvention: source_to_test, test_to_source, is_test_file
- GoConvention: colocated *_test.go
- JavaScriptConvention: .test.js / .spec.tsx patterns
- JavaMavenConvention: src/main/ -> src/test/ mapping
- RubyConvention: lib/ -> spec/ mapping
- ApexConvention: Test / _Test suffix handling
- get_convention_for_language()
- find_test_candidates() and find_source_candidates()
"""
from __future__ import annotations

import pytest

from roam.index.test_conventions import (
    PythonConvention,
    GoConvention,
    JavaScriptConvention,
    JavaMavenConvention,
    RubyConvention,
    ApexConvention,
    TestConvention,
    get_convention_for_language,
    get_conventions,
    find_test_candidates,
    find_source_candidates,
)


# ---------------------------------------------------------------------------
# PythonConvention
# ---------------------------------------------------------------------------

class TestPythonConvention:
    def setup_method(self):
        self.conv = PythonConvention()

    def test_source_to_test_includes_tests_dir(self):
        paths = self.conv.source_to_test_paths("src/models.py")
        assert "tests/test_models.py" in paths

    def test_source_to_test_colocated(self):
        paths = self.conv.source_to_test_paths("src/models.py")
        assert "src/test_models.py" in paths

    def test_test_to_source_includes_models(self):
        paths = self.conv.test_to_source_paths("tests/test_models.py")
        assert any("models.py" in p for p in paths)

    def test_test_to_source_suffix_form(self):
        paths = self.conv.test_to_source_paths("models_test.py")
        assert any("models.py" in p for p in paths)

    def test_is_test_file_prefix(self):
        assert self.conv.is_test_file("test_foo.py") is True

    def test_is_test_file_suffix(self):
        assert self.conv.is_test_file("foo_test.py") is True

    def test_is_test_file_conftest(self):
        assert self.conv.is_test_file("conftest.py") is True

    def test_is_test_file_negative(self):
        assert self.conv.is_test_file("models.py") is False

    def test_name_property(self):
        assert self.conv.name == "python"

    def test_languages_property(self):
        assert "python" in self.conv.languages


# ---------------------------------------------------------------------------
# GoConvention
# ---------------------------------------------------------------------------

class TestGoConvention:
    def setup_method(self):
        self.conv = GoConvention()

    def test_source_to_test(self):
        paths = self.conv.source_to_test_paths("main.go")
        assert paths == ["main_test.go"]

    def test_source_to_test_with_dir(self):
        paths = self.conv.source_to_test_paths("pkg/handler.go")
        assert paths == ["pkg/handler_test.go"]

    def test_test_to_source(self):
        paths = self.conv.test_to_source_paths("main_test.go")
        assert paths == ["main.go"]

    def test_is_test_file_true(self):
        assert self.conv.is_test_file("main_test.go") is True

    def test_is_test_file_false(self):
        assert self.conv.is_test_file("main.go") is False

    def test_skip_already_test_file(self):
        """source_to_test_paths returns [] if input is already a test file."""
        assert self.conv.source_to_test_paths("main_test.go") == []


# ---------------------------------------------------------------------------
# JavaScriptConvention
# ---------------------------------------------------------------------------

class TestJavaScriptConvention:
    def setup_method(self):
        self.conv = JavaScriptConvention()

    def test_source_to_test_js(self):
        paths = self.conv.source_to_test_paths("src/utils.js")
        assert "src/utils.test.js" in paths
        assert "src/utils.spec.js" in paths

    def test_source_to_test_tsx(self):
        paths = self.conv.source_to_test_paths("src/App.tsx")
        assert "src/App.test.tsx" in paths
        assert "src/App.spec.tsx" in paths

    def test_is_test_file_test_js(self):
        assert self.conv.is_test_file("utils.test.js") is True

    def test_is_test_file_spec_tsx(self):
        assert self.conv.is_test_file("App.spec.tsx") is True

    def test_is_test_file_negative(self):
        assert self.conv.is_test_file("App.tsx") is False

    def test_test_to_source(self):
        paths = self.conv.test_to_source_paths("src/utils.test.js")
        assert any("utils.js" in p for p in paths)

    def test_languages(self):
        langs = self.conv.languages
        assert "javascript" in langs
        assert "typescript" in langs


# ---------------------------------------------------------------------------
# JavaMavenConvention
# ---------------------------------------------------------------------------

class TestJavaMavenConvention:
    def setup_method(self):
        self.conv = JavaMavenConvention()

    def test_source_to_test(self):
        paths = self.conv.source_to_test_paths("src/main/java/com/app/Foo.java")
        assert "src/test/java/com/app/FooTest.java" in paths

    def test_test_to_source(self):
        paths = self.conv.test_to_source_paths("src/test/java/com/app/FooTest.java")
        assert "src/main/java/com/app/Foo.java" in paths

    def test_is_test_file_true(self):
        assert self.conv.is_test_file("src/test/java/com/app/FooTest.java") is True

    def test_is_test_file_false_wrong_dir(self):
        assert self.conv.is_test_file("src/main/java/com/app/FooTest.java") is False


# ---------------------------------------------------------------------------
# RubyConvention
# ---------------------------------------------------------------------------

class TestRubyConvention:
    def setup_method(self):
        self.conv = RubyConvention()

    def test_source_to_test(self):
        paths = self.conv.source_to_test_paths("lib/models/user.rb")
        assert any("spec" in p and "user_spec.rb" in p for p in paths)

    def test_test_to_source(self):
        paths = self.conv.test_to_source_paths("spec/models/user_spec.rb")
        assert any("lib" in p and "user.rb" in p for p in paths)

    def test_is_test_file_true(self):
        assert self.conv.is_test_file("spec/user_spec.rb") is True

    def test_is_test_file_false(self):
        assert self.conv.is_test_file("lib/user.rb") is False


# ---------------------------------------------------------------------------
# ApexConvention
# ---------------------------------------------------------------------------

class TestApexConvention:
    def setup_method(self):
        self.conv = ApexConvention()

    def test_source_to_test_paths(self):
        paths = self.conv.source_to_test_paths("classes/Account.cls")
        assert "classes/AccountTest.cls" in paths
        assert "classes/Account_Test.cls" in paths

    def test_is_test_file_suffix(self):
        assert self.conv.is_test_file("AccountTest.cls") is True

    def test_is_test_file_underscore_suffix(self):
        assert self.conv.is_test_file("Account_Test.cls") is True

    def test_is_test_file_false(self):
        assert self.conv.is_test_file("Account.cls") is False

    def test_test_to_source(self):
        paths = self.conv.test_to_source_paths("classes/AccountTest.cls")
        assert "classes/Account.cls" in paths

    def test_test_to_source_underscore(self):
        paths = self.conv.test_to_source_paths("classes/Account_Test.cls")
        assert "classes/Account.cls" in paths

    def test_source_to_test_skips_test_files(self):
        assert self.conv.source_to_test_paths("classes/AccountTest.cls") == []


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

class TestConventionRegistry:
    def test_get_convention_for_python(self):
        conv = get_convention_for_language("python")
        assert conv is not None
        assert isinstance(conv, PythonConvention)

    def test_get_convention_for_go(self):
        conv = get_convention_for_language("go")
        assert conv is not None
        assert isinstance(conv, GoConvention)

    def test_get_convention_for_unknown(self):
        assert get_convention_for_language("fortran") is None

    def test_get_conventions_returns_all(self):
        convs = get_conventions()
        assert len(convs) >= 6

    def test_find_test_candidates_with_language(self):
        candidates = find_test_candidates("src/models.py", language="python")
        assert "tests/test_models.py" in candidates

    def test_find_test_candidates_without_language(self):
        """Without language, all conventions contribute candidates."""
        candidates = find_test_candidates("src/models.py")
        assert len(candidates) >= 1

    def test_find_source_candidates_with_language(self):
        candidates = find_source_candidates("tests/test_models.py", language="python")
        assert any("models.py" in c for c in candidates)

    def test_find_source_candidates_unknown_language(self):
        """Unknown language returns empty list."""
        assert find_source_candidates("test_foo.py", language="fortran") == []

    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            TestConvention()
