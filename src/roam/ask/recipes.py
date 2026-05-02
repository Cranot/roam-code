"""The recipe registry for ``roam ask``.

Each recipe is a tiny DAG of existing roam commands plus an intent
description used by the classifier. Twelve v12.0 recipes (full 22-recipe
surface lands in v12.1). Order is irrelevant — the classifier ranks
by TF-IDF similarity against the user's query.
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
    """

    name: str
    intent: str
    examples: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    commands: tuple[tuple[str, tuple[str, ...]], ...] = ()
    summary: str = ""


# v12.0 recipes — chosen for coverage of the most common workflows
# and to showcase the new v12 primitives (retrieve, critique, fleet, taint).
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
    ),
]


def by_name(name: str) -> Recipe | None:
    """Look up a recipe by name. Returns ``None`` if unknown."""
    for r in RECIPES:
        if r.name == name:
            return r
    return None
