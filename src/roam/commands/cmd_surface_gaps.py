"""roam surface-gaps — reconcile CLI, MCP, and documentation surfaces.

This command displaces manual registry greps by comparing the authoritative
CLI and MCP source registries with the generated ``docs/COMMANDS.md`` index.
It is deterministic and does not require a project index.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_COMMAND_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|")
_IGNORED_MATURITY = {"internal", "deprecated"}
_RESOLUTION_ERRORS = (OSError, RuntimeError, SyntaxError, KeyError, TypeError, ValueError)


def _resolve_implementation_commands() -> tuple[set[str] | None, str | None]:
    """Return canonical, public CLI commands from the source registry."""
    try:
        from roam.commands.cmd_surface import _MATURITY
        from roam.surface_counts import canonical_cli_commands

        commands = {name for name in canonical_cli_commands() if _MATURITY.get(name, "stable") not in _IGNORED_MATURITY}
    except _RESOLUTION_ERRORS as exc:
        return None, f"implementation layer unavailable: {exc.__class__.__name__}: {exc}"
    return commands, None


def _resolve_mcp_commands(
    implementation_commands: set[str],
) -> tuple[set[str] | None, str | None]:
    """Map declared MCP tools back to canonical CLI command names."""
    try:
        from roam.surface_counts import mcp_candidate_tool_names, mcp_tool_names

        tool_names = set(mcp_tool_names())
        commands = {name for name in implementation_commands if mcp_candidate_tool_names(name) & tool_names}
    except _RESOLUTION_ERRORS as exc:
        return None, f"MCP exposure layer unavailable: {exc.__class__.__name__}: {exc}"
    return commands, None


def _parse_documented_commands(text: str) -> set[str]:
    """Parse public command rows from the generated command index."""
    commands: set[str] = set()
    rows_seen = 0
    for line in text.splitlines():
        match = _COMMAND_ROW.match(line)
        if match is None:
            continue
        rows_seen += 1
        name, maturity = match.groups()
        if maturity.strip().lower() not in _IGNORED_MATURITY:
            commands.add(name)
    if rows_seen == 0:
        raise ValueError("no command table rows found")
    return commands


def _resolve_documented_commands() -> tuple[set[str] | None, str | None]:
    """Return public commands named in the generated documentation index."""
    try:
        from roam.surface_counts import _repo_root

        doc_path = Path(_repo_root()) / "docs" / "COMMANDS.md"
        commands = _parse_documented_commands(doc_path.read_text(encoding="utf-8"))
    except _RESOLUTION_ERRORS as exc:
        return None, f"documentation layer unavailable: {exc.__class__.__name__}: {exc}"
    return commands, None


def _find_surface_gaps(
    implementation: set[str] | None,
    mcp_exposed: set[str] | None,
    documented: set[str] | None,
) -> list[dict[str, str]]:
    """Return one deterministic finding for every resolvable layer gap."""
    findings: list[dict[str, str]] = []

    if implementation is not None and mcp_exposed is not None:
        findings.extend(
            {
                "command": name,
                "gap": "implemented_not_mcp_exposed",
                "message": "implemented, not MCP-exposed",
            }
            for name in sorted(implementation - mcp_exposed)
        )

    if implementation is not None and documented is not None:
        findings.extend(
            {
                "command": name,
                "gap": "undocumented_command",
                "message": "undocumented command",
            }
            for name in sorted(implementation - documented)
        )
        findings.extend(
            {
                "command": name,
                "gap": "documented_not_implemented",
                "message": "documented but not implemented",
            }
            for name in sorted(documented - implementation)
        )

    return sorted(findings, key=lambda finding: (finding["command"], finding["gap"]))


@roam_capability(
    name="surface-gaps",
    category="getting-started",
    summary="Find gaps between CLI registration, MCP exposure, and command documentation.",
    outputs=["findings", "verdict"],
    tags=["diagnostics", "surface"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("surface-gaps")
@click.pass_context
def surface_gaps(ctx: click.Context) -> None:
    """Find gaps between CLI registration, MCP exposure, and documentation."""
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    implementation, implementation_warning = _resolve_implementation_commands()
    if implementation is None:
        mcp_exposed, mcp_warning = None, "MCP comparison skipped: implementation layer unavailable"
    else:
        mcp_exposed, mcp_warning = _resolve_mcp_commands(implementation)
    documented, documentation_warning = _resolve_documented_commands()

    warnings = [
        warning for warning in (implementation_warning, mcp_warning, documentation_warning) if warning is not None
    ]
    findings = _find_surface_gaps(implementation, mcp_exposed, documented)

    comparisons_run = []
    if implementation is not None and mcp_exposed is not None:
        comparisons_run.append("implementation:mcp_exposure")
    if implementation is not None and documented is not None:
        comparisons_run.extend(["implementation:documentation", "documentation:implementation"])

    verdict = f"{len(findings)} surface gaps" if findings else "No surface gaps"
    summary = {
        "verdict": verdict,
        "gap_count": len(findings),
        "comparison_count": len(comparisons_run),
        "partial_success": bool(warnings),
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "surface-gaps",
                    summary=summary,
                    findings=findings,
                    comparisons_run=comparisons_run,
                    layers={
                        "implementation": implementation is not None,
                        "mcp_exposure": mcp_exposed is not None,
                        "documentation": documented is not None,
                    },
                    warnings=warnings,
                    budget=token_budget,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    for finding in findings:
        click.echo(f"  [{finding['gap']}] {finding['command']} — {finding['message']}")
    for warning in warnings:
        click.echo(f"  SKIPPED: {warning}")
