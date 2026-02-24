"""Next-step suggestion helper for roam commands.

Generates 2-3 actionable follow-up suggestions after each command result,
giving AI agents a clear path forward without additional planning.
"""

from __future__ import annotations


def suggest_next_steps(command: str, context: dict) -> list[str]:
    """Generate 2-3 actionable next steps based on command output.

    Parameters
    ----------
    command:
        The name of the command that just ran (e.g. "health", "context").
    context:
        A dict of key metrics from the command output used to tailor
        suggestions (e.g. ``{"score": 45, "symbol": "MyClass"}``).

    Returns
    -------
    list[str]
        At most 3 concise suggestion strings, each containing the exact
        ``roam`` command to run next.
    """
    steps: list[str] = []

    if command == "health":
        score = context.get("score", 100)
        critical = context.get("critical_issues", 0)
        cycles = context.get("cycles", 0)
        if score < 70:
            steps.append("Run `roam hotspots` to find the highest-churn files contributing to low health")
        if critical > 0 or cycles > 0:
            steps.append("Run `roam debt` to quantify the refactoring effort required")
        if score < 50:
            steps.append("Run `roam vibe-check` to detect AI code rot patterns")
        else:
            steps.append("Run `roam trends --days 30` to track the health score over time")

    elif command == "context":
        symbol = context.get("symbol", "")
        callers = context.get("callers", 0)
        blast_radius = context.get("blast_radius_symbols", 0)
        sym_arg = f" {symbol}" if symbol else ""
        if blast_radius > 5 or callers > 10:
            steps.append(f"Run `roam impact{sym_arg}` to see the full blast radius before modifying")
        steps.append(f"Run `roam preflight{sym_arg}` to check blast radius, tests, and fitness in one shot")
        if callers == 0:
            steps.append(f"Run `roam dead` to confirm whether this symbol is truly unreferenced")
        else:
            steps.append(f"Run `roam diagnose{sym_arg}` if this symbol is involved in a bug")

    elif command == "hotspots":
        upgrades = context.get("upgrades", 0)
        total = context.get("total", 0)
        if total == 0:
            steps.append("Run `roam ingest-trace` to load runtime trace data before using hotspots")
        else:
            if upgrades > 0:
                steps.append("Run `roam impact <symbol>` on each UPGRADE hotspot to assess change risk")
            steps.append("Run `roam split <file>` on any oversized files to reduce complexity")
            steps.append("Run `roam health` to see how runtime hotspots affect the overall health score")

    elif command == "diagnose":
        symbol = context.get("symbol", "")
        top_suspect = context.get("top_suspect", "")
        sym_arg = f" {symbol}" if symbol else ""
        suspect_arg = f" {top_suspect}" if top_suspect else sym_arg
        steps.append(f"Run `roam trace{sym_arg}` to trace execution paths leading to this symbol")
        if top_suspect:
            steps.append(f"Run `roam impact{suspect_arg}` to see how many callers the top suspect affects")
        steps.append(f"Run `roam context{sym_arg}` to get the full caller/callee graph for focused investigation")

    elif command == "dead":
        safe_count = context.get("safe", 0)
        review_count = context.get("review", 0)
        if safe_count > 0:
            steps.append("Run `roam safe-delete <symbol>` to safely remove a high-confidence dead symbol")
        if review_count > 0:
            steps.append("Run `roam dead --by-directory` to group dead code by directory for batch cleanup")
        steps.append("Run `roam dead --extinction <symbol>` to predict the cascade before deleting a symbol")

    return steps[:3]


def format_next_steps_text(steps: list[str]) -> str:
    """Format next steps as a plain-text section appended to command output.

    Parameters
    ----------
    steps:
        List of suggestion strings from :func:`suggest_next_steps`.

    Returns
    -------
    str
        A formatted string with a ``NEXT STEPS:`` header, or an empty
        string if *steps* is empty.
    """
    if not steps:
        return ""
    lines = ["", "NEXT STEPS:"]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}")
    return "\n".join(lines)
