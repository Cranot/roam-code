"""Suggest reviewers for a diff by git-blame line ownership (lines-added ranking).

Promotes pr-risk's hidden ``suggested_reviewers`` capability into its own
read-only advisory command. Ranks authors by total ``lines_added`` across the
non-test files a diff touches, using the indexed git history
(``git_file_changes`` / ``git_commits``) — NOT a live ``git blame`` shell-out.

This is a lighter, single-signal complement to ``suggest-reviewers`` (which
blends blame ownership + CODEOWNERS + recency + expertise breadth). Use
``blame-reviewers`` when you want the raw "who wrote the most lines here"
ranking scoped to a specific diff range, exactly as pr-risk computes it.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because reviewer suggestions are invocation-scoped attribution
rankings (top-N authors per diff) — routing metadata for a human PR
workflow, not per-location code-analysis findings with source coordinates.
See ``cmd_suggest_reviewers`` / ``cmd_pr_risk`` for the parallel
advisory-attribution disclosure pattern.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files, resolve_changed_to_db
from roam.commands.cmd_pr_risk import rank_blame_reviewers
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json


@roam_capability(
    name="blame-reviewers",
    category="review",
    summary="Suggest reviewers for a diff by git-blame line ownership",
    maturity="beta",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("blame-reviewers")
@click.argument("commit_range", required=False)
@click.option("--staged", is_flag=True, help="Rank reviewers for staged changes")
@click.option("--top", "top_n", type=int, default=5, help="Number of reviewers to suggest (default: 5)")
@click.pass_context
def blame_reviewers(ctx, commit_range, staged, top_n):
    """Suggest reviewers for a diff by git-blame line ownership.

    Ranks authors by total lines added across the non-test files the diff
    touches, using the indexed git history. This is the standalone form of
    the reviewer ranking embedded in ``roam pr-risk``.

    Pass a COMMIT_RANGE (e.g. ``HEAD~3..HEAD``) for committed changes, or
    use ``--staged`` for staged changes. Default: unstaged changes.

    \b
    Examples:
      roam blame-reviewers
      roam blame-reviewers --staged
      roam blame-reviewers HEAD~3..HEAD
      roam --json blame-reviewers HEAD~1..HEAD
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    root = find_project_root()

    label = commit_range or ("staged" if staged else "unstaged")

    changed = get_changed_files(root, staged=staged, commit_range=commit_range)
    if not changed:
        _emit_empty(
            json_mode,
            budget=token_budget,
            label=label,
            verdict=f"No changes found for {label}",
            changed_files=[],
        )
        return

    with open_db(readonly=True) as conn:
        file_map = resolve_changed_to_db(conn, changed)
        if not file_map:
            _emit_empty(
                json_mode,
                budget=token_budget,
                label=label,
                verdict="Changed files not in index. Run `roam index` first.",
                changed_files=changed,
            )
            return

        top_authors = rank_blame_reviewers(conn, file_map, limit=top_n)
        n_changed = len(file_map)

    reviewers = [{"author": a, "actor": a, "lines": lines} for a, lines in top_authors]

    if not reviewers:
        _emit_empty(
            json_mode,
            budget=token_budget,
            label=label,
            verdict=f"No blame authors found for {n_changed} changed file{'s' if n_changed != 1 else ''}",
            changed_files=list(file_map.keys()),
        )
        return

    verdict = (
        f"{len(reviewers)} reviewer{'s' if len(reviewers) != 1 else ''} suggested "
        f"for {n_changed} changed file{'s' if n_changed != 1 else ''}"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "blame-reviewers",
                    summary={
                        "verdict": verdict,
                        "reviewers_suggested": len(reviewers),
                        "changed_files": n_changed,
                        "label": label,
                    },
                    budget=token_budget,
                    reviewers=reviewers,
                    changed_files=list(file_map.keys()),
                )
            )
        )
        return

    # Text output
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    rows = [[str(i), r["author"], str(r["lines"])] for i, r in enumerate(reviewers, 1)]
    click.echo(format_table(["RANK", "REVIEWER", "LINES ADDED"], rows))


def _emit_empty(json_mode, *, budget, label, verdict, changed_files):
    """Emit a clean 'no suggestions' result without crashing."""
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "blame-reviewers",
                    summary={
                        "verdict": verdict,
                        "reviewers_suggested": 0,
                        "changed_files": len(changed_files),
                        "label": label,
                    },
                    budget=budget,
                    reviewers=[],
                    changed_files=changed_files,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")
