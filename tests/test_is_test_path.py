"""W873: canonical ``roam.catalog._shared.is_test_path`` coverage.

Pins the catalog-layer test-path detector. The helper unions every
historical in-file variant; this test exercises representative paths
from each of the historical call-sites that were folded into it
(W873 hoist: ``detectors._is_test_path`` + ``type_switch._file_is_test``).

If a new test-naming convention has to be supported, add it to
``_TEST_DIR_SEGMENTS`` / ``_TEST_DIR_PREFIXES`` / ``_TEST_FILE_SUFFIXES``
/ ``_TEST_BASENAME_INFIXES`` in ``src/roam/catalog/_shared.py`` AND add
a paired case here.
"""

from __future__ import annotations

import pytest

from roam.catalog._shared import is_test_path


@pytest.mark.parametrize(
    "path",
    [
        # Pytest convention: ``tests/`` root + ``test_*.py`` basename.
        "tests/test_auth.py",
        # Nested ``tests/`` directory anywhere in the path.
        "src/foo/tests/test_bar.py",
        # ``__tests__/`` (Jest / React Testing Library convention).
        "src/components/__tests__/Button.test.tsx",
        # ``spec/`` (RSpec / Ruby convention).
        "spec/models/user_spec.rb",
        # ``testing/`` (occasionally seen in PHP / Go projects).
        "src/testing/helpers.php",
        # Go convention: ``*_test.go`` basename, no test dir required.
        "internal/auth/handler_test.go",
        # ``conftest.py`` (pytest fixture file, often outside ``tests/``).
        "src/myproject/conftest.py",
        # Windows-style backslash separators — the helper must
        # normalise these to forward slashes before matching.
        "tests\\foo\\test_bar.py",
        # Vitest / Jest: ``.test.`` infix on the basename.
        "src/utils/format.test.ts",
        # ``.spec.`` infix on the basename.
        "src/utils/format.spec.js",
    ],
)
def test_is_test_path_matches_known_test_files(path: str) -> None:
    assert is_test_path(path) is True, f"expected is_test_path({path!r}) to be True"


@pytest.mark.parametrize(
    "path",
    [
        # Plain source file.
        "src/roam/cli.py",
        # Filename that contains ``test`` as a substring but is not a
        # test file — e.g. ``contest.py`` or ``latest.py``.
        "src/utils/latest.py",
        "src/utils/contest.py",
        # Empty / falsy inputs return False rather than crashing.
        "",
        # A non-test ``.py`` file in a non-test directory.
        "src/components/Button.tsx",
        # Plausible-looking but non-test path: directory called
        # ``tester`` (not ``tests``) should NOT match.
        "src/tester/handler.py",
    ],
)
def test_is_test_path_rejects_non_test_files(path: str) -> None:
    assert is_test_path(path) is False, f"expected is_test_path({path!r}) to be False"


def test_is_test_path_case_insensitive() -> None:
    """Mixed-case ``Tests/`` (case-insensitive filesystem) still matches."""
    assert is_test_path("Tests/test_auth.py") is True
    assert is_test_path("src/__TESTS__/foo.test.ts") is True


# ---------------------------------------------------------------------------
# W886 / W889 parity: commands-layer ``is_test_file`` must agree with the
# catalog canonical on the curated set covering every pattern the 4
# W886 delegating sites (cmd_over_fetch, metrics_history, rules.builtin,
# rules.dataflow) previously hand-rolled, PLUS the cross-language
# camelCase basenames closed in W889. The two helpers cover overlapping
# but distinct "test-file" definitions:
#
#   * ``catalog._shared.is_test_path``       — most permissive catalog-layer
#     detector (W873 + W889). Pure pattern lists; no delegation to
#     file_roles.
#   * ``commands.changed_files.is_test_file`` — canonical commands-layer
#     detector. Delegates to ``index.file_roles.is_test`` →
#     ``test_conventions.is_test_file`` for cross-language coverage, with
#     a legacy basename/dir fallback.
#
# Both detect the same baseline (test_*.py, *_test.py, .test.ts, .spec.ts,
# tests/, __tests__/, spec/, testing/) AND the Java/PHP/Kotlin/C#/Swift/
# Scala/Apex camelCase ``*Test.<ext>`` / ``*Tests.<ext>`` / ``*Spec.scala``
# family (W889 — parity closed by extending catalog ``is_test_path`` with
# ``_CAMELCASE_TEST_BASENAME_PATTERNS``).
# ---------------------------------------------------------------------------


def test_w886_parity_catalog_vs_commands_layer() -> None:
    """Catalog ``is_test_path`` and commands ``is_test_file`` agree on the
    union of patterns the 4 W886 delegating sites previously hand-rolled.

    Post-W889: also includes the camelCase ``*Test.<ext>`` family that
    was a known catalog-layer gap until W889 widened the helper.
    """
    from roam.commands.changed_files import is_test_file

    # Inputs every legacy variant in W886's 4 sites would have matched.
    shared_positive_cases = [
        # cmd_over_fetch PHP variant (the snake_case subset)
        "tests/UserModel.php",
        "src/test/foo.php",
        "src/testing/helpers.php",
        # metrics_history Python-narrow variant
        "test_foo.py",
        "foo_test.py",
        # rules.builtin (pre-hoist detectors._is_test_path clone)
        "foo/tests/bar.py",
        "foo/test/bar.py",
        "foo/__tests__/bar.js",
        "foo/spec/bar.rb",
        # rules.dataflow JS spec variant
        "src/utils/foo.spec.js",
        "src/utils/foo.spec.ts",
        # Cross-cutting: directory + basename intersections
        "src/components/__tests__/Button.test.tsx",
        "internal/auth/handler_test.go",
        "src/myproject/conftest.py",
    ]
    shared_negative_cases = [
        "src/roam/cli.py",
        "src/utils/latest.py",
        "src/utils/contest.py",
        "src/components/Button.tsx",
        "src/tester/handler.py",
    ]

    for path in shared_positive_cases:
        catalog = is_test_path(path)
        commands = is_test_file(path)
        assert catalog == commands == True, (  # noqa: E712 — explicit identity for clarity
            f"divergence at {path!r}: catalog={catalog} commands={commands}"
        )

    for path in shared_negative_cases:
        catalog = is_test_path(path)
        commands = is_test_file(path)
        assert catalog == commands == False, (  # noqa: E712 — explicit identity for clarity
            f"divergence at {path!r}: catalog={catalog} commands={commands}"
        )


# ---------------------------------------------------------------------------
# W889: positive parity — ``catalog._shared.is_test_path`` now recognises
# the cross-language camelCase / PascalCase test basenames (was W886's
# known gap). Flipping the prior xfail-style "we miss these" assertion to
# a positive one closes the divergence on Java / Kotlin / C# / Swift /
# PHP / Scala / Apex codebases (the catalog-layer detectors —
# ``smells``, ``type_switch``, ``parallel_hierarchy``,
# ``clones_cross_layer`` — were over-reporting findings on ``UserTest.php``
# / ``UserTests.cs`` / etc. before this fix).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Java: ``*Test.java`` and ``*Tests.java`` (JUnit / TestNG).
        "src/com/example/UserTest.java",
        "src/com/example/UserTests.java",
        # Kotlin: ``*Test.kt`` / ``*Tests.kt``.
        "src/main/kotlin/UserTest.kt",
        "src/main/kotlin/UserTests.kt",
        # C#: ``*Test.cs`` / ``*Tests.cs`` (xUnit / NUnit / MSTest).
        "src/MyApp/UserTest.cs",
        "services/PaymentTests.cs",
        # Swift: ``*Test.swift`` / ``*Tests.swift`` (XCTest).
        "App/Models/UserTests.swift",
        "App/UserTest.swift",
        # PHP: ``*Test.php`` (PHPUnit / Pest) outside any ``tests/`` dir.
        "app/Models/UserTest.php",
        # Scala: ``*Test.scala`` and ``*Spec.scala`` (ScalaTest).
        "src/main/scala/UserTest.scala",
        "src/main/scala/UserSpec.scala",
        # Salesforce Apex: ``*Test.cls``.
        "force-app/main/default/classes/UserTest.cls",
    ],
)
def test_w889_catalog_layer_recognises_camelcase_test_basenames(path: str) -> None:
    """Catalog ``is_test_path`` MUST recognise camelCase test basenames
    so cross-language detectors don't mis-classify them as production
    code. Asserts catalog/commands parity on each case.
    """
    from roam.commands.changed_files import is_test_file

    assert is_test_path(path) is True, (
        f"catalog ``is_test_path`` should recognise camelCase test basename: {path!r}"
    )
    assert is_test_file(path) is True, (
        f"commands ``is_test_file`` should recognise camelCase test basename: {path!r}"
    )


@pytest.mark.parametrize(
    "path",
    [
        # Production sources in the same languages — must NOT match.
        "app/Models/UserService.php",
        "src/com/example/UserService.java",
        "src/MyApp/UserService.cs",
        "App/Models/User.swift",
        "src/main/kotlin/UserService.kt",
        "src/main/scala/UserService.scala",
        # Lowercase ``test`` inside a longer word — the canonical regex
        # is case-sensitive on the ``Test``/``Tests`` token to avoid
        # over-reach. ``latest.java`` ends in lowercase ``test``; not a
        # test file.
        "app/latest.java",
        "src/latest.cs",
        # Boundary: bare ``Test.java`` — DOES match per the canonical
        # ``^.*Tests?\.java$`` regex (``.*`` allows empty prefix). Both
        # layers agree on this; documented here so the choice is
        # deliberate (parity with index.test_conventions canonical).
        # No assertion here — see the positive parametrize above if you
        # need to flip this boundary.
    ],
)
def test_w889_catalog_layer_rejects_camelcase_production_files(path: str) -> None:
    """Negative cases: production source files in the same languages
    must NOT be classified as tests by either layer. Confirms the
    case-sensitive ``Test``/``Tests`` token discipline.
    """
    from roam.commands.changed_files import is_test_file

    assert is_test_path(path) is False, (
        f"catalog ``is_test_path`` should reject production file: {path!r}"
    )
    assert is_test_file(path) is False, (
        f"commands ``is_test_file`` should reject production file: {path!r}"
    )


# ---------------------------------------------------------------------------
# W891: Elixir / Dart ``_test.<ext>`` basenames. The canonical
# ``roam.index.test_conventions.DEFAULT_TEST_PATTERNS`` already covers
# ``^.*_test\.exs$`` and ``^.*_test\.dart$``. Pre-W891 the catalog-layer
# ``_TEST_FILE_SUFFIXES`` tuple was missing both suffixes, so the catalog
# detector silently misclassified Elixir / Dart test files as production
# code — same FP-risk class as the W889 camelCase parity gap.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Elixir: ``*_test.exs`` (ExUnit convention).
        "lib/auth/user_test.exs",
        "test/auth/user_test.exs",
        # Dart: ``*_test.dart`` (package:test convention).
        "lib/models/user_test.dart",
        "test/models/user_test.dart",
    ],
)
def test_w891_catalog_layer_recognises_elixir_and_dart_basenames(path: str) -> None:
    """Catalog ``is_test_path`` MUST recognise ``_test.exs`` (Elixir) and
    ``_test.dart`` (Dart) basenames so catalog-layer detectors don't
    misclassify test files in those stacks. Asserts catalog/commands
    parity on each case.
    """
    from roam.commands.changed_files import is_test_file

    assert is_test_path(path) is True, (
        f"catalog ``is_test_path`` should recognise Elixir/Dart test basename: {path!r}"
    )
    assert is_test_file(path) is True, (
        f"commands ``is_test_file`` should recognise Elixir/Dart test basename: {path!r}"
    )


# ---------------------------------------------------------------------------
# W893: Apex ``*_Test.cls`` parity pin. The canonical
# ``DEFAULT_TEST_PATTERNS`` Apex regex (``^.*Test\.cls$``) already greedily
# matches the optional underscore prefix because ``.*`` consumes it, but
# only the ``ApexConvention`` adapter ``is_test_file`` was previously
# pinned on the ``_Test.cls`` shape. This test asserts the
# parity-across-layers contract explicitly so a future tightening of the
# canonical regex (e.g. ``^[A-Za-z][A-Za-z0-9]*Test\.cls$`` — no
# underscores allowed) would fail loud rather than silently regress the
# ``UserController_Test.cls`` classification.
# ---------------------------------------------------------------------------


def test_w893_apex_underscore_test_cls_parity() -> None:
    """``*_Test.cls`` basenames MUST be classified as tests by every
    layer (catalog ``is_test_path``, canonical
    ``index.test_conventions.is_test_file``, commands
    ``changed_files.is_test_file``, and the per-language
    ``ApexConvention.is_test_file`` adapter). Pins W893.
    """
    from roam.commands.changed_files import is_test_file as commands_is_test_file
    from roam.index.test_conventions import (
        ApexConvention,
        is_test_file as canonical_is_test_file,
    )

    path = "force-app/main/default/classes/UserController_Test.cls"
    assert is_test_path(path) is True, (
        f"catalog ``is_test_path`` should recognise ``*_Test.cls``: {path!r}"
    )
    assert commands_is_test_file(path) is True, (
        f"commands ``is_test_file`` should recognise ``*_Test.cls``: {path!r}"
    )
    assert canonical_is_test_file(path) is True, (
        f"canonical ``is_test_file`` should recognise ``*_Test.cls``: {path!r}"
    )
    # ApexConvention adapter takes a bare basename / relative path.
    assert ApexConvention().is_test_file("UserController_Test.cls") is True, (
        "ApexConvention adapter should recognise ``*_Test.cls``"
    )
