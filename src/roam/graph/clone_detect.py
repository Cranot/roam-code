"""AST-based structural clone detection via subtree hashing.

Detects Type-2 clones (identical structure, different identifiers/literals)
by hashing tree-sitter AST subtrees and comparing hash bags via Jaccard
similarity.  Works at both function-level and file-level granularity.

Algorithm:
  1. Parse each file with tree-sitter (reuses existing parser infrastructure)
  2. For each function body, walk the AST depth-first
  3. Hash each subtree as hash(node_type, tuple(child_hashes)), normalizing
     identifiers and literals so structurally identical code with different
     names produces the same hash
  4. Collect hash multisets (bags) per function
  5. Compare bags via Jaccard index: |A ∩ B| / |A ∪ B|
  6. Cluster similar functions via Union-Find

References:
  - SourcererCC (Sajnani et al., 2016) — token-based clone detection
  - PMD CPD — AST-based copy-paste detection
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# W93 follow-up: the clones detector is the proof-of-concept migration onto
# the central findings registry (``src/roam/db/findings.py``). Detector
# version is stamped on every emitted finding so consumers can spot rows
# produced under an older clone-detection shape (e.g. before a Jaccard
# tightening). Bump per the rules in roam.catalog.versions when the
# detector shape changes meaningfully.
CLONES_DETECTOR_VERSION: str = "1.0.0"

# Node types whose text values are normalized (treated as equivalent)
_NORMALIZED_LEAF_TYPES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
        # Literals
        "string",
        "string_fragment",
        "string_content",
        "template_string",
        "number",
        "integer",
        "float",
        "true",
        "false",
        "null",
        "none",
        "nil",
        "undefined",
    }
)

# Node types to skip entirely (don't affect structural comparison)
_SKIP_TYPES = frozenset(
    {
        "comment",
        "block_comment",
        "line_comment",
        "marginalia",
        "decorator",
        "annotation",
    }
)

# Minimum AST nodes in a function to be worth comparing
_MIN_AST_NODES = 8


@dataclass
class ClonePair:
    """A pair of structurally similar functions."""

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
    similarity: float


@dataclass
class CloneCluster:
    """A group of structurally similar functions."""

    cluster_id: int
    members: list[dict] = field(default_factory=list)
    avg_similarity: float = 0.0
    pattern: str = ""
    suggestion: str = ""


# ---------------------------------------------------------------------------
# AST hashing
# ---------------------------------------------------------------------------


def _stable_hash(value: bytes) -> int:
    """Return a deterministic 64-bit hash of *value*.

    Python's builtin ``hash()`` of strings/tuples-of-strings is randomized
    per process (via ``PYTHONHASHSEED``), which makes hash bags from one
    process incomparable with bags from another. We use the first 8 bytes
    of SHA-1 instead so the bags pickle-round-trip cleanly across
    ProcessPool workers.
    """
    import hashlib as _hashlib

    return int.from_bytes(_hashlib.sha1(value).digest()[:8], "big", signed=False)


def _ast_hash_bag(node) -> tuple[Counter, int]:
    """Collect a multiset of subtree hashes for a tree-sitter node.

    Returns (hash_bag, total_node_count).
    Normalizes identifiers and literals so structurally identical code
    with different names produces the same hashes.

    Uses a stable cross-process hash so bags computed in different worker
    processes (via ``_extract_func_records_pickleable``) compare correctly
    when later passed to ``_jaccard_bags``.
    """
    bag: Counter = Counter()
    count = 0
    _SKIP = b"_skip_"

    def walk(n) -> int:
        nonlocal count

        if n.type in _SKIP_TYPES:
            return _stable_hash(_SKIP)

        count += 1

        if n.child_count == 0:
            # Leaf node
            if n.type in _NORMALIZED_LEAF_TYPES:
                h = _stable_hash(b"_leaf_:" + n.type.encode("utf-8", errors="replace"))
            else:
                # Keywords, operators, punctuation — keep exact text
                text = n.text if isinstance(n.text, bytes) else str(n.text).encode("utf-8", errors="replace")
                h = _stable_hash(
                    b"_tok_:"
                    + n.type.encode("utf-8", errors="replace")
                    + b":"
                    + text
                )
            bag[h] += 1
            return h

        # Internal node — hash based on type + children structure
        child_hashes: list[int] = []
        for child in n.children:
            if child.type not in _SKIP_TYPES:
                child_hashes.append(walk(child))

        # Serialize child hashes as fixed-width little-endian bytes so the
        # resulting hash is stable across processes.
        children_bytes = b"".join(h.to_bytes(8, "big") for h in child_hashes)
        h = _stable_hash(
            b"_node_:" + n.type.encode("utf-8", errors="replace") + b":" + children_bytes
        )
        bag[h] += 1
        return h

    walk(node)
    return bag, count


def _jaccard_bags(a: Counter, b: Counter) -> float:
    """Jaccard similarity on multisets (bags)."""
    all_keys = set(a) | set(b)
    if not all_keys:
        return 1.0
    intersection = sum(min(a.get(k, 0), b.get(k, 0)) for k in all_keys)
    union = sum(max(a.get(k, 0), b.get(k, 0)) for k in all_keys)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Function node extraction from tree-sitter AST
# ---------------------------------------------------------------------------

_FUNCTION_NODE_TYPES = frozenset(
    {
        # Python / C / C++ all share `function_definition`
        "function_definition",
        # JS/TS / Swift share `function_declaration`
        "function_declaration",
        "method_definition",
        "arrow_function",
        # Java/C#/Kotlin/Scala / Go / PHP all share `method_declaration`
        "method_declaration",
        "constructor_declaration",
        # Rust
        "function_item",
        # Ruby
        "method",
    }
)


def _find_function_nodes(tree) -> list:
    """Walk tree-sitter AST and collect all function/method definition nodes."""
    results = []
    if tree is None:
        return results

    def walk(node):
        if node.type in _FUNCTION_NODE_TYPES:
            results.append(node)
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return results


def _get_function_name(node, source: bytes) -> str:
    """Extract function name from a tree-sitter function node."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "field_identifier", "name"):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return "<anonymous>"


def _get_function_body(node):
    """Find the body node of a function definition."""
    for child in node.children:
        if child.type in ("block", "statement_block", "compound_statement", "function_body", "method_body", "body"):
            return child
    # Fallback: use the function node itself
    return node


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
        groups: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            groups[self.find(x)].append(x)
        return dict(groups)


# ---------------------------------------------------------------------------
# Main detection pipeline
# ---------------------------------------------------------------------------


@dataclass
class _FuncInfo:
    """Internal struct for a parsed function."""

    idx: int
    file_path: str
    name: str
    qname: str
    line_start: int
    line_end: int
    node_count: int
    hash_bag: Counter


def _fetch_candidate_files(conn, scope: str | None):
    """Pull candidate file rows from the DB, optionally narrowed to a path
    prefix."""
    scope_clause = ""
    params: list = []
    if scope:
        scope_norm = scope.replace("\\", "/")
        scope_clause = " AND f.path LIKE ?"
        params.append(f"{scope_norm}%")
    return conn.execute(
        "SELECT f.id, f.path, f.language FROM files f WHERE f.language IS NOT NULL" + scope_clause,
        params,
    ).fetchall()


def _extract_func_records_pickleable(
    file_path: str,
    language: str | None,
    project_root_str: str,
    min_lines: int,
) -> list[tuple]:
    """Worker function: re-parse a single file and return picklable tuples.

    Returns a list of (file_path, name, line_start, line_end, node_count,
    hash_bag) tuples. Module-level so it can be pickled across processes.
    """
    from roam.index.parser import parse_file

    try:
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(project_root_str) / file_path
        if not path.exists():
            return []
        tree, source, _lang = parse_file(path, language)
        if tree is None or source is None:
            return []
    except Exception:
        return []

    out: list[tuple] = []
    for fn_node in _find_function_nodes(tree):
        line_start = fn_node.start_point[0] + 1
        line_end = fn_node.end_point[0] + 1
        if line_end - line_start + 1 < min_lines:
            continue
        name = _get_function_name(fn_node, source)
        body = _get_function_body(fn_node)
        bag, node_count = _ast_hash_bag(body)
        if node_count < _MIN_AST_NODES:
            continue
        # Counter pickles cleanly; basic types are JSON-safe.
        out.append((file_path, name, line_start, line_end, node_count, bag))
    return out


def _extract_func_infos_from_file(file_row, min_lines: int, start_idx: int) -> list[_FuncInfo]:
    """Re-parse a single file and yield its qualifying ``_FuncInfo``s.

    Returns the list (possibly empty) and never raises — best-effort
    extraction is the contract callers rely on. Thin wrapper around
    ``_extract_func_records_pickleable`` that fills in ``idx``.
    """
    from roam.db.connection import find_project_root

    file_path = file_row["path"]
    project_root = find_project_root()
    records = _extract_func_records_pickleable(
        file_path, file_row["language"], str(project_root), min_lines
    )

    out: list[_FuncInfo] = []
    idx = start_idx
    for file_path, name, line_start, line_end, node_count, bag in records:
        out.append(
            _FuncInfo(
                idx=idx,
                file_path=file_path,
                name=name,
                qname=f"{file_path}:{name}",
                line_start=line_start,
                line_end=line_end,
                node_count=node_count,
                hash_bag=bag,
            )
        )
        idx += 1
    return out


# Threshold: don't pay process-pool overhead on tiny projects.
# ProcessPool spinup on Windows is ~1-2s per worker. We need enough work
# to amortize ~8 × 1.5s = ~12s of fixed overhead. Empirically, ~500 files
# is the break-even on roam-code; smaller projects parse faster serially.
_PARALLEL_MIN_FILES = 500
# Cap workers to avoid context-switch thrashing and Windows process explosion.
_PARALLEL_MAX_WORKERS = 8


def _parallel_extract_func_infos(
    file_rows: list,
    min_lines: int,
) -> list[_FuncInfo]:
    """Extract func infos across all files, optionally in parallel.

    Falls back to serial when:
    - ``ROAM_NO_PARALLEL`` env var is set
    - fewer than ``_PARALLEL_MIN_FILES`` candidate files (process spinup > savings)

    Numerical ``idx`` is assigned by main process post-collection so results
    are deterministic regardless of worker completion order.
    """
    from roam.db.connection import find_project_root

    project_root_str = str(find_project_root())

    if os.environ.get("ROAM_NO_PARALLEL") or len(file_rows) < _PARALLEL_MIN_FILES:
        # Serial path
        all_records: list[tuple] = []
        for fr in file_rows:
            all_records.extend(
                _extract_func_records_pickleable(
                    fr["path"], fr["language"], project_root_str, min_lines
                )
            )
    else:
        # Parallel path — ProcessPool because work is CPU-bound (tree-sitter).
        from concurrent.futures import ProcessPoolExecutor

        workers = max(1, min(os.cpu_count() or 4, _PARALLEL_MAX_WORKERS))
        all_records = []
        args = [(fr["path"], fr["language"], project_root_str, min_lines) for fr in file_rows]
        try:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                # ex.map returns in submission order, preserving determinism.
                for recs in ex.map(_extract_func_records_pickleable_starmap, args):
                    all_records.extend(recs)
        except Exception:
            # If process pool fails (sandbox/pickling), fall back to serial.
            all_records = []
            for fr in file_rows:
                all_records.extend(
                    _extract_func_records_pickleable(
                        fr["path"], fr["language"], project_root_str, min_lines
                    )
                )

    # Build _FuncInfo with deterministic idx assignment.
    out: list[_FuncInfo] = []
    for idx, (file_path, name, line_start, line_end, node_count, bag) in enumerate(all_records):
        out.append(
            _FuncInfo(
                idx=idx,
                file_path=file_path,
                name=name,
                qname=f"{file_path}:{name}",
                line_start=line_start,
                line_end=line_end,
                node_count=node_count,
                hash_bag=bag,
            )
        )
    return out


def _extract_func_records_pickleable_starmap(args: tuple) -> list[tuple]:
    """Shim to pass tuple-of-args to ProcessPoolExecutor.map (no starmap)."""
    return _extract_func_records_pickleable(*args)


def _bucket_funcs_by_size(funcs: list[_FuncInfo]) -> dict[int, list[_FuncInfo]]:
    """Bucket functions by AST-node-count so candidate pairs are restricted
    to functions of similar size."""

    def _bucket(f: _FuncInfo) -> int:
        return f.node_count // max(f.node_count // 3, 5)

    by_bucket: dict[int, list[_FuncInfo]] = defaultdict(list)
    for f in funcs:
        by_bucket[_bucket(f)].append(f)
    return by_bucket


def _compare_func_pair(a: _FuncInfo, b: _FuncInfo, min_similarity: float) -> ClonePair | None:
    """Compute Jaccard similarity for one function pair. Returns a
    ``ClonePair`` if they pass the size + similarity gates, else None."""
    ratio = min(a.node_count, b.node_count) / max(a.node_count, b.node_count)
    if ratio < 0.5:
        return None
    sim = _jaccard_bags(a.hash_bag, b.hash_bag)
    if sim < min_similarity:
        return None
    return ClonePair(
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
        similarity=round(sim, 3),
    )


# Worker-side cache populated by ``_pair_worker_init``. Lives in each
# worker process for the duration of the pool.
_PAIR_WORKER_FUNCS: dict[int, _FuncInfo] = {}
_PAIR_WORKER_MIN_SIM: float = 0.0


def _pair_worker_init(funcs_serialized: list, min_similarity: float) -> None:
    """ProcessPool initializer: hydrate per-worker func cache once."""
    global _PAIR_WORKER_FUNCS, _PAIR_WORKER_MIN_SIM
    _PAIR_WORKER_FUNCS = {f.idx: f for f in funcs_serialized}
    _PAIR_WORKER_MIN_SIM = min_similarity


def _pair_worker_compare_batch(idx_pairs: list[tuple[int, int]]) -> list[tuple]:
    """Worker: compare a batch of (idx_a, idx_b) pairs from the cached funcs.

    Returns picklable tuples (idx_a, idx_b, ClonePair-as-tuple|None).
    """
    out: list[tuple] = []
    funcs = _PAIR_WORKER_FUNCS
    min_sim = _PAIR_WORKER_MIN_SIM
    for ia, ib in idx_pairs:
        a = funcs.get(ia)
        b = funcs.get(ib)
        if a is None or b is None:
            continue
        ratio = min(a.node_count, b.node_count) / max(a.node_count, b.node_count)
        if ratio < 0.5:
            continue
        sim = _jaccard_bags(a.hash_bag, b.hash_bag)
        if sim < min_sim:
            continue
        out.append(
            (
                ia,
                ib,
                round(sim, 3),
                a.file_path,
                a.name,
                a.qname,
                a.line_start,
                a.line_end,
                b.file_path,
                b.name,
                b.qname,
                b.line_start,
                b.line_end,
            )
        )
    return out


def _enumerate_candidate_pairs(funcs: list[_FuncInfo]) -> list[tuple[int, int]]:
    """Build the deduplicated (idx_a, idx_b) pair list from bucketed funcs."""
    by_bucket = _bucket_funcs_by_size(funcs)
    seen: set[tuple[int, int]] = set()
    pairs: list[tuple[int, int]] = []
    for bucket_key, members in by_bucket.items():
        candidates = list(members)
        for delta in (-1, 1):
            adj = by_bucket.get(bucket_key + delta)
            if adj:
                candidates.extend(adj)
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                key = (min(a.idx, b.idx), max(a.idx, b.idx))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(key)
    return pairs


def _find_clone_pairs(
    funcs: list[_FuncInfo], min_similarity: float
) -> tuple[list[ClonePair], _UnionFind, dict[tuple[int, int], float]]:
    """Discover clone pairs by comparing functions inside the same bucket
    and across adjacent buckets. Returns (pairs, union_find, pair_scores).

    When ``ROAM_NO_PARALLEL`` is unset and the candidate pair count is large,
    the pairwise Jaccard comparison is parallelized via ProcessPool. The
    Union-Find merge and pair-list assembly remain serial in the main
    process so output ordering is deterministic.
    """
    candidate_pairs = _enumerate_candidate_pairs(funcs)
    pairs: list[ClonePair] = []
    uf = _UnionFind()
    pair_scores: dict[tuple[int, int], float] = {}

    # Threshold: ProcessPool spinup is ~1-2s × 8 workers ≈ 12s of fixed
    # overhead on Windows. We need enough pair-comparison work to amortize
    # that. Empirically, ~100K candidate pairs is the break-even on
    # roam-code (1.99M pairs took 125s serial → 30s parallel, ratio holds
    # down to ~100K). Below that, the serial path wins.
    parallel_threshold = 100_000
    use_parallel = (
        not os.environ.get("ROAM_NO_PARALLEL")
        and len(candidate_pairs) >= parallel_threshold
    )

    if use_parallel:
        try:
            from concurrent.futures import ProcessPoolExecutor

            workers = max(1, min(os.cpu_count() or 4, _PARALLEL_MAX_WORKERS))
            # Chunk pairs so each worker processes ~1000 comparisons per batch
            # — minimizes IPC overhead while keeping load balanced.
            chunk_size = max(500, len(candidate_pairs) // (workers * 4))
            chunks = [
                candidate_pairs[i : i + chunk_size]
                for i in range(0, len(candidate_pairs), chunk_size)
            ]
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_pair_worker_init,
                initargs=(funcs, min_similarity),
            ) as ex:
                all_hits: list[tuple] = []
                # ex.map preserves submission order — chunks are submitted
                # in the same order as candidate_pairs was built, so the
                # resulting Union-Find call order matches the serial path
                # exactly. No extra sort needed.
                for hits in ex.map(_pair_worker_compare_batch, chunks):
                    all_hits.extend(hits)
            for (
                ia,
                ib,
                sim,
                fa,
                na,
                qa,
                la,
                lea,
                fb,
                nb,
                qb,
                lb,
                leb,
            ) in all_hits:
                pair = ClonePair(
                    file_a=fa,
                    func_a=na,
                    qname_a=qa,
                    line_a=la,
                    line_end_a=lea,
                    file_b=fb,
                    func_b=nb,
                    qname_b=qb,
                    line_b=lb,
                    line_end_b=leb,
                    similarity=sim,
                )
                pairs.append(pair)
                pair_scores[(ia, ib)] = sim
                uf.union(ia, ib)
            return pairs, uf, pair_scores
        except Exception:
            # Fall through to serial path on any pool failure.
            pass

    # Serial path
    funcs_by_idx = {f.idx: f for f in funcs}
    for ia, ib in candidate_pairs:
        a = funcs_by_idx[ia]
        b = funcs_by_idx[ib]
        pair = _compare_func_pair(a, b, min_similarity)
        if pair is not None:
            pairs.append(pair)
            pair_scores[(ia, ib)] = pair.similarity
            uf.union(ia, ib)
    return pairs, uf, pair_scores


def detect_clones(
    conn,
    *,
    min_similarity: float = 0.70,
    min_lines: int = 5,
    scope: str | None = None,
    max_functions: int = 2000,
) -> tuple[list[ClonePair], list[CloneCluster]]:
    """Detect structural code clones across the indexed codebase.

    Re-parses source files and compares function AST structures via
    subtree hashing.  Returns (pairs, clusters).
    """
    files = _fetch_candidate_files(conn, scope)

    # Per-file parsing is CPU-bound (tree-sitter) and independent across
    # files — parallelize via ProcessPool when there's enough work to
    # amortize spinup cost. Falls back to serial under ROAM_NO_PARALLEL.
    funcs: list[_FuncInfo] = _parallel_extract_func_infos(list(files), min_lines)

    if len(funcs) > max_functions:
        funcs.sort(key=lambda f: -f.node_count)
        funcs = funcs[:max_functions]
        for i, f in enumerate(funcs):
            f.idx = i

    pairs, uf, pair_scores = _find_clone_pairs(funcs, min_similarity)
    pairs.sort(key=lambda p: -p.similarity)

    func_by_idx = {f.idx: f for f in funcs}
    raw_clusters = uf.clusters()
    clusters: list[CloneCluster] = []
    cluster_id = 0

    for root, member_idxs in raw_clusters.items():
        if len(member_idxs) < 2:
            continue

        cluster_id += 1
        member_funcs = [func_by_idx[i] for i in member_idxs if i in func_by_idx]
        if len(member_funcs) < 2:
            continue

        # Average pairwise similarity
        sims = []
        for i in range(len(member_funcs)):
            for j in range(i + 1, len(member_funcs)):
                key = (min(member_funcs[i].idx, member_funcs[j].idx), max(member_funcs[i].idx, member_funcs[j].idx))
                if key in pair_scores:
                    sims.append(pair_scores[key])
                else:
                    s = _jaccard_bags(member_funcs[i].hash_bag, member_funcs[j].hash_bag)
                    sims.append(s)

        avg_sim = sum(sims) / len(sims) if sims else 0.0

        member_funcs.sort(key=lambda f: (f.file_path, f.line_start))
        members = [
            {
                "file": f.file_path,
                "function": f.name,
                "qualified_name": f.qname,
                "line_start": f.line_start,
                "line_end": f.line_end,
                "ast_nodes": f.node_count,
            }
            for f in member_funcs
        ]

        pattern = _infer_clone_pattern([f.name for f in member_funcs])
        suggestion = _suggest_extraction([f.name for f in member_funcs])

        clusters.append(
            CloneCluster(
                cluster_id=cluster_id,
                members=members,
                avg_similarity=round(avg_sim, 3),
                pattern=pattern,
                suggestion=suggestion,
            )
        )

    clusters.sort(key=lambda c: (-len(c.members), -c.avg_similarity))
    return pairs, clusters


# ---------------------------------------------------------------------------
# Persistence — populate clone_pairs and clone_clusters tables
# ---------------------------------------------------------------------------


def _resolve_symbol_id(conn, file_path: str, func_name: str, line_start: int) -> int | None:
    """Best-effort lookup of ``symbols.id`` for a (file, func, line) triple.

    Used by the findings-registry emit path so registry rows can be JOINed
    back to ``symbols`` when present. Returns ``None`` when the symbol
    can't be resolved (anonymous function, mismatched indexer state, or
    pre-W89 schema with no ``symbols`` table) — emit_finding tolerates a
    NULL subject_id by design.
    """
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? AND s.line_start = ? "
            "LIMIT 1",
            (file_path, func_name, line_start),
        ).fetchone()
        if row is not None:
            return int(row[0])
        # Line numbers occasionally drift between the indexer's symbol
        # extraction and tree-sitter's function-node start (decorator
        # rows, type alias prefixes). Fall back to name-only lookup
        # before giving up so we don't routinely emit subject_id=NULL.
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? "
            "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) "
            "LIMIT 1",
            (file_path, func_name, line_start),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        # Pre-W89 schema or symbols table absent — fall back to NULL.
        return None


def _clone_pair_finding_id(qname_a: str, qname_b: str) -> str:
    """Stable, sort-order-invariant finding id for one clone pair.

    Sorted qnames + a short SHA-1 prefix → deterministic regardless of
    which side is "a" vs "b" in this run. Re-running the detector on the
    same input upserts the same row rather than duplicating.
    """
    left, right = sorted((qname_a, qname_b))
    digest = hashlib.sha1(f"{left}|{right}".encode("utf-8")).hexdigest()[:12]
    return f"clones:pair:{digest}"


def store_clones(conn, pairs: list[ClonePair], clusters: list[CloneCluster]) -> None:
    """Persist detection results to clone_pairs and clone_clusters tables.

    Truncates and replaces — clone results are always a complete snapshot,
    not incremental. Called from `roam clones --persist` and (eventually)
    the indexer's final stage.

    W93 follow-up: every persisted clone pair ALSO emits a row to the
    central findings registry. The detector-specific tables remain
    authoritative; the findings rows are the denormalised cross-detector
    surface (``roam findings list/show/count``, future SARIF emit, …).
    The emit is wrapped in a defensive try/except so pre-W89 DBs (without
    the ``findings`` table) don't crash on this code path.
    """
    conn.execute("DELETE FROM clone_pairs")
    conn.execute("DELETE FROM clone_clusters")

    cluster_id_map: dict[int, int] = {}
    for c in clusters:
        canonical = c.members[0] if c.members else {}
        conn.execute(
            "INSERT INTO clone_clusters "
            "(id, canonical_qname, canonical_file, canonical_func, canonical_line, "
            " member_count, avg_similarity, pattern, suggestion) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c.cluster_id,
                canonical.get("qualified_name"),
                canonical.get("file"),
                canonical.get("function"),
                canonical.get("line_start"),
                len(c.members),
                c.avg_similarity,
                c.pattern,
                c.suggestion,
            ),
        )
        cluster_id_map[c.cluster_id] = c.cluster_id

    pair_to_cluster: dict[tuple[str, str], int] = {}
    for c in clusters:
        qnames = [m.get("qualified_name") for m in c.members]
        for i in range(len(qnames)):
            for j in range(i + 1, len(qnames)):
                key = tuple(sorted((qnames[i], qnames[j])))
                pair_to_cluster[key] = c.cluster_id

    for p in pairs:
        cluster_id = pair_to_cluster.get(tuple(sorted((p.qname_a, p.qname_b))))
        conn.execute(
            "INSERT INTO clone_pairs "
            "(qname_a, qname_b, file_a, file_b, func_a, func_b, "
            " line_a, line_end_a, line_b, line_end_b, similarity, cluster_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p.qname_a,
                p.qname_b,
                p.file_a,
                p.file_b,
                p.func_a,
                p.func_b,
                p.line_a,
                p.line_end_a,
                p.line_b,
                p.line_end_b,
                p.similarity,
                cluster_id,
            ),
        )

    # W93 follow-up — mirror each clone pair into the central findings
    # registry. Detector-specific table writes above remain authoritative;
    # this block is purely additive. Wrapped so a pre-W89 DB (where the
    # findings table doesn't exist) silently no-ops rather than crashing
    # the legacy clone_pairs write path that consumers like `roam critique`
    # and `roam retrieve` already depend on.
    try:
        _emit_clone_findings(conn, pairs)
    except sqlite3.OperationalError:
        # findings table missing (pre-W89 schema) — degrade gracefully.
        pass


def _emit_clone_findings(conn, pairs: list[ClonePair]) -> None:
    """Emit one ``FindingRecord`` per clone pair into the registry.

    Subject is the "a" side of the pair (the lower-sorted qname when we
    constructed the finding id; here we use whichever side ``ClonePair``
    carries — agents wanting both sides JOIN on ``evidence_json``). When
    the symbols-table lookup resolves the function, ``subject_id`` is
    populated; otherwise it stays NULL (registry permits NULL subjects
    for file/edge/commit findings).
    """
    # Import here so the legacy clone_pairs path doesn't pay the import
    # cost on every persist when callers haven't migrated to consume
    # findings yet (W93 itself just made the table maturity-experimental).
    from roam.db.findings import (
        CONFIDENCE_STRUCTURAL,
        FindingRecord,
        emit_finding,
    )

    for p in pairs:
        subject_id = _resolve_symbol_id(conn, p.file_a, p.func_a, p.line_a)
        partner_id = _resolve_symbol_id(conn, p.file_b, p.func_b, p.line_b)
        finding_id_str = _clone_pair_finding_id(p.qname_a, p.qname_b)
        evidence = {
            "qname_a": p.qname_a,
            "qname_b": p.qname_b,
            "file_a": p.file_a,
            "file_b": p.file_b,
            "func_a": p.func_a,
            "func_b": p.func_b,
            "line_a": p.line_a,
            "line_end_a": p.line_end_a,
            "line_b": p.line_b,
            "line_end_b": p.line_end_b,
            "similarity": p.similarity,
            "partner_symbol_id": partner_id,
        }
        # Confidence threshold mirrors the cmd_clones _classify_similarity
        # contract: only structural-level confidence when Jaccard >= 0.70.
        # Below that, the pair shouldn't even reach store_clones (the
        # detector default threshold is 0.70) — but keep the gate explicit
        # for future callers that pass --threshold below the floor.
        confidence = (
            CONFIDENCE_STRUCTURAL if p.similarity >= 0.70 else "heuristic"
        )
        claim = (
            f"{p.func_a} ({p.file_a}:{p.line_a}) is a structural clone of "
            f"{p.func_b} ({p.file_b}:{p.line_b}) — Jaccard {p.similarity:.2f}"
        )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id_str,
                subject_kind="symbol",
                subject_id=subject_id,
                claim=claim,
                evidence_json=json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="clones",
                source_version=CLONES_DETECTOR_VERSION,
            ),
        )


def get_clone_siblings(conn, file_path: str, func_name: str) -> list[dict]:
    """Return persisted clone siblings of a function.

    Used by `roam critique` (the clone-not-edited check) and by the retrieve
    reranker (clone-canonical-boost). Returns rows from clone_pairs where
    the queried function appears as either side of the pair.

    Each row: {sibling_qname, sibling_file, sibling_func, sibling_line,
              sibling_line_end, similarity, cluster_id}.
    Returns empty list if no clones persisted yet (run `roam clones --persist`).
    """
    qname = f"{file_path}:{func_name}"
    rows = conn.execute(
        "SELECT qname_b AS sibling_qname, file_b AS sibling_file, "
        "       func_b AS sibling_func, line_b AS sibling_line, "
        "       line_end_b AS sibling_line_end, similarity, cluster_id "
        "FROM clone_pairs WHERE qname_a = ? "
        "UNION "
        "SELECT qname_a AS sibling_qname, file_a AS sibling_file, "
        "       func_a AS sibling_func, line_a AS sibling_line, "
        "       line_end_a AS sibling_line_end, similarity, cluster_id "
        "FROM clone_pairs WHERE qname_b = ?",
        (qname, qname),
    ).fetchall()
    return [dict(r) for r in rows]


def get_cluster_members(conn, cluster_id: int) -> list[dict]:
    """Return all members of a clone cluster, derived from clone_pairs.

    Used by the retrieve reranker to surface the canonical sibling and
    deduplicate redundant matches.
    """
    rows = conn.execute(
        "SELECT DISTINCT qname_a AS qname, file_a AS file, func_a AS func, "
        "       line_a AS line_start, line_end_a AS line_end "
        "FROM clone_pairs WHERE cluster_id = ? "
        "UNION "
        "SELECT DISTINCT qname_b AS qname, file_b AS file, func_b AS func, "
        "       line_b AS line_start, line_end_b AS line_end "
        "FROM clone_pairs WHERE cluster_id = ?",
        (cluster_id, cluster_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pattern inference helpers
# ---------------------------------------------------------------------------

import re as _re

_SPLIT_RE = _re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")
_STOP_WORDS = {
    "get",
    "set",
    "use",
    "handle",
    "on",
    "is",
    "has",
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


def _name_tokens(name: str) -> set[str]:
    parts = _SPLIT_RE.findall(name)
    tokens = {p.lower() for p in parts if len(p) >= 2}
    for part in name.split("_"):
        for s in _SPLIT_RE.findall(part):
            if len(s) >= 2:
                tokens.add(s.lower())
    return tokens


def _infer_clone_pattern(names: list[str]) -> str:
    token_freq: Counter = Counter()
    for name in names:
        for t in _name_tokens(name) - _STOP_WORDS:
            token_freq[t] += 1

    common = [t for t, c in token_freq.most_common(3) if c >= 2]
    if common:
        return f"shared {common[0]} logic across {len(names)} functions"
    return f"identical control flow structure ({len(names)} functions)"


def _suggest_extraction(names: list[str]) -> str:
    token_freq: Counter = Counter()
    for name in names:
        for t in _name_tokens(name) - _STOP_WORDS:
            token_freq[t] += 1

    common = [t for t, c in token_freq.most_common(2) if c >= 2]
    if common:
        base = "_".join(common[:2])
        return f"Extract common logic into a generic {base}() helper"
    return "Extract shared logic into a parameterized helper function"
