"""MCP (Model Context Protocol) server for roam-code.

Exposes roam codebase-comprehension commands as structured MCP tools
so that AI coding agents can query project structure, health, dependencies,
and change-risk through a standard tool interface.

Usage:
    python -m roam.mcp_server
    # or
    fastmcp run roam.mcp_server:mcp
"""

from __future__ import annotations

import json
import subprocess

try:
    from fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "fastmcp is required for the roam MCP server.  "
        "Install it with:  pip install fastmcp"
    )

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "roam-code",
    description="Codebase comprehension for AI coding agents",
)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _run_roam(args: list[str], root: str = ".") -> dict:
    """Run a roam CLI command with ``--json`` and return parsed output.

    Parameters
    ----------
    args:
        Arguments to pass after ``roam --json``, e.g. ``["health"]``.
    root:
        Working directory for the subprocess (the project root).

    Returns
    -------
    dict
        Parsed JSON from stdout on success, or an ``{"error": ...}`` dict
        on failure / timeout.
    """
    cmd = ["roam", "--json"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return {
            "error": result.stderr.strip() or "Command failed",
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 60s"}
    except json.JSONDecodeError as exc:
        return {"error": f"Failed to parse JSON output: {exc}"}
    except Exception as exc:
        return {"error": str(exc)}


# ===================================================================
# Tier 1 tools -- the most valuable for day-to-day AI agent work
# ===================================================================


@mcp.tool()
def understand(root: str = ".") -> dict:
    """Get a full codebase overview in a single call.

    Returns project structure, tech stack (languages, frameworks, build tool),
    architecture (layers, clusters, entry points, key abstractions),
    health score, hotspots, naming conventions, complexity overview,
    design patterns, technical-debt hotspots, and a suggested file
    reading order.

    This is the recommended first call when an agent begins working with
    a new repository.
    """
    return _run_roam(["understand"], root)


@mcp.tool()
def health(root: str = ".") -> dict:
    """Assess overall codebase health.

    Returns a 0-100 health score together with detailed metrics:
    cycle count, god-component count, bottleneck symbols,
    dead-export count, layer violations, and per-issue severity
    breakdowns.  Use this to decide where to focus refactoring effort.
    """
    return _run_roam(["health"], root)


@mcp.tool()
def preflight(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Run a compound pre-change safety check for a symbol or file.

    Combines blast radius, affected tests, complexity, coupling,
    convention checks, and fitness-rule violations into one response --
    reducing round-trips from 5-6 calls to 1.

    Parameters
    ----------
    target:
        Symbol name or file path to check.  If empty, checks all
        currently changed (unstaged) files.
    staged:
        If True, check staged (``git add``-ed) changes instead of
        unstaged working-tree changes.
    """
    args = ["preflight"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def search_symbol(query: str, root: str = ".") -> dict:
    """Search for symbols by name substring (case-insensitive).

    Returns matching functions, classes, methods, variables, and
    interfaces with their kind, file location, signature, and
    PageRank importance.  Useful for finding the right qualified
    name before calling ``context`` or ``impact``.
    """
    return _run_roam(["search", query], root)


@mcp.tool()
def context(symbol: str, task: str = "", root: str = ".") -> dict:
    """Get the minimal context needed to safely modify a symbol.

    Returns the symbol definition, its direct callers and callees,
    the file it lives in, related tests, graph metrics (PageRank,
    fan-in/out, betweenness), and symbol-level complexity metrics.

    Parameters
    ----------
    symbol:
        Qualified or short name of the symbol to inspect.
    task:
        Optional task hint -- one of "refactor", "debug", "extend",
        "review", or "understand".  When provided the output is
        tailored to include extra data relevant to that task (e.g.
        complexity details for refactor, test coverage for debug).
    """
    args = ["context", symbol]
    if task:
        args.extend(["--task", task])
    return _run_roam(args, root)


@mcp.tool()
def trace(source: str, target: str, root: str = ".") -> dict:
    """Find the shortest dependency path between two symbols.

    Returns each hop along the path (symbol name, kind, location,
    edge type), the total hop count, coupling classification
    (strong / moderate / weak), and any hub nodes encountered
    along the way.  Useful for understanding how a change in
    *source* can propagate to *target*.
    """
    return _run_roam(["trace", source, target], root)


@mcp.tool()
def impact(symbol: str, root: str = ".") -> dict:
    """Show the blast radius of changing a symbol.

    Walks the reverse dependency graph to find every symbol and file
    that would be affected if the given symbol's signature or
    behaviour changed.  Returns affected symbols grouped by hop
    distance, affected files, and severity assessment.
    """
    return _run_roam(["impact", symbol], root)


@mcp.tool()
def file_info(path: str, root: str = ".") -> dict:
    """Show a file skeleton: every definition with its signature.

    Returns all symbols defined in the file (functions, classes,
    methods, variables, interfaces) with kind, line range, signature,
    export status, and parent relationships.  Also includes
    per-kind counts and the file's language.
    """
    return _run_roam(["file", path], root)


# ===================================================================
# Tier 2 tools -- change-risk and deeper analysis
# ===================================================================


@mcp.tool()
def pr_risk(staged: bool = False, root: str = ".") -> dict:
    """Compute a risk score for pending changes.

    Analyses the current diff (or staged changes) and produces an
    overall risk rating (LOW / MEDIUM / HIGH / CRITICAL) based on
    file count, line churn, coupling surprises, test coverage, and
    whether high-PageRank symbols are touched.

    Parameters
    ----------
    staged:
        If True, analyse staged changes instead of the working-tree diff.
    """
    args = ["pr-risk"]
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def breaking_changes(target: str = "HEAD~1", root: str = ".") -> dict:
    """Detect potential breaking changes between git refs.

    Compares the current working tree to *target* and identifies
    exported symbols whose signatures changed, were removed, or
    had parameters reordered.  Returns each breaking change with
    the old and new signatures and the affected symbol location.

    Parameters
    ----------
    target:
        Git ref to compare against (default ``HEAD~1``).
    """
    return _run_roam(["breaking", target], root)


@mcp.tool()
def affected_tests(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Find test files affected by a change.

    Starting from a changed symbol or file, walks reverse dependency
    edges to locate test files that directly or transitively exercise
    the changed code.  Returns each test file with the symbols that
    link it to the change and the hop distance.

    Parameters
    ----------
    target:
        Symbol name or file path.  If empty, uses all currently changed
        files.
    staged:
        If True, start from staged changes rather than the working-tree
        diff.
    """
    args = ["affected-tests"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def dead_code(root: str = ".") -> dict:
    """List unreferenced exported symbols (dead code).

    Finds exported symbols that have no incoming edges in the
    dependency graph, filtering out known entry points and framework
    lifecycle hooks.  Returns each dead symbol with kind, location,
    and the file it belongs to.
    """
    return _run_roam(["dead"], root)


@mcp.tool()
def complexity_report(threshold: int = 15, root: str = ".") -> dict:
    """Show per-symbol cognitive complexity metrics.

    Ranks functions and methods by cognitive complexity score.
    Only symbols at or above *threshold* are included.  Returns
    each symbol with its complexity score, nesting depth, parameter
    count, line count, severity label, and file location.

    Parameters
    ----------
    threshold:
        Minimum cognitive-complexity score to include (default 15).
    """
    return _run_roam(["complexity", "--threshold", str(threshold)], root)


@mcp.tool()
def repo_map(budget: int = 0, root: str = ".") -> dict:
    """Show a project skeleton with entry points and key symbols.

    Produces a compact map of the repository structure: files grouped
    by directory, annotated with their most important symbols (by
    PageRank).  Useful for giving an AI agent a quick spatial overview.

    Parameters
    ----------
    budget:
        Approximate token budget for the output.  0 means no limit.
    """
    args = ["map"]
    if budget > 0:
        args.extend(["--budget", str(budget)])
    return _run_roam(args, root)


# ===================================================================
# MCP Resources -- static/cached summaries available at fixed URIs
# ===================================================================


@mcp.resource("roam://health")
def get_health_resource() -> str:
    """Current codebase health snapshot (JSON).

    Provides the same data as the ``health`` tool but exposed as an
    MCP resource so agents can subscribe to or poll it.
    """
    data = _run_roam(["health"])
    return json.dumps(data, indent=2)


@mcp.resource("roam://summary")
def get_summary_resource() -> str:
    """Full codebase summary (JSON).

    Equivalent to calling the ``understand`` tool, exposed as a
    resource for agents that prefer resource-based access.
    """
    data = _run_roam(["understand"])
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
