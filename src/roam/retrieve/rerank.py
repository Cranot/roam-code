"""A.1 — structural reranker for `roam retrieve`.

The reranker takes a list of first-stage candidates (from FTS5) plus a
seed map (from `seeds.infer_seeds` or caller-supplied `--seed-file`)
and produces a re-scored, re-ranked list. The score is a weighted blend
of structural signals that competitors with vector-only RAG cannot
compute:

* **alpha** — PageRank score (personalised on seeds when available, falling
  back to the persisted global PageRank in ``graph_metrics``).
* **epsilon** — clone-canonical boost. If a candidate has clone siblings
  in the persisted ``clone_pairs`` table (A.0), the candidate is tagged so
  the JSON envelope can surface "canonical / sibling-of" relationships.
* A retained lexical baseline from the FTS5 score so candidates with
  weak structural signal but strong textual relevance still rank.

`beta` (co-change) and `delta` (runtime hotspot) are applied in the
scoring blend below (`structural_score`); `gamma` (layer-distance) is
reserved in the weights dict for forward-compatibility but is not yet
wired through.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from roam.config import get_retrieve_config
from roam.db.connection import batched_in
from roam.graph.clone_detect import get_clone_siblings
from roam.graph.dark_matter import co_change_scores_to_seed_set_bulk
from roam.retrieve.seeds import extract_tokens
from roam.runtime.hotspots import runtime_score

#: Default lexical-baseline coefficient when config is unavailable.
#: Source of truth: ``[retrieve] lexical_baseline`` in ``.roam/config.toml``.
DEFAULT_LEXICAL_BASELINE = 0.5


def structural_score(
    conn: sqlite3.Connection,
    candidates: list[dict],
    seeds: dict[int, float],
    weights: dict[str, float],
    *,
    use_personalized: bool = True,
    config_root: Path | None = None,
    lexical_baseline: float | None = None,
    task: str = "",
) -> list[dict]:
    """Rerank *candidates* with structural signals.

    Parameters
    ----------
    conn:
        Open roam DB connection (read-only is fine).
    candidates:
        List of dicts with at least ``symbol_id`` and ``fts_score``. May
        also carry ``name``, ``qualified_name``, ``file_path``, ``kind``,
        ``line_start``, ``line_end``.
    seeds:
        Personalisation mass for PageRank. Empty dict triggers a
        global-PageRank lookup against ``graph_metrics``.
    weights:
        ``{alpha, beta, gamma, delta, epsilon}`` from
        ``roam.config.get_retrieve_weights``. Unrecognised keys are
        ignored.
    use_personalized:
        When ``True`` and ``seeds`` is non-empty, build the symbol graph
        and run :func:`personalized_pagerank`. Otherwise rely on the
        cached ``graph_metrics.pagerank`` column.

    Returns
    -------
    list[dict]
        ``candidates`` with two added fields per item: ``score`` (final
        sortable float) and ``justifications`` (dict of tag → value used
        by the JSON envelope).  Sorted descending by ``score``.
    """
    if not candidates:
        return []

    candidate_ids = {int(c["symbol_id"]) for c in candidates}
    signal_maps = _gather_signal_maps(
        conn,
        candidates,
        candidate_ids,
        seeds,
        task=task,
        use_personalized=use_personalized,
    )
    ctx = _build_scoring_context(
        candidates,
        weights,
        task=task,
        seeds=seeds,
        use_personalized=use_personalized,
        config_root=config_root,
        lexical_baseline=lexical_baseline,
        **signal_maps,
    )
    return _rank_candidates(candidates, ctx)


def _gather_signal_maps(
    conn: sqlite3.Connection,
    candidates: list[dict],
    candidate_ids: set[int],
    seeds: dict[int, float],
    *,
    task: str,
    use_personalized: bool,
) -> dict[str, object]:
    """Collect all independently-computed signal maps into one dict.

    Each reranking signal is computed by its own heuristic (path matching,
    query-shape detection, batched DB lookup). Gathering them in one place
    lets the scoring formula consume them cohesively without
    :func:`structural_score` needing to know how each signal is derived.
    """
    return {
        "path_token_boost": _path_token_boost(candidates, task),
        "rule_yaml_penalty": _rule_yaml_penalty(candidates, task),
        "test_file_penalty": _test_file_penalty(candidates, task),
        "cmd_companion_boost": _cmd_companion_boost(candidates),
        "async_query_boost": _async_query_boost(candidates, task, conn=conn),
        "recency_boost": _recency_boost(conn, candidates, task),
        "pr_scores": _pagerank_scores(conn, candidate_ids, seeds, use_personalized=use_personalized),
        "clone_tags": _clone_tags(conn, candidates),
        "cochange_scores": _cochange_scores(conn, candidate_ids, seeds),
        "runtime_scores": _runtime_scores(conn, candidate_ids),
        "semantic_scores": _semantic_scores(conn, candidate_ids, task),
    }


def _rank_candidates(candidates: list[dict], ctx: dict[str, object]) -> list[dict]:
    """Apply the scoring formula to every candidate and sort descending."""
    out = [_score_and_justify(c, ctx) for c in candidates]
    out.sort(key=lambda x: -x["score"])
    return out


def _build_scoring_context(
    candidates: list[dict],
    weights: dict[str, float],
    *,
    task: str,
    seeds: dict[int, float],
    use_personalized: bool,
    config_root: Path | None,
    lexical_baseline: float | None,
    pr_scores: dict[int, float],
    cochange_scores: dict[int, float],
    runtime_scores: dict[int, float],
    semantic_scores: dict[int, float],
    path_token_boost: dict[int, float],
    rule_yaml_penalty: dict[int, float],
    test_file_penalty: dict[int, float],
    cmd_companion_boost: dict[int, float],
    async_query_boost: dict[int, float],
    recency_boost: dict[int, float],
    clone_tags: dict[int, dict],
) -> dict[str, object]:
    """Return a read-only context that captures the scoring formula state.

    This separates query-dependent weight adaptation and denominator
    computation from the per-candidate scoring loop, so ``structural_score``
    can remain an orchestrator.
    """
    pr_max = max(pr_scores.values()) if pr_scores else 0.0
    fts_max = max((float(c.get("fts_score", 0.0)) for c in candidates), default=0.0)
    cochange_max = max(cochange_scores.values()) if cochange_scores else 0.0
    runtime_max = max(runtime_scores.values()) if runtime_scores else 0.0
    semantic_max = max(semantic_scores.values()) if semantic_scores else 0.0

    alpha = float(weights.get("alpha", 0.40))
    beta = float(weights.get("beta", 0.25))
    delta = float(weights.get("delta", 0.15))
    epsilon = float(weights.get("epsilon", 0.05))
    zeta = float(weights.get("zeta", 0.20))  # v12.2 semantic similarity

    if lexical_baseline is None:
        cfg = get_retrieve_config(config_root)
        lexical_baseline = float(cfg.get("lexical_baseline", DEFAULT_LEXICAL_BASELINE))

    # dogfood — implementation-style queries shift weight from structural
    # (alpha) toward lexical (lexical_baseline). See structural_score docs.
    impl_query = _is_impl_style_query(task)
    if impl_query:
        alpha = alpha * 0.70
        lexical_baseline = lexical_baseline * 1.20

    return {
        "alpha": alpha,
        "beta": beta,
        "delta": delta,
        "epsilon": epsilon,
        "zeta": zeta,
        "lexical_baseline": lexical_baseline,
        "pr_max": pr_max,
        "fts_max": fts_max,
        "cochange_max": cochange_max,
        "runtime_max": runtime_max,
        "semantic_max": semantic_max,
        "pr_scores": pr_scores,
        "cochange_scores": cochange_scores,
        "runtime_scores": runtime_scores,
        "semantic_scores": semantic_scores,
        "path_token_boost": path_token_boost,
        "rule_yaml_penalty": rule_yaml_penalty,
        "test_file_penalty": test_file_penalty,
        "cmd_companion_boost": cmd_companion_boost,
        "async_query_boost": async_query_boost,
        "recency_boost": recency_boost,
        "clone_tags": clone_tags,
        "seeds": seeds,
        "use_personalized": use_personalized,
    }


def _is_impl_style_query(task: str) -> bool:
    """Return True when *task* looks like an implementation lookup."""
    if not task:
        return False
    lowered_task = task.lower().strip()
    return any(lowered_task.startswith(p) for p in ("where ", "how ", "find ", "locate ", "show me "))


def _score_and_justify(c: dict, ctx: dict[str, object]) -> dict:
    """Compute the blended structural score and justification for one candidate."""
    signals, clone_info = _normalized_signals(c, ctx)
    score = _blend_score(signals, ctx)
    justifications = _justifications_for_signals(signals, clone_info, ctx)
    return {**c, "score": round(score, 4), "justifications": justifications}


def _normalized_signals(c: dict, ctx: dict[str, object]) -> tuple[dict[str, float], dict | None]:
    """Return normalized signal values and clone membership for one candidate."""
    sid = int(c["symbol_id"])
    pr_max = float(ctx["pr_max"])  # type: ignore[arg-type]
    fts_max = float(ctx["fts_max"])  # type: ignore[arg-type]
    cochange_max = float(ctx["cochange_max"])  # type: ignore[arg-type]
    runtime_max = float(ctx["runtime_max"])  # type: ignore[arg-type]
    semantic_max = float(ctx["semantic_max"])  # type: ignore[arg-type]

    pr_scores = ctx["pr_scores"]  # type: ignore[assignment]
    cochange_scores = ctx["cochange_scores"]  # type: ignore[assignment]
    runtime_scores = ctx["runtime_scores"]  # type: ignore[assignment]
    semantic_scores = ctx["semantic_scores"]  # type: ignore[assignment]
    clone_tags = ctx["clone_tags"]  # type: ignore[assignment]
    clone_info = clone_tags.get(sid)

    signals: dict[str, float] = {
        "pr_norm": (pr_scores.get(sid, 0.0) / pr_max) if pr_max > 0 else 0.0,
        "fts_norm": (float(c.get("fts_score", 0.0)) / fts_max) if fts_max > 0 else 0.0,
        "cochange_norm": cochange_scores.get(sid, 0.0) / cochange_max if cochange_max > 0 else 0.0,
        "runtime_norm": runtime_scores.get(sid, 0.0) / runtime_max if runtime_max > 0 else 0.0,
        "semantic_norm": semantic_scores.get(sid, 0.0) / semantic_max if semantic_max > 0 else 0.0,
        "clone_boost": float(ctx["epsilon"]) if clone_info else 0.0,  # type: ignore[arg-type]
        "path_token_boost": ctx["path_token_boost"].get(sid, 0.0),  # type: ignore[union-attr]
        "cmd_companion_boost": ctx["cmd_companion_boost"].get(sid, 0.0),  # type: ignore[union-attr]
        "async_query_boost": ctx["async_query_boost"].get(sid, 0.0),  # type: ignore[union-attr]
        "recency_boost": ctx["recency_boost"].get(sid, 0.0),  # type: ignore[union-attr]
        "rule_yaml_penalty": ctx["rule_yaml_penalty"].get(sid, 0.0),  # type: ignore[union-attr]
        "test_file_penalty": ctx["test_file_penalty"].get(sid, 0.0),  # type: ignore[union-attr]
    }
    return signals, clone_info


def _blend_score(signals: dict[str, float], ctx: dict[str, object]) -> float:
    """Blend normalized signals into a single score."""
    return (
        float(ctx["alpha"]) * signals["pr_norm"]  # type: ignore[arg-type]
        + float(ctx["beta"]) * signals["cochange_norm"]  # type: ignore[arg-type]
        + float(ctx["delta"]) * signals["runtime_norm"]  # type: ignore[arg-type]
        + float(ctx["zeta"]) * signals["semantic_norm"]  # type: ignore[arg-type]
        + float(ctx["lexical_baseline"]) * signals["fts_norm"]  # type: ignore[arg-type]
        + signals["clone_boost"]
        + signals["path_token_boost"]
        + signals["cmd_companion_boost"]
        + signals["async_query_boost"]
        + signals["recency_boost"]
        + signals["rule_yaml_penalty"]
        + signals["test_file_penalty"]
    )


def _justifications_for_signals(
    signals: dict[str, float],
    clone_info: dict | None,
    ctx: dict[str, object],
) -> dict[str, object]:
    """Build the human-readable justification dict from normalized signals."""
    justifications: dict[str, object] = {}
    if signals["pr_norm"] > 0:
        justifications["pagerank"] = round(signals["pr_norm"], 4)
        seeds = ctx["seeds"]  # type: ignore[assignment]
        use_personalized = ctx["use_personalized"]  # type: ignore[assignment]
        justifications["pagerank_kind"] = "personalized" if (seeds and use_personalized) else "global"
    if signals["fts_norm"] > 0:
        justifications["fts"] = round(signals["fts_norm"], 4)
    if signals["cochange_norm"] > 0:
        justifications["co_change"] = round(signals["cochange_norm"], 4)
    if signals["runtime_norm"] > 0:
        justifications["runtime_hot"] = round(signals["runtime_norm"], 4)
    if signals["semantic_norm"] > 0:
        justifications["semantic"] = round(signals["semantic_norm"], 4)
    if clone_info:
        justifications["clone_cluster"] = clone_info["cluster_id"]
        justifications["clone_siblings"] = clone_info["sibling_count"]
    return justifications


def _path_token_boost(candidates: list[dict], task: str) -> dict[int, float]:
    """Per-candidate boost for files whose path contains a task token.

    Splits the path into components (``/`` and ``_`` and ``.``) and
    intersects with the task tokens. A file like
    ``src/roam/languages/ruby_lang.py`` matches "ruby" through the
    ``ruby_lang`` component for the query "Ruby Tier 1 language
    extractor", while ``src/roam/languages/aura_lang.py`` doesn't —
    even though both score similarly on lexical/structural signal.

    Magnitude is bounded at ~0.15 so it can lift relevant files into
    top-K without dominating the structural blend.
    """
    lowered = _task_path_tokens(task)
    if not lowered:
        return {}

    out: dict[int, float] = {}
    for c in candidates:
        scored = _boost_candidate_when_path_names_task_token(c, lowered)
        if scored is None:
            continue
        sid, boost = scored
        out[sid] = boost
    return out


def _boost_candidate_when_path_names_task_token(candidate: dict, tokens: set[str]) -> tuple[int, float] | None:
    """Return a bounded lift only when a candidate path names a task token."""
    sid = int(candidate.get("symbol_id") or 0)
    path = (candidate.get("file_path") or candidate.get("file") or "").lower()
    if not path or not sid:
        return None
    hits = _token_hits(tokens, _path_parts(path))
    if not hits:
        return None
    return sid, _boost_for_hit_count(len(hits))


def _task_path_tokens(task: str) -> set[str]:
    """Return lowercased task tokens eligible for path matching."""
    if not task:
        return set()
    tokens = extract_tokens(task)
    return {t.lower() for t in tokens if len(t) >= 3}


def _path_parts(path: str) -> set[str]:
    """Normalize ``path`` and split it into searchable components.

    Splits on ``/``, ``\\``, ``.``, ``_`` and ``-`` so that
    ``src/roam/languages/ruby_lang.py`` becomes ``{"src", "roam",
    "languages", "ruby", "lang", "py"}`` (components under three
    characters are discarded).
    """
    parts: set[str] = set()
    for piece in path.replace("\\", "/").split("/"):
        for sub in piece.replace(".", " ").replace("_", " ").replace("-", " ").split():
            if len(sub) >= 3:
                parts.add(sub)
    return parts


def _token_hits(tokens: set[str], parts: set[str]) -> set[str]:
    """Return the subset of ``tokens`` that match at least one path part.

    Prefix-match either direction: query token "clone" matches path
    component "clones"; query token "extractor" matches path
    component "extractors". Cap both sides at length 4 so we don't
    over-match short tokens.
    """
    hits: set[str] = set()
    for token in tokens:
        for part in parts:
            if (
                part == token
                or (len(token) >= 4 and part.startswith(token))
                or (len(part) >= 4 and token.startswith(part))
            ):
                hits.add(token)
                break
    return hits


def _boost_for_hit_count(hit_count: int) -> float:
    """Map the number of matched tokens to a bounded boost magnitude.

    Up to 0.15 total: 0.075 for the first hit, +0.04 for each additional
    hit, capped.
    """
    return min(0.15, 0.075 + 0.04 * (hit_count - 1))


def _rule_yaml_penalty(candidates: list[dict], task: str) -> dict[int, float]:
    """Penalise rule-corpus YAML files for implementation-style queries.

    Rule files like ``rules/community/correctness/COR-*.yaml`` match
    tokens like "clone", "match", "implement" because they are static-
    analysis rules *about* those concepts — but they are never the
    answer to "where is X implemented". The showed 6/20 top-K slots eaten by rule YAMLs for a single query.

    Heuristic: only demote when the query looks like an
    implementation question (starts with "where", "how", or
    "find"/"locate"). Queries that mention "rule", "yaml", or
    "lint" should *not* demote — the user wants the rule itself.

    Magnitude: -0.20 (mirrors path_token_boost's max). Empirically
    enough to displace the rule rows without entirely banning them.
    """
    if not task:
        return {}
    lowered_task = task.lower().strip()
    impl_question = any(lowered_task.startswith(prefix) for prefix in ("where ", "how ", "find ", "locate "))
    if not impl_question:
        return {}
    if any(word in lowered_task for word in ("rule", "yaml", "lint", "policy")):
        return {}

    out: dict[int, float] = {}
    for c in candidates:
        sid = int(c.get("symbol_id") or 0)
        path = (c.get("file_path") or c.get("file") or "").replace("\\", "/").lower()
        if not sid or not path:
            continue
        if path.startswith("rules/") or "/rules/community/" in path or path.endswith(".yaml") or path.endswith(".yml"):
            out[sid] = -0.20
    return out


#: Directory prefixes that mark a test path (rerank-local semantics —
#: narrower than :func:`roam.commands.changed_files.is_test_file` by
#: design; rerank's penalty has been tuned against the 30-task bench
#: with THIS exact pattern set and broadening it changes recall numbers).
_RERANK_TEST_DIR_PREFIXES = ("tests/", "test/", "spec/")

#: Directory fragments that mark a test path (substring match).
_RERANK_TEST_DIR_FRAGMENTS = ("/tests/", "/test/", "/spec/", "/__tests__/")

#: Basename suffixes / fragments that mark a test file (rerank-local).
_RERANK_TEST_BASENAME_SUFFIXES = ("_test.py", "_test.go", "_test.rs")
_RERANK_TEST_BASENAME_FRAGMENTS = (".test.", ".spec.")


def _is_test_path(path: str) -> bool:
    """Return True when ``path`` looks like a test file under rerank semantics.

    Callers MUST pass an already-normalised path: forward slashes only
    and lower-cased (rerank's call-sites pre-normalise at the candidate
    boundary). This helper consolidates the test-path detection that
    was inlined at the call-site in :func:`_test_file_penalty`.

    Deliberately narrower than
    :func:`roam.commands.changed_files.is_test_file` — broadening would
    add ``conftest.py``, ``_test.java``, etc. and reshape the test-vs-
    impl ranking trade-off that was tuned against the 30-task bench
    (see ``Magnitude: -0.18`` rationale in :func:`_test_file_penalty`).
    """
    if not path:
        return False
    if any(path.startswith(prefix) for prefix in _RERANK_TEST_DIR_PREFIXES):
        return True
    if any(fragment in path for fragment in _RERANK_TEST_DIR_FRAGMENTS):
        return True
    basename = path.rsplit("/", 1)[-1]
    if basename.startswith("test_"):
        return True
    if any(basename.endswith(suffix) for suffix in _RERANK_TEST_BASENAME_SUFFIXES):
        return True
    if any(fragment in basename for fragment in _RERANK_TEST_BASENAME_FRAGMENTS):
        return True
    return False


def _test_file_penalty(candidates: list[dict], task: str) -> dict[int, float]:
    """Demote test-file candidates for implementation-style queries. dogfood: a query like *"where is the patch
    verifier with clones-not-edited check"* surfaced
    ``test_verify_patch_match`` (a test) as the top result and the
    actual ``check_clones_not_edited`` implementation at #4. The
    structural reranker correctly flagged tests as high-fan-in /
    high-PageRank (every test imports the conftest fixtures and the
    function under test), but for "where is X" queries the user wants
    the IMPLEMENTATION, not the test.

    Heuristic: same shape as ``_rule_yaml_penalty`` —

    * only fires for implementation-style queries (start with
      ``where``, ``how``, ``find``, ``locate``).
    * but skips when the query explicitly mentions tests or
      assertions ("test", "spec", "fixture", "conftest", "assert",
      "expect") — the user wants tests in that case.
    * picks up paths that look like tests (``tests/``, ``test/``,
      ``spec/``, ``__tests__/``, basename matching ``test_*`` /
      ``*_test.py`` / ``*.test.*`` / ``*.spec.*``).

    Magnitude: -0.18 — tuned against the 30-task bench to keep
    legitimate test answers in top-20 (the bench expects tests as
    co-answers for "where is X" queries) while still pushing
    high-PR test fixtures below same-token implementations at
    top-5/10. Stronger penalties (-0.25) regressed recall@20 even
    while improving recall@5; -0.18 was the sweet spot.
    """
    if not task:
        return {}
    lowered_task = task.lower().strip()
    impl_question = any(lowered_task.startswith(prefix) for prefix in ("where ", "how ", "find ", "locate "))
    if not impl_question:
        return {}
    # User explicitly wants tests — leave the ranking alone.
    if any(word in lowered_task for word in ("test", "spec", "fixture", "conftest", "assert", "expect")):
        return {}

    out: dict[int, float] = {}
    for c in candidates:
        sid = int(c.get("symbol_id") or 0)
        path = (c.get("file_path") or c.get("file") or "").replace("\\", "/").lower()
        if not sid or not path:
            continue
        if _is_test_path(path):
            out[sid] = -0.18
    return out


_ASYNC_QUERY_TOKENS = frozenset(
    {
        "async",
        "await",
        "awaitable",
        "coroutine",
        "asyncio",
        "loop",
        "concurrent",
        "non-blocking",
        "nonblocking",
        "aiohttp",
        "httpx",
        "asyncpg",
        "aiofiles",
    }
)


def _async_query_boost(candidates: list[dict], task: str, *, conn=None) -> dict[int, float]:
    """Boost ``is_async=True`` candidates when the query talks about
    async / await / coroutines.

    Reads ``symbols.is_async`` for the candidate set in one batch
    query. Cheap because the candidate set is bounded (<300).
    Magnitude 0.10 — matches ``cmd_companion_boost`` so async
    candidates rise into top-K when the query is async-shaped without
    overwhelming structurally-stronger non-async candidates.
    """
    if not task or not candidates or conn is None:
        return {}
    lowered = task.lower()
    if not any(tok in lowered for tok in _ASYNC_QUERY_TOKENS):
        return {}

    sids = [int(c.get("symbol_id") or 0) for c in candidates if c.get("symbol_id") is not None]
    if not sids:
        return {}
    out: dict[int, float] = {}
    try:
        rows = batched_in(
            conn,
            "SELECT id FROM symbols WHERE id IN ({ph}) AND is_async = 1",
            sids,
        )
    except sqlite3.Error:
        return {}
    for r in rows:
        out[int(r[0])] = 0.10
    return out


_HISTORICAL_QUERY_TOKENS = frozenset(
    {
        "old",
        "legacy",
        "deprecated",
        "history",
        "historical",
        "archive",
        "archived",
        "ancient",
        "removed",
        "former",
    }
)


def _recency_boost(conn: sqlite3.Connection, candidates: list[dict], task: str) -> dict[int, float]:
    """Boost candidates whose file was recently edited.

    Hypothesis: when a developer asks "where is X?" they're usually
    asking about code they're actively working on. Recent edits
    correlate with relevance for the impl-style queries that
    dominate the workload.

    Magnitude: up to +0.05 for files edited *today*, decaying
    linearly to zero at 14 days. Smaller than ``async_query_boost``
    (0.10) because the synthetic 30-task bench couldn't validate a
    larger recency tilt — the bench labels treat all expected files
    as equal regardless of mtime, so a strong recency lift slightly
    rearranges co-equal answers and shows as bench-neutral. The
    magnitude is tuned to be bench-neutral while still nudging
    real-world impl queries toward currently-active code.

    Suppressed when the query is shaped like a historical question
    ("where was the *old* auth handler", "deprecated routes",
    "legacy code") — recent edits are the *opposite* of what the
    user wants in those cases.

    The signal comes from ``MAX(git_commits.timestamp)`` per file
    via ``git_file_changes``; cheap once per call (one batched
    query for the candidate set, no per-candidate I/O).
    """
    if not candidates:
        return {}
    # Suppress for historical queries — recent edits are anti-signal.
    if task:
        lowered = task.lower()
        if any(tok in lowered for tok in _HISTORICAL_QUERY_TOKENS):
            return {}

    # Resolve candidate file_ids. Many candidates carry a path but
    # not the file_id; we batch-resolve through ``files.path``.
    paths = list({(c.get("file_path") or c.get("file") or "") for c in candidates})
    paths = [p for p in paths if p]
    if not paths:
        return {}
    try:
        path_rows = batched_in(
            conn,
            "SELECT id, path FROM files WHERE path IN ({ph})",
            paths,
        )
    except sqlite3.OperationalError:
        return {}
    file_id_by_path = {r["path"]: int(r["id"]) for r in path_rows}
    file_ids = list(file_id_by_path.values())
    if not file_ids:
        return {}

    # Latest commit timestamp per file. Single batched query — no
    # per-candidate fan-out. Files with no git history are absent
    # from the result and thus get no boost.
    try:
        ts_rows = batched_in(
            conn,
            """
            SELECT gfc.file_id, MAX(gc.timestamp) AS latest
            FROM git_file_changes gfc
            JOIN git_commits gc ON gfc.commit_id = gc.id
            WHERE gfc.file_id IN ({ph})
            GROUP BY gfc.file_id
            """,
            file_ids,
        )
    except sqlite3.OperationalError:
        return {}
    if not ts_rows:
        return {}
    latest_by_file_id = {int(r["file_id"]): float(r["latest"] or 0) for r in ts_rows}

    import time

    now = time.time()
    out: dict[int, float] = {}
    for c in candidates:
        sid = int(c.get("symbol_id") or 0)
        path = c.get("file_path") or c.get("file") or ""
        if not sid or not path:
            continue
        fid = file_id_by_path.get(path)
        if fid is None:
            continue
        latest = latest_by_file_id.get(fid)
        if latest is None or latest <= 0:
            continue
        age_days = (now - latest) / 86400.0
        if age_days < 0 or age_days > 14:
            continue
        # Linear decay: 0d → 0.05, 14d → 0.
        boost = 0.05 * max(0.0, (14.0 - age_days) / 14.0)
        if boost > 0:
            out[sid] = boost
    return out


def _cmd_companion_boost(candidates: list[dict]) -> dict[int, float]:
    """Lift ``commands/cmd_FOO.py`` when a *strongly-ranked* candidate
    has ``FOO`` as a path component.

    The CLI wrapper file is conceptually paired with its engine
    module, but the two share no path tokens. The dogfood notes
    2026-05-01 confirmed cmd-companion files systematically miss
    top-K. This boost lifts them only when the engine module is
    *itself* a strong match — using the strongest companion's
    fts_score to scale the boost.

    Selectivity: a +0.25 fixed boost lifts every cmd_*.py whenever
    *any* candidate matches the stem, which over-promotes (e.g.
    cmd_verify_imports, cmd_fleet, cmd_verify all surface for a
    "patch verifier" query). Scaling by the companion's normalised
    fts_score avoids this — weak companion → weak boost.

    Boost magnitude: ``0.05 + 0.20 * (companion_fts_norm)``, capped
    at 0.25. A companion at the top of the FTS distribution gets
    the full 0.25; one at the bottom gets ~0.05 (still better than
    nothing for the legitimate cmd_FOO.py companions, while leaving
    the unrelated ones at the noise floor).
    """
    if not candidates:
        return {}

    fts_max = max((float(c.get("fts_score") or 0.0) for c in candidates), default=0.0)
    if fts_max <= 0:
        return {}

    component_strength, cmd_candidates = _collect_cmd_companion_evidence(candidates)
    out: dict[int, float] = {}
    for sid, stem in cmd_candidates:
        best_fts = _strongest_evidence_for_cmd_wrapper(stem, component_strength)
        if best_fts <= 0:
            continue
        norm = best_fts / fts_max
        out[sid] = min(0.25, 0.05 + 0.20 * norm)
    return out


def _collect_cmd_companion_evidence(candidates: list[dict]) -> tuple[dict[str, float], list[tuple[int, str]]]:
    """Collect wrapper stems and non-wrapper token strengths separately.

    This keeps the selectivity-vs-recall rule explicit: command wrappers
    are boost targets, while non-command path tokens are the evidence
    that a wrapper deserves the lift.
    """
    component_strength: dict[str, float] = {}
    cmd_candidates: list[tuple[int, str]] = []
    for c in candidates:
        path = _path_for_cmd_companion_matching(c)
        if not path:
            continue
        sid = int(c.get("symbol_id") or 0)
        if not sid:
            continue
        stem = _cmd_boost_target_stem(path)
        if stem:
            cmd_candidates.append((sid, stem))
            continue
        _remember_tokens_that_can_lift_cmd_wrapper(
            component_strength,
            path,
            float(c.get("fts_score") or 0.0),
        )
    return component_strength, cmd_candidates


def _path_for_cmd_companion_matching(candidate: dict) -> str:
    """Return the path form used by cmd-companion matching."""
    return (candidate.get("file_path") or candidate.get("file") or "").replace("\\", "/").lower()


def _cmd_boost_target_stem(path: str) -> str | None:
    """Return the ``FOO`` part from ``cmd_FOO.py`` paths."""
    basename = path.rsplit("/", 1)[-1]
    if not basename.startswith("cmd_") or not basename.endswith(".py"):
        return None
    stem = basename[len("cmd_") : -len(".py")]
    return stem or None


def _remember_tokens_that_can_lift_cmd_wrapper(component_strength: dict[str, float], path: str, fts: float) -> None:
    """Record the strongest FTS evidence for each companion token."""
    for token in _tokens_that_can_justify_cmd_wrapper(path):
        if fts > component_strength.get(token, 0.0):
            component_strength[token] = fts


def _tokens_that_can_justify_cmd_wrapper(path: str) -> Iterable[str]:
    """Yield path tokens eligible to justify a cmd-wrapper boost."""
    for piece in path.split("/"):
        piece_clean = piece.replace(".py", "").replace(".js", "").replace(".ts", "")
        for token in piece_clean.replace("_", " ").replace("-", " ").split():
            if len(token) >= 4:
                yield token


def _strongest_evidence_for_cmd_wrapper(stem: str, component_strength: dict[str, float]) -> float:
    """Return the strongest non-wrapper FTS score matching a command stem."""
    best_fts = 0.0
    for component, strength in component_strength.items():
        if _component_can_justify_cmd_stem(stem, component) and strength > best_fts:
            best_fts = strength
    return best_fts


def _component_can_justify_cmd_stem(stem: str, component: str) -> bool:
    """Match exact names plus long-prefix variants without broad short stems."""
    return (
        component == stem
        or (len(stem) >= 4 and component.startswith(stem))
        or (len(component) >= 4 and stem.startswith(component))
    )


def _cochange_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    seeds: dict[int, float],
) -> dict[int, float]:
    """Per-candidate β contribution: max co-change score against any seed.

    No seeds → empty dict (β contributes 0 to every candidate). The
    reranker normalises across the candidate set so absolute magnitudes
    don't matter — only the *relative* ordering of co-change links.
    """
    if not seeds:
        return {}
    seed_ids = list(seeds.keys())
    cand_list = list(candidate_ids)
    if not cand_list:
        return {}
    # Bulk path: pre-fetch the whole (candidate-file x seed-file) co-change
    # matrix in a bounded number of SQL round-trips, then score in-memory.
    # Output-identical to the old per-candidate co_change_score_to_seed_set
    # loop; replaces the latent O(C x S x 2 SQL) N+1 (W: rerank β fix).
    try:
        return co_change_scores_to_seed_set_bulk(conn, cand_list, seed_ids)
    except sqlite3.OperationalError:
        # Missing git_cochange / file_stats / symbols table — the old loop
        # treated this as score 0.0 for every candidate (empty β dict).
        return {}


def _runtime_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
) -> dict[int, float]:
    """Per-candidate δ contribution: runtime-importance score in [0, 1].

    Scans ``runtime_stats`` once per call; symbols without ingested
    traces are simply absent from the result (β/δ are additive — no
    runtime data means δ contributes 0, never negative).
    """
    cand_set = set(candidate_ids)
    if not cand_set:
        return {}
    out: dict[int, float] = {}
    for sid in cand_set:
        try:
            score = runtime_score(conn, sid)
        except sqlite3.OperationalError:
            score = 0.0
        if score > 0:
            out[sid] = score
    return out


def _semantic_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    task: str,
) -> dict[int, float]:
    """Per-candidate ζ contribution: semantic similarity to *task*, in [0, 1].

    Returns an empty dict (ζ contributes 0 to every candidate) when:

    * The ``symbol_embeddings`` table is absent or empty.
    * The optional ``[semantic]`` extras (onnxruntime + tokenizers) aren't
      importable.
    * The model files aren't on disk.

    This is the v12.2 fifth signal. The reranker normalises across the
    candidate set, so absolute magnitudes don't matter — only the
    relative ordering of semantic similarity contributes.
    """
    if not task or not task.strip():
        return {}
    cand_set = list(candidate_ids)
    if not cand_set:
        return {}
    try:
        from roam.retrieve.semantic import semantic_score
    except ImportError:
        return {}
    try:
        return semantic_score(conn, cand_set, task)
    except sqlite3.Error:
        return {}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _pagerank_scores(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[int],
    seeds: dict[int, float],
    *,
    use_personalized: bool,
) -> dict[int, float]:
    """Return ``{symbol_id: pagerank}`` for the candidate set.

    With seeds and personalisation on, builds the symbol graph and runs
    :func:`personalized_pagerank`. Otherwise pulls cached scores from
    ``graph_metrics``. Either path returns scores only for the candidate
    ids — the caller never sees the full graph distribution.
    """
    candidate_set = set(candidate_ids)
    if not candidate_set:
        return {}

    if seeds and use_personalized:
        try:
            from roam.graph.builder import build_symbol_graph
            from roam.graph.pagerank import personalized_pagerank

            G = build_symbol_graph(conn)
            full = personalized_pagerank(G, seeds)
        except (ImportError, sqlite3.Error):
            full = {}
        scores = {sid: full.get(sid, 0.0) for sid in candidate_set}
        if any(v > 0 for v in scores.values()):
            return scores
        # Personalised PR returned nothing useful — fall through to cached.

    # batched_in() chunks at SQLITE_MAX_VARIABLE_NUMBER (default 999) — required
    # by CLAUDE.md when callers may pass --k > 80 (top_n grows to 1000 placeholders).
    rows = batched_in(
        conn,
        "SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
        list(candidate_set),
    )
    return {int(row[0]): float(row[1]) for row in rows}


def _clone_tags(conn: sqlite3.Connection, candidates: list[dict]) -> dict[int, dict]:
    """Resolve clone membership per candidate.

    Returns ``{symbol_id: {cluster_id, sibling_count}}`` for every
    candidate with at least one persisted clone sibling.
    """
    out: dict[int, dict] = {}
    for c in candidates:
        file_path = c.get("file_path")
        name = c.get("name")
        if not file_path or not name:
            continue
        siblings = get_clone_siblings(conn, file_path, name)
        if not siblings:
            continue
        # All siblings of one symbol share its cluster (by construction
        # in store_clones); pick the first non-null cluster_id.
        cluster_id = next(
            (s["cluster_id"] for s in siblings if s.get("cluster_id") is not None),
            None,
        )
        out[int(c["symbol_id"])] = {
            "cluster_id": cluster_id,
            "sibling_count": len(siblings),
        }
    return out
