"""Rename-invariant clone detection — DECKARD-style characteristic vectors.

The existing W95 ``clones`` detector (``src/roam/graph/clone_detect.py``)
hashes AST *subtree shapes* via SHA-1 and compares hash bags with Jaccard
similarity. That detector normalises identifiers and literals at the leaf
level, so a function pair that differs only in variable / parameter names
SHOULD already collide on hashes. In practice the Jaccard threshold (0.70+
default) is sensitive to small structural perturbations: a rename that
happens to add or drop one ``return`` early in the function body shifts a
handful of internal-node hashes and tanks the Jaccard score below
threshold. The result is recall holes on Type-2 alpha-renamed clones.

This module implements a *coarser*, recall-oriented detector that catches
the Type-2 cases the W95 detector misses. The core primitive is a
**characteristic vector** — the count of each AST node type appearing in
a function body. Two functions with identical characteristic vectors are
structurally identical at the type level, regardless of:

- variable / parameter names (caught by W95 too, in principle)
- literal values (caught by W95 too)
- the exact *shape* of internal nodes (NOT caught by W95 when nesting
  reorders subtree hashes)

The trade-off is precision: two functions can share a characteristic
vector while doing different things if the order of operations happens
to use the same node types (e.g. ``a + b`` vs ``b + a`` collide; ``a +
b`` vs ``a - b`` do NOT collide because ``+`` and ``-`` are different
operator-token node types in tree-sitter). We document this explicitly
in the test file. The intended use is to find pairs the W95 detector
misses — downstream callers can re-rank by W95 similarity if they want
higher precision.

Algorithm:

1.  Enumerate function symbols (``symbols.kind in ('function','method')``).
2.  Re-parse each owning file via the existing tree-sitter pipeline.
3.  Walk each function body, counting the frequency of each AST node
    type into a sparse ``dict[str, int]`` vector.
4.  Bucket functions by ``(node_count_bucket, top-3 node-type signature)``
    so we only run pairwise cosine on near-identical shapes. This is the
    pragmatic substitute for full LSH suggested by the W855 task spec.
5.  Inside each bucket, compute cosine similarity for every pair; emit a
    ``RenameClonePair`` finding for pairs at or above the configured
    threshold (default 0.95).

Confidence tier: ``structural`` (deterministic AST-level analysis, no
text heuristics).

LAW-4 anchors used by the verdict / fact strings: ``clones``, ``pairs``,
``findings``, ``markers``.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Re-use the existing function-node-type set + body-extraction helpers
# from the W95 detector. Importing keeps the two detectors in sync as
# new languages add function-node kinds.
from roam.graph.clone_detect import (
    _CLONE_ELIGIBLE_LANGUAGES,
    _SKIP_TYPES,
    _find_function_nodes,
    _get_function_body,
    _get_function_name,
)

# Slug uses underscores (matches the dataclass default); siblings use dashes — drift documented in W1138-followup
RENAME_INVARIANT_CLONE_DETECTOR = "rename_invariant_clones"
RENAME_INVARIANT_CLONE_DETECTOR_VERSION = "1.0.0"

# Skip extremely small functions — their vectors are too short to be
# discriminative. The W95 detector uses 8 AST nodes as the floor; we use
# the same floor for parity, so the two detectors compare like-for-like.
_MIN_AST_NODES = 8

# Bucket granularity for node-count grouping. We use **exact node-count
# match**: two functions that share a characteristic vector also share a
# node count, so an exact match preserves recall on the primary
# rename-invariant case while collapsing the O(n²) blow-up that wider
# buckets produce on real codebases (1.8M pairs on roam-code with a
# 10%-tolerance bucket vs ~2K pairs with exact match).
_BUCKET_EXACT_NODE_COUNT = True

# Signature length: bucket by the top-K most-frequent node types AND by
# the total distinct-type count. K=5 (vs K=3) discriminates Python
# function-call boilerplate from genuine control-flow shapes — most
# small Python functions top out on the same 3 types
# (``identifier``, ``.``, ``argument_list``), so K=3 alone over-buckets.
_SIGNATURE_TOP_K = 5


@dataclass
class RenameClonePair:
    """A pair of functions with near-identical AST node-type frequency vectors.

    Mirrors the public shape of ``roam.graph.clone_detect.ClonePair`` so
    downstream consumers can treat the two clone-pair stream types
    uniformly. The ``confidence`` field is fixed at ``"structural"`` per
    the CLAUDE.md confidence-tier vocabulary.
    """

    file_a: str
    func_a: str
    qname_a: str
    line_a: int
    line_end_a: int
    file_b: str
    func_b: str
    qname_b: str
    line_b: int
    line_end_b: int
    cosine_similarity: float
    node_count_a: int
    node_count_b: int
    confidence: str = "structural"
    detector: str = "rename_invariant_clones"


@dataclass
class _FuncVector:
    """One function's characteristic vector ready for bucketing + comparison."""

    file_path: str
    name: str
    qname: str
    line_start: int
    line_end: int
    node_count: int
    vector: Counter[str] = field(default_factory=Counter)
    norm: float = 0.0
    top_k_signature: tuple[str, ...] = ()

    def finalise(self) -> None:
        """Pre-compute the L2 norm and top-K signature for fast bucketing."""
        self.norm = math.sqrt(sum(c * c for c in self.vector.values()))
        # Sort by (-count, type) so the signature is deterministic when
        # counts tie. We pick top-K node types by frequency.
        sorted_types = sorted(self.vector.items(), key=lambda kv: (-kv[1], kv[0]))
        self.top_k_signature = tuple(t for t, _ in sorted_types[:_SIGNATURE_TOP_K])


# ---------------------------------------------------------------------------
# Vector extraction
# ---------------------------------------------------------------------------


def _node_type_vector(node) -> tuple[Counter[str], int]:
    """Walk a tree-sitter node and return (type_frequency, total_count).

    Skips comment / decorator / annotation nodes — these are stylistic
    noise that should not affect clone detection. (Same skip set as the
    W95 detector for consistency.)
    """
    counts: Counter[str] = Counter()
    total = 0

    def walk(n) -> None:
        nonlocal total
        if n.type in _SKIP_TYPES:
            return
        counts[n.type] += 1
        total += 1
        for child in n.children:
            walk(child)

    walk(node)
    return counts, total


def _extract_function_vectors(
    file_path: str,
    language: str | None,
    project_root: Path,
    min_lines: int,
) -> list[_FuncVector]:
    """Re-parse one file and return a finalised _FuncVector per function.

    Mirrors the file-iteration shape of
    ``roam.graph.clone_detect._extract_func_records_pickleable`` so the
    two detectors index the same set of functions.
    """
    from roam.index.parser import parse_file

    path = Path(file_path)
    if not path.is_absolute():
        path = project_root / file_path
    if not path.exists():
        return []

    try:
        tree, source, _lang = parse_file(path, language)
    except Exception:
        return []
    if tree is None or source is None:
        return []

    out: list[_FuncVector] = []
    for fn_node in _find_function_nodes(tree):
        line_start = fn_node.start_point[0] + 1
        line_end = fn_node.end_point[0] + 1
        if line_end - line_start + 1 < min_lines:
            continue
        name = _get_function_name(fn_node, source)
        body = _get_function_body(fn_node)
        vec, node_count = _node_type_vector(body)
        if node_count < _MIN_AST_NODES:
            continue
        fv = _FuncVector(
            file_path=file_path,
            name=name,
            qname=f"{file_path}:{name}",
            line_start=line_start,
            line_end=line_end,
            node_count=node_count,
            vector=vec,
        )
        fv.finalise()
        out.append(fv)
    return out


# ---------------------------------------------------------------------------
# Bucketing + pairwise comparison
# ---------------------------------------------------------------------------


def _bucket_key(fv: _FuncVector) -> tuple[int, int, tuple[str, ...]]:
    """Map a function vector to a bucket key.

    Bucket: ``(node_count, distinct_type_count, top_k_signature)``.
    Two functions in the same bucket have an identical *characteristic
    fingerprint*; characteristic-vector clones (which by definition
    share the underlying node-type counts) always bucket together, so
    recall is preserved. Distinct functions almost never collide on all
    three components, which is what keeps the pair count manageable.
    """
    distinct_types = len(fv.vector)
    return (fv.node_count, distinct_types, fv.top_k_signature)


def _cosine(a: _FuncVector, b: _FuncVector) -> float:
    """Cosine similarity on the two characteristic vectors."""
    if a.norm == 0.0 or b.norm == 0.0:
        return 0.0
    # Iterate over the smaller vector for speed
    if len(a.vector) > len(b.vector):
        a, b = b, a
    dot = 0
    for k, v in a.vector.items():
        bv = b.vector.get(k)
        if bv is not None:
            dot += v * bv
    return dot / (a.norm * b.norm)


def _fetch_candidate_files(conn: sqlite3.Connection, scope: str | None):
    """Pull (id, path, language) rows for files we should re-parse.

    Mirrors ``roam.graph.clone_detect._fetch_candidate_files`` — same
    columns + same scope semantics + same ``_CLONE_ELIGIBLE_LANGUAGES``
    allowlist so both detectors see the same candidate set.
    """
    placeholders = ",".join("?" * len(_CLONE_ELIGIBLE_LANGUAGES))
    sql = f"SELECT f.id, f.path, f.language FROM files f WHERE f.language IN ({placeholders})"
    params: list = sorted(_CLONE_ELIGIBLE_LANGUAGES)
    if scope:
        scope_norm = scope.replace("\\", "/")
        sql += " AND f.path LIKE ?"
        params.append(f"{scope_norm}%")
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_rename_invariant_clones(
    conn: sqlite3.Connection,
    *,
    similarity_threshold: float = 0.95,
    min_lines: int = 5,
    scope: str | None = None,
    project_root: Path | None = None,
) -> list[RenameClonePair]:
    """Find Type-2 alpha-renamed clones via characteristic-vector matching.

    Parameters
    ----------
    conn:
        Read-only roam DB connection. Used to enumerate candidate files
        from the ``files`` table; the actual AST work happens by
        re-parsing the file off disk via the standard parser pipeline.
    similarity_threshold:
        Minimum cosine similarity for two functions to count as a clone
        pair. Default 0.95 — characteristic vectors are coarse, so we
        want a high threshold to keep false-positive rate low.
    min_lines:
        Skip functions shorter than this. Default 5 matches the W95
        detector default so both run over the same function set.
    scope:
        Optional path prefix; only files under this prefix are scanned.
    project_root:
        Project root for resolving relative file paths. When ``None``,
        the standard ``find_project_root()`` helper is used.

    Returns
    -------
    list[RenameClonePair]
        Sorted descending by cosine similarity. Empty when no qualifying
        pair clears the threshold.
    """
    if project_root is None:
        from roam.db.connection import find_project_root

        project_root = find_project_root()

    rows = _fetch_candidate_files(conn, scope)

    all_vectors: list[_FuncVector] = []
    for row in rows:
        # Row may be a sqlite3.Row or a tuple depending on conn factory.
        path = row["path"] if hasattr(row, "keys") else row[1]
        lang = row["language"] if hasattr(row, "keys") else row[2]
        all_vectors.extend(_extract_function_vectors(path, lang, project_root, min_lines))

    # Group by bucket key. Buckets isolate near-identical shapes so we
    # only pay O(n^2) within a small bucket, not over the whole corpus.
    buckets: dict[tuple[int, tuple[str, ...]], list[_FuncVector]] = defaultdict(list)
    for fv in all_vectors:
        buckets[_bucket_key(fv)].append(fv)

    pairs: list[RenameClonePair] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        n = len(members)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = members[i], members[j]
                # Avoid pairing a function with itself (same file + same
                # name + same start line is the indexer round-trip).
                if a.file_path == b.file_path and a.name == b.name and a.line_start == b.line_start:
                    continue
                sim = _cosine(a, b)
                if sim < similarity_threshold:
                    continue
                pairs.append(
                    RenameClonePair(
                        file_a=a.file_path,
                        func_a=a.name,
                        qname_a=a.qname,
                        line_a=a.line_start,
                        line_end_a=a.line_end,
                        file_b=b.file_path,
                        func_b=b.name,
                        qname_b=b.qname,
                        line_b=b.line_start,
                        line_end_b=b.line_end,
                        cosine_similarity=sim,
                        node_count_a=a.node_count,
                        node_count_b=b.node_count,
                    )
                )

    pairs.sort(key=lambda p: -p.cosine_similarity)
    return pairs


__all__ = [
    "RENAME_INVARIANT_CLONE_DETECTOR",
    "RENAME_INVARIANT_CLONE_DETECTOR_VERSION",
    "RenameClonePair",
    "detect_rename_invariant_clones",
]


# ---------------------------------------------------------------------------
# Test helpers (importable so the test file can exercise the inner pieces
# without going through the SQLite-backed entry point).
# ---------------------------------------------------------------------------


def _vectorise_source(source: str, language: str = "python") -> list[_FuncVector]:
    """Parse a literal source snippet and return _FuncVector per function.

    Used by the tests to exercise the vector / cosine layer without
    needing a full project + SQLite DB.
    """
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES

    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    tree = parser.parse(source.encode("utf-8"))

    out: list[_FuncVector] = []
    for fn_node in _find_function_nodes(tree):
        line_start = fn_node.start_point[0] + 1
        line_end = fn_node.end_point[0] + 1
        name = _get_function_name(fn_node, source.encode("utf-8"))
        body = _get_function_body(fn_node)
        vec, node_count = _node_type_vector(body)
        if node_count < _MIN_AST_NODES:
            continue
        fv = _FuncVector(
            file_path="<inline>",
            name=name,
            qname=f"<inline>:{name}",
            line_start=line_start,
            line_end=line_end,
            node_count=node_count,
            vector=vec,
        )
        fv.finalise()
        out.append(fv)
    return out
