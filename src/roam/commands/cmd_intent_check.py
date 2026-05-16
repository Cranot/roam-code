"""``roam intent-check`` — pre-flight check that an intended command is allowed (R16).

A no-side-effect query: takes a command name an agent INTENDS to run,
returns a verdict (ALLOWED / BLOCKED) under the currently-active mode.
The intended command is NOT executed.

Designed for harnesses that want to short-circuit on mode-blocked
actions before allocating a tool-call slot. Pairs with ``roam mode``
(switch / inspect) and ``roam permit`` (verdict over a diff).

Exit codes:
  * 0 — ALLOWED
  * 5 — BLOCKED (mode forbids the command)
  * 2 — usage (no command supplied)

Examples::

    roam intent-check attest
    # -> BLOCKED — 'attest' not allowed in safe_edit mode;
    #              run `roam mode autonomous_pr` to enable it

    roam intent-check preflight
    # -> ALLOWED — 'preflight' allowed in safe_edit mode

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because intent-check outputs are invocation-scoped
user-intent-classification aggregates (ALLOWED / BLOCKED verdict over
an intended command name under the active mode) — not per-location
code violations. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH Bucket B propagation plan + W1221-audit memo.
"""

from __future__ import annotations

from typing import Optional

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.exit_codes import EXIT_GATE_FAILURE, EXIT_SUCCESS, EXIT_USAGE
from roam.modes import (
    VALID_MODES,
    check_command_allowed,
    list_modes,
    resolve_mode,
)
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


@roam_capability(
    category="agent-os",
    summary=(
        "Check whether an intended roam command is allowed in the active mode. "
        "Pre-flight gate for R16 agent-mode policy; does not execute the command."
    ),
    inputs=["intended_command", "active_mode"],
    outputs=["verdict", "allowed", "reason", "upgrade_mode"],
    examples=[
        "roam intent-check attest",
        "roam intent-check preflight",
        "ROAM_AGENT_MODE=read_only roam intent-check critique",
    ],
    tags=["agent", "policy", "r16", "gate"],
    ai_safe=True,
    requires_index=False,
    since="13.0",
)
@click.command(name="intent-check")
@click.argument("intended_command", required=False, default=None)
@click.pass_context
def intent_check_cmd(ctx, intended_command: Optional[str]):
    """Verify INTENDED_COMMAND would be allowed by the active mode.

    Returns ALLOWED (exit 0) or BLOCKED (exit 5). Does NOT run the
    intended command — this is a pre-flight policy check.

    \b
    Examples:
      roam intent-check attest          # mode-blocked verb
      roam intent-check preflight       # read-only-safe verb
      roam --json intent-check critique # JSON envelope
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    repo_root = find_project_root()

    if not intended_command:
        verdict = "no command supplied — pass a command name to check"
        envelope = json_envelope(
            "intent-check",
            summary={
                "verdict": verdict,
                "state": "error",
                "partial_success": True,
                "allowed": False,
                "reason": "missing INTENDED_COMMAND argument",
            },
            agent_contract={
                "facts": ["intent-check requires a command name to query"],
                "next_commands": ["roam intent-check <command>"],
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}", err=True)
        ctx.exit(EXIT_USAGE)

    active = resolve_mode(repo_root)
    allowed, reason = check_command_allowed(repo_root, intended_command, active)

    # Find which modes WOULD allow it (for upgrade suggestion).
    policies = list_modes(repo_root)
    modes_allowing = [m for m in VALID_MODES if intended_command in policies[m].allowed_commands]
    upgrade_to = modes_allowing[0] if (not allowed and modes_allowing) else None

    if allowed:
        verdict = f"ALLOWED — '{intended_command}' allowed in {active.name} mode"
    else:
        verdict = f"BLOCKED — {reason}"

    envelope = json_envelope(
        "intent-check",
        summary={
            "verdict": verdict,
            "state": "ok",
            "partial_success": not allowed,
            "allowed": allowed,
            "reason": reason,
            "active_mode": active.name,
            "intended_command": intended_command,
            "upgrade_mode": upgrade_to,
            "modes_allowing": modes_allowing,
        },
        agent_contract={
            "facts": [
                f"active mode is {active.name}",
                f"'{intended_command}' is {'ALLOWED' if allowed else 'BLOCKED'}",
                (
                    f"'{intended_command}' is allowed in: {', '.join(modes_allowing)}"
                    if modes_allowing
                    else f"'{intended_command}' is not in any mode's allow-list"
                ),
            ],
            "next_commands": (
                [f"roam {intended_command}"]
                if allowed
                else ([f"roam mode {upgrade_to}  # to unlock '{intended_command}'"] if upgrade_to else [])
            ),
        },
    )

    # Auto-log: intent-check IS an agent decision boundary.
    try:
        auto_log(
            envelope,
            action="intent-check",
            target=intended_command,
            repo_root=repo_root,
        )
    except Exception:
        pass

    if json_mode:
        click.echo(to_json(envelope))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  active mode:     {active.name}")
        click.echo(f"  intended:        {intended_command}")
        click.echo(f"  allowed:         {allowed}")
        if not allowed and upgrade_to:
            click.echo(f"  upgrade with:    roam mode {upgrade_to}")

    ctx.exit(EXIT_SUCCESS if allowed else EXIT_GATE_FAILURE)
