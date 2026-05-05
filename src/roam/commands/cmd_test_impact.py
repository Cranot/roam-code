"""``roam test-impact <range>`` — tests transitively reachable from changed symbols.

redactedsharper scope than `roam affected-tests`. Walks BFS over the
reverse call graph from each changed symbol, collects every test file
reached, and ranks by the number of changed symbols that reach each
test (so a test reachable from 5 changes ranks above one reachable
from 1).
"""

from __future__ import annotations

import subprocess

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.index.file_roles import is_test
from roam.output.formatter import json_envelope, to_json


def _changed_files(commit_range: str | None) -> list[str]:
    args = ["git", "diff", "--name-only"]
    if commit_range:
        args.append(commit_range)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]


@click.command(name="test-impact")
@click.argument("commit_range", required=False, default=None)
@click.option("--max-hops", type=int, default=5, show_default=True, help="BFS depth from each changed symbol.")
@click.option("--limit", type=int, default=20, show_default=True, help="Top N tests to surface.")
@click.pass_context
def test_impact(ctx, commit_range, max_hops, limit) -> None:
    """List tests transitively reachable from symbols changed in <range>."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    files = _changed_files(commit_range)
    files = [f for f in files if not is_test(f)]
    if not files:
        verdict = f"no non-test source files changed in {commit_range or 'working tree'}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "test-impact",
                        summary={"verdict": verdict, "count": 0},
                        tests=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    try:
        import networkx as nx

        from roam.graph.builder import build_symbol_graph
    except ImportError:
        click.echo("Graph module not available. Run `roam index`.")
        return

    with open_db(readonly=True) as conn:
        rows = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE f.path IN ({','.join('?' for _ in files)})",
            files,
        ).fetchall()
        seed_ids = [r["id"] for r in rows]

        if not seed_ids:
            verdict = "changed files have no indexed symbols"
            if json_mode:
                click.echo(to_json(json_envelope("test-impact", summary={"verdict": verdict, "count": 0}, tests=[])))
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        G = build_symbol_graph(conn)
        # Reverse the graph so descendants are *callers* of the changed
        # symbol. Tests are typically callers (or transitive callers).
        RG = G.reverse(copy=False)

        test_hits: dict[str, int] = {}
        for sid in seed_ids:
            if sid not in RG:
                continue
            try:
                lengths = nx.single_source_shortest_path_length(RG, sid, cutoff=int(max_hops))
            except Exception:
                continue
            for nid in lengths:
                if nid == sid:
                    continue
                node = G.nodes.get(nid, {}) or {}
                path = (node.get("file_path") or "").replace("\\", "/")
                if path and is_test(path):
                    test_hits[path] = test_hits.get(path, 0) + 1

    ranked = sorted(test_hits.items(), key=lambda x: -x[1])
    items = [{"file": path, "reach_count": cnt} for path, cnt in ranked[:limit]]

    verdict = (
        f"{len(test_hits)} test file(s) reachable from {len(files)} changed file(s)"
        if test_hits
        else f"no tests reach the {len(files)} changed file(s) within {max_hops} hop(s)"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "test-impact",
                    summary={"verdict": verdict, "count": len(test_hits)},
                    changed_files=files,
                    tests=items,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not items:
        return
    click.echo()
    click.echo(f"{'Reach':>5}  Test file")
    click.echo(f"{'-' * 5}  {'-' * 60}")
    for it in items:
        click.echo(f"{it['reach_count']:>5}  {it['file']}")
