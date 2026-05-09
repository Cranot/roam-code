"""Shared primitives for grep / refs-text / delete-check / history-grep.

These helpers compose the roam index (symbols, edges, clone_pairs,
graph_metrics, file_stats, git_*) with raw text-search results from
ripgrep / git grep so consumers can answer questions text grep alone
cannot:

* "is this hit in code reachable from <entry>?" — `build_reachable_set`
* "what's the smallest enclosing symbol per match?" — `build_interval_index`
* "is this hit inside a known clone class?" — `lookup_clone_class`
* "what's the last author / churn / pagerank for this hit?" —
  `attach_blame`, `attach_heat`, `attach_pagerank`

The functions are deliberately I/O-light: they take a DB cursor, a list
of match dicts (path, line, content), and return enriched copies. No
filesystem reads beyond what callers already do for the grep itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Engine selection — ripgrep > git grep > indexed-file scan
# ---------------------------------------------------------------------------


def _which(name: str) -> str | None:
    """Cached PATH lookup for executables."""
    return shutil.which(name)


def detect_engine() -> str:
    """Return the preferred engine name: 'ripgrep', 'git', or 'fallback'.

    Honours the ROAM_GREP_ENGINE env var so tests / users can pin a choice.
    Valid values: ``ripgrep``, ``git``, ``auto`` (default).
    """
    pinned = os.environ.get("ROAM_GREP_ENGINE", "auto").strip().lower()
    if pinned in {"ripgrep", "rg"}:
        return "ripgrep" if _which("rg") else "fallback"
    if pinned in {"git", "git-grep"}:
        return "git" if _which("git") else "fallback"
    # auto
    if _which("rg"):
        return "ripgrep"
    if _which("git"):
        return "git"
    return "fallback"


def run_search(
    *,
    patterns: list[str],
    root: Path,
    globs: list[str] | None = None,
    fixed_string: bool = False,
    case_insensitive: bool = False,
    word_boundary: bool = False,
    engine: str | None = None,
    timeout: int = 30,
) -> list[dict]:
    """Run a content search and return ``[{path, line, content}]``.

    Multi-pattern via ``-e A -e B`` (treated as alternation by both
    ripgrep and git grep). Multi-glob via repeated ``-g`` (ripgrep) or
    pathspec (git grep). ``fixed_string`` toggles literal mode.

    Returns an empty list on engine failure or no matches; callers fall
    back to ``indexed_file_scan`` if needed.
    """
    if not patterns:
        return []
    eng = engine or detect_engine()
    globs = globs or []

    if eng == "ripgrep":
        return _run_ripgrep(patterns, root, globs, fixed_string, case_insensitive, word_boundary, timeout)
    if eng == "git":
        return _run_git_grep(patterns, root, globs, fixed_string, case_insensitive, word_boundary, timeout)
    # fallback handled at higher layer (we have no engine)
    return []


def _run_ripgrep(patterns, root, globs, fixed, ci, wb, timeout):
    cmd = ["rg", "-n", "--no-heading", "--color", "never", "-H", "-I"]
    if fixed:
        cmd.append("-F")
    if ci:
        cmd.append("-i")
    if wb:
        cmd.append("-w")
    for p in patterns:
        cmd.extend(["-e", p])
    for g in globs:
        cmd.extend(["-g", g])
    return _run_and_parse(cmd, root, timeout)


def _run_git_grep(patterns, root, globs, fixed, ci, wb, timeout):
    cmd = ["git", "grep", "-n", "-I", "--no-color"]
    cmd.append("-F" if fixed else "-E")
    if ci:
        cmd.append("-i")
    if wb:
        cmd.append("-w")
    for p in patterns:
        cmd.extend(["-e", p])
    if globs:
        cmd.append("--")
        cmd.extend(globs)
    return _run_and_parse(cmd, root, timeout)


def _run_and_parse(cmd, root, timeout):
    matches: list[dict] = []
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return matches
    # 0 = matches, 1 = no matches (both engines)
    if result.returncode > 1:
        return matches
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, line_num, content = parts
        try:
            matches.append(
                {
                    "path": path.replace("\\", "/"),
                    "line": int(line_num),
                    "content": content.rstrip("\r\n"),
                }
            )
        except ValueError:
            continue
    return matches


def indexed_file_scan(patterns_compiled, conn, root: Path, glob_filter=None) -> list[dict]:
    """Last-resort scan over indexed files when no engine is available.

    ``patterns_compiled`` is a list of pre-compiled regex objects. A line
    is reported once per pattern it matches.
    """
    from roam.index.gitignore import matches_gitignore

    matches: list[dict] = []
    rows = conn.execute("SELECT path FROM files").fetchall()
    for r in rows:
        rel = r["path"]
        if glob_filter and not any(matches_gitignore(rel, g) for g in glob_filter):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rx in patterns_compiled:
                if rx.search(line):
                    matches.append({"path": rel, "line": i, "content": line})
                    break
    return matches


# ---------------------------------------------------------------------------
# Per-file interval index (replaces N+1 per-match SELECT)
# ---------------------------------------------------------------------------


def build_interval_index(conn, file_paths: Iterable[str]) -> dict:
    """Bulk-fetch all symbols for the given files.

    Returns ``{file_path: [(start, end, sym_dict), ...]}`` sorted by
    ``(end - start)`` ascending so lookups can pick the smallest
    containing span first.

    Use ``find_enclosing(index, path, line)`` to query — O(n) over
    symbols of one file (small in practice; per-file symbol count is
    bounded by the file's class/method count).
    """
    paths = [p for p in file_paths if p]
    if not paths:
        return {}
    # File ID lookup
    rows = conn.execute("SELECT id, path FROM files").fetchall()
    id_to_path = {r["id"]: r["path"] for r in rows}
    path_to_id = {r["path"]: r["id"] for r in rows}

    file_ids = [path_to_id[p] for p in paths if p in path_to_id]
    if not file_ids:
        return {}

    from roam.db.connection import batched_in

    out: dict[str, list[tuple[int, int, dict]]] = defaultdict(list)
    sql = "SELECT id, file_id, name, qualified_name, kind, line_start, line_end FROM symbols WHERE file_id IN ({ph})"
    for r in batched_in(conn, sql, file_ids):
        fp = id_to_path.get(r["file_id"])
        if not fp or r["line_start"] is None or r["line_end"] is None:
            continue
        out[fp].append(
            (
                int(r["line_start"]),
                int(r["line_end"]),
                {
                    "id": r["id"],
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "kind": r["kind"],
                    "line_start": r["line_start"],
                    "line_end": r["line_end"],
                },
            )
        )
    # Sort each file's spans by length ascending (smallest first)
    for fp in out:
        out[fp].sort(key=lambda t: (t[1] - t[0], t[0]))
    return dict(out)


def find_enclosing(interval_index: dict, path: str, line: int) -> dict | None:
    """Smallest symbol containing ``line`` in ``path``, or None."""
    spans = interval_index.get(path)
    if not spans:
        return None
    for start, end, sym in spans:
        if start <= line <= end:
            return sym
    return None


# ---------------------------------------------------------------------------
# Reachability — forward BFS from a named entry
# ---------------------------------------------------------------------------


def build_reachable_set(conn, entry_name: str | None) -> set[int] | None:
    """Symbol IDs reachable from one or more named entries.

    ``entry_name`` accepts a single name or a comma-separated list
    (``"main,handle_request,worker"``) — useful when the actual entry
    is a dispatch table (Click LazyGroup, FastAPI router, …) where a
    single seed under-covers the live surface.

    Returns ``None`` (not empty) if *no* listed entry resolves; callers
    can distinguish "unknown entry" from "entry has no callees".
    """
    if not entry_name:
        return None
    names = [n.strip() for n in entry_name.split(",") if n.strip()]
    if not names:
        return None
    from roam.commands.graph_helpers import bfs_reachable, build_forward_adj

    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT id FROM symbols WHERE name IN ({placeholders}) OR qualified_name IN ({placeholders})",
        (*names, *names),
    ).fetchall()
    if not rows:
        return None
    seeds = {r["id"] for r in rows}
    adj = build_forward_adj(conn)
    return bfs_reachable(adj, seeds)


def build_orphan_set(conn) -> set[int]:
    """Symbol IDs with zero inbound edges in the call graph (true orphans).

    Used for ``--unreachable`` when no entry is supplied. A symbol is
    orphan iff nothing in the indexed graph calls it.
    """
    rows = conn.execute(
        "SELECT s.id FROM symbols s LEFT JOIN edges e ON e.target_id = s.id WHERE e.target_id IS NULL"
    ).fetchall()
    return {r["id"] for r in rows}


# ---------------------------------------------------------------------------
# Clone-class annotation
# ---------------------------------------------------------------------------


def build_clone_index(conn) -> dict[tuple[str, int, int], list[dict]]:
    """Lookup table from ``(file_path, line_start, line_end)`` to clone siblings.

    Each value is a list of sibling spans the matched span clones with.
    A grep hit at ``(path, line)`` joins via the enclosing symbol's
    ``(file, line_start, line_end)`` — see ``lookup_clone_siblings``.
    """
    out: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    rows = conn.execute(
        "SELECT file_a, line_a, line_end_a, func_a, "
        "       file_b, line_b, line_end_b, func_b, similarity, cluster_id "
        "FROM clone_pairs"
    ).fetchall()
    for r in rows:
        a_key = (r["file_a"].replace("\\", "/"), int(r["line_a"]), int(r["line_end_a"] or r["line_a"]))
        b_key = (r["file_b"].replace("\\", "/"), int(r["line_b"]), int(r["line_end_b"] or r["line_b"]))
        sim = float(r["similarity"])
        cid = r["cluster_id"]
        out[a_key].append(
            {"file": b_key[0], "line": b_key[1], "func": r["func_b"], "similarity": sim, "cluster_id": cid}
        )
        out[b_key].append(
            {"file": a_key[0], "line": a_key[1], "func": r["func_a"], "similarity": sim, "cluster_id": cid}
        )
    return dict(out)


def lookup_clone_siblings(clone_index: dict, sym: dict | None, path: str) -> list[dict]:
    """Return sibling spans for a symbol's span, [] if none."""
    if not sym:
        return []
    key = (path.replace("\\", "/"), int(sym["line_start"]), int(sym["line_end"]))
    return clone_index.get(key, [])


# ---------------------------------------------------------------------------
# Bridge annotation — cross-language edges
# ---------------------------------------------------------------------------


def build_bridge_index(conn) -> dict[str, list[dict]]:
    """Per-source-file bridge edges. Returns ``{source_file_path: [edge, ...]}``.

    Only edges with a non-null ``bridge`` are included (cross-language
    links produced by bridges/registry). Useful for grep hits in
    .yml / .env / .proto / .vue files: the edge's target is the code
    that consumes the file.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    rows = conn.execute(
        "SELECT e.bridge, e.kind, e.line, "
        "       sf.path AS source_file, "
        "       ts.qualified_name AS target_qname, ts.name AS target_name, "
        "       ts.kind AS target_kind, tf.path AS target_file "
        "FROM edges e "
        "LEFT JOIN files sf ON e.source_file_id = sf.id "
        "LEFT JOIN symbols ts ON e.target_id = ts.id "
        "LEFT JOIN files tf ON ts.file_id = tf.id "
        "WHERE e.bridge IS NOT NULL"
    ).fetchall()
    for r in rows:
        sf = r["source_file"]
        if not sf:
            continue
        out[sf.replace("\\", "/")].append(
            {
                "bridge": r["bridge"],
                "kind": r["kind"],
                "line": r["line"],
                "target_qname": r["target_qname"],
                "target_name": r["target_name"],
                "target_kind": r["target_kind"],
                "target_file": (r["target_file"] or "").replace("\\", "/") or None,
            }
        )
    return dict(out)


# ---------------------------------------------------------------------------
# History annotations: blame + heat
# ---------------------------------------------------------------------------


def attach_blame(matches: list[dict], root: Path) -> None:
    """In-place: add ``blame_author`` + ``blame_date`` per match.

    Uses ``git blame --porcelain -L line,line`` per (path, line). Cached
    by (path, line). Falls back to None on failure.
    """
    cache: dict[tuple[str, int], tuple[str | None, str | None]] = {}
    for m in matches:
        key = (m["path"], m["line"])
        if key not in cache:
            cache[key] = _git_blame_line(root, m["path"], m["line"])
        author, date = cache[key]
        m["blame_author"] = author
        m["blame_date"] = date


def _git_blame_line(root: Path, path: str, line: int) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "blame", "--porcelain", "-L", f"{line},{line}", "--", path],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0:
        return None, None
    author = None
    ts = None
    for ln in result.stdout.splitlines():
        if ln.startswith("author "):
            author = ln[len("author ") :].strip() or None
        elif ln.startswith("author-time "):
            try:
                from datetime import datetime, timezone

                t = int(ln[len("author-time ") :].strip())
                ts = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
            except (ValueError, OSError):
                ts = None
        if author and ts:
            break
    return author, ts


def attach_heat(matches: list[dict], conn) -> None:
    """In-place: add ``heat_churn`` (total_churn) + ``heat_commits`` per hit.

    Numbers come from file_stats; symbol-level churn would require
    per-symbol git plumbing. File-level is a strong proxy and free.
    """
    rows = conn.execute(
        "SELECT f.path, fs.total_churn, fs.commit_count FROM file_stats fs JOIN files f ON fs.file_id = f.id"
    ).fetchall()
    by_path = {r["path"].replace("\\", "/"): (r["total_churn"], r["commit_count"]) for r in rows}
    for m in matches:
        churn, commits = by_path.get(m["path"], (0, 0))
        m["heat_churn"] = int(churn or 0)
        m["heat_commits"] = int(commits or 0)


def attach_pagerank(matches: list[dict], conn) -> None:
    """In-place: add ``pagerank`` per hit, sourced from enclosing symbol."""
    rows = conn.execute("SELECT symbol_id, pagerank FROM graph_metrics").fetchall()
    pr = {r["symbol_id"]: float(r["pagerank"] or 0.0) for r in rows}
    for m in matches:
        sym = m.get("_enclosing")
        m["pagerank"] = pr.get(sym["id"], 0.0) if sym else 0.0


# ---------------------------------------------------------------------------
# File-role / surface classification for refs-text
# ---------------------------------------------------------------------------


def classify_surface(path: str) -> str:
    """Map a path to one of: code, test, docs, config, generated, vendored, other.

    Wraps the file_roles classifier with a coarser bucket suitable for
    UI/audit output. Reachability lookups are layered on top of `code`.
    """
    from roam.index.file_roles import (
        ROLE_CONFIG,
        ROLE_DATA,
        ROLE_DOCS,
        ROLE_GENERATED,
        ROLE_SOURCE,
        ROLE_TEST,
        ROLE_VENDORED,
        classify_file,
    )

    role = classify_file(path)
    if role == ROLE_SOURCE:
        return "code"
    if role == ROLE_TEST:
        return "test"
    if role == ROLE_DOCS:
        return "docs"
    if role in (ROLE_CONFIG, ROLE_DATA):
        return "config"
    if role == ROLE_GENERATED:
        return "generated"
    if role == ROLE_VENDORED:
        return "vendored"
    return "other"


# ---------------------------------------------------------------------------
# Group-by collapse + sort helpers
# ---------------------------------------------------------------------------


def group_by_symbol(matches: list[dict]) -> list[dict]:
    """Collapse hits sharing the same enclosing symbol into a group dict.

    Returns ``[{path, enclosing_symbol, enclosing_kind, count, first_line,
    samples: [...]}]``. Hits with no enclosing symbol are emitted as
    standalone single-hit groups.
    """
    out: dict[tuple, dict] = {}
    order: list[tuple] = []
    for m in matches:
        sym = m.get("_enclosing")
        if sym:
            key = (m["path"], sym["qualified_name"] or sym["name"], sym["kind"])
        else:
            key = (m["path"], None, None, m["line"])  # singleton
        if key not in out:
            out[key] = {
                "path": m["path"],
                "enclosing_symbol": sym["qualified_name"] if sym else None,
                "enclosing_kind": sym["kind"] if sym else None,
                "count": 0,
                "first_line": m["line"],
                "samples": [],
                "_enclosing": sym,
            }
            order.append(key)
        g = out[key]
        g["count"] += 1
        if m["line"] < g["first_line"]:
            g["first_line"] = m["line"]
        if len(g["samples"]) < 3:
            g["samples"].append({"line": m["line"], "content": m["content"]})
        # Propagate annotations from the first hit
        for k in (
            "pagerank",
            "heat_churn",
            "heat_commits",
            "blame_author",
            "blame_date",
            "reachable",
            "clone_siblings",
        ):
            if k in m and k not in g:
                g[k] = m[k]
    return [out[k] for k in order]


# Keep bisect import live for callers that may want to extend the index later
__all__ = [
    "detect_engine",
    "run_search",
    "indexed_file_scan",
    "build_interval_index",
    "find_enclosing",
    "build_reachable_set",
    "build_orphan_set",
    "build_clone_index",
    "lookup_clone_siblings",
    "build_bridge_index",
    "attach_blame",
    "attach_heat",
    "attach_pagerank",
    "classify_surface",
    "group_by_symbol",
]
