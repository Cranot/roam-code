"""``roam mode`` — agent-mode policy CLI (R16).

Inspects, switches, and queries the active agent mode. Modes gate which
roam commands an agent is allowed to invoke:

  * ``read_only``     — explore / inspect (no edits)
  * ``safe_edit``     — read_only + diff/critique/pr-bundle
  * ``migration``     — safe_edit + migration-plan/validate-plan/apply-plan
  * ``autonomous_pr`` — migration + pr-prep/pr-analyze/commit/attest

Substrate only — this command exposes the policy and persists the active
mode to ``.roam/active_mode``, but does NOT auto-enforce blocking at
command-dispatch level. Enforcement is opt-in via
``roam.modes.check_command_allowed()``.

Usage::

    roam mode                       # show active mode + allowed-command count
    roam mode read_only             # switch to read_only and persist
    roam mode --check attest        # query: is attest allowed right now?
    roam mode --list                # list all valid modes + counts
    roam mode --json                # JSON envelope

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam mode`` operates on substrate state in ``.roam/``
(the active-mode marker) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
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
    set_active_mode,
)
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


def _detect_stale_active_mode(repo_root) -> Optional[str]:
    """Return the raw contents of ``.roam/active_mode`` IFF it exists but
    names an unknown mode (so callers can warn instead of silently
    falling through to the default). Returns ``None`` when the file is
    absent or contents are valid.
    """
    path = repo_root / ".roam" / "active_mode"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw and raw not in VALID_MODES:
        return raw
    return None


def _build_envelope(
    *,
    repo_root,
    mode_arg: Optional[str],
    check_arg: Optional[str],
    list_flag: bool,
) -> tuple[dict, int]:
    """Return ``(envelope_dict, exit_code)`` for the requested operation."""
    policies = list_modes(repo_root)
    total_commands = len({c for p in policies.values() for c in p.allowed_commands})

    # ---- --list: enumerate every mode + count
    if list_flag:
        modes_view = []
        for name in VALID_MODES:
            p = policies[name]
            modes_view.append(
                {
                    "mode": name,
                    "allowed_count": len(p.allowed_commands),
                    "allowed_commands": sorted(p.allowed_commands),
                    "source": p.source,
                }
            )
        active = resolve_mode(repo_root)
        verdict = (
            f"{len(VALID_MODES)} modes available; active mode: {active.name} "
            f"({len(active.allowed_commands)} allowed commands)"
        )
        return (
            json_envelope(
                "mode",
                summary={
                    "verdict": verdict,
                    "state": "ok",
                    "partial_success": False,
                    "active_mode": active.name,
                    "active_allowed_count": len(active.allowed_commands),
                    "modes_total": len(VALID_MODES),
                    "policy_source": active.source,
                },
                modes=modes_view,
                agent_contract={
                    "facts": [
                        f"{name} mode allows {len(policies[name].allowed_commands)} commands" for name in VALID_MODES
                    ],
                    "next_commands": [
                        f"roam mode {name}  # switch to {name}" for name in VALID_MODES if name != active.name
                    ],
                },
            ),
            EXIT_SUCCESS,
        )

    # ---- --check: query a specific command
    if check_arg:
        active = resolve_mode(repo_root)
        allowed, reason = check_command_allowed(repo_root, check_arg, active)
        verdict = f"'{check_arg}' allowed in {active.name} mode" if allowed else f"BLOCKED: {reason}"
        # Find which modes allow it (for next_commands).
        modes_allowing = [m for m in VALID_MODES if check_arg in policies[m].allowed_commands]
        return (
            json_envelope(
                "mode",
                summary={
                    "verdict": verdict,
                    "state": "ok",
                    "partial_success": not allowed,
                    "active_mode": active.name,
                    "checked_command": check_arg,
                    "allowed": allowed,
                    "reason": reason,
                    "modes_allowing": modes_allowing,
                },
                agent_contract={
                    "facts": [
                        f"active mode is {active.name}",
                        f"'{check_arg}' is {'allowed' if allowed else 'BLOCKED'}",
                        (
                            f"'{check_arg}' is allowed in: {', '.join(modes_allowing)}"
                            if modes_allowing
                            else f"'{check_arg}' is not in any mode's allow-list"
                        ),
                    ],
                    "next_commands": (
                        []
                        if allowed
                        else [f"roam mode {modes_allowing[0]}  # to unlock '{check_arg}'"]
                        if modes_allowing
                        else []
                    ),
                },
            ),
            EXIT_SUCCESS if allowed else EXIT_GATE_FAILURE,
        )

    # ---- mode <name>: switch active mode
    if mode_arg:
        if mode_arg not in VALID_MODES:
            return (
                json_envelope(
                    "mode",
                    summary={
                        "verdict": (f"unknown mode '{mode_arg}' (valid: {', '.join(VALID_MODES)})"),
                        "state": "error",
                        "partial_success": True,
                        "active_mode": resolve_mode(repo_root).name,
                        "requested_mode": mode_arg,
                    },
                    valid_modes=list(VALID_MODES),
                    agent_contract={
                        "facts": [
                            f"requested mode '{mode_arg}' is not a valid mode",
                            f"valid modes: {', '.join(VALID_MODES)}",
                        ],
                        "next_commands": [f"roam mode {name}" for name in VALID_MODES],
                    },
                ),
                EXIT_USAGE,
            )
        set_active_mode(repo_root, mode_arg)
        policy = resolve_mode(repo_root, mode_name=mode_arg)
        verdict = f"active mode: {policy.name} ({len(policy.allowed_commands)} allowed commands)"
        return (
            json_envelope(
                "mode",
                summary={
                    "verdict": verdict,
                    "state": "ok",
                    "partial_success": False,
                    "active_mode": policy.name,
                    "allowed_count": len(policy.allowed_commands),
                    "modes_total": len(VALID_MODES),
                    "policy_source": policy.source,
                    "persisted": True,
                },
                allowed_commands=sorted(policy.allowed_commands),
                denied_commands=sorted(
                    {c for p in policies.values() for c in p.allowed_commands} - policy.allowed_commands
                ),
                agent_contract={
                    "facts": [
                        f"active mode is {policy.name}",
                        f"{policy.name} mode allows {len(policy.allowed_commands)} of {total_commands} unique commands across all modes",
                        f"policy source: {policy.source}",
                    ],
                    "next_commands": [
                        "roam mode --list  # see all modes",
                        "roam mode --check <cmd>  # query a specific command",
                    ],
                },
            ),
            EXIT_SUCCESS,
        )

    # ---- default: show active mode
    active = resolve_mode(repo_root)
    stale_raw = _detect_stale_active_mode(repo_root)
    verdict = f"active mode: {active.name} ({len(active.allowed_commands)} allowed commands)"
    facts = [
        f"active mode is {active.name}",
        f"{active.name} mode allows {len(active.allowed_commands)} of {total_commands} unique commands across all modes",
        f"policy source: {active.source}",
    ]
    summary = {
        "verdict": verdict,
        "state": "ok",
        "partial_success": False,
        "active_mode": active.name,
        "allowed_count": len(active.allowed_commands),
        "modes_total": len(VALID_MODES),
        "policy_source": active.source,
        "persisted": False,
    }
    if stale_raw is not None:
        facts.insert(
            0,
            f"stale .roam/active_mode (contents='{stale_raw}' not in {sorted(VALID_MODES)}); falling through to {active.name}",
        )
        summary["stale_active_mode_file"] = stale_raw
        summary["partial_success"] = True
    return (
        json_envelope(
            "mode",
            summary=summary,
            allowed_commands=sorted(active.allowed_commands),
            denied_commands=sorted(
                {c for p in policies.values() for c in p.allowed_commands} - active.allowed_commands
            ),
            agent_contract={
                "facts": facts,
                "next_commands": (
                    [f"roam mode {active.name}  # rewrite .roam/active_mode with a valid value"]
                    if stale_raw is not None
                    else []
                )
                + [f"roam mode {name}  # switch to {name}" for name in VALID_MODES if name != active.name],
            },
        ),
        EXIT_SUCCESS,
    )


@roam_capability(
    category="agent-os",
    summary=("Read/switch the active agent mode and query mode-gated commands. Substrate for R16 agent-mode policy."),
    inputs=["mode_name", "command_to_check"],
    outputs=["active_mode", "allowed_commands", "denied_commands", "verdict"],
    examples=[
        "roam mode",
        "roam mode read_only",
        "roam mode --check attest",
        "roam mode --list",
    ],
    tags=["agent", "policy", "r16"],
    ai_safe=True,
    requires_index=False,
    since="13.0",
)
@click.command(name="mode")
@click.argument("mode_name", required=False, default=None)
@click.option(
    "--check",
    "check_cmd",
    default=None,
    metavar="COMMAND",
    help="Query whether COMMAND is allowed in the active mode.",
)
@click.option(
    "--list",
    "list_flag",
    is_flag=True,
    default=False,
    help="List every valid mode and its allowed-command count.",
)
@click.pass_context
def mode_cmd(
    ctx,
    mode_name: Optional[str],
    check_cmd: Optional[str],
    list_flag: bool,
):
    """Show, switch, or query the active agent mode.

    Substrate only — this command persists the active mode and answers
    "is X allowed?" queries, but does not auto-enforce gating at the
    CLI dispatch level. Enforcement is opt-in via
    ``roam.modes.check_command_allowed()``.

    \b
    Examples:
      roam mode                       # show active mode + allowed-command count
      roam mode read_only             # switch to read_only and persist
      roam mode --check attest        # is attest allowed in active mode?
      roam mode --list                # enumerate every mode
      roam --json mode                # JSON envelope
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    repo_root = find_project_root()

    # W294 - capture the pre-switch active mode BEFORE _build_envelope
    # calls set_active_mode so we can emit the ``mode_from`` event field
    # to the run ledger. Best-effort: expected policy/config resolution
    # failures here just leave ``mode_from`` unset (the W292 harvester
    # needs only ``mode_to`` to corroborate the matching AuthorityRef).
    mode_from_value: Optional[str] = None
    if mode_name:
        try:
            mode_from_value = resolve_mode(repo_root).name
        except (OSError, TypeError, ValueError):
            mode_from_value = None

    envelope, exit_code = _build_envelope(
        repo_root=repo_root,
        mode_arg=mode_name,
        check_arg=check_cmd,
        list_flag=list_flag,
    )

    # Auto-log (opt-in, never crashes) — mode switches are real decision
    # boundaries; this is high-signal-per-call.
    try:
        action = "mode-switch" if mode_name else ("mode-check" if check_cmd else "mode-show")
        target = mode_name or check_cmd or ""
        # W294 - stamp authority-shaped ``mode_to``/``mode_from`` event
        # fields on a successful mode switch so the W292 collector
        # harvester corroborates the matching mode ``AuthorityRef`` and
        # promotes it to ``provenance="run_ledger"``. We only emit on
        # the SWITCH path (not --check / default show) and only when the
        # envelope reports state="ok" (the switch actually happened).
        # The ``auto_log`` whitelist drops any non-whitelisted keys
        # silently, so the kwarg is safe by construction.
        extra_event_fields: dict | None = None
        if mode_name:
            summary = envelope.get("summary") or {}
            if isinstance(summary, dict) and summary.get("state") == "ok":
                new_mode = summary.get("active_mode")
                if (
                    isinstance(new_mode, str)
                    and new_mode
                    # Skip when the "switch" was a no-op (caller passed the
                    # currently-active mode). No corroboration value, and
                    # the verdict already records the same mode on both
                    # axes.
                    and new_mode != mode_from_value
                ):
                    fields: dict = {"mode_to": new_mode}
                    if isinstance(mode_from_value, str) and mode_from_value:
                        fields["mode_from"] = mode_from_value
                    extra_event_fields = fields
        auto_log(
            envelope,
            action=action,
            target=target,
            repo_root=repo_root,
            extra_event_fields=extra_event_fields,
        )
    except Exception as _exc:
        # auto_log itself never raises, but the extra_event_fields
        # derivation above touches the envelope dict — surface lineage
        # so a dropped mode-switch ledger event has a discoverable cause.
        from roam.observability import log_swallowed

        log_swallowed("cmd_mode:auto_log_extra_fields", _exc)

    if json_mode:
        click.echo(to_json(envelope))
        ctx.exit(exit_code)

    summary = envelope.get("summary") or {}
    click.echo(f"VERDICT: {summary.get('verdict', '')}")

    if list_flag:
        for m in envelope.get("modes", []):
            click.echo(f"  {m['mode']:<14}  {m['allowed_count']:>4} allowed  (source: {m['source']})")
    elif check_cmd:
        click.echo(f"  active mode:     {summary.get('active_mode')}")
        click.echo(f"  checked:         {summary.get('checked_command')}")
        click.echo(f"  allowed:         {summary.get('allowed')}")
        click.echo(f"  reason:          {summary.get('reason')}")
        if summary.get("modes_allowing"):
            click.echo(f"  allowed in:      {', '.join(summary['modes_allowing'])}")
    else:
        click.echo(f"  active mode:     {summary.get('active_mode')}")
        click.echo(f"  allowed count:   {summary.get('allowed_count')}")
        click.echo(f"  policy source:   {summary.get('policy_source')}")
        if mode_name:
            click.echo("  persisted to:    .roam/active_mode")

    ctx.exit(exit_code)
