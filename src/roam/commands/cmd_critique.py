"""roam critique — graph-grounded patch verifier (A.2).

Reads a unified diff (stdin) and runs roam-grounded checks against it:

    git diff | roam critique
    git diff main..HEAD | roam critique --json

The killer signal is *clones-not-edited*: for every changed symbol that
has a persisted clone sibling (see ``roam clones --persist``) outside the
diff, we flag the sibling as a likely missed change. v12.0 ships this
plus a minimal blast-radius caller count; v12.1 wires intent ↔
semantic-diff and dark-matter expectations.
"""

from __future__ import annotations

import subprocess
import sys

import click

from roam.commands.resolve import ensure_index
from roam.critique.aggregator import aggregate
from roam.critique.checks import (
    check_clones_not_edited,
    check_impact,
    check_intent_alignment,
    find_changed_symbols,
    parse_diff,
)
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@click.command()
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read diff from a file instead of stdin.",
)
@click.option(
    "--high-callers",
    type=int,
    default=10,
    show_default=True,
    help="Direct-caller threshold above which `impact` emits a medium-severity finding.",
)
@click.option(
    "--intent",
    "intent_text",
    type=str,
    default=None,
    help=(
        "PR title or commit subject to check for alignment with the diff's "
        "semantic shape (e.g. 'fix login bug', 'rename UserSession -> "
        "Session'). Falls back to the latest git commit subject if a git "
        "repo is detected and this flag is omitted."
    ),
)
@click.pass_context
def critique(ctx, input_path, high_callers, intent_text):
    """Verify a patch against the indexed graph.

    Pipe a unified diff in via stdin (``git diff | roam critique``) or
    pass a file with ``--input``. The output is a ranked list of
    findings: clone siblings that may need the same change, symbols
    with high blast radius, and (in v12.1) intent / dark-matter checks.

    Returns exit code 5 when at least one *high* severity finding is
    present (mirrors ``cmd_rules`` ``EXIT_GATE_FAILURE``) so CI can
    gate on it.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if input_path:
        with open(input_path, encoding="utf-8") as fh:
            diff_text = fh.read()
    else:
        if sys.stdin.isatty():
            raise click.UsageError("no diff on stdin and no --input — pipe `git diff` in or pass --input PATH")
        diff_text = sys.stdin.read()

    if not diff_text.strip():
        raise click.UsageError("diff is empty")

    ensure_index()

    regions = parse_diff(diff_text)

    # Auto-pick up latest commit subject if --intent wasn't passed.
    effective_intent = intent_text
    if effective_intent is None:
        try:
            proc = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if proc.returncode == 0:
                effective_intent = proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            effective_intent = None

    with open_db(readonly=True) as conn:
        changed_symbols = find_changed_symbols(conn, regions)
        findings = []
        findings.extend(check_clones_not_edited(conn, changed_symbols, regions))
        findings.extend(check_impact(conn, changed_symbols, high_callers=high_callers))
        if effective_intent:
            findings.extend(check_intent_alignment(effective_intent, changed_symbols, regions))

    result = aggregate(findings)
    summary = {
        "verdict": result["verdict"],
        "changed_files": len(regions),
        "changed_symbols": len(changed_symbols),
        "findings": len(result["findings"]),
        "high_severity": result["severity_breakdown"].get("high", 0),
        "intent": effective_intent,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "critique",
                    summary=summary,
                    budget=token_budget,
                    severity_breakdown=result["severity_breakdown"],
                    findings=result["findings"],
                    top_finding=result["top_finding"],
                    changed_symbols=[
                        {
                            "symbol_id": s.symbol_id,
                            "name": s.name,
                            "qualified_name": s.qualified_name,
                            "kind": s.kind,
                            "file_path": s.file_path,
                            "line_start": s.line_start,
                            "line_end": s.line_end,
                        }
                        for s in changed_symbols
                    ],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {result['verdict']}")
        click.echo()
        click.echo(f"  changed files:   {len(regions)}")
        click.echo(f"  changed symbols: {len(changed_symbols)}")
        if result["findings"]:
            click.echo()
            for f in result["findings"]:
                click.echo(f"[{f['severity'].upper()}] {f['check']} :: {f['title']}")
                for line in f["detail"].splitlines():
                    click.echo(f"    {line}")
                click.echo()

    if result["severity_breakdown"].get("high", 0) > 0:
        ctx.exit(5)
