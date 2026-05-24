"""Next-step suggestion helper for roam commands.

Generates 2-3 actionable follow-up suggestions after each command result,
giving AI agents a clear path forward without additional planning.
"""

from __future__ import annotations


def _steps_for_health(context: dict) -> list[str]:
    """Suggestions after `roam health` — branch on score, criticals, cycles."""
    steps: list[str] = []
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
    return steps


def _steps_for_context(context: dict) -> list[str]:
    """Suggestions after `roam context` — branch on blast radius and callers."""
    steps: list[str] = []
    symbol = context.get("symbol", "")
    callers = context.get("callers", 0)
    blast_radius = context.get("blast_radius_symbols", 0)
    sym_arg = f" {symbol}" if symbol else ""
    if blast_radius > 5 or callers > 10:
        steps.append(f"Run `roam impact{sym_arg}` to see the full blast radius before modifying")
    steps.append(f"Run `roam preflight{sym_arg}` to check blast radius, tests, and fitness in one shot")
    if callers == 0:
        steps.append("Run `roam dead` to confirm whether this symbol is truly unreferenced")
    else:
        steps.append(f"Run `roam diagnose{sym_arg}` if this symbol is involved in a bug")
    return steps


def _steps_for_hotspots(context: dict) -> list[str]:
    """Suggestions after `roam hotspots` — gate on trace data presence."""
    steps: list[str] = []
    upgrades = context.get("upgrades", 0)
    total = context.get("total", 0)
    if total == 0:
        steps.append("Run `roam ingest-trace` to load runtime trace data before using hotspots")
    else:
        if upgrades > 0:
            steps.append("Run `roam impact <symbol>` on each UPGRADE hotspot to assess change risk")
        steps.append("Run `roam split <file>` on any oversized files to reduce complexity")
        steps.append("Run `roam health` to see how runtime hotspots affect the overall health score")
    return steps


def _steps_for_diagnose(context: dict) -> list[str]:
    """Suggestions after `roam diagnose` — top-suspect-aware trace/impact chain."""
    steps: list[str] = []
    symbol = context.get("symbol", "")
    top_suspect = context.get("top_suspect", "")
    sym_arg = f" {symbol}" if symbol else ""
    suspect_arg = f" {top_suspect}" if top_suspect else sym_arg
    # CONSTRAINT 12: `roam trace` requires SOURCE TARGET (two args).
    # Previously suggested `roam trace <symbol>` which Click rejects
    # with "Missing argument 'TARGET'" — non-executable. Use the
    # top-suspect -> symbol form when both names exist; otherwise
    # fall back to `roam impact` (single-arg, gives caller chains).
    if top_suspect and symbol:
        steps.append(
            f"Run `roam trace {top_suspect} {symbol}` to trace the execution path from the top suspect to this symbol"
        )
    elif symbol:
        steps.append(f"Run `roam impact{sym_arg}` to see the transitive caller chain affected by this symbol")
    if top_suspect:
        steps.append(f"Run `roam impact{suspect_arg}` to see how many callers the top suspect affects")
    steps.append(f"Run `roam context{sym_arg}` to get the full caller/callee graph for focused investigation")
    return steps


def _steps_for_dead(context: dict) -> list[str]:
    """Suggestions after `roam dead` — safe-delete, by-directory, extinction."""
    steps: list[str] = []
    safe_count = context.get("safe", 0)
    review_count = context.get("review", 0)
    if safe_count > 0:
        steps.append("Run `roam safe-delete <symbol>` to safely remove a high-confidence dead symbol")
    if review_count > 0:
        steps.append("Run `roam dead --by-directory` to group dead code by directory for batch cleanup")
    steps.append("Run `roam dead --extinction <symbol>` to predict the cascade before deleting a symbol")
    return steps


def _steps_for_preflight(context: dict) -> list[str]:
    """Suggestions after `roam preflight` — branch on risk level."""
    steps: list[str] = []
    symbol = context.get("symbol", "")
    risk = (context.get("risk_level") or "").upper()
    sym_arg = f" {symbol}" if symbol else ""
    if risk in ("HIGH", "CRITICAL"):
        steps.append(f"Run `roam impact{sym_arg}` to see the full transitive blast radius before changing")
        steps.append(f"Run `roam diagnose{sym_arg}` to identify the highest-risk caller")
    if risk in ("HIGH", "CRITICAL", "MEDIUM"):
        steps.append(f"Run `roam affected-tests{sym_arg}` to know which test suite covers your change")
    if not steps:
        # LOW / OK — point the user at the natural follow-up: stage edits and re-verify.
        steps.append("Run `roam diff` after editing to re-check blast radius on the staged change")
    return steps


def _steps_for_impact(context: dict) -> list[str]:
    """Suggestions after `roam impact` — branch on affected count."""
    steps: list[str] = []
    symbol = context.get("symbol", "")
    affected = context.get("affected_symbols", 0) or context.get("symbols", 0)
    sym_arg = f" {symbol}" if symbol else ""
    if affected > 50:
        steps.append(f"Run `roam closure{sym_arg}` to see what the minimum coordinated change set looks like")
    if affected > 0:
        steps.append(f"Run `roam affected-tests{sym_arg}` to find tests that exercise the impacted surface")
    steps.append(f"Run `roam preflight{sym_arg}` for a one-shot risk verdict combining all signals")
    return steps


def _steps_for_pr_risk(context: dict) -> list[str]:
    """Suggestions after `roam pr-risk` — branch on risk level and driver."""
    steps: list[str] = []
    risk_level = (context.get("risk_level") or "").upper()
    driver = context.get("driver", "")
    if risk_level in ("HIGH", "CRITICAL"):
        steps.append("Run `roam diff --staged` to see the structural delta of staged-only changes")
        if driver in ("test_coverage_low", "test_coverage"):
            steps.append("Run `roam test-gaps --changed` to find the specific files that lack tests")
        elif driver in ("hotspot_score", "hotspot"):
            steps.append("Run `roam hotspots` to see the runtime hotspots driving the risk score")
        else:
            steps.append("Run `roam suggest-reviewers` to find the right people to loop in")
    elif risk_level == "MODERATE":
        steps.append("Run `roam critique` (pipe `git diff` in) to verify the patch against the indexed graph")
    return steps


def _steps_for_critique(context: dict) -> list[str]:
    """Suggestions after `roam critique` — branch on high-severity and bench hint."""
    steps: list[str] = []
    high = context.get("high_severity", 0)
    bench_hint = context.get("bench_hint")
    if high > 0:
        steps.append("Run `roam preflight <symbol>` on each high-severity finding before merging")
    if bench_hint:
        # The bench hint already names the right command in plain text;
        # surface it as a structured next step too so JSON consumers see it.
        steps.append(f"Run the bench/test command for this hot path: `{bench_hint}`")
    steps.append("Run `roam diff` to confirm the structural delta of what you actually changed")
    # When critique surfaced something material, the highest-signal
    # moment to suggest PR Replay: "if this PR had this much, what
    # about the last 30?" Phrased as a question so it doesn't read
    # as a sales prompt.
    if high > 0:
        steps.append("See what current detectors would have caught on your last 30 PRs: `roam pr-replay --tier sample`")
    return steps


def _steps_for_stale_refs(context: dict) -> list[str]:
    """Suggestions after `roam stale-refs` — branch on missing / fixable / anchor."""
    # synergize — turn a stale-refs scan into a launchpad
    # for the natural follow-up: auto-fix what's safe, review what's
    # not, and gate CI on branch-new findings only.
    steps: list[str] = []
    missing = context.get("missing_targets", 0)
    fixable = context.get("fixable_count", 0)
    anchor = context.get("anchor_findings", 0)
    sources_count = context.get("by_confidence", {}) if isinstance(context.get("by_confidence"), dict) else {}
    low = sources_count.get("LOW", 0)
    none = sources_count.get("NONE", 0)
    if missing == 0:
        # Clean repo — point at the CI gate so they can keep it that way.
        steps.append("Add `roam stale-refs --gate --diff` to CI to keep the repo this clean")
    else:
        if fixable > 0:
            steps.append(
                f"Run `roam stale-refs --fix preview` to inspect the {fixable} HIGH-confidence "
                "auto-rewrite(s); follow with `--fix apply` to write them"
            )
        if anchor > 0:
            steps.append(
                f"Review {anchor} anchor finding(s) — those are header-slug mismatches "
                "(no rename hint applies; fix the URL or update the header)"
            )
        if low > 0 or none > 0:
            steps.append(
                "Run `roam stale-refs --by-file` to triage the remaining LOW/NONE-confidence "
                "findings one document at a time"
            )
        if not steps:
            # Pure-MEDIUM repo — point at the gate as the path forward.
            steps.append("Run `roam stale-refs --gate --diff` in CI so new dangling refs can't be merged")
    return steps


def _steps_for_retrieve(context: dict) -> list[str]:
    """Suggestions after `roam retrieve` — branch on confidence."""
    steps: list[str] = []
    low_confidence = context.get("low_confidence", False)
    if low_confidence:
        steps.append("Refine the task with a known symbol or `--seed-file <path>` to anchor the search")
        steps.append("Run `roam search <token>` if you know a name fragment — it's exact-match instead of semantic")
    else:
        steps.append("Run `roam context <symbol>` on the top result to get caller/callee detail")
        steps.append("Run `roam preflight <symbol>` if you intend to modify the top result")
    return steps


# Dispatch table: command name -> handler that builds suggestion list.
# Adding a new command is a one-line registration here.
_HANDLERS = {
    "health": _steps_for_health,
    "context": _steps_for_context,
    "hotspots": _steps_for_hotspots,
    "diagnose": _steps_for_diagnose,
    "dead": _steps_for_dead,
    "preflight": _steps_for_preflight,
    "impact": _steps_for_impact,
    "pr-risk": _steps_for_pr_risk,
    "critique": _steps_for_critique,
    "stale-refs": _steps_for_stale_refs,
    "retrieve": _steps_for_retrieve,
}


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
    handler = _HANDLERS.get(command)
    if handler is None:
        return []
    return handler(context)[:3]


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
