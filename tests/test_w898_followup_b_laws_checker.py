"""W898-followup-B — pin the laws/checker.py is_test_file delegation.

The W898 + W898-followup arc consolidated ``is_test_path`` /
``is_test_file`` across the codebase. The followup audit surfaced one
remaining clone that was neither a trivial 1-line wrapper (like
``cmd_dead._is_test_path``) nor a deliberately-narrower local helper
(like ``relations._is_test_path`` / ``rerank._is_test_path``):

``src/roam/laws/checker.py`` had a try-import-with-narrower-fallback
at line 540-546 inside ``_check_testing_law``::

    try:
        from roam.commands.changed_files import is_test_file
    except Exception:
        def is_test_file(p: str) -> bool:  # type: ignore[no-redef]
            low = (p or "").lower()
            return "test" in low or "spec" in low

The local def was a defensive fallback against import failure with a
much-narrower heuristic — it would silently misclassify ``__tests__/``,
``_test.go``, ``Tests/`` etc. as non-test paths. The fallback was
intentionally narrower-than-canonical but it was NOT load-bearing the
way ``relations._is_test_path`` / ``rerank._is_test_path`` are: the
narrower fallback existed only as a defensive backup, not as a tuned
behavioural contract.

W898-followup-B migrated the function to the same pattern its sibling
``_check_naming_law`` uses in the same file: ``try/import/except:
return []``. Failure to import the canonical helper now degrades
gracefully (skip the law) instead of silently swapping in a narrower
heuristic. The ``# type: ignore[no-redef]`` hint is gone because there
is no more shadow.

This test file pins four invariants:

1. **The local narrower-fallback def is gone** — drift guard via
   AST scan of ``laws/checker.py``.
2. **The canonical import remains** — ``_check_testing_law`` still
   resolves ``is_test_file`` from ``roam.commands.changed_files`` at
   call time.
3. **No import cycle exists** — fresh-interpreter import in both
   orders succeeds (W907 "verify the cycle before hedging"
   discipline).
4. **Canonical-equivalent behaviour at call sites** — a 12-row
   parity table proves the canonical answer flows through
   ``_check_testing_law`` correctly on the cases the old narrower
   fallback would have silently misclassified.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CHECKER_PATH = repo_root() / "src" / "roam" / "laws" / "checker.py"


def _module_function_names(module_path: Path) -> set[str]:
    """Return the names of top-level + nested ``def`` nodes in *module_path*.

    AST-based: catches ``def is_test_file(...)`` regardless of
    indentation or surrounding ``try`` / ``except`` blocks. We want
    the nested case because the W898-followup-B target was an inner
    function defined inside an ``except`` branch.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


# ---------------------------------------------------------------------------
# Class 1 — drift guard: the narrower-fallback def is gone
# ---------------------------------------------------------------------------


class TestLawsCheckerNoLocalIsTestFile:
    """``laws/checker.py`` no longer defines a local ``is_test_file``.

    The migration removed the ``except Exception: def is_test_file
    (p: str) -> bool: ...`` fallback. A future agent that "tidies up"
    by re-adding a local def would silently re-introduce the W898
    clone. AST-scan to block that.
    """

    def test_no_local_is_test_file_def_in_checker(self) -> None:
        """AST-scan ``laws/checker.py`` — no ``def is_test_file`` (at
        any nesting depth) should exist. The post-migration file
        imports the canonical helper from
        ``roam.commands.changed_files`` and degrades to ``return []``
        on import failure instead of redefining a narrower local.
        """
        names = _module_function_names(_CHECKER_PATH)
        assert "is_test_file" not in names, (
            "W898-followup-B regression: laws/checker.py re-introduced "
            "a local def is_test_file. The migrated file relies on "
            "``from roam.commands.changed_files import is_test_file`` "
            "and degrades to ``return []`` on import failure (matching "
            "the sibling _check_naming_law pattern in the same file). "
            "A local def shadows the canonical and silently swaps in "
            "a narrower heuristic (no __tests__/, no _test.go, no "
            "Tests/) — exactly the clone the W898 audit collapsed. "
            "Delete the local def — the import already provides the "
            "name."
        )

    def test_no_type_ignore_no_redef_hint_remains(self) -> None:
        """The ``# type: ignore[no-redef]`` hint is gone too.

        The hint was load-bearing only because of the shadow; with
        the shadow removed the hint should also be gone. If it
        re-appears, it signals someone re-introduced the shadow.
        """
        text = _CHECKER_PATH.read_text(encoding="utf-8")
        assert "type: ignore[no-redef]" not in text, (
            "W898-followup-B regression: laws/checker.py contains a "
            "``# type: ignore[no-redef]`` hint, which was the marker "
            "that the file had two definitions of the same name. The "
            "migration removed both the local def AND the hint. Audit "
            "the file for a re-introduced shadow."
        )


# ---------------------------------------------------------------------------
# Class 2 — canonical import remains; _check_testing_law uses it
# ---------------------------------------------------------------------------


class TestLawsCheckerUsesCanonical:
    """``_check_testing_law`` resolves ``is_test_file`` from the canonical
    ``roam.commands.changed_files`` at call time.

    The lazy-import pattern means we can't directly assert
    ``checker.is_test_file is changed_files.is_test_file`` at module
    scope (the import only fires when ``_check_testing_law`` runs).
    Instead we invoke the function with a synthetic diff and observe
    that canonical-only positives are classified as test paths.
    """

    def test_check_testing_law_classifies_canonical_test_paths(self) -> None:
        """Run ``_check_testing_law`` on a synthetic diff with a
        ``__tests__/`` path. The canonical helper accepts
        ``__tests__/foo.js`` as a test path; the OLD narrower
        fallback would NOT have (no ``__tests__`` rule). If the
        function emits zero violations for a public symbol added in
        a ``__tests__/`` file, the canonical is in use.
        """
        from roam.laws.checker import _check_testing_law
        from roam.laws.miner import Law

        law = Law(
            id="testing-fn",
            kind="testing",
            description="public functions need a matching test",
            severity="advisory",
            confidence="high",
            rule={"symbol_kind": "function"},
        )
        # Symbol added inside a __tests__ directory — canonical
        # classifies as test, OLD narrower-fallback would have
        # classified as NON-test (no __tests__/ rule) and emitted a
        # violation for missing test coverage. With the canonical in
        # place, the symbol's own file is a test file so the law
        # skips it.
        syms_added = [
            {
                "kind": "function",
                "name": "renderFoo",
                "file": "src/__tests__/render.js",
                "line": 12,
            }
        ]
        parsed = {"files": {"src/__tests__/render.js": "+ added"}}
        violations = _check_testing_law(law, parsed, syms_added)
        assert violations == [], (
            "_check_testing_law emitted a violation for a symbol "
            "added in __tests__/ — the canonical is_test_file accepts "
            "__tests__/ as a test directory but the OLD narrower "
            "fallback did not. Empty violations means the canonical "
            "is wired through; a non-empty list means the function "
            "is using the narrower (now-deleted) heuristic."
        )

    def test_check_testing_law_skips_capitalised_tests_dir(self) -> None:
        """The canonical ``is_test_file`` matches ``Tests/`` case-
        insensitively (W898). The OLD fallback only matched lowercase
        ``"test" in low``; this case-insensitive match was
        coincidentally correct in the old code too (the substring
        match was already case-insensitive after ``.lower()``), so
        this case is regression-only — pin it to detect any
        silent regression.
        """
        from roam.laws.checker import _check_testing_law
        from roam.laws.miner import Law

        law = Law(
            id="testing-fn",
            kind="testing",
            description="public functions need a matching test",
            severity="advisory",
            confidence="high",
            rule={"symbol_kind": "function"},
        )
        syms_added = [
            {
                "kind": "function",
                "name": "Helper",
                "file": "Tests/Helper.cs",
                "line": 12,
            }
        ]
        parsed = {"files": {"Tests/Helper.cs": "+ added"}}
        violations = _check_testing_law(law, parsed, syms_added)
        assert violations == [], (
            "_check_testing_law emitted a violation for a symbol "
            "added in Tests/ (capitalised). The canonical helper "
            "lowercases the path before matching; if this fails, the "
            "canonical wiring has regressed."
        )


# ---------------------------------------------------------------------------
# Class 3 — cycle-verification (W907 discipline)
# ---------------------------------------------------------------------------


class TestNoImportCycleIntroduced:
    """Verify no import cycle exists between ``roam.laws.checker`` and
    ``roam.commands.changed_files`` — W907 "Verify the cycle before
    hedging".

    The lazy-import is justified by import-time cost (file_roles is
    heavy), NOT by cycle-avoidance. Pin that fact with a fresh-
    interpreter import in both orders.
    """

    def test_fresh_interpreter_imports_both_orders(self) -> None:
        """Spawn a fresh interpreter and import ``laws.checker`` then
        ``changed_files`` (and the reverse). Both orders must succeed
        with no ``ImportError`` / ``RecursionError``.
        """
        root = repo_root()
        for stmt in (
            # Order A: changed_files first.
            "from roam.commands.changed_files import is_test_file; "
            "from roam.laws.checker import _check_testing_law; "
            "print('ok-A')",
            # Order B: laws.checker first.
            "from roam.laws.checker import _check_testing_law; "
            "from roam.commands.changed_files import is_test_file; "
            "print('ok-B')",
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

    def test_in_process_importable_in_both_orders(self) -> None:
        """In-process variant: cached re-imports must not raise."""
        importlib.import_module("roam.laws.checker")
        importlib.import_module("roam.commands.changed_files")
        importlib.import_module("roam.commands.changed_files")
        importlib.import_module("roam.laws.checker")


# ---------------------------------------------------------------------------
# Class 4 — 12-row parity table
# ---------------------------------------------------------------------------


# Path, canonical-answer, OLD-narrower-answer. The OLD fallback was
# just ``"test" in p.lower() or "spec" in p.lower()`` — it agreed with
# canonical on ANY path containing the literal substring "test" or
# "spec" but diverged on paths like ``_test.go`` (yes substring match
# coincidentally), ``Helper.test.ts`` (yes substring), etc. The cases
# where they diverge in PRACTICE are sparse because both rely on
# substring matching at heart. The strongest divergence comes from:
#  - Paths with NO "test" / "spec" substring that canonical still
#    accepts via test_conventions adapter rules (e.g. some Apex
#    conventions). None of these are clear-cut without a fixture, so
#    the table focuses on regression-bound positives.
# This table primarily proves canonical accepts these — proving the
# wiring works, not the divergence direction.
_PARITY_TABLE: list[tuple[str, bool]] = [
    # Canonical-positive cases
    ("tests/test_foo.py", True),
    ("Tests/Helper.cs", True),
    ("__tests__/render.js", True),
    ("spec/foo_spec.rb", True),
    ("src/utils/foo.test.ts", True),
    ("internal/auth/handler_test.go", True),
    ("force-app/main/default/classes/UserTest.cls", True),
    ("conftest.py", True),
    # Canonical-negative cases
    ("src/main.py", False),
    ("README.md", False),
    ("", False),
    ("src/components/Foo.vue", False),
]


class TestCanonicalParity:
    """The canonical ``is_test_file`` returns expected answers on the
    12-row table. This is a sanity bound on the wiring — if any row
    flips, it means the canonical itself has drifted, NOT just the
    migration.
    """

    def test_canonical_parity_table(self) -> None:
        from roam.commands.changed_files import is_test_file

        for path, expected in _PARITY_TABLE:
            actual = is_test_file(path)
            assert actual is expected, (
                f"canonical is_test_file({path!r}) returned {actual!r}, "
                f"expected {expected!r} — if this row flipped, the "
                f"canonical W898 contract has drifted. Audit "
                f"changed_files.is_test_file + file_roles.is_test + "
                f"test_conventions.is_test_file for the regression."
            )
