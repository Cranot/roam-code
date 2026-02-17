"""Workspace overlay DB: cross-repo edges and metadata."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from roam.workspace.config import get_workspace_db_path


WORKSPACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ws_repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    role TEXT,
    db_path TEXT NOT NULL,
    last_indexed REAL
);

CREATE TABLE IF NOT EXISTS ws_route_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES ws_repos(id) ON DELETE CASCADE,
    symbol_id INTEGER NOT NULL,
    url_pattern TEXT NOT NULL,
    http_method TEXT,
    kind TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER,
    symbol_name TEXT
);

CREATE TABLE IF NOT EXISTS ws_cross_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_repo_id INTEGER NOT NULL REFERENCES ws_repos(id),
    source_symbol_id INTEGER NOT NULL,
    target_repo_id INTEGER NOT NULL REFERENCES ws_repos(id),
    target_symbol_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_ws_route_symbols_repo
    ON ws_route_symbols(repo_id);
CREATE INDEX IF NOT EXISTS idx_ws_route_symbols_url
    ON ws_route_symbols(url_pattern);
CREATE INDEX IF NOT EXISTS idx_ws_cross_edges_source
    ON ws_cross_edges(source_repo_id, source_symbol_id);
CREATE INDEX IF NOT EXISTS idx_ws_cross_edges_target
    ON ws_cross_edges(target_repo_id, target_symbol_id);
"""


def get_workspace_connection(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    """Get a SQLite connection to the workspace overlay DB."""
    if readonly:
        uri = db_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def ensure_workspace_schema(conn: sqlite3.Connection) -> None:
    """Create workspace tables if they don't exist."""
    conn.executescript(WORKSPACE_SCHEMA_SQL)


@contextmanager
def open_workspace_db(root: Path, readonly: bool = False):
    """Context manager for workspace DB access."""
    db_path = get_workspace_db_path(root)
    conn = get_workspace_connection(db_path, readonly=readonly)
    try:
        if not readonly:
            ensure_workspace_schema(conn)
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def upsert_repo(conn: sqlite3.Connection, name: str, path: str,
                role: str, db_path: str, last_indexed: float | None = None) -> int:
    """Insert or update a repo entry. Returns the repo id."""
    conn.execute(
        "INSERT INTO ws_repos (name, path, role, db_path, last_indexed) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET "
        "path=excluded.path, role=excluded.role, "
        "db_path=excluded.db_path, last_indexed=excluded.last_indexed",
        (name, path, role, db_path, last_indexed),
    )
    row = conn.execute("SELECT id FROM ws_repos WHERE name=?", (name,)).fetchone()
    return row["id"]


def get_repos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all registered repos."""
    return conn.execute("SELECT * FROM ws_repos ORDER BY name").fetchall()


def get_cross_edges(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all cross-repo edges."""
    return conn.execute(
        "SELECT e.*, "
        "  sr.name AS source_repo_name, tr.name AS target_repo_name "
        "FROM ws_cross_edges e "
        "JOIN ws_repos sr ON sr.id = e.source_repo_id "
        "JOIN ws_repos tr ON tr.id = e.target_repo_id "
        "ORDER BY e.kind, e.id"
    ).fetchall()


def clear_cross_edges(conn: sqlite3.Connection) -> None:
    """Remove all cross-repo edges and route symbols (before re-resolve)."""
    conn.execute("DELETE FROM ws_cross_edges")
    conn.execute("DELETE FROM ws_route_symbols")
