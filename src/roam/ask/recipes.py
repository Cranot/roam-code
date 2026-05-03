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
        gates=("Treat low-confidence retrieve as a search miss", "Add seed files when top spans do not cover task terms"),
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
        followups=("roam rules --changed", "roam test-impact"),
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
        intent="Check for taint paths, attack surface, and reachable vulns",
        examples=(
            "audit security",
            "any attack surface I'm missing",
            "find sql injection or xss reach",
            "run a security review",
        ),
        keywords=(
            "security",
            "audit",
            "taint",
            "vuln",
            "attack",
            "sqli",
            "xss",
            "exploit",
            "cve",
        ),
        commands=(
            ("taint", ("--ci",)),
            ("adversarial", ()),
        ),
        summary=(
            "Taint reach (graph-BFS, OpenVEX-correct) plus the adversarial "
            "attack-surface review. Failures gate-able in CI via exit 5."
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
        gates=("Do not rename fixtures until reverse dependencies are updated", "Run impacted tests after fixture changes"),
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
]


def by_name(name: str) -> Recipe | None:
    """Look up a recipe by name. Returns ``None`` if unknown."""
    for r in RECIPES:
        if r.name == name:
            return r
    return None
