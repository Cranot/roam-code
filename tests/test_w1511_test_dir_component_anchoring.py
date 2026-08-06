"""W1511 — pin component-boundary anchoring for the test-directory fallback.

``commands.changed_files.is_test_file`` matched its test-directory list with
an unanchored substring test::

    _TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/", "testing/"]
    return any(d in p_lower for d in _TEST_DIR_PATS)

Because every pattern carries a trailing ``/``, the substring can only land at
a component boundary — so the effective rule was "any directory component
whose name **ends with** ``test`` / ``tests`` / ``__tests__`` / ``spec`` /
``testing``". ``latest/`` matched (last four chars ``t,e,s,t``); ``manifest/``
did not (``f,e,s,t``). That silently classified ``src/latest/``,
``releases/latest/``, ``app/contest/``, ``src/backtesting/`` and roam-code's
own ``src/roam/attest/`` package as test paths, and the predicate is used as a
``continue``/skip filter at ~150 call sites (catalog detectors, dead-code,
taint dataflow, auth-gaps, safe-delete, verify auto-selection).

The fix is **strictly narrowing** and must stay that way. Two halves are
pinned here and BOTH are load-bearing:

* the collision cases must be ``False``;
* the genuine test-directory conventions must stay ``True`` — including the
  ones the canonical anchored ``test_conventions._TEST_DIR_PATTERNS``
  (``(^|/)tests/`` …) does **not** cover and which only this fallback
  supplies: .NET test projects (``MyProject.Tests/``), Xcode/XCTest targets
  (``MyAppUITests/``), Gradle/Android source sets (``androidTest/``,
  ``sharedTest/``, ``integrationTest/``) and delimiter-separated lowercase
  directories (``integration-tests/``, ``api_tests/``).

Without the second half a future "just make it an exact component match"
simplification silently reclassifies every file in those trees as production
source — which, at the suppression call sites, turns real test files into
production findings.
"""

from __future__ import annotations

import pytest

# The pre-fix rule, reproduced verbatim so the strict-narrowing property below
# is checked against the real historical behaviour rather than a paraphrase.
_LEGACY_TEST_DIR_PATS = ["tests/", "test/", "__tests__/", "spec/", "testing/"]


def _legacy_dir_match(path: str) -> bool:
    """The unanchored substring rule this fix replaces."""
    return any(d in path.replace("\\", "/").lower() for d in _LEGACY_TEST_DIR_PATS)


# ---------------------------------------------------------------------------
# Direction 1 — collisions. A directory component that merely ENDS WITH
# "test"/"tests"/"spec"/"testing" is not a test directory.
# ---------------------------------------------------------------------------

COLLISION_PATHS = [
    # "latest" — the canonical collision, at every depth and shape.
    "latest/build.py",
    "dist/latest/bundle.js",
    "releases/latest/server.py",
    "docs/latest/api_reference.py",
    "src/x/latest/y/z.py",
    # Other real words ending in "test".
    "app/contest/scoring.py",
    "src/greatest/x.js",
    "app/protest/views.py",
    "src/pretest/setup.py",
    # roam-code's own production package — "attest" ends in "test".
    "src/roam/attest/vsa.py",
    "src/roam/attest/cga.py",
    # A vendored/tooling directory named after the tool, not a test dir.
    "pytest/foo.py",
    # "...testing" and "...spec" suffixes.
    "src/backtesting/engine.py",
    "src/myspec/parser.py",
    "src/respec/core.rb",
    # Control: already correct before the fix (guards against over-widening
    # while fixing the anchoring).
    "src/tester/handler.py",
    "src/manifest/loader.py",
    "src/main.py",
]


@pytest.mark.parametrize("path", COLLISION_PATHS)
def test_w1511_directory_ending_in_test_is_not_a_test_path(path: str) -> None:
    """A component that ends with — but is not — a test token is production."""
    from roam.catalog._shared import is_test_path
    from roam.commands.changed_files import is_test_file

    assert is_test_file(path) is False, (
        f"commands ``is_test_file`` must not classify {path!r} as a test path: "
        "the directory component only ENDS WITH a test token, it is not one"
    )
    assert is_test_path(path) is False, f"catalog ``is_test_path`` must agree with commands on {path!r}"


# ---------------------------------------------------------------------------
# Direction 2 — every genuine convention still classifies as a test.
# ---------------------------------------------------------------------------

GENUINE_TEST_PATHS = [
    # Exact lowercase tokens (also covered by the canonical detector).
    "tests/test_auth.py",
    "test/helpers.py",
    "src/components/__tests__/Button.jsx",
    "spec/models/user_spec.rb",
    "src/testing/helpers.php",
    "src/foo/tests/conftest.py",
    # Case-insensitive exact tokens — load-bearing on case-insensitive
    # filesystems (W898); the canonical detector is case-SENSITIVE and
    # misses all four, so this fallback is the only thing covering them.
    "Tests/Helper.cs",
    "SPEC/foo.rb",
    "src/__TESTS__/a.js",
    "TESTING/b.py",
    # Delimiter-separated last token — hyphen, underscore and dot.
    "integration-tests/api/client.py",
    "e2e-tests/flow.js",
    "api_tests/client.py",
    "src/MyProject.Tests/TestHelper.cs",
    "src/MyProject.UnitTests/Fixtures/Data.cs",
    "src/MyProject.IntegrationTests/Setup.cs",
    # camelCase / PascalCase source sets and targets. The capital ``T`` is
    # the only thing separating these from ``latest``.
    "app/src/androidTest/java/com/x/Rules.kt",
    "app/src/sharedTest/java/com/x/Fakes.kt",
    "src/integrationTest/kotlin/Setup.kt",
    "src/functionalTest/groovy/Spec.groovy",
    "MyAppUITests/Helpers.swift",
    "ios/MyAppTests/LoginTests.swift",
    # Filename conventions — must survive untouched, no test directory
    # anywhere in the path.
    "src/app/UserTest.php",
    "pkg/svc/handler_test.go",
    "src/comp/Foo.spec.ts",
    "src/comp/Foo.test.tsx",
    "src/myproject/conftest.py",
    "src/com/example/UserTests.java",
    "src/main/kotlin/UserTest.kt",
    "lib/auth/user_test.exs",
    "lib/models/user_test.dart",
    "force-app/main/default/classes/UserController_Test.cls",
    "App/Models/UserTests.swift",
    # Windows-style separators must normalise before matching.
    "tests\\foo\\test_bar.py",
    "src\\MyProject.Tests\\Helper.cs",
    "src\\latest\\..\\Tests\\Helper.cs",
]


@pytest.mark.parametrize("path", GENUINE_TEST_PATHS)
def test_w1511_genuine_test_conventions_still_classify_as_tests(path: str) -> None:
    """Anchoring must not drop any convention the fallback already covered."""
    from roam.catalog._shared import is_test_path
    from roam.commands.changed_files import is_test_file

    assert is_test_file(path) is True, f"commands ``is_test_file`` must still classify {path!r} as a test path"
    assert is_test_path(path) is True, f"catalog ``is_test_path`` must agree with commands on {path!r}"


# ---------------------------------------------------------------------------
# Direction 3 — the fix is strictly NARROWING. It may never invent a new
# positive, only remove wrong ones. This is what makes the change safe to
# land across every suppression call site at once.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", COLLISION_PATHS + GENUINE_TEST_PATHS)
def test_w1511_directory_rule_is_a_strict_subset_of_the_legacy_rule(path: str) -> None:
    """Every directory-driven positive must also be a legacy positive.

    The legacy substring rule was over-broad, never under-broad. Any path the
    anchored rule accepts on directory grounds alone must therefore have been
    accepted before too — otherwise the "fix" is a widening in disguise.
    """
    from roam.commands.changed_files import _is_test_dir_component

    p_cs = path.replace("\\", "/")
    anchored = any(_is_test_dir_component(c) for c in p_cs.split("/")[:-1])
    if anchored:
        assert _legacy_dir_match(path), (
            f"WIDENING: {path!r} is a test path under the anchored rule but was NOT one under the legacy substring rule"
        )


# ---------------------------------------------------------------------------
# Direction 4 — the component predicate itself, so the three accepted shapes
# are documented independently of any path plumbing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        # 1. exact token, any case
        ("tests", True),
        ("test", True),
        ("__tests__", True),
        ("spec", True),
        ("testing", True),
        ("Tests", True),
        ("SPEC", True),
        ("__TESTS__", True),
        # 2. last delimiter-separated token, any case
        ("integration-tests", True),
        ("api_tests", True),
        ("MyProject.Tests", True),
        ("MyProject.UnitTests", True),
        ("e2e-tests", True),
        # 3. camelCase / PascalCase suffix, case-SENSITIVE
        ("androidTest", True),
        ("sharedTest", True),
        ("integrationTest", True),
        ("functionalTest", True),
        ("MyAppUITests", True),
        ("UserSpec", True),
        # Negatives — the whole point of the fix.
        ("latest", False),
        ("greatest", False),
        ("contest", False),
        ("protest", False),
        ("attest", False),
        ("pytest", False),
        ("backtesting", False),
        ("myspec", False),
        ("respec", False),
        ("tester", False),
        ("manifest", False),
        ("src", False),
        ("", False),
        # Capitalised collisions: "Latest" ends in lowercase "test", so the
        # case-sensitive camelCase rule must not fire on it either.
        ("Latest", False),
        ("Backtesting", False),
    ],
)
def test_w1511_test_dir_component_predicate(component: str, expected: bool) -> None:
    from roam.commands.changed_files import _is_test_dir_component

    assert _is_test_dir_component(component) is expected, f"_is_test_dir_component({component!r}) should be {expected}"
