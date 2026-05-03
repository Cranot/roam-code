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
from roam.retrieve.semantic import semantic_coverage


def _retrieve_confidence(candidates: list[dict], task: str = "") -> str:
    """Classify the confidence of a retrieve result.

    Returns ``"low"`` when the top result probably doesn't match the
    query, ``"ok"`` otherwise. Heuristic (redacted,
    two iterations):

    * **Token coverage** — count distinct query tokens that appear
      across the top-5 candidate paths. If only 1 token is covered
      across all 5 (e.g. "trace login flow" → only "trace" matches
      anywhere), the answer is almost certainly junk: the lexical
      hits are tracking one common word.
    * **Score signals** — also flag when top score is < 0.30 AND
      top-vs-fifth spread is < 0.10 (scores bunched at the noise
      floor).

    Either signal trips low-confidence; the token-coverage check
    catches the original "trace the login flow" failure mode that
    the score-only heuristic missed (top was 1.1 but covered just
    one query token).
    """
    if not candidates:
        return "low"
    scores = [float(c.get("score") or 0.0) for c in candidates if c.get("score") is not None]
    if not scores:
        return "ok"

    # High-confidence override (redacted): a top-1 that
    # significantly outranks the 2nd hit (gap ≥ 0.30 in normalised
    # space) signals a unique winner — the structural reranker found
    # one strong answer rather than many equal candidates. Skip the
    # token-coverage check in that case.
    # Distinguishes the email query ("where is email sending" →
    # send_welcome at 0.900, 2nd at 0.275 → gap 0.625, real answer)
    # from the trace query ("trace the login flow" → 1.014, 2nd 0.942
    # → gap 0.072, all matching one common word).
    top = scores[0]
    second = scores[1] if len(scores) > 1 else 0.0
    if top - second >= 0.30:
        return "ok"
    fifth = scores[min(4, len(scores) - 1)]
    if top < 0.30 and (top - fifth) < 0.10:
        return "low"

    # Token-coverage check across top-10 path+name. Match against both
    # path AND symbol name — a test like ``test_implements_edge`` in
    # ``test_comprehensive.py`` covers "implements" via name even though
    # the path doesn't. Trip low-confidence when ≥3 query tokens were
    # supplied but ≤1 of them surfaces *anywhere* in the top-10. That
    # catches "trace the login flow" (top-10 has "trace" everywhere
    # but never "login" or "flow") while letting "AST clone detection
    # implemented" pass (clone, implements, detect all surface).
    if task and candidates:
        try:
            from roam.retrieve.seeds import extract_tokens

            tokens = extract_tokens(task)
        except Exception:
            tokens = []
        if len(tokens) >= 2:
            lowered = {t.lower() for t in tokens if len(t) >= 4}
            covered: set[str] = set()
            for c in candidates[:10]:
                surface = (
                    (c.get("file_path") or c.get("file") or "")
                    + " "
                    + (c.get("name") or "")
                    + " "
                    + (c.get("qualified_name") or "")
                ).lower()
                for tok in lowered:
                    if tok in surface:
                        covered.add(tok)
                    elif len(tok) >= 7:
                        # Prefix match for plurals/derivations: "detection"
                        # → look for "detect", "implementing" → "implement".
                        # Length floor ≥7 means the prefix has ≥4 chars
                        # which is past the noise floor for path tokens.
                        if tok[:-3] in surface and len(tok[:-3]) >= 4:
                            covered.add(tok)
            # Trip when ≥2 query tokens were supplied but ≤1 covered
            # in top-10. Catches "trace login flow" (only "trace"
            # matches, "login"/"flow" never appear) without firing
            # on "AST clone detection implemented" where "clone" and
            # "implemented" both surface in top-10.
            if len(lowered) >= 2 and len(covered) <= 1:
                return "low"
    return "ok"


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
        semantic_diag = semantic_coverage(conn)
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
                            semantic_coverage=semantic_diag,
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
    confidence = _retrieve_confidence(candidates, task_str)
    base_verdict = (
        f"{len(candidates)} span{'s' if len(candidates) != 1 else ''} "
        f"({result['budget_used']}/{result['budget']} tokens, "
        f"{len(result['seeds'])} seed{'s' if len(result['seeds']) != 1 else ''})"
        if candidates
        else "No candidates matched the task text"
    )
    # R.5 (dogfood 2026-05-01): "trace the login flow" against a repo
    # with no login flow returned 20 spans with no warning. The agent
    # had no signal that the answer was junk. We now prepend a
    # confidence tag to the verdict when (a) the top score is below
    # an absolute floor or (b) scores are bunched within a narrow
    # band — both indicators that lexical hits are spread thin
    # rather than concentrated on a real match.
    verdict = f"low confidence — {base_verdict}" if confidence == "low" else base_verdict

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
                        "semantic_embeddings": semantic_diag["embeddings"],
                        "semantic_coverage_pct": semantic_diag["coverage_pct"],
                    },
                    budget=effective_budget,
                    task=result["task"],
                    weights=result["weights"],
                    semantic_coverage=semantic_diag,
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
    if float(result["weights"].get("zeta", 0.0) or 0.0) > 0 and not semantic_diag["ready"]:
        click.echo(
            "SEMANTIC: 0 dense vectors available; zeta is currently inert. "
            "Configure semantic backend and rerun `roam index` to activate it."
        )
