"""Plan-compile SQLite cache policy — the cross-session ``_run_roam`` result
cache and its index-generation invalidation sweep.

Extracted from ``roam.plan.compiler`` so the persistence policy (what is held
at rest, for how long, when it is wiped) lives apart from the compiler's
dispatch/runner code. ``compiler`` keeps thin compatibility re-exports of every
public name here, so existing call sites and tests that reference
``compiler._run_roam_persist_*`` keep working unchanged.

This module is a leaf: it imports only stdlib + ``roam.observability``. The one
backward reference (``_fast_json_dumps``, used when serializing a cache row) is
a function-local lazy import so there is no import cycle with ``compiler``.
"""

from __future__ import annotations

import json
import os
import time
from hashlib import sha256

from roam.observability import log_swallowed


def _set_wal(conn) -> None:
    """Best-effort: switch a cache connection to WAL journal mode (W148 family).

    WAL is a throughput optimization for the SQLite caches; if the filesystem
    doesn't support it SQLite raises an operational error and we keep the
    default journal mode (correctness unaffected). The failure is surfaced via
    ``log_swallowed`` so it is visible under verbose observability without
    flooding stderr on filesystems that cannot use WAL."""
    import sqlite3

    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        log_swallowed("plan_cache.set_wal", exc)


def _index_db_path(cwd: str | None) -> str:
    return os.path.join(cwd or ".", ".roam", "index.db")


# W147 — persistent _run_roam result cache (SQLite, cross-session).
# In-memory _RUN_ROAM_CACHE dies with the process. For long-lived MCP
# server sessions, agents repeatedly ask the same probes across many
# tasks — each `roam uses X` re-pays a 30-50ms subprocess cost even
# though the underlying graph is unchanged. Persist successful results
# keyed on (args, cwd, repo_head). 24-hour TTL; 4096-row cap.
_RUN_ROAM_PERSIST_CAP = 4096
_RUN_ROAM_PERSIST_TTL_S = 24 * 3600.0
_RUN_ROAM_PERSIST_TABLE_INITED: set[str] = set()


# W468 — subcommands whose JSON results embed raw source snippets, matched
# file lines, or config values. These can carry secrets lifted from the repo
# (API keys, tokens, PEM blocks) and would otherwise sit in the cross-session
# SQLite cache (compile-envelope-cache.sqlite) for the full 24h TTL. We skip
# persisting them at rest; the 60s in-proc cache (_RUN_ROAM_CACHE) is
# unaffected because it never touches disk. `grep`/`retrieve`/`search-semantic`/
# `taint` are wired through _run_roam today; `file`/`refs-text`/`history-grep`/
# `config` are listed preemptively so a future probe that routes them through
# _run_roam is covered.
_RUN_ROAM_PERSIST_SENSITIVE_SUBCMDS = frozenset(
    {
        "grep",  # matched source lines
        "retrieve",  # ranked source spans
        "search-semantic",  # ranked code snippets
        "taint",  # source spans along taint flows
        "file",  # file skeleton incl. signatures
        "refs-text",  # string audit with surrounding source
        "history-grep",  # matched lines from git history
        "config",  # raw config values
    }
)


def _run_roam_persist_is_sensitive(args: list[str]) -> bool:
    """True if the subcommand result embeds raw source/config content.

    Used to skip the persistent SQLite cache so secrets/snippets are not held
    at rest for the 24h TTL. The first token of ``args`` is the roam subcommand
    name (e.g. ``["grep", "--", pat]`` -> ``"grep"``).
    """
    return bool(args) and args[0] in _RUN_ROAM_PERSIST_SENSITIVE_SUBCMDS


def _run_roam_persist_path(cwd: str | None) -> str | None:
    if not cwd:
        return None
    try:
        roam_dir = os.path.join(cwd, ".roam")
        if not os.path.isdir(roam_dir):
            return None
        path = os.path.join(roam_dir, "compile-envelope-cache.sqlite")
        _persist_sweep_stale_generation(path, cwd)
        return path
    except (OSError, TypeError):
        return None


# Tables whose rows are DERIVED FROM THE INDEX but invalidated only by
# TTL + repo HEAD. Under uncommitted dev edits HEAD never moves, so a row
# captured from a stale index outlives `roam index --force` and keeps
# feeding poisoned facts (stale line numbers) into freshly-stamped
# envelopes — observed live 2026-06-11 via probe_pos/run_roam on the
# structural_callers path. env_cache is NOT listed: it carries per-row
# dep mtimes + its own index stamp and self-evicts with precision.
_INDEX_DERIVED_TABLES = (
    "run_roam_cache",
    "probe_pos_cache",
    "probe_neg_cache",
    "symbol_resolution_cache",
    "plan_cache",
)
_PERSIST_GENERATION_SWEPT: set[str] = set()


def _persist_sweep_stale_generation(path: str, cwd: str | None) -> None:
    """Once per process per cache DB: if .roam/index.db's mtime differs
    from the generation recorded in the DB, wipe every index-derived
    table. Re-indexing thereby invalidates all coarse-keyed caches at
    once; precision-keyed tables keep their own row-level checks."""
    if path in _PERSIST_GENERATION_SWEPT:
        return
    _PERSIST_GENERATION_SWEPT.add(path)
    try:
        generation = str(int(os.path.getmtime(_index_db_path(cwd)) * 1000))
    except OSError:
        return  # no index yet — nothing derived from it can be stale
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            _apply_generation_sweep(conn, generation)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — cache hygiene must never break a compile
        log_swallowed("compile.persist_sweep", exc)


def _apply_generation_sweep(conn, generation: str) -> None:
    """Wipe the index-derived tables unless `generation` matches the one
    recorded in persist_meta; record the new generation either way."""
    import sqlite3

    conn.execute("CREATE TABLE IF NOT EXISTS persist_meta (k TEXT PRIMARY KEY, v TEXT)")
    row = conn.execute("SELECT v FROM persist_meta WHERE k='index_generation'").fetchone()
    if row and row[0] == generation:
        return
    for table in _INDEX_DERIVED_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — closed enum above
        except sqlite3.OperationalError as exc:
            log_swallowed("compile.persist_sweep.missing_table", exc)
    conn.execute(
        "INSERT OR REPLACE INTO persist_meta VALUES ('index_generation', ?)",
        (generation,),
    )
    conn.commit()


def _run_roam_persist_ensure_schema(conn) -> None:
    """Create table + W148 WAL pragma on first use per connection."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_roam_cache (key TEXT PRIMARY KEY, head TEXT, result_json TEXT, ts REAL)"
    )
    _set_wal(conn)


def _run_roam_persist_key(args: list[str], cwd_norm: str) -> str:
    blob = "\x1f".join([cwd_norm or "", *args]).encode("utf-8", "replace")
    return sha256(blob).hexdigest()[:24]


def _run_roam_persist_get(args: list[str], cwd: str | None, head: str) -> dict | None:
    path = _run_roam_persist_path(cwd)
    if not path:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            if path not in _RUN_ROAM_PERSIST_TABLE_INITED:
                _run_roam_persist_ensure_schema(conn)
                _RUN_ROAM_PERSIST_TABLE_INITED.add(path)
            key = _run_roam_persist_key(args, cwd or "")
            row = conn.execute(
                "SELECT head, result_json, ts FROM run_roam_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, result_json, ts = row
            if (time.time() - ts) > _RUN_ROAM_PERSIST_TTL_S:
                conn.execute("DELETE FROM run_roam_cache WHERE key=?", (key,))
                conn.commit()
                return None
            if head and cached_head and cached_head != head:
                conn.execute("DELETE FROM run_roam_cache WHERE key=?", (key,))
                conn.commit()
                return None
            try:
                return json.loads(result_json)
            except json.JSONDecodeError:
                return None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.run_roam_persist.get", exc)
        return None


def _run_roam_persist_put(args: list[str], cwd: str | None, head: str, value: dict | None) -> None:
    if value is None:
        return
    path = _run_roam_persist_path(cwd)
    if not path:
        return
    # Lazy import: _fast_json_dumps lives in roam.plan.compiler (general envelope
    # serialization, orjson fast-path). Imported at call time so this leaf module
    # has no import cycle with compiler.
    from roam.plan.compiler import _fast_json_dumps

    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            if path not in _RUN_ROAM_PERSIST_TABLE_INITED:
                _run_roam_persist_ensure_schema(conn)
                _RUN_ROAM_PERSIST_TABLE_INITED.add(path)
            key = _run_roam_persist_key(args, cwd or "")
            conn.execute(
                "INSERT OR REPLACE INTO run_roam_cache VALUES (?,?,?,?)",
                (key, head or "", _fast_json_dumps(value), time.time()),
            )
            # LRU-ish cap: evict oldest by ts when over.
            (count,) = conn.execute("SELECT COUNT(*) FROM run_roam_cache").fetchone()
            if count > _RUN_ROAM_PERSIST_CAP:
                conn.execute(
                    "DELETE FROM run_roam_cache WHERE key IN (SELECT key FROM run_roam_cache ORDER BY ts LIMIT ?)",
                    (count - _RUN_ROAM_PERSIST_CAP,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.run_roam_persist.put", exc)
