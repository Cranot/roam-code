"""Shared symbol resolution and index helpers for all roam commands."""

import click

from roam.db.connection import db_exists
from roam.db.queries import SYMBOL_BY_NAME, SYMBOL_BY_QUALIFIED, SEARCH_SYMBOLS


def ensure_index():
    """Build the index if it doesn't exist yet."""
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


def pick_best(conn, rows):
    """Pick the most-referenced symbol from ambiguous matches.

    Returns the row with the highest incoming edge count, or None if
    no candidate has any incoming edges.
    """
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]

    ids = [r["id"] for r in rows]
    ph = ",".join("?" for _ in ids)
    counts = conn.execute(
        f"SELECT target_id, COUNT(*) as cnt FROM edges "
        f"WHERE target_id IN ({ph}) GROUP BY target_id",
        ids,
    ).fetchall()
    ref_map = {c["target_id"]: c["cnt"] for c in counts}
    best = max(rows, key=lambda r: ref_map.get(r["id"], 0))
    if ref_map.get(best["id"], 0) > 0:
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


def find_symbol(conn, name):
    """Find a symbol by name with disambiguation.

    Lookup chain:
    1. Parse file:symbol hint if present
    2. Try qualified_name match (fetchall)
    3. Try simple name match (fetchall)
    4. Try fuzzy LIKE match (limit 10)
    5. At each step: if multiple matches -> pick_best (most incoming edges)
    6. If file hint provided -> filter candidates first

    Always returns a single row or None. Never returns a list.
    """
    file_hint, symbol_name = _parse_file_hint(name)

    # 1. Qualified name match
    rows = conn.execute(SYMBOL_BY_QUALIFIED, (symbol_name,)).fetchall()
    if file_hint:
        rows = _filter_by_file(rows, file_hint)
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        best = pick_best(conn, rows)
        if best:
            return best
        return rows[0]

    # 2. Simple name match
    rows = conn.execute(SYMBOL_BY_NAME, (symbol_name,)).fetchall()
    if file_hint:
        rows = _filter_by_file(rows, file_hint)
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        best = pick_best(conn, rows)
        if best:
            return best
        return rows[0]

    # 3. Fuzzy match
    rows = conn.execute(SEARCH_SYMBOLS, (f"%{symbol_name}%", 10)).fetchall()
    if file_hint:
        rows = _filter_by_file(rows, file_hint)
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        best = pick_best(conn, rows)
        if best:
            return best
        return rows[0]

    return None
