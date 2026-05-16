"""Detect semantically duplicate functions via structural similarity."""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import Counter, defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.index.file_roles import is_test as _is_test
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

# W165 — Test / fixture / production bucketing for duplicates findings.
#
# The 212-eval dogfood corpus (internal/dogfood/SYNTHESIS-2026-05-12.md)
# surfaced 713/853 (84%) duplicate clusters living in tests/ — intentional
# pytest parametrize patterns that drown real refactor candidates. The fix
# is symmetric with W165 on the clones detector: classify each cluster into
# {production, test_intentional, mixed}, attach the bucket to every
# emitted ``findings`` row, surface per-bucket counts in the verdict line,
# and let agents filter via ``--exclude-tests`` / ``--exclude-fixtures``.
#
# Mixed is preserved through ``--exclude-tests`` (one side src, one side
# test ⇒ potential test-leakage signal worth keeping).

_FIXTURE_PATTERN = re.compile(r"(^|/)(fixtures?|test_fixtures|testdata|test_data)(/|$)")


def _is_fixture_path(path: str) -> bool:
    """True if *path* lives under a fixture / testdata directory.

    Path-only (no I/O). Matches ``fixtures/``, ``test_fixtures/``,
    ``testdata/``, ``test_data/`` at any depth. The bigger overlap on
    roam-code is ``tests/fixtures/`` which is also caught by
    ``_is_test``; both flags are kept independent so callers can filter
    just fixtures (parametrize-corpora) without filtering tests.
    """
    if not path:
        return False
    return _FIXTURE_PATTERN.search(path.replace("\\", "/")) is not None


def _role_bucket_for_files(files: list[str]) -> str:
    """Classify a multi-member cluster by the role mix of its files.

    All-test (is_test ∪ fixture) ⇒ test_intentional; all-source ⇒
    production; mixed ⇒ mixed. Empty input ⇒ production (the caller
    already filtered empty clusters).
    """
    if not files:
        return "production"
    test_sides = [bool(f and (_is_test(f) or _is_fixture_path(f))) for f in files]
    if all(test_sides):
        return "test_intentional"
    if any(test_sides):
        return "mixed"
    return "production"


# W136 (W93 follow-up): duplicates is the next detector migrating onto the
# central findings registry, alongside the already-migrated ``clones``
# (W95), ``dead`` (W99), ``complexity`` (W102), and ``smells`` (W109).
# Where ``clones`` compares AST subtree hashes (Type-2 textual clones),
# ``duplicates`` clusters functions by weighted similarity of AST-derived
# metrics read from the DB (``symbol_metrics`` + ``math_signals`` +
# ``graph_metrics``). The two detectors emit under distinct
# ``source_detector`` values — ``"clones"`` vs ``"duplicates"`` — so the
# registry can tell their findings apart.
#
# Bump this stamp when the weight formula in :func:`_compute_similarity`
# or the bucketing / pre-filter shape changes meaningfully — both affect
# which clusters survive thresholding and therefore which findings emit.
DUPLICATES_DETECTOR_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Name tokenization
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")


def _name_tokens(name: str) -> set[str]:
    """Split a symbol name into lowercase token set (camelCase/snake_case)."""
    parts = _SPLIT_RE.findall(name)
    tokens = {p.lower() for p in parts if len(p) >= 2}
    # Also split on underscores for snake_case
    for part in name.split("_"):
        sub = _SPLIT_RE.findall(part)
        for s in sub:
            if len(s) >= 2:
                tokens.add(s.lower())
    return tokens


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity of two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Structural similarity
# ---------------------------------------------------------------------------


def _body_structure_vector(row) -> dict:
    """Extract a structural fingerprint from symbol_metrics + math_signals."""
    return {
        "line_count": row["line_count"] or 0,
        "param_count": row["param_count"] or 0,
        "nesting_depth": row["nesting_depth"] or 0,
        "cognitive_complexity": row["cognitive_complexity"] or 0,
        "return_count": row["return_count"] or 0,
        "bool_op_count": row["bool_op_count"] or 0,
        "callback_depth": row["callback_depth"] or 0,
        "loop_depth": row["loop_depth"] or 0,
        "has_nested_loops": row["has_nested_loops"] or 0,
        "has_self_call": row["has_self_call"] or 0,
    }


def _body_similarity(a: dict, b: dict) -> float:
    """Compare two body structure vectors.

    Uses normalized absolute difference per feature, averaged.
    """
    features = list(a.keys())
    if not features:
        return 0.0
    total = 0.0
    for f in features:
        va, vb = a[f], b[f]
        max_val = max(abs(va), abs(vb), 1)
        total += 1.0 - abs(va - vb) / max_val
    return total / len(features)


def _param_similarity(count_a: int, count_b: int) -> float:
    """Similarity of parameter counts."""
    if count_a == 0 and count_b == 0:
        return 1.0
    max_p = max(count_a, count_b, 1)
    return 1.0 - abs(count_a - count_b) / max_p


def _signature_similarity(sig_a: str | None, sig_b: str | None) -> float:
    """Compare function signatures (return type tokens if available)."""
    if not sig_a and not sig_b:
        return 1.0
    if not sig_a or not sig_b:
        return 0.3  # one has signature, other doesn't -- partial match
    tokens_a = _name_tokens(sig_a)
    tokens_b = _name_tokens(sig_b)
    return _jaccard(tokens_a, tokens_b)


def _safe_get(row, key, default=None):
    """Safely get a value from a sqlite3.Row or dict."""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError):
        return default


def _compute_similarity(row_a, row_b) -> float:
    """Compute weighted structural similarity between two symbol rows.

    Weights: body_structure(0.4) + params(0.25) + name(0.2) + signature(0.15)
    """
    body_a = _body_structure_vector(row_a)
    body_b = _body_structure_vector(row_b)
    body_sim = _body_similarity(body_a, body_b)

    param_sim = _param_similarity(
        row_a["param_count"] or 0,
        row_b["param_count"] or 0,
    )

    name_sim = _jaccard(
        _name_tokens(row_a["name"]),
        _name_tokens(row_b["name"]),
    )

    sig_sim = _signature_similarity(
        _safe_get(row_a, "signature"),
        _safe_get(row_b, "signature"),
    )

    return 0.40 * body_sim + 0.25 * param_sim + 0.20 * name_sim + 0.15 * sig_sim


# ---------------------------------------------------------------------------
# Union-Find for clustering
# ---------------------------------------------------------------------------


class _UnionFind:
    """Disjoint-set / Union-Find data structure."""

    def __init__(self):
        self.parent: dict[int, int] = {}
        self.rank: dict[int, int] = {}

    def find(self, x: int) -> int:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def clusters(self) -> dict[int, list[int]]:
        """Return {root_id: [member_ids]}."""
        groups: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            groups[self.find(x)].append(x)
        return dict(groups)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "get",
    "set",
    "use",
    "handle",
    "on",
    "is",
    "has",
    "can",
    "do",
    "the",
    "for",
    "with",
    "from",
    "init",
    "new",
    "run",
    "to",
    "a",
}


def _infer_pattern(names: list[str]) -> str:
    """Infer a shared behavioral pattern from function names."""
    all_tokens: list[set[str]] = []
    for name in names:
        tokens = _name_tokens(name) - _STOP_WORDS
        all_tokens.append(tokens)

    if not all_tokens:
        return "similar structure"

    # Find tokens common to at least 2 functions
    token_freq: Counter = Counter()
    for ts in all_tokens:
        for t in ts:
            token_freq[t] += 1

    common = [t for t, c in token_freq.most_common() if c >= 2]
    # Find tokens unique to each
    unique_per = []
    for ts in all_tokens:
        unique = ts - set(common)
        if unique:
            unique_per.append(sorted(unique)[0])

    if common:
        verb = common[0] if common else "process"
        variants = ", ".join(unique_per[:4]) if unique_per else "variants"
        return f"shared {verb} logic with {variants}"

    return "similar control flow structure"


def _suggest_refactor(names: list[str], pattern: str) -> str:
    """Generate a refactoring suggestion for a duplicate cluster."""
    all_tokens: list[set[str]] = []
    for name in names:
        tokens = _name_tokens(name) - _STOP_WORDS
        all_tokens.append(tokens)

    # Find the common verb/action
    token_freq: Counter = Counter()
    for ts in all_tokens:
        for t in ts:
            token_freq[t] += 1

    common = [t for t, c in token_freq.most_common(3) if c >= 2]

    if common:
        base_name = "_".join(common[:2])
        return f"Extract common logic into a generic {base_name}() helper"

    return "Extract shared logic into a parameterized helper function"


# ---------------------------------------------------------------------------
# W136 — findings registry emit
# ---------------------------------------------------------------------------


def _duplicates_cluster_finding_id(member_qnames: list[str]) -> str:
    """Stable, deterministic finding id for one duplicate cluster.

    A cluster is identified by the SORTED tuple of its member qualified
    names. Sorting makes the id independent of the order Union-Find walks
    the members in — the same set of duplicates always hashes to the
    same id, so re-running ``roam duplicates --persist`` upserts in
    place rather than churning rows.

    We hash the joined sorted qnames (rather than a raw set repr) to keep
    the id short and stable across Python versions / set-ordering quirks.
    """
    raw = "|".join(sorted(qn for qn in member_qnames if qn))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"duplicates:cluster:{digest}"


def _emit_duplicates_findings(
    conn: sqlite3.Connection,
    clusters: list[dict],
    source_version: str,
) -> int:
    """Mirror each duplicate cluster into the central findings registry.

    Returns the count of finding rows written (one per cluster). The
    caller is responsible for opening ``conn`` writable and committing;
    :func:`emit_finding` does not commit on its own.

    Confidence tier rationale: every duplicate cluster surfaces from
    deterministic AST-derived metric comparison (``symbol_metrics`` +
    ``math_signals``) plus name-token Jaccard, gated by a fixed
    threshold and weight formula. No regex, no pattern matching — same
    input always produces the same cluster shape. That maps to
    :data:`CONFIDENCE_STRUCTURAL` per the W109 substrate definitions
    ("AST / graph evidence"). The 0..1 similarity score is preserved in
    the evidence payload for consumers that want to weight by it; the
    registry's confidence tier is the detector-level evidence class,
    not a per-finding gradation.

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (no ``findings`` table) silently no-ops rather than crashing the
    standard duplicates read path.
    """
    # Local import keeps the cost out of the read-only path — callers
    # without --persist never reach here, so the import only runs when
    # we're actually writing.
    from roam.db.findings import (
        CONFIDENCE_STRUCTURAL,
        FindingRecord,
        emit_finding,
    )

    written = 0
    for c in clusters:
        functions = c.get("functions") or []
        if len(functions) < 2:
            # Defensive: a single-member "cluster" isn't a duplicate.
            # The cluster-builder already filters this, but the registry
            # write should stay tolerant.
            continue

        # Collect qualified names, falling back to bare name if the row
        # carries no qualified_name (rare, but possible on some
        # languages where the extractor doesn't synthesise qnames).
        member_qnames: list[str] = []
        for r in functions:
            qn = r["qualified_name"] or r["name"] or ""
            if qn:
                member_qnames.append(str(qn))
        if len(member_qnames) < 2:
            continue

        # Subject linkage: pick the symbol with the highest PageRank as
        # the cluster's "anchor" subject. Mirrors the clone-cluster
        # pattern of attaching one representative subject_id while the
        # full member list lives in evidence. NULL subject_id is allowed
        # by the registry, but populating it lets ``roam findings``
        # filter by symbol JOINs.
        anchor = max(
            functions,
            key=lambda r: (r["pagerank"] or 0.0, r["id"] or 0),
        )
        subject_id: int | None = None
        try:
            subject_id = int(anchor["id"]) if anchor["id"] is not None else None
        except (TypeError, ValueError):
            subject_id = None

        finding_id = _duplicates_cluster_finding_id(member_qnames)

        members_evidence = []
        for r in functions:
            members_evidence.append(
                {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "kind": r["kind"],
                    "file": r["file_path"],
                    "line_start": r["line_start"],
                    "line_count": r["line_count"] or 0,
                    "pagerank": round(r["pagerank"] or 0.0, 4),
                }
            )

        # W165 — bucket the cluster (production / test_intentional /
        # mixed) and persist it on every finding so registry consumers
        # (``roam findings list --detector duplicates``) can post-filter.
        role_bucket = _role_bucket_for_files([r["file_path"] for r in functions])

        evidence = {
            "similarity": c.get("similarity"),
            "size": c.get("size") or len(functions),
            "pattern": c.get("pattern"),
            "suggestion": c.get("suggestion"),
            "total_pagerank": c.get("total_pagerank"),
            "members": members_evidence,
            "role_bucket": role_bucket,
        }

        # Build a short, human-readable claim. We name the anchor and the
        # cluster size — the agent-contract anchor for a duplicate finding
        # is "cluster" (a concrete-noun terminal in the W109 anchor set).
        sim = c.get("similarity")
        sim_pct = round(float(sim) * 100) if sim is not None else 0
        anchor_name = anchor["name"] or "?"
        claim = f"Duplicate cluster: {len(functions)} functions ({sim_pct}% avg similarity) anchored at {anchor_name}"

        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol" if subject_id is not None else "file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                # All duplicates findings are structural — deterministic
                # metric comparison from DB tables, no regex / pattern
                # heuristics. See module docstring for the tier rationale.
                confidence=CONFIDENCE_STRUCTURAL,
                source_detector="duplicates",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="duplicates",
    category="refactoring",
    summary="Detect semantically duplicate functions via structural similarity",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option(
    "--threshold",
    default=0.75,
    show_default=True,
    type=click.FloatRange(0.0, 1.0),
    help="Similarity threshold (0.0-1.0)",
)
@click.option("--min-lines", default=5, show_default=True, type=int, help="Minimum function size to consider")
@click.option("--scope", default=None, type=str, help="Limit analysis to files under this path")
@click.option(
    "--sample",
    default=0,
    show_default=True,
    type=int,
    help="Deterministically sample at most N candidates (0=disabled, use all)",
)
@click.option(
    "--max-pairs",
    default=1000,
    show_default=True,
    type=int,
    help="Cap on number of duplicate-pair clusters reported (0=unlimited)",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each duplicate cluster into the central findings registry "
        "(``roam findings list --detector duplicates``). The detector-"
        "specific text/JSON output is unchanged; the registry rows are the "
        "denormalised cross-detector surface. The persisted set is the FULL "
        "pre-truncation cluster list, so --max-pairs only affects display. "
        "Re-running with the same source upserts in place (no duplicates)."
    ),
)
@click.option(
    "--exclude-tests",
    is_flag=True,
    default=False,
    help=(
        "Drop duplicate clusters where ALL participating files are tests "
        "(role_bucket=test_intentional — usually pytest parametrize). "
        "Mixed-bucket clusters (one side src, one side test) survive — "
        "they surface possible test-leakage and are worth keeping."
    ),
)
@click.option(
    "--exclude-fixtures",
    is_flag=True,
    default=False,
    help=(
        "Drop duplicate clusters where any participating file lives under "
        "a fixtures/ / testdata/ / test_data/ directory."
    ),
)
@click.pass_context
def duplicates(
    ctx,
    threshold,
    min_lines,
    scope,
    sample,
    max_pairs,
    persist,
    exclude_tests,
    exclude_fixtures,
):
    """Detect semantically duplicate functions via structural similarity.

    Unlike ``smells`` (which detects structural anti-patterns like god classes),
    this command clusters semantically similar functions for consolidation.

    Finds functions that are structurally similar (same control flow,
    parameter shape, naming pattern) but not textual clones.  Uses
    AST-derived metrics from the index for comparison.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=not persist) as conn:
        # ── 1. Candidate selection ───────────────────────────────────
        scope_clause = ""
        params: list = []
        if scope:
            scope_norm = scope.replace("\\", "/")
            scope_clause = " AND f.path LIKE ?"
            params.append(f"{scope_norm}%")

        candidates = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, s.signature, "
            "       s.line_start, s.line_end, f.path AS file_path, "
            "       COALESCE(sm.line_count, s.line_end - s.line_start + 1, 0) AS line_count, "
            "       COALESCE(sm.param_count, 0) AS param_count, "
            "       COALESCE(sm.nesting_depth, 0) AS nesting_depth, "
            "       COALESCE(sm.cognitive_complexity, 0) AS cognitive_complexity, "
            "       COALESCE(sm.return_count, 0) AS return_count, "
            "       COALESCE(sm.bool_op_count, 0) AS bool_op_count, "
            "       COALESCE(sm.callback_depth, 0) AS callback_depth, "
            "       COALESCE(ms.loop_depth, 0) AS loop_depth, "
            "       COALESCE(ms.has_nested_loops, 0) AS has_nested_loops, "
            "       COALESCE(ms.has_self_call, 0) AS has_self_call, "
            "       COALESCE(gm.pagerank, 0) AS pagerank "
            "FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id "
            "LEFT JOIN math_signals ms ON s.id = ms.symbol_id "
            "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
            "WHERE s.kind IN ('function', 'method') "
            "  AND COALESCE(sm.line_count, s.line_end - s.line_start + 1, 0) >= ? " + scope_clause,
            [min_lines] + params,
        ).fetchall()

        if len(candidates) < 2:
            verdict = "No duplicate candidates found"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "duplicates",
                            summary={
                                "verdict": verdict,
                                "total_clusters": 0,
                                "total_functions": 0,
                                "estimated_reducible_lines": 0,
                            },
                            clusters=[],
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {verdict}")
            return

        # ── Performance guard: bucket-based pair generation already prunes
        # the candidate space heavily (param_count, line_count bands).  At
        # 50K candidates the bucketed pair scan stays well under the
        # O(n^2) worst case in practice; beyond that the algorithm needs
        # explicit sampling to keep the work bounded.
        _MAX_CANDIDATES = 50000
        _HARD_LIMIT = _MAX_CANDIDATES
        original_candidate_count = len(candidates)
        partial_success = False
        sampled = False
        sample_size = 0

        if sample and sample > 0 and len(candidates) > sample:
            # Deterministic sample: sort by id then take every k-th row.
            ordered = sorted(candidates, key=lambda r: r["id"])
            step = max(1, len(ordered) // sample)
            candidates = ordered[::step][:sample]
            sampled = True
            sample_size = len(candidates)
            partial_success = True
        elif len(candidates) > _HARD_LIMIT and not scope:
            # Hard cap: deterministic stride sample down to _HARD_LIMIT so
            # the command produces useful signal instead of bailing out
            # entirely.  Caller can re-run with --scope or --sample to
            # control the trade-off explicitly.
            ordered = sorted(candidates, key=lambda r: r["id"])
            step = max(1, len(ordered) // _HARD_LIMIT)
            candidates = ordered[::step][:_HARD_LIMIT]
            sampled = True
            sample_size = len(candidates)
            partial_success = True

        # Build lookup
        by_id = {r["id"]: r for r in candidates}

        # ── 2. Pre-filter by shape ───────────────────────────────────
        # Group by (param_count bucket, line_count bucket) to avoid O(n^2)
        def _bucket_key(r):
            pc = r["param_count"] or 0
            lc = r["line_count"] or 0
            # Bucket: param_count, line_count in bands of ~30%
            lc_bucket = int(lc / max(lc * 0.3, 3)) if lc > 0 else 0
            return (pc, lc_bucket)

        # For each candidate, check neighbors in nearby buckets
        by_bucket: dict[tuple, list] = defaultdict(list)
        for r in candidates:
            key = _bucket_key(r)
            by_bucket[key].append(r)

        # Generate candidate pairs: same bucket + adjacent buckets
        pairs_to_check: list[tuple] = []
        seen_pairs: set[tuple[int, int]] = set()

        for key, members in by_bucket.items():
            pc, lc_b = key
            # Check same bucket
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    # Skip functions in the same file with the same name
                    # (likely overloads, not duplicates)
                    pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                    if pair_key not in seen_pairs:
                        # Check shape compatibility: param count +/- 1,
                        # line count +/- 30%
                        pa, pb = a["param_count"] or 0, b["param_count"] or 0
                        la, lb = a["line_count"] or 0, b["line_count"] or 0
                        if abs(pa - pb) <= 1:
                            max_l = max(la, lb, 1)
                            if abs(la - lb) / max_l <= 0.30:
                                seen_pairs.add(pair_key)
                                pairs_to_check.append((a, b))

            # Check adjacent param count buckets
            for dpc in (-1, 1):
                adj_key = (pc + dpc, lc_b)
                if adj_key in by_bucket:
                    for a in members:
                        for b in by_bucket[adj_key]:
                            pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                            if pair_key not in seen_pairs:
                                pa = a["param_count"] or 0
                                pb = b["param_count"] or 0
                                la = a["line_count"] or 0
                                lb = b["line_count"] or 0
                                if abs(pa - pb) <= 1:
                                    max_l = max(la, lb, 1)
                                    if abs(la - lb) / max_l <= 0.30:
                                        seen_pairs.add(pair_key)
                                        pairs_to_check.append((a, b))

            # Check adjacent line count buckets
            for dlc in (-1, 1):
                adj_key = (pc, lc_b + dlc)
                if adj_key in by_bucket:
                    for a in members:
                        for b in by_bucket[adj_key]:
                            pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                            if pair_key not in seen_pairs:
                                pa = a["param_count"] or 0
                                pb = b["param_count"] or 0
                                la = a["line_count"] or 0
                                lb = b["line_count"] or 0
                                if abs(pa - pb) <= 1:
                                    max_l = max(la, lb, 1)
                                    if abs(la - lb) / max_l <= 0.30:
                                        seen_pairs.add(pair_key)
                                        pairs_to_check.append((a, b))

        # ── 3. Score pairs ───────────────────────────────────────────
        uf = _UnionFind()
        pair_scores: dict[tuple[int, int], float] = {}

        for a, b in pairs_to_check:
            sim = _compute_similarity(a, b)
            if sim >= threshold:
                pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                pair_scores[pair_key] = sim
                uf.union(a["id"], b["id"])

        # ── 4. Build clusters ────────────────────────────────────────
        raw_clusters = uf.clusters()

        # Filter to clusters with >= 2 members
        cluster_list = []
        for root, members in raw_clusters.items():
            if len(members) < 2:
                continue

            member_rows = [by_id[m] for m in members if m in by_id]
            if len(member_rows) < 2:
                continue

            # Compute average similarity within cluster
            sims = []
            for i in range(len(member_rows)):
                for j in range(i + 1, len(member_rows)):
                    pk = (
                        min(member_rows[i]["id"], member_rows[j]["id"]),
                        max(member_rows[i]["id"], member_rows[j]["id"]),
                    )
                    if pk in pair_scores:
                        sims.append(pair_scores[pk])
                    # W20.1 fix: skip transitive (non-scored) pairs to avoid
                    # O(n^2) blow-up on large clusters; the cluster is already
                    # union-find-connected via above-threshold edges.
            avg_sim = sum(sims) / len(sims) if sims else 0.0

            # Combined PageRank
            total_pr = sum(r["pagerank"] or 0 for r in member_rows)

            names = [r["name"] for r in member_rows]
            pattern = _infer_pattern(names)
            suggestion = _suggest_refactor(names, pattern)

            # Sort members by line_start for consistent output
            member_rows.sort(key=lambda r: (r["file_path"], r["line_start"] or 0))

            cluster_list.append(
                {
                    "similarity": round(avg_sim, 2),
                    "size": len(member_rows),
                    "functions": member_rows,
                    "pattern": pattern,
                    "suggestion": suggestion,
                    "total_pagerank": total_pr,
                }
            )

        # ── 5. Rank clusters ────────────────────────────────────────
        # Sort by: size desc, similarity desc, pagerank desc
        cluster_list.sort(key=lambda c: (-c["size"], -c["similarity"], -c["total_pagerank"]))

        # ── 5.W165 Bucket + filter ──────────────────────────────────
        # Attach role_bucket to every cluster on the live structure so
        # downstream paths (verdict, JSON output, persist) share one
        # classification call. ``--exclude-tests`` drops only the
        # test_intentional bucket — mixed clusters (one side src, one
        # side test) survive deliberately as a test-leakage signal.
        # ``--exclude-fixtures`` drops any cluster touching a fixtures/
        # / testdata/ path regardless of bucket.
        for c in cluster_list:
            c["role_bucket"] = _role_bucket_for_files([r["file_path"] for r in c["functions"]])

        if exclude_tests or exclude_fixtures:
            filtered: list[dict] = []
            for c in cluster_list:
                files = [r["file_path"] for r in c["functions"]]
                if exclude_fixtures and any(_is_fixture_path(f) for f in files):
                    continue
                if exclude_tests and c["role_bucket"] == "test_intentional":
                    continue
                filtered.append(c)
            cluster_list = filtered

        # Per-bucket cluster counts surfaced in the verdict line
        # (Pattern-3 vocabulary improvement: buckets at the surface, not
        # hidden in JSON).
        bucket_counts = {"production": 0, "test_intentional": 0, "mixed": 0}
        for c in cluster_list:
            bucket_counts[c.get("role_bucket", "production")] += 1

        # ── 5a. W136: mirror full cluster set into findings registry ─
        # Runs ONLY with --persist. We emit BEFORE --max-pairs truncation
        # so the registry stays comprehensive regardless of how the
        # current invocation slices the display. Wrapped so a pre-W89 DB
        # (no ``findings`` table) silently no-ops rather than crashing
        # the standard duplicates read path.
        if persist:
            try:
                _emit_duplicates_findings(conn, cluster_list, DUPLICATES_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

        # ── 5b. Apply --max-pairs truncation ─────────────────────────
        total_clusters_found = len(cluster_list)
        truncated = False
        if max_pairs and max_pairs > 0 and len(cluster_list) > max_pairs:
            cluster_list = cluster_list[:max_pairs]
            truncated = True
            partial_success = True

        # ── 6. Compute summary stats ────────────────────────────────
        total_functions = sum(c["size"] for c in cluster_list)
        estimated_lines = 0
        for c in cluster_list:
            # Each cluster: all but one function's lines are "reducible"
            lines = sorted([r["line_count"] or 0 for r in c["functions"]])
            if len(lines) > 1:
                estimated_lines += sum(lines[:-1])  # keep the longest

        verdict_parts: list[str] = []
        if cluster_list:
            verdict_parts.append(
                f"{len(cluster_list)} duplicate cluster"
                f"{'s' if len(cluster_list) != 1 else ''} found ({total_functions} functions) "
                f"({bucket_counts['production']} production"
                f" · {bucket_counts['test_intentional']} test_intentional"
                f" · {bucket_counts['mixed']} mixed)"
            )
        else:
            verdict_parts.append("No semantic duplicates detected")
        if sampled:
            verdict_parts.append(f"sampled {sample_size}/{original_candidate_count} candidates")
        if truncated:
            verdict_parts.append(f"truncated to top {len(cluster_list)}/{total_clusters_found} clusters")
        verdict = "; ".join(verdict_parts)

        # ── 7. Output ───────────────────────────────────────────────
        # Build the per-cluster JSON dicts once — the SARIF path and the
        # JSON path both consume them; keeping the construction shared
        # guarantees the SARIF projection sees the same shape that
        # downstream registry consumers do.
        clusters_json = []
        for i, c in enumerate(cluster_list):
            clusters_json.append(
                {
                    "id": i + 1,
                    "similarity": c["similarity"],
                    "size": c["size"],
                    "functions": [
                        {
                            "name": r["name"],
                            "qualified_name": r["qualified_name"],
                            "kind": r["kind"],
                            "file": r["file_path"],
                            "line": r["line_start"],
                            "lines": r["line_count"] or 0,
                            "pagerank": round(r["pagerank"] or 0, 4),
                        }
                        for r in c["functions"]
                    ],
                    "pattern": c["pattern"],
                    "suggestion": c["suggestion"],
                    # W165 — bucket on the live cluster object so JSON
                    # consumers can filter without going through the
                    # registry.
                    "role_bucket": c.get("role_bucket", "production"),
                }
            )

        # -- SARIF format ----------------------------------------------------
        # W1213: SARIF projection for CI / GitHub Code Scanning integration.
        # Branches BEFORE the json / text paths so those legacy paths stay
        # byte-identical to pre-W1213 output. ``--sarif --json`` resolves to
        # SARIF (CI-format consumers want the SARIF document, not the JSON
        # envelope). Mirrors the W1172 ``cmd_clones`` SARIF wiring.
        if sarif_mode:
            from roam.output.sarif import duplicates_to_sarif, write_sarif

            sarif_envelope = json_envelope(
                "duplicates",
                summary={
                    "verdict": verdict,
                    "total_clusters": len(cluster_list),
                    "total_functions": total_functions,
                    "estimated_reducible_lines": estimated_lines,
                },
                clusters=clusters_json,
            )
            click.echo(write_sarif(duplicates_to_sarif(sarif_envelope)))
            return

        if json_mode:
            summary_payload = {
                "verdict": verdict,
                "total_clusters": len(cluster_list),
                "total_functions": total_functions,
                "estimated_reducible_lines": estimated_lines,
                "candidate_count": original_candidate_count,
                # W165 — per-bucket cluster counts in the summary.
                "role_buckets": bucket_counts,
            }
            if partial_success:
                summary_payload["partial_success"] = True
            if sampled:
                summary_payload["sampled"] = True
                summary_payload["sample_size"] = sample_size
                summary_payload["candidates_total"] = original_candidate_count
            if truncated:
                summary_payload["truncated"] = True
                summary_payload["clusters_total"] = total_clusters_found
                summary_payload["max_pairs"] = max_pairs
            click.echo(
                to_json(
                    json_envelope(
                        "duplicates",
                        summary=summary_payload,
                        budget=token_budget,
                        clusters=clusters_json,
                    )
                )
            )
            return

        # ── Text output ──────────────────────────────────────────────
        click.echo(f"VERDICT: {verdict}")
        if not cluster_list:
            return

        click.echo()
        for i, c in enumerate(cluster_list):
            click.echo(
                f"CLUSTER {i + 1} (similarity {c['similarity']:.2f}, "
                f"{c['size']} functions) [{c.get('role_bucket', 'production')}]:"
            )
            for r in c["functions"]:
                kind_str = abbrev_kind(r["kind"])
                click.echo(
                    f"  {kind_str} {r['name']:<35s} "
                    f"at {loc(r['file_path'], r['line_start'])}"
                    f"    ({r['line_count'] or 0} lines)"
                )
            click.echo(f"  Shared pattern: {c['pattern']}")
            click.echo(f"  Suggestion: {c['suggestion']}")
            click.echo()

        click.echo(
            f"SUMMARY: {len(cluster_list)} clusters, "
            f"{total_functions} functions, "
            f"estimated {estimated_lines} lines of reducible duplication"
        )
