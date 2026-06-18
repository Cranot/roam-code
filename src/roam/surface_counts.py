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
    """Find the repository root by walking up until src/roam exists.

    Source-tree-only helper. Used by dev scripts / tests that need the
    project root (e.g. to resolve ``dev/compatibility-baseline.json``).
    Runtime helpers MUST use :func:`_package_file` instead — wheel
    installs have no ``src/`` prefix and this walk will fail there.
    """
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        cli_path = parent / "src" / "roam" / "cli.py"
        mcp_path = parent / "src" / "roam" / "mcp_server.py"
        if cli_path.exists() and mcp_path.exists():
            return parent
    raise RuntimeError("Could not locate repository root from surface_counts.py")


def _package_file(filename: str) -> Path:
    """Resolve a file inside the installed ``roam`` package, wheel-safe.

    Uses ``importlib.resources.files("roam")`` so this works identically
    in three layouts:

    1. ``pip install roam-code`` wheel install — ``site-packages/roam/<filename>``.
    2. ``pip install -e .`` editable install — ``src/roam/<filename>``.
    3. Source-tree run without install (``PYTHONPATH=src`` or pytest from
       repo root) — falls back to the dev-tree walk via :func:`_repo_root`.

    Pre-fix the W420 cascade migrated ``cmd_surface`` / ``cmd_compatibility``
    / ``cmd_doctor`` / ``cmd_capabilities`` to call ``cli_commands()`` /
    ``mcp_tool_names()`` here. Those helpers walked ``Path(__file__).parents``
    looking for ``src/roam/cli.py`` — a path that does not exist in a
    wheel install. Result: ``roam --json surface`` and ``roam --json
    capabilities`` raised ``RuntimeError`` for every PyPI user. Mirrors
    the W554 / W664 / W668 wheel-safe resource pattern already used by
    ``cmd_evidence_oscal`` and ``cmd_taint``.
    """
    try:
        from importlib.resources import files as _resource_files

        # ``roam`` is a regular package (has ``__init__.py``), so ``files()``
        # returns a concrete on-disk path — no ``as_file()`` / temp-extraction
        # hazard (W643/W668 lesson).
        package_resource = _resource_files("roam") / filename
        resolved = Path(str(package_resource))
        if resolved.exists():
            return resolved
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass

    # Fallback: source-tree layout without install (e.g. running tests with
    # ``PYTHONPATH=src`` before ``pip install -e .`` has run).
    dev_path = _repo_root() / "src" / "roam" / filename
    if dev_path.exists():
        return dev_path
    raise RuntimeError(f"Could not locate {filename} via importlib.resources or dev-tree walk")


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
    cli_path = _package_file("cli.py")
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
    mcp_path = _package_file("mcp_server.py")
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
        raise ValueError(f"duplicate @_tool(name=...) decorations in mcp_server.py: {duplicates}")
    return sorted(names)


_MCP_TOOL_ALIASES: dict[str, set[str]] = {
    "annotate-symbol": {"roam_annotate_symbol"},
    "bisect": {"roam_bisect_blame"},
    "breaking": {"roam_breaking_changes"},
    "budget": {"roam_budget_check"},
    "capsule": {"roam_capsule_export"},
    "cga": {"roam_cga_emit", "roam_cga_verify"},
    "churn": {"roam_weather"},
    "complexity": {"roam_complexity_report"},
    "context": {"roam_context", "roam_ws_context"},
    "dead": {"roam_dead_code"},
    "digest": {"roam_trends"},
    "file": {"roam_file_info"},
    "findings": {"roam_findings_count", "roam_findings_list", "roam_findings_show"},
    "oracle": {
        "roam_oracle_is_clone_of",
        "roam_oracle_is_reachable_from_entry",
        "roam_oracle_is_test_only",
        "roam_oracle_route_exists",
        "roam_oracle_symbol_exists",
    },
    "refs": {"roam_uses"},
    "review": {"roam_review_change"},
    "rules": {"roam_check_rules", "roam_rules_check", "roam_rules_validate"},
    "search": {"roam_search_semantic", "roam_search_symbol"},
    "snapshot": {"roam_trends"},
    "trend": {"roam_trends"},
    "trends": {"roam_trends"},
    "understand": {"roam_understand", "roam_ws_understand"},
    "uses": {"roam_uses"},
    "vulns": {"roam_vuln_map", "roam_vuln_reach"},
    "weather": {"roam_weather"},
}

_MCP_TOOL_SUFFIXES: tuple[str, ...] = (
    "blame",
    "changes",
    "check",
    "code",
    "emit",
    "export",
    "info",
    "plan",  # W421: recognise subcommand-group wrappers like roam_fleet_plan
    "report",
    "validate",
    "verify",
)


def mcp_candidate_tool_names(command_name: str) -> set[str]:
    """Return plausible MCP wrapper names for a CLI command.

    Used by both ``roam surface`` and the MCP-wrapper coverage audit. Keep
    the non-obvious aliases here so those two surfaces cannot drift apart
    on names like ``dead`` -> ``roam_dead_code``.
    """
    base = command_name.replace("-", "_")
    candidates = {f"roam_{base}"}
    candidates.update(f"roam_{base}_{suffix}" for suffix in _MCP_TOOL_SUFFIXES)
    candidates.update(_MCP_TOOL_ALIASES.get(command_name, set()))
    return candidates


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
    mcp_path = _package_file("mcp_server.py")
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
                    except (TypeError, ValueError):
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
    mcp_path = _package_file("mcp_server.py")
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
                        decorations.append((kw.value.value, node.name, node.lineno))
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


def _eval_set_expr(node: ast.expr, env: dict[str, set[str]]) -> set[str]:
    """Evaluate a static set expression from `_PRESETS` against a name env.

    Supports the exact shapes used in ``mcp_server._PRESETS``:

    - ``set()`` -> empty set (used for the ``full`` sentinel)
    - ``_CORE_TOOLS.copy()`` -> a copy of ``env["_CORE_TOOLS"]``
    - ``{"a", "b", ...}`` -> a Set literal
    - ``_CORE_TOOLS | {"a", "b"}`` -> union BinOp combining the above

    Raises ``ValueError`` on any unsupported shape so a future ``_PRESETS``
    rewrite that uses, say, list comprehensions or imported names fails
    loudly instead of silently producing wrong counts.
    """
    if isinstance(node, ast.Set):
        return {ast.literal_eval(elt) for elt in node.elts}
    if isinstance(node, ast.Call):
        # set() with no args -> empty set sentinel for the "full" preset
        if isinstance(node.func, ast.Name) and node.func.id == "set" and not node.args and not node.keywords:
            return set()
        # `<Name>.copy()` -> copy of the named set
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "copy"
            and isinstance(node.func.value, ast.Name)
            and not node.args
            and not node.keywords
        ):
            ref = node.func.value.id
            if ref not in env:
                raise ValueError(f"unknown set name in _PRESETS: {ref!r}")
            return set(env[ref])
        raise ValueError(f"unsupported call in _PRESETS: {ast.dump(node)}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _eval_set_expr(node.left, env) | _eval_set_expr(node.right, env)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"unknown set name in _PRESETS: {node.id!r}")
        return set(env[node.id])
    raise ValueError(f"unsupported expression in _PRESETS: {ast.dump(node)}")


def mcp_preset_counts() -> dict[str, int]:
    """Return ``{preset_name: tool_count}`` parsed from ``_PRESETS`` in mcp_server.py.

    AST-only; no runtime import of ``roam.mcp_server`` (which transitively
    requires every command module and is fragile on fresh installs). The
    ``full`` preset is the empty-set sentinel meaning "no filter / all
    tools" — we resolve it to the actual total so consumers don't see a
    misleading 0. Closed enumeration (CLAUDE.md Constraint 8): the keys
    are exactly the literal keys of ``_PRESETS``.
    """
    mcp_path = _package_file("mcp_server.py")
    module = _load_ast(mcp_path)
    core_tools = _literal_assignment(module, "_CORE_TOOLS")
    if not isinstance(core_tools, set):
        raise TypeError("_CORE_TOOLS is not a set literal")
    # _WORKFLOW_TOOLS is the optional sibling set holding tools that USED
    # to live in core (pre-2026-05-24 shrink) and now live in the
    # specialised presets via the `_CORE_TOOLS | _WORKFLOW_TOOLS | {extras}`
    # union. Absent on older mcp_server.py revisions; treat as empty set
    # in that case so the AST evaluator stays backward-compatible.
    try:
        workflow_tools = _literal_assignment(module, "_WORKFLOW_TOOLS")
    except (KeyError, ValueError):
        workflow_tools = set()
    if not isinstance(workflow_tools, set):
        raise TypeError("_WORKFLOW_TOOLS is not a set literal")

    # Locate the `_PRESETS = {...}` (or annotated `_PRESETS: ... = {...}`) Dict node.
    presets_dict: ast.Dict | None = None
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_PRESETS":
                    if isinstance(node.value, ast.Dict):
                        presets_dict = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "_PRESETS"
                and node.value is not None
                and isinstance(node.value, ast.Dict)
            ):
                presets_dict = node.value
        if presets_dict is not None:
            break
    if presets_dict is None:
        raise KeyError("`_PRESETS` dict not found in mcp_server.py")

    # Build the static name environment the preset values reference.
    decorated = set()
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue
            for kw in decorator.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    decorated.add(kw.value.value)
    total = len(decorated)
    env: dict[str, set[str]] = {
        "_CORE_TOOLS": set(core_tools),
        "_WORKFLOW_TOOLS": set(workflow_tools),
    }

    counts: dict[str, int] = {}
    for key_node, value_node in zip(presets_dict.keys, presets_dict.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            raise ValueError(f"non-string key in _PRESETS: {ast.dump(key_node)}")
        name = key_node.value
        members = _eval_set_expr(value_node, env)
        # `full` is the empty-set "no filter / all tools" sentinel — match
        # the runtime resolution in mcp_server / cmd_surface so consumers
        # see the actual total, not 0.
        counts[name] = len(members) if members else total
    return counts


def mcp_surface_counts() -> dict:
    """Return MCP tool counts from `_tool(name=...)` decorators and presets."""
    mcp_path = _package_file("mcp_server.py")
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
        "preset_counts": mcp_preset_counts(),
    }


def collect_surface_counts() -> dict:
    return {"cli": cli_surface_counts(), "mcp": mcp_surface_counts()}


def main() -> None:
    sys.stdout.write(json.dumps(collect_surface_counts(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
