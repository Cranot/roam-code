"""SQLite connection management with WAL mode and performance pragmas."""

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


def get_db_path(project_root: Path | None = None) -> Path:
    """Get the path to the index database."""
    if project_root is None:
        project_root = find_project_root()
    db_dir = project_root / DEFAULT_DB_DIR
    db_dir.mkdir(exist_ok=True)
    return db_dir / DEFAULT_DB_NAME


def get_connection(db_path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Get a SQLite connection with optimized settings."""
    if db_path is None:
        db_path = get_db_path()

    if readonly:
        uri = db_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30)

    conn.row_factory = sqlite3.Row
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
    _safe_alter(conn, "snapshots", "tangle_ratio", "REAL")
    _safe_alter(conn, "snapshots", "avg_complexity", "REAL")
    _safe_alter(conn, "snapshots", "brain_methods", "INTEGER")


def _safe_alter(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't exist."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass  # Column already exists


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
