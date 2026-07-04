"""Dark matter detection: co-changing files with no structural dependency.

Public helpers
==============

* :func:`dark_matter_edges` — full repo scan returning hidden file-pair
  couplings ranked by NPMI.
* :func:`co_change_score` — symbol-pair Jaccard score in [0, 1] used by
  the retrieve reranker (β signal), the patch verifier (`roam critique`
  dark-matter check), and the fleet planner's conflict-edge weighting.
  One helper, three downstream consumers.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path

from roam.db.connection import batched_in


def _npmi(p_ab: float, p_a: float, p_b: float) -> float:
    """Normalized Pointwise Mutual Information [-1, +1]."""
    if p_ab <= 0 or p_a <= 0 or p_b <= 0:
        return -1.0
    pmi = math.log(p_ab / (p_a * p_b))
    neg_log_pab = -math.log(p_ab)
    if neg_log_pab == 0:
        return 1.0
    return pmi / neg_log_pab


def dark_matter_edges(conn, *, min_cochanges: int = 3, min_npmi: float = 0.3) -> list[dict]:
    """Find co-changing file pairs with no structural dependency.

    Returns list of dicts sorted by NPMI descending:
        {file_id_a, file_id_b, path_a, path_b, npmi, lift, strength, cochange_count}
    """
    # Total commits
    row = conn.execute("SELECT COUNT(*) FROM git_commits").fetchone()
    total_commits = max(row[0] if row else 1, 1)

    # Per-file commit counts
    file_commits: dict[int, int] = {}
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    # Co-change pairs above threshold
    cochange_rows = conn.execute(
        "SELECT file_id_a, file_id_b, cochange_count FROM git_cochange WHERE cochange_count >= ?",
        (min_cochanges,),
    ).fetchall()

    # Bidirectional structural edge set (any edge = not dark matter)
    structural: set[tuple[int, int]] = set()
    for fe in conn.execute("SELECT source_file_id, target_file_id FROM file_edges WHERE symbol_count >= 1").fetchall():
        structural.add((fe["source_file_id"], fe["target_file_id"]))
        structural.add((fe["target_file_id"], fe["source_file_id"]))

    # File path lookup
    id_to_path: dict[int, str] = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        id_to_path[f["id"]] = f["path"]

    results: list[dict] = []
    for r in cochange_rows:
        fid_a, fid_b = r["file_id_a"], r["file_id_b"]
        if (fid_a, fid_b) in structural:
            continue

        cochanges = r["cochange_count"]
        ca = file_commits.get(fid_a, 1)
        cb = file_commits.get(fid_b, 1)

        p_ab = cochanges / total_commits
        p_a = ca / total_commits
        p_b = cb / total_commits
        npmi = _npmi(p_ab, p_a, p_b)

        if npmi < min_npmi:
            continue

        avg = (ca + cb) / 2
        strength = cochanges / avg if avg > 0 else 0
        lift = (cochanges * total_commits) / max(ca * cb, 1)

        results.append(
            {
                "file_id_a": fid_a,
                "file_id_b": fid_b,
                "path_a": id_to_path.get(fid_a, f"file_id={fid_a}"),
                "path_b": id_to_path.get(fid_b, f"file_id={fid_b}"),
                "npmi": round(npmi, 3),
                "lift": round(lift, 2),
                "strength": round(strength, 2),
                "cochange_count": cochanges,
            }
        )

    results.sort(key=lambda x: -x["npmi"])
    return results


# ---------------------------------------------------------------------------
# Symbol-level co-change score (β signal for retrieve, critique, fleet)
# ---------------------------------------------------------------------------


def file_co_change_score(
    conn: sqlite3.Connection,
    file_id_a: int,
    file_id_b: int,
) -> float:
    """Jaccard-style co-change score between two file ids in [0, 1].

    Returns 1.0 for two file ids that always change together; 0.0 when
    they have never been touched in the same commit; ``0.0`` if either
    file id is missing or has zero commits. Same-file pairs short-circuit
    to ``0.0`` because the reranker shouldn't double-count a candidate
    with itself.

    Implementation:
    * looks up the directional ``git_cochange`` row,
    * uses ``file_stats.commit_count`` for each file's total touches,
    * returns ``cochanges / (commits_a + commits_b - cochanges)``.

    The function is *cheap* — three indexed reads — and intentionally
    has no caching so it's safe to call inside a tight rerank loop on
    a long-lived daemon connection.
    """
    if file_id_a == file_id_b:
        return 0.0
    a, b = (file_id_a, file_id_b) if file_id_a < file_id_b else (file_id_b, file_id_a)

    row = conn.execute(
        "SELECT cochange_count FROM git_cochange "
        "WHERE (file_id_a = ? AND file_id_b = ?) "
        "   OR (file_id_a = ? AND file_id_b = ?) "
        "LIMIT 1",
        (a, b, b, a),
    ).fetchone()
    if not row or not row[0]:
        return 0.0
    cochanges = float(row[0])

    counts = conn.execute(
        f"SELECT file_id, commit_count FROM file_stats WHERE file_id IN ({','.join('?' * 2)})",
        (a, b),
    ).fetchall()
    commit_map: dict[int, int] = {r[0]: r[1] or 0 for r in counts}
    ca = float(commit_map.get(a, 0))
    cb = float(commit_map.get(b, 0))
    union = ca + cb - cochanges
    if union <= 0:
        return 0.0
    return min(1.0, cochanges / union)


def _canonical_pairs(
    candidates: list[int],
    seeds: list[int],
) -> set[tuple[int, int]]:
    """Collapse a candidate×seed cross-product to canonical (min, max) keys.

    WHY: ``file_co_change_score`` treats order as irrelevant and same-file
    pairs as zero. The bulk fetch only needs one canonical key per distinct
    unordered pair, so this shrinks the DB query surface before any SQL is
    issued.
    """
    pairs: set[tuple[int, int]] = set()
    for c in candidates:
        for s in seeds:
            if c == s:
                continue
            pairs.add((c, s) if c < s else (s, c))
    return pairs


def _load_cochange_map(
    conn: sqlite3.Connection,
    file_ids: set[int],
) -> dict[tuple[int, int], float]:
    """Bulk-fetch cochange counts for any pair among ``file_ids``.

    WHY: one batched IN-query replaces ``O(C × S)`` per-pair lookups while
    still returning a canonical-keyed map the scorer can use blindly.
    """
    cochange_map: dict[tuple[int, int], float] = {}
    for row in batched_in(
        conn,
        "SELECT file_id_a, file_id_b, cochange_count FROM git_cochange "
        "WHERE file_id_a IN ({ph}) AND file_id_b IN ({ph})",
        file_ids,
    ):
        fa, fb, cnt = int(row[0]), int(row[1]), row[2]
        if not cnt:
            continue
        key = (fa, fb) if fa < fb else (fb, fa)
        cochange_map[key] = float(cnt)
    return cochange_map


def _load_commit_map(
    conn: sqlite3.Connection,
    file_ids: set[int],
) -> dict[int, int]:
    """Bulk-fetch commit counts for ``file_ids``."""
    commit_map: dict[int, int] = {}
    for row in batched_in(
        conn,
        "SELECT file_id, commit_count FROM file_stats WHERE file_id IN ({ph})",
        file_ids,
    ):
        commit_map[int(row[0])] = row[1] or 0
    return commit_map


def _pair_score(
    c: int,
    s: int,
    cochange_map: dict[tuple[int, int], float],
    commit_map: dict[int, int],
) -> float | None:
    """Score a single (candidate, seed) pair from prefetched maps.

    WHY: isolates the per-pair guard logic (same-file skip, missing
    cochange, degenerate union, cap) so the cross-product loop stays a
    simple map lookup.
    """
    if c == s:
        return None
    key = (c, s) if c < s else (s, c)
    cochanges = cochange_map.get(key)
    if not cochanges:
        return None
    ca = float(commit_map.get(key[0], 0))
    cb = float(commit_map.get(key[1], 0))
    union = ca + cb - cochanges
    if union <= 0:
        return None
    score = min(1.0, cochanges / union)
    return score if score > 0 else None


def _score_cross_product(
    candidates: list[int],
    seeds: list[int],
    cochange_map: dict[tuple[int, int], float],
    commit_map: dict[int, int],
) -> dict[tuple[int, int], float]:
    """Score every candidate×seed pair from prefetched maps.

    WHY: turn the canonical-keyed DB results back into the original
    candidate×seed scores without issuing any further SQL.
    """
    out: dict[tuple[int, int], float] = {}
    for c in candidates:
        for s in seeds:
            score = _pair_score(c, s, cochange_map, commit_map)
            if score is not None:
                out[(c, s)] = score
    return out


def file_co_change_scores_bulk(
    conn: sqlite3.Connection,
    candidate_file_ids: Iterable[int],
    seed_file_ids: Iterable[int],
) -> dict[tuple[int, int], float]:
    """Bulk co-change scores for every ``(candidate_file, seed_file)`` pair.

    Output-identical drop-in for calling :func:`file_co_change_score`
    once per pair, but issues a *bounded* number of SQL round-trips
    (two batched IN-queries) instead of ``~2 x C x S``. The retrieve
    reranker uses this so the ``C x S`` β loop becomes in-memory dict
    lookups (W: latent N+1 fix).

    Returns a dict keyed by the **original** ``(candidate_file_id,
    seed_file_id)`` pair. A pair is present only when its score would be
    ``> 0`` under :func:`file_co_change_score`; absent pairs score 0.0.
    Same-file pairs are never inserted (the per-pair helper short-circuits
    those to 0.0).

    The per-pair score is replicated byte-for-byte:
    * ``git_cochange`` is canonically stored with ``file_id_a < file_id_b``
      (see ``index/git_stats.py:compute_cochange``), so a single
      canonical ``(min, max)`` lookup matches the per-pair
      ``OR ... LIMIT 1`` query exactly;
    * ``file_stats.commit_count`` defaults to 0 when a file id is absent,
      mirroring ``commit_map.get(..., 0)``;
    * ``union = ca + cb - cochanges``; ``min(1.0, cochanges / union)``.
    """
    cand_list = [int(c) for c in candidate_file_ids]
    seed_list = [int(s) for s in seed_file_ids]
    if not cand_list or not seed_list:
        return {}

    canon_pairs = _canonical_pairs(cand_list, seed_list)
    if not canon_pairs:
        return {}

    file_ids: set[int] = set()
    for a, b in canon_pairs:
        file_ids.add(a)
        file_ids.add(b)

    cochange_map = _load_cochange_map(conn, file_ids)
    if not cochange_map:
        return {}

    commit_map = _load_commit_map(conn, file_ids)
    return _score_cross_product(cand_list, seed_list, cochange_map, commit_map)


def _load_seed_files_for_max_score(
    conn: sqlite3.Connection,
    seed_symbol_ids: Iterable[int],
) -> set[int]:
    """Resolve seed symbols to file ids without imposing incidental order.

    WHY: the seed side is consumed as a max-over-set selection. Preserving
    membership matters; sorting the ids only spends work on an order the
    scoring contract never observes.
    """
    seed_ids = {int(s) for s in seed_symbol_ids}
    if not seed_ids:
        return set()

    seed_files: set[int] = set()
    for row in batched_in(
        conn,
        "SELECT DISTINCT file_id FROM symbols WHERE id IN ({ph})",
        seed_ids,
    ):
        if row[0] is not None:
            seed_files.add(int(row[0]))
    return seed_files


def co_change_scores_to_seed_set_bulk(
    conn: sqlite3.Connection,
    candidate_symbol_ids: Iterable[int],
    seed_symbol_ids: list[int] | set[int],
) -> dict[int, float]:
    """Bulk version of :func:`co_change_score_to_seed_set` for many candidates.

    Output-identical to calling :func:`co_change_score_to_seed_set` once
    per candidate symbol, but resolves all symbol→file ids in two batched
    queries and pre-fetches the whole ``(candidate_file x seed_file)``
    co-change matrix in one bulk pass (see
    :func:`file_co_change_scores_bulk`).

    Returns a dict ``{candidate_symbol_id: best_score}`` containing only
    candidates whose max co-change score against any seed file is ``> 0``
    — matching the ``if score > 0`` filter the reranker applies to the
    per-candidate path.
    """
    cand_list = [int(c) for c in candidate_symbol_ids]
    seed_ids = {int(s) for s in seed_symbol_ids}
    if not cand_list or not seed_ids:
        return {}

    # Resolve candidate symbol -> file id (bulk). co_change_score_to_seed_set
    # reads exactly one file_id per candidate symbol; preserve "missing or
    # NULL file_id => candidate absent" semantics.
    cand_file: dict[int, int] = {}
    for row in batched_in(
        conn,
        "SELECT id, file_id FROM symbols WHERE id IN ({ph})",
        set(cand_list),
    ):
        if row[1] is not None:
            cand_file[int(row[0])] = int(row[1])

    seed_files = _load_seed_files_for_max_score(conn, seed_ids)
    if not seed_files:
        return {}

    candidate_files = set(cand_file.values())
    pair_scores = file_co_change_scores_bulk(conn, candidate_files, seed_files)

    out: dict[int, float] = {}
    for cand_sym in cand_list:
        cfile = cand_file.get(cand_sym)
        if cfile is None:
            continue
        # Mirror the per-candidate short-circuit: seed set equal to just
        # the candidate's own file scores 0.0.
        if seed_files == {cfile}:
            continue
        best = 0.0
        for sf in seed_files:
            if sf == cfile:
                continue
            score = pair_scores.get((cfile, sf), 0.0)
            if score > best:
                best = score
        if best > 0:
            out[cand_sym] = best
    return out


def co_change_score(
    conn: sqlite3.Connection,
    symbol_id_a: int,
    symbol_id_b: int,
) -> float:
    """Jaccard-style co-change score between two symbols in [0, 1].

    Resolves each symbol to its file id (one indexed read each) and
    delegates to :func:`file_co_change_score`. Same-file pairs short
    to ``0.0``; missing symbols return ``0.0``.

    Used by:
    * **retrieve reranker** — β contribution per candidate against the
      seed file set;
    * **critique** dark-matter check — surfaces files that *should*
      have moved with the patch but didn't;
    * **fleet planner** conflict-edge weighting.
    """
    if symbol_id_a == symbol_id_b:
        return 0.0
    rows = conn.execute(
        f"SELECT id, file_id FROM symbols WHERE id IN ({','.join('?' * 2)})",
        (symbol_id_a, symbol_id_b),
    ).fetchall()
    if len(rows) != 2:
        return 0.0
    file_map = {r[0]: r[1] for r in rows}
    file_a = file_map.get(symbol_id_a)
    file_b = file_map.get(symbol_id_b)
    if file_a is None or file_b is None:
        return 0.0
    return file_co_change_score(conn, int(file_a), int(file_b))


def co_change_score_to_seed_set(
    conn: sqlite3.Connection,
    candidate_symbol_id: int,
    seed_symbol_ids: list[int] | set[int],
) -> float:
    """Max co-change score between *candidate* and any of the seed symbols.

    The retrieve reranker uses this to populate β: a candidate that
    co-changes strongly with *any* of the query's seed files inherits
    its full weight. Returns 0.0 when the candidate's file shares no
    history with the seed files.
    """
    if not seed_symbol_ids:
        return 0.0
    candidate_file_row = conn.execute(
        "SELECT file_id FROM symbols WHERE id = ?",
        (candidate_symbol_id,),
    ).fetchone()
    if not candidate_file_row or candidate_file_row[0] is None:
        return 0.0
    candidate_file = int(candidate_file_row[0])

    seed_list = list(seed_symbol_ids)
    if not seed_list:
        return 0.0
    seed_files: set[int] = set()
    for chunk_start in range(0, len(seed_list), 400):
        chunk = seed_list[chunk_start : chunk_start + 400]
        rows = conn.execute(
            f"SELECT DISTINCT file_id FROM symbols WHERE id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for row in rows:
            if row[0] is not None:
                seed_files.add(int(row[0]))

    if not seed_files or seed_files == {candidate_file}:
        return 0.0

    best = 0.0
    for sf in seed_files:
        if sf == candidate_file:
            continue
        s = file_co_change_score(conn, candidate_file, sf)
        if s > best:
            best = s
    return best


# ---------------------------------------------------------------------------
# Hypothesis Engine
# ---------------------------------------------------------------------------

_RE_TABLE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+[`"\']?(\w+)[`"\']?',
    re.IGNORECASE,
)
# Negative filter: Python `from X import` and JS/TS `import ... from "X"` look
# identical to the SQL `FROM` verb under a naive regex. Drop lines that match
# either shape before declaring a "shared table" — otherwise every Python file
# co-occurs on `__future__`, `typing`, etc. and the SHARED_DB classifier
# fires false positives on 100% of Python-only co-changing pairs.
_RE_PYTHON_IMPORT_LINE = re.compile(r"^\s*from\s+[\w.]+\s+import\b", re.MULTILINE)
_RE_JS_IMPORT_LINE = re.compile(r'^\s*import\b[^;\n]*\bfrom\s+[`"\']', re.MULTILINE)


def _extract_sql_tables(text: str) -> set[str]:
    """Extract candidate SQL table names from `text`, excluding lines that are
    Python `from X import` or JS/TS `import ... from "X"` statements (which the
    naive _RE_TABLE regex would otherwise classify as SQL).
    """
    if not text:
        return set()
    # Strip lines that look like Python/JS imports before scanning.
    stripped_lines = []
    for line in text.splitlines():
        if _RE_PYTHON_IMPORT_LINE.match(line):
            continue
        if _RE_JS_IMPORT_LINE.match(line):
            continue
        stripped_lines.append(line)
    return set(_RE_TABLE.findall("\n".join(stripped_lines)))


_RE_EVENT_EMIT = re.compile(
    r'\.\s*(?:emit|dispatch|publish)\s*\(\s*["\']([^"\']+)["\']',
)
_RE_EVENT_SUB = re.compile(
    r'\.\s*(?:on|subscribe|addEventListener)\s*\(\s*["\']([^"\']+)["\']',
)
_RE_CONFIG = re.compile(
    r'(?:os\.environ|getenv|process\.env|config\.get)\s*[\[(]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_RE_API = re.compile(
    r'["\'](/api/[^"\']+)["\']',
)


class HypothesisEngine:
    """Classify WHY two files co-change without structural dependency."""

    def __init__(self, project_root: Path):
        self._root = project_root
        self._file_cache: dict[str, str] = {}

    def _read(self, rel_path: str) -> str:
        if rel_path in self._file_cache:
            return self._file_cache[rel_path]
        try:
            text = (self._root / rel_path).read_text(encoding="utf-8", errors="replace")[:5000]
        except (OSError, UnicodeDecodeError):
            text = ""
        self._file_cache[rel_path] = text
        return text

    def hypothesize(self, path_a: str, path_b: str) -> dict:
        """Return {category, detail, confidence} for a pair of file paths."""
        text_a = self._read(path_a)
        text_b = self._read(path_b)

        if not text_a and not text_b:
            return {"category": "UNKNOWN", "detail": "files not readable", "confidence": 0.3}

        # SHARED_DB
        tables_a = _extract_sql_tables(text_a)
        tables_b = _extract_sql_tables(text_b)
        shared_tables = tables_a & tables_b
        if shared_tables:
            names = ", ".join(sorted(shared_tables)[:3])
            return {
                "category": "SHARED_DB",
                "detail": f"both reference table(s): {names}",
                "confidence": 0.8,
            }

        # EVENT_BUS
        emits_a = set(_RE_EVENT_EMIT.findall(text_a))
        subs_a = set(_RE_EVENT_SUB.findall(text_a))
        emits_b = set(_RE_EVENT_EMIT.findall(text_b))
        subs_b = set(_RE_EVENT_SUB.findall(text_b))
        shared_events = (emits_a & subs_b) | (emits_b & subs_a)
        if shared_events:
            names = ", ".join(sorted(shared_events)[:3])
            return {
                "category": "EVENT_BUS",
                "detail": f"emit/subscribe event(s): {names}",
                "confidence": 0.7,
            }

        # SHARED_CONFIG
        cfg_a = set(_RE_CONFIG.findall(text_a))
        cfg_b = set(_RE_CONFIG.findall(text_b))
        shared_cfg = cfg_a & cfg_b
        if shared_cfg:
            names = ", ".join(sorted(shared_cfg)[:3])
            return {
                "category": "SHARED_CONFIG",
                "detail": f"shared config key(s): {names}",
                "confidence": 0.6,
            }

        # SHARED_API
        apis_a = set(_RE_API.findall(text_a))
        apis_b = set(_RE_API.findall(text_b))
        shared_api = apis_a & apis_b
        if shared_api:
            names = ", ".join(sorted(shared_api)[:3])
            return {
                "category": "SHARED_API",
                "detail": f"shared API endpoint(s): {names}",
                "confidence": 0.6,
            }

        # TEXT_SIMILARITY
        if text_a and text_b:
            ratio = SequenceMatcher(None, text_a, text_b).ratio()
            if ratio >= 0.6:
                return {
                    "category": "TEXT_SIMILARITY",
                    "detail": f"text similarity {ratio:.0%}",
                    "confidence": 0.5,
                }

        return {"category": "UNKNOWN", "detail": "no pattern detected", "confidence": 0.3}

    def classify_all(self, pairs: list[dict]) -> list[dict]:
        """Add 'hypothesis' key to each pair dict in-place."""
        for pair in pairs:
            pair["hypothesis"] = self.hypothesize(pair["path_a"], pair["path_b"])
        return pairs
