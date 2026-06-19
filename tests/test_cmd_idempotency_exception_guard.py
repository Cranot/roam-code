"""Regression guard for ``roam idempotency`` root-resolution fallback."""

from __future__ import annotations

import ast

from tests._helpers.repo_root import repo_root


def _caught_names(expr: ast.expr | None) -> set[str]:
    if expr is None:
        return {"<bare>"}
    if isinstance(expr, ast.Name):
        return {expr.id}
    if isinstance(expr, ast.Attribute):
        return {expr.attr}
    if isinstance(expr, ast.Tuple):
        names: set[str] = set()
        for item in expr.elts:
            names.update(_caught_names(item))
        return names
    return set()


def test_idempotency_command_uses_typed_exception_fallback() -> None:
    path = repo_root() / "src" / "roam" / "commands" / "cmd_idempotency.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    broad_handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _caught_names(node.type)
        if "<bare>" in names or names.intersection({"Exception", "BaseException"}):
            broad_handlers.append(f"{path.relative_to(repo_root()).as_posix()}:{node.lineno}")

    assert broad_handlers == []
