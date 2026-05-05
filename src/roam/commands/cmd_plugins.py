"""``roam plugins`` — list discovered plugins (Pass 30, plugin SDK surfacing).

Surfaces the existing plugin system (commands, detectors, language
extractors, extension/grammar aliases) so authors can verify their
plugin is being picked up. Without this, the only feedback was a
silent failure path — the discovery succeeded but nothing told you so.
"""

from __future__ import annotations

import os

import click

from roam.output.formatter import json_envelope, to_json
from roam.plugins import (
    discover_plugins,
    get_plugin_commands,
    get_plugin_detectors,
    get_plugin_errors,
    get_plugin_language_extensions,
    get_plugin_language_extractors,
    get_plugin_language_grammar_aliases,
)


@click.command()
@click.pass_context
def plugins_cmd(ctx) -> None:
    """List discovered roam plugins (commands, detectors, language extractors)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    discover_plugins()
    cmds = get_plugin_commands()
    dets = get_plugin_detectors()
    langs = get_plugin_language_extractors()
    exts = get_plugin_language_extensions()
    aliases = get_plugin_language_grammar_aliases()
    errors = get_plugin_errors()

    counts = {
        "commands": len(cmds),
        "detectors": len(dets),
        "languages": len(langs),
        "extensions": len(exts),
        "grammar_aliases": len(aliases),
        "errors": len(errors),
    }
    total = sum(counts[k] for k in ("commands", "detectors", "languages"))
    verdict = f"{total} plugin contribution(s) discovered" if total else "no plugins discovered"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "plugins",
                    summary={"verdict": verdict, **counts},
                    commands=sorted(cmds.keys()),
                    detectors=[d.name for d in dets],
                    languages=sorted(langs.keys()),
                    extensions=exts,
                    grammar_aliases=aliases,
                    errors=errors,
                    env_var="ROAM_PLUGIN_MODULES",
                    env_value=os.environ.get("ROAM_PLUGIN_MODULES", ""),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if cmds:
        click.echo("Commands:")
        for name in sorted(cmds):
            click.echo(f"  {name}")
    if dets:
        click.echo("Detectors:")
        for d in dets:
            click.echo(f"  {d.name}")
    if langs:
        click.echo("Languages:")
        for lang in sorted(langs):
            ext_str = ", ".join(e for e, l in exts.items() if l == lang) or "—"
            click.echo(f"  {lang} (extensions: {ext_str})")
    env_val = os.environ.get("ROAM_PLUGIN_MODULES", "")
    if env_val:
        click.echo()
        click.echo(f"ROAM_PLUGIN_MODULES={env_val}")
    if errors:
        click.echo()
        click.echo("Discovery errors:")
        for e in errors:
            click.echo(f"  {e}")
    if not (cmds or dets or langs):
        click.echo()
        click.echo("To register a plugin:")
        click.echo("  ROAM_PLUGIN_MODULES=mypkg.roam_plugin roam plugins")
        click.echo("  or define entry-point group 'roam.plugins' in your package.")
