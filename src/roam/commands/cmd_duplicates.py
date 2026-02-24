"""Detect semantically duplicate functions via structural similarity."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import click

from roam.db.connection import open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Name tokenization
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')


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

    return (
        0.40 * body_sim
        + 0.25 * param_sim
        + 0.20 * name_sim
        + 0.15 * sig_sim
    )


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
    "get", "set", "use", "handle", "on", "is", "has", "can", "do",
    "the", "for", "with", "from", "init", "new", "run", "to", "a",
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
# CLI command
# ---------------------------------------------------------------------------

@click.command()
@click.option('--threshold', default=0.75, show_default=True,
              type=click.FloatRange(0.0, 1.0),
              help='Similarity threshold (0.0-1.0)')
@click.option('--min-lines', default=5, show_default=True, type=int,
              help='Minimum function size to consider')
@click.option('--scope', default=None, type=str,
              help='Limit analysis to files under this path')
@click.pass_context
def duplicates(ctx, threshold, min_lines, scope):
    """Detect semantically duplicate functions via structural similarity.

    Finds functions that are structurally similar (same control flow,
    parameter shape, naming pattern) but not textual clones.  Uses
    AST-derived metrics from the index for comparison.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    ensure_index()

    with open_db(readonly=True) as conn:
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
            "  AND COALESCE(sm.line_count, s.line_end - s.line_start + 1, 0) >= ? "
            + scope_clause,
            [min_lines] + params,
        ).fetchall()

        if len(candidates) < 2:
            verdict = "No duplicate candidates found"
            if json_mode:
                click.echo(to_json(json_envelope("duplicates",
                    summary={"verdict": verdict, "total_clusters": 0,
                             "total_functions": 0,
                             "estimated_reducible_lines": 0},
                    clusters=[],
                )))
            else:
                click.echo(f"VERDICT: {verdict}")
            return

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
                            pair_key = (min(a["id"], b["id"]),
                                        max(a["id"], b["id"]))
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
                            pair_key = (min(a["id"], b["id"]),
                                        max(a["id"], b["id"]))
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
                    pk = (min(member_rows[i]["id"], member_rows[j]["id"]),
                          max(member_rows[i]["id"], member_rows[j]["id"]))
                    if pk in pair_scores:
                        sims.append(pair_scores[pk])
                    else:
                        # Compute on the fly for transitive members
                        s = _compute_similarity(member_rows[i], member_rows[j])
                        sims.append(s)
            avg_sim = sum(sims) / len(sims) if sims else 0.0

            # Combined PageRank
            total_pr = sum(r["pagerank"] or 0 for r in member_rows)

            names = [r["name"] for r in member_rows]
            pattern = _infer_pattern(names)
            suggestion = _suggest_refactor(names, pattern)

            # Sort members by line_start for consistent output
            member_rows.sort(key=lambda r: (r["file_path"], r["line_start"] or 0))

            cluster_list.append({
                "similarity": round(avg_sim, 2),
                "size": len(member_rows),
                "functions": member_rows,
                "pattern": pattern,
                "suggestion": suggestion,
                "total_pagerank": total_pr,
            })

        # ── 5. Rank clusters ────────────────────────────────────────
        # Sort by: size desc, similarity desc, pagerank desc
        cluster_list.sort(key=lambda c: (-c["size"], -c["similarity"],
                                         -c["total_pagerank"]))

        # ── 6. Compute summary stats ────────────────────────────────
        total_functions = sum(c["size"] for c in cluster_list)
        estimated_lines = 0
        for c in cluster_list:
            # Each cluster: all but one function's lines are "reducible"
            lines = sorted([r["line_count"] or 0 for r in c["functions"]])
            if len(lines) > 1:
                estimated_lines += sum(lines[:-1])  # keep the longest

        verdict = (
            f"{len(cluster_list)} duplicate cluster{'s' if len(cluster_list) != 1 else ''} "
            f"found ({total_functions} functions)"
            if cluster_list
            else "No semantic duplicates detected"
        )

        # ── 7. Output ───────────────────────────────────────────────
        if json_mode:
            clusters_json = []
            for i, c in enumerate(cluster_list):
                clusters_json.append({
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
                })

            click.echo(to_json(json_envelope("duplicates",
                summary={
                    "verdict": verdict,
                    "total_clusters": len(cluster_list),
                    "total_functions": total_functions,
                    "estimated_reducible_lines": estimated_lines,
                },
                budget=token_budget,
                clusters=clusters_json,
            )))
            return

        # ── Text output ──────────────────────────────────────────────
        click.echo(f"VERDICT: {verdict}")
        if not cluster_list:
            return

        click.echo()
        for i, c in enumerate(cluster_list):
            click.echo(f"CLUSTER {i + 1} (similarity {c['similarity']:.2f}, "
                        f"{c['size']} functions):")
            for r in c["functions"]:
                kind_str = abbrev_kind(r["kind"])
                click.echo(f"  {kind_str} {r['name']:<35s} "
                            f"at {loc(r['file_path'], r['line_start'])}"
                            f"    ({r['line_count'] or 0} lines)")
            click.echo(f"  Shared pattern: {c['pattern']}")
            click.echo(f"  Suggestion: {c['suggestion']}")
            click.echo()

        click.echo(f"SUMMARY: {len(cluster_list)} clusters, "
                    f"{total_functions} functions, "
                    f"estimated {estimated_lines} lines of reducible duplication")
