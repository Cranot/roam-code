"""roam retrieve — graph-aware context server (A.1).

Hands the calling agent a minimal, ranked, budget-bounded set of code
spans for a free-form task. Differs from ``roam context`` in that the
ranking is structural (PageRank + clones + lexical), not symbol-specific.

Examples
--------
    roam retrieve "is it safe to delete UserSession"
    roam retrieve "trace login flow" --seed-files src/auth.py --budget 6000
    roam --json retrieve "n+1 query in checkout" --k 10
"""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.config import get_retrieve_config
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, loc, to_json
from roam.retrieve.pipeline import run_retrieve


@click.command()
@click.argument("task", nargs=-1, required=True)
@click.option(
    "--budget",
    type=int,
    default=None,
    help="Token budget for the returned spans (default: from .roam/config.toml or 4000).",
)
@click.option(
    "--k",
    type=int,
    default=None,
    help="Maximum number of candidates to return (default: from config or 20).",
)
@click.option(
    "--rerank",
    type=click.Choice(["fast", "off", "learned"], case_sensitive=False),
    default=None,
    help=(
        "'fast' = structural rerank (default). 'off' = lexical only. "
        "'learned' = LightGBM LambdaMART trained on your bench (requires "
        '``pip install "roam-code[learned]"`` + a trained model at '
        "``$ROAM_LEARNED_MODEL``; falls back to 'fast' when unavailable)."
    ),
)
@click.option(
    "--seed-files",
    "seed_files",
    multiple=True,
    type=str,
    help="Seed the rerank with one or more files (can be repeated). Falls back to inference when absent.",
)
@click.pass_context
def retrieve(ctx, task, budget, k, rerank, seed_files):
    """Return ranked code spans for a free-form task.

    Composes hybrid first-stage (FTS5) + structural reranker (PageRank +
    clone-canonical signal) + token-budget cap. Output includes
    justification tags so callers can see *why* each span ranked.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cli_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    task_str = " ".join(task).strip()
    if not task_str:
        raise click.UsageError("task text cannot be empty")

    ensure_index()

    cfg = get_retrieve_config()
    effective_budget = budget if budget is not None else (cli_budget or cfg.get("default_budget", 4000))
    effective_k = k if k is not None else cfg.get("default_k", 20)
    effective_rerank = (rerank or cfg.get("default_rerank", "fast")).lower()

    with open_db(readonly=True) as conn:
        # Defensive guard: if symbol_fts has been wiped (rare, but seen
        # mid-session after schema migrations on cloud-synced repos), the
        # entire pipeline silently returns 0 candidates. Surface a clear
        # remediation message instead.
        try:
            fts_count = conn.execute("SELECT COUNT(*) FROM symbol_fts").fetchone()[0]
        except Exception:
            fts_count = -1
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if sym_count > 0 and fts_count == 0:
            msg = f"VERDICT: search index is empty (0 / {sym_count} symbols indexed for FTS5)."
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "retrieve",
                            summary={
                                "verdict": msg,
                                "candidates": 0,
                                "total_candidates": 0,
                                "fts_rows": 0,
                                "symbol_count": sym_count,
                            },
                            budget=effective_budget,
                            task=task_str,
                        )
                    )
                )
            else:
                click.echo(msg)
                click.echo("Run `roam index --force` to rebuild the search index.")
            return

        result = run_retrieve(
            conn,
            task_str,
            budget=effective_budget,
            k=effective_k,
            rerank=effective_rerank,
            seed_files=list(seed_files) or None,
        )

    candidates = result["candidates"]
    verdict = (
        f"{len(candidates)} span{'s' if len(candidates) != 1 else ''} "
        f"({result['budget_used']}/{result['budget']} tokens, "
        f"{len(result['seeds'])} seed{'s' if len(result['seeds']) != 1 else ''})"
        if candidates
        else "No candidates matched the task text"
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "retrieve",
                    summary={
                        "verdict": verdict,
                        "candidates": len(candidates),
                        "total_candidates": result["total_candidates"],
                        "budget": result["budget"],
                        "budget_used": result["budget_used"],
                        "k": result["k"],
                        "rerank": result["rerank"],
                        "seed_count": len(result["seeds"]),
                    },
                    budget=effective_budget,
                    task=result["task"],
                    weights=result["weights"],
                    seeds=result["seeds"],
                    candidates=candidates,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not candidates:
        click.echo()
        click.echo("Try `roam retrieve <task> --seed-files <path>` to anchor the search.")
        return

    click.echo()
    click.echo(f"TASK: {result['task']}")
    if result["seeds"]:
        click.echo(f"SEEDS: {len(result['seeds'])} symbol(s) ({result['rerank']} rerank)")
    click.echo()

    for idx, item in enumerate(candidates, start=1):
        score = item.get("score", 0.0)
        kind = item.get("kind", "?")
        name = item.get("name", "?")
        path = item.get("file_path", "?")
        line = item.get("line_start") or 0
        click.echo(f"{idx:2d}. [{score:.3f}] {kind:<8} {name:<40s} {loc(path, line)}")
        just = item.get("justifications") or {}
        tags = []
        if "pagerank" in just:
            tags.append(f"pr={just['pagerank']}({just.get('pagerank_kind', '?')})")
        if "fts" in just:
            tags.append(f"fts={just['fts']}")
        if "clone_cluster" in just:
            tags.append(f"clone(cluster={just['clone_cluster']},siblings={just['clone_siblings']})")
        if tags:
            click.echo(f"    why: {' '.join(tags)}")

    click.echo()
    click.echo(
        f"SUMMARY: {len(candidates)} of {result['total_candidates']} candidates, "
        f"{result['budget_used']} tokens used (budget {result['budget']})"
    )
