"""Repo-local agent constitution CLI (R24 capstone).

Five subcommands::

    roam constitution init   [--with-laws/--no-laws] [--with-rules/--no-rules] [--force]
    roam constitution check
    roam constitution show   [--json]
    roam constitution apply  --gate before_edit|after_edit|before_pr [--strict] [--symbol X] [--file Y]
    roam constitution where

The constitution is the *single declarative file* an agent reads first
when it joins a repo. It points at every other agent-OS substrate the
repo has (``AGENTS.md``, ``roam-laws.yml``, ``.roam/rules/``,
``.roam/memory.jsonl``) plus the required checks each workflow gate
must run and the policy thresholds that govern blast radius / cycles /
test coverage.

This command CONSUMES those substrates -- it does not extend them. The
laws / rules / memory / runs / pr-bundle commands continue to own their
own subsystems.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam constitution`` operates on substrate state in
``.roam/`` (policy envelopes over the constitution capstone) — not code
locations or per-location violations. The state is consumed by other
roam commands + agent runtimes directly from disk; SARIF would be
redundant. See action.yml _SUPPORTED_SARIF allowlist + W1181-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.constitution.loader import (
    VALID_GATES,
    apply_constitution,
    check_constitution,
    constitution_path,
    init_constitution,
    load_constitution,
)
from roam.db.connection import find_project_root
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@roam_capability(
    name="constitution",
    category="setup",
    summary="Single declarative file unifying AGENTS.md, laws, rules, memory + gate checks.",
    inputs=[],
    outputs=["constitution"],
    examples=[
        "roam constitution init",
        "roam constitution check",
        "roam constitution show --json",
        "roam constitution apply --gate before_edit --symbol useThemeClasses",
        "roam constitution where",
    ],
    tags=["constitution", "agent-os", "policy"],
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
@click.group("constitution")
@click.pass_context
def constitution_group(ctx):
    """Repo-local agent constitution -- capstone for agent-OS substrates.

    Unifies ``AGENTS.md`` (human instructions), ``roam-laws.yml`` (mined
    laws), ``.roam/rules/*.yml`` (graph-aware rules), and
    ``.roam/memory.jsonl`` (portable agent memory) behind a single
    declarative file at ``.roam/constitution.yml``. Adds the required
    checks (``preflight``, ``critique``, ``pr-bundle validate``) and
    policy thresholds an agent must observe.

    Run ``roam constitution init`` once per repo; agents read it first.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@constitution_group.command("init")
@click.option(
    "--with-laws/--no-laws",
    "with_laws",
    default=True,
    show_default=True,
    help="Include the laws source pointer if a laws file exists.",
)
@click.option(
    "--with-rules/--no-rules",
    "with_rules",
    default=True,
    show_default=True,
    help="Include the rules source pointer if a rules directory exists.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing constitution.yml.",
)
@click.pass_context
def constitution_init(ctx, with_laws, with_rules, force):
    """Generate ``.roam/constitution.yml`` from the current repo state.

    Auto-detects every supporting file. Absent files yield an absent
    ``sources`` key (NOT a stub path) -- so ``constitution check`` does
    not flag what was never there.

    Idempotent: re-running without ``--force`` is a no-op and emits a
    ``state: "already_initialized"`` envelope.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    path = constitution_path(root)

    if path.exists() and not force:
        verdict = f"constitution already exists at {path.relative_to(root)} -- pass --force to overwrite"
        env = json_envelope(
            "constitution-init",
            summary={
                "verdict": verdict,
                "partial_success": True,
                "state": "already_initialized",
                "created": False,
            },
            budget=token_budget,
            path=str(path),
        )
        if json_mode:
            click.echo(to_json(env))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"  path: {path}")
            click.echo("Hint: pass --force to regenerate.")
        try:
            auto_log(env, action="constitution-init", target=str(path), repo_root=root)
        except Exception:
            pass
        return

    try:
        written = init_constitution(root, with_laws=with_laws, with_rules=with_rules, force=force)
    except Exception as exc:
        verdict = f"failed to write constitution: {exc}"
        env = json_envelope(
            "constitution-init",
            summary={
                "verdict": verdict,
                "partial_success": True,
                "state": "error",
                "created": False,
            },
            budget=token_budget,
        )
        if json_mode:
            click.echo(to_json(env))
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return

    # Reload from disk to surface exactly what was written.
    constitution = load_constitution(root)
    sources = constitution.sources if constitution else {}
    n_sources = len(sources)
    n_gates = len(constitution.required_checks) if constitution else 0

    verdict = f"Constitution initialized ({n_sources} source(s) detected, {n_gates} required-checks gate(s) configured)"

    env = json_envelope(
        "constitution-init",
        summary={
            "verdict": verdict,
            "partial_success": False,
            "state": "initialized",
            "created": True,
            "source_count": n_sources,
            "gate_count": n_gates,
        },
        budget=token_budget,
        path=str(written),
        sources=sources,
        gates=list(constitution.required_checks.keys()) if constitution else [],
    )

    if json_mode:
        click.echo(to_json(env))
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(f"  path:     {written}")
        if sources:
            click.echo("  sources:")
            for name, p in sources.items():
                click.echo(f"    {name}: {p}")
        else:
            click.echo("  sources: (none detected -- add AGENTS.md / roam-laws.yml / .roam/memory.jsonl)")
        if constitution and constitution.required_checks:
            click.echo("  gates:")
            for gate, items in constitution.required_checks.items():
                click.echo(f"    {gate}: {len(items)} check(s)")

    try:
        auto_log(env, action="constitution-init", target=str(written), repo_root=root)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@constitution_group.command("check")
@click.pass_context
def constitution_check(ctx):
    """Verify every declared source exists and every required-check resolves.

    Emits a structured per-source / per-command status table. Use this
    on CI as a low-cost smoke test that the constitution still points
    at real files and known commands.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    constitution = load_constitution(root)

    if constitution is None:
        verdict = "no constitution -- run `roam constitution init` first"
        env = json_envelope(
            "constitution-check",
            summary={
                "verdict": verdict,
                "partial_success": True,
                "state": "not_initialized",
                "ok": False,
            },
            budget=token_budget,
            path=str(constitution_path(root)),
            # W20.6 error-msg consistency
            agent_contract={
                "facts": ["no .roam/constitution.yml in this repo"],
                "next_commands": ["roam constitution init"],
            },
        )
        if json_mode:
            click.echo(to_json(env))
        else:
            click.echo(f"VERDICT: {verdict}")
        try:
            auto_log(env, action="constitution-check", repo_root=root)
        except Exception:
            pass
        return

    report = check_constitution(root, constitution)

    env = json_envelope(
        "constitution-check",
        summary={
            "verdict": report.summary_verdict,
            "partial_success": not report.ok,
            "state": report.state,
            "ok": report.ok,
            "source_total": len(report.sources),
            "command_total": len(report.commands),
            "mode_issue_total": len(report.mode_issues),
        },
        budget=token_budget,
        sources=[s.to_dict() for s in report.sources],
        commands=[c.to_dict() for c in report.commands],
        mode_issues=report.mode_issues,
        path=str(constitution._path) if constitution._path else "",
    )

    if json_mode:
        click.echo(to_json(env))
    else:
        click.echo(f"VERDICT: {report.summary_verdict}")
        if report.sources:
            rows = [[s.name, s.path, s.state, s.detail] for s in report.sources]
            click.echo("")
            click.echo("Sources:")
            click.echo(format_table(["Name", "Path", "State", "Detail"], rows))
        if report.commands:
            rows = [[c.gate, c.command, c.state] for c in report.commands]
            click.echo("")
            click.echo("Required checks:")
            click.echo(format_table(["Gate", "Command", "State"], rows))
        if report.mode_issues:
            click.echo("")
            click.echo("Mode allow-list issues:")
            rows = [[m["mode"], m["command"], m["state"]] for m in report.mode_issues]
            click.echo(format_table(["Mode", "Command", "State"], rows))

    try:
        auto_log(env, action="constitution-check", repo_root=root)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@constitution_group.command("show")
@click.pass_context
def constitution_show(ctx):
    """Render the currently-loaded constitution (text or JSON).

    Text mode prints each section in a stable order. JSON mode emits
    the constitution dict inside a standard envelope so downstream
    tools can consume it programmatically.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    constitution = load_constitution(root)

    if constitution is None:
        verdict = "no constitution -- run `roam constitution init` first"
        env = json_envelope(
            "constitution-show",
            summary={
                "verdict": verdict,
                "partial_success": True,
                "state": "not_initialized",
            },
            budget=token_budget,
            path=str(constitution_path(root)),
            # W20.6 error-msg consistency
            agent_contract={
                "facts": ["no .roam/constitution.yml in this repo"],
                "next_commands": ["roam constitution init"],
            },
        )
        if json_mode:
            click.echo(to_json(env))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    n_sources = len(constitution.sources)
    n_gates = len(constitution.required_checks)
    n_modes = len(constitution.modes)
    verdict = f"constitution v{constitution.version} with {n_sources} source(s), {n_gates} gate(s), {n_modes} mode(s)"

    env = json_envelope(
        "constitution-show",
        summary={
            "verdict": verdict,
            "partial_success": False,
            "state": "ok",
            "source_count": n_sources,
            "gate_count": n_gates,
            "mode_count": n_modes,
        },
        budget=token_budget,
        constitution=constitution.to_dict(),
        path=str(constitution._path) if constitution._path else "",
    )

    if json_mode:
        click.echo(to_json(env))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  path:    {constitution._path}")
    md = constitution.metadata or {}
    if md:
        name = md.get("name", "")
        desc = md.get("description", "")
        gen_at = md.get("generated_at", "")
        click.echo("")
        click.echo("Metadata:")
        if name:
            click.echo(f"  name:         {name}")
        if desc:
            click.echo(f"  description:  {desc}")
        if gen_at:
            click.echo(f"  generated_at: {gen_at}")
    if constitution.sources:
        click.echo("")
        click.echo("Sources:")
        for k, v in constitution.sources.items():
            click.echo(f"  {k}: {v}")
    if constitution.required_checks:
        click.echo("")
        click.echo("Required checks:")
        for gate, items in constitution.required_checks.items():
            click.echo(f"  {gate}:")
            for it in items:
                click.echo(f"    - {it}")
    if constitution.modes:
        click.echo("")
        click.echo("Modes:")
        for mode, items in constitution.modes.items():
            click.echo(f"  {mode}: {len(items)} command(s)")
    if constitution.policy:
        click.echo("")
        click.echo("Policy:")
        for k, v in constitution.policy.items():
            click.echo(f"  {k}: {v}")
    if constitution.metadata_signals:
        click.echo("")
        click.echo("Metadata signals:")
        for k, v in constitution.metadata_signals.items():
            click.echo(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@constitution_group.command("apply")
@click.option(
    "--gate",
    type=click.Choice(VALID_GATES),
    required=True,
    help="Gate to run: before_edit | after_edit | before_pr.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit 5 if ANY check exits non-zero (for CI integration).",
)
@click.option(
    "--symbol",
    default=None,
    help="Fill the ${symbol} placeholder in required-check templates.",
)
@click.option(
    "--file",
    "file_var",
    default=None,
    help="Fill the ${file} placeholder in required-check templates.",
)
@click.option(
    "--timeout",
    default=120,
    type=int,
    show_default=True,
    help="Per-check subprocess timeout (seconds).",
)
@click.pass_context
def constitution_apply(ctx, gate, strict, symbol, file_var, timeout):
    """Run the required-check commands for one gate.

    Substitutes ``${symbol}`` / ``${file}`` placeholders from the
    options. Checks with unresolved placeholders are SKIPPED with a
    recorded reason -- never invoked with the literal token.

    Aggregates the per-check results into a single verdict. With
    ``--strict``, exits 5 on ANY failure -- suitable for CI gates.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    constitution = load_constitution(root)

    if constitution is None:
        verdict = "no constitution -- run `roam constitution init` first"
        env = json_envelope(
            "constitution-apply",
            summary={
                "verdict": verdict,
                "partial_success": True,
                "state": "not_initialized",
                "gate": gate,
            },
            budget=token_budget,
            # W20.6 error-msg consistency
            agent_contract={
                "facts": ["no .roam/constitution.yml in this repo"],
                "next_commands": ["roam constitution init"],
            },
        )
        if json_mode:
            click.echo(to_json(env))
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return

    variables: dict[str, str] = {}
    if symbol:
        variables["symbol"] = symbol
    if file_var:
        variables["file"] = file_var

    report = apply_constitution(
        root,
        constitution,
        gate=gate,
        variables=variables,
        timeout=timeout,
    )

    partial_success = report.state in ("partial", "failed")

    env = json_envelope(
        "constitution-apply",
        summary={
            "verdict": report.summary_verdict,
            "partial_success": partial_success,
            "state": report.state,
            "gate": gate,
            "passed": report.passed_count,
            "failed": report.failed_count,
            "total": len(report.results),
        },
        budget=token_budget,
        results=[r.to_dict() for r in report.results],
    )

    if json_mode:
        click.echo(to_json(env))
    else:
        click.echo(f"VERDICT: {report.summary_verdict}")
        if report.results:
            rows = []
            for r in report.results:
                status = "skip" if r.skipped else ("pass" if r.passed else "fail")
                detail = r.skip_reason if r.skipped else (r.verdict[:60] if r.verdict else "")
                rows.append([r.command, str(r.exit_code), status, detail])
            click.echo("")
            click.echo(format_table(["Command", "Exit", "Status", "Verdict"], rows))

    try:
        auto_log(env, action="constitution-apply", target=gate, repo_root=root)
    except Exception:
        pass

    if strict and report.any_failed:
        ctx.exit(5)


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------


@constitution_group.command("where")
@click.pass_context
def constitution_where(ctx):
    """Print the canonical constitution path."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    root = find_project_root()
    path = constitution_path(root)
    exists = path.exists()

    if exists:
        verdict = str(path)
        state = "ok"
    else:
        verdict = f"{path} (does not exist -- run `roam constitution init`)"
        state = "not_initialized"

    env = json_envelope(
        "constitution-where",
        summary={
            "verdict": verdict,
            "partial_success": not exists,
            "state": state,
            "exists": exists,
        },
        budget=token_budget,
        path=str(path),
    )

    if json_mode:
        click.echo(to_json(env))
    else:
        click.echo(str(path))
