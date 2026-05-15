"""Utilities for reconciling public surface-area counts.

This module parses source files directly so count checks do not depend on
runtime imports or optional dependencies.
"""

from __future__ import annotations

import ast
import json
import sys
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
    """Return raw CLI command registration from `_COMMANDS`.

    Scope: core roam commands only. Plugin commands registered at runtime
    via ``ctx.register_command()`` are NOT included — this loader is
    AST-only by design so doc-headline counts reflect what ships with
    ``pip install roam-code``.
    """
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

    canonical = []
    for (mod_name, attr_name), names in by_target.items():
        if not names:
            continue
        # Prefer the name matching the Click function attr (primary), else first alphabetically
        primary = attr_name.replace("_", "-")
        canonical.append(primary if primary in names else sorted(names)[0])
    return sorted(canonical)


def mcp_tool_names() -> list[str]:
    """Return all MCP tool names registered via `@_tool(name=...)` decorators.

    W444 fail-loud (W531 discipline): historically this helper returned
    ``sorted(set(names))`` which silently collapsed duplicate ``@_tool(name=...)``
    decorations across the source file. Callers then treated the collapsed
    list as the truth, hiding the W432-class duplicate-registration bug from
    every downstream consumer (README count, wrapper-coverage test, surface
    count). Now we raise ``ValueError`` with the duplicate entries instead;
    combined with the runtime smoke test ``tests/test_w444_mcp_tool_names_no_dedupe.py``
    this is defense-in-depth against duplicate-registration regressions.
    """
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
    duplicates = sorted(name for name, c in Counter(names).items() if c > 1)
    if duplicates:
        raise ValueError(
            f"duplicate @_tool(name=...) decorations in mcp_server.py: {duplicates}"
        )
    return sorted(names)


def mcp_tool_descriptions() -> list[tuple[str, str]]:
    """Return ``(tool_name, description)`` for every ``@_tool(...)`` decoration.

    The description is pulled from the decorator's ``description=`` kwarg when
    it is a literal string (or a tuple of string fragments concatenated by the
    parser, which ``ast.literal_eval`` handles transparently). When the
    decorator omits ``description=`` the helper falls back to the wrapped
    function's docstring first sentence (single-line, period-stripped). When
    neither is available the entry carries an empty string so the caller can
    decide on a placeholder.

    Sorted alphabetically by tool name. Used by
    ``dev/build_readme_counts.py`` to auto-generate the README MCP tool
    table — eliminates the recurring drift class where every wrapper batch
    landed without a README update (W299..W306, W449).
    """
    mcp_path = _repo_root() / "src" / "roam" / "mcp_server.py"
    module = _load_ast(mcp_path)
    entries: dict[str, str] = {}
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            name: str | None = None
            description: str | None = None
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    name = kw.value.value
                elif kw.arg == "description":
                    try:
                        val = ast.literal_eval(kw.value)
                        if isinstance(val, str):
                            description = val
                    except Exception:
                        description = None
            if name is None:
                continue
            if not description:
                # Fall back to the wrapped function's docstring first sentence.
                doc = ast.get_docstring(node) or ""
                if doc:
                    first = doc.strip().split("\n", 1)[0].strip()
                    # Trim at first period for a one-liner; keep up to ~200 chars.
                    if "." in first:
                        first = first.split(".", 1)[0].strip() + "."
                    description = first[:240]
            entries[name] = (description or "").strip()
    return sorted(entries.items())


def mcp_tool_decorations() -> list[tuple[str, str, int]]:
    """Return every `@_tool(name=...)` decoration as (tool_name, def_name, lineno).

    Distinct from :func:`mcp_tool_names` (which dedupes into a sorted set):
    this preserves the raw list so callers can detect duplicate-name
    registrations. Two ``@_tool`` decorations with the same ``name`` kwarg
    silently overwrite ``_TOOL_METADATA[name]`` and produce undefined
    dispatch behaviour under FastMCP — see W432.
    """
    mcp_path = _repo_root() / "src" / "roam" / "mcp_server.py"
    module = _load_ast(mcp_path)
    decorations: list[tuple[str, str, int]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        decorations.append(
                            (kw.value.value, node.name, node.lineno)
                        )
    return decorations


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
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_tool"


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
    sys.stdout.write(json.dumps(collect_surface_counts(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
