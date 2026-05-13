"""Batch symbol search: run multiple name queries in one command.

Mirrors the ``roam_batch_search`` MCP tool but exposed at the CLI for
agents and humans that prefer subprocess invocation. The single most
important semantic difference from ``roam search`` is that this command
matches **symbol name only** by default — the historical behaviour
matched file paths as well, so a query like ``useAccountBalance`` would
return ``setup`` from ``tests/composables/account/useAccountBalance.test.ts``
because the path matches. Opt back into the old behaviour with
``--include-paths`` for users who want a wider net.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

_MAX_BATCH_QUERIES = 10

# Symbol-name-only SQL — the default path. Matches ``s.name`` and
# ``s.qualified_name`` only; explicitly does NOT touch ``f.path`` so a
# substring that happens to appear in a directory or test fixture name
# can't pollute the results.
_BATCH_SYMBOL_ONLY_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbols s "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE (s.name LIKE ? COLLATE NOCASE "
    "    OR s.qualified_name LIKE ? COLLATE NOCASE) "
    "ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name "
    "LIMIT ?"
)

# Legacy wide-match SQL — restored only when --include-paths is set.
# Matches symbol name OR qualified name OR file path. Useful for the
# rare case where the agent is searching for a fixture / fragment by
# the path it lives under, but the wrong default for exact symbol
# lookup.
_BATCH_WITH_PATHS_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbols s "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE (s.name LIKE ? COLLATE NOCASE "
    "    OR s.qualified_name LIKE ? COLLATE NOCASE "
    "    OR f.path LIKE ? COLLATE NOCASE) "
    "ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name "
    "LIMIT ?"
)


def _run_one(conn, q: str, limit: int, include_paths: bool) -> list[dict]:
    """Execute one query against the DB and return plain dict rows."""
    like = f"%{q}%"
    if include_paths:
        rows = conn.execute(_BATCH_WITH_PATHS_SQL, (like, like, like, limit)).fetchall()
    else:
        rows = conn.execute(_BATCH_SYMBOL_ONLY_SQL, (like, like, limit)).fetchall()
    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"] or "",
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line_start": r["line_start"],
            "pagerank": round(float(r["pagerank"] or 0), 4),
        }
        for r in rows
    ]


@roam_capability(
    name="batch-search",
    category="exploration",
    summary="Run up to 10 symbol-name patterns in a single command.",
    inputs=["queries"],
    outputs=["results", "verdict"],
    examples=["roam batch-search loginUser logoutUser"],
    tags=["search", "batch"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command("batch-search")
@click.argument("queries", nargs=-1)
@click.option(
    "--limit-per-query",
    "limit_per_query",
    type=int,
    default=5,
    show_default=True,
    help="Max results per individual query (capped at 50).",
)
@click.option(
    "--include-paths",
    "include_paths",
    is_flag=True,
    default=False,
    help=(
        "Also match against file paths. Off by default — the previous "
        "wide-match behaviour returned spurious matches when a query "
        "string happened to appear in a directory or fixture filename "
        "(e.g. ``useAccountBalance`` matching ``setup`` from "
        "``tests/composables/account/useAccountBalance.test.ts``)."
    ),
)
@click.pass_context
def batch_search(ctx, queries, limit_per_query, include_paths):
    """Search up to 10 symbol-name patterns in a single command.

    Replaces 10 sequential ``roam search`` calls with one DB connection.
    Each positional argument is one independent query; results are
    grouped by query in the JSON envelope.

    \b
    Examples:
      roam batch-search Auth Login Logout
      roam batch-search useFoo useBar --limit-per-query 10
      roam batch-search Setup --include-paths    # match path too

    See also ``search`` (single-query, more options), ``complete``
    (prefix completion), and ``grep`` (file-content search).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    queries_list: list[str] = [str(q) for q in (queries or []) if q][:_MAX_BATCH_QUERIES]
    limit = max(1, min(int(limit_per_query), 50))

    if not queries_list:
        verdict = "no queries provided"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "batch-search",
                        summary={
                            "verdict": verdict,
                            "queries_executed": 0,
                            "total_matches": 0,
                            "partial_success": True,
                        },
                        queries=[],
                        results={},
                        errors={},
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        click.echo("Pass one or more name substrings, e.g. `roam batch-search Auth Login`.")
        return

    ensure_index()

    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    with open_db(readonly=True) as conn:
        for q in queries_list:
            try:
                results[q] = _run_one(conn, q, limit, include_paths)
            except Exception as exc:  # noqa: BLE001 — surface any DB error per query
                errors[q] = str(exc)

    total_matches = sum(len(v) for v in results.values())
    if results and total_matches:
        verdict = f"{total_matches} matches across {len(results)} queries"
    elif results:
        verdict = "no matches found"
    else:
        verdict = "all queries failed"
    if errors:
        verdict += f", {len(errors)} queries failed"
    # ``partial_success`` is required on every envelope (CLAUDE.md
    # checklist + Pattern 2). True when any per-query call errored,
    # OR when we produced zero results across all queries.
    partial = bool(errors) or (not results) or total_matches == 0

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "batch-search",
                    summary={
                        "verdict": verdict,
                        "queries_executed": len(queries_list),
                        "total_matches": total_matches,
                        "include_paths": include_paths,
                        "partial_success": partial,
                    },
                    queries=queries_list,
                    results=results,
                    errors=errors,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    for q in queries_list:
        rows = results.get(q, [])
        click.echo(f"\n=== {q} ({len(rows)} matches) ===")
        if not rows:
            err = errors.get(q)
            if err:
                click.echo(f"  error: {err}")
            else:
                click.echo("  (no matches)")
            continue
        for r in rows:
            qn = r["qualified_name"] or r["name"]
            click.echo(f"  {qn} [{r['kind']}] {r['file_path']}:{r['line_start']}")
