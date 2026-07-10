"""Import sampled profiler output and rank indexed source spans by runtime share."""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.exit_codes import EXIT_ERROR
from roam.output.formatter import format_table, json_envelope, to_json
from roam.runtime.profile_ingest import parse_speedscope, rank_hot_spans


def _emit_error(json_mode: bool, profile_file: Path, message: str, token_budget: int = 0) -> None:
    verdict = f"Failed to parse profiler trace: {profile_file}"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "profile-import",
                    summary={
                        "verdict": verdict,
                        "state": "parse_error",
                        "partial_success": True,
                        "mapped_spans": 0,
                        "unmapped_frames": 0,
                    },
                    budget=token_budget,
                    error=message,
                    spans=[],
                    unmapped_frames=[],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
        click.echo(message)


@roam_capability(
    name="profile-import",
    category="health",
    summary="Map sampled profiler frames to indexed source spans and rank cumulative runtime share",
    maturity="stable",
    mcp_expose=False,
    mcp_preset=(),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="profile-import")
@click.argument("profile_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--top", type=click.IntRange(min=1), default=20, show_default=True, help="Maximum mapped spans to show.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.pass_context
def profile_import(ctx, profile_file: Path, top: int, json_output: bool) -> None:
    """Rank source spans from a sampled py-spy/speedscope JSON profile.

    This command reads sampled speedscope JSON deterministically. Each sampled
    frame must carry ``file`` and ``line`` fields to map to the most-specific
    indexed symbol span containing that location. Unmapped frames remain in
    the result with an explicit reason.
    """
    json_mode = json_output or (ctx.obj.get("json") if ctx.obj else False)
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    try:
        profile = parse_speedscope(profile_file)
        with open_db(readonly=True) as conn:
            result = rank_hot_spans(conn, profile)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        _emit_error(json_mode, profile_file, str(exc), token_budget)
        ctx.exit(EXIT_ERROR)
        return

    all_spans = result["spans"]
    spans = all_spans[:top]
    unmapped = result["unmapped_frames"]
    partial = bool(unmapped)
    verdict = f"{len(all_spans)} mapped hot spans; {len(unmapped)} unmapped profiler frames"
    summary = {
        "verdict": verdict,
        "state": "partial" if partial else "complete",
        "partial_success": partial,
        "format": "speedscope",
        "profile_count": profile.profile_count,
        "sample_count": len(profile.samples),
        "frame_count": len(profile.frames),
        "mapped_spans": len(all_spans),
        "returned_spans": len(spans),
        "unmapped_frames": len(unmapped),
        "total_weight": profile.total_weight,
        "unit": profile.unit,
        "runtime_share_definition": "cumulative_sample_weight / total_sample_weight",
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "profile-import",
                    summary=summary,
                    budget=token_budget,
                    spans=spans,
                    unmapped_frames=unmapped,
                    agent_contract={
                        "facts": [
                            verdict,
                            f"{len(profile.samples)} weighted profiler samples",
                        ],
                        "next_commands": ["roam profile-import <speedscope.json>"],
                    },
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}\n")
    if spans:
        rows = [
            [
                span["symbol_name"],
                f"{span['file']}:{span['line_start']}-{span['line_end']}",
                f"{span['runtime_share_pct']:.2f}%",
            ]
            for span in spans
        ]
        click.echo(format_table(["Symbol", "Source span", "Cumulative runtime"], rows, budget=30))
    if unmapped:
        click.echo("\nUNMAPPED FRAMES")
        rows = [
            [
                frame["name"],
                f"{frame['file'] or '-'}:{frame['line'] or '-'}",
                f"{frame['runtime_share_pct']:.2f}%",
                frame["reason"],
            ]
            for frame in unmapped
        ]
        click.echo(format_table(["Frame", "Location", "Cumulative runtime", "Reason"], rows, budget=30))
