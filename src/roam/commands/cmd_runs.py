"""Per-agent-run event ledger CLI (R20 substrate).

Six subcommands:

  - ``roam runs start --agent <name>``     -- create a new run directory
  - ``roam runs log --action <action> ...``     -- append an event to a run
  - ``roam runs end [--run-id <id>]``    -- stamp ended_at + final status
  - ``roam runs list [filters]``         -- stream run metadata
  - ``roam runs show <run_id>``          -- dump events for a run
  - ``roam runs verify [run_id|--all]``  -- verify the HMAC signing chain

A run lives on disk at ``.roam/runs/<run_id>/`` -- two files per run
(``meta.json`` + ``events.jsonl``). This is the SUBSTRATE for R20:
CGA signing, replay, agent-score and audit-trail features build on top.

The CLI mirrors the API in :mod:`roam.runs.ledger`; agents that prefer a
programmatic interface can call that directly.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam runs`` operates on substrate state in ``.roam/``
(ledger entries) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""  # W20.6 docstring: added verify subcommand to keep doc accurate

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.ledger import (
    VALID_STATUSES,
    end_run,
    latest_in_progress_run,
    list_runs,
    log_event,
    read_run_events,
    read_run_meta,
    run_dir,
    runs_root,
    start_run,
)
from roam.runs.signing import (
    ensure_ledger_key,
    ledger_key_path,
    verify_chain,
)

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="runs",
    category="setup",
    summary="Per-agent-run event ledger: start, log, end, list, show.",
    inputs=[],
    outputs=["run_id", "events"],
    examples=["roam runs start --agent claude", "roam runs list", "roam runs show RUN_ID"],
    tags=["runs", "ledger", "agent-os"],
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
@click.group("runs")
@click.pass_context
def runs_group(ctx):
    """Per-agent-run event ledger.

    Each run is a directory under ``.roam/runs/<run_id>/`` with two
    files: ``meta.json`` (identity + status) and ``events.jsonl``
    (append-only event stream). Use ``roam runs start`` to open a run,
    ``roam runs log`` to append events, ``roam runs end`` to close it,
    and ``roam runs list`` / ``roam runs show`` to inspect.

    Substrate for R20 replay / agent-score / audit-trail features.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# runs start
# ---------------------------------------------------------------------------


@runs_group.command("start")
@click.option("--agent", required=True, help="Agent identifier (e.g. claude-code, cursor, human).")
@click.pass_context
def runs_start(ctx, agent):
    """Open a new agent run. Echoes the run_id on stdout.

    Captures the run_id so subsequent ``roam runs log`` calls in the
    same shell can omit ``--run-id`` and target the active run.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    try:
        root = find_project_root()
        meta = start_run(root, agent=agent)
    except ValueError as exc:
        verdict = f"error: {exc}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-start",
                        summary={"verdict": verdict, "partial_success": True, "started": False},
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    # W14.2 Synergy 4 — surface the mode tag the ledger captured.
    mode_phrase = f", mode={meta.mode}" if meta.mode else ""
    verdict = f"started run {meta.run_id} (agent={meta.agent}{mode_phrase})"

    summary = {
        "verdict": verdict,
        "partial_success": False,
        "state": "in_progress",
        "started": True,
        "run_id": meta.run_id,
    }
    if meta.mode:
        summary["mode"] = meta.mode

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "runs-start",
                    summary=summary,
                    budget=token_budget,
                    run=meta.to_dict(),
                    path=str(run_dir(root, meta.run_id)),
                    hint={
                        "env": "ROAM_RUN_ID",
                        "shell_export": f"export ROAM_RUN_ID={meta.run_id}",
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  run_id:    {meta.run_id}")
    click.echo(f"  agent:     {meta.agent}")
    click.echo(f"  started:   {meta.started_at}")
    click.echo(f"  status:    {meta.status}")
    if meta.mode:
        click.echo(f"  mode:      {meta.mode}")
    click.echo(f"  path:      {run_dir(root, meta.run_id)}")
    click.echo("")
    click.echo(f"Hint: export ROAM_RUN_ID={meta.run_id}")


# ---------------------------------------------------------------------------
# runs log
# ---------------------------------------------------------------------------


@runs_group.command("log")
@click.option("--run-id", default=None, help="Run id to log against. Defaults to the latest in-progress run.")
@click.option("--action", required=True, help="Event action (e.g. preflight, diff, edit, test, commit, envelope).")
@click.option("--target", default="", help="Symbol / file / target of the action (free-form).")
@click.option("--verdict", "summary_verdict", default="", help="One-line verdict surfaced from the underlying command.")
@click.option(
    "--envelope-command",
    default="",
    help="Name of the roam command that emitted the underlying envelope (if any).",
)
@click.option(
    "--partial-success",
    is_flag=True,
    default=False,
    help="Mark the action as partial-success (mirrors envelope.summary.partial_success).",
)
@click.option("--elapsed-ms", default=0, type=int, help="Elapsed time in milliseconds.")
@click.option(
    "--signal",
    "signals",
    multiple=True,
    help="<KEY>=<VALUE> signal (repeatable). Bundled into the event's 'signals' dict.",
)
@click.pass_context
def runs_log(
    ctx,
    run_id,
    action,
    target,
    summary_verdict,
    envelope_command,
    partial_success,
    elapsed_ms,
    signals,
):
    """Append an event to a run's events.jsonl.

    With no ``--run-id`` we target the most-recent in-progress run for
    the current repo. If there is no active run we error explicitly
    rather than silently swallowing the event.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    if not run_id:
        # Resolve the implicit run: the most-recent in-progress one.
        # Surface a precise error if none is active so the caller knows
        # exactly what to do next.
        active = latest_in_progress_run(root)
        if active is None:
            verdict = "no active run -- run `roam runs start --agent <name>` first"  # W20.6 error-msg consistency
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "runs-log",
                            summary={
                                "verdict": verdict,
                                "partial_success": True,
                                "state": "no_active_run",
                                "logged": False,
                            },
                            # W20.6 error-msg consistency
                            agent_contract={
                                "facts": ["no in-progress run exists for this repo"],
                                "next_commands": ["roam runs start --agent <name>"],
                            },
                        )
                    )
                )
                ctx.exit(2)
            click.echo(f"VERDICT: {verdict}")
            ctx.exit(2)
        run_id = active.run_id

    # Validate the run exists before logging.
    meta = read_run_meta(root, run_id)
    if meta is None:
        verdict = f"run {run_id} does not exist -- run `roam runs list` to find a valid run_id"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-log",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "unknown_run",
                            "logged": False,
                        },
                        # W20.6 error-msg consistency
                        agent_contract={
                            "facts": [f"no run named {run_id} in this repo"],
                            "next_commands": ["roam runs list", "roam runs start --agent <name>"],
                        },
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    # Bundle freeform signals (key=value pairs) into a dict for the event.
    signals_dict: dict = {}
    for raw in signals:
        if "=" in raw:
            k, _, v = raw.partition("=")
            signals_dict[k.strip()] = v.strip()
        else:
            # No '=' -> treat as a boolean flag set to True.
            signals_dict[raw.strip()] = True

    event_fields = {
        "action": action,
        "target": target,
        "envelope_command": envelope_command,
        "partial_success": partial_success,
        "summary_verdict": summary_verdict,
        "signals": signals_dict,
        "elapsed_ms": int(elapsed_ms),
    }
    seq = log_event(root, run_id, **event_fields)

    verdict = f"logged event seq={seq} (action={action}) to {run_id}"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "runs-log",
                    summary={
                        "verdict": verdict,
                        "partial_success": False,
                        "state": "ok",
                        "logged": True,
                        "run_id": run_id,
                        "seq": seq,
                    },
                    budget=token_budget,
                    event={**event_fields, "seq": seq},
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  run_id:   {run_id}")
    click.echo(f"  seq:      {seq}")
    click.echo(f"  action:   {action}")
    if target:
        click.echo(f"  target:   {target}")
    if summary_verdict:
        click.echo(f"  verdict:  {summary_verdict}")


# ---------------------------------------------------------------------------
# runs end
# ---------------------------------------------------------------------------


@runs_group.command("end")
@click.option("--run-id", default=None, help="Run id to close. Defaults to the latest in-progress run.")
@click.option(
    "--status",
    default="completed",
    show_default=True,
    type=click.Choice(sorted(VALID_STATUSES - {"in_progress"})),
    help="Final status. Cannot be 'in_progress'.",
)
@click.option(
    "--with-pr-bundle-emit",
    is_flag=True,
    default=False,
    help=(
        "After closing the run, invoke ``pr-bundle emit`` (with default "
        "auto-collect) on the current branch's bundle and roll the emit "
        "envelope into the runs-end response under ``pr_bundle_emitted``. "
        "No-op (state=no_active_bundle_to_emit) if no bundle exists."
    ),
)
@click.pass_context
def runs_end(ctx, run_id, status, with_pr_bundle_emit):
    """Stamp ended_at + final status on a run's meta.json.

    \b
    With ``--with-pr-bundle-emit``, fuses the natural final step of the
    agent loop ``open run → do work → close run + ship bundle`` into one
    command. The run is closed FIRST so a partial pr-bundle emit can never
    block the close.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    if not run_id:
        active = latest_in_progress_run(root)
        if active is None:
            verdict = "no active run to end -- run `roam runs start --agent <name>` first"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "runs-end",
                            summary={
                                "verdict": verdict,
                                "partial_success": True,
                                "state": "no_active_run",
                                "ended": False,
                            },
                            # W20.6 error-msg consistency
                            agent_contract={
                                "facts": ["no in-progress run exists for this repo"],
                                "next_commands": ["roam runs start --agent <name>"],
                            },
                        )
                    )
                )
                ctx.exit(2)
            click.echo(f"VERDICT: {verdict}")
            ctx.exit(2)
        run_id = active.run_id

    try:
        meta = end_run(root, run_id, status=status)
    except (FileNotFoundError, ValueError) as exc:
        verdict = f"error: {exc}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-end",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "error",
                            "ended": False,
                        },
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    # W15.2 — optionally chain into ``pr-bundle emit`` so the close-run
    # and ship-bundle acts fuse into one command. The run is already closed
    # at this point; bundle-emit failure NEVER blocks the close.
    pr_bundle_emitted: dict | None = None
    pr_bundle_state: str | None = None
    pr_bundle_partial = False
    if with_pr_bundle_emit:
        pr_bundle_emitted, pr_bundle_state, pr_bundle_partial = _emit_pr_bundle_for_end(ctx, root)

    verdict = f"ended run {meta.run_id} (status={meta.status})"
    if with_pr_bundle_emit:
        # Include the bundle verdict in the fused verdict line so an agent
        # that reads only ``summary.verdict`` still sees both signals
        # (LAW 6 — verdict works standalone).
        if pr_bundle_state == "no_active_bundle_to_emit":
            verdict = f"{verdict} + no pr-bundle to emit (no active bundle)"
        elif pr_bundle_emitted is not None:
            bundle_verdict = (pr_bundle_emitted.get("summary") or {}).get("verdict", "")
            if bundle_verdict:
                verdict = f"{verdict} + emitted pr-bundle (verdict: {bundle_verdict})"
            else:
                verdict = f"{verdict} + pr-bundle emit attempted"
        elif pr_bundle_state == "emit_failed":
            verdict = f"{verdict} + pr-bundle emit FAILED (run preserved)"
        else:
            verdict = f"{verdict} + pr-bundle emit failed"

    summary = {
        "verdict": verdict,
        "partial_success": pr_bundle_partial,
        "state": meta.status,
        "ended": True,
        "run_id": meta.run_id,
    }
    if pr_bundle_state is not None:
        summary["pr_bundle_state"] = pr_bundle_state

    if json_mode:
        env_payload = {
            "summary": summary,
            "budget": token_budget,
            "run": meta.to_dict(),
        }
        if pr_bundle_emitted is not None:
            env_payload["pr_bundle_emitted"] = pr_bundle_emitted
        click.echo(
            to_json(
                json_envelope(
                    "runs-end",
                    **env_payload,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  run_id:    {meta.run_id}")
    click.echo(f"  agent:     {meta.agent}")
    click.echo(f"  started:   {meta.started_at}")
    click.echo(f"  ended:     {meta.ended_at}")
    click.echo(f"  status:    {meta.status}")
    if pr_bundle_state is not None:
        click.echo(f"  pr_bundle: {pr_bundle_state}")


def _emit_pr_bundle_for_end(ctx, root) -> tuple[dict | None, str, bool]:
    """Invoke ``pr-bundle emit`` programmatically for ``runs end``.

    Returns ``(emit_envelope_or_None, state_tag, partial_success)``:
      - ``emit_envelope_or_None``: the parsed envelope from the emit call,
        or ``None`` when no bundle exists for the current branch.
      - ``state_tag``: one of ``"emitted"`` / ``"no_active_bundle_to_emit"``
        / ``"emit_failed"``.
      - ``partial_success``: True if the bundle was not emitted cleanly.

    Best-effort: catches every exception so a broken bundle never blocks
    the run-close. (Pattern 2 + LAW 6 — surface the failure state explicitly
    rather than silently reporting success.)
    """
    try:
        from roam.commands.cmd_pr_bundle import _bundle_path, pr_bundle_emit
    except Exception:
        return None, "emit_failed", True

    try:
        bundle_path = _bundle_path(root)
    except Exception:
        return None, "emit_failed", True

    if not bundle_path.is_file():
        return None, "no_active_bundle_to_emit", True

    # Invoke pr-bundle emit via ctx.invoke so it runs with the same Click
    # context (json mode, budget, etc.) as the parent runs-end call.
    # Capture its stdout so we can parse the envelope out and roll it into
    # ours; otherwise the JSON would be double-echoed.
    import io
    import sys as _sys

    saved_stdout = _sys.stdout
    captured = io.StringIO()
    _sys.stdout = captured
    try:
        try:
            ctx.invoke(pr_bundle_emit, auto_collect=True)
        except SystemExit:
            # pr-bundle emit doesn't normally exit, but be defensive.
            pass
        except Exception:
            return None, "emit_failed", True
    finally:
        _sys.stdout = saved_stdout

    raw = captured.getvalue().strip()
    if not raw:
        # No output — likely text mode + a quiet success path. Not a failure.
        return {"summary": {"verdict": "pr-bundle emit completed (text mode)"}}, "emitted", False

    # Parse out the envelope. JSON output is one JSON object; text mode would
    # have ``VERDICT:`` prefix. Handle both gracefully.
    try:
        import json as _json_mod

        emit_env = _json_mod.loads(raw)
    except Exception:
        # Text mode — synthesize a minimal envelope so callers always see
        # something parseable.
        first_line = raw.splitlines()[0] if raw.splitlines() else ""
        verdict_line = first_line.replace("VERDICT:", "").strip() if "VERDICT" in first_line else first_line
        return (
            {"summary": {"verdict": verdict_line or "pr-bundle emit completed"}},
            "emitted",
            False,
        )

    state = (emit_env.get("summary") or {}).get("state") or ""
    partial = (emit_env.get("summary") or {}).get("partial_success", False)
    if state == "complete":
        return emit_env, "emitted", bool(partial)
    # Any non-complete state still ran the emit — surface the bundle state
    # but mark the runs-end as partial_success so the agent sees it didn't
    # ship cleanly.
    return emit_env, "emitted", True


# ---------------------------------------------------------------------------
# runs list
# ---------------------------------------------------------------------------


@runs_group.command("list")
@click.option("--agent", default=None, help="Filter to runs by this agent.")
@click.option("--since", default=None, help="Filter to runs started at >= <SINCE> (ISO-8601).")
@click.option(
    "--status",
    default=None,
    type=click.Choice(sorted(VALID_STATUSES)),
    help="Filter to runs with this status.",
)
@click.option(
    "--top", "--limit", "top", default=0, type=int, help="Cap output to <N> runs (0 = no cap)."
)  # W1142: --limit alias
@click.pass_context
def runs_list(ctx, agent, since, status, top):
    """Stream runs, newest first.

    Empty state (no runs yet) returns a clean envelope with
    ``state: no_runs`` -- never an error or empty stdout.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    rroot = runs_root(root)

    if not rroot.exists():
        verdict = "no runs yet -- run `roam runs start --agent <name>` to open one"  # W20.6 error-msg consistency
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-list",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_runs",
                            "total": 0,
                        },
                        budget=token_budget,
                        runs=[],
                        path=str(rroot),
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    metas = list(list_runs(root, agent=agent, since=since, status=status))
    # W1142-followup-B: cap-hit disclosure. Record the full pre-slice
    # run count so the envelope can disclose when ``--limit`` truncated
    # the list.
    total_metas_full = len(metas)
    if top > 0:
        metas = metas[:top]
    metas_truncated = total_metas_full > len(metas)

    total = len(metas)
    if total == 0:
        verdict = "no runs match the given filters"
        state = "no_matches"
    else:
        verdict = f"{total} run{'s' if total != 1 else ''}"
        state = "ok"

    if json_mode:
        # W1142-followup-B: cap-hit disclosure on the canonical JSON
        # envelope. ``count``/``total_count``/``truncated``/``limit``
        # surface whether the agent's --limit collapsed signal.
        _cap_summary = {
            "count": len(metas),
            "total_count": total_metas_full,
            "truncated": metas_truncated,
            "limit": top,
        }
        _warnings_out: list[str] = []
        if metas_truncated:
            _warnings_out.append(f"truncated to {len(metas)} of {total_metas_full} — pass --limit larger to see more")
        _summary = {
            "verdict": verdict,
            "partial_success": metas_truncated,
            "state": state,
            "total": total,
            **_cap_summary,
        }
        if _warnings_out:
            _summary["warnings_out"] = _warnings_out
        click.echo(
            to_json(
                json_envelope(
                    "runs-list",
                    summary=_summary,
                    budget=token_budget,
                    runs=[m.to_dict() for m in metas],
                    path=str(rroot),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if total == 0:
        return
    rows = []
    for m in metas:
        rows.append([m.run_id, m.agent, m.started_at, m.ended_at or "-", m.status])
    click.echo(format_table(["Run", "Agent", "Started", "Ended", "Status"], rows))


# ---------------------------------------------------------------------------
# runs show
# ---------------------------------------------------------------------------


@runs_group.command("show")
@click.argument("run_id")
@click.pass_context
def runs_show(ctx, run_id):
    """Dump a run's meta + every event in seq order."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    meta = read_run_meta(root, run_id)
    if meta is None:
        verdict = f"run {run_id} does not exist -- run `roam runs list` to find a valid run_id"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-show",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "unknown_run",
                            "total": 0,
                        },
                        budget=token_budget,
                        run=None,
                        events=[],
                        # W20.6 error-msg consistency
                        agent_contract={
                            "facts": [f"no run named {run_id} in this repo"],
                            "next_commands": ["roam runs list"],
                        },
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    events = list(read_run_events(root, run_id))
    total = len(events)
    mode_phrase = f" mode={meta.mode}" if meta.mode else ""
    verdict = f"run {run_id} status={meta.status} events={total}{mode_phrase}"

    summary = {
        "verdict": verdict,
        "partial_success": False,
        "state": meta.status,
        "total": total,
        "run_id": meta.run_id,
    }
    if meta.mode:
        summary["mode"] = meta.mode

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "runs-show",
                    summary=summary,
                    budget=token_budget,
                    run=meta.to_dict(),
                    events=events,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  run_id:   {meta.run_id}")
    click.echo(f"  agent:    {meta.agent}")
    click.echo(f"  started:  {meta.started_at}")
    if meta.ended_at:
        click.echo(f"  ended:    {meta.ended_at}")
    click.echo(f"  status:   {meta.status}")
    if meta.mode:
        click.echo(f"  mode:     {meta.mode}")
    click.echo("")
    if not events:
        click.echo("  (no events)")
        return
    rows = []
    for ev in events:
        seq = ev.get("seq", "?")
        ts = ev.get("ts", "-")
        action = ev.get("action", "-")
        target = ev.get("target", "") or "-"
        v = ev.get("summary_verdict", "") or "-"
        # Trim verdict for table density.
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        rows.append([str(seq), ts, action, target, v])
    click.echo(format_table(["Seq", "Ts", "Action", "Target", "Verdict"], rows))


# ---------------------------------------------------------------------------
# runs verify (R20 phase 4 — HMAC chain integrity)
# ---------------------------------------------------------------------------


def _verify_one_run(root, run_id: str) -> dict:
    """Verify the HMAC chain for *run_id* and return a dict-summary.

    The returned shape feeds directly into the ``runs-verify`` envelope.
    Keys: ``run_id`` plus everything :func:`verify_chain` reports
    (``state``, ``events_verified``, ``first_tamper_at_seq``,
    ``partial_success``, ``final_signature``, ``details``).
    """
    meta = read_run_meta(root, run_id)
    if meta is None:
        return {
            "run_id": run_id,
            "state": "unknown_run",
            "events_verified": 0,
            "first_tamper_at_seq": None,
            "partial_success": True,
            "final_signature": None,
            "details": f"run {run_id} does not exist",
        }
    try:
        key = ensure_ledger_key(root)
    except Exception as exc:
        return {
            "run_id": run_id,
            "state": "key_missing",
            "events_verified": 0,
            "first_tamper_at_seq": None,
            "partial_success": True,
            "final_signature": None,
            "details": f"ledger key unavailable: {exc}",
        }
    events = list(read_run_events(root, run_id))
    result = verify_chain(events, key)
    result["run_id"] = run_id
    return result


@runs_group.command("verify")
@click.argument("run_id", required=False, default=None)
@click.option(
    "--all",
    "verify_all",
    is_flag=True,
    default=False,
    help="Verify every run in the repo. Mutually exclusive with <RUN_ID>.",
)
@click.pass_context
def runs_verify(ctx, run_id, verify_all):
    """Verify the HMAC signing chain over a run's events.jsonl.

    \b
    Each event's signature is HMAC(prev_sig || canonical_event_json).
    Mutating any event invalidates every subsequent signature, so a
    verifier can pinpoint the first tampered seq.

    \b
    States:
      ok         every signature matches
      tampered   chain broken; first_tamper_at_seq names where
      unsigned   legacy events from before signing landed (advisory)
      key_missing the .ledger_key file is unreadable / wrong size

    Exit codes: 0 on ``ok`` / ``unsigned``, 5 on ``tampered``,
    2 on usage error (e.g. unknown run_id).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()

    if verify_all and run_id:
        verdict = "pass <RUN_ID> OR --all, not both"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-verify",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "usage_error",
                            "events_verified": 0,
                        },
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    if not verify_all and not run_id:
        verdict = "pass a <RUN_ID> or use --all to verify every run"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-verify",
                        summary={
                            "verdict": verdict,
                            "partial_success": True,
                            "state": "usage_error",
                            "events_verified": 0,
                        },
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    # ----- single-run path -----------------------------------------------
    if run_id and not verify_all:
        result = _verify_one_run(root, run_id)
        state = result["state"]

        if state == "unknown_run":
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "runs-verify",
                            summary={
                                "verdict": result["details"],
                                "partial_success": True,
                                "state": "unknown_run",
                                "events_verified": 0,
                                "first_tamper_at_seq": None,
                            },
                        )
                    )
                )
                ctx.exit(2)
            click.echo(f"VERDICT: {result['details']}")
            ctx.exit(2)

        events_verified = result["events_verified"]
        first_tamper = result["first_tamper_at_seq"]

        if state == "ok":
            verdict = (
                f"run {run_id} verified ({events_verified} event"
                f"{'s' if events_verified != 1 else ''}, all signatures match)"
            )
        elif state == "tampered":
            verdict = f"TAMPER DETECTED at seq={first_tamper}; chain breaks here"
        elif state == "unsigned":
            verdict = (
                f"run {run_id} has {events_verified} unsigned event"
                f"{'s' if events_verified != 1 else ''} (legacy/pre-signing — advisory only)"
            )
        elif state == "key_missing":
            verdict = f"ledger key missing or unreadable for {run_id}"
        else:
            verdict = f"unknown verify state: {state}"

        summary = {
            "verdict": verdict,
            "partial_success": bool(result.get("partial_success", False)),
            "state": state,
            "events_verified": events_verified,
            "first_tamper_at_seq": first_tamper,
            "run_id": run_id,
        }
        if result.get("final_signature"):
            summary["final_signature"] = result["final_signature"]

        facts = [
            f"run {run_id} has {events_verified} signed event{'s' if events_verified != 1 else ''}",
            f"chain integrity: {state}",
        ]
        if state == "tampered" and first_tamper is not None:
            facts.append(f"first tamper at seq={first_tamper}")
        next_commands = []
        if state == "ok":
            next_commands = [f"roam runs show {run_id}"]
        elif state == "tampered":
            next_commands = [f"roam runs show {run_id} to inspect events around seq={first_tamper}"]
        elif state == "unsigned":
            next_commands = [f"roam runs show {run_id} (chain cannot be verified)"]
        elif state == "key_missing":
            next_commands = ["roam runs start --agent <name>"]

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-verify",
                        summary=summary,
                        budget=token_budget,
                        agent_contract={
                            "facts": facts,
                            "next_commands": next_commands,
                        },
                        details=result.get("details", ""),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"  run_id:           {run_id}")
            click.echo(f"  state:            {state}")
            click.echo(f"  events_verified:  {events_verified}")
            if first_tamper is not None:
                click.echo(f"  first_tamper:     seq={first_tamper}")
            if result.get("final_signature"):
                click.echo(f"  final_signature:  {result['final_signature']}")

        if state == "tampered":
            ctx.exit(5)
        return

    # ----- --all path -----------------------------------------------------
    all_runs = list(list_runs(root))
    if not all_runs:
        verdict = "no runs to verify"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "runs-verify",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_runs",
                            "events_verified": 0,
                            "runs_verified": 0,
                            "runs_tampered": 0,
                            "runs_unsigned": 0,
                        },
                        budget=token_budget,
                        runs=[],
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    results = [_verify_one_run(root, m.run_id) for m in all_runs]
    total = len(results)
    by_state: dict[str, int] = {}
    total_events = 0
    for r in results:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        total_events += int(r.get("events_verified", 0) or 0)
    tampered = by_state.get("tampered", 0)
    unsigned = by_state.get("unsigned", 0)
    ok = by_state.get("ok", 0)
    key_missing = by_state.get("key_missing", 0)

    if tampered:
        state = "tampered"
        verdict = f"TAMPER DETECTED in {tampered}/{total} run{'s' if total != 1 else ''}"
    elif key_missing:
        state = "key_missing"
        verdict = f"ledger key missing for {key_missing}/{total} run(s)"
    elif unsigned:
        state = "unsigned" if ok == 0 else "ok"
        verdict = f"verified {total} run(s): {ok} ok, {unsigned} unsigned (legacy)"
    else:
        state = "ok"
        verdict = f"verified {total} run(s), all signatures match"

    partial_success = bool(tampered or unsigned or key_missing)

    summary = {
        "verdict": verdict,
        "partial_success": partial_success,
        "state": state,
        "events_verified": total_events,
        "runs_verified": total,
        "runs_ok": ok,
        "runs_tampered": tampered,
        "runs_unsigned": unsigned,
        "runs_key_missing": key_missing,
    }

    facts = [
        f"{total} run{'s' if total != 1 else ''} scanned",
        f"{ok} ok, {tampered} tampered, {unsigned} unsigned, {key_missing} key_missing",
    ]
    next_commands = []
    if tampered:
        first_bad = next((r for r in results if r["state"] == "tampered"), None)
        if first_bad is not None:
            next_commands.append(f"roam runs verify {first_bad['run_id']} to see the broken chain")
    elif state == "ok":
        next_commands.append("roam runs list to inspect run metadata")
    elif state == "unsigned":
        # W1091: populate next_commands on every state branch (LAW 4)
        next_commands.append("roam runs list --detail")
    elif state == "key_missing":
        # W1091: populate next_commands on every state branch (LAW 4)
        next_commands.append("roam runs start --agent <name>")

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "runs-verify",
                    summary=summary,
                    budget=token_budget,
                    runs=results,
                    agent_contract={
                        "facts": facts,
                        "next_commands": next_commands,
                    },
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  total_runs:     {total}")
        click.echo(f"  ok:             {ok}")
        click.echo(f"  tampered:       {tampered}")
        click.echo(f"  unsigned:       {unsigned}")
        click.echo(f"  key_missing:    {key_missing}")
        rows = []
        for r in results:
            rows.append(
                [
                    r["run_id"],
                    r["state"],
                    str(r.get("events_verified", 0) or 0),
                    str(r.get("first_tamper_at_seq", "")) or "-",
                ]
            )
        if rows:
            click.echo("")
            click.echo(format_table(["Run", "State", "Events", "TamperSeq"], rows))

    if tampered:
        ctx.exit(5)


# Suppress "unused import" warnings — referenced from the verify wiring.
_ = ledger_key_path
