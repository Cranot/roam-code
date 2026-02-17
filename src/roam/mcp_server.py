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
    description=(
        "Codebase intelligence for AI coding agents. "
        "Pre-indexes symbols, call graphs, dependencies, architecture, "
        "and git history into a local SQLite DB. "
        "One tool call replaces 5-10 Glob/Grep/Read calls. "
        "All tools are read-only and safe to call freely."
    ),
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
    """Get a full codebase briefing in a single call.

    WHEN TO USE: Call this FIRST when you start working with a new or
    unfamiliar repository. Do NOT use Glob/Grep/Read to explore the
    codebase manually -- this tool gives you everything in one shot.

    Returns: tech stack, architecture overview (layers, clusters, entry
    points, key abstractions), health score, hotspots, naming conventions,
    design patterns, and a suggested file reading order.

    Output is ~2,000-4,000 tokens of structured JSON. After calling this,
    use `search_symbol` or `context` to drill into specific areas.
    """
    return _run_roam(["understand"], root)


@mcp.tool()
def health(root: str = ".") -> dict:
    """Get the codebase health score (0-100) with issue breakdown.

    WHEN TO USE: Call this to assess overall code quality before deciding
    where to focus refactoring effort, or to check whether recent changes
    degraded health. Do NOT call this if you already called `understand`
    (which includes health data) or `preflight` (which includes it per-symbol).

    Returns: composite health score, cycle count, god-component count,
    bottleneck symbols, dead-export count, layer violations, per-file
    health scores, and tangle ratio.
    """
    return _run_roam(["health"], root)


@mcp.tool()
def preflight(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Pre-change safety check. Call this BEFORE modifying any symbol or file.

    WHEN TO USE: Always call this before making code changes. It replaces
    5-6 separate tool calls by combining blast radius, affected tests,
    complexity, coupling, convention checks, and fitness violations into
    one response. Do NOT call `context`, `impact`, `affected_tests`, or
    `complexity_report` separately if preflight covers your need.

    Parameters
    ----------
    target:
        Symbol name or file path to check. If empty, checks all
        currently changed (unstaged) files.
    staged:
        If True, check staged (git add-ed) changes instead.

    Returns: risk level, blast radius (affected symbols and files),
    test files to run, complexity metrics, coupling data, and any
    fitness rule violations.
    """
    args = ["preflight"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def search_symbol(query: str, root: str = ".") -> dict:
    """Find symbols by name (case-insensitive substring match).

    WHEN TO USE: Call this when you know part of a symbol name and need
    the exact qualified name, file location, or kind. Use this before
    calling `context` or `impact` to get the correct symbol identifier.
    Do NOT use Grep to search for function definitions -- this is faster
    and returns structured data with PageRank importance.

    Parameters
    ----------
    query:
        Name substring to search for (e.g., "auth", "User", "handle_request").

    Returns: matching symbols with kind (function/class/method), file path,
    line number, signature, export status, and PageRank importance score.
    """
    return _run_roam(["search", query], root)


@mcp.tool()
def context(symbol: str, task: str = "", root: str = ".") -> dict:
    """Get the minimal context needed to work with a specific symbol.

    WHEN TO USE: Call this when you need to understand or modify a
    specific function, class, or method. Returns the exact files and
    line ranges to read -- much more targeted than `understand`.
    For pre-change safety checks, prefer `preflight` instead (it
    includes context data plus blast radius and tests).

    Parameters
    ----------
    symbol:
        Qualified or short name of the symbol to inspect.
    task:
        Optional hint: "refactor", "debug", "extend", "review", or
        "understand". Tailors output (e.g., adds complexity details
        for refactor, test coverage for debug).

    Returns: symbol definition, direct callers and callees, file location
    with line ranges, related tests, graph metrics (PageRank, fan-in/out,
    betweenness), and complexity metrics.
    """
    args = ["context", symbol]
    if task:
        args.extend(["--task", task])
    return _run_roam(args, root)


@mcp.tool()
def trace(source: str, target: str, root: str = ".") -> dict:
    """Find the shortest dependency path between two symbols.

    WHEN TO USE: Call this when you need to understand HOW a change in
    one symbol could affect another. Shows each hop along the path with
    symbol names, edge types, and locations.

    Parameters
    ----------
    source:
        Starting symbol name.
    target:
        Destination symbol name.

    Returns: path hops (symbol name, kind, location, edge type), total
    hop count, coupling classification (strong/moderate/weak), and any
    hub nodes encountered.
    """
    return _run_roam(["trace", source, target], root)


@mcp.tool()
def impact(symbol: str, root: str = ".") -> dict:
    """Show the blast radius of changing a symbol.

    WHEN TO USE: Call this when you need to know everything that would
    break if a symbol's signature or behavior changed. For pre-change
    checks, prefer `preflight` which includes impact data plus tests
    and fitness checks.

    Parameters
    ----------
    symbol:
        Symbol to analyze.

    Returns: affected symbols grouped by hop distance, affected files,
    total affected count, and severity assessment.
    """
    return _run_roam(["impact", symbol], root)


@mcp.tool()
def file_info(path: str, root: str = ".") -> dict:
    """Show a file skeleton: every symbol definition with its signature.

    WHEN TO USE: Call this when you need to understand what a file
    contains without reading the full source. Returns a structured
    outline that is more useful than Read for getting an overview.

    Parameters
    ----------
    path:
        File path relative to the project root.

    Returns: all symbols in the file (functions, classes, methods) with
    kind, line range, signature, export status, and parent relationships.
    Also includes per-kind counts and the file's detected language.
    """
    return _run_roam(["file", path], root)


# ===================================================================
# Tier 2 tools -- change-risk and deeper analysis
# ===================================================================


@mcp.tool()
def pr_risk(staged: bool = False, root: str = ".") -> dict:
    """Compute a risk score (0-100) for pending changes.

    WHEN TO USE: Call this before committing or creating a PR to assess
    risk. Analyzes the current diff and produces a risk rating (LOW /
    MODERATE / HIGH / CRITICAL) with specific risk factors.

    Parameters
    ----------
    staged:
        If True, analyze staged changes instead of working-tree diff.

    Returns: risk score, risk level, per-file breakdown (symbols changed,
    blast radius, churn), suggested reviewers, coupling surprises, and
    any new dead exports created.
    """
    args = ["pr-risk"]
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def breaking_changes(target: str = "HEAD~1", root: str = ".") -> dict:
    """Detect breaking API changes between git refs.

    WHEN TO USE: Call this before releasing or merging to check if any
    public APIs were broken. Finds removed exports, changed signatures,
    and reordered parameters.

    Parameters
    ----------
    target:
        Git ref to compare against (default: HEAD~1).

    Returns: each breaking change with old/new signatures, the affected
    symbol location, and the change type (removed/signature_changed/
    params_reordered).
    """
    return _run_roam(["breaking", target], root)


@mcp.tool()
def affected_tests(target: str = "", staged: bool = False, root: str = ".") -> dict:
    """Find test files that exercise the changed code.

    WHEN TO USE: Call this to know which tests to run after making
    changes. Walks reverse dependency edges from changed code to find
    test files. For a full pre-change check, prefer `preflight` which
    includes affected tests plus blast radius and fitness checks.

    Parameters
    ----------
    target:
        Symbol name or file path. If empty, uses all currently changed files.
    staged:
        If True, start from staged changes.

    Returns: test files with the symbols that link them to the change
    and the hop distance.
    """
    args = ["affected-tests"]
    if target:
        args.append(target)
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def math(task: str = "", confidence: str = "", root: str = ".") -> dict:
    """Detect suboptimal algorithms and suggest better approaches.

    WHEN TO USE: Call this to find code that uses naive algorithms when
    better alternatives exist (e.g., manual sort instead of built-in,
    linear scan instead of binary search, nested-loop lookup instead of
    hash join). Returns specific suggestions with complexity analysis.

    Parameters
    ----------
    task:
        Filter by task ID (e.g., "sorting", "membership", "nested-lookup").
        Empty means all tasks.
    confidence:
        Filter by confidence level: "high", "medium", or "low".

    Returns: findings grouped by algorithm category, each with current
    vs. better approach, complexity comparison, and improvement tips.
    """
    args = ["math"]
    if task:
        args.extend(["--task", task])
    if confidence:
        args.extend(["--confidence", confidence])
    return _run_roam(args, root)


@mcp.tool()
def dead_code(root: str = ".") -> dict:
    """List unreferenced exported symbols (dead code candidates).

    WHEN TO USE: Call this to find code that can be safely removed.
    Finds exported symbols with zero incoming edges, filtering out
    known entry points and framework lifecycle hooks.

    Returns: each dead symbol with kind, location, file, and a safety
    verdict indicating confidence level.
    """
    return _run_roam(["dead"], root)


@mcp.tool()
def complexity_report(threshold: int = 15, root: str = ".") -> dict:
    """Rank functions by cognitive complexity.

    WHEN TO USE: Call this to find the most complex functions that
    should be refactored. Only symbols at or above the threshold are
    included. For checking a single symbol, prefer `context` or
    `preflight` which include complexity data.

    Parameters
    ----------
    threshold:
        Minimum cognitive-complexity score to include (default 15).

    Returns: symbols ranked by complexity with score, nesting depth,
    parameter count, line count, severity label, and file location.
    """
    return _run_roam(["complexity", "--threshold", str(threshold)], root)


@mcp.tool()
def repo_map(budget: int = 0, root: str = ".") -> dict:
    """Show a compact project skeleton with key symbols.

    WHEN TO USE: Call this for a spatial overview of the repository
    structure -- files grouped by directory, annotated with their most
    important symbols (by PageRank). Lighter than `understand`, useful
    when you just need the file layout.

    Parameters
    ----------
    budget:
        Approximate token budget for the output. 0 means no limit.

    Returns: files grouped by directory with top symbols per file,
    annotated with kind and importance.
    """
    args = ["map"]
    if budget > 0:
        args.extend(["--budget", str(budget)])
    return _run_roam(args, root)


@mcp.tool()
def tour(root: str = ".") -> dict:
    """Generate a codebase onboarding guide.

    WHEN TO USE: Call this when onboarding to a new codebase or helping
    a developer understand the project structure. Produces a structured
    architecture tour: top symbols by importance, reading order based on
    topological layers, entry points, and language breakdown. More
    detailed than `understand` for onboarding; use `understand` for a
    quick briefing.

    Returns: language breakdown, codebase statistics (files, symbols,
    edges, test ratio, avg health), top-10 symbols with roles
    (Hub/Core utility/Orchestrator/Leaf), suggested file reading order
    by topological layer, and entry points for exploration.
    """
    return _run_roam(["tour"], root)


@mcp.tool()
def visualize(
    focus: str = "",
    format: str = "mermaid",
    depth: int = 1,
    limit: int = 30,
    direction: str = "TD",
    no_clusters: bool = False,
    file_level: bool = False,
    root: str = ".",
) -> dict:
    """Generate a Mermaid or DOT architecture diagram from the codebase graph.

    WHEN TO USE: Call this to get a visual dependency diagram of the
    codebase architecture. Uses smart filtering (PageRank, clusters,
    cycle highlighting) to produce readable diagrams. Paste Mermaid
    output into markdown or use DOT with Graphviz.

    Parameters
    ----------
    focus:
        Focus on a specific symbol (BFS neighborhood). If empty,
        shows the top-N most important symbols by PageRank.
    format:
        Output format: "mermaid" or "dot".
    depth:
        BFS depth for focus mode (default 1).
    limit:
        Max nodes in overview mode (default 30).
    direction:
        Mermaid direction: "TD" (top-down) or "LR" (left-right).
    no_clusters:
        Disable Louvain cluster grouping.
    file_level:
        Use file-level graph instead of symbol graph.

    Returns: diagram text (Mermaid or DOT), node/edge counts, and
    format metadata.
    """
    args = ["visualize", "--format", format, "--depth", str(depth),
            "--limit", str(limit), "--direction", direction]
    if focus:
        args.extend(["--focus", focus])
    if no_clusters:
        args.append("--no-clusters")
    if file_level:
        args.append("--file-level")
    return _run_roam(args, root)


@mcp.tool()
def diagnose(symbol: str, depth: int = 2, root: str = ".") -> dict:
    """Root cause analysis for a failing symbol.

    WHEN TO USE: Call this when debugging a bug or test failure and you
    need to find the likely root cause. Ranks upstream callers and
    downstream callees by a composite risk score combining git churn,
    cognitive complexity, file health, and co-change entropy. Much
    faster than manually tracing call chains.

    Parameters
    ----------
    symbol:
        The symbol suspected of being involved in the bug.
    depth:
        How many hops upstream/downstream to analyze (default 2).

    Returns: target symbol metrics, upstream suspects ranked by risk,
    downstream suspects ranked by risk, co-change partners, recent
    git commits, and a verdict naming the top suspect.
    """
    args = ["diagnose", symbol, "--depth", str(depth)]
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


# ===================================================================
# Workspace tools -- multi-repo analysis
# ===================================================================


@mcp.tool()
def ws_understand(root: str = ".") -> dict:
    """Get a unified overview of a multi-repo workspace.

    WHEN TO USE: Call this when working with a project that spans
    multiple repositories (e.g., frontend + backend). Returns stats
    for each repo, cross-repo API connections, and key symbols.
    Requires a workspace to be initialized with `roam ws init`.

    Parameters
    ----------
    root:
        Working directory (must be within the workspace).

    Returns: per-repo stats (files, symbols, languages, key symbols),
    cross-repo edge count, and connection details.
    """
    return _run_roam(["ws", "understand"], root)


@mcp.tool()
def ws_context(symbol: str, root: str = ".") -> dict:
    """Get cross-repo augmented context for a symbol.

    WHEN TO USE: Call this when you need to understand a symbol that
    participates in cross-repo API calls. For example, querying a
    backend controller will also show frontend callers that hit its
    endpoints. Requires `roam ws init` + `roam ws resolve`.

    Parameters
    ----------
    symbol:
        Symbol name to search for across all workspace repos.

    Returns: symbol definition(s) found across repos, callers/callees
    within each repo, and cross-repo API edges.
    """
    return _run_roam(["ws", "context", symbol], root)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
