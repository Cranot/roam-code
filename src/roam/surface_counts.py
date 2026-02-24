"""Utilities for reconciling public surface-area counts.

This module parses source files directly so count checks do not depend on
runtime imports or optional dependencies.
"""

from __future__ import annotations

import ast
import json
from collections import Counter, defaultdict
from pathlib import Path


def _repo_root() -> Path:
    """Find the repository root by walking up until src/roam exists."""
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        cli_path = parent / "src" / "roam" / "cli.py"
        mcp_path = parent / "src" / "roam" / "mcp_server.py"
        if cli_path.exists() and mcp_path.exists():
            return parent
    raise RuntimeError("Could not locate repository root from surface_counts.py")


def _load_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_assignment(module: ast.Module, name: str):
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise KeyError(f"Assignment '{name}' not found")


def cli_commands() -> dict[str, tuple[str, str]]:
    """Return raw CLI command registration from `_COMMANDS`."""
    cli_path = _repo_root() / "src" / "roam" / "cli.py"
    module = _load_ast(cli_path)
    commands = _literal_assignment(module, "_COMMANDS")
    if not isinstance(commands, dict):
        raise TypeError("_COMMANDS is not a dict literal")
    return commands


def canonical_cli_commands() -> list[str]:
    """Return canonical command names (aliases collapsed to one primary name)."""
    by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, target in cli_commands().items():
        if not isinstance(name, str):
            continue
        if not isinstance(target, (tuple, list)) or len(target) != 2:
            continue
        mod_name, attr_name = target
        if not isinstance(mod_name, str) or not isinstance(attr_name, str):
            continue
        by_target[(mod_name, attr_name)].append(name)

    canonical = [sorted(names)[0] for names in by_target.values() if names]
    return sorted(canonical)


def mcp_tool_names() -> list[str]:
    """Return all MCP tool names registered via `@_tool(name=...)` decorators."""
    mcp_path = _repo_root() / "src" / "roam" / "mcp_server.py"
    module = _load_ast(mcp_path)
    names: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        names.append(kw.value.value)
    return sorted(set(names))


def cli_surface_counts() -> dict:
    """Return CLI command counts from `_COMMANDS` in `src/roam/cli.py`."""
    commands = cli_commands()

    by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, target in commands.items():
        if not isinstance(name, str):
            continue
        if not isinstance(target, (tuple, list)) or len(target) != 2:
            continue
        mod_name, attr_name = target
        if not isinstance(mod_name, str) or not isinstance(attr_name, str):
            continue
        by_target[(mod_name, attr_name)].append(name)

    alias_groups = sorted(
        (sorted(names) for names in by_target.values() if len(names) > 1),
        key=lambda names: (names[0], len(names)),
    )
    alias_names = sum(len(group) - 1 for group in alias_groups)

    return {
        "command_names": len(commands),
        "canonical_commands": len(commands) - alias_names,
        "alias_names": alias_names,
        "alias_groups": alias_groups,
    }


def _is_tool_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_tool"
    )


def mcp_surface_counts() -> dict:
    """Return MCP tool counts from `_tool(name=...)` decorators and presets."""
    mcp_path = _repo_root() / "src" / "roam" / "mcp_server.py"
    module = _load_ast(mcp_path)
    core_tools = _literal_assignment(module, "_CORE_TOOLS")
    if not isinstance(core_tools, set):
        raise TypeError("_CORE_TOOLS is not a set literal")

    decorated_tool_names: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        decorated_tool_names.append(kw.value.value)

    duplicates = sorted(name for name, c in Counter(decorated_tool_names).items() if c > 1)
    return {
        "core_tools": len(core_tools),
        "registered_tools": len(set(decorated_tool_names)),
        "duplicate_tool_names": duplicates,
    }


def collect_surface_counts() -> dict:
    return {"cli": cli_surface_counts(), "mcp": mcp_surface_counts()}


def main() -> None:
    print(json.dumps(collect_surface_counts(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
