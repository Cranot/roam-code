"""Count-drift lint for the smell-detector registry (W862; W942 pivot).

Three places in ``src/roam/catalog/smells.py`` name a detector count and
must stay in sync:

1. Module docstring: ``"<N> deterministic detectors"``.
2. ``run_all_detectors()`` docstring: ``"Run all <N> smell detectors"``.
3. The decorator-driven registry behind
   ``roam.catalog.registry.all_detectors()``.

W856 caught this drift accidentally (24 vs 25). This standing test
catches the next drift at PR time. AST-based -- robust against
formatting changes.

W942: post-W941, ``ALL_DETECTORS`` is itself a derived view over
``all_detectors()``, so comparing the docstring count to
``len(ALL_DETECTORS)`` would compare the registry to itself indirectly.
The lint now reads directly from
``roam.catalog.registry.all_detectors()`` -- the canonical
source-of-truth -- so a future refactor that drops or renames the
``ALL_DETECTORS`` derived view cannot silently disarm the drift guard.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import roam.catalog.smells as smells_module
from roam.catalog.registry import all_detectors

_SMELLS_PATH = Path(smells_module.__file__).resolve()
_MODULE_DOCSTRING_PATTERN = re.compile(r"(\d+) deterministic detectors")
_RUN_ALL_DOCSTRING_PATTERN = re.compile(r"Run all (\d+) smell detectors")


def _load_smells_ast() -> tuple[ast.Module, str]:
    """Parse smells.py and return (module-AST, source-text)."""
    source = _SMELLS_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(_SMELLS_PATH))
    return module, source


def _find_run_all_detectors(module: ast.Module) -> ast.FunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run_all_detectors":
            return node
    raise AssertionError(
        f"Could not find `def run_all_detectors(...)` in {_SMELLS_PATH}. "
        "The count-drift lint depends on this function's docstring."
    )


def _extract_single_count(
    text: str,
    pattern: re.Pattern[str],
    *,
    location: str,
) -> int:
    """Find exactly one match for `pattern` in `text` and return its int.

    Failing with a clear message is itself part of the drift signal:
    if a docstring loses its count marker, we want the test to fail
    loudly rather than silently pass on a vacuous truth.
    """
    matches = pattern.findall(text)
    if not matches:
        raise AssertionError(
            f"{location}: docstring lost its count marker matching "
            f"`{pattern.pattern}`. Restore the phrase with the current "
            f"registry size ({len(list(all_detectors()))}) so the "
            f"count-drift lint can keep guarding it."
        )
    if len(matches) > 1:
        raise AssertionError(
            f"{location}: docstring has {len(matches)} matches for "
            f"`{pattern.pattern}` (expected exactly one). Found counts: "
            f"{matches}. Collapse the duplicates so a single number "
            f"is the source of truth."
        )
    return int(matches[0])


def _entry_names() -> list[str]:
    return [smell_id for smell_id, _fn in all_detectors()]


def test_module_docstring_count_matches_registry() -> None:
    module, _source = _load_smells_ast()
    module_docstring = ast.get_docstring(module)
    assert module_docstring is not None, (
        f"{_SMELLS_PATH}: module docstring is missing. "
        "The count-drift lint reads `<N> deterministic detectors` from it."
    )
    # Line number of the docstring's string literal (body[0] is Expr(Constant(...))).
    first_stmt = module.body[0]
    docstring_line = getattr(first_stmt, "lineno", 1)

    docstring_count = _extract_single_count(
        module_docstring,
        _MODULE_DOCSTRING_PATTERN,
        location=f"{_SMELLS_PATH}:{docstring_line} (module docstring)",
    )
    registry_count = len(list(all_detectors()))

    if docstring_count != registry_count:
        raise AssertionError(
            f"smells.py module docstring claims {docstring_count} "
            f"detectors but the decorator-driven registry has "
            f"{registry_count}.\n"
            f"   Fix: bump the docstring at "
            f"{_SMELLS_PATH}:{docstring_line} OR remove the @detector "
            f"annotation for the new entry in src/roam/catalog/smells.py.\n"
            f"   Found counts: docstring={docstring_count}, "
            f"registry={registry_count}, entries={_entry_names()}"
        )


def test_run_all_detectors_docstring_count_matches_registry() -> None:
    module, _source = _load_smells_ast()
    func = _find_run_all_detectors(module)
    func_docstring = ast.get_docstring(func)
    assert func_docstring is not None, (
        f"{_SMELLS_PATH}: `run_all_detectors` has no docstring. "
        "The count-drift lint reads `Run all <N> smell detectors` from it."
    )
    docstring_line = getattr(func, "lineno", 1)

    docstring_count = _extract_single_count(
        func_docstring,
        _RUN_ALL_DOCSTRING_PATTERN,
        location=(
            f"{_SMELLS_PATH}:{docstring_line} "
            "(run_all_detectors docstring)"
        ),
    )
    registry_count = len(list(all_detectors()))

    if docstring_count != registry_count:
        raise AssertionError(
            f"smells.py `run_all_detectors` docstring claims "
            f"{docstring_count} detectors but the decorator-driven "
            f"registry has {registry_count}.\n"
            f"   Fix: bump the docstring at "
            f"{_SMELLS_PATH}:{docstring_line} OR remove the @detector "
            f"annotation for the new entry in src/roam/catalog/smells.py.\n"
            f"   Found counts: docstring={docstring_count}, "
            f"registry={registry_count}, entries={_entry_names()}"
        )


def test_both_docstring_counts_are_identical() -> None:
    """Cross-check the two docstrings agree with each other.

    If the registry test fails AND this passes, the registry diverged
    from BOTH docstrings (single bump needed). If both registry tests
    pass and this fails, something is structurally impossible — failure
    here is a meta-bug in the lint itself.
    """
    module, _source = _load_smells_ast()
    module_docstring = ast.get_docstring(module) or ""
    func = _find_run_all_detectors(module)
    func_docstring = ast.get_docstring(func) or ""

    module_count = _extract_single_count(
        module_docstring,
        _MODULE_DOCSTRING_PATTERN,
        location=f"{_SMELLS_PATH} (module docstring)",
    )
    func_count = _extract_single_count(
        func_docstring,
        _RUN_ALL_DOCSTRING_PATTERN,
        location=f"{_SMELLS_PATH} (run_all_detectors docstring)",
    )

    if module_count != func_count:
        raise AssertionError(
            f"smells.py docstrings disagree: module docstring says "
            f"{module_count} but run_all_detectors says {func_count}. "
            f"Pick one number (the current registry size is "
            f"{len(list(all_detectors()))}) and update both call sites."
        )
