"""Shared symbol resolution and index helpers for all roam commands."""

from __future__ import annotations

import os
import sqlite3
import subprocess

import click

from roam.db.connection import batched_in, db_exists, find_project_root, open_db
from roam.db.queries import SEARCH_SYMBOLS, SYMBOL_BY_NAME, SYMBOL_BY_QUALIFIED


def _git_head_short() -> str | None:
    """Return current ``HEAD`` short SHA, or ``None`` if git is unavailable."""
    try:
        root = find_project_root()
    except Exception as _exc:  # noqa: BLE001 — defensive
        # Expected absence (no .git / outside a repo) returns None below;
        # surface unexpected failures via the observability hook.
        from roam.observability import log_swallowed

        log_swallowed("resolve:_git_head_short:find_project_root", _exc)
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
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as _exc:
        # Git missing / rev-parse timed out -> None. A TimeoutExpired is a
        # real failure masquerading as "git unavailable" — surface it.
        from roam.observability import log_swallowed

        log_swallowed("resolve:_git_head_short:git_rev_parse", _exc)
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
    status = index_status()
    if status is None or status.get("fresh") is True:
        return None
    return status.get("hint")


def _git_dirty_count() -> int | None:
    """Return the number of files with working-tree modifications.

    Uses ``git status --porcelain`` so it's fast (metadata-only). Counts
    every entry: modified, added, deleted, untracked. Returns ``None`` if
    git is unavailable. surfaces working-tree drift that the
    HEAD-vs-indexed check misses (you can be at the same commit but have
    edits the index hasn't seen).
    """
    try:
        root = find_project_root()
    except Exception as _exc:  # noqa: BLE001 — defensive
        # Expected absence (no .git / outside a repo) returns None below;
        # surface unexpected failures via the observability hook.
        from roam.observability import log_swallowed

        log_swallowed("resolve:_git_dirty_count:find_project_root", _exc)
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as _exc:
        # Git missing / status timed out -> None. A TimeoutExpired is a
        # real failure masquerading as "git unavailable" — surface it.
        from roam.observability import log_swallowed

        log_swallowed("resolve:_git_dirty_count:git_status", _exc)
        return None
    if result.returncode != 0:
        return None
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    return len(lines)


def index_status() -> dict | None:
    """Return a structured ``index_status`` dict for envelope use.

    Returns ``None`` when staleness cannot be determined (no git, no
    commits indexed, env opt-out). Otherwise:

        {
          "fresh": bool,
          "indexed_commit": "...",
          "head_commit": "...",
          "dirty_files": int | None,   # working-tree drift count
          "hint": "...",
        }

    Round 4 #20 / U: callers should attach this at the TOP of their
    JSON envelope (and print before the VERDICT in text mode) so an
    agent reading top-down can't miss a stale-index warning.
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
    except Exception as _exc:  # noqa: BLE001 — defensive
        # Expected absence (no DB, no git_commits table on an older index)
        # collapses to "staleness unknown" -> None; surface anything else.
        from roam.observability import log_swallowed

        log_swallowed("resolve:index_status:git_commits_query", _exc)
        return None
    if not indexed_short:
        return None
    dirty = _git_dirty_count()
    # even when HEAD matches, working-tree edits make the
    # index stale for symbol-resolution purposes. Surface that.
    if indexed_short == head:
        if dirty and dirty > 0:
            return {
                "fresh": False,
                "indexed_commit": indexed_short,
                "head_commit": head,
                "dirty_files": dirty,
                "hint": (
                    f"{dirty} file(s) modified in working tree since last index — "
                    "run `roam index` to refresh symbol/edge data."
                ),
            }
        return {
            "fresh": True,
            "indexed_commit": indexed_short,
            "head_commit": head,
            "dirty_files": dirty,
            "hint": None,
        }
    return {
        "fresh": False,
        "indexed_commit": indexed_short,
        "head_commit": head,
        "dirty_files": dirty,
        "hint": (
            f"index latest commit {indexed_short} != HEAD {head} — git-derived metrics "
            f"(commits, churn, co-change, weather) may be stale. Run `roam index --force`."
        ),
    }


# Maximum suggestions returned by fts_suggestions()
_MAX_FTS_SUGGESTIONS = 5


def ensure_index(quiet: bool = False, suppress_cold_start_advisory: bool = False) -> None:
    """Build the index if it doesn't exist yet.

    Args:
        quiet: If True, suppress progress output during indexing.
        suppress_cold_start_advisory: If True, skip the "No roam index found.
            Run `roam init`..." advisory before building. Set this from
            commands whose PURPOSE is to build the index (``roam init``) — the
            user just asked to create the index, so recommending they run the
            command they're already running is confusing first-time UX (W1291).
    """
    if not db_exists():
        if not quiet and not suppress_cold_start_advisory:
            click.echo(
                "No roam index found. Run `roam init` to create one.\n"
                "  Tip: If you already ran `roam init`, your current directory may be\n"
                "       outside the project root. cd into the project root and retry.\n"
                "  If this looks unexpected, run `roam doctor` to diagnose your install."
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


def empty_corpus_state(conn: sqlite3.Connection) -> dict | None:
    """Return the canonical Pattern-2 empty-corpus disclosure when the index
    holds zero symbols, else ``None``.

    Analysis commands (``cycles`` / ``dashboard`` / ``verify`` / ``debt`` / …)
    run their graph/metric logic over the symbol table; on a 0-symbol corpus
    that logic produces a vacuously "clean" / "PASS" / "HEALTHY" verdict —
    a Pattern-2 silent SAFE (absent state read as a clean result). Calling this
    early in the JSON branch lets a command emit an explicit
    ``state: "empty_corpus"`` + ``partial_success: True`` instead. Mirrors the
    guard already shipped in ``cmd_health`` so the state vocabulary is identical
    across commands (Pattern-3 cross-command consistency).

    Returns a dict to spread into ``summary`` (``state`` + ``partial_success``);
    the caller supplies its own ``verdict`` so the LAW-4 fact terminal stays
    command-appropriate.

    Canonical signal: ``state == "empty_corpus"`` is consistent across EVERY
    adopter — agents should branch on it. ``partial_success`` is intentionally
    NOT uniform: most commands treat an empty corpus as a degraded input
    (``True``), but a few (e.g. ``dead``) treat "scanned everything, found
    nothing to flag" as a fully-resolved result and override it to ``False``
    (see ``test_w802_dead_empty_corpus`` for that command's reasoning). Callers
    may therefore overwrite ``partial_success`` after spreading this dict; the
    ``state`` field is the cross-command-stable disclosure.
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
    except sqlite3.OperationalError:
        # symbols table absent (index never built / partial) — let the caller's
        # normal not_initialized handling speak; do not assert empty_corpus.
        return None
    if row is not None and row[0] == 0:
        return {"state": "empty_corpus", "partial_success": True}
    return None


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


def _stamp_tier(row, tier: str) -> dict:
    """W1249: convert a sqlite3.Row to a dict and stamp ``_resolution_tier``.

    ``find_symbol`` / ``find_symbol_with_alternatives`` walk a 3-tier chain
    (qualified-name exact -> simple-name exact -> fuzzy LIKE). The returned
    row carries no tier metadata, so prior callers re-derived it by comparing
    ``row["name"]`` / ``row["qualified_name"]`` against the input (W1242 /
    W1244) or by re-querying (W1248). W1249 hoists that boilerplate into the
    resolver itself: the boundary stamps the tier on every returned row, and
    callers read ``row.get("_resolution_tier", "symbol")``.

    sqlite3.Row is immutable so we widen to ``dict`` at the boundary; all
    existing key-access patterns (``row["id"]``, ``row.keys()``,
    ``dict(row)``) keep working on the wider type.

    Tier vocabulary aligns with W1241's ``_RESOLUTION_KINDS`` closed enum
    (``symbol`` / ``fuzzy`` / ``unresolved``; the ``file`` kind is owned by
    caller-side file-path resolution and is not stamped here).
    """
    out = dict(row)
    out["_resolution_tier"] = tier
    return out


def resolve_file_symbols(
    conn: sqlite3.Connection, raw_input: str
) -> tuple[int | None, set[int], str | None, str | None]:
    """Resolve a file-path-like target to its file row + owned symbols + tier.

    Pattern-1 Variant D substrate (Wave A of the
    ``(internal memo)`` remediation): replaces
    three drifted lookalike helpers that lived inline in
    ``cmd_safe_zones`` (returned ``(file_id, sym_ids)``),
    ``cmd_affected_tests`` (returned ``(sym_ids, file_paths)``), and
    ``cmd_metrics`` (returned ``(kind, id, row)`` with the file-substring
    path silently undisclosed). Each helper silently fell back to a
    ``LIKE %name`` substring match without surfacing the degradation, and
    callers then emitted a fully-resolved success verdict — the canonical
    Variant D failure shape.

    The canonical contract:

    1. Try an exact-path match (``files.path = ?``). Returns ``tier="file"``.
    2. On miss, try a ``LIKE %name`` substring match (``files.path LIKE ?``).
       Returns ``tier="file_substring"`` so callers can disclose the
       degradation via :func:`roam.output.formatter.resolution_disclosure`
       (the W1309 ``file_substring`` enum member already lives in
       ``_RESOLUTION_KINDS``).
    3. On miss, return ``(None, set(), None, None)`` so callers can route
       to ``unresolved`` disclosure / "target not found" envelopes.

    The path-separator normalisation (Windows ``\\`` -> POSIX ``/``) is
    applied once at the boundary so consumer call sites stay agnostic.

    Args:
        conn: A live ``sqlite3.Connection`` from ``open_db``.
        raw_input: The user-supplied target string. May contain either
            POSIX or Windows path separators; both are normalised.

    Returns:
        A 4-tuple ``(file_id, sym_ids, file_path, tier)``:

        - ``file_id``: The resolved ``files.id`` value, or ``None`` on miss.
        - ``sym_ids``: ``set[int]`` of symbol ids owned by ``file_id``.
          Empty set on miss OR on a file with zero indexed symbols (both
          tier-disclosable; the empty-set + ``tier="file"`` shape is a
          valid resolution).
        - ``file_path``: The canonical ``files.path`` value as stored in
          the index (may differ from ``raw_input`` after substring match),
          or ``None`` on miss.
        - ``tier``: One of ``"file"`` (exact match), ``"file_substring"``
          (LIKE %name fallback), or ``None`` (no match). The tier is a
          member of ``_RESOLUTION_KINDS`` so callers can pass it directly
          to ``resolution_disclosure(tier, target=raw_input)``.

    Caller pattern (post-Wave B adoption template)::

        file_id, sym_ids, fpath, tier = resolve_file_symbols(conn, target)
        if file_id is None:
            # ... unresolved disclosure ...
            return
        disclosure = resolution_disclosure(tier, target=target)
        # disclosure["partial_success"] is True when tier == "file_substring"
    """
    normalized = raw_input.replace("\\", "/")

    # Tier 1: exact-path match.
    row = conn.execute(
        "SELECT id, path FROM files WHERE path = ?",
        (normalized,),
    ).fetchone()
    tier: str | None
    if row is not None:
        tier = "file"
    else:
        # Tier 2: LIKE %name substring fallback. ORDER BY path keeps the
        # result deterministic when multiple files share a basename suffix
        # (e.g. ``service.py`` matches both ``src/service.py`` and
        # ``tests/service.py``); LIMIT 1 mirrors the legacy single-row
        # behaviour all three helpers shared.
        row = conn.execute(
            "SELECT id, path FROM files WHERE path LIKE ? ORDER BY path LIMIT 1",
            (f"%{normalized}",),
        ).fetchone()
        if row is None:
            return None, set(), None, None
        tier = "file_substring"

    file_id = row["id"]
    file_path = row["path"]
    syms = conn.execute(
        "SELECT id FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()
    return file_id, {s["id"] for s in syms}, file_path, tier


def find_symbol_with_alternatives(conn: sqlite3.Connection, name: str) -> tuple[dict | None, list[dict]]:
    """Find a symbol with disambiguation, returning the best plus alternatives.

    Same lookup chain as :func:`find_symbol`, but also returns the other
    matches at the same lookup tier so callers can surface
    ``did_you_mean`` hints. Alternatives are sorted by the same importance
    score used to choose the winner.

    W1249: every returned row (best + alternatives) carries the
    ``_resolution_tier`` key per the closed enum
    (``symbol`` for the two exact-name rungs, ``fuzzy`` for the LIKE
    fallback). Callers read ``row.get("_resolution_tier", "symbol")``;
    the default keeps backwards compatibility for any code path that
    constructs row-like dicts independently.
    """
    file_hint, symbol_name = _parse_file_hint(name)

    # Tier per query position; ``SYMBOL_BY_QUALIFIED`` and ``SYMBOL_BY_NAME``
    # are both exact-match rungs (the W1242/W1244/W1248 detection helpers all
    # collapsed them to ``symbol``); ``SEARCH_SYMBOLS`` is the LIKE fallback.
    for query, params, tier in (
        (SYMBOL_BY_QUALIFIED, (symbol_name,), "symbol"),
        (SYMBOL_BY_NAME, (symbol_name,), "symbol"),
        (SEARCH_SYMBOLS, (f"%{symbol_name}%", 10), "fuzzy"),
    ):
        rows = conn.execute(query, params).fetchall()
        if file_hint:
            rows = _filter_by_file(rows, file_hint)
        if not rows:
            continue
        if len(rows) == 1:
            return _stamp_tier(rows[0], tier), []

        signals = _ambiguity_signals(conn, rows)

        # Bind `signals` as default so the closure captures THIS iteration's
        # value (avoids the late-binding-loop closure pitfall flagged by B023).
        def _score(r, _sig=signals):
            return (
                _sig["ref"].get(r["id"], 0),
                _sig["pr"].get(r["id"], 0),
                _sig["cc"].get(r["id"], 0),
                _sig["churn"].get(r["file_id"], 0) if "file_id" in r.keys() else 0,
                _path_rank(r["file_path"]),
                -r["id"],
            )

        ordered = sorted(rows, key=_score, reverse=True)
        best = ordered[0]
        alternatives = [_stamp_tier(r, tier) for r in ordered[1:]]
        return _stamp_tier(best, tier), alternatives

    return None, []


def find_symbol(conn: sqlite3.Connection, name: str) -> dict | None:
    """Find a symbol by name with disambiguation.

    Lookup chain:
    1. Parse file:symbol hint if present
    2. Try qualified_name match (fetchall)
    3. Try simple name match (fetchall)
    4. Try fuzzy LIKE match (limit 10)
    5. At each step: if multiple matches -> rank by importance signals
    6. If file hint provided -> filter candidates first

    Always returns a single row or None. Never returns a list.

    W1249: the returned row carries ``_resolution_tier`` (``"symbol"`` for
    exact-name matches, ``"fuzzy"`` for LIKE-fallback matches). Callers read
    ``row.get("_resolution_tier", "symbol")``; the default keeps backwards
    compatibility for callers that build row-like dicts independently.
    """
    best, alternatives = find_symbol_with_alternatives(conn, name)
    if alternatives and isinstance(best, dict):
        # T4: disclose that the name was ambiguous instead of silently picking one.
        # Callers/envelopes can read ``_ambiguous`` + ``_alternatives_count`` without a
        # signature change; the single-row contract for the 23 callers is preserved.
        best.setdefault("_ambiguous", True)
        best.setdefault("_alternatives_count", len(alternatives))
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
    except Exception as _exc:  # noqa: BLE001 — defensive
        # FTS5 unavailable / MATCH-syntax error is the documented trigger
        # for the LIKE fallback below — make the degradation loud.
        from roam.observability import log_swallowed

        log_swallowed("resolve:fts_suggestions:fts5_query", _exc)
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
        except Exception as _exc:  # noqa: BLE001 — defensive
            # Both lookup paths failed -> zero suggestions returned;
            # surface the cause so it isn't mistaken for "no matches".
            from roam.observability import log_swallowed

            log_swallowed("resolve:fts_suggestions:like_fallback", _exc)
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


def _batch_barrel_counts_to_avoid_n1(conn: sqlite3.Connection, file_ids: list[int]) -> dict[int, int]:
    """Batch definition counts so index.* barrel filtering avoids N+1 I/O."""
    counts = {file_id: 0 for file_id in file_ids}
    rows = batched_in(
        conn,
        """
        SELECT file_id, COUNT(*) AS own_defs
        FROM symbols
        WHERE file_id IN ({ph})
          AND kind IN ('function', 'class', 'method')
        GROUP BY file_id
        """,
        file_ids,
    )
    counts.update({r["file_id"]: r["own_defs"] for r in rows})
    return counts


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
    barrel_counts = _batch_barrel_counts_to_avoid_n1(
        conn,
        [path_to_id[path] for path in conventional_paths if os.path.basename(path).startswith("index.")],
    )

    # 2. Barrel-file filtering: skip index.* files with <=2 own definitions
    for path in conventional_paths:
        bn = os.path.basename(path)
        if bn.startswith("index."):
            file_id = path_to_id[path]
            own_defs = barrel_counts[file_id]
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
