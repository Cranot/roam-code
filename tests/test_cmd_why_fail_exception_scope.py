from __future__ import annotations

import ast
from pathlib import Path

_MODULE = Path("src/roam/commands/cmd_why_fail.py")


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_why_fail_bfs_catches_only_missing_networkx_nodes() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))

    bfs_handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        call_names = [_name(call.func) for stmt in node.body for call in ast.walk(stmt) if isinstance(call, ast.Call)]
        if "nx.single_source_shortest_path_length" in call_names:
            bfs_handlers.extend(node.handlers)

    assert [_name(handler.type) for handler in bfs_handlers] == ["nx.NodeNotFound"]
