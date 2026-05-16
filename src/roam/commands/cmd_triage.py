"""Security finding triage workflow -- manage suppression of findings.

Subcommands:

- ``roam triage list``  -- show all current suppressions
- ``roam triage add``   -- add a new suppression
- ``roam triage stats`` -- show suppression statistics
- ``roam triage check`` -- check if a specific finding is suppressed

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because cmd_triage operates on substrate state in ``.roam/``
(suppression records, triage decisions) -- not code locations or
per-location violations. The state is consumed by other roam commands
+ agent runtimes directly from disk; SARIF would be redundant. See
action.yml _SUPPORTED_SARIF allowlist + W1189-audit reclassification
memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.suppression import (
    VALID_STATUSES,
    is_suppressed,
    load_suppressions,
    save_suppression,
    suppression_stats,
)
from roam.db.connection import find_project_root
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="triage",
    category="workflow",
    summary="Manage security finding suppressions",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "debug"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.group("triage")
@click.pass_context
def triage(ctx):
    """Manage security finding suppressions.

    Suppressions are stored in .roam-suppressions.yml at the project root.
    Each suppression marks a specific finding (rule + file + optional line)
    as reviewed with a reason and status.

    This is the only write path for suppression data.  Security commands
    like ``secrets``, ``auth-gaps``, ``watch``, and ``flag-dead`` read
    suppressions to filter their output.  Use ``check-rules`` to detect
    violations, then ``triage add`` to mark reviewed findings.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# triage list
# ---------------------------------------------------------------------------


@triage.command("list")
@click.pass_context
def triage_list(ctx):
    """Show all current suppressions from .roam-suppressions.yml."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    root = find_project_root()
    suppressions = load_suppressions(root)

    total = len(suppressions)
    if total == 0:
        verdict = "no suppressions"
    else:
        verdict = f"{total} suppression(s)"

    # --- JSON output ---
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "triage-list",
                    summary={"verdict": verdict, "total": total},
                    budget=token_budget,
                    suppressions=suppressions,
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if not suppressions:
        click.echo("  No suppressions found.")
        click.echo("  Use 'roam triage add' to suppress a finding.")
        return

    rows = []
    for sup in suppressions:
        loc = sup.get("file", "")
        line = sup.get("line")
        if line is not None:
            loc = f"{loc}:{line}"
        rows.append(
            [
                sup.get("rule", ""),
                loc,
                sup.get("status", ""),
                sup.get("reason", ""),
                sup.get("date", ""),
            ]
        )

    click.echo(format_table(["Rule", "Location", "Status", "Reason", "Date"], rows))


# ---------------------------------------------------------------------------
# triage add
# ---------------------------------------------------------------------------


@triage.command("add")
@click.option("--rule", required=True, help="Rule identifier (e.g. secret-detection).")
@click.option("--path", "file_path", default=None, help="File path (relative to project root).")
@click.option(
    "--file",
    "file_path",
    default=None,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option("--reason", required=True, help="Justification for suppression.")
@click.option(
    "--status",
    required=True,
    type=click.Choice(sorted(VALID_STATUSES)),
    help="Suppression status.",
)
@click.option("--line", "line_num", default=None, type=int, help="Optional line number.")
@click.option("--author", default=None, help="Author identifier (e.g. email).")
@click.pass_context
def triage_add(ctx, rule, file_path, reason, status, line_num, author):
    """Add a new suppression to .roam-suppressions.yml."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    if not file_path:
        raise click.UsageError("Missing option '--path' (file path relative to project root).")

    try:
        save_suppression(
            root,
            rule=rule,
            file=file_path,
            reason=reason,
            status=status,
            line=line_num,
            author=author,
        )
    except ValueError as exc:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "triage-add",
                        summary={"verdict": f"error: {exc}", "added": False},
                    )
                )
            )
        else:
            click.echo(f"ERROR: {exc}")
        ctx.exit(1)
        return

    loc = file_path
    if line_num is not None:
        loc = f"{file_path}:{line_num}"

    verdict = f"suppressed {rule} at {loc} ({status})"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "triage-add",
                    summary={"verdict": verdict, "added": True},
                    suppression={
                        "rule": rule,
                        "file": file_path,
                        "line": line_num,
                        "reason": reason,
                        "status": status,
                        "author": author,
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo()
    click.echo(f"  Rule:   {rule}")
    click.echo(f"  File:   {loc}")
    click.echo(f"  Status: {status}")
    click.echo(f"  Reason: {reason}")
    if author:
        click.echo(f"  Author: {author}")


# ---------------------------------------------------------------------------
# triage stats
# ---------------------------------------------------------------------------


@triage.command("stats")
@click.pass_context
def triage_stats(ctx):
    """Show suppression statistics (count by status, rule, file)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    root = find_project_root()
    suppressions = load_suppressions(root)
    stats = suppression_stats(suppressions)

    total = stats["total"]
    if total == 0:
        verdict = "no suppressions"
    else:
        parts = []
        for st, count in sorted(stats["by_status"].items()):
            parts.append(f"{count} {st}")
        verdict = f"{total} suppression(s): {', '.join(parts)}"

    # --- JSON output ---
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "triage-stats",
                    summary={"verdict": verdict, "total": total},
                    budget=token_budget,
                    by_status=stats["by_status"],
                    by_rule=stats["by_rule"],
                    by_file=stats["by_file"],
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if total == 0:
        click.echo("  No suppressions found.")
        return

    # By status
    click.echo("  By status:")
    for st, count in sorted(stats["by_status"].items()):
        click.echo(f"    {st}: {count}")
    click.echo()

    # By rule
    click.echo("  By rule:")
    for rl, count in sorted(stats["by_rule"].items(), key=lambda x: -x[1]):
        click.echo(f"    {rl}: {count}")
    click.echo()

    # By file (top 10)
    click.echo("  By file:")
    file_items = sorted(stats["by_file"].items(), key=lambda x: -x[1])
    for fl, count in file_items[:10]:
        click.echo(f"    {fl}: {count}")
    if len(file_items) > 10:
        click.echo(f"    (+{len(file_items) - 10} more)")


# ---------------------------------------------------------------------------
# triage check
# ---------------------------------------------------------------------------


@triage.command("check")
@click.argument("rule")
@click.argument("file_path")
@click.option("--line", "line_num", default=None, type=int, help="Optional line number.")
@click.pass_context
def triage_check(ctx, rule, file_path, line_num):
    """Check if a specific finding is suppressed.

    Arguments: RULE FILE_PATH
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    suppressions = load_suppressions(root)

    suppressed = is_suppressed(suppressions, rule, file_path, line=line_num)

    loc = file_path
    if line_num is not None:
        loc = f"{file_path}:{line_num}"

    if suppressed:
        verdict = f"suppressed: {rule} at {loc}"
        # Find the matching suppression for detail output
        matching = None
        norm_file = file_path.replace("\\", "/")
        for sup in suppressions:
            sup_file = sup.get("file", "").replace("\\", "/")
            if sup.get("rule") == rule and sup_file == norm_file:
                sup_line = sup.get("line")
                if sup_line is not None and line_num is not None:
                    if int(sup_line) != int(line_num):
                        continue
                matching = sup
                break
    else:
        verdict = f"not suppressed: {rule} at {loc}"
        matching = None

    # --- JSON output ---
    if json_mode:
        result: dict = {
            "rule": rule,
            "file": file_path,
            "line": line_num,
            "suppressed": suppressed,
        }
        if matching:
            result["suppression"] = matching
        click.echo(
            to_json(
                json_envelope(
                    "triage-check",
                    summary={"verdict": verdict, "suppressed": suppressed},
                    result=result,
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if suppressed and matching:
        click.echo(f"  Status: {matching.get('status', 'unknown')}")
        click.echo(f"  Reason: {matching.get('reason', 'none')}")
        if matching.get("author"):
            click.echo(f"  Author: {matching['author']}")
        if matching.get("date"):
            click.echo(f"  Date:   {matching['date']}")
    elif not suppressed:
        click.echo(f"  Finding {rule} at {loc} is NOT suppressed.")
        click.echo("  Use 'roam triage add' to suppress it.")
