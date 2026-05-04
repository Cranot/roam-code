"""Shared symbol resolution and index helpers for all roam commands."""

from __future__ import annotations

import os
import sqlite3
import subprocess

import click

from roam.db.connection import db_exists, find_project_root, open_db
from roam.db.queries import SEARCH_SYMBOLS, SYMBOL_BY_NAME, SYMBOL_BY_QUALIFIED


def _git_head_short() -> str | None:
    """Return current ``HEAD`` short SHA, or ``None`` if git is unavailable."""
    try:
        root = find_project_root()
    except Exception:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def index_staleness_hint() -> str | None:
    """Return a one-line ``hint`` string when the index appears stale.

    Heuristic: compare the latest commit hash in ``git_commits`` against
    ``git rev-parse HEAD``. When they differ (and HEAD isn't a parent of
    the indexed commit), the index is missing recent commits — every
    git-aware metric will be off (commit_count, churn, co-change). The
    hint is suppressed when ``ROAM_NO_STALENESS_HINT=1`` is set so CI
    pipelines that index then mutate the tree don't see noise.
    """
    if os.environ.get("ROAM_NO_STALENESS_HINT"):
        return None
    head = _git_head_short()
    if not head:
        return None
    try:
        with open_db(readonly=True) as conn:
            row = conn.execute("SELECT hash FROM git_commits ORDER BY timestamp DESC LIMIT 1").fetchone()
            if row is None:
                return None
            indexed_short = (row[0] or "")[:12]
    except Exception:
        return None
    if not indexed_short or indexed_short == head:
        return None
    return (
        f"index latest commit {indexed_short} != HEAD {head} — git-derived metrics "
        f"(commits, churn, co-change, weather) may be stale. Run `roam index --force`."
    )


# Maximum suggestions returned by fts_suggestions()
_MAX_FTS_SUGGESTIONS = 5


def ensure_index(quiet: bool = False) -> None:
    """Build the index if it doesn't exist yet.

    Args:
        quiet: If True, suppress progress output during indexing.
    """
    if not db_exists():
        if not quiet:
            click.echo(
                "No roam index found. Run `roam init` to create one.\n"
                "  Tip: If you already ran `roam init`, your current directory may be\n"
                "       outside the project root. cd into the project root and retry."
            )
        from roam.index.indexer import Indexer

        Indexer().run(quiet=quiet)


def require_index() -> None:
    """Raise IndexMissingError if the index does not exist.

    Use this instead of ``ensure_index()`` in CI / gate commands where
    auto-building is not appropriate and the caller needs a clear exit code.
    """
    if not db_exists():
        from roam.exit_codes import IndexMissingError

        raise IndexMissingError()


# ---------------------------------------------------------------------------
# Remediation hint helpers — produce actionable error messages for agents
# ---------------------------------------------------------------------------


def symbol_not_found_hint(name: str) -> str:
    """Return a user-facing error message with remediation steps for a missing symbol.

    Produces a multi-line message pointing agents toward ``roam search`` and
    ``roam index`` so they can self-recover without human intervention.

    Example output::

        Symbol not found: "foo"
          Tip: Run `roam search foo` to find similar symbols.
               If the symbol was recently added, run `roam index` to refresh the index.
    """
    # Strip file hint prefix for the search suggestion (e.g. "src/foo.py:bar" -> "bar")
    search_term = name.split(":", 1)[-1] if (":" in name and "::" not in name) else name
    return (
        f'Symbol not found: "{name}"\n'
        f"  Tip: Run `roam search {search_term}` to find similar symbols.\n"
        f"       If the symbol was recently added, run `roam index` to refresh the index."
    )


def file_not_found_hint(path: str) -> str:
    """Return a user-facing error message with remediation steps for a missing file.

    Example output::

        File not found in index: "src/foo.py"
          Tip: Run `roam index` if the file was recently added.
               Use a partial path or check spelling with `roam file <path>`.
    """
    return (
        f'File not found in index: "{path}"\n'
        f"  Tip: Run `roam index` if the file was recently added.\n"
        f"       Use a partial path or check spelling -- e.g. `roam file {path}`."
    )


# Path-priority bias when multiple symbols share a name. A bench script
# in dev/ that defines its own ``open_db`` shouldn't shadow the canonical
# library's ``open_db`` — the canonical one lives under src/ or lib/.
# Higher rank wins. Negative ranks are penalised paths (test/dev/example).
_PATH_PRIORITY = (
    ("/src/", 3),
    ("/lib/", 3),
    ("src/roam/", 3),  # roam-internal canonical path
    ("/dev/", -2),
    ("/scripts/", -2),
    ("/examples/", -2),
    ("/tests/", -1),
    ("/test/", -1),
)


def _path_rank(path: str | None) -> int:
    if not path:
        return 0
    p = path.replace("\\", "/")
    for needle, rank in _PATH_PRIORITY:
        if needle in p:
            return rank
    return 0


def pick_best(conn: sqlite3.Connection, rows: list) -> dict | None:
    """Pick the most important symbol from ambiguous matches.

    Tie-breaking order (high to low):
    1. Highest incoming edge count (most-called).
    2. Highest PageRank (transitive importance, useful when callers are
       template-only or dynamic and not captured as edges).
    3. Highest cognitive complexity (proxy for "real implementation"
       beating placeholder/empty handlers with the same name).
    4. Highest file churn (hot file > cold file).
    5. Highest path priority (canonical src/ paths beat dev/ scripts).
    6. Lowest symbol id (deterministic final tiebreak).

    Returns the chosen row when at least one signal is present. When all
    candidates score zero across every signal, returns ``None`` so the
    caller can fall back to the first SQL-ordered row (the historic
    behaviour) — this preserves stability for unindexed projects.
    """
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]

    ids = [r["id"] for r in rows]
    ph = ",".join("?" for _ in ids)

    counts = conn.execute(
        f"SELECT target_id, COUNT(*) as cnt FROM edges WHERE target_id IN ({ph}) GROUP BY target_id",
        ids,
    ).fetchall()
    ref_map = {c["target_id"]: c["cnt"] for c in counts}

    pr_rows = conn.execute(
        f"SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
        ids,
    ).fetchall()
    pr_map = {r["symbol_id"]: r["pagerank"] or 0 for r in pr_rows}

    cc_rows = conn.execute(
        f"SELECT symbol_id, cognitive_complexity FROM symbol_metrics WHERE symbol_id IN ({ph})",
        ids,
    ).fetchall()
    cc_map = {r["symbol_id"]: r["cognitive_complexity"] or 0 for r in cc_rows}

    file_ids = list({r["file_id"] for r in rows if "file_id" in r.keys() and r["file_id"] is not None})
    churn_map: dict[int, int] = {}
    if file_ids:
        ph_f = ",".join("?" for _ in file_ids)
        churn_rows = conn.execute(
            f"SELECT file_id, total_churn FROM file_stats WHERE file_id IN ({ph_f})",
            file_ids,
        ).fetchall()
        churn_map = {r["file_id"]: r["total_churn"] or 0 for r in churn_rows}

    def _key(r):
        return (
            ref_map.get(r["id"], 0),
            pr_map.get(r["id"], 0),
            cc_map.get(r["id"], 0),
            churn_map.get(r["file_id"], 0) if "file_id" in r.keys() else 0,
            _path_rank(r["file_path"]),
            -r["id"],
        )

    best = max(rows, key=_key)
    has_signal = (
        ref_map.get(best["id"], 0) > 0
        or pr_map.get(best["id"], 0) > 0
        or cc_map.get(best["id"], 0) > 0
        or (churn_map.get(best["file_id"], 0) if "file_id" in best.keys() else 0) > 0
    )
    if has_signal:
        return best
    return None


def _parse_file_hint(name):
    """Parse 'file:symbol' syntax into (file_hint, symbol_name).

    If no colon is present, returns (None, name).
    """
    if ":" in name:
        parts = name.split(":", 1)
        # Guard against qualified names like MyClass::method
        if "::" not in name and parts[0] and parts[1]:
            return parts[0], parts[1]
    return None, name


def _filter_by_file(rows, file_hint):
    """Filter candidate rows by file path substring match."""
    if not file_hint:
        return rows
    # Normalize separators
    hint = file_hint.replace("\\", "/").lower()
    filtered = [r for r in rows if hint in (r["file_path"] or "").replace("\\", "/").lower()]
    return filtered if filtered else rows


def _ambiguity_signals(conn: sqlite3.Connection, rows: list) -> dict:
    """Fetch ranking signals for an ambiguous match set in one batch."""
    if not rows:
        return {"ref": {}, "pr": {}, "cc": {}, "churn": {}}
    ids = [r["id"] for r in rows]
    ph = ",".join("?" for _ in ids)
    ref_map = {
        c["target_id"]: c["cnt"]
        for c in conn.execute(
            f"SELECT target_id, COUNT(*) as cnt FROM edges WHERE target_id IN ({ph}) GROUP BY target_id",
            ids,
        ).fetchall()
    }
    pr_map = {
        r["symbol_id"]: r["pagerank"] or 0
        for r in conn.execute(
            f"SELECT symbol_id, pagerank FROM graph_metrics WHERE symbol_id IN ({ph})",
            ids,
        ).fetchall()
    }
    cc_map = {
        r["symbol_id"]: r["cognitive_complexity"] or 0
        for r in conn.execute(
            f"SELECT symbol_id, cognitive_complexity FROM symbol_metrics WHERE symbol_id IN ({ph})",
            ids,
        ).fetchall()
    }
    file_ids = list({r["file_id"] for r in rows if "file_id" in r.keys() and r["file_id"] is not None})
    churn_map: dict[int, int] = {}
    if file_ids:
        ph_f = ",".join("?" for _ in file_ids)
        churn_map = {
            r["file_id"]: r["total_churn"] or 0
            for r in conn.execute(
                f"SELECT file_id, total_churn FROM file_stats WHERE file_id IN ({ph_f})",
                file_ids,
            ).fetchall()
        }
    return {"ref": ref_map, "pr": pr_map, "cc": cc_map, "churn": churn_map}


def _row_signals(row, signals) -> dict:
    """Lift a row's importance signals out of the batch lookup."""
    return {
        "incoming_edges": signals["ref"].get(row["id"], 0),
        "pagerank": round(signals["pr"].get(row["id"], 0) or 0, 4),
        "cognitive_complexity": signals["cc"].get(row["id"], 0) or 0,
        "file_churn": (signals["churn"].get(row["file_id"], 0) if "file_id" in row.keys() else 0),
    }


def find_symbol_with_alternatives(conn: sqlite3.Connection, name: str) -> tuple[dict | None, list[dict]]:
    """Find a symbol with disambiguation, returning the best plus alternatives.

    Same lookup chain as :func:`find_symbol`, but also returns the other
    matches at the same lookup tier so callers can surface
    ``did_you_mean`` hints. Alternatives are sorted by the same importance
    score :func:`pick_best` uses to choose the winner.
    """
    file_hint, symbol_name = _parse_file_hint(name)

    for query, params in (
        (SYMBOL_BY_QUALIFIED, (symbol_name,)),
        (SYMBOL_BY_NAME, (symbol_name,)),
        (SEARCH_SYMBOLS, (f"%{symbol_name}%", 10)),
    ):
        rows = conn.execute(query, params).fetchall()
        if file_hint:
            rows = _filter_by_file(rows, file_hint)
        if not rows:
            continue
        if len(rows) == 1:
            return rows[0], []

        signals = _ambiguity_signals(conn, rows)

        def _score(r):
            return (
                signals["ref"].get(r["id"], 0),
                signals["pr"].get(r["id"], 0),
                signals["cc"].get(r["id"], 0),
                signals["churn"].get(r["file_id"], 0) if "file_id" in r.keys() else 0,
                _path_rank(r["file_path"]),
                -r["id"],
            )

        ordered = sorted(rows, key=_score, reverse=True)
        best = ordered[0]
        return best, ordered[1:]

    return None, []


def find_symbol(conn: sqlite3.Connection, name: str) -> dict | None:
    """Find a symbol by name with disambiguation.

    Lookup chain:
    1. Parse file:symbol hint if present
    2. Try qualified_name match (fetchall)
    3. Try simple name match (fetchall)
    4. Try fuzzy LIKE match (limit 10)
    5. At each step: if multiple matches -> pick_best (importance-weighted)
    6. If file hint provided -> filter candidates first

    Always returns a single row or None. Never returns a list.
    """
    best, _ = find_symbol_with_alternatives(conn, name)
    return best


def fts_suggestions(conn, name: str, limit: int = _MAX_FTS_SUGGESTIONS) -> list:
    """Return FTS5-ranked suggestions for a symbol name that was not found.

    Queries the ``symbol_fts`` virtual table (FTS5/BM25) with a prefix match
    on each token derived from *name*, falling back to a LIKE match when FTS5
    is not available or the term syntax produces an error.

    Returns a list of dicts with keys: name, qualified_name, kind, file_path,
    line_start.  At most *limit* entries are returned.
    """
    _, symbol_name = _parse_file_hint(name)
    if not symbol_name:
        return []

    rows: list = []

    # --- FTS5 path: BM25-ranked full-text search ---
    try:
        # Tokenise the query: split on underscores and dots so that e.g.
        # "FlaskAp" matches "Flask_App" via porter stemming, and "flask_app"
        # matches "FlaskApp" via the unicode61 tokenizer's camelCase handling.
        tokens = symbol_name.replace("_", " ").replace(".", " ").split()
        if tokens:
            fts_query = " OR ".join(f'"{t}"*' for t in tokens)
        else:
            fts_query = f'"{symbol_name}"*'
        rows = conn.execute(
            "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, s.line_start "
            "FROM symbol_fts sf "
            "JOIN symbols s ON sf.rowid = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE symbol_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, limit),
        ).fetchall()
    except Exception:
        rows = []

    # --- Fallback: LIKE match when FTS5 is unavailable or returned nothing ---
    if not rows:
        try:
            rows = conn.execute(
                "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, s.line_start "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE s.name LIKE ? COLLATE NOCASE "
                "ORDER BY s.name "
                "LIMIT ?",
                (f"%{symbol_name}%", limit),
            ).fetchall()
        except Exception:
            rows = []

    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line_start": r["line_start"],
        }
        for r in rows
    ]


def detect_entry_points(conn: sqlite3.Connection) -> list:
    """Detect project entry points from conventional filenames, main() functions, and route decorators.

    Returns a list of dicts: [{"path": str, "reason": str}]

    Detection strategy (applied in order, duplicates suppressed):
    1. Conventional filenames — well-known project entry file names.
    2. Barrel-file filtering — index.* files with <=2 own definitions are
       skipped (they are re-export barrels, not real entry points).
    3. main() functions — files that define a top-level ``main`` function.
    4. Route/command decorators — files that contain ``@route`` or ``@command``
       style decorators, indicating a web or CLI entry point.
    """
    import os

    _ENTRY_NAMES = {
        "main.py",
        "__main__.py",
        "__init__.py",
        "index.js",
        "index.ts",
        "main.go",
        "main.rs",
        "app.py",
        "app.js",
        "app.ts",
        "mod.rs",
        "lib.rs",
        "setup.py",
        "manage.py",
        "server.py",
        "handler.go",
    }

    results: list = []
    seen_paths: set = set()

    # Fetch all files with id so we can do the barrel check
    files = conn.execute("SELECT id, path FROM files").fetchall()

    # Build a path -> id map for barrel checking
    path_to_id: dict = {f["path"]: f["id"] for f in files}

    # 1. Conventional filenames
    conventional_paths = [f["path"] for f in files if os.path.basename(f["path"]) in _ENTRY_NAMES]

    # 2. Barrel-file filtering: skip index.* files with <=2 own definitions
    for path in conventional_paths:
        bn = os.path.basename(path)
        if bn.startswith("index."):
            file_id = path_to_id.get(path)
            if file_id is not None:
                own_defs = conn.execute(
                    "SELECT COUNT(*) FROM symbols WHERE file_id = ? AND kind IN ('function', 'class', 'method')",
                    (file_id,),
                ).fetchone()[0]
                if own_defs <= 2:
                    continue  # barrel file — skip
        if path not in seen_paths:
            seen_paths.add(path)
            results.append({"path": path, "reason": "conventional filename"})

    # 3. main() function lookup
    main_files = conn.execute(
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = 'main' AND s.kind = 'function'",
    ).fetchall()
    for r in main_files:
        if r["path"] not in seen_paths:
            seen_paths.add(r["path"])
            results.append({"path": r["path"], "reason": "main() function"})

    # 4. Route/command decorator detection
    decorated_files = conn.execute(
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.kind = 'decorator' AND (s.name LIKE '%route%' OR s.name LIKE '%command%')",
    ).fetchall()
    for r in decorated_files:
        if r["path"] not in seen_paths:
            seen_paths.add(r["path"])
            results.append({"path": r["path"], "reason": "route/command decorator"})

    return results


def symbol_not_found(conn: sqlite3.Connection, name: str, *, json_mode: bool = False) -> str:
    """Build a 'symbol not found' error message with FTS5-powered suggestions.

    In text mode returns a multi-line string like::

        Symbol 'FlaskAp' not found. Did you mean:
          cls    Flask_App  (src/app.py:12)
          fn     flask_app  (src/factory.py:5)

    In JSON mode returns a JSON string (roam envelope) with fields:
    ``error``, ``query``, and ``suggestions`` (list of name/kind/location dicts).

    Callers should ``click.echo`` the result and then ``raise SystemExit(1)``.
    """
    from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

    suggestions = fts_suggestions(conn, name)

    if json_mode:
        suggestion_dicts = [
            {
                "name": s["name"],
                "qualified_name": s["qualified_name"],
                "kind": s["kind"],
                "location": loc(s["file_path"], s["line_start"]),
            }
            for s in suggestions
        ]
        return to_json(
            json_envelope(
                "error",
                summary={
                    "error": f"Symbol not found: {name}",
                    "suggestions_count": len(suggestion_dicts),
                },
                error=f"Symbol not found: {name}",
                query=name,
                suggestions=suggestion_dicts,
            )
        )

    # Text mode
    lines = [f"Symbol '{name}' not found."]
    if suggestions:
        lines.append("Did you mean:")
        for s in suggestions:
            kind_str = abbrev_kind(s["kind"])
            location = loc(s["file_path"], s["line_start"])
            label = s["qualified_name"] or s["name"]
            lines.append(f"  {kind_str:<6s} {label}  ({location})")
    return "\n".join(lines)
