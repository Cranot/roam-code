"""Detect semantically duplicate functions via structural similarity."""

from __future__ import annotations

import functools
import hashlib
import json as _json
import math
import os
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
DUPLICATES_DETECTOR_VERSION: str = "1.1.0"

# Geometric line-count band base for duplicate-candidate bucketing. Each band
# spans a ~1.3x line ratio, so two functions within the ±30% shape window land
# in the same or an adjacent band — the full 3x3 (param ±1, band ±1)
# neighbourhood scanned below covers every bucket that can hold a
# shape-compatible pair. Replaces a degenerate int(lc / max(lc * 0.3, 3)) that
# evaluated to a constant 3 for every function >=10 lines, collapsing all of
# them into one band per param-count and driving O(n^2) pair generation on
# large repos (duplicates ran >360s on roam-code's ~21K candidates).
_LOG_BAND_BASE: float = math.log(1.3)

# Hard ceiling on shape-compatible candidate pairs scored in one run. Even with
# geometric banding the ±30%/±1-param window admits millions of pairs on a large
# repo (functions skew to a single ``self`` param), and _compute_similarity is
# regex-heavy, so scoring all of them wedges the command (>360s on roam-code).
# When the budget is hit we stop enumerating, score what we have, and disclose
# it via summary.partial_success + a "scored N pairs (capped)" verdict note
# (re-run with --scope or --sample for fuller coverage). Tunable via
# ROAM_DUPLICATES_MAX_PAIRS.
try:
    _MAX_PAIRS_TO_CHECK = max(1, int(os.environ.get("ROAM_DUPLICATES_MAX_PAIRS", "1000000")))
except ValueError:
    _MAX_PAIRS_TO_CHECK = 1000000

# ---------------------------------------------------------------------------
# Name tokenization
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")


@functools.lru_cache(maxsize=None)
def _name_tokens(name: str) -> set[str]:
    """Split a symbol name into lowercase token set (camelCase/snake_case).

    lru_cached: each symbol name recurs across the many candidate pairs it
    participates in, and the returned set is read-only at every call site
    (jaccard / set-difference only — never mutated in place), so memoizing
    eliminates redundant regex tokenization in the hot _compute_similarity loop.
    """
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


# The 10 structural-fingerprint features, in fixed order. _body_structure_vector
# emits a flat tuple in this order so _body_similarity can iterate positionally
# without a dict-key walk. Order is irrelevant to the averaged result — it only
# has to be stable between the two operands of one comparison.
_STRUCT_FEATURES: tuple[str, ...] = (
    "line_count",
    "param_count",
    "nesting_depth",
    "cognitive_complexity",
    "return_count",
    "bool_op_count",
    "callback_depth",
    "loop_depth",
    "has_nested_loops",
    "has_self_call",
)
_STRUCT_FEATURE_COUNT: int = len(_STRUCT_FEATURES)


def _body_structure_vector(row) -> tuple[int, ...]:
    """Extract a structural fingerprint from symbol_metrics + math_signals.

    Returns a flat 10-tuple in :data:`_STRUCT_FEATURES` order. Computed once
    per candidate row at load time (hoisted out of the per-pair scoring loop):
    a candidate participates in ~93 pairs on the roam-code corpus, so building
    this per-pair recomputed the same tuple ~93x. Pre-computing it on the row
    dict turns 2M builds into ~21K.
    """
    return (
        row["line_count"] or 0,
        row["param_count"] or 0,
        row["nesting_depth"] or 0,
        row["cognitive_complexity"] or 0,
        row["return_count"] or 0,
        row["bool_op_count"] or 0,
        row["callback_depth"] or 0,
        row["loop_depth"] or 0,
        row["has_nested_loops"] or 0,
        row["has_self_call"] or 0,
    )


def _body_similarity(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Compare two body structure vectors (flat tuples, _STRUCT_FEATURES order).

    Uses normalized absolute difference per feature, averaged. Positional
    iteration over the two pre-built tuples replaces the old dict-key walk —
    the hot path of the 1M-pair scoring loop.
    """
    if not a:
        return 0.0
    total = 0.0
    for va, vb in zip(a, b):
        diff = va - vb
        if diff < 0:
            diff = -diff
        if diff == 0:
            total += 1.0
            continue
        av = va if va >= 0 else -va
        bv = vb if vb >= 0 else -vb
        max_val = av if av > bv else bv
        if max_val < 1:
            max_val = 1
        total += 1.0 - diff / max_val
    return total / _STRUCT_FEATURE_COUNT


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


def _precompute_row_fields(row) -> dict:
    """Convert a candidate row to a dict with hoisted per-row similarity data.

    ``_compute_similarity`` recurs over every pair a row appears in (~93x on
    the roam-code corpus). Pre-computing the structure vector, name-token set
    and signature-token set ONCE per candidate — instead of once per pair —
    is the central perf hoist: it turns the 1M-pair scoring loop into pure
    arithmetic + set ops over cached values. The returned dict is a drop-in
    replacement for the original ``sqlite3.Row`` (``d[key]`` is identical to
    ``Row[key]``); the extra ``_struct`` / ``_name_tok`` / ``_sig_tok`` keys
    are read by :func:`_compute_similarity` when present.
    """
    d = dict(row)
    d["_struct"] = _body_structure_vector(d)
    d["_name_tok"] = _name_tokens(d["name"])
    sig = d.get("signature")
    # _sig_tok mirrors _signature_similarity's branches: None when the
    # signature is empty/absent, else the tokenized set.
    d["_sig_tok"] = _name_tokens(sig) if sig else None
    d["_has_sig"] = bool(sig)
    return d


def _compute_similarity(row_a, row_b) -> float:
    """Compute weighted structural similarity between two symbol rows.

    Weights: body_structure(0.4) + params(0.25) + name(0.2) + signature(0.15)

    Fast path: when both rows are precomputed dicts carrying the ``_struct``
    / ``_name_tok`` / ``_sig_tok`` keys stamped by
    :func:`_precompute_row_fields`, the per-row derived data is read straight
    from the cache (the duplicates command feeds pre-stamped rows). Slow
    path: raw rows (e.g. direct unit-test callers) compute the derived data
    on the fly — output-identical, just not hoisted.
    """
    # ``in`` checks keys on a dict but VALUES on a sqlite3.Row, so the
    # fast-path gate keys on dict-ness rather than ``"_struct" in row``.
    fast = isinstance(row_a, dict) and isinstance(row_b, dict) and "_struct" in row_a and "_struct" in row_b

    if fast:
        body_sim = _body_similarity(row_a["_struct"], row_b["_struct"])
        name_sim = _jaccard(row_a["_name_tok"], row_b["_name_tok"])
        # Fast path for signature: reproduce _signature_similarity's
        # branch structure exactly against the pre-computed token sets.
        has_a, has_b = row_a["_has_sig"], row_b["_has_sig"]
        if not has_a and not has_b:
            sig_sim = 1.0
        elif not has_a or not has_b:
            sig_sim = 0.3
        else:
            sig_sim = _jaccard(row_a["_sig_tok"], row_b["_sig_tok"])
    else:
        body_sim = _body_similarity(
            _body_structure_vector(row_a),
            _body_structure_vector(row_b),
        )
        name_sim = _jaccard(
            _name_tokens(row_a["name"]),
            _name_tokens(row_b["name"]),
        )
        sig_sim = _signature_similarity(
            _safe_get(row_a, "signature"),
            _safe_get(row_b, "signature"),
        )

    param_sim = _param_similarity(
        row_a["param_count"] or 0,
        row_b["param_count"] or 0,
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


def _ranked_common_tokens(names: list[str], limit: int | None = None) -> list[str]:
    """Return tokens shared by >= 2 of *names*, ranked deterministically.

    ``_name_tokens`` returns a ``set``, whose iteration order varies with
    ``PYTHONHASHSEED``. ``Counter.most_common`` breaks frequency ties by
    insertion order, so feeding it from that set produced a different
    winner run-to-run whenever several tokens tied on frequency. We pin
    the tie-break by sorting the full frequency table on ``(-count,
    token)`` — descending count, then ascending token text — before
    slicing. With a clear frequency winner the result is unchanged; only
    ties are now resolved by alphabetical token order instead of by
    randomized set-iteration order.

    Mirrors :func:`roam.graph.clone_detect._ranked_common_tokens` so the
    two parallel DRY detectors share one tie-break discipline. ``limit``
    of ``None`` returns every shared token (the ``_infer_pattern`` case);
    an int caps the slice (the ``_suggest_refactor`` case).
    """
    token_freq: Counter = Counter()
    for name in names:
        for t in _name_tokens(name) - _STOP_WORDS:
            token_freq[t] += 1

    ranked = sorted(token_freq.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit is not None:
        ranked = ranked[:limit]
    return [t for t, c in ranked if c >= 2]


def _infer_pattern(names: list[str]) -> str:
    """Infer a shared behavioral pattern from function names."""
    all_tokens: list[set[str]] = []
    for name in names:
        tokens = _name_tokens(name) - _STOP_WORDS
        all_tokens.append(tokens)

    if not all_tokens:
        return "similar structure"

    # Find tokens common to at least 2 functions, ranked deterministically
    # (descending frequency, ascending token text) so the chosen ``verb``
    # below is stable regardless of PYTHONHASHSEED — see _ranked_common_tokens.
    common = _ranked_common_tokens(names)
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
    # Common verb/action tokens, ranked deterministically (descending
    # frequency, ascending token text) so the generated helper name is
    # stable regardless of PYTHONHASHSEED — see _ranked_common_tokens.
    common = _ranked_common_tokens(names, 3)

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
                    # W-dogfood (W336 sibling family): 4-decimal rounding
                    # collapses ~72% of nonzero PR values to 0.0 on
                    # 5K+ symbol graphs (per-node floor ~1.4e-05).
                    # Match cmd_search / cmd_intent / cmd_hover 6-decimal
                    # precedent.
                    "pagerank": round(r["pagerank"] or 0.0, 6),
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

    # W607-BM -- substrate-boundary plumbing on the duplicates DRY-detection
    # detector. cmd_duplicates is the W805 paired-scoring sibling of
    # cmd_dark_matter (W607-BK, just landed); both detect DRY/architecture
    # debt from different signal axes (co-change vs structural-similarity).
    # The substrate boundaries we wrap:
    #
    #   * query_candidates           -- the symbol_metrics + math_signals
    #                                    + graph_metrics SELECT that feeds
    #                                    the bucketed pair generator.
    #   * compute_similarity         -- the per-pair weighted scoring
    #                                    (``_compute_similarity`` calls
    #                                    over the bucketed candidate pairs).
    #   * classify_role_buckets      -- W165 production/test/mixed bucket
    #                                    classification per cluster.
    #   * emit_findings              -- registry mirror under --persist
    #                                    (W136 cluster subject_kind).
    #   * serialize_to_sarif         -- SARIF projection for CI gates.
    #
    # Marker family ``duplicates_<phase>_failed:<exc_class>:<detail>``
    # (underscore form -- matches the W805 paired sibling cmd_dark_matter
    # marker discipline). Empty bucket -> no field added -> byte-identical
    # envelope on the happy path (W607-A..BK parity).
    # Threads into BOTH the top-level ``warnings_out`` (preserved-list-field
    # discipline) AND ``summary.warnings_out`` + ``summary.partial_success
    # = True``.
    _w607bm_warnings_out: list[str] = []

    def _run_check_bm(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BM marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a
        ``duplicates_<phase>_failed:<exc_class>:<detail>`` marker via
        ``_w607bm_warnings_out`` and return *default* -- the envelope
        still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bm_warnings_out.append(f"duplicates_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-DD -- additive aggregation-phase plumbing on the duplicates
    # DRY-detection detector. Mirror of cmd_dark_matter's W607-CZ wave on
    # the W805 structural-debt paired-scoring sibling. The four
    # aggregation-phase boundaries we wrap:
    #
    #   * score_classify     -- cluster ranking / sorting step that
    #                            turns the raw union-find groups into a
    #                            stable display order.
    #   * compute_predicate  -- per-bucket cluster counts +
    #                            total_functions / estimated_reducible
    #                            -lines rollup.
    #   * compute_verdict    -- verdict-string assembly (bucket count
    #                            sentence + sampled / truncated
    #                            qualifiers).
    #   * serialize_envelope -- ``json_envelope("duplicates", ...)`` call.
    #                            A schema-shape refactor that breaks the
    #                            serializer would otherwise crash AFTER
    #                            all substrate + aggregation signals
    #                            were already gathered.
    #
    # W607-BM / DD PHASE-NAME COLLISION (W607-CH 4th-discipline): the
    # substrate-CALL layer uses phase names query_candidates /
    # compute_similarity / classify_role_buckets / emit_findings /
    # serialize_to_sarif. None collide with score_classify /
    # compute_predicate / compute_verdict / serialize_envelope, so no
    # rename is required. ``serialize_to_sarif`` vs ``serialize_envelope``
    # are deliberately distinct phase names so an agent can tell which
    # serialiser raised.
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` in a
    # ``_run_check_dd(...)`` call MUST be a literal constant. cmd_sbom
    # W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export W607-CR sealed
    # this axis. The 5th discipline (``len()`` lives INSIDE the closure,
    # not at the kwarg-bind site) is pinned by the test AST audit.
    _w607dd_warnings_out: list[str] = []

    def _run_check_dd(phase, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DD marker emission.

        Mirror of ``_run_check_bm`` shape (same
        ``duplicates_<phase>_failed:`` marker family) but writes into
        ``_w607dd_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dd_warnings_out.append(f"duplicates_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        # ── 1. Candidate selection ───────────────────────────────────
        scope_clause = ""
        params: list = []
        if scope:
            scope_norm = scope.replace("\\", "/")
            scope_clause = " AND f.path LIKE ?"
            params.append(f"{scope_norm}%")

        def _query_candidates():
            return conn.execute(
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

        candidates = _run_check_bm(
            "query_candidates",
            _query_candidates,
            default=[],
        )
        if candidates is None:
            candidates = []

        if len(candidates) < 2:
            # W805 (Pattern 2: silent fallbacks) — fewer than 2 candidate
            # functions means the duplicate-detection algorithm CANNOT
            # produce findings (it needs pairs to compare). The previous
            # verdict "No duplicate candidates found" was a silent SAFE
            # indistinguishable from "scan ran cleanly across 1000 funcs
            # and found no clusters". Disclose the absent input state so
            # agents see that the detector ran in a degraded mode.
            symbol_count = conn.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('function', 'method')").fetchone()[
                0
            ]
            if symbol_count == 0:
                w805_state = "empty_corpus"
                verdict = (
                    "no symbols to analyze (corpus has 0 functions/methods; "
                    "run `roam index --force` to populate the graph "
                    "before duplicate detection)"
                )
            elif len(candidates) == 0:
                w805_state = "no_candidates"
                verdict = (
                    f"no candidates above min-lines threshold ({min_lines}; "
                    f"all {symbol_count} functions are smaller — "
                    f"detector had no input to analyze)"
                )
            else:
                # Exactly 1 candidate — the algorithm needs pairs.
                w805_state = "insufficient_candidates"
                verdict = (
                    "only 1 candidate function above min-lines threshold "
                    "(duplicate detection requires at least 2 to form a pair)"
                )
            if json_mode:
                _early_summary: dict = {
                    "verdict": verdict,
                    "total_clusters": 0,
                    "total_functions": 0,
                    "estimated_reducible_lines": 0,
                    "state": w805_state,
                    "partial_success": True,
                    "candidates_scanned": len(candidates),
                }
                _early_kwargs: dict = {
                    "summary": _early_summary,
                    "clusters": [],
                }
                # W607-BM: even the early-exit path may carry substrate
                # markers (the candidate query itself can raise -> we end
                # up here with an empty candidates list and a marker on
                # the accumulator). Surface them via the same
                # top-level + summary mirror discipline.
                if _w607bm_warnings_out:
                    _early_summary["warnings_out"] = list(_w607bm_warnings_out)
                    _early_kwargs["warnings_out"] = list(_w607bm_warnings_out)
                click.echo(to_json(json_envelope("duplicates", **_early_kwargs)))
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

        # ── Hoist per-row similarity data out of the pair-scoring loop ──
        # Convert each surviving candidate Row -> dict, stamping the
        # structure vector / name-token set / signature-token set ONCE.
        # _compute_similarity recurs over every pair a row appears in
        # (~93x on the roam-code corpus), so without this hoist the same
        # per-row derived data is rebuilt ~93x — _body_structure_vector
        # alone was 2M builds for 1M pairs. The dict is a drop-in
        # replacement for the Row (``d[key]`` is identical), so every
        # downstream consumer (bucketing, clustering, JSON serialisation,
        # findings emit) is unaffected. Done AFTER sampling so only the
        # rows actually scored get stamped.
        candidates = [_precompute_row_fields(r) for r in candidates]

        # Build lookup
        by_id = {r["id"]: r for r in candidates}

        # ── 2. Pre-filter by shape ───────────────────────────────────
        # Group by (param_count, geometric line-count band). Each band spans a
        # ~1.3x line ratio (_LOG_BAND_BASE), so two functions within the ±30%
        # shape window always land in the same or an adjacent band; the full
        # set of buckets that can hold a shape-compatible pair is the 3x3
        # (param ±1, band ±1) neighbourhood scanned below.
        def _bucket_key(r):
            pc = r["param_count"] or 0
            lc = r["line_count"] or 0
            lc_bucket = int(math.log(lc) / _LOG_BAND_BASE) if lc > 0 else 0
            return (pc, lc_bucket)

        # Stable candidate order so the pair-scoring budget below yields a
        # deterministic partial result across runs / CI when the cap is hit.
        # (Order does not affect the uncapped result — cluster membership is
        # union-find, which is order-independent.)
        candidates.sort(key=lambda r: r["id"])
        by_bucket: dict[tuple, list] = defaultdict(list)
        for r in candidates:
            by_bucket[_bucket_key(r)].append(r)

        # Generate candidate pairs across the 3x3 (param ±1, band ±1)
        # neighbourhood. seen_pairs dedupes the symmetric cross-bucket visits;
        # the shape filter (param ±1, line ±30%) is applied per pair. The old
        # code scanned only 5 of the 9 neighbours (same bucket + param±1 +
        # band±1, never the diagonals), so it silently missed shape-compatible
        # pairs whose param AND band both differed by one once banding was
        # non-degenerate — the full neighbourhood closes that gap.
        pairs_to_check: list[tuple] = []
        seen_pairs: set[tuple[int, int]] = set()
        pairs_budget_hit = False

        def _consider(a, b) -> None:
            nonlocal pairs_budget_hit
            if pairs_budget_hit:
                return
            pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if pair_key in seen_pairs:
                return
            pa, pb = a["param_count"] or 0, b["param_count"] or 0
            if abs(pa - pb) > 1:
                return
            la, lb = a["line_count"] or 0, b["line_count"] or 0
            max_l = max(la, lb, 1)
            if abs(la - lb) / max_l > 0.30:
                return
            seen_pairs.add(pair_key)
            pairs_to_check.append((a, b))
            if len(pairs_to_check) >= _MAX_PAIRS_TO_CHECK:
                pairs_budget_hit = True

        _NEIGHBOUR_OFFSETS = (
            (0, 0),
            (0, 1),
            (0, -1),
            (1, 0),
            (1, 1),
            (1, -1),
            (-1, 0),
            (-1, 1),
            (-1, -1),
        )
        for (pc, lc_b), members in by_bucket.items():
            if pairs_budget_hit:
                break
            for dpc, dlc in _NEIGHBOUR_OFFSETS:
                if pairs_budget_hit:
                    break
                others = by_bucket.get((pc + dpc, lc_b + dlc))
                if not others:
                    continue
                if dpc == 0 and dlc == 0:
                    for i in range(len(members)):
                        a = members[i]
                        for j in range(i + 1, len(members)):
                            _consider(a, members[j])
                        if pairs_budget_hit:
                            break
                else:
                    for a in members:
                        for b in others:
                            _consider(a, b)
                        if pairs_budget_hit:
                            break

        if pairs_budget_hit:
            partial_success = True

        # ── 3. Score pairs ───────────────────────────────────────────
        uf = _UnionFind()
        pair_scores: dict[tuple[int, int], float] = {}

        # W607-BM: wrap the scoring loop so a raise inside
        # ``_compute_similarity`` surfaces a structured marker rather than
        # crashing the command. The default is a no-op (empty union-find
        # / empty pair_scores) so the envelope still emits the
        # ``no semantic duplicates detected`` verdict on a degraded path.
        def _score_pairs():
            for a, b in pairs_to_check:
                sim = _compute_similarity(a, b)
                if sim >= threshold:
                    pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                    pair_scores[pair_key] = sim
                    uf.union(a["id"], b["id"])

        _run_check_bm(
            "compute_similarity",
            _score_pairs,
            default=None,
        )

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
        # W607-DD: wrap the ranking step so a __lt__-raising sentinel in
        # the key tuple (e.g. a custom score type) surfaces a structured
        # marker rather than crashing the command. Floor is a no-op (the
        # union-find already produced a deterministic, if unsorted,
        # cluster_list).
        def _score_classify_clusters():
            cluster_list.sort(key=lambda c: (-c["size"], -c["similarity"], -c["total_pagerank"]))

        _run_check_dd(
            "score_classify",
            _score_classify_clusters,
            default=None,
        )

        # ── 5.W165 Bucket + filter ──────────────────────────────────
        # Attach role_bucket to every cluster on the live structure so
        # downstream paths (verdict, JSON output, persist) share one
        # classification call. ``--exclude-tests`` drops only the
        # test_intentional bucket — mixed clusters (one side src, one
        # side test) survive deliberately as a test-leakage signal.
        # ``--exclude-fixtures`` drops any cluster touching a fixtures/
        # / testdata/ path regardless of bucket.
        # W607-BM: wrap the W165 role-bucket classification. A raise inside
        # ``_role_bucket_for_files`` (e.g., on a malformed file_path) now
        # surfaces a structured marker rather than crashing the command;
        # the cluster's bucket safe-floors to "production" so the
        # downstream verdict/JSON paths still emit cleanly.
        def _classify_buckets():
            for c in cluster_list:
                c["role_bucket"] = _role_bucket_for_files([r["file_path"] for r in c["functions"]])

        _run_check_bm(
            "classify_role_buckets",
            _classify_buckets,
            default=None,
        )
        # Safe-floor: any cluster left without a bucket (e.g. partial
        # success of the loop above) gets the production default so the
        # downstream verdict/JSON shape stays well-formed.
        for c in cluster_list:
            c.setdefault("role_bucket", "production")

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
        # W607-DD: wrap the per-bucket roll-up. A KeyError-poisoned
        # role_bucket vocabulary refactor would otherwise crash here.
        # Floor returns a zeroed dict matching the empty-state branch so
        # downstream verdict + summary fields stay non-null. W978 5th-
        # discipline: ``len()`` calls live INSIDE the closure, not at the
        # kwarg-bind site.
        def _compute_bucket_counts():
            _bc = {"production": 0, "test_intentional": 0, "mixed": 0}
            for _c in cluster_list:
                _bc[_c.get("role_bucket", "production")] += 1
            return _bc

        bucket_counts = _run_check_dd(
            "compute_predicate",
            _compute_bucket_counts,
            default={"production": 0, "test_intentional": 0, "mixed": 0},
        )
        if bucket_counts is None:
            bucket_counts = {"production": 0, "test_intentional": 0, "mixed": 0}

        # ── 5a. W136: mirror full cluster set into findings registry ─
        # Runs ONLY with --persist. We emit BEFORE --max-pairs truncation
        # so the registry stays comprehensive regardless of how the
        # current invocation slices the display.
        #
        # W607-BM: replaces the pre-existing ``try / except
        # sqlite3.OperationalError: pass`` Pattern-2 silent-fallback. The
        # old block silently no-op'd whenever the findings table was
        # missing (pre-W89 schema) OR whenever ANY OperationalError
        # surfaced (locked DB, full disk, etc.). New path surfaces the
        # exception class + detail via a structured marker so the
        # degradation is visible to consumers -- mirrors the W607-BK
        # paired-sibling Pattern-2 elimination on cmd_dark_matter.
        if persist:

            def _emit_and_commit():
                _emit_duplicates_findings(conn, cluster_list, DUPLICATES_DETECTOR_VERSION)
                conn.commit()

            _run_check_bm(
                "emit_findings",
                _emit_and_commit,
                default=None,
            )

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

        # W607-DD: wrap the verdict-string assembly. A __format__-raising
        # sentinel inside one of the count fields (or a vocabulary
        # refactor that removes a bucket key) would otherwise crash here.
        # Floor must NOT re-interpolate the values that tripped the
        # closure (W978 first-hypothesis discipline). Use a literal
        # "duplicates completed" floor that still satisfies LAW 6
        # standalone-parse.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: ``len(cluster_list)`` /
        # ``len(...)`` calls live INSIDE the closure, not at the
        # kwarg-bind site. cmd_taint W607-CJ 5th-discipline anchor.
        def _build_verdict_str():
            _parts: list[str] = []
            if cluster_list:
                _parts.append(
                    f"{len(cluster_list)} duplicate cluster"
                    f"{'s' if len(cluster_list) != 1 else ''} found ({total_functions} functions) "
                    f"({bucket_counts['production']} production"
                    f" · {bucket_counts['test_intentional']} test_intentional"
                    f" · {bucket_counts['mixed']} mixed)"
                )
            else:
                _parts.append("No semantic duplicates detected")
            if sampled:
                _parts.append(f"sampled {sample_size}/{original_candidate_count} candidates")
            if truncated:
                _parts.append(f"truncated to top {len(cluster_list)}/{total_clusters_found} clusters")
            if pairs_budget_hit:
                _parts.append(
                    f"scored {len(pairs_to_check)} candidate pairs (capped at "
                    f"{_MAX_PAIRS_TO_CHECK}; re-run with --scope or --sample for fuller coverage)"
                )
            return "; ".join(_parts)

        verdict = _run_check_dd(
            "compute_verdict",
            _build_verdict_str,
            default="duplicates completed",
        )
        if verdict is None:
            verdict = "duplicates completed"

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
                            # W-dogfood (W336 sibling family): 6-decimal
                            # rounding (matches cmd_search / cmd_intent
                            # / cmd_hover); 4-decimal collapsed ~72% of
                            # nonzero PR values to 0.0 on 5K+ graphs.
                            "pagerank": round(r["pagerank"] or 0, 6),
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
            # W607-BM: wrap the SARIF projection so a raise inside
            # ``duplicates_to_sarif`` surfaces a structured marker rather
            # than crashing the CI gate. Default is an empty SARIF
            # document shape so ``write_sarif`` still emits valid JSON.
            sarif_doc = _run_check_bm(
                "serialize_to_sarif",
                duplicates_to_sarif,
                sarif_envelope,
                default={
                    "version": "2.1.0",
                    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
                    "runs": [],
                },
            )
            click.echo(write_sarif(sarif_doc))
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
            if pairs_budget_hit:
                summary_payload["pairs_capped"] = True
                summary_payload["pairs_scored"] = len(pairs_to_check)
                summary_payload["pairs_budget"] = _MAX_PAIRS_TO_CHECK

            # W607-BM + W607-DD: surface substrate-call AND aggregation-
            # phase markers (preserved-list-field discipline). Both
            # families share the canonical ``duplicates_*`` prefix and
            # combine into the same top-level ``warnings_out`` + summary
            # mirror. Empty combined list -> no field added -> byte-
            # identical envelope on the happy path.
            _combined_warnings = list(_w607bm_warnings_out) + list(_w607dd_warnings_out)
            _envelope_kwargs: dict = {
                "summary": summary_payload,
                "budget": token_budget,
                "clusters": clusters_json,
            }
            if _combined_warnings:
                summary_payload["warnings_out"] = list(_combined_warnings)
                summary_payload["partial_success"] = True
                _envelope_kwargs["warnings_out"] = list(_combined_warnings)

            # W607-DD -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("duplicates", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name. Mirror of
            # cmd_dark_matter's W607-CZ serialize_envelope floor.
            _envelope_floor: dict = {
                "command": "duplicates",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": verdict,
                    "partial_success": True,
                    "warnings_out": list(_combined_warnings),
                },
                "warnings_out": list(_combined_warnings),
            }
            _envelope = _run_check_dd(
                "serialize_envelope",
                json_envelope,
                "duplicates",
                default=_envelope_floor,
                **_envelope_kwargs,
            )
            # W607-DD -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``duplicates_serialize_envelope_failed:`` marker was
            # appended to ``_w607dd_warnings_out`` and the floor stub
            # carries only the pre-raise combined list. Rebuild the
            # floor stub's warnings_out so the new marker reaches the
            # JSON output. Clean path -> envelope is the real
            # json_envelope return value, no rebuild needed.
            if _envelope is _envelope_floor and _w607dd_warnings_out:
                _combined_warnings = list(_w607bm_warnings_out) + list(_w607dd_warnings_out)
                _envelope_floor["summary"]["warnings_out"] = list(_combined_warnings)
                _envelope_floor["warnings_out"] = list(_combined_warnings)
                _envelope = _envelope_floor

            click.echo(to_json(_envelope))
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
