"""Boolean-oracle command group — fast yes/no answers over the indexed graph.

Designed for agents: each subcommand returns a single boolean (plus a short
reason) so the agent's prompt stays tight. Direct counter to CKB v9.2's
``symbolExists`` pattern. Five oracles ship in v12.1:

* ``symbol-exists <name>``        — does any symbol with that name/qname exist?
* ``route-exists <path>``         — does any HTTP route match this path?
* ``is-test-only <name>``         — does the symbol have ANY non-test callers?
* ``is-reachable-from-entry <name>`` — can a graph BFS hit this symbol from an entry-point?
* ``is-clone-of <name>``          — does the symbol participate in a persisted clone cluster?

Text output is ``VERDICT: true|false — <reason>``; JSON output uses
``json_envelope`` with ``summary.value`` (the boolean) plus ``summary.reason``.
"""

from __future__ import annotations

import sqlite3

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Pure oracle implementations — each returns (value: bool, reason: str)
# These are split out from the Click decorators so MCP wrappers can call
# them directly without round-tripping through the CLI.
# ---------------------------------------------------------------------------


def oracle_symbol_exists(conn: sqlite3.Connection, name: str) -> tuple[bool, str]:
    """Does any symbol with this name OR qualified_name exist?

    Match is exact on ``name`` OR exact on ``qualified_name`` OR suffix on
    ``qualified_name`` ending with ``.name`` (so ``UserSession.refresh``
    finds methods qualified as ``module.UserSession.refresh``).
    """
    if not name:
        return False, "empty query"
    row = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ?",
        (name, name, f"%.{name}"),
    ).fetchone()
    count = int(row[0]) if row else 0
    if count == 0:
        return False, f"no symbol matching '{name}'"
    return True, f"{count} symbol(s) match '{name}'"


def oracle_route_exists(conn: sqlite3.Connection, path: str) -> tuple[bool, str]:
    """Does any HTTP route definition match the given URL path?

    Reads the persisted ``cross_repo_edges`` table when available (populated
    by ``roam ws resolve``), falling back to a scan over symbol names that
    look like route handlers (``app.get/post/...``, ``Route::get/...``,
    ``@app.get(...)``) when the workspace tables aren't present.
    """
    if not path:
        return False, "empty path"
    if not path.startswith("/"):
        path = "/" + path
    # First try cross_repo_edges (workspace mode).
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM cross_repo_edges WHERE url_pattern = ? OR url_pattern LIKE ?",
            (path, f"{path}%"),
        ).fetchone()
        if row and int(row[0]) > 0:
            return True, f"matched {int(row[0])} cross-repo route(s)"
    except sqlite3.OperationalError:
        pass  # workspace tables not present
    # Fallback: scan symbols for route-handler-shaped names. We can't fully
    # reconstruct the URL pattern from the symbol graph alone, so we widen
    # to *any* route-shaped symbol and let the agent narrow down.
    row = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE LOWER(name) IN ('get','post','put','delete','patch') "
        "  AND kind IN ('method','function')"
    ).fetchone()
    count = int(row[0]) if row else 0
    if count == 0:
        return False, "no route-handler symbols indexed; try `roam ws resolve` first"
    return False, f"{count} route handler(s) found, but URL match needs `roam ws resolve`"


def oracle_is_test_only(conn: sqlite3.Connection, name: str) -> tuple[bool, str]:
    """Is this symbol used only by test code?

    Resolution order (matches dogfood expectations 2026-05-01):

    1. **Symbol lives in a test file** → ``True``. The previous heuristic
       was "all callers in test files", but a test method like
       ``test_check_count`` has zero callers (pytest invokes it by
       reflection), and the heuristic returned ``False`` for the most
       canonically test-only thing in the codebase. We now check the
       symbol's *own* ``file_role`` first — anything in
       ``file_role='test'`` is test-only by definition.
    2. **Symbol is in a non-test file with all callers in test files**
       → ``True``. Catches helpers that live in production but are
       only ever called from tests (genuinely test-only).
    3. **Symbol has no callers and is in a non-test file** → ``False``
       with a clearer "orphan" reason than before.
    """
    if not name:
        return False, "empty query"
    target_rows = conn.execute(
        """
        SELECT s.id, COALESCE(f.file_role, 'unknown') AS role
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name = ? OR s.qualified_name = ?
        """,
        (name, name),
    ).fetchall()
    if not target_rows:
        return False, f"no symbol named '{name}'"

    # 1) Symbol's own file_role is 'test' → trivially test-only.
    own_test = [r for r in target_rows if r[1] == "test"]
    if own_test and len(own_test) == len(target_rows):
        return True, "symbol lives in a test file (file_role='test')"
    # Mixed (e.g. same name in test + source) — fall through to caller
    # analysis so the answer reflects actual usage.

    target_ids = [int(r[0]) for r in target_rows]
    placeholders = ",".join("?" * len(target_ids))
    caller_rows = conn.execute(
        f"SELECT s.id, COALESCE(f.file_role, 'unknown') AS role "
        f"FROM edges e "
        f"JOIN symbols s ON s.id = e.source_id "
        f"JOIN files f ON f.id = s.file_id "
        f"WHERE e.target_id IN ({placeholders}) AND e.kind IN ('call', 'reference', 'calls', 'references')",
        target_ids,
    ).fetchall()
    if not caller_rows:
        # 3) No callers AND no callees lives in a test file ⇒ orphan in source.
        return False, f"no callers found for '{name}' (orphan in source)"
    test_count = sum(1 for r in caller_rows if r[1] == "test")
    total = len(caller_rows)
    if test_count == total:
        return True, f"all {total} caller(s) live in test files"
    return False, f"{total - test_count}/{total} caller(s) are non-test"


def oracle_is_reachable_from_entry(conn: sqlite3.Connection, name: str, *, max_hops: int = 10) -> tuple[bool, str]:
    """Is there a path from ANY entry-point symbol to this target?

    Entry points = symbols in files with ``file_role = 'entry'`` OR
    ``is_entry = 1``. Uses BFS over ``edges.kind IN ('calls', 'references')``.

    ``max_hops`` is clamped to ``[1, 1000]`` — values below 1 produce
    confusing "unreachable within -5 hops" messages, and values above
    1000 risk pathological BFS on degenerate graphs (cycles are guarded
    by the visited set, so the cap is just a safety belt).
    """
    if not name:
        return False, "empty query"
    max_hops = max(1, min(1000, int(max_hops)))
    target_rows = conn.execute(
        "SELECT id FROM symbols WHERE name = ? OR qualified_name = ?",
        (name, name),
    ).fetchall()
    if not target_rows:
        return False, f"no symbol named '{name}'"
    target_ids = {int(r[0]) for r in target_rows}

    # Entry-point definition (redacted, two iterations):
    #
    # First attempt — files with no incoming ``file_edges``. Caught the
    # previous bug (``no entry-point symbols indexed`` on every query)
    # but mis-classified ``cli`` itself: ``src/roam/cli.py`` IS imported
    # by tests + internal helpers so it had incoming edges and wasn't
    # an entry point. The user expects ``is-reachable cli`` to be True.
    #
    # Second attempt — same import-graph definition PLUS name-based
    # fallback for canonical entry symbols (``cli``, ``main``,
    # ``__main__``, ``run``, ``app``, ``serve``). Anything with one of
    # these names IS treated as an entry point regardless of whether
    # someone imported its file. Catches the "main is the entry point
    # but tests import it" case without false-positives elsewhere.
    try:
        entry_rows = conn.execute(
            """
            SELECT s.id FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE f.id NOT IN (SELECT DISTINCT target_file_id FROM file_edges)
            """
        ).fetchall()
        entry_ids = {int(r[0]) for r in entry_rows}
    except sqlite3.OperationalError:
        entry_ids = set()

    try:
        named_entry_rows = conn.execute(
            "SELECT id FROM symbols WHERE name IN ('cli', 'main', '__main__', 'run', 'app', 'serve', 'entrypoint')"
        ).fetchall()
        entry_ids.update(int(r[0]) for r in named_entry_rows)
    except sqlite3.OperationalError:
        pass

    if not entry_ids:
        return False, "no entry-point symbols indexed (run `roam init` to (re)build the index)"

    if entry_ids & target_ids:
        return True, "target is itself an entry point"

    visited: set[int] = set(entry_ids)
    frontier = list(entry_ids)
    hop = 0
    while frontier and hop < max_hops:
        next_frontier: list[int] = []
        for chunk_start in range(0, len(frontier), 400):
            chunk = frontier[chunk_start : chunk_start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT target_id FROM edges "
                f"WHERE source_id IN ({placeholders}) "
                f"  AND kind IN ('call', 'reference', 'calls', 'references')",
                chunk,
            ).fetchall()
            for r in rows:
                tid = int(r[0])
                if tid in target_ids:
                    return True, f"reachable in {hop + 1} hop(s)"
                if tid not in visited:
                    visited.add(tid)
                    next_frontier.append(tid)
        frontier = next_frontier
        hop += 1
    return False, f"unreachable from {len(entry_ids)} entry point(s) within {max_hops} hops"


def oracle_is_clone_of(conn: sqlite3.Connection, name: str) -> tuple[bool, str]:
    """Does this symbol participate in a persisted clone cluster?

    Reads ``clone_pairs`` (populated by ``roam clones --persist``). The
    schema stores qualified names as ``qname_a`` / ``qname_b`` — match on
    suffix so a bare symbol name like ``handle_login`` finds qualified
    entries like ``auth.handle_login``.

    Returns ``False`` with a hint when the table is empty or absent.
    """
    if not name:
        return False, "empty query"
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM clone_pairs WHERE qname_a = ? OR qname_b = ?   OR qname_a LIKE ? OR qname_b LIKE ?",
            (name, name, f"%.{name}", f"%.{name}"),
        ).fetchone()
    except sqlite3.OperationalError:
        return False, "clone tables not present; run `roam clones --persist` first"
    count = int(row[0]) if row else 0
    if count == 0:
        return False, f"no clone siblings for '{name}'"
    return True, f"{count} persisted clone pair(s) for '{name}'"


# ---------------------------------------------------------------------------
# Click surface
# ---------------------------------------------------------------------------


def _emit(ctx, oracle_name: str, value: bool, reason: str, **extra) -> None:
    """Render the verdict in the requested mode (text or JSON envelope).

    ``oracle_name`` is the kebab-case slug of the oracle (e.g.
    ``"symbol-exists"``) used in the envelope's ``command`` field. The
    ``**extra`` dict carries the original CLI args (``name``, ``path``,
    ``max_hops``) into the JSON output for round-trip fidelity.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    verdict = "true" if value else "false"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    f"oracle:{oracle_name}",
                    summary={"verdict": verdict, "value": value, "reason": reason},
                    **extra,
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict} — {reason}")


@click.group(name="oracle", help="Boolean oracles — quick yes/no answers for agents.")
def oracle() -> None:
    """Container for the five v12.1 boolean oracles."""


@oracle.command("symbol-exists")
@click.argument("name")
@click.pass_context
def symbol_exists_cmd(ctx, name: str) -> None:
    """Does a symbol with this name exist?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        value, reason = oracle_symbol_exists(conn, name)
    _emit(ctx, "symbol-exists", value, reason, name=name)


@oracle.command("route-exists")
@click.argument("path")
@click.pass_context
def route_exists_cmd(ctx, path: str) -> None:
    """Does a route handler match this URL path?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        value, reason = oracle_route_exists(conn, path)
    _emit(ctx, "route-exists", value, reason, path=path)


@oracle.command("is-test-only")
@click.argument("name")
@click.pass_context
def is_test_only_cmd(ctx, name: str) -> None:
    """Are all callers of this symbol in test files?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        value, reason = oracle_is_test_only(conn, name)
    _emit(ctx, "is-test-only", value, reason, name=name)


@oracle.command("is-reachable-from-entry")
@click.argument("name")
@click.option("--max-hops", type=int, default=10, help="BFS depth cap (default 10).")
@click.pass_context
def is_reachable_cmd(ctx, name: str, max_hops: int) -> None:
    """Is the symbol reachable from any entry point via the call graph?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        value, reason = oracle_is_reachable_from_entry(conn, name, max_hops=max_hops)
    _emit(ctx, "is-reachable-from-entry", value, reason, name=name, max_hops=max_hops)


@oracle.command("is-clone-of")
@click.argument("name")
@click.pass_context
def is_clone_of_cmd(ctx, name: str) -> None:
    """Does this symbol have persisted clone siblings?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        value, reason = oracle_is_clone_of(conn, name)
    _emit(ctx, "is-clone-of", value, reason, name=name)
