"""Shared file-reading and AST-parsing helpers for world-model detectors.

Used by: ``none_eq_comparison``, ``redundant_boolean_return``,
``restore_loss`` (``_read_source`` only), ``self_comparison``,
``unchecked_result``, ``unreachable_after_return``, ``unreachable_except``.

Centralising here removes duplicate copies and ensures consistent behaviour:

* ``_read_source`` always uses ``read_text`` with ``errors="replace"`` and
  returns ``("", [])`` on any I/O failure — callers gate on ``not lines``.
* ``_parse_function`` always dedents the slice and catches
  ``(SyntaxError, ValueError)`` before returning the first function
  declaration or ``None``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from roam.observability import log_swallowed


def _read_source(repo_root: Path, rel_path: str) -> tuple[str, list[str]]:
    """Read one source file, returning (text, lines) or ("", []) on failure."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return "", []
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, text.splitlines(keepends=True)
    except OSError as exc:
        log_swallowed(f"world_model:body_read:{rel_path}", exc)
        return "", []


def _parse_function(body_text: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Parse a source slice and return its outer function declaration."""
    try:
        tree = ast.parse(textwrap.dedent(body_text))
    except (SyntaxError, ValueError):
        return None
    return next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
