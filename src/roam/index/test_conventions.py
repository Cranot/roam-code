"""Pluggable test naming convention adapters + canonical test-detection facade.

This module is the **single source of truth** for "is this a test file" and
"what kind of test is it" detection across roam-code. Before W6.2 there
were two parallel implementations: this file's per-language adapters and
``file_roles._TEST_PATTERNS`` (a flat global regex list). Consumer commands
called whichever happened to be imported, producing inconsistent
classifications (Pattern 4 from `the dogfood synthesis notes`,
mirror of the naming-conventions divergence Fix G addressed).

Two-layer API
-------------

* **Canonical facade** (module-level functions — **use these from CLI
  commands and consumer code**): ``is_test_file``, ``classify_test_kind``,
  ``source_for_test``, ``language_for_test``. These dispatch to the
  appropriate per-language adapter based on the path's extension so the
  same input always returns the same classification regardless of which
  consumer asks. **This is the canonical entry point — every command
  that classifies a test file MUST go through here.**

* **Adapter layer** (one class per convention): ``PythonConvention``,
  ``GoConvention``, ``JavaScriptConvention``, ``JavaMavenConvention``,
  ``RubyConvention``, ``ApexConvention``, ``CSharpConvention``. Each
  exposes ``is_test_file``, ``classify_kind``, ``source_to_test_paths``,
  and ``test_to_source_paths``. **Do not call these directly from
  consumer commands** — use the facade instead. The adapters are
  implementation details that the facade dispatches to; calling them
  directly bypasses the cross-language fallback rules and re-introduces
  Pattern 4 (conventions detector inconsistency).

  Exception: ``find_test_candidates`` / ``find_source_candidates`` /
  ``get_conventions`` / ``get_convention_for_language`` legitimately need
  to enumerate / pick a specific adapter (e.g. for source ↔ test path
  mapping, which is intrinsically language-specific). Those helpers are
  part of the public facade.

Facade ↔ adapter parity guarantee (W12.x)
-----------------------------------------

For every path whose extension belongs to a registered adapter, the
facade and that adapter return the **same** classification. The
``test_test_detection_consolidation.py`` parity suite pins this. The
adapter's ``classify_kind`` is treated as the canonical implementation
for its language (e.g. Vitest's "colocated spec defaults to unit"
convention lives on ``JavaScriptConvention`` and the facade delegates to
it for ``.js/.ts/.jsx/.tsx/.mjs/.cjs/.mts/.cts/.vue`` paths). For
extensions without a registered adapter (Kotlin, Scala, Elixir, Dart,
PHP, Swift, Rust, ...), the facade applies the cross-language fallback
rules in ``_TEST_KIND_PATH_PATTERNS`` / ``_TEST_KIND_NAME_PATTERNS``.

The facade consults the adapter list in registry order and falls back to
``DEFAULT_TEST_PATTERNS`` (a flat regex list covering ~16 conventions —
Python, Go, JS/TS/Vitest/Vue, Java, Kotlin, C#, Ruby, PHP, Scala, Elixir,
Dart, Apex, Rust, Swift) for "any language" callers that don't know the
language ahead of time.

Supported conventions
---------------------
  - Python: test_*.py / *_test.py in tests/ or same directory
  - Go: *_test.go colocated with source
  - JavaScript/TypeScript/Vitest: *.test.{js,ts,jsx,tsx,vue} /
    *.spec.{js,ts,jsx,tsx,vue} (Vitest also covers Vue SFC tests)
  - Java: src/test/java mirrors src/main/java, *Test.java
  - Rust: inline #[test] mod + tests/ directory
  - Ruby: spec/*_spec.rb mirrors lib/
  - Salesforce Apex: *Test.cls, *_Test.cls
  - C#: separate test projects (*.Tests, *.UnitTests, etc.)
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

# Test-kind labels — kept in sync with file_roles.TEST_KIND_*
KIND_UNIT = "unit"
KIND_INTEGRATION = "integration"
KIND_E2E = "e2e"
KIND_SMOKE = "smoke"
KIND_UNKNOWN = "unknown"


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

    # ------------------------------------------------------------------
    # Default implementations for kind classification + reverse helper.
    # Subclasses may override classify_kind for framework-specific logic
    # (e.g. Vitest colocation defaults to unit).
    # ------------------------------------------------------------------

    def classify_kind(self, path: str) -> str:
        """Classify a test file as ``unit``, ``integration``, ``e2e``, or
        ``unknown``. Default implementation is path-pattern based.

        Subclasses with framework-specific colocation conventions (e.g.
        Vitest, which defaults colocated specs to ``unit``) should
        override.
        """
        if not self.is_test_file(path):
            return KIND_UNKNOWN
        p = path.replace("\\", "/").lower()
        base = os.path.basename(p)
        # Directory-based: most specific first.
        if re.search(r"(^|/)(e2e|end-to-end|cypress|playwright|selenium)/", p):
            return KIND_E2E
        if re.search(r"(^|/)(integration|integ)/", p):
            return KIND_INTEGRATION
        if re.search(r"(^|/)unit/", p):
            return KIND_UNIT
        # Name-based: .e2e. / .integration. / .unit. infixes.
        if re.search(r"(?:^|[._-])(e2e|end[._-]to[._-]end)(?:[._-]|$)", base):
            return KIND_E2E
        if re.search(r"(?:^|[._-])(integration|integ)(?:[._-]|$)", base):
            return KIND_INTEGRATION
        if re.search(r"(?:^|[._-])unit(?:[._-]|$)", base):
            return KIND_UNIT
        return KIND_UNKNOWN

    def source_for_test(self, test_path: str) -> Optional[str]:
        """Return the first source-file candidate for a test, or None."""
        cands = self.test_to_source_paths(test_path)
        return cands[0] if cands else None


class PythonConvention(TestConvention):
    @property
    def name(self):
        return "python"

    @property
    def languages(self):
        return frozenset({"python"})

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
        return (
            (base.startswith("test_") and base.endswith(".py"))
            or (base.endswith("_test.py"))
            or (base == "conftest.py")
        )


class GoConvention(TestConvention):
    @property
    def name(self):
        return "go"

    @property
    def languages(self):
        return frozenset({"go"})

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
    """JavaScript / TypeScript / Vitest test convention.

    Covers Jest, Vitest, Mocha, Cypress, and Playwright naming. Vitest
    adds two things over the classic Jest pattern that matter here:

    * ``.test.vue`` / ``.spec.vue`` for Vue SFC component tests.
    * Colocated specs default to ``unit`` (e.g.
      ``src/composables/useFoo.test.ts`` is a unit test even though no
      ``unit/`` directory is in the path).

    The base ``classify_kind`` only recognises ``unit/`` / ``integration/``
    / ``e2e/`` directories; the override below treats colocated and
    ``__tests__/`` specs as unit and detects ``tests/integration/``,
    ``tests/e2e/``, ``playwright/``, ``cypress/``, ``tests/smoke/`` as
    their respective kinds.

    The module-level facade ``classify_test_kind`` delegates to this
    override for every JS/TS/Vue path so consumers using either entry
    point get the same answer (W12.x parity fix). The override therefore
    must be a strict **superset** of the cross-language fallback —
    every directory / name pattern recognised by the facade must also
    be recognised here, OR the facade-delegates-to-adapter path will
    silently downgrade a classification.
    """

    @property
    def name(self):
        return "javascript"

    @property
    def languages(self):
        return frozenset({"javascript", "typescript", "vue"})

    # File extension alternation matches: .js / .jsx / .ts / .tsx / .mjs /
    # .cjs / .mts / .cts / .vue. Order doesn't matter inside the group.
    _TEST_PATTERN = re.compile(r"^.*\.(?:test|spec)\.(?:[jt]sx?|[mc][jt]s|vue)$")
    _TEST_PARSE_PATTERN = re.compile(r"^(.*)\.(test|spec)(\.(?:[jt]sx?|[mc][jt]s|vue))$")

    # Directory hints for kind classification (Vitest-aware). Mirrors the
    # facade's ``_TEST_KIND_PATH_PATTERNS`` so the adapter is a strict
    # superset of the cross-language fallback for JS/TS/Vue paths — the
    # facade can therefore delegate to this adapter without losing the
    # ``smoke`` / ``sanity`` classification.
    _E2E_DIR_RE = re.compile(r"(^|/)(e2e|end-to-end|cypress|playwright|selenium)/", re.IGNORECASE)
    _INTEGRATION_DIR_RE = re.compile(r"(^|/)(integration|integ)/", re.IGNORECASE)
    _SMOKE_DIR_RE = re.compile(r"(^|/)(smoke|sanity)/", re.IGNORECASE)
    _E2E_NAME_RE = re.compile(r"(?:^|[._-])(e2e|end[._-]to[._-]end)(?:[._-]|$)", re.IGNORECASE)
    _INTEGRATION_NAME_RE = re.compile(r"(?:^|[._-])(integration|integ)(?:[._-]|$)", re.IGNORECASE)
    _SMOKE_NAME_RE = re.compile(r"(?:^|[._-])(smoke|sanity)(?:[._-]|$)", re.IGNORECASE)

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
        m = self._TEST_PARSE_PATTERN.match(base)
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
        return bool(self._TEST_PATTERN.match(os.path.basename(str(path))))

    def classify_kind(self, path, all_files=None) -> str:  # noqa: ARG002 — accepted for facade-parity signature
        """Vitest-aware kind classification.

        Rules (most specific first):

        1. ``tests/e2e/``, ``e2e/``, ``cypress/``, ``playwright/``,
           ``selenium/`` → ``e2e``.
        2. ``tests/integration/``, ``integration/``, ``integ/`` → ``integration``.
        3. ``tests/smoke/``, ``smoke/``, ``sanity/`` → ``smoke``.
        4. ``.e2e.`` infix in filename → ``e2e``.
        5. ``.integration.`` infix in filename → ``integration``.
        6. ``.smoke.`` / ``.sanity.`` infix in filename → ``smoke``.
        7. Otherwise: ``unit`` (Vitest convention — colocated and
           ``__tests__/`` specs are unit tests by default).

        The ``all_files`` parameter is accepted (and ignored) so the
        signature matches the module-level facade ``classify_test_kind``
        — the facade can delegate to this method without an adapter shim.
        """
        p = str(path).replace("\\", "/")
        if not self.is_test_file(p):
            return KIND_UNKNOWN
        base = os.path.basename(p)
        if self._E2E_DIR_RE.search(p):
            return KIND_E2E
        if self._INTEGRATION_DIR_RE.search(p):
            return KIND_INTEGRATION
        if self._SMOKE_DIR_RE.search(p):
            return KIND_SMOKE
        if self._E2E_NAME_RE.search(base):
            return KIND_E2E
        if self._INTEGRATION_NAME_RE.search(base):
            return KIND_INTEGRATION
        if self._SMOKE_NAME_RE.search(base):
            return KIND_SMOKE
        # Vitest default: colocated / __tests__/ / generic tests/ dir
        # specs are unit tests.
        return KIND_UNIT


class JavaMavenConvention(TestConvention):
    @property
    def name(self):
        return "java-maven"

    @property
    def languages(self):
        return frozenset({"java"})

    def source_to_test_paths(self, source_path):
        p = source_path.replace("\\", "/")
        if "src/test/" in p:
            return []
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
        return (base.endswith("Test.java") or base.endswith("Tests.java")) and "src/test/" in path.replace("\\", "/")


class RubyConvention(TestConvention):
    @property
    def name(self):
        return "ruby"

    @property
    def languages(self):
        return frozenset({"ruby"})

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
    def name(self):
        return "apex"

    @property
    def languages(self):
        return frozenset({"apex"})

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


class CSharpConvention(TestConvention):
    @property
    def name(self):
        return "csharp"

    @property
    def languages(self):
        return frozenset({"c_sharp", "csharp", "c#"})

    def source_to_test_paths(self, source_path):
        """Map C# source files to potential test file paths.

        Common patterns:
        - src/MyProject/Services/UserService.cs -> tests/MyProject.Tests/Services/UserServiceTests.cs
        - src/MyProject/UserService.cs -> tests/MyProject.Tests/UserServiceTest.cs
        - MyProject/UserService.cs -> MyProject.Tests/UserServiceTests.cs
        """
        p = source_path.replace("\\", "/")
        base = os.path.basename(p)
        name = os.path.splitext(base)[0]

        # skip if already a test file
        if name.endswith("Test") or name.endswith("Tests"):
            return []

        candidates = []
        dir_part = os.path.dirname(p)

        # extract the relative path within the project
        # handle patterns like: src/ProjectName/Subdir/File.cs
        parts = dir_part.split("/") if dir_part else []

        # try to find project name (usually after src/ or first directory)
        project_name = None
        relative_subdir = ""

        if "src" in parts:
            src_idx = parts.index("src")
            if src_idx + 1 < len(parts):
                project_name = parts[src_idx + 1]
                if src_idx + 2 < len(parts):
                    relative_subdir = "/".join(parts[src_idx + 2 :])
        elif len(parts) > 0:
            # no src/ dir, first part might be project name
            project_name = parts[0]
            if len(parts) > 1:
                relative_subdir = "/".join(parts[1:])

        # generate test file candidates
        for test_suffix in ["Tests", "Test"]:
            test_name = f"{name}{test_suffix}.cs"

            if project_name:
                # pattern: tests/ProjectName.Tests/Subdir/FileTests.cs
                for test_project_suffix in [".Tests", ".UnitTests", ".IntegrationTests"]:
                    test_project = f"{project_name}{test_project_suffix}"
                    if relative_subdir:
                        candidates.append(f"tests/{test_project}/{relative_subdir}/{test_name}")
                        candidates.append(f"test/{test_project}/{relative_subdir}/{test_name}")
                    else:
                        candidates.append(f"tests/{test_project}/{test_name}")
                        candidates.append(f"test/{test_project}/{test_name}")

                # pattern: ProjectName.Tests/Subdir/FileTests.cs (sibling test project)
                for test_project_suffix in [".Tests", ".UnitTests", ".IntegrationTests"]:
                    test_project = f"{project_name}{test_project_suffix}"
                    if relative_subdir:
                        candidates.append(f"{test_project}/{relative_subdir}/{test_name}")
                    else:
                        candidates.append(f"{test_project}/{test_name}")
            else:
                # fallback: just try tests/ directory with same structure
                if dir_part:
                    candidates.append(f"tests/{dir_part}/{test_name}")
                    candidates.append(f"test/{dir_part}/{test_name}")
                else:
                    candidates.append(f"tests/{test_name}")
                    candidates.append(f"test/{test_name}")

        return candidates

    def test_to_source_paths(self, test_path):
        """Map C# test files to potential source file paths."""
        p = test_path.replace("\\", "/")
        base = os.path.basename(p)
        name = os.path.splitext(base)[0]

        # strip test suffix
        src_name = None
        if name.endswith("Tests"):
            src_name = name[:-5]
        elif name.endswith("Test"):
            src_name = name[:-4]
        else:
            return []

        candidates = []
        dir_part = os.path.dirname(p)
        parts = dir_part.split("/") if dir_part else []

        # find the test project directory and extract relative subdir
        project_name = None
        relative_subdir = ""

        # look for test project patterns: tests/ProjectName.Tests/Subdir or ProjectName.Tests/Subdir
        for i, part in enumerate(parts):
            if part.endswith(".Tests") or part.endswith(".UnitTests") or part.endswith(".IntegrationTests"):
                # found test project
                if part.endswith(".Tests"):
                    project_name = part[:-6]
                elif part.endswith(".UnitTests"):
                    project_name = part[:-10]
                elif part.endswith(".IntegrationTests"):
                    project_name = part[:-17]

                # everything after the test project is the relative subdir
                if i + 1 < len(parts):
                    relative_subdir = "/".join(parts[i + 1 :])
                break

        src_file = f"{src_name}.cs"

        if project_name:
            # pattern: tests/ProjectName.Tests/Subdir -> src/ProjectName/Subdir
            if relative_subdir:
                candidates.append(f"src/{project_name}/{relative_subdir}/{src_file}")
                candidates.append(f"{project_name}/{relative_subdir}/{src_file}")
            else:
                candidates.append(f"src/{project_name}/{src_file}")
                candidates.append(f"{project_name}/{src_file}")
        else:
            # fallback: remove tests/ prefix if present
            if parts and parts[0] in ("tests", "test"):
                if len(parts) > 1:
                    src_dir = "/".join(parts[1:])
                    candidates.append(f"src/{src_dir}/{src_file}")
                    candidates.append(f"{src_dir}/{src_file}")
                else:
                    candidates.append(f"src/{src_file}")
            elif dir_part:
                candidates.append(f"{dir_part}/{src_file}")

        return candidates

    def is_test_file(self, path):
        base = os.path.basename(path)
        name = os.path.splitext(base)[0]
        # check both filename pattern and directory pattern
        has_test_suffix = name.endswith("Test") or name.endswith("Tests")
        p = path.replace("\\", "/")
        in_test_dir = "/tests/" in p or "/test/" in p or p.startswith("tests/") or p.startswith("test/")
        in_test_project = ".Tests/" in p or ".UnitTests/" in p or ".IntegrationTests/" in p
        return has_test_suffix and (in_test_dir or in_test_project)


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
    CSharpConvention(),
]


# Pre-built singletons the facade uses for per-extension dispatch. Keep
# these as module-level constants so ``classify_test_kind`` doesn't pay
# the construction cost on every call (the facade is hot — called once
# per indexed file by ``test-pyramid``, ``endpoints``, ``n1`` etc.).
_JS_CONVENTION = next(c for c in _ALL_CONVENTIONS if isinstance(c, JavaScriptConvention))


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


# ---------------------------------------------------------------------------
# Canonical facade — "is this a test file" + kind classification
# ---------------------------------------------------------------------------
#
# This is the **public API** other modules should call. It consults the
# adapter list above (which handles per-language quirks) and falls back to
# a flat list of regex patterns covering languages without a dedicated
# adapter (Kotlin, Scala, Elixir, Dart, Swift, Rust, PHP, ...).
#
# The two parallel sources of truth (this module's adapters and
# ``file_roles._TEST_PATTERNS``) used to disagree — W6.2 consolidates them
# here. ``file_roles.is_test`` / ``file_roles.classify_test_kind`` now
# delegate to the facade defined below.


# Canonical pattern set covering all conventions roam-code recognises.
# Matched against the **basename** of a path (case-sensitive for Java/C#
# camelCase suffixes; lowercase for ext suffixes). Ordered most-specific
# first so the adapter routing can short-circuit.
DEFAULT_TEST_PATTERNS: list[re.Pattern[str]] = [
    # Python: test_*.py, *_test.py, conftest.py
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
    # Go: *_test.go
    re.compile(r"^.*_test\.go$"),
    # JavaScript / TypeScript / Vitest / Vue SFC:
    # *.test.{js,ts,jsx,tsx,mjs,cjs,mts,cts,vue}
    re.compile(r"^.*\.test\.(?:[jt]sx?|[mc][jt]s|vue)$"),
    re.compile(r"^.*\.spec\.(?:[jt]sx?|[mc][jt]s|vue)$"),
    # Java: *Test.java, *Tests.java
    re.compile(r"^.*Tests?\.java$"),
    # Kotlin: *Test.kt, *Tests.kt
    re.compile(r"^.*Tests?\.kt$"),
    # C#: *Test.cs, *Tests.cs
    re.compile(r"^.*Tests?\.cs$"),
    # Ruby: *_spec.rb
    re.compile(r"^.*_spec\.rb$"),
    # PHP: *Test.php
    re.compile(r"^.*Test\.php$"),
    # Scala: *Test.scala, *Spec.scala
    re.compile(r"^.*(?:Test|Spec)\.scala$"),
    # Elixir: *_test.exs
    re.compile(r"^.*_test\.exs$"),
    # Dart: *_test.dart
    re.compile(r"^.*_test\.dart$"),
    # Salesforce Apex: *Test.cls
    re.compile(r"^.*Test\.cls$"),
    # Rust: tests are usually inline, but *_test.rs files exist too
    re.compile(r"^.*_test\.rs$"),
    # Swift: *Tests.swift, *Test.swift
    re.compile(r"^.*Tests?\.swift$"),
]


# Directory-path patterns that mark a file as a test even when its name
# doesn't match (e.g. ``tests/helpers.py`` — not "test_*.py" but inside
# a test directory). Mirrors the test-related entries in
# ``file_roles._PATH_PATTERNS``.
_TEST_DIR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|/)tests/"),
    re.compile(r"(^|/)test/"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)spec/"),
    re.compile(r"(^|/)testing/"),
]


# Kind classification (path-pattern based, used by the facade when no
# adapter override applies).
_TEST_KIND_PATH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Most specific first.
    (re.compile(r"(^|/)(e2e|end-to-end|cypress|playwright|selenium)/"), KIND_E2E),
    (re.compile(r"(^|/)(integration|integ|int)/"), KIND_INTEGRATION),
    (re.compile(r"(^|/)(unit)/"), KIND_UNIT),
    (re.compile(r"(^|/)(smoke|sanity)/"), KIND_SMOKE),
]
_TEST_KIND_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(^|[._-])(e2e|end[._-]to[._-]end)([._-]|$)"), KIND_E2E),
    (re.compile(r"(?i)(^|[._-])(integration|integ)([._-]|$)"), KIND_INTEGRATION),
    (re.compile(r"(?i)(^|[._-])(unit)([._-]|$)"), KIND_UNIT),
    (re.compile(r"(?i)(^|[._-])(smoke|sanity)([._-]|$)"), KIND_SMOKE),
]

# File extensions whose ``.test.<ext>`` / ``.spec.<ext>`` infix is the
# Vitest/Jest/Mocha convention. Colocated specs of these kinds default
# to ``unit`` when no other directory hint is present.
_VITEST_LIKE_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue"})
_VITEST_INFIX_RE = re.compile(r"\.(test|spec)\.")


# Map of "extension → language label" used by ``language_for_test``.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".vue": "vue",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".exs": "elixir",
    ".dart": "dart",
    ".cls": "apex",
    ".rs": "rust",
    ".swift": "swift",
}


PathLike = Union[str, "Path"]


def _norm(path: PathLike) -> str:
    """Normalise a Path-or-str input to a forward-slash string."""
    return str(path).replace("\\", "/")


def is_test_file(path: PathLike) -> bool:
    """Return True if *path* is a test file under any supported convention.

    This is the canonical detector. It checks (1) any path component
    matching ``tests/``, ``test/``, ``__tests__/``, ``spec/``,
    ``testing/`` and (2) basename matching any pattern in
    ``DEFAULT_TEST_PATTERNS``. Per-language adapters in this module
    delegate to this function for the cross-language case but may have
    stricter checks for their own language.

    >>> is_test_file("tests/test_auth.py")
    True
    >>> is_test_file("src/composables/useFoo.test.ts")
    True
    >>> is_test_file("src/components/Foo.test.vue")
    True
    >>> is_test_file("src/main.py")
    False
    >>> is_test_file("src/components/Foo.vue")
    False
    """
    norm = _norm(path)
    # Test directory anywhere in the path
    for pattern in _TEST_DIR_PATTERNS:
        if pattern.search(norm):
            return True
    # Test filename pattern on the basename
    basename = os.path.basename(norm)
    for pattern in DEFAULT_TEST_PATTERNS:
        if pattern.match(basename):
            return True
    return False


def classify_test_kind(
    path: PathLike,
    all_files: Optional[set[Path]] = None,  # noqa: ARG001 — reserved for future content checks
) -> str:
    """Classify a test file as ``unit | integration | e2e | smoke | unknown``.

    Returns ``unknown`` for non-test files.

    This is the **canonical entry point**. It dispatches to per-language
    adapters by the path's extension so the same input always produces
    the same classification across every consumer command. In particular:

    * **JS / TS / Vue paths** (extensions ``.js`` / ``.jsx`` / ``.ts`` /
      ``.tsx`` / ``.mjs`` / ``.cjs`` / ``.mts`` / ``.cts`` / ``.vue``)
      delegate to :class:`JavaScriptConvention.classify_kind`, which
      applies the Vitest-aware rules (colocated and ``__tests__/`` specs
      default to ``unit``).
    * **Other extensions** fall through to the cross-language fallback
      below (path-pattern → name-pattern → Vitest-infix-defaults-to-unit).

    Path patterns win over name patterns (most-specific first).

    The ``all_files`` parameter is reserved for future cross-file
    inference (e.g. "this test imports from a service so it's
    integration") and is currently ignored. Pass-through is kept stable
    so callers can adopt the future signature without churn.

    >>> classify_test_kind("tests/e2e/login.test.ts")
    'e2e'
    >>> classify_test_kind("tests/integration/db.test.py")
    'integration'
    >>> classify_test_kind("src/composables/useFoo.test.ts")
    'unit'
    >>> classify_test_kind("src/components/Foo.test.vue")
    'unit'
    >>> classify_test_kind("tests/smoke/sanity.test.ts")
    'smoke'
    >>> classify_test_kind("tests/test_auth.py")
    'unknown'
    >>> classify_test_kind("src/main.py")
    'unknown'
    """
    norm = _norm(path)
    basename = os.path.basename(norm)
    ext = os.path.splitext(basename)[1].lower()

    # JS / TS / Vue: delegate to the Vitest-aware adapter. Its
    # ``classify_kind`` is a strict superset of the cross-language
    # fallback (it handles unit/integration/e2e/smoke directories +
    # name infixes AND adds the Vitest "colocated spec defaults to
    # unit" rule). Keeping the dispatch in one place is the W12.x
    # parity fix — every consumer hitting this facade now sees the
    # same classification as anyone who called the adapter directly.
    if ext in _VITEST_LIKE_EXTS:
        return _JS_CONVENTION.classify_kind(norm)

    if not is_test_file(norm):
        return KIND_UNKNOWN

    for pattern, kind in _TEST_KIND_PATH_PATTERNS:
        if pattern.search(norm):
            return kind
    for pattern, kind in _TEST_KIND_NAME_PATTERNS:
        if pattern.search(basename):
            return kind

    # Vitest fallback for non-JS/TS extensions that nonetheless use
    # ``.test.<ext>`` / ``.spec.<ext>``. Defensive — _VITEST_LIKE_EXTS
    # already covers the canonical set; this branch only fires for
    # extensions added to _VITEST_LIKE_EXTS without also getting their
    # own adapter.
    if ext in _VITEST_LIKE_EXTS and _VITEST_INFIX_RE.search(basename):
        return KIND_UNIT
    return KIND_UNKNOWN


def language_for_test(path: PathLike) -> str:
    """Return the language label for a test file.

    Returns one of: ``python | javascript | typescript | vue | go |
    java | kotlin | csharp | ruby | php | scala | elixir | dart | apex |
    rust | swift | unknown``.

    Non-test files still get a language label based on extension; the
    ``unknown`` return is reserved for extensions roam-code doesn't
    recognise.

    >>> language_for_test("test_foo.py")
    'python'
    >>> language_for_test("Foo.test.vue")
    'vue'
    >>> language_for_test("Foo.test.ts")
    'typescript'
    """
    norm = _norm(path)
    ext = os.path.splitext(norm)[1].lower()
    return _EXT_TO_LANG.get(ext, "unknown")


def source_for_test(
    test_path: PathLike,
    all_files: Optional[set[Path]] = None,
) -> Optional[Path]:
    """Return the first source-file candidate for a test, or None.

    Routes to the adapter for the test's language. If ``all_files`` is
    provided, returns only candidates that exist in the set (a Path-set
    of project files). Otherwise returns the first candidate the adapter
    produces.

    >>> source_for_test("tests/test_models.py") is not None
    True
    >>> source_for_test("src/main.py") is None
    True
    """
    norm = _norm(test_path)
    if not is_test_file(norm):
        return None
    lang = language_for_test(norm)
    conv = get_convention_for_language(lang)
    if conv is None:
        # Try every adapter — first hit wins.
        for c in _ALL_CONVENTIONS:
            candidates = c.test_to_source_paths(norm)
            if candidates:
                if all_files is None:
                    return Path(candidates[0])
                for cand in candidates:
                    if Path(cand) in all_files:
                        return Path(cand)
        return None
    candidates = conv.test_to_source_paths(norm)
    if not candidates:
        return None
    if all_files is None:
        return Path(candidates[0])
    for cand in candidates:
        if Path(cand) in all_files:
            return Path(cand)
    return None
