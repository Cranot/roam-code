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
from difflib import SequenceMatcher
from pathlib import Path


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
        tables_a = set(_RE_TABLE.findall(text_a))
        tables_b = set(_RE_TABLE.findall(text_b))
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
