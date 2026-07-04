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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.runs.ledger import latest_in_progress_run, log_event

# W294 - closed-allowlist of authority-shaped event fields that callers
# may stamp onto a run-ledger event via :func:`auto_log` 's
# ``extra_event_fields`` kwarg. The set mirrors
# :data:`roam.evidence.collector._RUN_LEDGER_AUTHORITY_FIELDS` exactly:
# these are the fields the W292 corroboration harvester reads when
# building the ``(authority_kind, authority_id)`` corroboration set, so
# only these field names can promote an ``AuthorityRef`` to
# ``provenance="run_ledger"``.
#
# Defense-in-depth: a closed whitelist prevents a future caller from
# smuggling arbitrary state into the ledger via this kwarg. Fields not
# in the whitelist are silently dropped (auto_log is best-effort and
# must never raise from a gate-command path); extending the whitelist
# is a deliberate source-code edit, not a runtime hack. Keep this set
# in sync with ``_RUN_LEDGER_AUTHORITY_FIELDS`` in the collector.
_AUTHORITY_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "mode",
        "active_mode",
        "mode_to",
        "mode_from",
        "permit_id",
        "lease_id",
        "approval_id",
        "rule_id",
    }
)

_AUTO_LOG_EXPECTED_FAILURES: tuple[type[Exception], ...] = (OSError, TypeError, ValueError)


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
    except _AUTO_LOG_EXPECTED_FAILURES as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # known disk-scan / metadata-shape FAILURE produces the same
        # ``None`` as a legitimate "no active run"; surface the lineage
        # so a broken ledger directory isn't silently read as
        # "auto-logging is off". Unexpected defects propagate instead of
        # being conserved as ledger availability.
        log_swallowed("runs.helpers:get_active_run_id:latest_in_progress", exc)
        return None
    return meta.run_id if meta else None


def _filter_authority_fields(
    extra_event_fields: Optional[Mapping[str, Any]],
) -> dict[str, str]:
    """Enforce the W294 closed whitelist on caller-supplied event fields.

    Non-string values and unknown keys are silently dropped: log_event's
    signature accepts **event_fields so any string survives, but we
    constrain the surface to what the W292 harvester reads.
    """
    safe: dict[str, str] = {}
    if not isinstance(extra_event_fields, Mapping):
        return safe
    for k, v in extra_event_fields.items():
        if k in _AUTHORITY_EVENT_FIELDS and isinstance(v, str) and v:
            safe[k] = v
    return safe


def _resolve_repo_root_for_auto_log() -> Optional[Path]:
    """Keep auto-log root lookup best-effort without hiding import/programmer bugs."""
    try:
        return find_project_root()
    except OSError as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" —
        # auto_log is opportunistic, but a find_project_root() filesystem
        # failure silently drops the event from the run ledger. Surface the
        # lineage so a missing timeline entry has a discoverable cause.
        log_swallowed("runs.helpers:auto_log:find_project_root", exc)
        return None


def _as_dict(value: Any) -> dict:
    """Coerce an untrusted envelope sub-object to a dict (``{}`` if not)."""
    return value if isinstance(value, dict) else {}


def auto_log(
    envelope: dict,
    action: str,
    target: str = "",
    repo_root: Optional[Path] = None,
    extra_event_fields: Optional[Mapping[str, Any]] = None,
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

    W294 extension - ``extra_event_fields`` lets writer-side call sites
    stamp authority-shaped corroboration fields onto the emitted event
    so the W292 collector harvester
    (:data:`roam.evidence.collector._RUN_LEDGER_AUTHORITY_FIELDS`) can
    promote matching ``AuthorityRef`` rows to ``provenance="run_ledger"``.
    Only keys in :data:`_AUTHORITY_EVENT_FIELDS` survive the safety
    filter; everything else is silently dropped (this kwarg is NOT an
    arbitrary-state escape hatch). The whitelist matches the harvester's
    closed read-list exactly.
    """
    if not isinstance(envelope, dict):
        envelope = {}
    if repo_root is None:
        repo_root = _resolve_repo_root_for_auto_log()
        if repo_root is None:
            return None
    run_id = get_active_run_id(repo_root)
    if not run_id:
        return None
    try:
        summary = _as_dict(envelope.get("summary"))
        agent_contract = _as_dict(envelope.get("agent_contract"))
        extra_fields_safe = _filter_authority_fields(extra_event_fields)

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
            **extra_fields_safe,
        )
    except _AUTO_LOG_EXPECTED_FAILURES as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" —
        # auto-logging is OPPORTUNISTIC and must never crash the gate
        # command that called us for known operational failures (filesystem,
        # shape). Unexpected programming errors propagate instead of being
        # conserved as silent ledger gaps. Surface the lineage so a broken
        # auto-log path is discoverable.
        log_swallowed("runs.helpers:auto_log:log_event", exc)
        return None
