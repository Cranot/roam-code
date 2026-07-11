"""The recipe registry for ``roam ask``.

Each recipe is a tiny DAG of existing roam commands plus intent and workflow
metadata used by the classifier and CLI. Order is irrelevant; the classifier
ranks by TF-IDF similarity against the user's query.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Recipe:
    """One roam-ask recipe.

    Attributes
    ----------
    name:
        Slug used in ``roam ask --explain`` output (kebab-case).
    intent:
        One-line description of what the user is trying to do —
        the corpus the classifier matches against.
    examples:
        Free-form example queries the classifier should match.
    keywords:
        High-confidence keywords. If the query contains any of these,
        the recipe gets a strong boost (verb-rules in the brainstorm).
    commands:
        Sequence of (cli_command, args_template) tuples. ``args_template``
        may contain ``{symbol}`` / ``{task}`` placeholders filled at
        runtime from the parsed query.
    summary:
        How to summarise the combined output to the user.
    phase:
        Workflow phase this recipe best supports.
    perspectives:
        Review lenses an agent should apply when interpreting results.
    followups:
        High-value next commands after the recipe completes.
    gates:
        Stop conditions or quality bars that should be satisfied before
        continuing the workflow.
    """

    name: str
    intent: str
    examples: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
    summary: str = ""
    phase: str = ""
    perspectives: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()


# Recipes are chosen for coverage of the most common workflows and to showcase
# the v12 primitives (retrieve, critique, fleet, taint, fixture impact).
RECIPES: list[Recipe] = [
    Recipe(
        name="safe-delete-check",
        intent="Decide whether it's safe to delete or remove a symbol",
        examples=(
            "is it safe to delete UserSession",
            "can I remove the legacy Auth class",
            "what breaks if I drop handle_login",
        ),
        keywords=("delete", "remove", "drop", "kill", "deprecate", "safe to"),
        commands=(
            ("preflight", ("{symbol}",)),
            ("uses", ("{symbol}",)),
        ),
        summary=(
            "Combined blast radius + caller analysis. Treat HIGH preflight "
            "verdict or any caller in production code as a stop-sign."
        ),
        phase="scope",
        perspectives=("blast-radius", "caller-safety", "deletion-readiness"),
        followups=("roam safe-delete {symbol}", "roam dead --summary"),
        gates=("Stop on HIGH/CRITICAL preflight risk", "Do not delete while production callers remain"),
    ),
    Recipe(
        name="onboard",
        intent="Get oriented in an unfamiliar codebase",
        examples=(
            "where do I start in this repo",
            "what does this codebase do",
            "give me a tour",
            "I'm new to this project",
        ),
        keywords=("start", "onboard", "tour", "new", "orientation", "overview"),
        commands=(("understand", ()),),
        summary=(
            "Full briefing: stack, architecture, health, hotspots. Pair "
            "with `roam tour --top 5` for the high-PageRank entry points."
        ),
        phase="discover",
        perspectives=("architecture-map", "hotspots", "reading-order"),
        followups=("roam tour --top 5", "roam dashboard"),
        gates=("Run or refresh the index before trusting the map", "Confirm hotspots before choosing a first edit"),
    ),
    Recipe(
        name="trace-task",
        intent=("Find the right code spans for a free-form natural-language task (uses the v12 graph-aware retrieve)"),
        examples=(
            "where does the login flow validate sessions",
            "trace the n+1 query in checkout",
            "find the symbol that handles webhook signatures",
            "show me the code for plan splitting",
        ),
        keywords=("trace", "find", "where", "show me", "look for", "locate"),
        commands=(("retrieve", ("{task}",)),),
        summary=(
            "Ranked spans with justification tags (PageRank + co-change + "
            "clones + lexical). Each span includes file:line and a why row."
        ),
        phase="retrieve",
        perspectives=("retrieval-relevance", "structural-ranking", "token-budget"),
        followups=("roam context {symbol}", "roam hover {symbol}"),
        gates=(
            "Treat low-confidence retrieve as a search miss",
            "Add seed files when top spans do not cover task terms",
        ),
    ),
    Recipe(
        name="locate-symbol",
        intent=(
            "Locate where a NAMED symbol is defined and what calls it — precise "
            "definition site(s) + caller list, not fuzzy span retrieval"
        ),
        examples=(
            "where is _canonicalize_task defined and what calls it",
            "where is compile_plan defined",
            "what calls handle_login",
            "who calls UserSession.refresh",
            "find the definition and callers of parse_config",
            "definition of make_msgid",
            "where is get_fqdn declared",
            "callers of compile_for_artifact",
        ),
        # Symbol-precise shapes only — deliberately NOT "where"/"find"/"locate"
        # (those belong to trace-task's fuzzy span retrieve for prose queries).
        # These keywords fire when the query names a concrete symbol + asks for
        # its definition or callers, so search+uses (exact) beats retrieve (fuzzy).
        keywords=(
            "defined",
            "definition",
            "declared",
            "what calls",
            "who calls",
            "callers of",
            "call sites",
        ),
        commands=(
            ("search", ("{symbol}",)),
            ("uses", ("{symbol}",)),
        ),
        summary=(
            "Exact definition site(s) from `roam search` plus the inbound caller "
            "list from `roam uses` — the precise answer for a NAMED symbol. roam "
            "ask routes here (not the fuzzy retrieve) when the query names a "
            "concrete identifier and asks where it's defined / what calls it."
        ),
        phase="locate",
        perspectives=("definition-site", "caller-list", "precision"),
        followups=("roam context {symbol}", "roam hover {symbol}", "roam trace <caller> {symbol}"),
        gates=("If search returns multiple definitions, prefer the source (non-test) one",),
    ),
    Recipe(
        name="module-deps",
        intent=(
            "Show what a named FILE/module imports and what imports it — "
            "dependency direction (importers + imports) for a concrete file"
        ),
        examples=(
            "what imports cmd_ask.py",
            "what files import recipes.py",
            "which files import utils.py",
            "what depends on compiler.py",
            "imports of formatter.py",
            "who imports cli.py",
            "importers of mcp_server.py",
            "dependencies of indexer.py",
            "what is the import graph for runner.py",
        ),
        # File-import direction. Distinct from dependency-update (PACKAGE upgrades)
        # and architecture-debt (project-wide coupling). Needs the filename WITH
        # extension — {file} extracts it; roam deps rejects a bare stem.
        # "what FILES import X" added 2026-06-07: the "files" token diluted the
        # match below threshold (codex nav A/B q2 phrasing → no-match → wasted
        # fallback call); now routes + roam_ask executes module-deps in one call.
        keywords=(
            "imports",
            "importers",
            "depends on",
            "dependencies of",
            "who imports",
            "what imports",
            "import graph",
            "files import",
            "what files import",
            "which files import",
        ),
        commands=(("deps", ("{file}",)),),
        summary=(
            "`roam deps <file>` — N imports + N importers in one envelope, the "
            "precise dependency-direction answer for a named file. Pass the "
            "filename WITH its extension. For PACKAGE upgrade/removal safety use "
            "dependency-update; for project-wide coupling use architecture-debt."
        ),
        phase="dependency",
        perspectives=("imports", "importers", "coupling-direction"),
        followups=("roam coupling {file}", "roam impact {file}", "roam context {file}"),
        gates=("File imports (this recipe) are not the same as package deps (dependency-update)",),
    ),
    Recipe(
        name="complexity-ranking",
        intent=(
            "Rank the most complex / highest-cognitive-complexity functions — the "
            "precise 'most complex' answer, not churn-weighted hotspots"
        ),
        examples=(
            "top 5 most complex functions",
            "most complex code",
            "what are the most complex functions",
            "complexity ranking",
            "which functions are the gnarliest",
            "rank functions by complexity",
        ),
        keywords=("most complex", "complexity ranking", "complex functions", "gnarliest", "by complexity"),
        commands=(("complexity", ("-n", "10")),),
        summary=(
            "Top functions by cognitive complexity (SonarSource-compatible) from "
            "`roam complexity`. The precise 'most complex' ranking — hot-spots "
            "weights churn too, so use this when the question is purely complexity."
        ),
        phase="prioritize",
        perspectives=("complexity", "refactor-priority"),
        followups=("roam preflight {symbol}", "roam hotspots --top 10"),
        gates=("Pair high complexity with test coverage before refactoring",),
    ),
    Recipe(
        name="describe-file",
        intent=(
            "Explain what a named FILE/module is for — its role + exported surface (file-level, not a single symbol)"
        ),
        examples=(
            "what does compiler.py do",
            "what is cmd_ask.py for",
            "explain the runner.py module",
            "purpose of formatter.py",
            "summarize what classifier.py does",
            "overview of recipes.py",
        ),
        # File-anchored phrasings — deliberately NOT bare "what does X do" (that's
        # symbol territory → why-this-exists); these fire on file-role questions.
        keywords=("what is for", "purpose of", "overview of", "summarize", "what's in", "role of"),
        commands=(("file", ("{file}",)),),
        summary=(
            "`roam file <path>` — the file's skeleton (exported defs/classes + "
            "structure) IS the answer to 'what does this file do'. For a single "
            "symbol's purpose use why-this-exists instead."
        ),
        phase="understand",
        perspectives=("file-role", "exported-surface", "structure"),
        followups=("roam understand", "roam deps {file}"),
        gates=("For one symbol (not a whole file), prefer why-this-exists",),
    ),
    Recipe(
        name="verify-patch",
        intent="Audit a patch against the indexed graph before committing",
        examples=(
            "review my changes",
            "is my pending diff safe",
            "audit the patch I just made",
            "did I miss anything",
        ),
        keywords=("review", "audit", "check", "safe", "verify", "patch", "diff"),
        commands=(
            ("diff", ()),
            ("critique", ()),
        ),
        summary=(
            "Blast radius of uncommitted changes plus the killer "
            "clones-not-edited check. Exit 5 = high-severity finding "
            "(CI-gateable)."
        ),
        phase="review",
        perspectives=("blast-radius", "clone-consistency", "intent-alignment"),
        followups=("roam rules --ci", "roam test-impact", "roam stale-refs --fix preview"),
        gates=("Stop on high-severity critique findings", "Add or run impacted tests before merge"),
    ),
    Recipe(
        name="plan-fleet",
        intent="Split work across multiple agents in parallel",
        examples=(
            "split this refactor across multiple agents",
            "plan parallel work for the auth migration",
            "partition the codebase for fleet",
            "I want to dispatch 4 agents",
        ),
        keywords=("split", "partition", "parallel", "fleet", "dispatch", "agents"),
        commands=(("fleet", ("plan", "{task}")),),
        summary=(
            "Graph-aware partition (Louvain + co-change + PageRank "
            "anchors) emits .roam-fleet.json for Composio / Copilot / raw."
        ),
        phase="parallelize",
        perspectives=("parallelism", "write-conflicts", "ownership-boundaries"),
        followups=("roam fleet verify .roam-fleet.json", "roam partition"),
        gates=("Do not dispatch overlapping write scopes", "Keep dependent partitions in later phases"),
    ),
    # ------------------------------------------------------------------
    # New in v12.0 second batch (recipes 6-12)
    # ------------------------------------------------------------------
    Recipe(
        name="find-bug",
        intent="Diagnose a failure given a symptom symbol or stack frame",
        examples=(
            "diagnose why handle_login is failing",
            "what is wrong with the AuthService.refresh method",
            "root cause of the broken parser",
            "debug the crashing webhook handler",
        ),
        keywords=("diagnose", "bug", "broken", "crash", "wrong", "root cause", "debug"),
        commands=(
            ("diagnose", ("{symbol}",)),
            ("retrieve", ("{task}",)),
        ),
        summary=(
            "Root-cause ranking from `diagnose` plus retrieved spans for "
            "additional context. Look for HIGH confidence findings first."
        ),
        phase="debug",
        perspectives=("root-cause", "side-effects", "retrieval-context"),
        followups=("roam trace {symbol}", "roam effects {symbol}"),
        gates=("Prioritize HIGH-confidence suspects first", "Verify side effects before changing shared code"),
    ),
    Recipe(
        name="trace-flow",
        intent="Trace the call chain through or from a symbol",
        examples=(
            "trace the call chain through handle_login",
            "what calls UserSession.refresh",
            "follow the path from request to database",
            "show callers of the auth middleware",
        ),
        keywords=("trace", "callers", "callees", "path", "call chain", "follow", "flow"),
        commands=(
            ("trace", ("{symbol}",)),
            ("uses", ("{symbol}",)),
        ),
        summary=(
            "k-shortest paths plus inbound caller list. Useful for "
            "understanding ripple effects before changing a hub function."
        ),
        phase="trace",
        perspectives=("call-chain", "consumer-map", "execution-path"),
        followups=("roam graph {symbol}", "roam effects {symbol}"),
        gates=("Stop when trace reaches unindexed or dynamic dispatch", "Validate external edges manually"),
    ),
    Recipe(
        name="what-broke",
        intent="Spot recent regressions and architectural drift",
        examples=(
            "what regressed since last week",
            "show recent quality drops",
            "did anything get worse this sprint",
            "compare current health to last release",
        ),
        keywords=("regress", "broke", "worse", "drift", "drop", "since", "recently"),
        commands=(
            ("trends", ("--compare",)),
            ("pr-diff", ()),
        ),
        summary=(
            "Snapshot delta + structural PR diff. Watch for new cycles, "
            "rising complexity, falling test ratio, or growing god-components."
        ),
        phase="monitor",
        perspectives=("trend-regression", "structural-delta", "release-risk"),
        followups=("roam trends --compare --json", "roam report quality"),
        gates=("Investigate new cycles before release", "Treat falling health score as a release risk"),
    ),
    Recipe(
        name="hot-spots",
        intent="Find code that changes often and is also complex",
        examples=(
            "show me the riskiest code",
            "where are the high-churn complex files",
            "what should I refactor first",
            "find hotspots",
        ),
        keywords=("hotspot", "risk", "churn", "complex", "refactor", "priority", "riskiest"),
        commands=(
            ("hotspots", ("--top", "10")),
            ("debt", ()),
        ),
        summary=(
            "Top-10 churn × complexity intersection plus the technical-debt "
            "ranking. These are the highest-leverage refactor targets."
        ),
        phase="prioritize",
        perspectives=("churn", "complexity", "refactor-roi"),
        followups=("roam debt", "roam preflight {symbol}"),
        gates=("Prefer hotspots with tests or clear boundaries", "Preflight the selected target before refactoring"),
    ),
    Recipe(
        name="security-audit",
        intent="Trace reachable taint paths to attack-surface sinks (sqli / xss / open redirect)",
        examples=(
            "trace attack surface",
            "find sql injection or xss reach",
            "review reachable taint paths",
            "run an adversarial review",
        ),
        keywords=(
            "taint",
            "attack",
            "sqli",
            "xss",
            "exploit",
            "cve",
            "reach",
            "adversarial",
        ),
        commands=(
            ("taint", ("--ci",)),
            ("adversarial", ()),
        ),
        summary=(
            "Taint reach (graph-BFS, OpenVEX-correct) plus the adversarial "
            "attack-surface review. Failures gate-able in CI via exit 5. "
            "For a broader audit covering secrets and supply-chain, use "
            "audit-security."
        ),
        phase="secure",
        perspectives=("taint-reachability", "attack-surface", "adversarial-review"),
        followups=("roam taint --json", "roam adversarial --json"),
        gates=("Stop on reachable taint to sink", "Require mitigation or documented suppression for exploitable paths"),
    ),
    Recipe(
        name="fixture-impact",
        intent=(
            "Show what tests / fixtures break if a pytest fixture is renamed, removed, or has its return shape changed"
        ),
        examples=(
            "if I rename cli_runner what tests break",
            "what depends on the cli_runner fixture",
            "who uses indexed_project",
            "blast radius of indexed_project",
            "find tests that consume mock_db_session",
        ),
        keywords=("fixture", "fixtures", "pytest", "conftest"),
        commands=(("pytest-fixtures", ("{symbol}", "--reverse")),),
        summary=(
            "Walks the implicit pytest fixture-parameter dependency graph "
            "in reverse — fixtures and tests that consume the named fixture "
            "transitively. Pass the fixture name as an identifier (snake_case "
            "or PascalCase). ``--json`` for the full list when output is "
            "capped."
        ),
        phase="test-impact",
        perspectives=("test-dependency", "implicit-edges", "rename-risk"),
        followups=("roam pytest-fixtures {symbol} --reverse --json", "roam test-impact"),
        gates=(
            "Do not rename fixtures until reverse dependencies are updated",
            "Run impacted tests after fixture changes",
        ),
    ),
    Recipe(
        name="dead-code-sweep",
        intent="Find unused or unreachable symbols ready for deletion",
        examples=(
            "find dead code",
            "what's unused",
            "scan for orphaned functions",
            "anything I can delete",
        ),
        keywords=("dead", "unused", "orphan", "unreachable", "obsolete", "cleanup"),
        commands=(("dead", ()),),
        summary=(
            "Dead-symbol ranking by aging + decay score. Pipe a candidate "
            "through `roam safe-delete <name>` before removing."
        ),
        phase="cleanup",
        perspectives=("reachability", "deletion-cascade", "noise-reduction"),
        followups=("roam safe-delete {symbol}", "roam preflight {symbol}"),
        gates=("Confirm dead-code candidates with safe-delete", "Avoid deleting public API without ownership review"),
    ),
    Recipe(
        name="architecture-debt",
        intent="Quantify architectural coupling and god-component risk",
        examples=(
            "how coupled is this codebase",
            "show architecture debt",
            "find god components",
            "what modules talk to too many others",
        ),
        keywords=(
            "couple",
            "coupling",
            "debt",
            "god",
            "tangle",
            "monolith",
            "modular",
            "architecture",
        ),
        commands=(
            ("debt", ()),
            ("coupling", ("--top", "10")),
        ),
        summary=(
            "Debt aggregate plus top-10 highly-coupled modules. Pair with "
            "`roam fingerprint` for spectral-gap and Fiedler analysis."
        ),
        phase="architecture",
        perspectives=("coupling", "god-components", "boundary-quality"),
        followups=("roam fingerprint", "roam health --json"),
        gates=("Stop on new cycles", "Extract boundaries before moving highly coupled modules"),
    ),
    # 12.15 — recipe expansion. Cover the agent workflows that
    # showed up repeatedly in dogfood but weren't classified.
    Recipe(
        name="trace-bug",
        intent="Diagnose a failing test, error, or bug back to its root cause",
        examples=(
            "why is my test failing",
            "trace this exception to its source",
            "debug this error",
            "what's causing this bug",
        ),
        keywords=("debug", "bug", "error", "failing", "broken", "crash", "exception", "traceback"),
        commands=(
            ("diagnose", ("{symbol}",)),
            ("trace", ("{symbol}",)),
            ("affected-tests", ("{symbol}",)),
        ),
        summary=(
            "Risk-ranked upstream/downstream suspects, dependency paths, and the "
            "tests that exercise the symbol — three lenses on the same bug."
        ),
        phase="diagnose",
        perspectives=("root-cause", "execution-path", "test-coverage"),
        followups=("roam context {symbol}", "roam diff"),
        gates=("Confirm reproduction before changing anything",),
    ),
    Recipe(
        name="who-owns",
        intent="Identify the human owners or expert authors of a symbol or area",
        examples=(
            "who owns this code",
            "who should review my change to UserSession",
            "who is the expert on auth",
            "find the maintainer",
        ),
        keywords=("owner", "owns", "expert", "maintainer", "author", "review", "loop in"),
        commands=(
            ("owner", ("{symbol}",)),
            ("suggest-reviewers", ()),
            ("bus-factor", ()),
        ),
        summary=(
            "Code-ownership map (CODEOWNERS + git churn) plus suggested reviewers "
            "and bus-factor warnings. Use to route changes through the right humans."
        ),
        phase="ownership",
        perspectives=("ownership", "review-routing", "bus-factor"),
        followups=("roam codeowners --check", "roam dev-profile <author>"),
        gates=("Loop in original author when bus-factor is 1",),
    ),
    Recipe(
        name="what-changed",
        intent="Summarise recent activity in a file, symbol, or directory",
        examples=(
            "what changed in this file recently",
            "show recent commits to auth.py",
            "what's the history of UserSession",
            "who last touched this",
        ),
        keywords=("recent", "history", "commits", "lately", "last week", "last month", "what changed"),
        commands=(
            ("weather", ()),
            ("trends", ("--days", "30")),
        ),
        summary=("Hot file weather plus 30-day trend snapshot. Use to spot churn spikes before reviewing a PR."),
        phase="discover",
        perspectives=("churn", "velocity", "regression-risk"),
        followups=("roam diagnose <symbol>", "roam pr-risk"),
        gates=("Re-run trends weekly to track movement",),
    ),
    Recipe(
        name="audit-security",
        intent="Run a comprehensive security audit covering secrets, taint flows, dependency vulnerabilities, and supply chain",
        examples=(
            "audit security",
            "comprehensive security audit",
            "any vulnerabilities or secrets here",
            "release-readiness security check",
        ),
        keywords=("security", "audit", "vulnerability", "vuln", "secret", "supply-chain", "release"),
        commands=(
            ("secrets", ()),
            ("taint", ()),
            ("vulns", ()),
            ("supply-chain", ()),
        ),
        summary=(
            "Four parallel security passes: secret leakage, taint reachability, "
            "known-CVE mapping, supply-chain trust. Treat any HIGH severity "
            "as a release blocker."
        ),
        phase="security",
        perspectives=("secrets", "data-flow", "supply-chain", "vulnerability-reachability"),
        followups=("roam taint --rules-pack sqli", "roam cga emit --include-taint"),
        gates=("Stop on any HIGH severity finding",),
    ),
    Recipe(
        name="explore-impact",
        intent="Predict the blast radius and risk of an upcoming change",
        examples=(
            "what will break if I change UserSession",
            "predict the impact of editing auth.py",
            "blast radius of this change",
            "is this change risky",
        ),
        keywords=("impact", "blast", "break", "predict", "ripple", "risky"),
        commands=(
            ("impact", ("{symbol}",)),
            ("affected-tests", ("{symbol}",)),
            ("preflight", ("{symbol}",)),
        ),
        summary=(
            "Transitive blast radius (PageRank-personalized), test coverage, "
            "and the unified preflight verdict — three lenses on one change."
        ),
        phase="scope",
        perspectives=("blast-radius", "test-coverage", "risk"),
        followups=("roam closure {symbol}", "roam diff"),
        gates=("Stop on CRITICAL preflight risk", "Cover all impacted tests before merging"),
    ),
    Recipe(
        name="find-similar",
        intent="Find code that duplicates or closely resembles a target",
        examples=(
            "find duplicates of this function",
            "are there similar implementations of UserSession",
            "show me clones",
            "what code looks like this",
        ),
        keywords=("duplicate", "clone", "similar", "copy", "redundant", "dedup"),
        commands=(
            ("clones", ("--threshold", "0.7")),
            ("duplicates", ()),
        ),
        summary=(
            "AST-hash clone detection + lexical duplicate finder. Use to consolidate copy-paste before a refactor."
        ),
        phase="cleanup",
        perspectives=("clone-detection", "deduplication", "consolidation"),
        followups=("roam suggest-refactoring", "roam plan-refactor"),
        gates=("Confirm tests still pass after consolidation",),
    ),
    Recipe(
        name="why-this-exists",
        intent="Understand why a piece of code exists — purpose, history, role",
        examples=(
            "why does this function exist",
            "what is UserSession for",
            "explain this code",
            "purpose of handle_login",
        ),
        keywords=("why", "purpose", "what for", "explain", "describe", "role"),
        commands=(
            ("why", ("{symbol}",)),
            ("symbol", ("{symbol}",)),
            ("hover", ("{symbol}",)),
        ),
        summary=(
            "Architectural role classification, full symbol metadata, and a "
            "compact one-line summary — three views on one symbol's purpose."
        ),
        phase="understand",
        perspectives=("role", "metadata", "summary"),
        followups=("roam context {symbol}", "roam trace <caller> {symbol}"),
        gates=("Don't repeat already-known intent — focus on unstated rationale",),
    ),
    Recipe(
        name="check-pr",
        intent="Run a full pre-merge review on a pull request",
        examples=(
            "review this PR",
            "is my pull request ready to merge",
            "audit the diff",
            "pre-merge check",
        ),
        keywords=("pr", "pull request", "merge", "ship", "review", "ready"),
        commands=(
            ("pr-risk", ()),
            ("pr-diff", ()),
            ("breaking", ()),
        ),
        summary=(
            "Risk score (with driver named), structural diff impact, and "
            "breaking-change detection. Pair with `roam critique` for the "
            "graph-grounded patch verifier."
        ),
        phase="review",
        perspectives=("risk-score", "structural-diff", "breaking-changes"),
        followups=("roam critique", "roam suggest-reviewers"),
        gates=("Stop on HIGH risk score", "Stop on any breaking change without major version bump"),
    ),
    Recipe(
        name="explore-tests",
        intent="Map test coverage, gaps, and orphaned tests",
        examples=(
            "show test coverage",
            "find untested code",
            "what tests exercise this",
            "are there orphan tests",
        ),
        keywords=("test", "coverage", "untested", "tests", "fixtures"),
        commands=(
            ("test-map", ()),
            ("coverage-gaps", ("--gate-pattern", ".*")),
            ("affected-tests", ("{symbol}",)),
        ),
        summary=(
            "Source↔test mapping, coverage gaps for the gate pattern, "
            "and the test set that exercises a specific symbol."
        ),
        phase="testing",
        perspectives=("coverage", "test-discovery", "untested-paths"),
        followups=("roam test-scaffold {symbol}", "roam pytest-fixtures"),
        gates=("Cover every gate-protected entry point",),
    ),
    Recipe(
        name="dependency-update",
        intent="Assess the safety of upgrading or removing a dependency",
        examples=(
            "is it safe to upgrade flask",
            "what depends on requests",
            "remove this package",
            "audit this dependency",
        ),
        keywords=("upgrade", "depend", "package", "library", "version", "lock", "remove"),
        commands=(
            ("supply-chain", ()),
            ("vulns", ()),
            ("vuln-reach", ()),
        ),
        summary=(
            "Dependency tree + known-CVE mapping + reachability. The "
            "reachability layer turns a 'CVE in transitive dep' alert into "
            "an actionable 'this CVE is reachable from your code'."
        ),
        phase="dependency",
        perspectives=("supply-chain", "vulnerability", "reachability"),
        followups=("roam sbom --format cyclonedx", "roam cga emit"),
        gates=("Block upgrades that introduce reachable HIGH CVEs",),
    ),
    Recipe(
        name="visualize-architecture",
        intent="Generate a diagram of the architecture, layers, or a focus area",
        examples=(
            "show me an architecture diagram",
            "draw the layers",
            "visualize the auth subsystem",
            "diagram of UserSession's neighborhood",
        ),
        keywords=("diagram", "visualize", "draw", "graph", "mermaid", "picture", "layout"),
        commands=(
            ("visualize", ()),
            ("layers", ()),
            ("clusters", ()),
        ),
        summary=(
            "Mermaid architecture diagram + topological layer breakdown + "
            "Louvain cluster grouping. Use the diagram in PR descriptions "
            "or design documents."
        ),
        phase="architecture",
        perspectives=("structure", "layers", "clusters"),
        followups=("roam visualize --focus <symbol>", "roam fingerprint"),
        gates=("Diagrams complement reading the code, they don't replace it",),
    ),
    Recipe(
        name="find-broken-links",
        intent="Find dangling file references — markdown links / HTML href-src / backtick paths whose target is missing",
        examples=(
            "find broken links",
            "any dead doc links in this repo",
            "check for stale file references",
            "what docs point at deleted files",
            "audit my readme for missing links",
            "broken doc links after the rename",
        ),
        keywords=(
            # NOTE: keyword bonus is substring-matched against the query
            # (`"ref" in "refresh"` would over-match), so we keep tokens
            # that are unlikely to appear inside unrelated words. Tokens
            # like "ref" / "refs" / "link" are dropped for that reason —
            # the longer forms below still pick up real intent.
            "broken",
            "dangling",
            "stale",
            "links",
            "reference",
            "references",
            "dead link",
            "dead links",
        ),
        commands=(("stale-refs", ()),),
        summary=(
            "Scans every text file in the repo, surfaces dangling markdown "
            "links, HTML href/src, and backtick paths whose target no "
            "longer exists. Includes basename-match rename hints. Pure "
            "filesystem scan — no index needed."
        ),
        phase="audit",
        perspectives=("doc-hygiene", "post-refactor-cleanup", "ci-gate"),
        followups=(
            "roam stale-refs --gate (CI gate, exit 5 on findings)",
            "roam stale-refs --by-file (group by source doc)",
            "roam stale-refs --watch (always-on terminal during refactor)",
            "roam stale-refs --fix preview (auto-rewrite HIGH-confidence)",
            "roam stale-refs --baseline-save b.json (freeze acknowledged debt)",
            "roam stale-refs --check-external (validate http(s) URLs too)",
            "roam lsp (squiggly underlines in your editor as you type)",
            "roam doc-staleness (stale docstring content)",
            "roam docs-coverage (missing public-symbol docs)",
        ),
        gates=(
            "Use --gate in CI to block merges with broken doc references",
            "Suppress historical mentions with --ignore CHANGELOG.md if needed",
        ),
    ),
    Recipe(
        name="is-feature-wired",
        intent=(
            "Check whether a feature is actually wired end-to-end — its "
            "handler symbol is reachable from an entry point AND reaches a "
            "data read/write site (not an orphaned stub)"
        ),
        examples=(
            "is create_user actually hooked up",
            "is the checkout feature wired end to end",
            "is handle_webhook actually reachable and does it write data",
            "is this feature actually wired",
            "check if process_payment is hooked up end-to-end",
            "is register_route wired to anything",
        ),
        # Distinctive multi-word tokens only — deliberately NOT
        # "reach"/"reachable"/"orphan"/"trace"/"flow", which collide with
        # security-audit / dead-code-sweep / trace-* keyword bonuses
        # (kw_bonus is substring-matched, classifier.py:140).
        keywords=(
            "wired",
            "hooked up",
            "hooked-up",
            "end to end",
            "end-to-end",
            "actually wired",
            "actually hooked",
            "fully wired",
        ),
        commands=(
            ("entry-points", ()),
            ("uses", ("{symbol}",)),
            ("effects", ("{symbol}",)),
        ),
        summary=(
            "Three lenses on 'is this feature actually hooked up?': the "
            "entry-point catalog (which symbols are real entries + their "
            "reachability coverage), the inbound caller list for the feature "
            "symbol (empty = orphaned handler, unless it IS an entry), and "
            "its transitive side-effects (io_read/io_write/writes_db = it "
            "reaches a data site). Wired = reachable from an entry AND "
            "reaches a read/write effect. No callers or no data effect = a "
            "stub that isn't actually hooked up."
        ),
        phase="verify",
        perspectives=(
            "entry-reachability",
            "handler-connectivity",
            "data-read-write-site",
        ),
        followups=(
            "roam trace <entry> {symbol}",
            "roam effects {symbol} --transitive",
            "roam impact {symbol}",
        ),
        gates=(
            "Treat zero inbound callers (and not itself an entry point) as an unwired/orphaned handler",
            "Treat absence of any io_read/io_write/writes_db effect as 'reaches no data site — likely a stub'",
        ),
    ),
    Recipe(
        name="field-trace",
        intent=(
            "Trace every layer touchpoint of a field/symbol BEFORE a rename — "
            "code references, cross-file text references, consumers, and "
            "FE<->BE API-bridge links (DB->BE->API->FE blast radius)"
        ),
        examples=(
            "blast radius of renaming user_email",
            "what touches the email field across layers",
            "every layer touchpoint before renaming created_at",
            "rename the user_id field everywhere - what will it touch",
            "field rename impact from database to frontend",
            "trace the price field across backend api and frontend",
        ),
        # Distinctive multi-word / suffixed tokens ONLY — the keyword bonus
        # is substring-matched (classifier.py:139-141), so a bare "rename"
        # keyword would fire on fixture-impact's own example query
        # "if I rename cli_runner what tests break" (+0.25 steal).
        # "renaming" / "field rename" do not substring-match that query.
        keywords=(
            "renaming",
            "rename a field",
            "rename the field",
            "field rename",
            "rename this field",
            "blast radius of renaming",
            "every layer",
            "across layers",
            "touchpoint",
        ),
        commands=(
            ("uses", ("{symbol}",)),
            ("refs-text", ("{symbol}",)),
            ("impact", ("{symbol}",)),
            ("x-lang", ()),
        ),
        summary=(
            "Four layers on one field. Layer 1 — direct code consumers: "
            "`uses` lists callers, importers, and inheritors of the symbol. "
            "Layer 2 — text references: `refs-text` groups every textual "
            "hit by surface (code with reachability + PageRank, tests, docs, "
            "config, generated, vendored, dead) and annotates cross-language "
            "bridge links; its verdict (SAFE-TO-REMOVE / REVIEW / "
            "LOAD-BEARING) is the rename-risk headline. Layer 3 — "
            "transitive consumers: `impact` gives the PageRank-personalized "
            "blast radius beyond direct callers. Layer 4 — FE<->BE API "
            "bridge: `x-lang` correlates fetch/axios/requests URLs to "
            "backend routes; filter its rest-api links for URLs whose route "
            "handler touches the field. Note the bridge matches URLs, not "
            "JSON payload keys — a rename that changes a serialized key is "
            "invisible to it."
        ),
        phase="scope",
        perspectives=(
            "code-refs",
            "text-refs",
            "consumer-blast-radius",
            "api-bridge",
        ),
        followups=(
            "roam mutate rename {symbol} <new-name>  # dry-run (default) = exact line-level rename plan",
            "roam refs-text {symbol} --per-match-detail",
            "roam impact {symbol} --json",
            "roam x-lang --scope src/",
        ),
        gates=(
            "Do not rename until every layer group is reviewed",
            "refs-text LOAD-BEARING verdict or reachable config/docs hits = update those surfaces in the same change",
            "A rename that changes a serialized JSON key is NOT visible to the rest-api bridge — check FE payload accessors by hand",
        ),
    ),
]


def by_name(name: str) -> Recipe | None:
    """Look up a recipe by name. Returns ``None`` if unknown."""
    for r in RECIPES:
        if r.name == name:
            return r
    return None
