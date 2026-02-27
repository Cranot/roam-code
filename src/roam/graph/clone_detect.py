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

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam database connection (readonly).
    min_similarity : float
        Minimum Jaccard similarity threshold (0.0–1.0).
    min_lines : int
        Skip functions shorter than this.
    scope : str or None
        Limit to files matching this path prefix.
    max_functions : int
        Safety cap to avoid O(n^2) blowup.
    """
    from roam.index.parser import parse_file

    # 1. Get candidate files from DB
    scope_clause = ""
    params: list = []
    if scope:
        scope_norm = scope.replace("\\", "/")
        scope_clause = " AND f.path LIKE ?"
        params.append(f"{scope_norm}%")

    files = conn.execute(
        "SELECT f.id, f.path, f.language FROM files f WHERE f.language IS NOT NULL" + scope_clause,
        params,
    ).fetchall()

    # 2. Parse files and extract function hash bags
    funcs: list[_FuncInfo] = []
    idx = 0

    for f in files:
        file_path = f["path"]
        try:
            path = Path(file_path)
            if not path.is_absolute():
                from roam.db.connection import find_project_root

                path = find_project_root() / path
            if not path.exists():
                continue

            tree, source, lang = parse_file(path, f["language"])
            if tree is None or source is None:
                continue

            func_nodes = _find_function_nodes(tree)
            for fn_node in func_nodes:
                line_start = fn_node.start_point[0] + 1
                line_end = fn_node.end_point[0] + 1
                line_count = line_end - line_start + 1

                if line_count < min_lines:
                    continue

                name = _get_function_name(fn_node, source)
                body = _get_function_body(fn_node)
                bag, node_count = _ast_hash_bag(body)

                if node_count < _MIN_AST_NODES:
                    continue

                funcs.append(
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
        except Exception:
            continue

    if len(funcs) > max_functions:
        # Take the largest functions (most likely to have meaningful clones)
        funcs.sort(key=lambda f: -f.node_count)
        funcs = funcs[:max_functions]
        for i, f in enumerate(funcs):
            f.idx = i

    # 3. Pre-filter: bucket by node count (within 50% of each other)
    def _bucket(f: _FuncInfo) -> int:
        return f.node_count // max(f.node_count // 3, 5)

    by_bucket: dict[int, list[_FuncInfo]] = defaultdict(list)
    for f in funcs:
        by_bucket[_bucket(f)].append(f)

    # 4. Compare pairs within and between adjacent buckets
    pairs: list[ClonePair] = []
    uf = _UnionFind()
    pair_scores: dict[tuple[int, int], float] = {}

    checked = set()
    for bucket_key, members in by_bucket.items():
        # Collect candidates: same bucket + adjacent buckets
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

                # Quick size check: node counts within 50%
                ratio = min(a.node_count, b.node_count) / max(a.node_count, b.node_count)
                if ratio < 0.5:
                    continue

                sim = _jaccard_bags(a.hash_bag, b.hash_bag)
                if sim >= min_similarity:
                    pairs.append(
                        ClonePair(
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
                    )
                    pair_scores[key] = sim
                    uf.union(a.idx, b.idx)

    # 5. Build clusters from Union-Find
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
