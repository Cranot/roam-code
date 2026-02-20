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
def dark_matter(min_npmi: float = 0.3, min_cochanges: int = 3, root: str = ".") -> dict:
    """Detect dark matter: file pairs that co-change but have no structural link.

    WHEN TO USE: Call this when you suspect hidden coupling between files
    that don't import each other but always change together. Returns
    dark-matter pairs with hypothesized reasons (shared DB, event bus,
    config, copy-paste). Complements `coupling` which shows all co-change
    pairs -- this filters to only structurally unlinked ones.

    Parameters
    ----------
    min_npmi:
        Minimum NPMI threshold (default 0.3). Higher = stronger coupling.
    min_cochanges:
        Minimum co-change count (default 3).

    Returns: dark-matter pairs with NPMI, lift, strength, co-change count,
    and hypothesis (category + detail + confidence) for each pair.
    """
    args = ["dark-matter", "--explain",
            "--min-npmi", str(min_npmi),
            "--min-cochanges", str(min_cochanges)]
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


@mcp.tool()
def relate(symbols: list[str], files: list[str] | None = None,
           depth: int = 3, root: str = ".") -> dict:
    """Show how a set of symbols relate: shared deps, call chains, conflicts.

    WHEN TO USE: Call this when you have queried multiple symbols via
    ``context`` and need to understand HOW they connect. Shows direct
    edges, shared dependencies, shared callers, conflict risks, distance
    matrix, and a cohesion score. More useful than running ``trace``
    pairwise for 3+ symbols.

    Parameters
    ----------
    symbols:
        List of symbol names to analyze relationships between.
    files:
        Optional file/directory paths to include all symbols from.
    depth:
        Max hops for connecting paths (default 3).

    Returns: relationships, shared dependencies, shared callers,
    conflict risks, distance matrix, and cohesion score.
    """
    args = ["relate"] + symbols
    if files:
        for f in files:
            args.extend(["--file", f])
    if depth != 3:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


# ===================================================================
# Tier 3 tools -- agentic memory
# ===================================================================


@mcp.tool()
def annotate_symbol(
    target: str, content: str,
    tag: str = "", author: str = "", expires: str = "",
    root: str = ".",
) -> dict:
    """Add a persistent annotation to a symbol or file.

    WHEN TO USE: Call this to leave a note for future agent sessions.
    Annotations survive reindexing and are auto-injected into ``context``
    output, giving every subsequent session institutional knowledge about
    the codebase.

    Parameters
    ----------
    target:
        Symbol name or file path to annotate.
    content:
        The annotation text (e.g., "O(n^2) loop, see PR #42").
    tag:
        Category tag: security, performance, gotcha, review, wip.
    author:
        Who is annotating (agent name or user).
    expires:
        Optional expiry datetime (ISO 8601, e.g. "2025-12-31").

    Returns: confirmation with the resolved target and tag.
    """
    args = ["annotate", target, content]
    if tag:
        args.extend(["--tag", tag])
    if author:
        args.extend(["--author", author])
    if expires:
        args.extend(["--expires", expires])
    return _run_roam(args, root)


@mcp.tool()
def get_annotations(
    target: str = "", tag: str = "", since: str = "",
    root: str = ".",
) -> dict:
    """Read annotations for a symbol, file, or the whole project.

    WHEN TO USE: Call this to retrieve institutional knowledge left by
    previous agent sessions or human reviewers. If you called ``context``
    with a task mode, annotations are already included in the output.

    Parameters
    ----------
    target:
        Symbol name or file path. If empty, returns all annotations.
    tag:
        Filter by tag (e.g., "security", "performance").
    since:
        Only annotations created after this datetime (ISO 8601).

    Returns: list of annotations with content, tag, author, and timestamps.
    """
    args = ["annotations"]
    if target:
        args.append(target)
    if tag:
        args.extend(["--tag", tag])
    if since:
        args.extend(["--since", since])
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


@mcp.tool()
def pr_diff(staged: bool = False, commit_range: str = "", root: str = ".") -> dict:
    """Show structural consequences of code changes (graph delta).

    WHEN TO USE: Call this during code review to understand the
    architectural impact of a PR. Shows metric deltas (health score,
    cycles, complexity), cross-cluster edges, layer violations, symbol
    changes, and graph footprint. Much richer than a text diff.

    Parameters
    ----------
    staged:
        If True, analyse only staged changes.
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.

    Returns: verdict, metric deltas, edge analysis, symbol changes,
    and graph footprint.
    """
    args = ["pr-diff"]
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    return _run_roam(args, root)


@mcp.tool()
def effects(target: str = "", file: str = "", effect_type: str = "", root: str = ".") -> dict:
    """Show side effects of functions (DB writes, network, filesystem, etc.).

    WHEN TO USE: Call this to understand what a function actually DOES
    beyond its signature. Shows both direct effects (from the function
    body) and transitive effects (inherited from callees via the call
    graph). Useful for assessing change risk and understanding data flow.

    Parameters
    ----------
    target:
        Symbol name to inspect effects for.
    file:
        File path to show effects per function.
    effect_type:
        Filter by effect type (e.g. "writes_db", "network").

    Returns: classified effects (direct and transitive) for the symbol,
    file, or entire codebase.
    """
    args = ["effects"]
    if target:
        args.append(target)
    if file:
        args.extend(["--file", file])
    if effect_type:
        args.extend(["--type", effect_type])
    return _run_roam(args, root)


@mcp.tool()
def budget_check(config: str = "", staged: bool = False, commit_range: str = "", root: str = ".") -> dict:
    """Check pending changes against architectural budgets.

    WHEN TO USE: Call this as a CI gate or before merging to verify
    that changes stay within defined quality budgets (max cycles,
    health floor, complexity ceiling, etc.). Exit code 1 if any
    budget is exceeded.

    Parameters
    ----------
    config:
        Path to custom budget YAML config.
    staged:
        If True, analyse only staged changes.
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.

    Returns: verdict, per-rule pass/fail results, and whether a
    baseline snapshot was available.
    """
    args = ["budget"]
    if config:
        args.extend(["--config", config])
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    return _run_roam(args, root)


@mcp.tool()
def attest(commit_range: str = "", staged: bool = False, output_format: str = "json",
           sign: bool = False, root: str = ".") -> dict:
    """Generate a proof-carrying PR attestation with all evidence bundled.

    WHEN TO USE: Call this before merging or in CI to get a single
    verifiable artifact that bundles blast radius, risk score, breaking
    changes, fitness violations, budget consumed, affected tests, and
    effects. The verdict indicates whether it is safe to merge.

    Parameters
    ----------
    commit_range:
        Git range like ``main..HEAD`` for branch comparison.
    staged:
        If True, attest only staged changes.
    output_format:
        Output format: ``json``, ``text``, or ``markdown``.
    sign:
        If True, include SHA-256 content hash for tamper detection.

    Returns: attestation metadata, evidence bundle, and merge verdict.
    """
    args = ["attest"]
    if commit_range:
        args.append(commit_range)
    if staged:
        args.append("--staged")
    if output_format:
        args.extend(["--format", output_format])
    if sign:
        args.append("--sign")
    return _run_roam(args, root)


@mcp.tool()
def capsule_export(redact_paths: bool = False, no_signatures: bool = False, root: str = ".") -> dict:
    """Export a sanitized structural graph without function bodies.

    WHEN TO USE: Call this to create a privacy-safe export of the
    codebase architecture for external review, audits, or consulting.
    Contains symbols, edges, clusters, and health metrics but no
    implementation code.

    Parameters
    ----------
    redact_paths:
        If True, anonymize file paths with hashes.
    no_signatures:
        If True, omit function signatures.

    Returns: topology, symbols, edges, clusters, and health metrics.
    """
    args = ["capsule"]
    if redact_paths:
        args.append("--redact-paths")
    if no_signatures:
        args.append("--no-signatures")
    return _run_roam(args, root)


@mcp.tool()
def path_coverage(from_pattern: str = "", to_pattern: str = "",
                  max_depth: int = 8, root: str = ".") -> dict:
    """Find critical call paths with zero test protection.

    WHEN TO USE: Call this to discover untested paths from entry
    points to sensitive sinks (DB writes, network, filesystem).
    Shows which paths are most at risk and suggests optimal test
    insertion points for maximum coverage.

    Parameters
    ----------
    from_pattern:
        Glob to filter entry points by file path.
    to_pattern:
        Glob to filter sinks by file path.
    max_depth:
        Maximum path depth (default: 8).

    Returns: untested paths ranked by risk, with test suggestions.
    """
    args = ["path-coverage"]
    if from_pattern:
        args.extend(["--from", from_pattern])
    if to_pattern:
        args.extend(["--to", to_pattern])
    if max_depth != 8:
        args.extend(["--max-depth", str(max_depth)])
    return _run_roam(args, root)


@mcp.tool()
def forecast(symbol: str = "", horizon: int = 30,
             alert_only: bool = False, root: str = ".") -> dict:
    """Predict when metrics will exceed thresholds.

    WHEN TO USE: Call this to identify functions with accelerating
    complexity or metrics trending toward dangerous thresholds.
    Uses Theil-Sen regression on snapshot history for aggregate
    trends and churn-weighted analysis for per-symbol risk.

    Parameters
    ----------
    symbol:
        Specific symbol to forecast.
    horizon:
        Number of snapshots to look ahead (default: 30).
    alert_only:
        If True, only show non-stable trends.

    Returns: aggregate metric trends and at-risk symbols.
    """
    args = ["forecast"]
    if symbol:
        args.extend(["--symbol", symbol])
    if horizon != 30:
        args.extend(["--horizon", str(horizon)])
    if alert_only:
        args.append("--alert-only")
    return _run_roam(args, root)


@mcp.tool()
def generate_plan(target: str = "", task: str = "refactor",
                  file_path: str = "", staged: bool = False,
                  depth: int = 2, root: str = ".") -> dict:
    """Generate a structured execution plan for modifying code.

    WHEN TO USE: Call this before any non-trivial code modification.
    Returns a step-by-step strategy: read order, invariants to preserve,
    safe modification points, touch-carefully warnings, test shortlist,
    and post-change verification commands.

    Parameters
    ----------
    target:
        Symbol name to plan for.
    task:
        Task type: refactor, debug, extend, review, understand.
    file_path:
        File to plan for (alternative to target).
    staged:
        Plan for staged changes.
    depth:
        Call graph depth for read order (default: 2).

    Returns: structured plan with 6 sections.
    """
    args = ["plan"]
    if target:
        args.append(target)
    if task != "refactor":
        args.extend(["--task", task])
    if file_path:
        args.extend(["--file", file_path])
    if staged:
        args.append("--staged")
    if depth != 2:
        args.extend(["--depth", str(depth)])
    return _run_roam(args, root)


@mcp.tool()
def adversarial_review(staged: bool = False, commit_range: str = "",
                       severity: str = "low", root: str = ".") -> dict:
    """Adversarial architecture review — challenge code changes.

    WHEN TO USE: Call this after making changes to get targeted
    architectural challenges. Acts as a "Dungeon Master" generating
    questions about cycles, layer violations, anti-patterns, and
    cross-cluster coupling that the developer must address.

    Parameters
    ----------
    staged:
        Review staged changes only.
    commit_range:
        Review a commit range (e.g. main..HEAD).
    severity:
        Minimum severity filter: low, medium, high, critical.

    Returns: list of architectural challenges with severity and questions.
    """
    args = ["adversarial"]
    if staged:
        args.append("--staged")
    if commit_range:
        args.extend(["--range", commit_range])
    if severity != "low":
        args.extend(["--severity", severity])
    return _run_roam(args, root)


@mcp.tool()
def cut_analysis(between_a: str = "", between_b: str = "",
                 leak_edges: bool = False, top_n: int = 10,
                 root: str = ".") -> dict:
    """Minimum cut analysis — find fragile domain boundaries.

    WHEN TO USE: Call this to identify the thinnest boundaries between
    architectural clusters and the highest-impact "leak edges" whose
    removal would best improve domain isolation. Useful for targeted
    refactoring decisions.

    Parameters
    ----------
    between_a:
        First cluster name (use with between_b for specific pair).
    between_b:
        Second cluster name.
    leak_edges:
        Focus on leak edge analysis.
    top_n:
        Show top N boundaries (default: 10).

    Returns: boundary analysis with min-cut sizes, thinness, and leak edges.
    """
    args = ["cut"]
    if between_a and between_b:
        args.extend(["--between", between_a, between_b])
    if leak_edges:
        args.append("--leak-edges")
    if top_n != 10:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@mcp.tool()
def get_invariants(target: str = "", public_api: bool = False,
                   breaking_risk: bool = False, top_n: int = 20,
                   root: str = ".") -> dict:
    """Discover implicit contracts for symbols.

    WHEN TO USE: Call this before modifying a symbol to understand what
    must remain true. Returns signature contracts, caller stability,
    usage spread, and breaking risk scores.

    Parameters
    ----------
    target:
        Symbol name or file path to analyze.
    public_api:
        Analyze all exported/public symbols.
    breaking_risk:
        Rank symbols by breaking risk (callers * file spread).
    top_n:
        Max symbols to show (default: 20).

    Returns: invariants per symbol with breaking risk scores.
    """
    args = ["invariants"]
    if target:
        args.append(target)
    if public_api:
        args.append("--public-api")
    if breaking_risk:
        args.append("--breaking-risk")
    if top_n != 20:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@mcp.tool()
def bisect_blame(metric: str = "health_score", threshold: float = 0,
                 direction: str = "degraded", top_n: int = 10,
                 root: str = ".") -> dict:
    """Find which snapshots caused architectural degradation.

    WHEN TO USE: Call this when health score has dropped or metrics
    have worsened. Walks snapshot history and ranks snapshots by the
    magnitude of metric changes to identify the commits that caused
    the biggest structural regressions.

    Parameters
    ----------
    metric:
        Metric to track (health_score, cycles, avg_complexity, etc.).
    threshold:
        Only show deltas exceeding this threshold.
    direction:
        Filter: degraded, improved, or both.
    top_n:
        Show top N snapshots by impact (default: 10).

    Returns: ranked list of snapshots by architectural impact.
    """
    args = ["bisect", "--metric", metric]
    if threshold > 0:
        args.extend(["--threshold", str(threshold)])
    if direction != "degraded":
        args.extend(["--direction", direction])
    if top_n != 10:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@mcp.tool()
def simulate(operation: str, symbol: str = "", target_file: str = "",
             file_a: str = "", file_b: str = "", root: str = ".") -> dict:
    """Simulate a structural change and predict metric deltas.

    WHEN TO USE: Call this before making architectural changes (moving,
    extracting, merging, or deleting symbols/files) to predict the impact
    on health score, modularity, cycles, and other metrics. Enables
    gradient-descent on architecture by testing "what if" scenarios.

    Parameters
    ----------
    operation:
        One of: "move", "extract", "merge", "delete".
    symbol:
        Symbol name for move/extract/delete operations.
    target_file:
        Destination file for move/extract operations.
    file_a:
        Target file for merge (file_b merges into file_a).
    file_b:
        Source file for merge (merged into file_a).

    Returns: predicted metric deltas (health score, cycles, modularity,
    layer violations, etc.), operation summary, verdict, and warnings.
    """
    args = ["simulate", operation]
    if operation in ("move", "extract"):
        if symbol:
            args.append(symbol)
        if target_file:
            args.append(target_file)
    elif operation == "merge":
        if file_a:
            args.append(file_a)
        if file_b:
            args.append(file_b)
    elif operation == "delete":
        if symbol:
            args.append(symbol)
    return _run_roam(args, root)


@mcp.tool()
def closure(symbol: str, rename: str = "", delete: bool = False, root: str = ".") -> dict:
    """Compute the minimal set of changes needed when modifying a symbol.

    WHEN TO USE: Call this when you need to know EXACTLY what must change
    for a rename, deletion, or modification. Unlike ``impact`` (blast
    radius -- what MIGHT break), closure tells you what MUST change.
    Returns the exact files and locations that need updating.

    Parameters
    ----------
    symbol:
        Symbol name to compute closure for.
    rename:
        New name for a rename operation. If provided, also searches
        for string references in doc/config files.
    delete:
        If True, compute deletion closure.

    Returns: list of changes grouped by type (update_call, update_import,
    update_test, update_doc), with file paths and line numbers.
    """
    args = ["closure", symbol]
    if rename:
        args.extend(["--rename", rename])
    if delete:
        args.append("--delete")
    return _run_roam(args, root)


@mcp.tool()
def doc_intent(symbol: str = "", doc: str = "",
               drift: bool = False, undocumented: bool = False,
               top_n: int = 20, root: str = ".") -> dict:
    """Link documentation to code — find what docs describe what code.

    WHEN TO USE: Call this to understand the relationship between
    documentation and code. Finds doc-to-code links, drift (dead
    references to removed symbols), and undocumented high-centrality
    symbols that should have docs.

    Parameters
    ----------
    symbol:
        Find docs mentioning this specific symbol.
    doc:
        Find code referenced by this specific doc file.
    drift:
        Show references to symbols that no longer exist.
    undocumented:
        Show important symbols not mentioned in any docs.
    top_n:
        Max items to show (default: 20).

    Returns: doc-code links, drift, and undocumented symbols.
    """
    args = ["intent"]
    if symbol:
        args.extend(["--symbol", symbol])
    if doc:
        args.extend(["--doc", doc])
    if drift:
        args.append("--drift")
    if undocumented:
        args.append("--undocumented")
    if top_n != 20:
        args.extend(["--top", str(top_n)])
    return _run_roam(args, root)


@mcp.tool()
def fingerprint(compact: bool = False, export_path: str = "",
                compare_path: str = "", root: str = ".") -> dict:
    """Extract a topology fingerprint for cross-repo comparison.

    WHEN TO USE: Call this to get the structural signature of a codebase
    (layers, modularity, connectivity, clusters, hub/bridge ratio,
    PageRank distribution). Use --compare to diff against another repo's
    saved fingerprint. Useful for identifying similar architectures or
    tracking structural drift over time.

    Parameters
    ----------
    compact:
        If True, return a single-line summary.
    export_path:
        If provided, save fingerprint JSON to this file path.
    compare_path:
        If provided, compare with a previously saved fingerprint JSON.

    Returns: topology metrics, cluster summaries, hub/bridge ratio,
    PageRank Gini, dependency direction, and anti-patterns.
    """
    args = ["fingerprint"]
    if compact:
        args.append("--compact")
    if export_path:
        args.extend(["--export", export_path])
    if compare_path:
        args.extend(["--compare", compare_path])
    return _run_roam(args, root)


@mcp.tool()
def rules_check(ci: bool = False, rules_dir: str = "", root: str = ".") -> dict:
    """Evaluate custom governance rules defined in .roam/rules/.

    WHEN TO USE: Call this to check architectural constraints defined as
    YAML rule files. Supports path_match rules (no direct edges between
    from/to patterns) and symbol_match rules (symbols matching criteria
    must satisfy requirements like test coverage). Use ``--ci`` in CI
    pipelines to fail on error-severity violations.

    Parameters
    ----------
    ci:
        If True, exit code 1 on error-severity violations.
    rules_dir:
        Custom rules directory path.

    Returns: per-rule pass/fail results with violation details.
    """
    args = ["rules"]
    if ci:
        args.append("--ci")
    if rules_dir:
        args.extend(["--rules-dir", rules_dir])
    return _run_roam(args, root)


@mcp.tool()
def orchestrate(n_agents: int, files: list[str] | None = None,
                staged: bool = False, root: str = ".") -> dict:
    """Partition codebase for parallel multi-agent work (swarm orchestration).

    WHEN TO USE: Call this before splitting work across multiple AI agents.
    Assigns exclusive write zones, read-only dependencies, interface
    contracts, a merge order, and a conflict probability score so agents
    can work in parallel without stepping on each other.

    Parameters
    ----------
    n_agents:
        Number of agents to partition work for.
    files:
        Optional list of files or directories to restrict to.
    staged:
        If True, restrict to files in the git staging area.

    Returns: per-agent write/read file lists, contracts, merge order,
    conflict probability, and shared interface symbols.
    """
    args = ["orchestrate", "--agents", str(n_agents)]
    if files:
        for f in files:
            args.extend(["--files", f])
    if staged:
        args.append("--staged")
    return _run_roam(args, root)


@mcp.tool()
def mutate(operation: str, symbol: str = "", target_file: str = "",
           new_name: str = "", from_symbol: str = "", to_symbol: str = "",
           args: str = "", lines: str = "", apply: bool = False,
           root: str = ".") -> dict:
    """Syntax-less agentic editing -- move, rename, add-call, extract symbols.

    WHEN TO USE: Call this when you need to make structural code changes
    (move a symbol to a new file, rename across the codebase, add a call
    between functions, or extract lines into a new function). Automatically
    rewrites imports and updates references. Default is dry-run (preview);
    set apply=True to write changes.

    Parameters
    ----------
    operation:
        One of: "move", "rename", "add-call", "extract".
    symbol:
        Symbol name for move/rename/extract operations.
    target_file:
        Destination file for move operation.
    new_name:
        New name for rename or extract operations.
    from_symbol:
        Calling symbol for add-call operation.
    to_symbol:
        Callee symbol for add-call operation.
    args:
        Arguments string for add-call (e.g. "data, config").
    lines:
        Line range for extract (e.g. "5-10").
    apply:
        If True, write changes to disk. Default is dry-run.

    Returns: change plan with files modified, per-file changes, and verdict.
    """
    cmd_args = ["mutate", operation]
    if operation == "move":
        if symbol:
            cmd_args.append(symbol)
        if target_file:
            cmd_args.append(target_file)
    elif operation == "rename":
        if symbol:
            cmd_args.append(symbol)
        if new_name:
            cmd_args.append(new_name)
    elif operation == "add-call":
        if from_symbol:
            cmd_args.extend(["--from", from_symbol])
        if to_symbol:
            cmd_args.extend(["--to", to_symbol])
        if args:
            cmd_args.extend(["--args", args])
    elif operation == "extract":
        if symbol:
            cmd_args.append(symbol)
        if lines:
            cmd_args.extend(["--lines", lines])
        if new_name:
            cmd_args.extend(["--name", new_name])
    if apply:
        cmd_args.append("--apply")
    return _run_roam(cmd_args, root)


@mcp.tool()
def vuln_map(npm_audit: str = "", pip_audit: str = "", trivy: str = "",
             osv: str = "", generic: str = "", root: str = ".") -> dict:
    """Ingest vulnerability scanner reports and match to codebase symbols.

    WHEN TO USE: Call this to import vulnerability data from security scanners
    (npm audit, pip-audit, Trivy, OSV, or a generic JSON format). Each
    vulnerability is matched to symbols in the codebase index so you can
    assess real exposure. After ingestion, use ``vuln_reach`` to check
    reachability.

    Parameters
    ----------
    npm_audit:
        Path to npm audit JSON report.
    pip_audit:
        Path to pip-audit JSON report.
    trivy:
        Path to Trivy JSON report.
    osv:
        Path to OSV scanner JSON report.
    generic:
        Path to generic JSON vulnerability list.

    Returns: ingested vulnerabilities with symbol match status.
    """
    args = ["vuln-map"]
    if npm_audit:
        args.extend(["--npm-audit", npm_audit])
    if pip_audit:
        args.extend(["--pip-audit", pip_audit])
    if trivy:
        args.extend(["--trivy", trivy])
    if osv:
        args.extend(["--osv", osv])
    if generic:
        args.extend(["--generic", generic])
    return _run_roam(args, root)


@mcp.tool()
def vuln_reach(from_entry: str = "", cve: str = "", root: str = ".") -> dict:
    """Query reachability of ingested vulnerabilities through the call graph.

    WHEN TO USE: Call this after ``vuln_map`` to determine which vulnerabilities
    are actually reachable from entry points in your code. Unreachable vulns
    can be safely deprioritized. Shows shortest path, hop count, and blast
    radius for each reachable vulnerability.

    Parameters
    ----------
    from_entry:
        Check reachability from a specific entry point symbol.
    cve:
        Analyze a specific CVE ID.

    Returns: reachability status, paths, hop counts, and blast radius
    for each vulnerability.
    """
    args = ["vuln-reach"]
    if from_entry:
        args.extend(["--from", from_entry])
    if cve:
        args.extend(["--cve", cve])
    return _run_roam(args, root)


# ===================================================================
# Runtime trace tools
# ===================================================================


@mcp.tool()
def ingest_trace(trace_file: str, format: str = "", root: str = ".") -> dict:
    """Ingest runtime traces and match spans to symbols.

    WHEN TO USE: Call this to overlay runtime performance data on top of
    the static codebase graph. Supports OpenTelemetry, Jaeger, Zipkin,
    and a simple generic JSON format. After ingestion, use ``hotspots``
    to find discrepancies between static and runtime rankings.

    Parameters
    ----------
    trace_file:
        Path to the JSON trace file.
    format:
        Trace format: "otel", "jaeger", "zipkin", "generic".
        If empty, auto-detects from the JSON structure.

    Returns: ingested span count, matched/unmatched symbols, and per-span
    details including call count, latency, and error rate.
    """
    args = ["ingest-trace"]
    if format:
        args.extend([f"--{format}", trace_file])
    else:
        args.append(trace_file)
    return _run_roam(args, root)


@mcp.tool()
def runtime_hotspots(runtime_sort: bool = False, discrepancy: bool = False,
                     root: str = ".") -> dict:
    """Show runtime hotspots where static and runtime rankings disagree.

    WHEN TO USE: Call this after ingesting traces to find hidden hotspots
    -- symbols that static analysis considers safe but are runtime-critical
    (UPGRADE), or statically risky symbols with low traffic (DOWNGRADE).

    Parameters
    ----------
    runtime_sort:
        If True, sort by runtime metrics (call count).
    discrepancy:
        If True, only show static/runtime mismatches (UPGRADE/DOWNGRADE).

    Returns: hotspots with classification, static rank, runtime rank,
    and both static and runtime metrics.
    """
    args = ["hotspots"]
    if runtime_sort:
        args.append("--runtime")
    if discrepancy:
        args.append("--discrepancy")
    return _run_roam(args, root)


# ===================================================================
# Semantic search
# ===================================================================


@mcp.tool()
def search_semantic(query: str, top: int = 10, threshold: float = 0.05,
                    root: str = ".") -> dict:
    """Find symbols by natural language query using TF-IDF semantic search.

    WHEN TO USE: Call this when you have a conceptual description of what
    you are looking for rather than an exact symbol name. For example,
    "database connection handling" or "user authentication logic". Uses
    TF-IDF cosine similarity to rank symbols by relevance. For exact
    name matching, use ``search_symbol`` instead.

    Parameters
    ----------
    query:
        Natural language search query.
    top:
        Number of results to return (default 10).
    threshold:
        Minimum similarity score (default 0.05).

    Returns: ranked list of matching symbols with similarity scores,
    file paths, kinds, and line numbers.
    """
    args = ["search-semantic", query, "--top", str(top),
            "--threshold", str(threshold)]
    return _run_roam(args, root)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
