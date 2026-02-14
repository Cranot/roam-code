"""Pluggable test naming convention adapters.

Each adapter maps source files → expected test file paths and vice versa,
based on language/framework conventions.

Supported conventions:
  - Python: test_*.py / *_test.py in tests/ or same directory
  - Go: *_test.go colocated with source
  - JavaScript/TypeScript: *.test.{js,ts,jsx,tsx} / *.spec.{js,ts,jsx,tsx}
  - Java: src/test/java mirrors src/main/java, *Test.java
  - Rust: inline #[test] mod + tests/ directory
  - Ruby: spec/*_spec.rb mirrors lib/
  - Salesforce Apex: *Test.cls, *_Test.cls
"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod


class TestConvention(ABC):
    """Base class for test naming conventions."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Convention name (e.g. 'python', 'go', 'java-maven')."""

    @property
    @abstractmethod
    def languages(self) -> frozenset[str]:
        """Languages this convention applies to."""

    @abstractmethod
    def source_to_test_paths(self, source_path: str) -> list[str]:
        """Given a source file, return candidate test file paths."""

    @abstractmethod
    def test_to_source_paths(self, test_path: str) -> list[str]:
        """Given a test file, return candidate source file paths."""

    @abstractmethod
    def is_test_file(self, path: str) -> bool:
        """Check if a path is a test file under this convention."""


class PythonConvention(TestConvention):
    @property
    def name(self): return "python"

    @property
    def languages(self): return frozenset({"python"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        base = os.path.basename(p)
        name = os.path.splitext(base)[0]
        dir_part = os.path.dirname(p)

        candidates = []
        # test_<name>.py in tests/ directory
        candidates.append(f"tests/test_{name}.py")
        # test_<name>.py colocated
        if dir_part:
            candidates.append(f"{dir_part}/test_{name}.py")
        else:
            candidates.append(f"test_{name}.py")
        # <name>_test.py colocated
        if dir_part:
            candidates.append(f"{dir_part}/{name}_test.py")
        else:
            candidates.append(f"{name}_test.py")
        # tests/ subdir matching source layout
        if dir_part:
            candidates.append(f"tests/{dir_part}/test_{name}.py")
        return candidates

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        base = os.path.basename(p)
        name = os.path.splitext(base)[0]
        dir_part = os.path.dirname(p)

        candidates = []
        # test_foo.py -> foo.py
        if name.startswith("test_"):
            src_name = name[5:]
        elif name.endswith("_test"):
            src_name = name[:-5]
        else:
            return []

        candidates.append(f"src/{src_name}.py")
        candidates.append(f"{src_name}.py")
        if dir_part and dir_part != "tests":
            candidates.append(f"{dir_part}/{src_name}.py")
        # Strip tests/ prefix to find source
        if dir_part.startswith("tests/"):
            src_dir = dir_part[6:]
            candidates.append(f"{src_dir}/{src_name}.py")
            candidates.append(f"src/{src_dir}/{src_name}.py")
        return candidates

    def is_test_file(self, path):
        base = os.path.basename(path)
        return (base.startswith("test_") and base.endswith(".py")) or \
               (base.endswith("_test.py")) or \
               (base == "conftest.py")


class GoConvention(TestConvention):
    @property
    def name(self): return "go"

    @property
    def languages(self): return frozenset({"go"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        if p.endswith("_test.go"):
            return []
        name = os.path.splitext(p)[0]
        return [f"{name}_test.go"]

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        if not p.endswith("_test.go"):
            return []
        return [p.replace("_test.go", ".go")]

    def is_test_file(self, path):
        return path.replace("\\", "/").endswith("_test.go")


class JavaScriptConvention(TestConvention):
    @property
    def name(self): return "javascript"

    @property
    def languages(self): return frozenset({"javascript", "typescript"})

    _TEST_PATTERN = re.compile(r"^.*\.(test|spec)\.[jt]sx?$")

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        base = os.path.basename(p)
        name, ext = os.path.splitext(base)
        if self._TEST_PATTERN.match(base):
            return []
        dir_part = os.path.dirname(p)
        candidates = []
        for suffix in [".test", ".spec"]:
            test_name = f"{name}{suffix}{ext}"
            if dir_part:
                candidates.append(f"{dir_part}/{test_name}")
                candidates.append(f"{dir_part}/__tests__/{test_name}")
            else:
                candidates.append(test_name)
                candidates.append(f"__tests__/{test_name}")
        return candidates

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        base = os.path.basename(p)
        m = re.match(r"^(.*)\.(test|spec)(\.[jt]sx?)$", base)
        if not m:
            return []
        name, _, ext = m.groups()
        dir_part = os.path.dirname(p)
        candidates = []
        src_name = f"{name}{ext}"
        if "__tests__" in dir_part:
            parent = dir_part.replace("__tests__/", "").replace("/__tests__", "")
            candidates.append(f"{parent}/{src_name}" if parent else src_name)
        if dir_part:
            candidates.append(f"{dir_part}/{src_name}")
        candidates.append(f"src/{src_name}")
        return candidates

    def is_test_file(self, path):
        return bool(self._TEST_PATTERN.match(os.path.basename(path)))


class JavaMavenConvention(TestConvention):
    @property
    def name(self): return "java-maven"

    @property
    def languages(self): return frozenset({"java"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        if "src/test/" in p:
            return []
        name = os.path.splitext(os.path.basename(p))[0]
        test_path = p.replace("src/main/", "src/test/")
        test_name = os.path.splitext(test_path)[0]
        return [f"{test_name}Test.java", f"{test_name}Tests.java"]

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        if "src/test/" not in p:
            return []
        base = os.path.splitext(os.path.basename(p))[0]
        if base.endswith("Tests"):
            src_name = base[:-5]
        elif base.endswith("Test"):
            src_name = base[:-4]
        else:
            return []
        src_path = p.replace("src/test/", "src/main/")
        src_dir = os.path.dirname(src_path)
        return [f"{src_dir}/{src_name}.java"]

    def is_test_file(self, path):
        base = os.path.basename(path)
        return (base.endswith("Test.java") or base.endswith("Tests.java")) and \
               "src/test/" in path.replace("\\", "/")


class RubyConvention(TestConvention):
    @property
    def name(self): return "ruby"

    @property
    def languages(self): return frozenset({"ruby"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        name = os.path.splitext(os.path.basename(p))[0]
        dir_part = os.path.dirname(p)
        spec_dir = dir_part.replace("lib/", "spec/") if "lib/" in dir_part else f"spec/{dir_part}"
        return [f"{spec_dir}/{name}_spec.rb"]

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        base = os.path.basename(p)
        if not base.endswith("_spec.rb"):
            return []
        name = base[:-8]  # remove _spec.rb
        dir_part = os.path.dirname(p)
        src_dir = dir_part.replace("spec/", "lib/") if "spec/" in dir_part else dir_part
        return [f"{src_dir}/{name}.rb"]

    def is_test_file(self, path):
        return path.replace("\\", "/").endswith("_spec.rb")


class ApexConvention(TestConvention):
    @property
    def name(self): return "apex"

    @property
    def languages(self): return frozenset({"apex"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        name = os.path.splitext(os.path.basename(p))[0]
        if name.endswith("Test") or name.endswith("_Test"):
            return []
        dir_part = os.path.dirname(p)
        prefix = f"{dir_part}/" if dir_part else ""
        return [f"{prefix}{name}Test.cls", f"{prefix}{name}_Test.cls"]

    def test_to_source_paths(self, test_path):
        p = test_path.replace("\\", "/")
        name = os.path.splitext(os.path.basename(p))[0]
        dir_part = os.path.dirname(p)
        prefix = f"{dir_part}/" if dir_part else ""
        if name.endswith("_Test"):
            return [f"{prefix}{name[:-5]}.cls"]
        if name.endswith("Test"):
            return [f"{prefix}{name[:-4]}.cls"]
        return []

    def is_test_file(self, path):
        base = os.path.splitext(os.path.basename(path))[0]
        return base.endswith("Test") or base.endswith("_Test")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ALL_CONVENTIONS: list[TestConvention] = [
    PythonConvention(),
    GoConvention(),
    JavaScriptConvention(),
    JavaMavenConvention(),
    RubyConvention(),
    ApexConvention(),
]


def get_conventions() -> list[TestConvention]:
    """Return all registered test conventions."""
    return list(_ALL_CONVENTIONS)


def get_convention_for_language(language: str) -> TestConvention | None:
    """Return the convention matching a language, or None."""
    for conv in _ALL_CONVENTIONS:
        if language in conv.languages:
            return conv
    return None


def find_test_candidates(source_path: str, language: str | None = None) -> list[str]:
    """Return candidate test file paths for a source file.

    If language is provided, uses only that convention.
    Otherwise tries all conventions and returns merged results.
    """
    if language:
        conv = get_convention_for_language(language)
        if conv:
            return conv.source_to_test_paths(source_path)
        return []

    candidates = []
    for conv in _ALL_CONVENTIONS:
        candidates.extend(conv.source_to_test_paths(source_path))
    return list(dict.fromkeys(candidates))  # dedupe preserving order


def find_source_candidates(test_path: str, language: str | None = None) -> list[str]:
    """Return candidate source file paths for a test file."""
    if language:
        conv = get_convention_for_language(language)
        if conv:
            return conv.test_to_source_paths(test_path)
        return []

    candidates = []
    for conv in _ALL_CONVENTIONS:
        candidates.extend(conv.test_to_source_paths(test_path))
    return list(dict.fromkeys(candidates))
