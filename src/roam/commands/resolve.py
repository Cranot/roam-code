"""Shared symbol resolution and index helpers for all roam commands."""

from __future__ import annotations

import click

from roam.db.connection import db_exists
from roam.db.queries import SYMBOL_BY_NAME, SYMBOL_BY_QUALIFIED, SEARCH_SYMBOLS

# Maximum suggestions returned by fts_suggestions()
_MAX_FTS_SUGGESTIONS = 5


def ensure_index(quiet: bool = False):
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


def symbol_not_found(conn, name: str, *, json_mode: bool = False) -> str:
    """Build a 'symbol not found' error message with FTS5-powered suggestions.

    In text mode returns a multi-line string like::

        Symbol 'FlaskAp' not found. Did you mean:
          cls    Flask_App  (src/app.py:12)
          fn     flask_app  (src/factory.py:5)

    In JSON mode returns a JSON string (roam envelope) with fields:
    ``error``, ``query``, and ``suggestions`` (list of name/kind/location dicts).

    Callers should ``click.echo`` the result and then ``raise SystemExit(1)``.
    """
    from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope

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
        return to_json(json_envelope(
            "error",
            summary={
                "error": f"Symbol not found: {name}",
                "suggestions_count": len(suggestion_dicts),
            },
            error=f"Symbol not found: {name}",
            query=name,
            suggestions=suggestion_dicts,
        ))

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
