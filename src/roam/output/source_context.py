"""Shared source-context primitives — "code AT a location, with a staleness
guard."

Why this module exists (2026-06-02 dogfood): the dominant fallback pattern
in production telemetry is that roam tools return a LOCATION (file:line) and
the agent then re-greps / re-Reads to see the actual code. We fixed this for
`roam search` (body_preview) and `roam uses` (call_line) — but with DUPLICATED
read-file-with-staleness-guard logic. This module is the single canonical
implementation so every location-returning command (and the compiler probes)
can attach source context the same way, and a future `--with-context` flag
has one place to grow.

The staleness guard is the load-bearing piece: during active development the
index goes stale fast (line numbers shift), so a naive "read line N" shows
WRONG content. When a symbol name is supplied and is absent from the slice,
these helpers return '' rather than mislead.
"""

from __future__ import annotations

import os


def _resolve(rel_path: str, cwd: str | None = None) -> str | None:
    if not rel_path:
        return None
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(cwd or os.getcwd(), rel_path)


def read_body_preview(
    rel_path: str, line_start, symbol_name: str = "", n_lines: int = 6, cwd: str | None = None
) -> str:
    """First `n_lines` of a definition starting at `line_start` (1-indexed).

    Used by `roam search` so the agent doesn't Read the whole file (24% of
    search_symbol fallbacks were file Reads). Staleness guard: if
    `symbol_name` is given and absent from the first 2 lines, return ''."""
    full = _resolve(rel_path, cwd)
    if not full or not line_start:
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    start = max(0, int(line_start) - 1)
    slice_lines = lines[start : start + n_lines]
    if symbol_name and symbol_name not in "".join(slice_lines[:2]):
        return ""  # stale index — line points at wrong content
    return "".join(slice_lines).rstrip()


def read_source_line(rel_path: str, line_no, symbol_name: str = "", max_len: int = 200, cwd: str | None = None) -> str:
    """The single trimmed source line at `line_no` (1-indexed).

    Used by `roam uses` so the agent SEES the calling line without
    re-grepping the symbol (76% of roam_uses fallbacks). Staleness guard: if
    `symbol_name` is given and absent from the line, return ''."""
    full = _resolve(rel_path, cwd)
    if not full or not line_no:
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    idx = int(line_no) - 1
    if not (0 <= idx < len(lines)):
        return ""
    text = lines[idx].strip()
    if symbol_name and symbol_name not in text:
        return ""
    return text[:max_len]
