"""roam retrieve — graph-aware context server (A.1).

Hands the calling agent a minimal, ranked, budget-bounded set of code
spans for a free-form task. Differs from ``roam context`` in that the
ranking is structural (PageRank + clones + lexical), not symbol-specific.

Examples
--------
    roam retrieve "is it safe to delete UserSession"
    roam retrieve "trace login flow" --seed-file src/auth.py --budget 6000
    roam --json retrieve "n+1 query in checkout" --k 10

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because retrieve outputs are invocation-scoped task-grounded
context envelopes (ranked code spans within a token budget for a
free-form task) — not per-location code violations. The ranked spans
are retrieval results, not findings; an external SARIF consumer would
have nothing actionable to gate on. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH Bucket B propagation plan + W1221-audit
memo.
"""

from __future__ import annotations

import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.config import get_retrieve_config
from roam.db.connection import open_db
from roam.output.confidence import verdict_prefix
from roam.output.formatter import json_envelope, loc, to_json
from roam.retrieve.pipeline import run_retrieve
from roam.retrieve.semantic import semantic_coverage

_RECOVERABLE_RETRIEVE_ERRORS: tuple[type[Exception], ...] = (
    click.ClickException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    sqlite3.Error,
)

# ---------------------------------------------------------------------------
# W607-BI substrate-CALL boundaries (ADDITIVE to W607-B)
# ---------------------------------------------------------------------------
# Module-level helpers that delegate to the underlying retrieve substrate.
# Tests monkeypatch THESE shims (not the pipeline module) so the W607-BI
# marker plumbing inside ``retrieve`` can disclose substrate-CALL failures
# without colliding with the existing W607-B outer-guard
# (``retrieve_pipeline_failed:``).
#
# Each shim accepts the same arguments as the underlying pipeline call
# and returns the same result. A raise inside any shim becomes a
# ``retrieve_<phase>_failed:<exc_class>:<detail>`` marker via the
# ``_run_check_bi`` closure inside the click command body.
#
# FTS5 vs RERANK degradation tests rely on the separation between
# ``_fts5_search_full`` (full pipeline) and ``_fts5_search_lexical_only``
# (lexical-only fallback). When ``_fts5_search_full`` raises, the wrapper
# retries with ``rerank="off"`` so the agent still receives raw FTS5
# results rather than a wholesale empty envelope.


def _load_retrieve_config():
    """W607-BI substrate-CALL: configuration load."""
    return get_retrieve_config()


def _compute_semantic_coverage(conn):
    """W607-BI substrate-CALL: semantic-coverage diagnostic."""
    return semantic_coverage(conn)


def _fts5_search_full(conn, task_str, *, budget, k, rerank, seed_files):
    """W607-BI substrate-CALL: full FTS5 + rerank pipeline.

    Delegates to ``run_retrieve``. On raise, the W607-BI wrapper retries
    with ``_fts5_search_lexical_only`` so a tfidf/structural-rerank fault
    still emits raw FTS5 results.
    """
    return run_retrieve(
        conn,
        task_str,
        budget=budget,
        k=k,
        rerank=rerank,
        seed_files=seed_files,
    )


def _fts5_search_lexical_only(conn, task_str, *, budget, k, seed_files):
    """W607-BI degradation fallback: lexical-only retrieve (rerank=off).

    Used when ``_fts5_search_full`` raises during the structural-rerank
    phase. The lexical-only path keeps FTS5 ranking but skips the
    PageRank + clone-canonical blend.
    """
    return run_retrieve(
        conn,
        task_str,
        budget=budget,
        k=k,
        rerank="off",
        seed_files=seed_files,
    )


def _allocate_token_budget(raw_budget):
    """W607-BI substrate-CALL: token-budget int() coercion.

    Mirrors cmd_context W607-BF ``_allocate_budget``. Wrapping the
    coercion in a substrate boundary lets a non-coercible budget surface
    as a structured marker + fallback to 0 rather than crashing the
    retrieve call wholesale.
    """
    return int(raw_budget) if raw_budget else 0


def _scope_filter_candidates(candidates, scope_path):
    """W607-BI substrate-CALL: post-filter candidates by path prefix."""
    normalised_scope = scope_path.replace("\\", "/").rstrip("/")
    return [
        c for c in candidates if (c.get("file_path") or "").replace("\\", "/").startswith(normalised_scope + "/")
    ], normalised_scope


def _extract_dry_run_spans(candidates):
    """W607-BI substrate-CALL: strip span content for --dry-run mode."""
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
    return stripped


def _suggest_refinements(task: str, candidates: list[dict]) -> list[str]:
    """Generate 2-3 refined queries when confidence is low.

    Heuristics:
    1. **Drop common NL words** — "trace the login flow" → "login flow".
       Removes filler that diluted the lexical signal.
    2. **Suggest --seed-file anchor** — using the file of the
       highest-scoring candidate as a seed often promotes the right
       neighbours.
    3. **Pivot to roam search** — when the query contains a clear
       identifier (PascalCase / snake_case), exact-match search may
       beat structural retrieval.

    Returns a list of human-readable suggested commands.
    """
    if not task:
        return []
    suggestions: list[str] = []

    # 1. Drop NL filler — keep the noun-shaped tokens only.
    filler = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "of",
        "to",
        "in",
        "on",
        "for",
        "where",
        "what",
        "how",
        "find",
        "show",
        "tell",
        "me",
        "this",
        "that",
        "with",
        "by",
        "from",
        "and",
        "or",
        "as",
        "at",
    }
    words = task.split()
    kept = [w for w in words if w.lower().strip(".,!?") not in filler]
    if len(kept) < len(words) and len(kept) >= 1:
        tighter = " ".join(kept)
        if tighter.strip() and tighter != task:
            suggestions.append(f'roam retrieve "{tighter}"')

    # 2. Anchor on the top candidate's file.
    if candidates:
        top_file = (candidates[0].get("file_path") or candidates[0].get("file") or "").replace("\\", "/")
        if top_file:
            suggestions.append(f'roam retrieve "{task}" --seed-file {top_file}')

    # 3. If the query contains an identifier-shape, suggest exact search.
    import re

    ident_match = re.search(r"\b([A-Z][A-Za-z0-9]{2,}|[a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b", task)
    if ident_match:
        suggestions.append(f"roam search {ident_match.group(1)}")

    return suggestions[:3]


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
        tokens = []
        if isinstance(task, str):
            from roam.retrieve.seeds import extract_tokens

            tokens = extract_tokens(task)
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


@roam_capability(
    category="exploration",
    summary="Retrieve a ranked, budget-bounded set of code spans for a free-form task.",
    inputs=["task", "budget"],
    outputs=["candidates", "verdict"],
    examples=[
        'roam retrieve "is it safe to delete UserSession"',
        'roam retrieve "trace login flow" --seed-file src/auth.py',
        'roam --json retrieve "n+1 query in checkout"',
    ],
    tags=["exploration", "retrieval", "agent"],
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
    "--seed-file",
    "seed_files",
    multiple=True,
    type=str,
    help="Seed the rerank with one or more files (can be repeated). Falls back to inference when absent.",
)
@click.option(
    "--seed-files",
    "seed_files",
    multiple=True,
    type=str,
    hidden=True,
    help="Deprecated alias for --seed-file. Retained for backward compatibility.",
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
@click.option(
    "--scope",
    "scope_path",
    type=str,
    default=None,
    help=(
        "restrict candidates to files under this directory "
        "(repeat-friendly for monorepos, e.g. ``--scope src/api`` or "
        "``--scope packages/web/src``)."
    ),
)
@click.pass_context
def retrieve(ctx, task, budget, k, rerank, seed_files, dry_run, scope_path):
    """Return ranked code spans for a free-form task.

    Composes hybrid first-stage (FTS5) + structural reranker (PageRank +
    clone-canonical signal) + token-budget cap. Output includes
    justification tags so callers can see *why* each span ranked.

    \b
    Examples:
      roam retrieve "trace login flow"
      roam retrieve "where is the n+1?" --k 10
      roam retrieve "rate limit logic" --scope src/api/
      roam retrieve "token refresh" --budget 2000

    See also ``ask`` (recipe-driven dispatch — pick a recipe by intent),
    ``search`` (exact-name lookup), and ``search-semantic`` (embedding-
    based natural-language search).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cli_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    task_str = " ".join(task).strip()
    if not task_str:
        from roam.output.errors import EMPTY_INPUT, structured_usage_error

        raise structured_usage_error(EMPTY_INPUT, "task text cannot be empty")

    ensure_index()

    # W607-B: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the retrieve pipeline. cmd_retrieve does NOT call
    # the W605-plumbed substrate directly (search_stored / search_fts /
    # fts5_*); it invokes ``run_retrieve`` which delegates to
    # ``retrieve.pipeline._first_stage`` + ``retrieve.seeds.infer_seeds``.
    # Those use ad-hoc ``try/except sqlite3.OperationalError`` paths that
    # currently DO NOT thread warnings_out. The W607-B disclosure shape
    # therefore lives at the outer-guard boundary only: any uncaught
    # exception from ``run_retrieve`` (substrate corruption, missing
    # table, locked DB, malformed FTS5 query bubbling past the inner
    # ``except``) emits the marker
    # ``retrieve_pipeline_failed:<exc_class>:<detail>`` and the envelope
    # surfaces with empty candidates + partial_success=True. Mirrors the
    # cmd_search_semantic W607-A idiom (semantic_search_stored_failed:).
    # Empty bucket → byte-identical envelope (hash-stable).
    warnings_out: list[str] = []

    # W607-BI: ADDITIVE per-phase substrate-CALL marker plumbing on top of
    # the W607-B outer-guard above. cmd_retrieve is the CLAUDE.md-canonical
    # graph-aware FTS5 retrieve command — agents call it as their primary
    # free-form task lookup. A silent failure in any of its substrate
    # boundaries (config load, FTS5 search, tfidf/structural rerank,
    # token-budget allocation, confidence scoring, span extraction,
    # serialize) directly degrades agent productivity. W607-BI wraps each
    # substrate call so a raise becomes a structured
    # ``retrieve_<phase>_failed:<exc_class>:<detail>`` marker instead of
    # a wholesale Click traceback. The W607-B outer-guard remains for
    # ``retrieve_pipeline_failed:`` as a final safety net.
    #
    # Empty W607-BI bucket → byte-identical envelope (hash-stable).
    _w607bi_warnings_out: list[str] = []

    def _run_check_bi(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BI marker emission.

        On a clean call the result is returned as-is. On a documented
        recoverable substrate error, surface a
        ``retrieve_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607bi_warnings_out`` and return *default* — the envelope still
        emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except _RECOVERABLE_RETRIEVE_ERRORS as exc:
            _w607bi_warnings_out.append(f"retrieve_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    cfg = _run_check_bi("load_config", _load_retrieve_config, default={})
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
        effective_budget = _run_check_bi("allocate_token_budget", _allocate_token_budget, budget, default=0)
    elif cli_budget:
        effective_budget = _run_check_bi("allocate_token_budget", _allocate_token_budget, cli_budget, default=0)
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
        except sqlite3.Error:
            fts_count = -1
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        semantic_diag = _run_check_bi(
            "compute_semantic_coverage",
            _compute_semantic_coverage,
            conn,
            default={"embeddings": 0, "coverage_pct": 0.0, "ready": False},
        )
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

        # W607-BI fts5_search substrate-CALL with tfidf_rerank degradation
        # fallback. ``_fts5_search_full`` runs the full FTS5 + structural
        # rerank pipeline. If that raises (e.g. tfidf_rerank /
        # structural_score crash), we surface BOTH a
        # ``retrieve_fts5_search_failed:...`` marker AND retry via the
        # lexical-only fallback ``_fts5_search_lexical_only`` so agents
        # still receive raw FTS5 results rather than an empty envelope.
        # If the fallback also fails, the W607-B outer-guard takes over.
        # We capture the original exception via _captured_exceptions so
        # the W607-B outer-guard can preserve the root exception class
        # in its marker (rather than a synthetic placeholder).
        _captured_exceptions: dict = {}

        def _fts5_search_capture(*args, **kwargs):
            try:
                return _fts5_search_full(*args, **kwargs)
            except _RECOVERABLE_RETRIEVE_ERRORS as exc:
                _captured_exceptions["full"] = exc
                raise

        def _lexical_fallback_capture(*args, **kwargs):
            try:
                return _fts5_search_lexical_only(*args, **kwargs)
            except _RECOVERABLE_RETRIEVE_ERRORS as exc:
                _captured_exceptions["lexical"] = exc
                raise

        result = _run_check_bi(
            "fts5_search",
            _fts5_search_capture,
            conn,
            task_str,
            budget=effective_budget,
            k=effective_k,
            rerank=effective_rerank,
            seed_files=list(seed_files) or None,
            default=None,
        )
        if result is None:
            # W607-BI RERANK DEGRADATION: full pipeline raised. Retry
            # with rerank="off" so agents still receive raw FTS5 results
            # ordered by lexical relevance. Mark the fallback path.
            result = _run_check_bi(
                "tfidf_rerank",
                _lexical_fallback_capture,
                conn,
                task_str,
                budget=effective_budget,
                k=effective_k,
                seed_files=list(seed_files) or None,
                default=None,
            )
        if result is None:
            # Both the full pipeline AND the lexical-only fallback
            # raised. Fall through to the W607-B outer-guard shape so
            # the rest of the envelope still emits consistent fields.
            # Preserve the ORIGINAL exception's class + detail on the
            # outer-guard marker so downstream consumers can parse the
            # root cause.
            root_exc = _captured_exceptions.get("full") or _captured_exceptions.get("lexical")
            if root_exc is not None:
                warnings_out.append(f"retrieve_pipeline_failed:{type(root_exc).__name__}:{root_exc}")
            else:
                warnings_out.append(
                    "retrieve_pipeline_failed:RuntimeError:both fts5_search and tfidf_rerank fallback raised"
                )
            result = {
                "task": task_str,
                "rerank": effective_rerank,
                "seeds": [],
                "candidates": [],
                "total_candidates": 0,
                "budget": effective_budget,
                "budget_used": 0,
                "k": effective_k,
                "weights": {},
            }

    candidates = result["candidates"]
    if scope_path:
        # post-filter candidates by path prefix. Normalising
        # to forward slashes keeps Windows happy.
        scope_result = _run_check_bi(
            "scope_filter",
            _scope_filter_candidates,
            candidates,
            scope_path,
            default=(candidates, scope_path.replace("\\", "/").rstrip("/")),
        )
        candidates, normalised_scope = scope_result
        result["candidates"] = candidates
        result["scope_applied"] = normalised_scope
    if dry_run:
        # Strip span content so the agent sees what *would* be retrieved
        # without paying the token cost. Keeps location / score / why.
        candidates = _run_check_bi(
            "extract_spans",
            _extract_dry_run_spans,
            candidates,
            default=candidates,
        )
    confidence_pair = _run_check_bi(
        "compute_confidence_score",
        _retrieve_confidence_score,
        candidates,
        task_str,
        default=(0.0, "low"),
    )
    confidence_score, confidence = confidence_pair
    base_verdict = (
        f"{len(candidates)} span{'s' if len(candidates) != 1 else ''} "
        f"({result['budget_used']}/{result['budget']} tokens, "
        f"{len(result['seeds'])} seed{'s' if len(result['seeds']) != 1 else ''})"
        if candidates
        else "No candidates matched the task text"
    )
    # R.5 (dogfood ): "trace the login flow" against a repo
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
        refinements = (
            _run_check_bi(
                "suggest_refinements",
                _suggest_refinements,
                task_str,
                candidates,
                default=[],
            )
            if confidence_score < 0.40 and candidates
            else []
        )
        summary: dict = {
            "verdict": verdict,
            "low_confidence": confidence == "low",
            "confidence": confidence_score,
            "refinements": refinements,
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
        }
        # W607-B + W607-BI combined disclosure: merge BOTH buckets so
        # consumers see every marker (outer-guard ``retrieve_pipeline_*``
        # + per-substrate ``retrieve_<phase>_*``). Empty combined bucket
        # → byte-identical envelope (hash-stable).
        combined = list(warnings_out) + list(_w607bi_warnings_out)
        if combined:
            summary["warnings_out"] = list(combined)
            summary["partial_success"] = True
        # W607-BI serialize_envelope substrate-CALL: wrap to_json so
        # a serialize raise falls back to a minimal envelope rather
        # than crashing the entire retrieve call. Mirrors cmd_context
        # W607-BF ``_emit_envelope`` discipline.
        envelope_kwargs: dict = {
            "budget": effective_budget,
            "task": result["task"],
            "weights": result["weights"],
            "semantic_coverage": semantic_diag,
            "seeds": result["seeds"],
            "candidates": candidates,
        }
        if combined:
            # Top-level mirror — required for
            # ``_ALWAYS_PRESERVED_LIST_FIELDS`` survival through
            # the formatter's strip_list_payloads in
            # default-detail mode. summary mirror alone wouldn't
            # survive list-payload stripping.
            envelope_kwargs["warnings_out"] = list(combined)
        envelope = json_envelope("retrieve", summary=summary, **envelope_kwargs)
        text = _run_check_bi(
            "serialize_envelope",
            lambda: to_json(envelope),
            default=None,
        )
        if text is None:
            final_combined = list(warnings_out) + list(_w607bi_warnings_out)
            text = to_json(
                json_envelope(
                    "retrieve",
                    summary={
                        "verdict": "retrieve serialize failed",
                        "warnings_out": list(final_combined),
                        "partial_success": True,
                    },
                    budget=effective_budget,
                    warnings_out=list(final_combined),
                )
            )
        click.echo(text)
        return

    click.echo(f"VERDICT: {verdict}")
    if not candidates:
        click.echo()
        click.echo("Try `roam retrieve <task> --seed-file <path>` to anchor the search.")
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

    # 12.14 — auto-refine on low confidence. When the score crosses
    # below 0.40 we know the user is unlikely to find the answer in
    # the returned spans. Surface 2-3 refined queries the agent can
    # try next: dropping generic NL words, adding a seed-files
    # anchor, or pivoting to ``roam search`` for exact name match.
    if confidence_score < 0.40 and candidates:
        refined = _suggest_refinements(task_str, candidates)
        if refined:
            click.echo()
            click.echo("REFINE: low confidence — try a tighter query:")
            for r in refined:
                click.echo(f"  {r}")
