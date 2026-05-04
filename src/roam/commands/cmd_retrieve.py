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
from roam.output.confidence import verdict_prefix
from roam.output.formatter import json_envelope, loc, to_json
from roam.retrieve.pipeline import run_retrieve
from roam.retrieve.semantic import semantic_coverage


def _retrieve_confidence_score(candidates: list[dict], task: str = "") -> tuple[float, str]:
    """Return a calibrated confidence number in ``[0.0, 1.0]`` plus a
    string label (``"low"`` / ``"ok"``) for backwards compat.

    Three signals combine multiplicatively:

    1. **Token coverage** — fraction of query tokens that appear in
       the top-10 results' name/path. Strong signal: if you ask for
       "auth login session" and only "auth" appears anywhere in the
       results, the search missed the intent.
    2. **Score gap** — how far does the top result outrank the
       runners-up. A unique winner (gap ≥ 0.30 in normalised space)
       is high-confidence; a flat distribution is low-confidence.
    3. **Top-score absolute floor** — scores bunched near 0.20 with
       no spread are noise-floor matches.

    Returns ``(score, label)``. The label is "low" when ``score < 0.40``,
    "ok" otherwise. The previous binary classifier preserved the
    legacy threshold (token-cover ≤ 1 OR top<0.30+spread<0.10);
    a continuous score lets the verdict carry more useful info
    (e.g. "0.62 confidence" vs "low / ok").
    """
    if not candidates:
        return 0.0, "low"
    scores = [float(c.get("score") or 0.0) for c in candidates if c.get("score") is not None]
    if not scores:
        return 0.50, "ok"  # have candidates but no scores — neutral

    top = scores[0]
    second = scores[1] if len(scores) > 1 else 0.0
    fifth = scores[min(4, len(scores) - 1)]

    # ---- Score-distribution signal ----
    # A unique winner is the strongest signal: gap ≥ 0.30 → score 1.0;
    # gap ≤ 0.05 → score 0.20; linear in between.
    gap = top - second
    if gap >= 0.30:
        gap_signal = 1.0
    elif gap <= 0.05:
        gap_signal = 0.20
    else:
        gap_signal = 0.20 + 0.80 * (gap - 0.05) / 0.25

    # Score floor: top < 0.30 with bunched tail → mostly noise.
    if top < 0.20 or (top < 0.30 and (top - fifth) < 0.10):
        floor_signal = 0.20
    else:
        floor_signal = min(1.0, top / 1.0)  # top score itself is in [0,1+]

    # ---- Token-coverage signal ----
    coverage_signal = 1.0  # default: one-token queries can't fail this check
    if task:
        try:
            from roam.retrieve.seeds import extract_tokens

            tokens = extract_tokens(task)
        except Exception:
            tokens = []
        if len(tokens) >= 2:
            lowered = {t.lower() for t in tokens if len(t) >= 4}
            if lowered:
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
                        elif len(tok) >= 7 and tok[:-3] in surface and len(tok[:-3]) >= 4:
                            covered.add(tok)
                # Coverage as a fraction of query tokens, squared so a
                # missing key word penalizes harder than linear. Without
                # this, "trace the login flow" (2/3 covered — "login"
                # missing) scored ``coverage_signal=0.67`` and the result
                # crossed the "ok" threshold, even though the missing
                # word was the actual subject. Squaring drops 0.67 → 0.45,
                # 1/3 → 0.11, 3/3 → 1.0 — preserving precision when all
                # tokens land while pushing partial-coverage queries
                # below the low-confidence threshold.
                coverage_signal = (len(covered) / len(lowered)) ** 2

    # Combine — weighted geometric mean preserves the "any signal at
    # the floor crashes the result" property of the old binary check
    # while letting strong signals compose.
    confidence = (gap_signal * 0.35) + (floor_signal * 0.25) + (coverage_signal * 0.40)
    confidence = max(0.0, min(1.0, confidence))
    label = "low" if confidence < 0.40 else "ok"
    return round(confidence, 3), label


def _retrieve_confidence(candidates: list[dict], task: str = "") -> str:
    """Backwards-compat shim — returns just the string label."""
    _, label = _retrieve_confidence_score(candidates, task)
    return label


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
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help=(
        "Return the search plan (candidate ids, scores, locations) without "
        "fetching span content. Round 4 feature D: lets agents see what "
        "would be retrieved before paying the token cost."
    ),
)
@click.pass_context
def retrieve(ctx, task, budget, k, rerank, seed_files, dry_run):
    """Return ranked code spans for a free-form task.

    Composes hybrid first-stage (FTS5) + structural reranker (PageRank +
    clone-canonical signal) + token-budget cap. Output includes
    justification tags so callers can see *why* each span ranked.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cli_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    task_str = " ".join(task).strip()
    if not task_str:
        from roam.output.errors import EMPTY_INPUT, structured_usage_error

        raise structured_usage_error(EMPTY_INPUT, "task text cannot be empty")

    ensure_index()

    cfg = get_retrieve_config()
    effective_k = k if k is not None else cfg.get("default_k", 20)
    effective_rerank = (rerank or cfg.get("default_rerank", "fast")).lower()

    # 12.13 — adaptive budget. The fixed 4000-token default was a
    # one-size-fits-all guess; a query with ``--k 5`` only needs
    # ~1500 tokens to surface 5 spans, while ``--k 50`` would
    # truncate against 4000. Scale proportionally to k, with a floor
    # of 1500 (smallest useful answer) and ceiling at the configured
    # default for the standard k=20 path so legacy behaviour is
    # preserved exactly. Explicit ``--budget`` always wins.
    if budget is not None:
        effective_budget = budget
    elif cli_budget:
        effective_budget = cli_budget
    else:
        config_budget = cfg.get("default_budget", 4000)
        # 200 tokens per result is the empirical mean span size on
        # the 30-task self-bench. max() floors small-k queries; we
        # cap at 2× config_budget so a runaway --k 200 doesn't burn
        # 40k tokens.
        adaptive = max(1500, effective_k * 200)
        effective_budget = min(adaptive, config_budget * 2)

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
    if dry_run:
        # Strip span content so the agent sees what *would* be retrieved
        # without paying the token cost. Keeps location / score / why.
        stripped = []
        for item in candidates:
            keep = {
                k: item[k]
                for k in (
                    "name",
                    "qualified_name",
                    "kind",
                    "file_path",
                    "line_start",
                    "line_end",
                    "score",
                    "justifications",
                    "symbol_id",
                )
                if k in item
            }
            stripped.append(keep)
        candidates = stripped
    confidence_score, confidence = _retrieve_confidence_score(candidates, task_str)
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
    # rather than concentrated on a real match. The string formatting
    # is centralised in :mod:`roam.output.confidence` (v12.12) so future
    # commands surface the same shape.
    # Phase-bonus 2026-05-04 — append the calibrated confidence
    # number to the verdict so agents can branch on a continuous
    # signal instead of a binary low/ok. The label-prefix shape is
    # preserved for backwards compat.
    verdict = verdict_prefix(base_verdict, confidence == "low")
    if candidates:
        verdict = f"{verdict} (confidence {confidence_score:.2f})"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "retrieve",
                    summary={
                        "verdict": verdict,
                        "low_confidence": confidence == "low",
                        "confidence": confidence_score,
                        "candidates": len(candidates),
                        "total_candidates": result["total_candidates"],
                        "budget": result["budget"],
                        "budget_used": result["budget_used"] if not dry_run else 0,
                        "k": result["k"],
                        "rerank": result["rerank"],
                        "seed_count": len(result["seeds"]),
                        "semantic_embeddings": semantic_diag["embeddings"],
                        "semantic_coverage_pct": semantic_diag["coverage_pct"],
                        "dry_run": dry_run,
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
