"""``roam replay <run_id>`` -- re-narrate a past agent run from the ledger.

Consumes the R20 substrate that W6.5 shipped (``.roam/runs/<run_id>/``)
and turns an opaque events.jsonl into something an agent or human can
actually scan: a numbered timeline of actions, verdicts, and an
overall verdict for the run.

Three modes:

  ``roam replay <run_id>``                              text narrative
  ``roam replay <run_id> --json``                       structured envelope
  ``roam replay <run_id> --execute --dry-run``          PREVIEW only -- shows the
                                                        commands that ``--execute``
                                                        would run.
  ``roam replay <run_id> --execute --no-dry-run``       actually re-run each
                                                        logged command.

Safety: ``--execute`` is destructive in spirit (it shells out to roam
subcommands which may write to the index / runs ledger / memory store).
We therefore REFUSE to execute unless the caller either:

  - passes ``--dry-run`` to preview (no side effects), OR
  - passes ``--no-dry-run`` to explicitly acknowledge they want side
    effects.

Bare ``--execute`` is an error -- this prevents accidental "I just
wanted to see what would happen" runs from mutating state.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.output.formatter import json_envelope, to_json
from roam.runs.ledger import read_run_events, read_run_meta, run_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_ts(ts: str) -> str:
    """Render an ISO-8601 ``ts`` as ``HH:MM:SS`` for the narrative timeline.

    Falls back to the raw string if parsing fails -- the narrative is a
    "best effort" view; we'd rather show something than nothing.
    """
    if not isinstance(ts, str) or not ts:
        return "--------"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:8] if len(ts) >= 8 else ts
    return dt.strftime("%H:%M:%S")


def _duration_ms(started_at: str, ended_at: Optional[str]) -> int:
    """Return run duration in ms. ``0`` when either side is missing/bad."""
    if not started_at or not ended_at:
        return 0
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    delta = e - s
    return int(delta.total_seconds() * 1000)


def _truncate(s: str, width: int = 60) -> str:
    """Single-line trim with ellipsis -- keeps the timeline readable."""
    if not isinstance(s, str):
        return ""
    s = s.replace("\n", " ").strip()
    if len(s) <= width:
        return s
    return s[: width - 3] + "..."


def _reconstruct_command(event: dict) -> Optional[list[str]]:
    """Build a ``roam <action> [target]`` argv from a logged event.

    Returns ``None`` when the action does not name a known roam
    subcommand -- meta-events like ``end`` or freeform ``commit``
    actions should not be re-executed.

    The reconstruction is deliberately minimal: action + optional
    target. Flags that the original invocation used are not recoverable
    from the ledger today (they aren't logged), so replay is a
    best-effort approximation, not a perfect rerun. The drift report
    surfaces this when verdicts differ.
    """
    from roam.cli import _COMMANDS  # local import to avoid CLI import cost

    action = (event.get("action") or "").strip()
    if not action:
        return None
    if action not in _COMMANDS:
        return None
    argv = ["roam", action]
    target = (event.get("target") or "").strip()
    if target:
        argv.append(target)
    return argv


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="replay",
    category="agent-os",
    summary="Re-narrate a past agent run from the ledger; optionally rerun its commands.",
    inputs=["run_id"],
    outputs=["events", "stats", "verdict"],
    examples=[
        "roam replay run_20260513_a3f9c2",
        "roam replay run_20260513_a3f9c2 --json",
        "roam replay run_20260513_a3f9c2 --execute --dry-run",
    ],
    tags=["runs", "replay", "agent-os", "r20"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("replay")
@click.argument("run_id")
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Re-run each logged command. REQUIRES --dry-run (preview) or --no-dry-run (acknowledge side effects).",
)
@click.option(
    "--dry-run/--no-dry-run",
    "dry_run",
    default=None,
    help="With --execute: --dry-run shows commands without running; --no-dry-run runs them.",
)
@click.pass_context
def replay_cmd(ctx, run_id, execute, dry_run):
    """Re-narrate a past run and (optionally) rerun its commands.

    Without ``--execute`` this is a pure read of ``.roam/runs/<run_id>/``
    that prints a numbered timeline and an overall verdict.

    With ``--execute`` we additionally reconstruct each logged command
    (action + target) and either preview the argv (``--dry-run``) or
    shell out to each (``--no-dry-run``). The latter is **destructive**
    in the sense that any side-effects of the underlying commands
    (writing to .roam/, mutating memory, etc.) happen for real.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    # --- Safety gate on --execute --------------------------------------
    # --execute must be paired with an explicit --dry-run/--no-dry-run.
    # This blocks the "I forgot it had side effects" foot-gun pattern.
    if execute and dry_run is None:
        verdict = (
            "refusing to --execute without --dry-run; pass --dry-run to preview "
            "or --no-dry-run to acknowledge side effects"
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "replay",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "execute_requires_dry_run",
                        },
                        run_id=run_id,
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    # --- Load run -------------------------------------------------------
    meta = read_run_meta(root, run_id)
    if meta is None:
        verdict = f"run {run_id} not found under .roam/runs/"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "replay",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "missing_run",
                            "next_commands": [
                                "roam runs list",
                                "roam runs start --agent <name>",
                            ],
                        },
                        run_id=run_id,
                        agent="",
                        duration_ms=0,
                        events_count=0,
                        events=[],
                        stats={
                            "unique_actions": [],
                            "partial_success_count": 0,
                            "by_action": {},
                            "verdicts": [],
                        },
                        facts_extra=[f"run {run_id} does not exist on disk"],
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        click.echo("  hint: run 'roam runs list' to see available runs")
        ctx.exit(2)

    events = list(read_run_events(root, run_id))
    events_count = len(events)
    duration_ms = _duration_ms(meta.started_at, meta.ended_at)

    # Stats -- the agent contract needs structured facts, not just text.
    actions = [(e.get("action") or "") for e in events if e.get("action")]
    action_counter = Counter(actions)
    unique_actions = sorted(action_counter.keys())
    partial_failures = [
        (e.get("action") or "") for e in events if bool(e.get("partial_success"))
    ]
    partial_count = len(partial_failures)
    verdicts = [
        e.get("summary_verdict", "") for e in events if e.get("summary_verdict")
    ]

    # State: missing_run handled above; here we discriminate between an
    # in-progress (not yet ended) run and a properly-closed one.
    if meta.status == "in_progress" or not meta.ended_at:
        state = "incomplete_run"
    else:
        state = "ok"

    # --- Compose verdict ------------------------------------------------
    gate_actions = {"preflight", "diff", "critique", "pr-prep", "pr-analyze", "attest", "verify"}
    gate_count = sum(1 for a in actions if a in gate_actions)
    # Cheap "SAFE reached" check: any logged verdict starts with SAFE.
    safe_reached = any(
        isinstance(v, str) and v.strip().upper().startswith("SAFE")
        for v in verdicts
    )
    if state == "incomplete_run":
        verdict = (
            f"replayed {events_count} event(s) from {run_id} (in progress, agent={meta.agent})"
        )
    else:
        parts = [
            f"agent {meta.agent} ran {gate_count} gate command(s)" if gate_count else f"agent {meta.agent} ran {len(actions)} action(s)",
        ]
        if safe_reached:
            parts.append("SAFE verdict reached")
        if partial_count:
            parts.append(f"{partial_count} partial result(s)")
        verdict = "; ".join(parts) or f"replayed {events_count} event(s) from {run_id}"

    # --- Execute path --------------------------------------------------
    # Only enter this branch when --execute is explicit AND --dry-run
    # was set (either way). Bare --execute would have errored above.
    execute_report = None
    if execute:
        reconstructed = []
        unknown_actions = []
        for ev in events:
            argv = _reconstruct_command(ev)
            if argv is None:
                if ev.get("action"):
                    unknown_actions.append(ev.get("action"))
                continue
            reconstructed.append(
                {
                    "seq": ev.get("seq"),
                    "argv": argv,
                    "shell": " ".join(shlex.quote(a) for a in argv),
                    "original_verdict": ev.get("summary_verdict", "") or "",
                }
            )
        execute_report = {
            "dry_run": bool(dry_run),
            "would_run_count": len(reconstructed),
            "unknown_actions": sorted(set(unknown_actions)),
            "commands": reconstructed,
            "results": [],
            "drift": [],
        }

        if not dry_run:
            # Live execution. Each shell-out is best-effort; we capture
            # exit + stdout tail so the drift report can compare. We do
            # NOT recurse into another `roam replay` invocation, but
            # otherwise any side effects of the underlying commands
            # happen for real.
            for item in reconstructed:
                argv = item["argv"]
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "roam"] + argv[1:],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                    )
                    new_out = (proc.stdout or "")[-400:]
                    exit_code = proc.returncode
                except (subprocess.TimeoutExpired, OSError) as exc:
                    new_out = f"(failed to invoke: {exc})"
                    exit_code = -1
                # Pull a 1-line new verdict if the rerun printed one.
                new_verdict = ""
                for line in new_out.splitlines():
                    if line.startswith("VERDICT:"):
                        new_verdict = line[len("VERDICT:") :].strip()
                        break
                drift = bool(
                    new_verdict
                    and item["original_verdict"]
                    and new_verdict != item["original_verdict"]
                )
                execute_report["results"].append(
                    {
                        "seq": item["seq"],
                        "argv": argv,
                        "exit_code": exit_code,
                        "new_verdict": new_verdict,
                        "original_verdict": item["original_verdict"],
                        "drift": drift,
                    }
                )
                if drift:
                    execute_report["drift"].append(
                        {
                            "seq": item["seq"],
                            "action": argv[1] if len(argv) > 1 else "",
                            "from": item["original_verdict"],
                            "to": new_verdict,
                        }
                    )

    # --- Agent contract (facts + next_commands) ------------------------
    facts = [
        f"run {run_id} has {events_count} event(s)",
        f"agent {meta.agent} status={meta.status}",
    ]
    if gate_count:
        facts.append(f"{gate_count} gate command(s) logged")
    if partial_count:
        facts.append(f"{partial_count} action(s) reported partial_success")
    if safe_reached:
        facts.append("at least one SAFE verdict reached")
    if execute_report:
        facts.append(
            f"--execute {'preview' if execute_report['dry_run'] else 'ran'} "
            f"{execute_report['would_run_count']} command(s)"
        )

    next_commands: list[str] = []
    if state == "incomplete_run":
        next_commands.append(f"roam runs end --run-id {run_id}")
    next_commands.append(f"roam runs show {run_id}")
    if execute and dry_run is True and execute_report and execute_report["would_run_count"]:
        next_commands.append(f"roam replay {run_id} --execute --no-dry-run")
    if not execute:
        next_commands.append("roam agent-score")

    # --- JSON envelope --------------------------------------------------
    # The formatter auto-derives ``agent_contract`` from ``summary.next_commands``
    # + summary numerics, so we route next_commands through summary
    # rather than fight the formatter. ``facts_extra`` is preserved as a
    # top-level payload key for consumers that want the richer narration.
    if json_mode:
        envelope_kwargs = {
            "run_id": run_id,
            "agent": meta.agent,
            "duration_ms": duration_ms,
            "events_count": events_count,
            "events": events,
            "stats": {
                "unique_actions": unique_actions,
                "partial_success_count": partial_count,
                "by_action": dict(action_counter),
                "verdicts": verdicts,
                "gate_count": gate_count,
                "safe_reached": safe_reached,
            },
            "meta": meta.to_dict(),
            "facts_extra": facts,
        }
        if execute_report is not None:
            envelope_kwargs["execute"] = execute_report
        click.echo(
            to_json(
                json_envelope(
                    "replay",
                    summary={
                        "verdict": verdict,
                        "partial_success": partial_count > 0 or state == "incomplete_run",
                        "state": state,
                        "run_id": run_id,
                        "next_commands": next_commands,
                    },
                    budget=token_budget,
                    **envelope_kwargs,
                )
            )
        )
        return

    # --- Text narrative ------------------------------------------------
    started = meta.started_at or "?"
    # W14.2 Synergy 4 — surface the mode at the top of the narrative.
    mode_phrase = f", mode={meta.mode}" if meta.mode else ""
    click.echo(
        f"RUN {run_id} (agent={meta.agent}{mode_phrase}) started {started}, "
        f"{events_count} events, status={meta.status}"
    )
    if events_count == 0:
        click.echo("  (no events logged)")
    for ev in events:
        seq = ev.get("seq", "?")
        ts = _short_ts(ev.get("ts", ""))
        action = ev.get("action", "-") or "-"
        target = ev.get("target", "") or ""
        v = _truncate(ev.get("summary_verdict", "") or "")
        partial = " [partial]" if ev.get("partial_success") else ""
        label = f"{action} {target}".strip()
        if v:
            click.echo(f"  [{seq}]  {ts}  {label:<40} -> \"{v}\"{partial}")
        else:
            click.echo(f"  [{seq}]  {ts}  {label}{partial}")
    if meta.ended_at:
        click.echo(f"  end       {_short_ts(meta.ended_at)}  status={meta.status}")
    click.echo(f"VERDICT: {verdict}")

    if execute_report is not None:
        click.echo("")
        mode_label = "PREVIEW (dry-run)" if execute_report["dry_run"] else "EXECUTED"
        click.echo(
            f"--execute {mode_label}: {execute_report['would_run_count']} command(s) to replay"
        )
        for item in execute_report["commands"]:
            click.echo(f"  [{item['seq']}] {item['shell']}")
        if execute_report["unknown_actions"]:
            click.echo(
                f"  ({len(execute_report['unknown_actions'])} action(s) "
                f"not in roam command registry: {', '.join(execute_report['unknown_actions'])})"
            )
        if execute_report["results"]:
            click.echo("")
            click.echo("Drift report:")
            if not execute_report["drift"]:
                click.echo("  (no verdict drift)")
            for d in execute_report["drift"]:
                click.echo(f"  [{d['seq']}] {d['action']}: \"{d['from']}\" -> \"{d['to']}\"")
