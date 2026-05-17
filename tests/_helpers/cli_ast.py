"""Shared AST-loading helper for source-file inspection tests.

W714 background. The W702 / W713 / W757 audits surfaced a 20+ site
duplication of the same two-line incantation across the test suite::

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

and a slightly more substantial duplication of the "find a
module-level constant assignment by name and return its
``ast.AST`` value node" loop. The duplication is benign individually
but invites the W506 / W518 / W536 failure mode: when the canonical
read shape needs to change (e.g. to track source-line offsets, to
swap in ``ast.parse(..., type_comments=True)``, or to add a
``filename=`` argument everywhere for better tracebacks), the change
has to land at 20+ call sites in lockstep.

This helper consolidates the two recurring shapes:

- ``load_ast(path)`` — read the file in UTF-8 and return the parsed
  ``ast.Module``, with ``filename=str(path)`` set so any
  ``SyntaxError`` carries the on-disk location.
- ``find_module_constant(tree_or_path, name)`` — walk the module
  body and return the ``ast.AST`` value node for the first
  module-level ``name = ...`` or ``name: T = ...`` assignment, or
  ``None`` when the name is not assigned at module scope. Mirrors
  the pattern in ``test_readme_surface_consistency.py`` and
  ``test_sarif_consumers_schema.py``.

Both helpers are deliberately small. They are NOT a general-purpose
AST framework — they cover the exact two shapes that recur in the
drift-guard test family, and nothing more. Adding a third helper
here without a real 3+-site duplication elsewhere is a YAGNI
violation; document the new pattern at the call site instead.

Public API (two names; pinned by a drift guard in
``tests/test_cli_ast_helper.py`` when one lands):

- ``load_ast(path: Path) -> ast.Module``
- ``find_module_constant(source: Path | ast.Module, name: str) -> ast.AST | None``
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Union

__all__ = ["load_ast", "find_module_constant"]


def load_ast(path: Path) -> ast.Module:
    """Read ``path`` as UTF-8 source and return the parsed ``ast.Module``.

    Equivalent to::

        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    The ``filename=`` argument is set so any ``SyntaxError`` raised
    by the parser carries the on-disk location -- callers passing a
    synthetic path (e.g. a ``tmp_path / "offender.py"``) get a
    useful traceback for free.

    Raises whatever ``Path.read_text`` or ``ast.parse`` raise; no
    swallowing. Tests that want to assert "this file is parseable"
    should call this and let the exception propagate.
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def find_module_constant(
    source: Union[Path, ast.Module],
    name: str,
) -> ast.AST | None:
    """Return the value node of a module-level ``name = ...`` assignment.

    Accepts either a ``Path`` (parsed via ``load_ast`` first) or an
    already-parsed ``ast.Module``. Returns the ``ast.AST`` value node
    for the first match, or ``None`` when ``name`` is not assigned at
    module scope.

    Handles both plain assignments::

        FOO = ("a", "b")

    and annotated assignments::

        FOO: tuple[str, ...] = ("a", "b")

    Only single-target assignments are considered (mirrors the
    W22.3 drift-guard discipline: dynamic / multi-target / dynamic
    construction defeats the AST audit anyway).

    For ``ast.Assign`` nodes with multiple targets (``A = B = ...``),
    each target is checked; the value is returned if any target
    matches ``name``.
    """
    if isinstance(source, Path):
        module = load_ast(source)
    else:
        module = source

    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    return None
