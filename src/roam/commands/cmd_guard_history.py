"""`roam guard-history` — minimal Roam Guard dashboard.

SARIF is deliberately NOT emitted: output is a tabular dashboard over
past verdict-log entries, not file-located findings — per-bundle SARIF
already ships from `roam proof-bundle --format sarif`.

Lists pr-bundles in `.roam/pr-bundles/` with branch, intent, last-known
verdict, and changed-files count. Sort by mtime (most recent first).

Useful as the CLI-side complement to a hosted dashboard — anyone with a
clone can see what bundles exist + what they say without spinning up infra.

Usage:
    roam guard-history                       # table view
    roam --json guard-history                # JSON envelope for tooling
    roam guard-history --limit 5
    roam guard-history --verdict blocked     # filter
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.guard_log import log_path_for, read_log_entries
from roam.output.formatter import json_envelope, to_json
from roam.pr_bundle_primitives import all_bundle_paths as _all_bundles
from roam.proof_bundle import compose_agent_change_proof_bundle, load_pr_bundle


def _branch_name(path: Path) -> str:
    """Recover branch from the filename (slashes are `__` per bundle convention)."""
    return path.stem.replace("__", "/")


def _summarize_bundle(path: Path, root: Path) -> dict[str, Any]:
    """Build a one-row summary of a bundle for the dashboard."""
    row: dict[str, Any] = {
        "path": str(path),
        "branch": _branch_name(path),
        "mtime": path.stat().st_mtime,
        "intent": None,
        "verdict": None,
        "changed_files": 0,
        "required_checks": 0,
        "executed_checks": 0,
        "error": None,
    }
    try:
        bundle = load_pr_bundle(path)
    except Exception as e:
        row["error"] = f"load_failed: {e}"
        return row
    row["intent"] = bundle.get("intent")
    try:
        v1 = compose_agent_change_proof_bundle(bundle, repo_root=root)
    except Exception as e:
        row["error"] = f"compose_failed: {e}"
        return row
    row["verdict"] = (v1.get("verdict") or {}).get("value")
    row["changed_files"] = len(v1.get("changed_files") or [])
    row["required_checks"] = len((v1.get("verification_contract") or {}).get("required") or [])
    row["executed_checks"] = len(v1.get("executed_checks") or [])
    return row


_VERDICT_ICONS = {
    "pass": "✓",
    "pass_with_warnings": "⚠",
    "needs_review": "👀",
    "blocked": "✗",
    None: "?",
}


def _log_entry_to_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a verdict-log JSONL entry into the same row shape as
    `_summarize_bundle` produces, so downstream rendering is uniform."""
    return {
        "path": entry.get("bundle"),
        "branch": entry.get("branch"),
        "mtime": None,
        "intent": entry.get("intent"),
        "verdict": entry.get("verdict"),
        "changed_files": entry.get("changed_files", 0),
        "required_checks": entry.get("required", 0),
        "executed_checks": entry.get("executed", 0),
        "ts": entry.get("ts"),
        "head_sha": entry.get("head_sha"),
        "source": "log",
    }


def _rows_from_log(root: Path, limit: int) -> list[dict[str, Any]]:
    """Fast-path: read recent rows from the verdict log instead of
    re-composing every bundle. Returns [] when the log doesn't exist."""
    entries = read_log_entries(root, limit=limit)
    return [_log_entry_to_row(e) for e in entries]


@click.command(name="guard-history")
@click.option("--limit", "-n", type=int, default=10, help="Max rows to show (default 10).")
@click.option(
    "--verdict",
    "verdict_filter",
    type=click.Choice(["pass", "pass_with_warnings", "needs_review", "blocked"]),
    default=None,
    help="Only show bundles with this verdict.",
)
@click.option(
    "--source",
    type=click.Choice(["auto", "log", "compose"]),
    default="auto",
    help="Where to read rows from: 'log' (fast, audit trail), "
    "'compose' (re-derive from bundles), 'auto' (log if present, "
    "else compose). Default: auto.",
)
@click.option(
    "--rebuild",
    is_flag=True,
    default=False,
    help="Force re-compose even when the verdict log exists. Equivalent to --source compose.",
)
@click.option("--branch", "branch_filter", type=str, default=None, help="Only show entries for this branch.")
@click.pass_context
@roam_capability(
    name="guard-history",
    category="planning",
    summary="List recent Roam Guard pr-bundles + their composed verdicts",
    inputs=("pr_bundle_dir",),
    outputs=("history_rows",),
    examples=(
        "roam guard-history",
        "roam guard-history --limit 20 --verdict blocked",
        "roam --json guard-history",
    ),
    tags=("planning", "proof-bundle", "history", "dashboard"),
)
def guard_history(
    ctx: click.Context,
    limit: int,
    verdict_filter: str | None,
    source: str,
    rebuild: bool,
    branch_filter: str | None,
) -> None:
    """Show recent pr-bundles + their last-known verdict."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    root = Path(find_project_root() or Path.cwd())
    bundle_paths = _all_bundles(root)

    # Decide source: explicit --rebuild forces compose. Else respect --source.
    if rebuild:
        source = "compose"
    log_exists = log_path_for(root).is_file()
    effective_source = source
    if source == "auto":
        effective_source = "log" if log_exists else "compose"

    rows: list[dict[str, Any]] = []
    # Oversample when any filter is active so we don't return < limit rows.
    oversample = 5 if (verdict_filter or branch_filter) else 1
    if effective_source == "log":
        # Fast-path: read the JSONL verdict log. No re-composition.
        for row in _rows_from_log(root, limit * oversample):
            if verdict_filter and row.get("verdict") != verdict_filter:
                continue
            if branch_filter and row.get("branch") != branch_filter:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
    else:
        # Compose path: load + compose each bundle on the fly.
        for p in bundle_paths:
            row = _summarize_bundle(p, root)
            row["source"] = "compose"
            if verdict_filter and row.get("verdict") != verdict_filter:
                continue
            if branch_filter and row.get("branch") != branch_filter:
                continue
            rows.append(row)
            if len(rows) >= limit:
                break

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-history",
                    summary={
                        "verdict": f"{len(rows)} bundle(s) via {effective_source}",
                        "bundle_count": len(rows),
                        "total_in_dir": len(bundle_paths),
                        "verdict_filter": verdict_filter,
                        "branch_filter": branch_filter,
                        "source": effective_source,
                        "log_available": log_exists,
                        "partial_success": False,
                    },
                    agent_contract={
                        "facts": [
                            f"{len(rows)} bundles shown",
                            f"{len(bundle_paths)} total in directory",
                        ],
                        "next_commands": [
                            "roam guard-pr --bundle <path>",
                            "roam proof-bundle --bundle <path>",
                        ],
                        "risks": [],
                    },
                    rows=rows,
                )
            )
        )
        return

    if not rows:
        click.echo("No pr-bundles found." if not bundle_paths else f"No bundles match verdict={verdict_filter!r}.")
        return

    # Plain ASCII table (LAW: no emojis in default output; use icons sparingly).
    click.echo(
        f"VERDICT: {len(rows)} bundle(s) via {effective_source} "
        f"(of {len(bundle_paths)} bundles on disk; log "
        f"{'present' if log_exists else 'absent'})"
    )
    click.echo(f"{'V':2s} {'BRANCH':25s} {'INTENT':40s} {'FILES':>6s} {'CHECKS':>10s}")
    click.echo("-" * 90)
    for row in rows:
        icon = _VERDICT_ICONS.get(row.get("verdict"), "?")
        branch = (row.get("branch") or "")[:25]
        intent = (row.get("intent") or "—")[:40]
        files = row.get("changed_files", 0)
        ex = row.get("executed_checks", 0)
        req = row.get("required_checks", 0)
        checks = f"{ex}/{req}"
        click.echo(f"{icon:2s} {branch:25s} {intent:40s} {files:>6d} {checks:>10s}")
