"""Shared implementation for CLI and MCP batch symbol search."""

from __future__ import annotations

MAX_BATCH_QUERIES = 10

# Retained for MCP compatibility helpers/tests. The active batch search path
# uses LIKE below so CLI and MCP match exactly for symbol-only and path modes.
BATCH_FTS_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbol_fts sf "
    "JOIN symbols s ON sf.rowid = s.id "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE symbol_fts MATCH ? "
    "ORDER BY rank "
    "LIMIT ?"
)

# Default path: match symbol name / qualified_name only, never file path.
# Path-substring matches caused false positives like ``useAccountBalance``
# returning ``setup`` from ``tests/.../useAccountBalance.test.ts``.
BATCH_LIKE_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbols s "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE (s.name LIKE ? COLLATE NOCASE "
    "    OR s.qualified_name LIKE ? COLLATE NOCASE) "
    "ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name "
    "LIMIT ?"
)

# Opt-in wider match: symbol name OR qualified name OR file path.
BATCH_LIKE_WITH_PATHS_SQL = (
    "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
    "s.line_start, COALESCE(gm.pagerank, 0) as pagerank "
    "FROM symbols s "
    "JOIN files f ON s.file_id = f.id "
    "LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id "
    "WHERE (s.name LIKE ? COLLATE NOCASE "
    "    OR s.qualified_name LIKE ? COLLATE NOCASE "
    "    OR f.path LIKE ? COLLATE NOCASE) "
    "ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name "
    "LIMIT ?"
)

def batch_search_one(
    conn,
    q: str,
    limit: int,
    include_paths: bool = False,
    *,
    like_sql: str = BATCH_LIKE_SQL,
    like_with_paths_sql: str = BATCH_LIKE_WITH_PATHS_SQL,
) -> tuple[list[dict], str | None]:
    """Search for one query in an open DB connection.

    Returns ``(rows, error_or_None)``. Rows are plain dicts.

    Match mode:
    - default: matches symbol name / qualified_name only
    - include_paths=True: legacy wide match including file path
    """
    like = f"%{q}%"

    if include_paths:
        try:
            rows = conn.execute(like_with_paths_sql, (like, like, like, limit)).fetchall()
        except Exception as exc:
            return [], str(exc)
    else:
        try:
            rows = conn.execute(like_sql, (like, like, limit)).fetchall()
        except Exception as exc:
            return [], str(exc)

    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"] or "",
            "kind": r["kind"],
            "file_path": r["file_path"],
            "line_start": r["line_start"],
            # 6 decimals preserve nonzero PageRank signal on large graphs.
            "pagerank": round(float(r["pagerank"] or 0), 6),
        }
        for r in rows
    ], None
