"""Helpers for auto-logging gate-command envelopes into the active run.

R20 Phase 2 — wires high-signal commands to opportunistically append an
event into whatever run is currently active. Started with the seven
classic gate commands (``preflight``, ``diff``, ``critique``, ``impact``,
``pr-prep``, ``pr-analyze``, ``attest``, ``verify``); has since grown to
cover constitution + strategic commands as those graduated to first-class
run-ledger citizens.

The "active run" is resolved with this precedence:

  1. ``ROAM_RUN_ID`` env var (explicit, set by the agent / harness).
  2. The newest in-progress run on disk (``status: "in_progress"``).

If neither is set the helper is a silent no-op: this is by design — gate
commands run constantly in CI / agent contexts where nobody opened a run,
and we never want auto-logging to pollute their output or fail their
exit code. Every error path returns ``None``; nothing is allowed to
escape from this module up into the parent command.

------------------------------------------------------------
Auto-log allowlist policy (W11 polish + W7.4 follow-up)
------------------------------------------------------------

Auto-logging is **opt-in per command** — every call site explicitly
imports :func:`auto_log` and calls it after the envelope is built. There
is no central allowlist data structure; the allowlist *is* the set of
``cmd_*.py`` modules that import this helper. To audit:

    grep -l "from roam.runs.helpers import auto_log" src/roam/commands/

Commands that DO auto-log (intentional — define agent action boundaries
or are high-signal-per-call):

  * **Gate commands** (the original R20 Phase 2 cohort, plus ``impact``
    promoted in W15.2):
    ``preflight``, ``diff``, ``critique``, ``impact``, ``pr-prep``,
    ``pr-analyze``, ``attest``, ``verify``. These bracket agent edits —
    every one is a decision point worth replaying. ``impact`` joined the
    set because it is the documented "what breaks" probe that an agent
    runs before committing to a refactor; missing it from the timeline
    breaks the ``replay`` reconstruction of agent decisions.

  * **Constitution gates** (capstone events):
    ``constitution-init``, ``constitution-check``, ``constitution-apply``.
    Initialisation + each enforcement pass; rare enough that every event
    is interesting.

  * **Strategic commands** (high-signal-per-call):
    ``laws-mine``, ``laws-check``, ``replay``, ``agent-score``,
    ``graph-diff``, ``architecture-drift``, ``side-effects``,
    ``idempotency``, ``pr-bundle`` and its emit/attach kin. These either
    summarise large amounts of state (``replay``, ``agent-score``,
    ``architecture-drift``) or define semantic-classification commitments
    (``side-effects``, ``idempotency``) worth recording.

Commands that DO NOT auto-log (intentional — kept out of the ledger so
the timeline stays scannable). Naming the exclusion set explicitly here
per CLAUDE.md LAW 7 (positive vocabulary, not silent absence):

  * **High-frequency comprehension commands** — the agent re-checks
    these constantly, so logging every call would drown the signal:
    ``health``, ``status``, ``where``, ``info``, ``version``, ``help``,
    ``help-all``, ``surface``, ``doctor``, ``db-check``, ``mcp-status``.

  * **Read-only browsing** — pure-information lookups with no decision
    semantic: ``search``, ``grep``, ``retrieve``, ``context``,
    ``symbol``, ``hover``, ``file``, ``api``, ``deps``,
    ``trace``, ``uses``, ``minimap``, ``tour``, ``understand``,
    ``describe``, ``dashboard``, ``stats``, ``index-stats``.
    (W15.2: ``impact`` moved OUT of this list — it carries the same
    decision semantic as ``preflight``/``diff`` and now auto-logs.)

  * **Indexing / maintenance** — runs the indexer; orthogonal to agent
    decision flow: ``init``, ``index``, ``reset``, ``clean``,
    ``recipes``, ``annotate``, ``config``, ``plugins``.

  * **Per-call analytics** — high-volume, low-decision-value:
    ``complexity``, ``clones``, ``duplicates``, ``smells``, ``metrics``,
    ``forecast``, ``hotspots``, ``orphan-imports``, ``stale-refs``,
    ``bus-factor``, ``churn``.

If you are adding a new command that legitimately defines an agent
action boundary (anything that "commits" the agent to a path or
publishes a verdict that downstream tooling consumes), wire it through
this helper. If you are unsure, do NOT auto-log — keeping the ledger
scannable is the higher-priority goal.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from roam.runs.ledger import latest_in_progress_run, log_event


def get_active_run_id(repo_root: Path) -> Optional[str]:
    """Resolve the currently-active run id, or ``None``.

    Order:
      - ``ROAM_RUN_ID`` env var (stripped); empty / whitespace counts as
        unset so an explicitly-cleared env doesn't shadow a real
        in-progress run.
      - Newest ``status == "in_progress"`` run on disk.
    """
    env_id = os.environ.get("ROAM_RUN_ID", "").strip()
    if env_id:
        return env_id
    try:
        meta = latest_in_progress_run(repo_root)
    except Exception:
        return None
    return meta.run_id if meta else None


def auto_log(
    envelope: dict,
    action: str,
    target: str = "",
    repo_root: Optional[Path] = None,
) -> Optional[int]:
    """Append an event to the active run, derived from a roam envelope.

    Returns the new event ``seq`` on success, or ``None`` if there is no
    active run / anything goes wrong. **Never raises** — gate commands
    that call us must not be derailed by a missing ledger directory, a
    permission error, or a malformed envelope.

    The event written has roughly the shape::

        {
          "ts": "...",
          "seq": N,
          "action": "preflight",
          "target": "useThemeClasses",
          "envelope_command": "preflight",
          "summary_verdict": "Safe to proceed — LOW risk for ...",
          "partial_success": false,
          "signals": {"facts": [...], "next_commands": [...]}
        }

    Consumers (``roam runs show``, future ``roam replay``) can render
    a run timeline straight from this stream.
    """
    if not isinstance(envelope, dict):
        envelope = {}
    if repo_root is None:
        try:
            from roam.db.connection import find_project_root

            repo_root = find_project_root()
        except Exception:
            return None
    try:
        run_id = get_active_run_id(repo_root)
    except Exception:
        return None
    if not run_id:
        return None
    try:
        summary = envelope.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        agent_contract = envelope.get("agent_contract") or {}
        if not isinstance(agent_contract, dict):
            agent_contract = {}
        return log_event(
            repo_root,
            run_id,
            action=action,
            target=target or "",
            envelope_command=envelope.get("command", "") or "",
            summary_verdict=summary.get("verdict", "") or "",
            partial_success=bool(summary.get("partial_success", False)),
            signals={
                "facts": agent_contract.get("facts", []) or [],
                "next_commands": agent_contract.get("next_commands", []) or [],
            },
        )
    except Exception:
        # Auto-logging is OPPORTUNISTIC. We must never crash the gate
        # command that called us.
        return None
