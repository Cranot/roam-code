"""Classify runtime/decorator-registered framework entrypoints.

A static call-graph analyser sees a decorator-registered function (an MCP
``@_tool(name="roam_X")`` wrapper, a ``@click.command``, an ``@app.route``,
…) as having ZERO callers: the framework invokes it via its runtime registry,
not via a literal ``foo()`` call inside another extracted symbol. Every consumer
of the call graph that reports "no callers → dead / unused" therefore needs to
recognise these entrypoints and NOT flag them.

This module is the single source of truth for that classification. It was
lifted out of ``commands/cmd_dead.py`` (the W157 dead-code exemption) so that
``uses`` and ``impact`` can consult the SAME classifier instead of each
re-reporting a bare "no consumers" for a live framework entrypoint (a
false-negative on roam's own precision selling point -- ``roam uses onboard``
reported 0 consumers for a live MCP tool). ``cmd_dead`` re-exports the private
names below for backward compatibility with its existing tests.

Currently the only registry modelled is FastMCP's ``@_tool`` roster; extend
``_load_mcp_tool_names`` / add sibling loaders to cover more decorator families.
"""

from __future__ import annotations

import os

# W157 -- runtime-decorator-registered symbols look "dead" to a static
# call-graph analyser because the framework invokes them via the decorator
# registry, not via a literal `foo()` call inside another extracted symbol.
# The MCP server's `@_tool(name="roam_X")` is the worst-offender: 44 of 73
# SAFE-tier findings on roam-code's own registry were MCP wrappers (per the
# W149 dogfood audit). Following the SAFE recommendation would silently
# break the MCP transport.
#
# Source of truth is ``roam.mcp_server._TOOL_METADATA``: every entry there
# is a Python function whose qualified name lives in ``mcp_server.py`` and
# whose framework consumer is FastMCP's runtime tool-registry. Importing it
# at module-load is too expensive for the readonly path (fastmcp may not be
# installed); resolve lazily on first need and cache.
_MCP_TOOL_NAMES_CACHE: frozenset[str] | None = None


def _load_mcp_tool_names() -> frozenset[str]:
    """Return the set of MCP-tool symbol names registered via ``@_tool``.

    Combines TWO sources so the gate matches the symbols-table view:

    1. ``roam.mcp_server._TOOL_METADATA`` keys -- the runtime
       ``name=``-kwarg form (``roam_clean``, ``roam_doctor``, …). This
       catches wrappers where ``@_tool(name="roam_X")`` happens to match
       the Python ``def`` name (about 40/149 wrappers).
    2. AST scan of ``mcp_server.py`` -- collects the Python ``def`` name
       of every function decorated with ``@_tool(...)``, regardless of
       what the ``name=`` kwarg says. This catches the majority of
       wrappers where the def name differs from the registered tool name
       (e.g. ``def search_semantic`` decorated as ``roam_search_semantic``).

    The symbols table stores the Python ``def`` name in
    ``symbols.name`` -- so the AST-derived set is what the callers actually
    need to match against. The ``_TOOL_METADATA`` set is included as a
    defensive belt-and-braces source for the cases where both names coincide.

    Cached after first load so the per-symbol classifier stays O(1).

    Degrades to an empty frozenset when neither source is reachable
    (e.g. roam installed from a wheel without the source file on disk,
    or fastmcp extra missing). An empty set yields the legacy un-seeded
    behaviour -- worst case is pre-W157 false positives, never a crash.
    """
    global _MCP_TOOL_NAMES_CACHE
    if _MCP_TOOL_NAMES_CACHE is not None:
        return _MCP_TOOL_NAMES_CACHE

    collected: set[str] = set()

    # Source 1: runtime metadata (name= kwarg form).
    try:
        # Importing mcp_server triggers the full ``@_tool`` decorator pass
        # which populates _TOOL_METADATA. fastmcp is optional, but the
        # metadata is populated *before* the fastmcp-presence check inside
        # ``_tool`` (per the comment at mcp_server.py:927) so this works
        # even on installs without the [mcp] extra.
        from roam.mcp_server import _TOOL_METADATA as _meta

        collected.update(_meta.keys())
    except (ImportError, AttributeError) as _exc:
        # fastmcp may be absent or mcp_server import may fail; Source 2
        # (AST scan below) still recovers the tool roster.
        from roam.observability import log_swallowed

        log_swallowed("framework_entrypoints:tool_metadata_import", _exc)

    # Source 2: AST scan of mcp_server.py -- recover the Python ``def``
    # names that the symbols table will store. We resolve the source
    # file via the imported module's __file__ rather than walking the
    # filesystem so editable installs and out-of-tree checkouts both
    # work without configuration.
    try:
        import ast

        import roam.mcp_server as _mcp_mod  # noqa: F401  (module-import side effect)

        src_path = getattr(_mcp_mod, "__file__", None)
        if src_path:
            with open(src_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=src_path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for deco in node.decorator_list:
                    # ``@_tool(name=...)`` is a Call whose .func is the
                    # bare Name "_tool". We only care about that shape;
                    # bare ``@_tool`` without parens isn't used in the
                    # codebase (the decorator signature requires kwargs).
                    if isinstance(deco, ast.Call):
                        target = deco.func
                        if isinstance(target, ast.Name) and target.id == "_tool":
                            collected.add(node.name)
                            break
                    elif isinstance(deco, ast.Name) and deco.id == "_tool":
                        collected.add(node.name)
                        break
    except (ImportError, OSError, SyntaxError, ValueError, AttributeError, TypeError):
        # Import (ast / mcp_server unavailable on a stripped wheel), file IO,
        # AST parse, or attribute/type issues during traversal -- none of these
        # should break a readonly path; degrade to empty per the docstring's
        # all-sources-down contract.
        pass

    _MCP_TOOL_NAMES_CACHE = frozenset(collected)
    return _MCP_TOOL_NAMES_CACHE


def _reset_mcp_tool_names_cache() -> None:
    """Test hook -- clear the cached MCP-tool name set."""
    global _MCP_TOOL_NAMES_CACHE
    _MCP_TOOL_NAMES_CACHE = None


def _is_mcp_tool_symbol(name: str, file_path: str) -> bool:
    """True if ``(name, file_path)`` names an MCP-tool wrapper.

    Anchored on BOTH the function name being in the ``@_tool`` roster AND
    the file being ``mcp_server.py``. The two-axis check prevents a
    coincidental shadow elsewhere (e.g. a test fixture named ``roam_clean``)
    from being silently exempted.
    """
    if not name or not file_path:
        return False
    base = os.path.basename(file_path).lower()
    if base != "mcp_server.py":
        return False
    return name in _load_mcp_tool_names()


def is_framework_entrypoint(name: str, file_path: str) -> bool:
    """Public: True if ``(name, file_path)`` is a runtime-registered framework
    entrypoint that a static call-graph analyser sees as having zero callers.

    Consumed by dead-code (exempt from SAFE), ``uses`` and ``impact`` (report a
    ``framework_entrypoint`` state rather than a misleading "no consumers").
    Currently recognises MCP ``@_tool`` wrappers; extend here as more decorator
    registries are modelled.
    """
    return _is_mcp_tool_symbol(name, file_path)
