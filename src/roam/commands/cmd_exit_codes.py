"""``roam exit-codes`` — list every exit code roam may return.

replaces grepping the docs. Reads ``roam.exit_codes`` and
emits a table that CI scripts and agents can use to branch.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because exit-codes outputs are invocation-scoped exit-code
catalog entries — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.output.formatter import format_catalog_output

_DESCRIPTIONS = {
    "EXIT_SUCCESS": "Command completed normally.",
    "EXIT_ERROR": "Generic failure (unhandled exception, missing file, etc.).",
    "EXIT_USAGE": "Bad arguments or flags. Check `--help`.",
    "EXIT_INDEX_MISSING": "No `.roam/index.db` found. Run `roam init` or `roam index`.",
    "EXIT_INDEX_STALE": "Index doesn't match the working tree. Run `roam index`.",
    "EXIT_GATE_FAILURE": "Quality gate failed (`--gate`, `--ci`, `--threshold`).",
    "EXIT_PARTIAL": "Command completed with warnings or skipped sections.",
}


@roam_capability(
    name="exit-codes",
    category="getting-started",
    summary="List every roam exit code with its meaning",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
    ai_safe=True,
    requires_index=False,
)
@click.command(name="exit-codes")
@click.pass_context
def exit_codes(ctx) -> None:
    """List every roam exit code with its meaning."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    from roam import exit_codes as ec

    rows = []
    for attr in dir(ec):
        if not attr.startswith("EXIT_"):
            continue
        val = getattr(ec, attr)
        if not isinstance(val, int):
            continue
        rows.append(
            {
                "name": attr,
                "code": int(val),
                "description": _DESCRIPTIONS.get(attr, ""),
            }
        )
    rows.sort(key=lambda r: r["code"])

    verdict = f"{len(rows)} exit code(s) defined"
    text_lines = [
        f"{'Code':>4}  {'Name':<24}  Description",
        f"{'-' * 4}  {'-' * 24}  {'-' * 50}",
    ]
    for r in rows:
        text_lines.append(f"{r['code']:>4}  {r['name']:<24}  {r['description']}")

    click.echo(
        format_catalog_output(
            json_mode,
            "exit-codes",
            verdict,
            rows,
            "exit_codes",
            text_lines,
        )
    )
