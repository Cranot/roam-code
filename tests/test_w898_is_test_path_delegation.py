"""W898 — pin the ``catalog._shared.is_test_path`` delegation contract.

The long arc W873 → W886 → W889 → W891 → W893 consolidated five
historical per-detector test-path heuristics into the catalog-layer
``catalog._shared.is_test_path``. In parallel, ``commands.changed_files.
is_test_file`` (the canonical commands-layer detector that delegates to
``roam.index.test_conventions.is_test_file``) had grown to cover the
same set. W898 collapses both onto one source of truth: the catalog
helper is now a thin delegate to the canonical commands-layer detector.

This file pins:

1. **Parity contract** — both helpers return identical answers on a
   curated table of positive + negative cases across every supported
   language convention. A drift on either side fails this test.
2. **Edge-case coverage** — explicit pin on the per-language cases that
   W886 / W889 / W891 / W893 originally fixed (camelCase Test*, Apex
   underscore Test.cls, Elixir _test.exs, Dart _test.dart). Guards
   against a future "simplification" of the canonical detector that
   silently regresses any of those carved-out fixes.
3. **No import cycle** — both modules import cleanly in both orders in
   the same process. Per the CLAUDE.md "Verify the cycle before
   hedging" rule (W907), this test exists so the lazy import inside
   ``is_test_path`` does NOT become cargo-cult: if a real cycle ever
   appears (e.g. ``changed_files`` starts importing from ``catalog``),
   this test fails loudly so the lazy import becomes load-bearing,
   not decorative.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Task 1 — Parity contract (12-row table covering every language convention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Positive: Python pytest layout.
        ("tests/test_auth.py", True),
        # Positive: Go convention.
        ("internal/auth/handler_test.go", True),
        # Positive: Vitest/Jest infix on JS.
        ("src/utils/format.test.ts", True),
        # Positive: Ruby RSpec.
        ("spec/models/user_spec.rb", True),
        # Positive: Java JUnit camelCase basename.
        ("src/com/example/UserTest.java", True),
        # Positive: Apex underscore Test.cls (W893).
        ("force-app/main/default/classes/UserController_Test.cls", True),
        # Positive: Elixir ExUnit (W891).
        ("lib/auth/user_test.exs", True),
        # Positive: Dart package:test (W891).
        ("lib/models/user_test.dart", True),
        # Negative: plain source file.
        ("src/roam/cli.py", False),
        # Negative: "latest" contains lowercase "test" but is NOT a test.
        ("src/utils/latest.py", False),
        # Negative: directory called "tester" — not "tests".
        ("src/tester/handler.py", False),
        # Negative: empty input falls through the falsy guard.
        ("", False),
    ],
)
def test_w898_catalog_and_commands_layers_return_identical_results(path: str, expected: bool) -> None:
    """Both helpers must agree on every cell of the parity table.

    Pins the W898 delegation: any divergence here means the canonical
    commands-layer detector has drifted from the catalog's documented
    test-path semantics.
    """
    from roam.catalog._shared import is_test_path
    from roam.commands.changed_files import is_test_file

    catalog = is_test_path(path)
    commands = is_test_file(path)
    assert catalog == commands == expected, (
        f"divergence at {path!r}: catalog={catalog} commands={commands} expected={expected}"
    )


# ---------------------------------------------------------------------------
# Task 2 — Edge-case pin (one assertion per historical W-fix)
# ---------------------------------------------------------------------------


def test_w898_pins_w886_w889_camelcase_test_basenames() -> None:
    """W886/W889: camelCase / PascalCase test basenames across Java /
    Kotlin / C# / Swift / PHP / Scala / Apex codebases. Pre-W889 the
    catalog detector mis-classified these as production code.
    """
    from roam.catalog._shared import is_test_path

    cases = [
        "src/com/example/UserTest.java",
        "src/com/example/UserTests.java",
        "src/main/kotlin/UserTest.kt",
        "src/main/kotlin/UserTests.kt",
        "src/MyApp/UserTest.cs",
        "services/PaymentTests.cs",
        "App/Models/UserTests.swift",
        "App/UserTest.swift",
        "app/Models/UserTest.php",
        "src/main/scala/UserTest.scala",
        "src/main/scala/UserSpec.scala",
        "force-app/main/default/classes/UserTest.cls",
    ]
    for path in cases:
        assert is_test_path(path) is True, f"W898 regression — W886/W889 camelCase basename should match: {path!r}"


def test_w898_pins_w891_elixir_dart_basenames() -> None:
    """W891: ``_test.exs`` (Elixir) and ``_test.dart`` (Dart) basenames.
    The canonical commands-layer detector already covered these; W891
    closed the catalog-side gap. W898 collapses both layers onto one.
    """
    from roam.catalog._shared import is_test_path

    cases = [
        "lib/auth/user_test.exs",
        "test/auth/user_test.exs",
        "lib/models/user_test.dart",
        "test/models/user_test.dart",
    ]
    for path in cases:
        assert is_test_path(path) is True, f"W898 regression — W891 Elixir/Dart basename should match: {path!r}"


def test_w898_pins_w893_apex_underscore_test_cls() -> None:
    """W893: ``UserController_Test.cls`` (Apex with underscore prefix
    on the ``Test`` token) must be classified as a test by every layer.
    The canonical regex ``^.*Test\\.cls$`` consumes the underscore via
    ``.*`` but the parity-across-layers invariant is what W898 pins.
    """
    from roam.catalog._shared import is_test_path

    path = "force-app/main/default/classes/UserController_Test.cls"
    assert is_test_path(path) is True


# ---------------------------------------------------------------------------
# Task 3 — Verify the cycle before hedging (W907)
# ---------------------------------------------------------------------------


def test_w898_no_circular_import_in_either_direction() -> None:
    """Import both modules in BOTH orders in the same process and
    verify neither raises ``ImportError`` / ``RecursionError``.

    This is the W907 discipline check: the lazy import inside
    ``is_test_path`` is a hedge, and the hedge is only honest if no
    real cycle exists. If this test starts failing because a real
    cycle appeared (e.g. ``changed_files`` grew an import from
    ``roam.catalog``), the lazy import becomes load-bearing — but the
    failure message will say so explicitly rather than silently
    masking the regression.
    """
    # Re-import is safe because both modules are already in
    # ``sys.modules`` from collection time; the assertion is on the
    # import action not raising, NOT on first-time load semantics.
    importlib.import_module("roam.catalog._shared")
    importlib.import_module("roam.commands.changed_files")
    # Reverse-order re-import (no-op for cached modules but exercises
    # the call shape).
    importlib.import_module("roam.commands.changed_files")
    importlib.import_module("roam.catalog._shared")


def test_w898_subprocess_fresh_interpreter_imports_both_orders() -> None:
    """Stronger cycle check: spawn a fresh Python interpreter and
    import both modules in BOTH orders. ``importlib`` in the parent
    process hits the module cache; this subprocess test exercises the
    cold-start path the way a user pip-install would.
    """
    root = repo_root()
    for stmt in (
        # Order A: catalog first.
        "from roam.catalog._shared import is_test_path; "
        "from roam.commands.changed_files import is_test_file; "
        "assert is_test_path('tests/foo.py') is True; "
        "assert is_test_file('tests/foo.py') is True",
        # Order B: commands first.
        "from roam.commands.changed_files import is_test_file; "
        "from roam.catalog._shared import is_test_path; "
        "assert is_test_path('tests/foo.py') is True; "
        "assert is_test_file('tests/foo.py') is True",
    ):
        proc = subprocess.run(
            [sys.executable, "-c", stmt],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"fresh-interpreter import failed:\nstmt={stmt!r}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
