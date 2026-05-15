"""Shared helper: resolve a (file_id, line) to the smallest containing symbol.

W774 — replaces the ``MIN(id)`` synthetic-source anti-pattern in
``registry_dispatch.py`` and ``laravel_post.py``. The bug: both modules
attribute every synthesised edge to whichever symbol happens to own the
lowest ``id`` in the file, regardless of where the dispatch site lives.
That made ``roam impact`` mis-attribute callers (e.g. the controller
*class* gets credited for the dispatch its *method* makes).

The fix:

1. Build ``{file_id: [(line_start, line_end, symbol_id), ...]}`` ranges
   from the ``symbols`` table for a given language.
2. For each anchor line, walk the file's ranges and pick the
   **innermost** symbol whose ``[line_start, line_end]`` contains the
   anchor — innermost = the one with the largest ``line_start`` that
   still satisfies the containment test.
3. If no symbol contains the line, return ``None`` so the caller can
   apply its own fallback (synthesise a file anchor, skip, or log).
   **Never** silently fall to the lowest-id symbol (Pattern 2: silent
   fallback discipline).

``line_end`` is permitted to be ``NULL`` in the symbols schema, which
both production code (PHP/Python extractors sometimes can't determine
end lines) and the in-memory test fixtures rely on. The helper treats
``line_end IS NULL`` as "extends to end of file" — that's the safest
assumption because under-counting `line_end` would cause false misses
on the containment test.
"""

from __future__ import annotations

from typing import Optional


_LINE_END_INFINITY = 10**9  # sentinel for "extends to end of file"


def build_file_symbol_ranges(
    conn,
    language: str,
) -> dict[int, list[tuple[int, int, int]]]:
    """Return ``{file_id: [(line_start, line_end_or_infinity, symbol_id), ...]}``.

    Symbols with ``line_start IS NULL`` are skipped — they cannot
    participate in line-based containment lookups. Synthetic
    file-anchor symbols (``name = '<roam-synthetic-file-anchor>'``) are
    also skipped so prior-run anchors cannot leak into a fresh
    resolver pass as a "containing" symbol.

    The per-file list is sorted by ``line_start`` ascending so callers
    can do a linear walk and pick the last match.
    """
    out: dict[int, list[tuple[int, int, int]]] = {}
    rows = conn.execute(
        """
        SELECT s.id, s.file_id, s.line_start, s.line_end
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = ?
          AND s.line_start IS NOT NULL
          AND s.name != '<roam-synthetic-file-anchor>'
        """,
        (language,),
    ).fetchall()
    for r in rows:
        line_start = r["line_start"]
        line_end = r["line_end"]
        if line_end is None or line_end < line_start:
            line_end = _LINE_END_INFINITY
        out.setdefault(r["file_id"], []).append(
            (int(line_start), int(line_end), int(r["id"]))
        )
    for file_id in out:
        out[file_id].sort(key=lambda t: t[0])
    return out


def containing_symbol_for_line(
    ranges: list[tuple[int, int, int]],
    line: int,
) -> Optional[int]:
    """Pick the innermost symbol whose ``[line_start, line_end]``
    contains ``line``. Returns ``None`` when no symbol contains it.

    "Innermost" = the candidate with the largest ``line_start`` among
    those satisfying ``line_start <= line <= line_end``. Because the
    range list is sorted by ``line_start`` ascending, we walk forward
    keeping the latest match. This correctly resolves nested symbols
    (method inside class inside module): the method's range starts
    after the class's, so it wins.

    Tie-break: when two symbols share the same ``line_start`` (common
    in test fixtures where every symbol is at line 1, and possible in
    real code when a class declaration and its first method share a
    starting line in tightly-formatted source), prefer the larger
    ``symbol_id``. Tree-sitter extractors emit parents before
    children, so a larger id reliably means "inserted later" =
    "inner". This keeps the dispatch-attribution semantics stable
    even when ``line_end`` is unavailable for both candidates.
    """
    best_id: Optional[int] = None
    best_start = -1
    for start, end, sym_id in ranges:
        if start > line:
            # Sorted by line_start — no later entry can contain this line.
            break
        if line > end:
            continue
        if start > best_start or (start == best_start and (best_id is None or sym_id > best_id)):
            best_id = sym_id
            best_start = start
    return best_id
