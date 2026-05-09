"""roam explain-command — show what a command does, its dependencies, and its cost.

Reports: category, maturity, aliases, deprecation, MCP exposure, the
DB tables it touches (best-effort source-grep), graph passes, optional
extras, expected cost, stale-index sensitivity. Used by docs and by
agents deciding which command to invoke.
"""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

import click

from roam.commands.cmd_surface import _build_surface
from roam.output.formatter import json_envelope, to_json

# Heuristic stale-index sensitivity. Commands that read graph metrics or
# cluster IDs are highly sensitive; those that touch git or filesystem
# directly are usually fine on a stale index.
_STALE_SENSITIVE = {
    "high": {
        "health",
        "smells",
        "debt",
        "complexity",
        "layers",
        "clusters",
        "spectral",
        "coupling",
        "dark-matter",
        "fan",
        "impact",
        "preflight",
        "critique",
        "pr-risk",
        "pr-analyze",
        "affected-tests",
        "test-impact",
        "trace",
        "dead",
        "fitness",
        "forecast",
        "math",
        "algo",
        "n1",
        "missing-index",
        "hotspots",
    },
    "medium": {
        "context",
        "retrieve",
        "search",
        "search-semantic",
        "uses",
        "deps",
        "symbol",
        "module",
        "describe",
        "minimap",
    },
    "low": {
        "history-grep",
        "grep",
        "refs-text",
        "stale-refs",
        "doc-staleness",
        "ws",
        "schema",
        "stats",
        "telemetry",
        "doctor",
        "surface",
        "version",
        "config",
        "init",
        "index",
        "watch",
    },
}


def _stale_sensitivity(name: str) -> str:
    for level, names in _STALE_SENSITIVE.items():
        if name in names:
            return level
    return "unknown"


_DB_TABLE_PATTERN = re.compile(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)|\bINTO\s+(\w+)|\bUPDATE\s+(\w+)", re.IGNORECASE)


def _scan_module_for_tables(module_path: Path) -> list[str]:
    """Best-effort scan: find DB table names referenced in the module source."""
    try:
        src = module_path.read_text(encoding="utf-8")
    except Exception:
        return []
    tables: set[str] = set()
    for m in _DB_TABLE_PATTERN.finditer(src):
        for g in m.groups():
            if g and not g.startswith("_") and len(g) > 2:
                tables.add(g.lower())
    # Filter out SQL keywords accidentally captured.
    noise = {"with", "where", "as", "on", "if", "exists", "select", "table"}
    return sorted(t for t in tables if t not in noise)


def _scan_module_for_extras(module_path: Path) -> list[str]:
    """Detect optional-extras imports (network, ml, mcp)."""
    try:
        src = module_path.read_text(encoding="utf-8")
    except Exception:
        return []
    extras: list[str] = []
    if re.search(r"\bimport\s+networkx|from\s+networkx\b", src):
        extras.append("networkx")
    if re.search(r"\bimport\s+numpy|from\s+numpy\b", src):
        extras.append("numpy")
    if re.search(r"\bimport\s+scipy|from\s+scipy\b", src):
        extras.append("scipy")
    if re.search(r"\bimport\s+onnxruntime|from\s+onnxruntime\b", src):
        extras.append("onnxruntime (semantic search)")
    if re.search(r"\bimport\s+watchdog|from\s+watchdog\b", src):
        extras.append("watchdog (file watch)")
    if re.search(r"\bimport\s+fastmcp|from\s+fastmcp\b", src):
        extras.append("fastmcp (MCP server)")
    return extras


@click.command("explain-command")
@click.argument("name")
@click.pass_context
def explain_command(ctx, name: str):
    """Show what a command does, what it depends on, and how stale-index sensitive it is."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    surface_data = _build_surface()
    match = next((c for c in surface_data["commands"] if c["name"] == name), None)
    if not match:
        click.echo(f"ERROR: unknown command '{name}'.", err=True)
        click.echo("  hint: run 'roam surface' to list every canonical command name.", err=True)
        ctx.exit(2)

    module_name = match["module"]
    try:
        module = importlib.import_module(module_name)
        module_path = Path(inspect.getfile(module)) if hasattr(inspect, "getfile") else None
    except Exception as exc:
        module = None
        module_path = None
        load_error = repr(exc)
    else:
        load_error = None

    db_tables = _scan_module_for_tables(module_path) if module_path else []
    extras = _scan_module_for_extras(module_path) if module_path else []

    # Click help-text introspection (best effort).
    help_text = ""
    if module is not None:
        fn = getattr(module, match["function"], None)
        if fn is not None and hasattr(fn, "help"):
            help_text = (fn.help or "").strip()
        elif fn is not None and fn.__doc__:
            help_text = fn.__doc__.strip().splitlines()[0]

    sensitivity = _stale_sensitivity(name)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "explain-command",
                    summary={
                        "verdict": "OK" if not load_error else "DEGRADED",
                        "command": name,
                        "category": match["category"],
                        "maturity": match["maturity"],
                        "stale_sensitivity": sensitivity,
                    },
                    command_info={
                        "name": name,
                        "module": module_name,
                        "function": match["function"],
                        "category": match["category"],
                        "maturity": match["maturity"],
                        "aliases": match["aliases"],
                        "deprecated_replacement": match["deprecated_replacement"],
                        "mcp_exposed": match["mcp_exposed"],
                        "help": help_text,
                        "db_tables_referenced": db_tables,
                        "optional_extras": extras,
                        "stale_sensitivity": sensitivity,
                        "module_load_error": load_error,
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {'OK' if not load_error else 'DEGRADED'}  ({name})")
    click.echo("")
    click.echo(f"  category:           {match['category']}")
    click.echo(f"  maturity:           {match['maturity']}")
    click.echo(f"  module:             {module_name}")
    click.echo(f"  function:           {match['function']}")
    if match["aliases"]:
        click.echo(f"  aliases:            {', '.join(match['aliases'])}")
    if match["deprecated_replacement"]:
        click.echo(f"  deprecated -> {match['deprecated_replacement']}")
    click.echo(f"  mcp exposed:        {'yes' if match['mcp_exposed'] else 'no'}")
    click.echo(f"  stale sensitivity:  {sensitivity}")
    if db_tables:
        click.echo(f"  db tables:          {', '.join(db_tables)}")
    if extras:
        click.echo(f"  optional extras:    {', '.join(extras)}")
    if help_text:
        click.echo("")
        click.echo("  help:")
        for line in help_text.splitlines()[:8]:
            click.echo(f"    {line}")
    if load_error:
        click.echo("")
        click.echo(f"  load error:         {load_error}", err=True)
