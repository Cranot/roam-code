"""Drift guard: the pre-push structural-gate bundle stays wired.

``scripts/prepush_check.py`` runs the repo-wide structural drift-guards
locally before ``git push`` — the gate that would have prevented this
session's ~14 CI fix-forward cascade (design:
``(internal memo)``). The value of that gate is
entirely in the SET of guards it bundles. If a guard test is renamed or
deleted and silently drops out of the FAST tuple, the pre-push gate quietly
stops catching that drift class — exactly the silent-rot failure mode the
"ship a structural guard with the campaign" rule warns against.

This module pins the contract via pure AST inspection (no subprocess, no
index build) so it runs in the FAST bundle itself:

1. ``scripts/prepush_check.py`` exists and parses as valid Python.
2. It exposes the ``FAST_PYTEST_GUARDS`` and ``FULL_PYTEST_GUARDS`` tuples,
   a ``main`` callable, and a ``repo_root`` resolver.
3. Every guard named in either tuple resolves to a real ``tests/`` file —
   so a renamed/deleted guard fails this test instead of silently leaving
   the bundle.
4. The expected high-frequency FAST guards (the dominant fix-forward class)
   are all present.
"""

from __future__ import annotations

import ast

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepush_check.py"
TESTS_DIR = REPO_ROOT / "tests"

# The FAST guards the design memo's back-test proved catch the dominant
# structural-drift fix-forward class. The script may carry MORE than these,
# but never fewer — dropping one of these is the regression this guards.
_EXPECTED_FAST_GUARDS = frozenset(
    {
        "test_w547_severity_drift.py",
        "test_law4_lint.py",
        "test_law4_anchor_counts.py",
        "test_w588_fragile_path_drift.py",
        "test_w662_bare_except_drift.py",
        "test_optional_imports_guarded.py",
        "test_findings_detector_count_drift.py",
        "test_detector_registry.py",
        "test_w444_mcp_tool_names_no_dedupe.py",
        "test_w462_landing_page_tool_count_drift.py",
        "test_mcp_server_card_hash.py",
        "test_compound_recipe_registry.py",
    }
)


def _parse_script() -> ast.Module:
    assert SCRIPT_PATH.exists(), (
        f"Expected the pre-push gate at {SCRIPT_PATH}. This script runs the "
        "structural drift-guards locally before push; without it the ~14 "
        "fix-forward cascade class is uncaught. See "
        "(internal memo)."
    )
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(SCRIPT_PATH))


def _extract_tuple_strings(tree: ast.Module, name: str) -> list[str]:
    """Return the string literals assigned to module-level ``name`` (a tuple)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name) and t.id == name]
        if not targets:
            # Also accept annotated assignment (name: type = (...)).
            continue
        if isinstance(node.value, (ast.Tuple, ast.List)):
            return [
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    # AnnAssign path (FAST_PYTEST_GUARDS: tuple[str, ...] = (...)).
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, (ast.Tuple, ast.List)):
                return [
                    elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    raise AssertionError(f"Could not find a module-level tuple/list literal named {name!r} in {SCRIPT_PATH.name}.")


def test_prepush_script_exists_and_parses() -> None:
    """The script exists and is valid, parseable Python."""
    tree = _parse_script()
    assert isinstance(tree, ast.Module)


def test_prepush_script_exposes_expected_symbols() -> None:
    """The gate exposes the contract symbols other surfaces depend on."""
    tree = _parse_script()
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.add(node.name)
        elif isinstance(node, ast.Assign):
            top_level_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top_level_names.add(node.target.id)

    for required in ("FAST_PYTEST_GUARDS", "FULL_PYTEST_GUARDS", "main", "repo_root"):
        assert required in top_level_names, (
            f"scripts/prepush_check.py must define {required!r}; the .githooks/pre-push "
            f"shim and this drift guard depend on it. Found: {sorted(top_level_names)}"
        )


def test_release_tier_registered() -> None:
    """The pre-tag preflight tier (--release) must stay wired.

    CONTRIBUTING.md + .githooks/pre-push document `prepush_check.py --release`
    as the gate that runs what CI runs before a tag. If the flag is renamed or
    dropped, that documented preflight silently no-ops — the exact gap that let
    the 13.8.0 tag take 8 sequential CI rounds. Import-light source assertion
    (mirrors the other guards in this file — no module import needed).
    """
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for flag in ("--release", "--full", "--fast"):
        assert flag in src, (
            f"scripts/prepush_check.py must register the {flag!r} tier; "
            ".githooks/pre-push + CONTRIBUTING.md document it as a release gate."
        )


def test_fast_bundle_contains_expected_guards() -> None:
    """Every high-frequency FAST guard from the design back-test is bundled."""
    tree = _parse_script()
    fast = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    missing = _EXPECTED_FAST_GUARDS - fast
    assert not missing, (
        f"FAST_PYTEST_GUARDS dropped expected guard(s): {sorted(missing)}. "
        "These are the dominant structural-drift fix-forward class (severity-rank, "
        "LAW-4, fragile-path, card-hash, detector-count, compound-recipe). Re-add "
        "them or update (internal memo) if intentionally removed."
    )


def test_every_bundled_guard_file_exists() -> None:
    """No bundled guard may reference a renamed/deleted test file."""
    tree = _parse_script()
    bundled = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    bundled |= set(_extract_tuple_strings(tree, "FULL_PYTEST_GUARDS"))
    missing = sorted(name for name in bundled if not (TESTS_DIR / name).exists())
    assert not missing, (
        f"prepush_check.py bundles test file(s) that do not exist: {missing}. "
        "A renamed or deleted guard silently drops out of the pre-push bundle. "
        "Update FAST_PYTEST_GUARDS / FULL_PYTEST_GUARDS to the new name."
    )
