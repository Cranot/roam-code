"""``roam brief`` -- one-page agent briefing (W14.5 / R20).

The "single command an agent runs first when joining a session." Brief
introspects local state via existing Python helpers (no subprocesses)
and composes a one-page summary covering five sections:

  * **next** -- what ``roam next`` would recommend right now
  * **highlights** -- 3-5 takeaways from ``roam agents-md`` (stack, top
    danger zones, top mined laws). We deliberately do NOT render the
    full AGENTS.md -- consumers who want it run ``roam agents-md``.
  * **pr_bundle** -- current PR bundle status on the active branch, or
    ``state: "no_active_bundle"`` if none.
  * **mode** -- active agent mode (``read_only`` / ``safe_edit`` /
    ``migration`` / ``autonomous_pr``) and its allow-list size.
  * **runs** -- the N most-recent runs from the ledger, plus any
    currently-in-progress run.

Design notes (CLAUDE.md / agi-in-md alignment):

  * **Verdict-first** (LAW 6). The ``summary.verdict`` line works
    standalone -- it names the mode, the next command, and any
    blockers, so an agent that reads only that line still gets the
    most important state.
  * **Imperative next_commands** (LAW 2). Every entry in
    ``agent_contract.next_commands`` is a literal ``roam <verb>``
    string that copy-pastes into a shell.
  * **No silent SAFE** (Pattern 2). If any section fails to gather
    (no constitution, no runs, no DB), the envelope marks that
    section's ``state`` explicitly (``"unavailable"`` /
    ``"no_active_bundle"`` / ``"no_runs"``) rather than reporting an
    empty success.
  * **Fast** (target <500ms). One DB connection, reused across every
    section helper that needs it. We never shell out to sibling
    commands -- everything goes through the Python API.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because brief outputs are invocation-scoped repo state snapshots
(mode, next, highlights, pr_bundle, runs) — informational summary, not
per-location violations. See action.yml _SUPPORTED_SARIF allowlist and
W1154 audit memo.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import click

from roam.capability import roam_capability
from roam.db.connection import db_exists, find_project_root, open_db
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default recent-runs window. Three is enough to spot a pattern without
# turning the one-page brief into a multi-screen scroll.
_DEFAULT_TOP_RUNS = 3

# Highlights: keep the agent-readable summary terse. Same rule of thumb
# as ``agents-md``'s default -- 3 dangers + 3 laws + a stack line is
# the sweet spot for a "30-second briefing".
_HIGHLIGHTS_TOP_DANGER = 3
_HIGHLIGHTS_TOP_LAWS = 3
_HIGHLIGHTS_TOP_STACK = 3

# Hard cap on facts emitted into ``agent_contract.facts``. Above this
# the contract starts noisy-up.
_MAX_AGENT_FACTS = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp, seconds precision, ``Z`` suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_repo_root() -> Optional[Path]:
    """Best-effort project-root resolution. Returns ``None`` on failure."""
    try:
        return find_project_root()
    except Exception:  # noqa: BLE001 -- best-effort root resolution; any failure means "unknown"
        return None


# ---------------------------------------------------------------------------
# Section: next
# ---------------------------------------------------------------------------


def _section_next(_conn: Optional[sqlite3.Connection]) -> dict[str, Any]:
    """Reuse ``cmd_next``'s collector + selector. Returns a flat dict.

    We call directly into ``roam.commands.cmd_next._collect_state`` and
    ``_select_suggestion`` rather than re-implementing them, so the two
    commands NEVER disagree on what the next step should be. If the
    import fails we degrade gracefully (state ``unavailable``).
    """
    try:
        from roam.commands.cmd_next import _collect_state, _select_suggestion
    except Exception as exc:
        return {
            "state": "unavailable",
            "verdict": "next-router unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        state = _collect_state()
        suggestion = _select_suggestion(state)
    except Exception as exc:
        return {
            "state": "unavailable",
            "verdict": "next-router crashed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "state": suggestion.get("state", "idle"),
        "verdict": suggestion.get("verdict", ""),
        "command": suggestion.get("command", ""),
        "reason": suggestion.get("reason", ""),
        "next_invocation": suggestion.get("next_invocation", ""),
    }


# ---------------------------------------------------------------------------
# Section: highlights (top of agents-md)
# ---------------------------------------------------------------------------


def _section_highlights(
    conn: Optional[sqlite3.Connection],
    repo_root: Optional[Path],
) -> dict[str, Any]:
    """Top-N from each ``agents_md`` section, no full markdown render.

    We call the individual ``_section_*`` helpers from
    :mod:`roam.agents_md.generator` so the numbers in brief match what
    ``roam agents-md`` would emit. Without a DB we still emit ``state``
    and an explanation so the envelope is never empty.
    """
    if conn is None:
        return {"state": "no_index", "stack": [], "danger_zones": [], "laws": []}
    # W15.2 followup — these helpers were promoted out of the private
    # ``_section_*`` namespace. Use the public names so a future refactor
    # can't silently rename them out from under brief.
    try:
        from roam.agents_md import (
            section_danger_zones,
            section_laws,
            section_stack,
        )
    except Exception as exc:
        return {
            "state": "unavailable",
            "stack": [],
            "danger_zones": [],
            "laws": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    stack: list[dict[str, Any]] = []
    try:
        stack = section_stack(conn) or []
    except Exception:  # noqa: BLE001 -- highlights are best-effort; a failing section degrades to empty
        stack = []
    stack = stack[:_HIGHLIGHTS_TOP_STACK]

    danger: list[dict[str, Any]] = []
    try:
        danger = section_danger_zones(conn, limit=_HIGHLIGHTS_TOP_DANGER) or []
    except Exception:  # noqa: BLE001 -- highlights are best-effort; a failing section degrades to empty
        danger = []

    laws: list[dict[str, Any]] = []
    try:
        laws = section_laws(conn, top_n=_HIGHLIGHTS_TOP_LAWS) or []
    except Exception:  # noqa: BLE001 -- highlights are best-effort; a failing section degrades to empty
        laws = []

    state = "ok"
    if not (stack or danger or laws):
        state = "empty"

    return {
        "state": state,
        "stack": stack,
        "danger_zones": danger,
        "laws": laws,
    }


# ---------------------------------------------------------------------------
# Section: pr_bundle
# ---------------------------------------------------------------------------


def _section_pr_bundle(repo_root: Optional[Path]) -> dict[str, Any]:
    """Status of the active branch's PR bundle, or ``no_active_bundle``.

    We resolve the bundle path via the same helper ``cmd_pr_bundle``
    uses (``_bundle_path`` is module-private, but reading the file is
    not load-bearing on its internals -- we just JSON-parse the file).
    """
    if repo_root is None:
        return {"state": "no_active_bundle", "reason": "project root unresolvable"}
    try:
        from roam.commands.cmd_pr_bundle import _bundle_path

        path = _bundle_path(repo_root)
    except Exception:  # noqa: BLE001 -- import or path resolution may fail; fall back to detached filename
        # Fall back to the detached-bundle filename.
        path = repo_root / ".roam" / "pr-bundle.json"

    if not path.is_file():
        return {"state": "no_active_bundle", "path": str(path)}

    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "state": "unreadable",
            "path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(data, dict):
        return {"state": "unreadable", "path": str(path), "error": "not a JSON object"}

    intent = data.get("intent") or ""
    affected = data.get("affected_symbols") or []
    risks = data.get("risks") or []
    tests_required = data.get("tests_required") or []
    tests_run = data.get("tests_run") or []
    git = data.get("git") or {}
    branch = git.get("branch") if isinstance(git, dict) else None

    return {
        "state": "active",
        "path": str(path),
        "branch": branch,
        "intent": intent,
        "intent_set": bool(intent),
        "affected_symbol_count": len(affected) if isinstance(affected, list) else 0,
        "risk_count": len(risks) if isinstance(risks, list) else 0,
        "tests_required": len(tests_required) if isinstance(tests_required, list) else 0,
        "tests_run": len(tests_run) if isinstance(tests_run, list) else 0,
        "updated_at": data.get("updated_at") or data.get("created_at") or "",
    }


# ---------------------------------------------------------------------------
# Section: mode
# ---------------------------------------------------------------------------


def _section_mode(repo_root: Optional[Path]) -> dict[str, Any]:
    """Active mode + allow-list count + resolution source."""
    if repo_root is None:
        return {"state": "unavailable", "active": "safe_edit", "allowed_count": 0}
    try:
        from roam.modes.policy import VALID_MODES, resolve_mode

        policy = resolve_mode(repo_root)
        total_modes = len(VALID_MODES)
    except Exception as exc:
        return {
            "state": "unavailable",
            "active": "safe_edit",
            "allowed_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "state": "ok",
        "active": policy.name,
        "allowed_count": len(policy.allowed_commands),
        "source": policy.source,
        "total_modes": total_modes,
    }


# ---------------------------------------------------------------------------
# Section: runs
# ---------------------------------------------------------------------------


def _section_runs(repo_root: Optional[Path], top_n: int) -> dict[str, Any]:
    """N most-recent closed runs + any in-progress run.

    Skips entirely if ``.roam/runs/`` doesn't exist -- this keeps the
    cost of ``brief`` near-zero on a brand-new repo.
    """
    if repo_root is None:
        return {"state": "unavailable", "recent": [], "in_progress": []}

    runs_dir = repo_root / ".roam" / "runs"
    if not runs_dir.is_dir():
        return {"state": "no_runs", "recent": [], "in_progress": []}

    try:
        from roam.runs.ledger import list_runs
    except Exception as exc:
        return {
            "state": "unavailable",
            "recent": [],
            "in_progress": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    recent: list[dict[str, Any]] = []
    in_progress: list[dict[str, Any]] = []
    try:
        all_runs = list(list_runs(repo_root))
    except Exception:  # noqa: BLE001 -- run ledger is optional; absent/corrupt ledger degrades to no runs
        all_runs = []

    for meta in all_runs:
        entry = {
            "run_id": meta.run_id,
            "agent": meta.agent,
            "status": meta.status,
            "started_at": meta.started_at,
            "ended_at": meta.ended_at or "",
        }
        if meta.status == "in_progress":
            in_progress.append(entry)
        else:
            if len(recent) < max(top_n, 0):
                recent.append(entry)

    state = "ok"
    if not recent and not in_progress:
        state = "no_runs"

    return {
        "state": state,
        "recent": recent,
        "in_progress": in_progress,
        "top_n": top_n,
    }


# ---------------------------------------------------------------------------
# Verdict + agent_contract composition
# ---------------------------------------------------------------------------


def _short_path(p: str, repo_root: Optional[Path]) -> str:
    """Render a path relative to repo root when possible."""
    if not p:
        return p
    if repo_root is None:
        return p
    try:
        rel = Path(p).relative_to(repo_root)
        return str(rel).replace("\\", "/")
    except ValueError:
        return p


def _compose_verdict(
    mode_section: dict[str, Any],
    next_section: dict[str, Any],
    runs_section: dict[str, Any],
    pr_bundle_section: dict[str, Any],
) -> str:
    """Single-line verdict (LAW 6 -- works standalone).

    Names the mode, the next command, the run count, and bundle status.
    """
    mode_name = mode_section.get("active") or "safe_edit"
    next_cmd = next_section.get("next_invocation") or "roam tour"
    in_progress_count = len(runs_section.get("in_progress") or [])
    recent_count = len(runs_section.get("recent") or [])
    bundle_state = pr_bundle_section.get("state") or "no_active_bundle"

    runs_phrase: str
    if in_progress_count:
        runs_phrase = f"{in_progress_count} active run(s)"
    elif recent_count:
        runs_phrase = f"{recent_count} recent run(s)"
    else:
        runs_phrase = "no runs"

    if bundle_state == "active":
        bundle_phrase = "pr-bundle: active"
    else:
        bundle_phrase = "no pr-bundle"

    return f"Briefed: mode={mode_name}, next=`{next_cmd}`, {runs_phrase}, {bundle_phrase}."


def _compose_facts(
    mode_section: dict[str, Any],
    next_section: dict[str, Any],
    runs_section: dict[str, Any],
    pr_bundle_section: dict[str, Any],
    highlights_section: dict[str, Any],
) -> list[str]:
    """Flat, imperative-anchored facts (LAW 4 + LAW 10).

    Ordered so the most decision-relevant fact comes first (LAW 3).
    """
    facts: list[str] = []

    # Mode -- always first; defines the action surface.
    mode_name = mode_section.get("active") or "safe_edit"
    allowed = mode_section.get("allowed_count") or 0
    facts.append(f"active mode: {mode_name} ({allowed} commands allowed)")

    # Next -- the verdict line, abbreviated to the imperative form.
    next_inv = next_section.get("next_invocation") or ""
    next_reason = next_section.get("reason") or "idle"
    if next_inv:
        facts.append(f"next recommended: `{next_inv}` (reason: {next_reason})")

    # PR bundle.
    bundle_state = pr_bundle_section.get("state")
    if bundle_state == "active":
        intent = pr_bundle_section.get("intent") or "(no intent set)"
        affected = pr_bundle_section.get("affected_symbol_count", 0)
        facts.append(
            f"pr-bundle active on branch `{pr_bundle_section.get('branch') or '?'}`: "
            f'intent="{intent}", {affected} affected symbol(s)'
        )
    elif bundle_state == "no_active_bundle":
        facts.append("no active pr-bundle on this branch")

    # Runs.
    in_progress = runs_section.get("in_progress") or []
    recent = runs_section.get("recent") or []
    if in_progress:
        first = in_progress[0]
        facts.append(f"in-progress run: {first.get('run_id')} (agent={first.get('agent')})")
    if recent:
        agents = sorted({r.get("agent", "") for r in recent if r.get("agent")})
        if agents:
            agents_phrase = ", ".join(agents[:3])
        else:
            agents_phrase = "unknown agent"
        facts.append(f"recent activity: {len(recent)} closed run(s) by {agents_phrase}")

    # Highlights -- one each so the agent has a concrete-noun anchor.
    danger = highlights_section.get("danger_zones") or []
    if danger:
        top = danger[0]
        facts.append(f"top danger zone: `{top.get('path')}` (score {top.get('danger_score')})")
    laws = highlights_section.get("laws") or []
    if laws:
        top = laws[0]
        desc = top.get("description") or top.get("id") or "law"
        facts.append(f"top mined law: {desc}")

    return facts[:_MAX_AGENT_FACTS]


def _compose_next_commands(
    next_section: dict[str, Any],
    pr_bundle_section: dict[str, Any],
) -> list[str]:
    """Imperative, copy-paste-executable commands (LAW 2 + CONSTRAINT 12)."""
    out: list[str] = []
    next_inv = next_section.get("next_invocation") or ""
    if next_inv:
        out.append(next_inv)
    # Always offer agents-md as the "what is this repo" follow-up.
    out.append("roam agents-md")
    # Constitution show is the natural follow-up for an unfamiliar agent.
    out.append("roam constitution show")
    # PR-bundle init when there's no active bundle.
    if pr_bundle_section.get("state") == "no_active_bundle":
        out.append('roam pr-bundle init --intent "..."')
    # Dedup but preserve order.
    seen: set[str] = set()
    unique: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def _render_text(
    repo_root: Optional[Path],
    mode_section: dict[str, Any],
    next_section: dict[str, Any],
    highlights_section: dict[str, Any],
    pr_bundle_section: dict[str, Any],
    runs_section: dict[str, Any],
    verdict: str,
    next_commands: list[str],
) -> str:
    """Render the brief as plain ASCII text -- < 60 lines, single page."""
    lines: list[str] = []

    # Header
    ts = _utc_now_iso()
    root_display = str(repo_root) if repo_root else "(no repo root)"
    mode_name = mode_section.get("active") or "safe_edit"
    allowed = mode_section.get("allowed_count") or 0
    total_modes = mode_section.get("total_modes") or 4
    lines.append(f"ROAM BRIEF -- {ts}")
    lines.append(f"Repo: {root_display}  |  Mode: {mode_name} ({allowed} cmds)  |  Modes available: {total_modes}")
    lines.append("")
    lines.append(f"VERDICT: {verdict}")
    lines.append("")

    # Next section
    lines.append(f"NEXT ({next_section.get('state', '?')}):")
    nv = next_section.get("verdict") or next_section.get("next_invocation") or ""
    if nv:
        lines.append(f"  -> {nv}")
    else:
        lines.append("  (no recommendation)")
    lines.append("")

    # Highlights section
    lines.append("HIGHLIGHTS:")
    stack = highlights_section.get("stack") or []
    if stack:
        parts = [f"{e.get('language')} ({e.get('files')})" for e in stack]
        lines.append(f"  Stack:        {', '.join(parts)}")
    else:
        lines.append("  Stack:        (no indexed languages)")
    danger = highlights_section.get("danger_zones") or []
    if danger:
        top = danger[0]
        path = _short_path(top.get("path", ""), repo_root)
        lines.append(
            f"  Top danger:   {path} "
            f"(score {top.get('danger_score')}, churn {top.get('churn')}, "
            f"complexity {top.get('complexity')})"
        )
    else:
        lines.append("  Top danger:   (none surfaced)")
    laws = highlights_section.get("laws") or []
    if laws:
        top = laws[0]
        desc = top.get("description") or top.get("id") or "(law)"
        conformance = top.get("conformance_pct")
        sample = top.get("sample_size")
        suffix = ""
        if conformance is not None and sample is not None:
            suffix = f" ({conformance}% conformance, {sample} samples)"
        lines.append(f"  Top law:      {desc}{suffix}")
    else:
        lines.append("  Top law:      (none mined)")
    lines.append("")

    # PR bundle section
    lines.append("ACTIVE PR BUNDLE:")
    if pr_bundle_section.get("state") == "active":
        intent = pr_bundle_section.get("intent") or "(no intent set)"
        branch = pr_bundle_section.get("branch") or "(unknown branch)"
        affected = pr_bundle_section.get("affected_symbol_count", 0)
        risks = pr_bundle_section.get("risk_count", 0)
        tests_req = pr_bundle_section.get("tests_required", 0)
        tests_run = pr_bundle_section.get("tests_run", 0)
        lines.append(f"  Branch: {branch}")
        lines.append(f"  Intent: {intent}")
        lines.append(f"  Affected: {affected} sym, risks: {risks}, tests: {tests_run}/{tests_req}")
    else:
        lines.append('  No active bundle. Initialize: roam pr-bundle init --intent "..."')
    lines.append("")

    # Runs section
    in_progress = runs_section.get("in_progress") or []
    recent = runs_section.get("recent") or []
    total_shown = len(in_progress) + len(recent)
    lines.append(f"RECENT RUNS ({total_shown}):")
    if not in_progress and not recent:
        lines.append("  (no runs logged yet)")
    for r in in_progress:
        lines.append(f"  {r.get('run_id'):<28} {r.get('agent', ''):<14} in_progress")
    for r in recent:
        lines.append(f"  {r.get('run_id'):<28} {r.get('agent', ''):<14} {r.get('status', '')}")
    lines.append("")

    # Next commands
    lines.append("NEXT COMMANDS:")
    for c in next_commands:
        lines.append(f"  {c}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="brief",
    category="exploration",
    summary="One-page agent briefing: mode + next + highlights + pr-bundle + runs.",
    inputs=[],
    outputs=["verdict", "sections"],
    examples=["roam brief", "roam --json brief"],
    tags=["router", "agent-os", "briefing"],
    ai_safe=True,
    requires_index=True,
    maturity="experimental",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    displaces=("orientation_tax",),
)
@click.command(name="brief")
@click.option(
    "--no-next",
    is_flag=True,
    default=False,
    help="Skip the next-command recommendation block.",
)
@click.option(
    "--no-pr-bundle",
    is_flag=True,
    default=False,
    help="Skip the PR-bundle status block (useful when not in PR mode).",
)
@click.option(
    "--no-highlights",
    is_flag=True,
    default=False,
    help="Skip the stack/danger/laws highlights block.",
)
@click.option(
    "--no-runs",
    is_flag=True,
    default=False,
    help="Skip the recent-runs block.",
)
@click.option(
    "--no-mode",
    is_flag=True,
    default=False,
    help="Skip the active-mode block.",
)
@click.option(
    "--top-runs",
    type=int,
    default=_DEFAULT_TOP_RUNS,
    show_default=True,
    help="How many recent closed runs to include.",
)
@click.pass_context
def brief_cmd(
    ctx,
    no_next: bool,
    no_pr_bundle: bool,
    no_highlights: bool,
    no_runs: bool,
    no_mode: bool,
    top_runs: int,
):
    """One-page agent briefing covering mode / next / highlights / pr-bundle / runs.

    \b
    Designed as the FIRST command an agent runs when joining a roam-indexed
    repo. Captures the most decision-relevant state without forcing the
    agent to run five separate commands.

    \b
    Examples:
      roam brief
      roam --json brief
      roam brief --top-runs 5
      roam brief --no-pr-bundle --no-runs
    """
    json_mode = bool(ctx.obj.get("json")) if ctx.obj else False

    repo_root = _safe_repo_root()

    # Open a single readonly DB connection if available, reuse it for
    # every section helper. If the index is missing we still emit a
    # useful envelope -- highlights will be empty but mode/next/runs
    # work without a DB.
    conn: Optional[sqlite3.Connection] = None
    index_present = False
    try:
        if repo_root is not None and db_exists(repo_root):
            index_present = True
    except Exception:  # noqa: BLE001 -- brief works without a DB; any probe failure means "no index"
        index_present = False

    sections_consulted: list[str] = []
    section_failures: list[str] = []

    try:
        if index_present:
            cm = open_db(readonly=True, project_root=repo_root)
            conn = cm.__enter__()  # type: ignore[attr-defined]
        else:
            cm = None  # type: ignore[assignment]

        # ---- Section: next
        if not no_next:
            try:
                next_section = _section_next(conn)
                sections_consulted.append("next")
            except Exception as exc:
                next_section = {
                    "state": "unavailable",
                    "verdict": "next-router crashed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                section_failures.append("next")
        else:
            next_section = {"state": "skipped", "verdict": ""}

        # ---- Section: highlights
        if not no_highlights:
            try:
                highlights_section = _section_highlights(conn, repo_root)
                sections_consulted.append("highlights")
            except Exception as exc:
                highlights_section = {
                    "state": "unavailable",
                    "stack": [],
                    "danger_zones": [],
                    "laws": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                section_failures.append("highlights")
        else:
            highlights_section = {
                "state": "skipped",
                "stack": [],
                "danger_zones": [],
                "laws": [],
            }

        # ---- Section: pr_bundle
        if not no_pr_bundle:
            try:
                pr_bundle_section = _section_pr_bundle(repo_root)
                sections_consulted.append("pr_bundle")
            except Exception as exc:
                pr_bundle_section = {
                    "state": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                section_failures.append("pr_bundle")
        else:
            pr_bundle_section = {"state": "skipped"}

        # ---- Section: mode
        if not no_mode:
            try:
                mode_section = _section_mode(repo_root)
                sections_consulted.append("mode")
            except Exception as exc:
                mode_section = {
                    "state": "unavailable",
                    "active": "safe_edit",
                    "allowed_count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                section_failures.append("mode")
        else:
            mode_section = {"state": "skipped", "active": "safe_edit", "allowed_count": 0}

        # ---- Section: runs
        if not no_runs:
            try:
                runs_section = _section_runs(repo_root, top_n=max(top_runs, 0))
                sections_consulted.append("runs")
            except Exception as exc:
                runs_section = {
                    "state": "unavailable",
                    "recent": [],
                    "in_progress": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                section_failures.append("runs")
        else:
            runs_section = {"state": "skipped", "recent": [], "in_progress": []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — see cleanup-guard rationale below
                # Genuine guard: a close() failure during cleanup has no
                # recoverable action and no user-visible consequence.
                pass

    # ---------------------------------------------------------------------
    # Verdict, facts, next_commands
    # ---------------------------------------------------------------------

    verdict = _compose_verdict(mode_section, next_section, runs_section, pr_bundle_section)
    facts = _compose_facts(
        mode_section,
        next_section,
        runs_section,
        pr_bundle_section,
        highlights_section,
    )
    next_commands = _compose_next_commands(next_section, pr_bundle_section)

    partial_success = bool(section_failures) or not index_present

    summary = {
        "verdict": verdict,
        "state": "partial" if partial_success else "ok",
        "partial_success": partial_success,
        "sections": sections_consulted,
        "section_failures": section_failures,
        "index_present": index_present,
    }

    envelope = json_envelope(
        "brief",
        summary=summary,
        next=next_section,
        highlights=highlights_section,
        mode=mode_section,
        runs=runs_section,
        pr_bundle=pr_bundle_section,
        agent_contract={
            "facts": facts,
            "next_commands": next_commands,
        },
    )

    # Opportunistic auto-log (R20). ``auto_log`` is documented + verified to
    # never raise (every internal failure path is caught and converted to
    # ``return None``); no defensive wrapper is needed here.
    auto_log(envelope, action="brief", target=str(repo_root) if repo_root else "")

    if json_mode:
        click.echo(to_json(envelope))
        return

    text = _render_text(
        repo_root,
        mode_section,
        next_section,
        highlights_section,
        pr_bundle_section,
        runs_section,
        verdict,
        next_commands,
    )
    click.echo(text)
