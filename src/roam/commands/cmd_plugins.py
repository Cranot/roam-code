"""``roam plugins`` — inspect discovered roam plugins.

Surfaces the plugin substrate (commands, detectors, language
extractors, framework detectors, bridges, extension/grammar aliases)
so plugin authors can verify their ``roam-plugin-*`` package is being
picked up. Without this, the only feedback was a silent failure path
— discovery succeeded but nothing told you so.

Subcommands::

    roam plugins              # default: behaves like ``list``
    roam plugins list         # list discovered plugins + contributions
    roam plugins info <name>  # detail on a single plugin
    roam plugins doctor       # check for failed loads (CI-friendly exit)
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.plugins import (
    discover_plugins,
    get_plugin_bridges,
    get_plugin_commands,
    get_plugin_detectors,
    get_plugin_errors,
    get_plugin_framework_detectors,
    get_plugin_language_extensions,
    get_plugin_language_extractors,
    get_plugin_language_grammar_aliases,
    get_plugins,
)


def _collect_state() -> dict:
    """Build the shared state dict consumed by every subcommand."""
    discover_plugins()
    cmds = get_plugin_commands()
    dets = get_plugin_detectors()
    langs = get_plugin_language_extractors()
    exts = get_plugin_language_extensions()
    aliases = get_plugin_language_grammar_aliases()
    fw_detectors = get_plugin_framework_detectors()
    bridges = get_plugin_bridges()
    errors = get_plugin_errors()
    plugins = get_plugins()

    counts = {
        "plugins": len(plugins),
        "commands": len(cmds),
        "detectors": len(dets),
        "languages": len(langs),
        "extensions": len(exts),
        "grammar_aliases": len(aliases),
        "framework_detectors": len(fw_detectors),
        "bridges": len(bridges),
        "errors": len(errors),
    }
    return {
        "counts": counts,
        "plugins": plugins,
        "commands": cmds,
        "detectors": dets,
        "languages": langs,
        "extensions": exts,
        "aliases": aliases,
        "framework_detectors": fw_detectors,
        "bridges": bridges,
        "errors": errors,
    }


def _plugin_to_dict(p) -> dict:
    return {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "source": p.source,
        "capabilities": list(p.capabilities),
    }


def _render_list_text(state: dict, verdict: str) -> str:
    lines: list[str] = []
    lines.append(f"VERDICT: {verdict}")
    lines.append("")

    plugins = state["plugins"]
    if plugins:
        lines.append("Plugins:")
        for p in plugins:
            caps = ",".join(p.capabilities) or "—"
            lines.append(f"  {p.name} (v{p.version}) [{caps}] — {p.source}")

    cmds = state["commands"]
    if cmds:
        lines.append("Commands:")
        for name in sorted(cmds):
            lines.append(f"  {name}")

    dets = state["detectors"]
    if dets:
        lines.append("Detectors:")
        for task, way, _fn in dets:
            lines.append(f"  {task}/{way}")

    langs = state["languages"]
    if langs:
        lines.append("Languages:")
        for lang in sorted(langs):
            ext_str = ", ".join(e for e, l in state["extensions"].items() if l == lang) or "—"
            lines.append(f"  {lang} (extensions: {ext_str})")

    if state["framework_detectors"]:
        lines.append("Framework detectors:")
        lines.append(f"  {len(state['framework_detectors'])} registered")

    if state["bridges"]:
        lines.append("Bridges:")
        for b in state["bridges"]:
            lines.append(f"  {b.name}")

    env_val = os.environ.get("ROAM_PLUGIN_MODULES", "")
    if env_val:
        lines.append("")
        lines.append(f"ROAM_PLUGIN_MODULES={env_val}")

    errors = state["errors"]
    if errors:
        lines.append("")
        lines.append("Discovery errors:")
        for e in errors:
            lines.append(f"  {e}")

    nothing = not (plugins or cmds or dets or langs or state["framework_detectors"] or state["bridges"])
    if nothing:
        lines.append("")
        lines.append("To register a plugin:")
        lines.append("  ROAM_PLUGIN_MODULES=mypkg.roam_plugin roam plugins")
        lines.append("  or define entry-point group 'roam.plugins' in your package.")

    return "\n".join(lines)


def _list_envelope(state: dict, verdict: str) -> dict:
    return json_envelope(
        "plugins",
        summary={"verdict": verdict, **state["counts"]},
        plugins=[_plugin_to_dict(p) for p in state["plugins"]],
        commands=sorted(state["commands"].keys()),
        detectors=[f"{t}/{w}" for t, w, _ in state["detectors"]],
        languages=sorted(state["languages"].keys()),
        extensions=state["extensions"],
        grammar_aliases=state["aliases"],
        framework_detectors=len(state["framework_detectors"]),
        bridges=[getattr(b, "name", str(b)) for b in state["bridges"]],
        errors=state["errors"],
        env_var="ROAM_PLUGIN_MODULES",
        env_value=os.environ.get("ROAM_PLUGIN_MODULES", ""),
    )


def _emit_list(ctx: click.Context) -> None:
    json_mode = ctx.obj.get("json") if ctx.obj else False
    state = _collect_state()
    total = sum(state["counts"][k] for k in ("commands", "detectors", "languages"))
    verdict = (
        f"{state['counts']['plugins']} plugin(s) discovered with "
        f"{total} contribution(s)"
        if state["plugins"] or total
        else "no plugins discovered"
    )
    if json_mode:
        click.echo(to_json(_list_envelope(state, verdict)))
        return
    click.echo(_render_list_text(state, verdict))


@roam_capability(
    name="plugins",
    category="agent-os",
    summary="List, inspect, and diagnose discovered roam plugins",
    maturity="experimental",
    mcp_expose=False,
    mcp_preset=(),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.group(invoke_without_command=True)
@click.pass_context
def plugins_cmd(ctx: click.Context) -> None:
    """Inspect roam plugins discovered via entry points / ROAM_PLUGIN_MODULES."""
    if ctx.invoked_subcommand is None:
        _emit_list(ctx)


@plugins_cmd.command("list")
@click.pass_context
def plugins_list(ctx: click.Context) -> None:
    """List discovered plugins and their contributions."""
    _emit_list(ctx)


@plugins_cmd.command("info")
@click.argument("name")
@click.pass_context
def plugins_info(ctx: click.Context, name: str) -> None:
    """Show details for a single plugin."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    state = _collect_state()
    target = next((p for p in state["plugins"] if p.name == name), None)
    if target is None:
        verdict = f"plugin '{name}' not found"
        envelope = json_envelope(
            "plugins.info",
            summary={"verdict": verdict, "found": False, "name": name},
            plugin=None,
            available=[p.name for p in state["plugins"]],
        )
        if json_mode:
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            if state["plugins"]:
                click.echo("")
                click.echo("Available:")
                for p in state["plugins"]:
                    click.echo(f"  {p.name}")
        ctx.exit(2)

    # Filter contributions attributed to this plugin's source label.
    src = target.source
    matched_commands = sorted(
        cmd for cmd, (_mod, _attr) in state["commands"].items()
    ) if src else []  # Per-plugin attribution lives in capabilities[]

    verdict = f"plugin '{target.name}' v{target.version}"
    envelope = json_envelope(
        "plugins.info",
        summary={"verdict": verdict, "found": True, "name": target.name, "version": target.version},
        plugin=_plugin_to_dict(target),
        commands=matched_commands,
    )
    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    click.echo(f"Name:         {target.name}")
    click.echo(f"Version:      {target.version}")
    click.echo(f"Source:       {target.source}")
    click.echo(f"Capabilities: {', '.join(target.capabilities) or '—'}")
    if target.description:
        click.echo(f"Description:  {target.description}")


@plugins_cmd.command("doctor")
@click.pass_context
def plugins_doctor(ctx: click.Context) -> None:
    """Check for plugin discovery failures.

    Exits non-zero when any plugin failed to load — designed to be
    wired into CI to catch plugin breakage on roam upgrades.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    state = _collect_state()
    errors = state["errors"]
    ok = not errors
    verdict = (
        "all plugins loaded cleanly"
        if ok
        else f"{len(errors)} plugin load failure(s) detected"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "plugins.doctor",
                    summary={
                        "verdict": verdict,
                        "ok": ok,
                        "error_count": len(errors),
                        "plugin_count": len(state["plugins"]),
                    },
                    errors=errors,
                    plugins=[_plugin_to_dict(p) for p in state["plugins"]],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        if errors:
            click.echo("")
            click.echo("Errors:")
            for e in errors:
                click.echo(f"  {e}")
        else:
            click.echo("")
            click.echo(f"Loaded {len(state['plugins'])} plugin(s).")

    if not ok:
        ctx.exit(5)
