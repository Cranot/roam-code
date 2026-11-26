"""Symbol/file completions backed by the FTS5 index.

Two surfaces:

1. **MCP protocol completion** — registered on the underlying low-level
   server (``mcp._mcp_server``). Activates when the agent / human user
   is typing into a *prompt argument* or *resource template variable*
   that the server knows takes a symbol or path. FastMCP 3.0.x doesn't
   expose this via decorator yet, so we patch the handler directly.

2. **Direct tool** — the ``roam_complete`` MCP wrapper
   (``mcp_server.py``) calls :func:`complete_paths` and
   :func:`complete_commands` from this module, but routes *symbol*
   completion through the CLI's strict left-anchored prefix matcher
   (``cmd_complete._prefix_symbols``) instead of the FTS5-backed
   :func:`complete_symbols` — the W3.1 parity fix: FTS5's camelCase
   tokenizer would let ``use*`` match ``MyUseFoo``, breaking the
   literal-prefix contract the tool promises.

:func:`complete_prefix` is the library-level one-call aggregator over
all three helpers (FTS5 semantics for symbols). The protocol completion
handler uses it for single-kind completions, and it remains a stable
public entry point for embedders and for MCP clients that lack
protocol-level completion support.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from roam.db.connection import get_db_path
from roam.observability import log_swallowed

__all__ = [
    "complete_symbols",
    "complete_paths",
    "complete_commands",
    "complete_prefix",
    "install_completion_handler",
]

# Argument-name conventions we'll auto-complete on. If a prompt or
# resource template uses an arg with one of these names, we treat its
# value as the partial.
_SYMBOL_ARG_NAMES = {"symbol", "name", "qname", "target"}
_PATH_ARG_NAMES = {"path", "file", "file_path"}
_COMMAND_ARG_NAMES = {"command", "cmd"}

_MAX_RESULTS = 30


@lru_cache(maxsize=1)
def _registered_command_names() -> tuple[str, ...]:
    """Read command names without importing the Click CLI entry point."""
    try:
        from roam.surface_counts import cli_commands

        return tuple(sorted(cli_commands().keys()))
    except (ImportError, KeyError, OSError, RuntimeError, SyntaxError, TypeError, ValueError) as exc:
        log_swallowed("completions:command_registry", exc)
        return ()


def _project_root_for(value: str | None) -> Path | None:
    """Best-effort discovery of the .roam dir given a CWD hint.

    Walks upward looking for a ``.roam/index.db`` co-located with a
    real project marker (``.git`` or ``pyproject.toml``). The marker
    requirement prevents pytest tmp_path tests from binding onto a
    stray ``.roam/index.db`` left in ``%TEMP%`` / ``/tmp`` by an
    earlier session — without it the walk reaches all the way to the
    filesystem root and any orphaned index becomes a flake source
    (AA4 audit, 2026-05-17).
    """
    start = Path(value or os.getcwd()).resolve()
    if not start.exists():
        start = Path.cwd()
    cur: Path | None = start
    home = None
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        home = None
    while cur is not None:
        has_index = get_db_path(cur, create=False).exists()
        has_marker = (cur / ".git").exists() or (cur / "pyproject.toml").exists()
        if has_index and has_marker:
            return cur
        # Stop walking once we cross above the user's home directory —
        # anything higher is system territory where finding a project
        # root would be coincidental at best.
        if home is not None and cur == home:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


@contextmanager
def _open_index(root: str | None = None):
    """Yield a read-only connection, including for older completion indexes."""
    from roam.db.connection import open_db
    from roam.db.freshness import StaleQueryIndexError, observe_query_index

    project = _project_root_for(root)
    if project is None:
        yield None
        return
    db_path = get_db_path(project, create=False)
    if not db_path.exists():
        yield None
        return
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_db(project_root=project, readonly=True))
        except StaleQueryIndexError:
            raise
        except Exception as exc:  # noqa: BLE001 -- completions also support older indexes
            log_swallowed("completions:open_db", exc)
            try:
                conn = stack.enter_context(closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)))
            except sqlite3.Error as exc:
                log_swallowed("completions:sqlite", exc)
                conn = None
            if conn is not None:
                observe_query_index(conn, project)
        yield conn


_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _fts5_prefix_query(prefix: str) -> str:
    """Build a prefix FTS5 MATCH expression for a partial token."""
    tokens = _FTS_TOKEN_RE.findall(prefix or "")
    if not tokens:
        return ""
    parts = [f"{t}*" for t in tokens]
    return " ".join(parts)


def _symbol_rows_with_like_rescue(conn: Any, prefix: str, limit: int) -> list[Any]:
    """Prefer FTS ranking while preserving completions on degraded indexes."""
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
        except sqlite3.Error:
            # FTS5 table missing / malformed query - fall through to
            # the LIKE fallback. Programmer errors propagate per W531.
            rows = []
    if rows:
        return rows
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
        return cur.fetchall()
    except sqlite3.Error:
        # symbols table missing on a corrupt / pre-init DB.
        # Programmer errors propagate per W531.
        return []


def _unique_symbol_names_preserving_rank(rows: list[Any], limit: int) -> list[str]:
    """Bound duplicate-prone SQL rows without disturbing ranking."""
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


def complete_symbols(prefix: str, *, limit: int = _MAX_RESULTS, root: str | None = None) -> list[str]:
    """Return up to *limit* symbol names matching ``prefix``."""
    if not prefix:
        return []
    with _open_index(root) as conn:
        if conn is None:
            return []
        rows = _symbol_rows_with_like_rescue(conn, prefix, limit)
        return _unique_symbol_names_preserving_rank(rows, limit)


def complete_paths(prefix: str, *, limit: int = _MAX_RESULTS, root: str | None = None) -> list[str]:
    """Return up to *limit* indexed file paths starting with ``prefix``."""
    with _open_index(root) as conn:
        if conn is None:
            return []
        like = (prefix or "") + "%"
        try:
            cur = conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ORDER BY path LIMIT ?",
                (like, limit),
            )
            return [row[0] for row in cur.fetchall() if row and row[0]]
        except sqlite3.Error:
            return []


def complete_commands(prefix: str, *, limit: int = _MAX_RESULTS) -> list[str]:
    """Return roam CLI command names matching ``prefix``."""
    p = (prefix or "").lower()
    out = [name for name in _registered_command_names() if name.lower().startswith(p)]
    return out[:limit]


def complete_prefix(
    prefix: str,
    *,
    kind: str = "symbol",
    limit: int = _MAX_RESULTS,
    root: str | None = None,
) -> dict[str, list[str]]:
    """Aggregate prefix completions for one ``kind`` into a single dict.

    Library-level public entry point, deliberately retained despite
    having no in-tree production callers: embedders and MCP clients
    without protocol-level completion support get one stable call
    covering symbols/paths/commands. NOT wired into the
    ``roam_complete`` MCP tool — since the W3.1 parity fix that
    wrapper uses the CLI's strict prefix matcher for symbols, whereas
    this aggregator keeps :func:`complete_symbols`'s FTS5 semantics.
    """
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


def _completion_values_for_decoupled_protocol_target(
    ref: Any,
    value: str,
    arg_name: str,
    *,
    prompt_reference_type: Any,
    resource_template_reference_type: Any,
) -> list[str]:
    """Keep protocol routing independent from MCP handler registration."""
    if arg_name in _SYMBOL_ARG_NAMES:
        return complete_prefix(value, kind="symbol").get("symbols", [])
    if arg_name in _PATH_ARG_NAMES:
        return complete_prefix(value, kind="path").get("paths", [])
    if arg_name in _COMMAND_ARG_NAMES:
        return complete_prefix(value, kind="command").get("commands", [])
    if isinstance(ref, resource_template_reference_type):
        uri = getattr(ref, "uri", "") or ""
        if "symbol" in uri:
            return complete_prefix(value, kind="symbol").get("symbols", [])
        if "file" in uri or "path" in uri:
            return complete_prefix(value, kind="path").get("paths", [])
    if isinstance(ref, prompt_reference_type):
        # Best-effort fallback for unknown prompt args.
        return complete_prefix(value, kind="symbol").get("symbols", [])[:10]
    return []


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
    handlers = getattr(low_level, "request_handlers", None)
    if not isinstance(handlers, dict):
        return False

    try:
        from mcp.types import (
            CompleteRequest,
            CompleteResult,
            Completion,
            PromptReference,
            ResourceTemplateReference,
            ServerResult,
        )
    except ImportError:
        return False

    from roam.db.freshness import capture_direct_index_reads
    from roam.output.formatter import stamp_query_index_freshness

    async def _complete(request: CompleteRequest) -> ServerResult:
        argument = request.params.argument
        with capture_direct_index_reads() as observations:
            values = _completion_values_for_decoupled_protocol_target(
                request.params.ref,
                (argument.value or "").strip(),
                (argument.name or "").lower(),
                prompt_reference_type=PromptReference,
                resource_template_reference_type=ResourceTemplateReference,
            )
        envelope: dict = {}
        for freshness in observations.values():
            stamp_query_index_freshness(envelope, freshness)

        return ServerResult(
            CompleteResult(
                completion=Completion(
                    values=values[:_MAX_RESULTS],
                    total=len(values),
                    hasMore=len(values) > _MAX_RESULTS,
                ),
                _meta=envelope.get("_meta"),
            )
        )

    # The SDK decorator accepts only Completion, so register a full result
    # handler to include freshness in the protocol result metadata.
    handlers[CompleteRequest] = _complete
    return True
