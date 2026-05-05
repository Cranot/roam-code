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

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

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


def _ast_hash_bag(node) -> tuple[Counter, int]:
    """Collect a multiset of subtree hashes for a tree-sitter node.

    Returns (hash_bag, total_node_count).
    Normalizes identifiers and literals so structurally identical code
    with different names produces the same hashes.
    """
    bag: Counter = Counter()
    count = 0

    def walk(n) -> int:
        nonlocal count

        if n.type in _SKIP_TYPES:
            return hash(("_skip_",))

        count += 1

        if n.child_count == 0:
            # Leaf node
            if n.type in _NORMALIZED_LEAF_TYPES:
                h = hash(("_leaf_", n.type))
            else:
                # Keywords, operators, punctuation — keep exact text
                h = hash(("_tok_", n.type, n.text))
            bag[h] += 1
            return h

        # Internal node — hash based on type + children structure
        child_hashes = []
        for child in n.children:
            if child.type not in _SKIP_TYPES:
                child_hashes.append(walk(child))

        h = hash(("_node_", n.type, tuple(child_hashes)))
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
        # Python
        "function_definition",
        # JS/TS
        "function_declaration",
        "method_definition",
        "arrow_function",
        # Java/C#/Kotlin/Scala
        "method_declaration",
        "constructor_declaration",
        # Go
        "function_declaration",
        "method_declaration",
        # Rust
        "function_item",
        # C/C++
        "function_definition",
        # Ruby
        "method",
        # PHP
        "function_definition",
        "method_declaration",
        # Swift
        "function_declaration",
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


def _extract_func_infos_from_file(file_row, min_lines: int, start_idx: int) -> list[_FuncInfo]:
    """Re-parse a single file and yield its qualifying ``_FuncInfo``s.

    Returns the list (possibly empty) and never raises — best-effort
    extraction is the contract callers rely on.
    """
    from roam.index.parser import parse_file

    file_path = file_row["path"]
    try:
        path = Path(file_path)
        if not path.is_absolute():
            from roam.db.connection import find_project_root

            path = find_project_root() / path
        if not path.exists():
            return []
        tree, source, _lang = parse_file(path, file_row["language"])
        if tree is None or source is None:
            return []
    except Exception:
        return []

    out: list[_FuncInfo] = []
    idx = start_idx
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


def _find_clone_pairs(
    funcs: list[_FuncInfo], min_similarity: float
) -> tuple[list[ClonePair], _UnionFind, dict[tuple[int, int], float]]:
    """Discover clone pairs by comparing functions inside the same bucket
    and across adjacent buckets. Returns (pairs, union_find, pair_scores)."""
    by_bucket = _bucket_funcs_by_size(funcs)
    pairs: list[ClonePair] = []
    uf = _UnionFind()
    pair_scores: dict[tuple[int, int], float] = {}
    checked: set[tuple[int, int]] = set()
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
                if key in checked:
                    continue
                checked.add(key)
                pair = _compare_func_pair(a, b, min_similarity)
                if pair is not None:
                    pairs.append(pair)
                    pair_scores[key] = pair.similarity
                    uf.union(a.idx, b.idx)
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

    funcs: list[_FuncInfo] = []
    for f in files:
        funcs.extend(_extract_func_infos_from_file(f, min_lines, len(funcs)))

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


def store_clones(conn, pairs: list[ClonePair], clusters: list[CloneCluster]) -> None:
    """Persist detection results to clone_pairs and clone_clusters tables.

    Truncates and replaces — clone results are always a complete snapshot,
    not incremental. Called from `roam clones --persist` and (eventually)
    the indexer's final stage.
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
