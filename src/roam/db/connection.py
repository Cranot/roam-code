"""SQLite connection management with adaptive journal mode and performance pragmas."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager

from roam.db.schema import SCHEMA_SQL

DEFAULT_DB_DIR = ".roam"
DEFAULT_DB_NAME = "index.db"


def find_project_root(start: str = ".") -> Path:
    """Find the project root by looking for .git directory."""
    current = Path(start).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path(start).resolve()


def _load_project_config(project_root: Path) -> dict:
    """Load .roam/config.json if it exists.

    Returns an empty dict if the file is missing or malformed.
    """
    config_path = project_root / DEFAULT_DB_DIR / "config.json"
    if config_path.exists():
        try:
            import json
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_project_config(config: dict, project_root: Path | None = None) -> Path:
    """Write (or update) .roam/config.json.

    Merges *config* into the existing config so existing keys are preserved.
    Returns the path of the written file.
    """
    import json
    if project_root is None:
        project_root = find_project_root()
    roam_dir = project_root / DEFAULT_DB_DIR
    roam_dir.mkdir(exist_ok=True)
    config_path = roam_dir / "config.json"
    existing = _load_project_config(project_root)
    existing.update(config)
    config_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return config_path


def get_db_path(project_root: Path | None = None) -> Path:
    """Get the path to the index database.

    Resolution order (first match wins):

    1. ``ROAM_DB_DIR`` environment variable — redirect to a local directory,
       useful when the project lives on OneDrive/Dropbox or a network drive.
    2. ``.roam/config.json`` → ``"db_dir"`` key — persistent per-project
       alternative to the env-var (write once with ``roam config``).
    3. Default: ``<project_root>/.roam/index.db``.
    """
    override = os.environ.get("ROAM_DB_DIR")
    if override:
        db_dir = Path(override)
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / DEFAULT_DB_NAME
    if project_root is None:
        project_root = find_project_root()
    # Check .roam/config.json for a db_dir override
    config = _load_project_config(project_root)
    if config.get("db_dir"):
        db_dir = Path(config["db_dir"])
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / DEFAULT_DB_NAME
    db_dir = project_root / DEFAULT_DB_DIR
    db_dir.mkdir(exist_ok=True)
    return db_dir / DEFAULT_DB_NAME


def _is_cloud_synced(path: Path) -> bool:
    """Detect if *path* lives under a cloud-sync folder (OneDrive, Dropbox, etc.).

    WAL mode creates auxiliary ``-wal`` and ``-shm`` files that cloud sync
    services aggressively lock, causing SQLite writes to stall.  When we
    detect a cloud-synced path we fall back to DELETE journal mode.
    """
    markers = ("onedrive", "dropbox", "google drive", "icloud")
    resolved = str(path.resolve()).lower()
    return any(m in resolved for m in markers)


def get_connection(db_path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Get a SQLite connection with optimized settings."""
    if db_path is None:
        db_path = get_db_path()

    if readonly:
        # UNC network paths (e.g. \\server\share\...) cannot be expressed as
        # valid SQLite file:// URIs — SQLite rejects authority-based URIs.
        # Mapped drive letters (M:\...) work fine.  We try the URI form first
        # (which enforces read-only at the driver level) and fall back to a
        # plain connection when the path cannot be expressed as a URI.
        try:
            uri = db_path.as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30)
        except (sqlite3.OperationalError, ValueError):
            conn = sqlite3.connect(str(db_path), timeout=30)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30)

    conn.row_factory = sqlite3.Row
    cloud = _is_cloud_synced(db_path)
    if not readonly:
        if cloud:
            # Cloud-sync services (OneDrive, Dropbox, etc.) lock WAL/SHM
            # auxiliary files and even the main DB during sync, causing
            # writes to stall.  Mitigations:
            #   1. DELETE journal — avoids WAL/SHM auxiliary files entirely
            #   2. EXCLUSIVE locking — holds the file lock for the session,
            #      preventing the sync agent from grabbing it mid-write
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def ensure_schema(conn: sqlite3.Connection):
    """Create tables if they don't exist, and apply migrations."""
    conn.executescript(SCHEMA_SQL)

    # Migrations for columns added after initial schema
    _safe_alter(conn, "symbols", "default_value", "TEXT")
    _safe_alter(conn, "file_stats", "health_score", "REAL")
    _safe_alter(conn, "file_stats", "cochange_entropy", "REAL")
    _safe_alter(conn, "file_stats", "cognitive_load", "REAL")
    _safe_alter(conn, "snapshots", "tangle_ratio", "REAL")
    _safe_alter(conn, "snapshots", "avg_complexity", "REAL")
    _safe_alter(conn, "snapshots", "brain_methods", "INTEGER")
    # v7.4: Halstead metrics + cyclomatic density
    _safe_alter(conn, "symbol_metrics", "cyclomatic_density", "REAL")
    _safe_alter(conn, "symbol_metrics", "halstead_volume", "REAL")
    _safe_alter(conn, "symbol_metrics", "halstead_difficulty", "REAL")
    _safe_alter(conn, "symbol_metrics", "halstead_effort", "REAL")
    _safe_alter(conn, "symbol_metrics", "halstead_bugs", "REAL")
    # v7.6: file role classification
    _safe_alter(conn, "files", "file_role", "TEXT DEFAULT 'source'")
    # v8.3: math_signals table — CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles it
    # v8.4: extended math signals
    _safe_alter(conn, "math_signals", "self_call_count", "INTEGER DEFAULT 0")
    _safe_alter(conn, "math_signals", "str_concat_in_loop", "INTEGER DEFAULT 0")
    _safe_alter(conn, "math_signals", "loop_invariant_calls", "TEXT")
    _safe_alter(conn, "math_signals", "loop_bound_small", "INTEGER DEFAULT 0")
    # Cross-language bridge metadata on edges
    _safe_alter(conn, "edges", "bridge", "TEXT")
    _safe_alter(conn, "edges", "confidence", "REAL")
    # v9.0: runtime_stats table — CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles it
    # Migration: ensure table exists for databases created before this version
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runtime_stats ("
        "id INTEGER PRIMARY KEY, "
        "symbol_id INTEGER REFERENCES symbols(id), "
        "symbol_name TEXT, "
        "file_path TEXT, "
        "trace_source TEXT, "
        "call_count INTEGER DEFAULT 0, "
        "p50_latency_ms REAL, "
        "p99_latency_ms REAL, "
        "error_rate REAL DEFAULT 0.0, "
        "last_seen TEXT, "
        "ingested_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    # v9.x: vulnerabilities table — CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles it
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vulnerabilities ("
        "id INTEGER PRIMARY KEY, "
        "cve_id TEXT, "
        "package_name TEXT NOT NULL, "
        "severity TEXT, "
        "title TEXT, "
        "source TEXT, "
        "matched_symbol_id INTEGER REFERENCES symbols(id), "
        "matched_file TEXT, "
        "reachable INTEGER DEFAULT 0, "
        "shortest_path TEXT, "
        "hop_count INTEGER, "
        "ingested_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    # TF-IDF semantic search table — CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles it
    conn.execute(
        "CREATE TABLE IF NOT EXISTS symbol_tfidf ("
        "symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id), "
        "terms TEXT NOT NULL, "
        "updated_at TEXT DEFAULT (datetime('now'))"
        ")"
    )


def _safe_alter(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't exist."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass  # Column already exists


# ---------------------------------------------------------------------------
# Batched IN-clause helpers — avoid SQLITE_MAX_VARIABLE_NUMBER (default 999)
# ---------------------------------------------------------------------------

_BATCH_SIZE = 400  # conservative — leaves room for extra params


def batched_in(conn, sql, ids, *, pre=(), post=(), batch_size=_BATCH_SIZE):
    """Execute *sql* with ``{ph}`` placeholder(s) in batches.

    Handles single and double IN-clauses automatically::

        # Single IN
        batched_in(conn, "SELECT * FROM t WHERE id IN ({ph})", ids)

        # Double IN (same set)
        batched_in(conn, "... WHERE src IN ({ph}) AND tgt IN ({ph})", ids)

        # Extra params before / after
        batched_in(conn, "... WHERE kind=? AND id IN ({ph})", ids, pre=[kind])

    Returns a flat list of all rows across batches.
    """
    if not ids:
        return []
    ids = list(ids)
    n_ph = sql.count("{ph}")
    chunk = max(1, batch_size // max(n_ph, 1))

    rows = []
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        ph = ",".join("?" for _ in batch)
        q = sql.replace("{ph}", ph)
        params = list(pre) + batch * n_ph + list(post)
        rows.extend(conn.execute(q, params).fetchall())
    return rows


def batched_count(conn, sql, ids, *, pre=(), post=(), batch_size=_BATCH_SIZE):
    """Like :func:`batched_in` but **sums** scalar results (for COUNT queries).

    Returns an integer total.
    """
    if not ids:
        return 0
    ids = list(ids)
    n_ph = sql.count("{ph}")
    chunk = max(1, batch_size // max(n_ph, 1))

    total = 0
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        ph = ",".join("?" for _ in batch)
        q = sql.replace("{ph}", ph)
        params = list(pre) + batch * n_ph + list(post)
        total += conn.execute(q, params).fetchone()[0]
    return total


def db_exists(project_root: Path | None = None) -> bool:
    """Check if an index database exists."""
    path = get_db_path(project_root)
    return path.exists() and path.stat().st_size > 0


@contextmanager
def open_db(readonly: bool = False, project_root: Path | None = None):
    """Context manager for database access. Creates schema if needed."""
    db_path = get_db_path(project_root)
    conn = get_connection(db_path, readonly=readonly)
    try:
        if not readonly:
            ensure_schema(conn)
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()
