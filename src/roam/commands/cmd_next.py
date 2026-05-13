"""Suggest the next roam command based on current repo state.

`roam next` is an agent-router (R15 / Agent-OS positioning). It consults
cheap, no-compute signals — index presence, index staleness, working-tree
dirtiness, recent session memory, and the most recent saved response
envelope — and emits a single imperative recommendation that an agent can
execute directly.

Design notes:
  * No heavy compute. Target latency <200ms. We use ``git status
    --porcelain`` (metadata only) and never call ``git log``.
  * Every signal is wrapped in defensive try/except — a missing
    ``.roam/`` directory, an unreadable ``memory.jsonl``, or a missing
    git binary must NOT crash the router.
  * The verdict is a standalone single line (LAW 6). The
    ``summary.command`` field carries the canonical machine-readable
    name for the suggested next command (just the verb, no flags).
  * Branches are listed in priority order in ``_select_suggestion``;
    the first matching branch wins.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import db_exists, find_project_root, get_db_path
from roam.output.formatter import json_envelope, to_json


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class RepoState:
    """Snapshot of cheap repo-state signals consulted by the router.

    Every field defaults to a safe "unknown" value so partial collection
    failures (no git, no ``.roam/`` directory, unreadable memory) cannot
    cascade into a crash.
    """

    has_index: bool = False
    index_stale: bool = False
    index_stale_reason: str | None = None
    has_uncommitted_changes: bool = False
    uncommitted_count: int = 0
    recent_envelope_next_command: str | None = None
    recent_envelope_source: str | None = None
    recent_memory_commands: list[str] = field(default_factory=list)
    project_root: str | None = None
    # R24 constitution signals -- populated only when ``.roam/constitution.yml``
    # exists AND a run is active. Cheap probe (no subprocess calls).
    has_constitution: bool = False
    pending_before_pr_check: str | None = None
    pending_before_pr_invocation: str | None = None
    active_run_id: str | None = None
    # R16 / W14.2 mode-upgrade probe — surface a recent intent-check BLOCKED
    # event whose upgrade target would unblock the agent. Populated only when
    # an active run exists AND its most recent intent-check event was BLOCKED
    # AND it named an upgrade mode. Otherwise all three are ``None``.
    mode_upgrade_target: str | None = None
    mode_upgrade_blocked_command: str | None = None
    mode_upgrade_invocation: str | None = None


# ---------------------------------------------------------------------------
# Signal collectors — each one is best-effort and never raises
# ---------------------------------------------------------------------------


def _git_porcelain_count(root: Path) -> int:
    """Return count of dirty files via ``git status --porcelain``. 0 on error."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0
    if result.returncode != 0:
        return 0
    return len([ln for ln in result.stdout.splitlines() if ln.strip()])


def _newest_source_mtime(root: Path, limit: int = 500) -> float:
    """Cheap mtime walk of the working tree.

    Returns the newest mtime seen across non-``.roam`` / non-``.git``
    files, or 0.0 if nothing is found. Capped at *limit* files so we
    don't walk huge repos — the router only needs *any* file newer than
    the index to flag staleness.
    """
    newest = 0.0
    count = 0
    skip_dirs = {".git", ".roam", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # In-place prune so we don't recurse into skip_dirs.
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fn in filenames:
                count += 1
                if count > limit:
                    return newest
                try:
                    p = Path(dirpath) / fn
                    m = p.stat().st_mtime
                    if m > newest:
                        newest = m
                except (OSError, FileNotFoundError):
                    continue
    except OSError:
        return newest
    return newest


def _check_index_staleness(root: Path) -> tuple[bool, str | None]:
    """Return (is_stale, reason). True if any source file is newer than the DB.

    Best-effort — returns ``(False, None)`` on any error so the router
    falls through to the next branch instead of falsely flagging stale.
    """
    try:
        db_path = get_db_path(root)
    except Exception:
        return (False, None)
    try:
        db_mtime = db_path.stat().st_mtime
    except OSError:
        return (False, None)
    newest = _newest_source_mtime(root)
    if newest == 0.0:
        return (False, None)
    if newest > db_mtime:
        return (True, f"source files modified after index mtime ({int(newest - db_mtime)}s newer)")
    return (False, None)


def _read_recent_memory_commands(root: Path, limit: int = 5) -> list[str]:
    """Read up to *limit* most-recent ``subject`` values from ``.roam/memory.jsonl``.

    Used to detect whether the agent already ran a given command recently
    (so we don't recommend the same one twice). Tolerates a missing file,
    a corrupted file, or a memory package that fails to import.
    """
    path = root / ".roam" / "memory.jsonl"
    if not path.exists():
        return []
    subjects: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    # Walk from the end — newest entries last in append-only JSONL.
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        subject = raw.get("subject")
        if isinstance(subject, str) and subject:
            subjects.append(subject)
        if len(subjects) >= limit:
            break
    return subjects


def _read_recent_envelope_next_command(root: Path) -> tuple[str | None, str | None]:
    """Look at the newest envelope in ``.roam/responses/`` for a next-command hint.

    Returns ``(command_string, source_file_name)`` or ``(None, None)`` if
    nothing useful is found. We only consult the single newest file —
    older envelopes are likely stale.
    """
    responses_dir = root / ".roam" / "responses"
    if not responses_dir.is_dir():
        return (None, None)
    try:
        entries = [p for p in responses_dir.iterdir() if p.suffix == ".json" and p.is_file()]
    except OSError:
        return (None, None)
    if not entries:
        return (None, None)
    try:
        newest = max(entries, key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
        return (None, None)
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)

    # Try agent_contract.next_commands first (canonical location).
    contract = data.get("agent_contract")
    if isinstance(contract, dict):
        nc = contract.get("next_commands")
        if isinstance(nc, list) and nc:
            first = nc[0]
            if isinstance(first, str) and first:
                return (first, newest.name)
            if isinstance(first, dict):
                cmd = first.get("command") or first.get("cmd") or first.get("action")
                if isinstance(cmd, str) and cmd:
                    return (cmd, newest.name)

    # Fallback: summary.next_commands (some commands emit this directly).
    summary = data.get("summary")
    if isinstance(summary, dict):
        nc = summary.get("next_commands")
        if isinstance(nc, list) and nc:
            first = nc[0]
            if isinstance(first, str) and first:
                return (first, newest.name)

    return (None, None)


def _bare_command_name(verdict_cmd: str) -> str:
    """Extract a canonical command name (just the verb) from a raw string.

    Accepts forms like ``"roam preflight <sym>"``, ``"preflight"``, or
    ``"roam --json preflight"`` and returns the leading subcommand verb.
    """
    s = verdict_cmd.strip()
    # Strip a leading 'roam '
    if s.startswith("roam "):
        s = s[5:].lstrip()
    # Drop any leading flags like '--json'
    tokens = [t for t in s.split() if t and not t.startswith("-")]
    return tokens[0] if tokens else s


def _read_recent_mode_block(root: Path) -> tuple[str | None, str | None, str | None]:
    """Find the most-recent ``intent-check`` BLOCKED event with an upgrade hint.

    Returns ``(blocked_command, upgrade_mode, full_invocation)`` or
    ``(None, None, None)``. The probe consults the active run's
    ``events.jsonl`` and walks events newest-first, returning the FIRST
    ``action == "intent-check"`` event whose verdict starts with
    ``"BLOCKED"`` AND whose ``signals.next_commands[0]`` names a
    ``roam mode <upgrade>`` invocation.

    The intent-check command (``cmd_intent_check.py``) already encodes
    the upgrade target in ``next_commands`` exactly as ``roam mode
    <upgrade>  # to unlock '<cmd>'`` -- we lift that string verbatim so
    the agent receives a copy-paste-executable suggestion (LAW 12).

    Best-effort throughout. A missing ledger, an unparseable file, or
    a missing helper module returns the empty triple so the router
    falls through to the next branch.
    """
    try:
        from roam.runs.helpers import get_active_run_id
        from roam.runs.ledger import read_run_events
    except Exception:
        return (None, None, None)
    try:
        run_id = get_active_run_id(root)
    except Exception:
        return (None, None, None)
    if not run_id:
        return (None, None, None)

    # Collect events newest-first. ``read_run_events`` streams in seq
    # order, so we materialise and reverse.
    try:
        events = list(read_run_events(root, run_id))
    except Exception:
        return (None, None, None)
    for ev in reversed(events):
        if not isinstance(ev, dict):
            continue
        if ev.get("action") != "intent-check":
            continue
        verdict = ev.get("summary_verdict")
        if not isinstance(verdict, str) or not verdict.strip().upper().startswith("BLOCKED"):
            continue
        # ``signals.next_commands`` may be a list of strings; the
        # intent-check command emits ``["roam mode <upgrade>  # ..."]``.
        signals = ev.get("signals")
        if not isinstance(signals, dict):
            continue
        nc = signals.get("next_commands")
        if not isinstance(nc, list) or not nc:
            continue
        first = nc[0]
        if not isinstance(first, str) or not first.strip():
            continue
        # Parse: strip a trailing "# ..." comment, then bare-name.
        s = first.split("#", 1)[0].strip()
        if not s:
            continue
        # Build a clean invocation token. ``s`` already starts with
        # ``roam mode <name>`` -- keep that whole prefix as the
        # invocation (copy-paste-executable).
        tokens = s.split()
        if len(tokens) < 3:
            continue
        if tokens[0] != "roam" or tokens[1] != "mode":
            continue
        upgrade_mode = tokens[2]
        # The blocked command is the event's ``target`` field (set by
        # cmd_intent_check.py as ``target=intended_command``).
        blocked = ev.get("target")
        if not isinstance(blocked, str) or not blocked:
            blocked = None
        return (blocked, upgrade_mode, s)
    return (None, None, None)


def _read_pending_before_pr_check(root: Path) -> tuple[str | None, str | None, str | None]:
    """Find a constitution ``before_pr`` check the active run hasn't done yet.

    Returns ``(bare_command, full_invocation, run_id)`` or
    ``(None, None, None)``. This is the R24 wiring: when an agent has
    an in-progress run AND the repo has a constitution, surface the
    next un-run ``before_pr`` check so the agent doesn't forget to
    validate the PR bundle / mined laws before opening a PR.

    Everything is wrapped defensively -- a missing constitution, an
    unparseable file, or a missing runs ledger must NEVER crash the
    router. If anything fails we return the empty triple and let the
    next branch fire.
    """
    try:
        from roam.constitution.loader import load_constitution

        constitution = load_constitution(root)
    except Exception:
        return (None, None, None)
    if constitution is None:
        return (None, None, None)
    before_pr = constitution.required_checks.get("before_pr") or []
    if not before_pr:
        return (None, None, None)

    # Resolve active run id without forcing an import cycle.
    try:
        from roam.runs.helpers import get_active_run_id

        run_id = get_active_run_id(root)
    except Exception:
        return (None, None, None)
    if not run_id:
        return (None, None, None)

    # Read the run's events.jsonl and collect every action / envelope_command
    # we've seen so we can mark which before_pr checks already fired.
    seen: set[str] = set()
    try:
        from roam.runs.ledger import read_run_events

        for ev in read_run_events(root, run_id):
            if not isinstance(ev, dict):
                continue
            action = ev.get("action")
            env_cmd = ev.get("envelope_command")
            if isinstance(action, str) and action:
                seen.add(_bare_command_name(action))
            if isinstance(env_cmd, str) and env_cmd:
                seen.add(_bare_command_name(env_cmd))
    except Exception:
        return (None, None, None)

    for template in before_pr:
        bare = _bare_command_name(template)
        if not bare:
            continue
        if bare in seen:
            continue
        # Build a clean executable string.
        s = template.strip()
        invocation = s if s.startswith("roam ") else f"roam {s}"
        return (bare, invocation, run_id)

    return (None, None, None)


# ---------------------------------------------------------------------------
# State assembly
# ---------------------------------------------------------------------------


def _collect_state() -> RepoState:
    """Gather all signals into a single ``RepoState`` snapshot.

    Every helper is wrapped — if any one fails, the router still produces
    a useful suggestion from the remaining signals.
    """
    state = RepoState()

    try:
        root = find_project_root()
    except Exception:
        return state

    state.project_root = str(root)
    try:
        state.has_index = db_exists(root)
    except Exception:
        state.has_index = False

    if state.has_index:
        try:
            is_stale, reason = _check_index_staleness(root)
            state.index_stale = is_stale
            state.index_stale_reason = reason
        except Exception:
            pass

    try:
        state.uncommitted_count = _git_porcelain_count(root)
        state.has_uncommitted_changes = state.uncommitted_count > 0
    except Exception:
        pass

    try:
        state.recent_memory_commands = _read_recent_memory_commands(root)
    except Exception:
        pass

    try:
        cmd, src = _read_recent_envelope_next_command(root)
        state.recent_envelope_next_command = cmd
        state.recent_envelope_source = src
    except Exception:
        pass

    # R24 constitution-pending probe -- only fires when the file exists
    # AND a run is active. Cheap, defensive, never raises.
    try:
        constitution_yml = root / ".roam" / "constitution.yml"
        state.has_constitution = constitution_yml.exists()
    except Exception:
        state.has_constitution = False
    if state.has_constitution:
        try:
            bare, invocation, run_id = _read_pending_before_pr_check(root)
            state.pending_before_pr_check = bare
            state.pending_before_pr_invocation = invocation
            state.active_run_id = run_id
        except Exception:
            pass

    # R16 / W14.2 — surface a recent intent-check BLOCKED event whose
    # upgrade target would unblock the agent. Independent of constitution
    # presence; only requires an active run with an intent-check trail.
    try:
        blocked_cmd, upgrade_mode, mode_invocation = _read_recent_mode_block(root)
        state.mode_upgrade_blocked_command = blocked_cmd
        state.mode_upgrade_target = upgrade_mode
        state.mode_upgrade_invocation = mode_invocation
    except Exception:
        pass

    return state


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------


def _select_suggestion(state: RepoState) -> dict:
    """Translate a ``RepoState`` snapshot into a single suggestion dict.

    Returned dict shape:
        {
            "verdict": str,   # paste-able imperative line
            "command": str,   # canonical machine-readable verb
            "reason":  str,   # short tag for the decision branch
            "state":   str,   # idle | stale | uncommitted | from_prior | uninitialized
            "next_invocation": str,  # full executable string for next_steps
        }

    Branches (priority order — first match wins):
      1. No index             -> ``roam init``                 (state: uninitialized)
      2. Stale index          -> ``roam index --force``        (state: stale)
      3. Recent envelope      -> command from prior envelope   (state: from_prior)
      4. Uncommitted          -> ``roam diff``                 (state: uncommitted)
      5. Constitution pending -> ``roam <before_pr_check>``    (state: constitution_pending)
      6. Mode upgrade needed  -> ``roam mode <upgrade>``       (state: mode_upgrade_needed)
      7. Idle                 -> ``roam tour``                 (state: idle)

    The recent-envelope branch is placed above ``uncommitted`` so that a
    multi-command workflow (preflight -> diff -> critique) keeps making
    forward progress; otherwise every step would collapse back to ``diff``.
    """
    if not state.has_index:
        return {
            "verdict": "Run `roam init` to index the codebase first.",
            "command": "init",
            "reason": "no_index",
            "state": "uninitialized",
            "next_invocation": "roam init",
        }

    if state.index_stale:
        return {
            "verdict": "Run `roam index --force` to refresh the stale index.",
            "command": "index",
            "reason": "stale_index",
            "state": "stale",
            "next_invocation": "roam index --force",
        }

    if state.recent_envelope_next_command:
        raw = state.recent_envelope_next_command
        bare = _bare_command_name(raw)
        # Build a clean executable string. If the source already starts
        # with 'roam', trust it; otherwise prepend 'roam '.
        if raw.strip().startswith("roam "):
            invocation = raw.strip()
        else:
            invocation = f"roam {raw.strip()}" if raw.strip() else f"roam {bare}"
        return {
            "verdict": f"Run `{invocation}` (suggested by last command's envelope).",
            "command": bare,
            "reason": "from_prior_envelope",
            "state": "from_prior",
            "next_invocation": invocation,
        }

    if state.has_uncommitted_changes:
        n = state.uncommitted_count
        return {
            "verdict": f"Run `roam diff` to see blast radius of {n} uncommitted file(s).",
            "command": "diff",
            "reason": "uncommitted",
            "state": "uncommitted",
            "next_invocation": "roam diff",
        }

    # R24 -- before falling through to idle, surface a constitution
    # `before_pr` check the active run hasn't run yet. The probe only
    # populates this field when both conditions are met (constitution
    # exists + in-progress run), so we don't pester users without a run.
    if state.pending_before_pr_check and state.pending_before_pr_invocation:
        return {
            "verdict": (
                f"Run `{state.pending_before_pr_invocation}` "
                f"-- constitution before_pr check not yet run in this run."
            ),
            "command": state.pending_before_pr_check,
            "reason": "constitution_before_pr_pending",
            "state": "constitution_pending",
            "next_invocation": state.pending_before_pr_invocation,
        }

    # R16 / W14.2 — surface a recent intent-check BLOCKED event. Fires
    # only when the active run logged ``intent-check`` with a BLOCKED
    # verdict AND an upgrade-mode hint. Branch sits below the
    # constitution-pending probe (those are pre-PR gates the agent
    # already knows are pending) but above plain idle so a clean tree
    # with a stale mode block still nudges the agent forward.
    if state.mode_upgrade_target and state.mode_upgrade_invocation:
        blocked = state.mode_upgrade_blocked_command or "a command"
        verdict = (
            f"Run `{state.mode_upgrade_invocation}` "
            f"to enable `{blocked}` (recent intent-check returned BLOCKED)."
        )
        return {
            "verdict": verdict,
            "command": "mode",
            "reason": "mode_upgrade_needed",
            "state": "mode_upgrade_needed",
            "next_invocation": state.mode_upgrade_invocation,
        }

    return {
        "verdict": "No pending work. Run `roam tour` to explore the codebase.",
        "command": "tour",
        "reason": "idle",
        "state": "idle",
        "next_invocation": "roam tour",
    }


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="next",
    category="exploration",
    summary="Suggest the next roam command from cheap repo-state signals.",
    inputs=[],
    outputs=["command", "verdict"],
    examples=["roam next"],
    tags=["router", "agent-os"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command(name="next")
@click.pass_context
def next_cmd(ctx):
    """Suggest the next roam command based on current repo state.

    Bounded agent-router that examines cheap signals (index presence,
    staleness, working-tree dirtiness, recent envelope, recent memory)
    and emits one imperative recommendation. Designed to run in <200ms.

    \b
    Examples:
      roam next
      roam --json next

    See also ``ask`` (free-form task routing) and ``workflow``
    (curated multi-step recipes).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    state = _collect_state()
    suggestion = _select_suggestion(state)

    if json_mode:
        # ``next_steps`` is the structured payload key the envelope
        # builder pulls into ``agent_contract.next_commands``. Keeping
        # it as a list with the chosen command first satisfies LAW 2
        # (imperative, copy-paste-executable).
        next_steps = [suggestion["next_invocation"]]
        # ``partial_success`` required on every envelope. True whenever
        # the router is reacting to a non-ideal repo state (no index /
        # stale index / pending work) so agents can see at a glance the
        # router is nudging away from idle.
        partial = suggestion["state"] not in ("idle", "from_prior")
        click.echo(
            to_json(
                json_envelope(
                    "next",
                    summary={
                        "verdict": suggestion["verdict"],
                        "command": suggestion["command"],
                        "reason": suggestion["reason"],
                        "state": suggestion["state"],
                        "partial_success": partial,
                    },
                    next_steps=next_steps,
                    state=asdict(state),
                )
            )
        )
        return

    # Text output — verdict first (LAW 6: must work standalone).
    click.echo(f"VERDICT: {suggestion['verdict']}")
    # A second line that surfaces the supporting state so a human
    # operator (not just an agent) can sanity-check the choice.
    if suggestion["state"] == "uninitialized":
        click.echo("STATE: no `.roam/index.db` found in this project.")
    elif suggestion["state"] == "stale":
        if state.index_stale_reason:
            click.echo(f"STATE: {state.index_stale_reason}")
    elif suggestion["state"] == "from_prior":
        if state.recent_envelope_source:
            click.echo(f"STATE: prior envelope `.roam/responses/{state.recent_envelope_source}`")
    elif suggestion["state"] == "uncommitted":
        click.echo(f"STATE: {state.uncommitted_count} uncommitted file(s).")
    elif suggestion["state"] == "mode_upgrade_needed":
        click.echo(
            f"STATE: recent intent-check BLOCKED -- upgrade to "
            f"{state.mode_upgrade_target} mode to unlock "
            f"`{state.mode_upgrade_blocked_command or '?'}`."
        )
    else:
        click.echo("STATE: index fresh, working tree clean, no prior envelope.")
