"""W898-followup — pin the post-W898 ``_is_test_path`` clone sweep.

W898 collapsed the catalog-layer ``catalog._shared.is_test_path`` onto
the canonical commands-layer ``changed_files.is_test_file``. The W898
audit then surfaced four remaining ``_is_test_path`` clones across the
codebase:

* ``src/roam/commands/cmd_dead.py`` — trivial 1-line wrapper that
  already delegated to ``is_test_file``. **Migrated this wave** to the
  W881/W886 import-alias pattern (``import is_test_file as
  _is_test_path``). The 7 internal call-sites continue to work
  through the alias without touching their argument names.
* ``src/roam/index/relations.py`` — local helper at line 339.
  **Intentionally NOT migrated.** The docstring is load-bearing:
  the helper is deliberately NARROWER than the canonical
  (directory-only, no basename heuristics, no ``spec/`` /
  ``__tests__/``) because broadening would change which variables
  survive ``_filter_import_candidates`` and reshape resolved
  import edges across the index. Behavioural non-equivalence —
  delegating would silently widen the indexer's "treat as test"
  surface.
* ``src/roam/retrieve/rerank.py`` — local helper at line 345.
  **Intentionally NOT migrated.** The docstring is load-bearing:
  the helper is deliberately NARROWER than the canonical because
  broadening would add ``conftest.py``, ``_test.java``, etc. and
  reshape the test-vs-impl ranking trade-off that was tuned
  against the 30-task retrieve bench (-0.18 magnitude rationale).
  Behavioural non-equivalence.
* ``src/roam/commands/cmd_n1.py`` — local helper at line 362.
  **Deferred** to the W1005-followup-D sibling agent this wave;
  audit-only, no change here.

This test file pins three invariants:

1. **cmd_dead delegate took** — the post-migration alias resolves to
   the canonical helper; call-sites stay functional.
2. **relations / rerank intentional non-delegation preserved** —
   both modules still define their local ``_is_test_path``, and the
   helper still returns the narrower answer on a divergence case
   (``conftest.py`` for rerank; basename-only test for relations).
3. **Drift guard on cmd_dead** — no future agent silently re-adds
   a local ``def _is_test_path`` to ``cmd_dead.py``, which would
   shadow the import alias and re-introduce the clone.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Helpers shared across the three classes below.
# ---------------------------------------------------------------------------


def _file_defines_function(module_path: Path, fn_name: str) -> bool:
    """Return True when *module_path* defines a top-level ``def`` of *fn_name*.

    AST-based (not regex): catches ``def _is_test_path(...)`` regardless
    of formatting and ignores the same name appearing inside strings,
    comments, or as a kwarg.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return True
    return False


# ---------------------------------------------------------------------------
# Class 1 — cmd_dead.py migrated to the W881/W886 import-alias pattern
# ---------------------------------------------------------------------------


class TestCmdDeadDelegated:
    """``cmd_dead._is_test_path`` is now the canonical ``is_test_file``."""

    def test_alias_resolves_to_canonical_helper(self) -> None:
        """Post-W898-followup, ``cmd_dead._is_test_path`` IS
        ``changed_files.is_test_file`` (same object, via import-alias).
        """
        from roam.commands import cmd_dead
        from roam.commands.changed_files import is_test_file

        assert cmd_dead._is_test_path is is_test_file

    def test_alias_classifies_test_paths_canonically(self) -> None:
        """Sanity check: the alias actually returns the canonical
        answers on a fixture that exercises a case the pre-migration
        1-line wrapper would have flagged identically — Python pytest
        layout, Go ``*_test.go``, JS ``*.test.ts``, Apex ``*Test.cls``.

        Each path is a test path under canonical semantics, so the
        alias must return True for all four (regression bound on the
        W898 canonical contract).
        """
        from roam.commands.cmd_dead import _is_test_path

        positives = [
            "tests/test_handler.py",
            "internal/auth/handler_test.go",
            "src/utils/format.test.ts",
            "force-app/main/default/classes/UserTest.cls",
        ]
        for path in positives:
            assert _is_test_path(path) is True, f"W898-followup: cmd_dead alias must classify {path!r} as test"

    def test_alias_returns_false_for_empty_and_falsy(self) -> None:
        """Canonical contract: ``is_test_file("")`` is False, not an
        exception. The 1-line pre-migration wrapper inherited this; the
        alias must too.
        """
        from roam.commands.cmd_dead import _is_test_path

        assert _is_test_path("") is False
        assert _is_test_path(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Class 2 — relations.py intentional non-delegation preserved
# ---------------------------------------------------------------------------


class TestRelationsNarrowerByDesign:
    """``relations._is_test_path`` is deliberately narrower than canonical.

    The docstring is load-bearing — broadening would change which
    variables survive ``_filter_import_candidates`` and reshape
    resolved import edges. This class pins that the helper is still
    locally defined AND still returns the narrower answer on a case
    where canonical and local diverge.
    """

    def test_local_helper_still_defined(self) -> None:
        """``relations.py`` still owns a local ``_is_test_path``.

        If this fails, someone deleted the helper without porting the
        narrower semantics to a new shared module. Re-read the
        ``relations._is_test_path`` docstring before deleting it.
        """
        path = repo_root() / "src" / "roam" / "index" / "relations.py"
        assert _file_defines_function(path, "_is_test_path"), (
            "W898-followup regression: relations._is_test_path was "
            "deleted. The helper is deliberately narrower than "
            "changed_files.is_test_file (directory-only, no basename "
            "heuristics) — broadening it changes resolved import "
            "edges. Restore the local helper OR audit every "
            "_filter_import_candidates call-site for the behavioural "
            "widening before deleting."
        )

    def test_local_helper_diverges_from_canonical_on_basename(self) -> None:
        """The local helper rejects basename-only test patterns
        (``conftest.py``, ``test_foo.py`` at repo root) that the
        canonical helper accepts. This divergence is the documented
        behavioural contract — pin it so a future "tidy up" doesn't
        silently align them.
        """
        from roam.commands.changed_files import is_test_file
        from roam.index.relations import _is_test_path as relations_is_test

        # Basename-only conftest at module root: canonical says yes
        # (it's a pytest fixture file), but relations.py says no
        # because relations only looks at directory components.
        path = "conftest.py"
        assert is_test_file(path) is True, "canonical should classify root conftest.py as test"
        assert relations_is_test(path) is False, (
            "relations._is_test_path is documented to be directory-only "
            "and must NOT match basename-only conftest.py — see "
            "docstring rationale. If this assertion flips, the helper "
            "has been silently broadened."
        )


# ---------------------------------------------------------------------------
# Class 3 — rerank.py intentional non-delegation preserved
# ---------------------------------------------------------------------------


class TestRerankNarrowerByDesign:
    """``rerank._is_test_path`` is deliberately narrower than canonical.

    The docstring cites the 30-task retrieve bench — broadening the
    pattern set changes recall numbers. This class pins that the
    helper is still locally defined AND still returns the narrower
    answer on a case where canonical accepts and local rejects.
    """

    def test_local_helper_still_defined(self) -> None:
        """``rerank.py`` still owns a local ``_is_test_path``.

        If this fails, someone deleted the helper without re-running
        the 30-task retrieve bench. Re-read the
        ``rerank._is_test_path`` docstring + the ``_test_file_penalty``
        Magnitude rationale before deleting.
        """
        path = repo_root() / "src" / "roam" / "retrieve" / "rerank.py"
        assert _file_defines_function(path, "_is_test_path"), (
            "W898-followup regression: rerank._is_test_path was "
            "deleted. The helper is deliberately narrower than "
            "changed_files.is_test_file (no conftest.py, no "
            "_test.java) — broadening it changes retrieve recall "
            "numbers tuned against the 30-task bench. Restore the "
            "local helper OR re-run the bench and re-tune the "
            "_test_file_penalty magnitude before deleting."
        )

    def test_local_helper_diverges_from_canonical_on_conftest(self) -> None:
        """The local rerank helper rejects ``conftest.py`` (no basename
        rule for it) while canonical accepts. This divergence is the
        documented bench-tuned contract — pin it.

        Note: rerank's call-sites pre-normalise the path to forward
        slashes + lowercased; ``conftest.py`` is already in that form.
        """
        from roam.commands.changed_files import is_test_file
        from roam.retrieve.rerank import _is_test_path as rerank_is_test

        path = "src/utils/conftest.py"
        assert is_test_file(path) is True, "canonical should classify conftest.py as test (basename rule)"
        assert rerank_is_test(path) is False, (
            "rerank._is_test_path is documented to be narrower than "
            "canonical (no conftest.py rule). If this assertion flips, "
            "the helper has been silently broadened and the 30-task "
            "bench recall numbers in the _test_file_penalty docstring "
            "are now stale."
        )


# ---------------------------------------------------------------------------
# Class 4 — Drift guard on cmd_dead (the ONLY file where the local
# wrapper was removed; if a future agent re-adds it, the import alias
# is silently shadowed and the clone returns).
# ---------------------------------------------------------------------------


class TestCmdDeadDriftGuard:
    """No future agent silently re-adds ``def _is_test_path`` to cmd_dead."""

    def test_cmd_dead_does_not_redefine_is_test_path_locally(self) -> None:
        """AST-scan ``cmd_dead.py``: assert no top-level ``def
        _is_test_path`` exists. The post-W898-followup file relies on
        the ``import is_test_file as _is_test_path`` alias; a local
        ``def`` would shadow it and silently reintroduce the clone.
        """
        path = repo_root() / "src" / "roam" / "commands" / "cmd_dead.py"
        assert not _file_defines_function(path, "_is_test_path"), (
            "W898-followup drift: cmd_dead.py re-introduced a local "
            "def _is_test_path. The post-migration file relies on "
            "the W881/W886 import-alias pattern "
            "(``from roam.commands.changed_files import is_test_file "
            "as _is_test_path``). A local def shadows the alias and "
            "re-creates the clone the W898 audit flagged. Delete the "
            "local def — the alias already provides the name."
        )


# ---------------------------------------------------------------------------
# Class 5 — Cycle-verification (W907 discipline)
# ---------------------------------------------------------------------------


class TestNoImportCycleIntroduced:
    """Verify no import cycle exists between the migrated module and
    ``commands.changed_files`` — W907 "Verify the cycle before hedging".

    cmd_dead.py is in ``roam.commands`` so it imports a sibling
    (``commands.changed_files``) — by construction no cycle is
    possible at the package boundary. This test runs the actual
    import in a fresh subprocess interpreter to prove it.
    """

    def test_cmd_dead_imports_in_fresh_interpreter(self) -> None:
        """Spawn a fresh interpreter and import ``cmd_dead`` then
        ``changed_files`` (and the reverse). Both orders must succeed
        with no ``ImportError`` / ``RecursionError``.
        """
        root = repo_root()
        for stmt in (
            # Order A: cmd_dead first.
            "from roam.commands import cmd_dead; "
            "from roam.commands.changed_files import is_test_file; "
            "assert cmd_dead._is_test_path is is_test_file",
            # Order B: changed_files first.
            "from roam.commands.changed_files import is_test_file; "
            "from roam.commands import cmd_dead; "
            "assert cmd_dead._is_test_path is is_test_file",
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
        """In-process variant: cached re-imports must not raise. This
        is the cheap CI-time check; the subprocess test above is the
        stronger cold-start check.
        """
        importlib.import_module("roam.commands.cmd_dead")
        importlib.import_module("roam.commands.changed_files")
        importlib.import_module("roam.commands.changed_files")
        importlib.import_module("roam.commands.cmd_dead")
