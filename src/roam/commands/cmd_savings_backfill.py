"""Derive privacy-preserving discovery episodes from local agent transcripts.

Output formats: text (default) and ``--json``. SARIF is deliberately NOT
emitted because backfill reports aggregate transcript-processing coverage and
privacy state, without file-located code findings or source coordinates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json
from roam.transcript_backfill import (
    DEFAULT_MAX_TRANSCRIPT_FILES,
    MAX_TRANSCRIPT_FILES_PER_SOURCE,
    TranscriptBackfillSafetyError,
    backfill_transcripts,
)


def _since_value(_ctx: click.Context, _param: click.Parameter, value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise click.BadParameter("use YYYY-MM-DD") from exc


@click.command(name="savings-backfill")
@click.option(
    "--transcripts-dir",
    required=True,
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="Transcript root; repeat to merge Claude and Codex stores.",
)
@click.option("--root", default=".", show_default=True, help="Project root receiving the derived snapshot.")
@click.option(
    "--source",
    type=click.Choice(["auto", "claude", "codex"], case_sensitive=False),
    default="auto",
    show_default=True,
)
@click.option("--since", callback=_since_value, help="Include transcript files modified on/after YYYY-MM-DD.")
@click.option(
    "--max-files",
    type=click.IntRange(min=1, max=MAX_TRANSCRIPT_FILES_PER_SOURCE),
    default=DEFAULT_MAX_TRANSCRIPT_FILES,
    show_default=True,
    help="Newest files to scan per --transcripts-dir under the hard resource cap.",
)
@click.option(
    "--all-projects",
    is_flag=True,
    help="Include every cwd in the source tree; default keeps only episodes under --root.",
)
@click.option("--dry-run", is_flag=True, help="Scan and report without writing transcript-episodes.jsonl.")
@click.pass_context
@roam_capability(
    name="savings-backfill",
    category="planning",
    summary="Extract privacy-preserving historical discovery episodes from Claude or Codex transcripts.",
    inputs=("--transcripts-dir", "--root", "--source", "--since", "--max-files"),
    outputs=("summary_envelope", "privacy_contract", "episode_counts"),
    examples=(
        "roam savings-backfill --transcripts-dir ~/.claude/projects",
        "roam savings-backfill --transcripts-dir ~/.codex/sessions --source codex",
    ),
    tags=("planning", "telemetry", "transcripts", "privacy", "savings"),
    ai_safe=True,
    requires_index=False,
    mcp_expose=False,
    mcp_preset=(),
    side_effect=True,
    stale_sensitive=False,
)
def savings_backfill(
    ctx: click.Context,
    transcripts_dir: tuple[str, ...],
    root: str,
    source: str,
    since: datetime | None,
    max_files: int,
    all_projects: bool,
    dry_run: bool,
) -> None:
    """Write a value-redacted historical episode snapshot for repeated-pattern discovery."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    try:
        result = backfill_transcripts(
            root,
            transcripts_dir,
            source=source.lower(),
            since=since,
            max_files=max_files,
            all_projects=all_projects,
            dry_run=dry_run,
        )
    except TranscriptBackfillSafetyError as exc:
        verdict = "Transcript backfill stopped safely because private-state containment could not be proven"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "savings-backfill",
                        budget=token_budget,
                        summary={
                            "verdict": verdict,
                            "state": "unsafe_path",
                            "partial_success": True,
                            "policy_admissible": False,
                        },
                        status="error",
                        isError=True,
                        error_code="RUN_FAILED",
                        error=str(exc),
                        privacy_contract={
                            "raw_transcripts_persisted": False,
                            "raw_prompts_or_responses_persisted": False,
                            "raw_paths_or_shell_values_persisted": False,
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"reason: {exc}")
        ctx.exit(1)
        return
    base_verdict = (
        f"Derived {result['episodes']} historical discovery episodes from "
        f"{result['files_with_episodes']} transcript files"
    )
    partial_reasons = [
        name
        for name, present in (
            ("unknown_formats", result["unknown_format_files"]),
            ("file_selection_truncated", result["files_truncated"]),
            ("oversized_files", result["oversized_files"]),
            ("traversal_truncated", result["traversal_truncated"]),
            ("degraded_files", result["degraded_transcript_files"]),
            ("aggregate_input_truncated", result["aggregate_limit_reached"] != "none"),
        )
        if present
    ]
    verdict = base_verdict
    if partial_reasons:
        verdict += f"; incomplete evidence: {', '.join(partial_reasons)}"
    summary = {
        "verdict": verdict,
        "state": result["state"],
        "partial_success": bool(partial_reasons),
        "episodes": result["episodes"],
        "files_with_episodes": result["files_with_episodes"],
        "policy_admissible": False,
    }
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "savings-backfill",
                    budget=token_budget,
                    summary=summary,
                    agent_contract={
                        "facts": [
                            f"{result['episodes']} historical discovery episodes",
                            f"{result['files_with_episodes']} transcript files",
                            "0 raw prompt, response, path, or shell-command texts persisted",
                        ],
                        "next_commands": ["roam savings"],
                        "risks": ["Historical proxy outcomes require prospective validation before policy promotion"],
                        "confidence": None,
                    },
                    **result,
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"events:             {result['events']}")
    click.echo(f"unknown formats:    {result['unknown_format_files']}")
    click.echo(f"files truncated:    {result['files_truncated']}")
    click.echo(f"oversized files:    {result['oversized_files']}")
    click.echo(f"traversal capped:   {result['traversal_truncated']}")
    click.echo(f"degraded files:     {result['degraded_transcript_files']}")
    click.echo(f"input budget:       {result['aggregate_limit_reached']}")
    click.echo(f"output:              {result['output']}")
    click.echo("privacy:             raw values excluded; sanitized shell templates retained")
    click.echo("Run `roam savings` to rank historical candidates separately from live evidence.")
