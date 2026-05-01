"""Symbol/file completions backed by the FTS5 index.

Two surfaces:

1. **MCP protocol completion** — registered on the underlying low-level
   server (``mcp._mcp_server``). Activates when the agent / human user
   is typing into a *prompt argument* or *resource template variable*
   that the server knows takes a symbol or path. FastMCP 3.0.x doesn't
   expose this via decorator yet, so we patch the handler directly.

2. **Direct tool** — :func:`complete_prefix` is also exposed as the
   ``roam_complete`` tool so agents can call it explicitly without
   relying on protocol-level completion (which not every MCP client
   supports yet).

Both paths share the same prefix-lookup function so behaviour stays
consistent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Argument-name conventions we'll auto-complete on. If a prompt or
# resource template uses an arg with one of these names, we treat its
# value as the partial.
_SYMBOL_ARG_NAMES = {"symbol", "name", "qname", "target"}
_PATH_ARG_NAMES = {"path", "file", "file_path"}
_COMMAND_ARG_NAMES = {"command", "cmd"}

_MAX_RESULTS = 30


def _project_root_for(value: str | None) -> Path | None:
    """Best-effort discovery of the .roam dir given a CWD hint."""
    start = Path(value or os.getcwd()).resolve()
    if not start.exists():
        start = Path.cwd()
    cur: Path | None = start
    while cur is not None:
        if (cur / ".roam" / "index.db").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def _open_index(root: str | None = None):
    """Open the SQLite index in read-only mode. Returns conn or None."""
    project = _project_root_for(root)
    if project is None:
        return None
    db_path = project / ".roam" / "index.db"
    if not db_path.exists():
        return None
    try:
        from roam.db.connection import open_db

        return open_db(db_path=str(db_path), readonly=True)
    except Exception:
        try:
            import sqlite3

            return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except Exception:
            return None


_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _fts5_prefix_query(prefix: str) -> str:
    """Build a prefix FTS5 MATCH expression for a partial token."""
    tokens = _FTS_TOKEN_RE.findall(prefix or "")
    if not tokens:
        return ""
    parts = [f"{t}*" for t in tokens]
    return " ".join(parts)


def complete_symbols(prefix: str, *, limit: int = _MAX_RESULTS, root: str | None = None) -> list[str]:
    """Return up to *limit* symbol names matching ``prefix``."""
    if not prefix:
        return []
    conn = _open_index(root)
    if conn is None:
        return []
    try:
        match_expr = _fts5_prefix_query(prefix)
        rows: list[Any] = []
        if match_expr:
            try:
                cur = conn.execute(
                    """
                    SELECT s.name
                    FROM symbol_fts f
                    JOIN symbols s ON s.id = f.rowid
                    WHERE symbol_fts MATCH ?
                    ORDER BY bm25(symbol_fts) ASC
                    LIMIT ?
                    """,
                    (match_expr, limit * 3),
                )
                rows = cur.fetchall()
            except Exception:
                rows = []
        if not rows:
            try:
                cur = conn.execute(
                    """
                    SELECT name FROM symbols
                    WHERE name LIKE ?
                    ORDER BY length(name) ASC
                    LIMIT ?
                    """,
                    (prefix + "%", limit * 3),
                )
                rows = cur.fetchall()
            except Exception:
                rows = []

        seen: set[str] = set()
        out: list[str] = []
        for row in rows:
            name = row[0] if not isinstance(row, str) else row
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) >= limit:
                break
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def complete_paths(prefix: str, *, limit: int = _MAX_RESULTS, root: str | None = None) -> list[str]:
    """Return up to *limit* indexed file paths starting with ``prefix``."""
    conn = _open_index(root)
    if conn is None:
        return []
    try:
        like = (prefix or "") + "%"
        try:
            cur = conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ORDER BY path LIMIT ?",
                (like, limit),
            )
            return [row[0] for row in cur.fetchall() if row and row[0]]
        except Exception:
            return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def complete_commands(prefix: str, *, limit: int = _MAX_RESULTS) -> list[str]:
    """Return roam CLI command names matching ``prefix``."""
    try:
        from roam.cli import _COMMANDS
    except Exception:
        return []
    p = (prefix or "").lower()
    out = sorted(name for name in _COMMANDS.keys() if name.lower().startswith(p))
    return out[:limit]


def complete_prefix(
    prefix: str,
    *,
    kind: str = "symbol",
    limit: int = _MAX_RESULTS,
    root: str | None = None,
) -> dict[str, list[str]]:
    """Public completion entry point used by ``roam_complete`` tool."""
    kind = (kind or "symbol").lower()
    if kind == "symbol":
        return {"symbols": complete_symbols(prefix, limit=limit, root=root)}
    if kind == "path":
        return {"paths": complete_paths(prefix, limit=limit, root=root)}
    if kind == "command":
        return {"commands": complete_commands(prefix, limit=limit)}
    if kind == "all":
        return {
            "symbols": complete_symbols(prefix, limit=limit, root=root),
            "paths": complete_paths(prefix, limit=limit, root=root),
            "commands": complete_commands(prefix, limit=limit),
        }
    return {}


# ---------------------------------------------------------------------------
# Protocol-level completion handler
# ---------------------------------------------------------------------------


def install_completion_handler(fastmcp_server: Any) -> bool:
    """Register a low-level completion handler on the FastMCP server.

    Returns True on success, False if the underlying handler hook is
    not exposed by this version of FastMCP. Safe to call multiple
    times (last registration wins).
    """
    if fastmcp_server is None:
        return False
    low_level = getattr(fastmcp_server, "_mcp_server", None)
    if low_level is None:
        return False
    completion_decorator = getattr(low_level, "completion", None)
    if not callable(completion_decorator):
        return False

    try:
        from mcp.types import (
            CompleteResult,
            Completion,
            CompletionArgument,
            CompletionContext,
            PromptReference,
            ResourceTemplateReference,
        )
    except Exception:
        return False

    @completion_decorator()  # type: ignore[misc]
    async def _complete(
        ref: Any,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> CompleteResult:
        value = (argument.value or "").strip()
        arg_name = (argument.name or "").lower()

        values: list[str] = []
        if arg_name in _SYMBOL_ARG_NAMES:
            values = complete_symbols(value)
        elif arg_name in _PATH_ARG_NAMES:
            values = complete_paths(value)
        elif arg_name in _COMMAND_ARG_NAMES:
            values = complete_commands(value)
        elif isinstance(ref, ResourceTemplateReference):
            uri = getattr(ref, "uri", "") or ""
            if "symbol" in uri:
                values = complete_symbols(value)
            elif "file" in uri or "path" in uri:
                values = complete_paths(value)
        elif isinstance(ref, PromptReference):
            # Best-effort fallback for unknown prompt args.
            values = complete_symbols(value)[:10]

        return CompleteResult(
            completion=Completion(
                values=values[:_MAX_RESULTS],
                total=len(values),
                hasMore=len(values) > _MAX_RESULTS,
            )
        )

    return True
