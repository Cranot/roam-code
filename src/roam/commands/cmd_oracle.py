"""Boolean-oracle command group — fast yes/no answers over the indexed graph.

Designed for agents: each subcommand returns a single boolean (plus a short
reason) so the agent's prompt stays tight. Direct counter to CKB v9.2's
``symbolExists`` pattern. Five oracles ship in v12.1:

* ``symbol-exists <name>``        — does any symbol with that name/qname exist?
* ``route-exists <path>``         — does any HTTP route match this path?
* ``is-test-only <name>``         — does the symbol have ANY non-test callers?
* ``is-reachable-from-entry <name>`` — can a graph BFS hit this symbol from an entry-point?
* ``is-clone-of <name>``          — does the symbol participate in a persisted clone cluster?

Text output is ``VERDICT: true|false|indeterminate — <reason>``. JSON
output uses ``json_envelope`` with ``summary.value`` (``true|false|null``),
``summary.reason``, ``summary.reason_class``, and ``summary.confidence``.
The tri-state shape lets agents distinguish "we proved no" from "we
can't tell" — a distinction that flat booleans were collapsing
redacted, #7, J).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


@dataclass(frozen=True)
class OracleResult:
    """One oracle answer. Carries enough metadata for an agent to reason.

    Unpacks as a ``(value, reason)`` tuple for backwards compatibility
    with callers that pre-date the tri-state envelope. New consumers
    should read ``result.reason_class`` and ``result.confidence``.

    ``value``:
        ``True`` / ``False`` when the answer is provable from the index;
        ``None`` when we lack data to decide (workspace not configured,
        clone table absent, etc).

    ``reason_class``:
        Short tag for downstream branching. Documented values:

        * ``definitive_yes`` / ``definitive_no``
        * ``indeterminate_workspace`` (route oracle without ``roam ws resolve``)
        * ``indeterminate_no_data`` (clone table absent, etc)
        * ``unreachable_dead`` (genuinely orphaned)
        * ``unreachable_scaffolding`` (docstring cites legacy/spec)
        * ``unreachable_test_only`` (lives in a test file)
        * ``unreachable_dynamic_import`` (target of dynamic import only)

    ``confidence``: ``high`` | ``medium`` | ``low`` | ``indeterminate``.
    """

    value: bool | None
    reason: str
    reason_class: str = "definitive_yes"
    confidence: str = "high"

    def __iter__(self):
        # Tuple-unpack as (value, reason) for backwards compatibility.
        yield self.value
        yield self.reason


def _scaffolding_evidence(conn: sqlite3.Connection, name: str) -> str | None:
    """Return a human-readable evidence string when the symbol's docstring
    matches the scaffolding heuristic from round 2 #4. Used by the
    reachability oracle to distinguish dead code from preserved
    reference code.
    """
    rows = conn.execute(
        "SELECT s.docstring FROM symbols s WHERE s.name = ? OR s.qualified_name = ?",
        (name, name),
    ).fetchall()
    try:
        from roam.commands.cmd_dead import _scaffolding_signals  # noqa: PLC0415 — lazy to avoid circular import
    except Exception:
        return None
    for r in rows:
        evidence = _scaffolding_signals(r[0] if not hasattr(r, "keys") else r["docstring"])
        if evidence:
            tags = []
            if evidence.get("behaviour_ids"):
                tags.append(f"ids={','.join(evidence['behaviour_ids'])}")
            if evidence.get("legacy_files"):
                tags.append(f"legacy={','.join(evidence['legacy_files'])}")
            if evidence.get("see_legacy"):
                tags.append("see_legacy")
            return ", ".join(tags) or "scaffolding"
    return None


# ---------------------------------------------------------------------------
# Pure oracle implementations — each returns (value: bool, reason: str)
# These are split out from the Click decorators so MCP wrappers can call
# them directly without round-tripping through the CLI.
# ---------------------------------------------------------------------------


def oracle_symbol_exists(conn: sqlite3.Connection, name: str) -> OracleResult:
    """Does any symbol with this name OR qualified_name exist?

    Match is exact on ``name`` OR exact on ``qualified_name`` OR suffix on
    ``qualified_name`` ending with ``.name`` (so ``UserSession.refresh``
    finds methods qualified as ``module.UserSession.refresh``).
    """
    if not name:
        return OracleResult(False, "empty query", "definitive_no", "high")
    row = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ?",
        (name, name, f"%.{name}"),
    ).fetchone()
    count = int(row[0]) if row else 0
    if count == 0:
        return OracleResult(False, f"no symbol matching '{name}'", "definitive_no", "high")
    return OracleResult(True, f"{count} symbol(s) match '{name}'", "definitive_yes", "high")


def oracle_route_exists(conn: sqlite3.Connection, path: str) -> OracleResult:
    """Does any HTTP route definition match the given URL path?

    Reads the persisted ``cross_repo_edges`` table when available
    (populated by ``roam ws resolve``), and returns
    ``value=None / reason_class='indeterminate_workspace'`` when route
    handlers are present but URL matching needs the workspace bridge.
    Round 4 #7 noted that returning ``False`` here was misleading —
    the route may exist; we just can't verify from this index alone.
    """
    if not path:
        return OracleResult(False, "empty path", "definitive_no", "high")
    if not path.startswith("/"):
        path = "/" + path
    # First try cross_repo_edges (workspace mode).
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM cross_repo_edges WHERE url_pattern = ? OR url_pattern LIKE ?",
            (path, f"{path}%"),
        ).fetchone()
        if row and int(row[0]) > 0:
            return OracleResult(
                True,
                f"matched {int(row[0])} cross-repo route(s)",
                "definitive_yes",
                "high",
            )
    except sqlite3.OperationalError:
        pass  # workspace tables not present
    # Fallback: scan symbols for route-handler-shaped names. We can't fully
    # reconstruct the URL pattern from the symbol graph alone.
    row = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE LOWER(name) IN ('get','post','put','delete','patch') "
        "  AND kind IN ('method','function')"
    ).fetchone()
    count = int(row[0]) if row else 0
    if count == 0:
        return OracleResult(
            False,
            "no route-handler symbols indexed; try `roam ws resolve` first",
            "indeterminate_no_data",
            "low",
        )
    return OracleResult(
        None,
        f"{count} route handler(s) found, but URL match needs `roam ws resolve`",
        "indeterminate_workspace",
        "indeterminate",
    )


def oracle_is_test_only(conn: sqlite3.Connection, name: str) -> OracleResult:
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
        return OracleResult(False, "empty query", "definitive_no", "high")
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
        return OracleResult(False, f"no symbol named '{name}'", "definitive_no", "high")

    # 1) Symbol's own file_role is 'test' → trivially test-only.
    own_test = [r for r in target_rows if r[1] == "test"]
    if own_test and len(own_test) == len(target_rows):
        return OracleResult(
            True,
            "symbol lives in a test file (file_role='test')",
            "unreachable_test_only",
            "high",
        )
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
        # No callers — the symbol is orphan in source. We can't say it's
        # test-only because no test calls it either; surface
        # indeterminate so agents don't conclude "delete it" from this
        # oracle alone.
        return OracleResult(
            None,
            f"no callers found for '{name}' (orphan in source)",
            "indeterminate_no_data",
            "low",
        )
    test_count = sum(1 for r in caller_rows if r[1] == "test")
    total = len(caller_rows)
    if test_count == total:
        return OracleResult(
            True,
            f"all {total} caller(s) live in test files",
            "unreachable_test_only",
            "high",
        )
    return OracleResult(
        False,
        f"{total - test_count}/{total} caller(s) are non-test",
        "definitive_no",
        "high",
    )


def oracle_is_reachable_from_entry(conn: sqlite3.Connection, name: str, *, max_hops: int = 10) -> OracleResult:
    """Is there a path from ANY entry-point symbol to this target?

    Entry points = symbols in files with ``file_role = 'entry'`` OR
    ``is_entry = 1``. Uses BFS over ``edges.kind IN ('calls', 'references')``.

    ``max_hops`` is clamped to ``[1, 1000]`` — values below 1 produce
    confusing "unreachable within -5 hops" messages, and values above
    1000 risk pathological BFS on degenerate graphs (cycles are guarded
    by the visited set, so the cap is just a safety belt).
    """
    if not name:
        return OracleResult(False, "empty query", "definitive_no", "high")
    max_hops = max(1, min(1000, int(max_hops)))
    target_rows = conn.execute(
        "SELECT id FROM symbols WHERE name = ? OR qualified_name = ?",
        (name, name),
    ).fetchall()
    if not target_rows:
        return OracleResult(False, f"no symbol named '{name}'", "definitive_no", "high")
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
        return OracleResult(
            None,
            "no entry-point symbols indexed (run `roam init` to (re)build the index)",
            "indeterminate_no_data",
            "low",
        )

    if entry_ids & target_ids:
        return OracleResult(True, "target is itself an entry point", "definitive_yes", "high")

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
                    return OracleResult(
                        True,
                        f"reachable in {hop + 1} hop(s)",
                        "definitive_yes",
                        "high",
                    )
                if tid not in visited:
                    visited.add(tid)
                    next_frontier.append(tid)
        frontier = next_frontier
        hop += 1

    # Unreachable — try to classify why so the agent doesn't conflate
    # "scaffolding code I should keep" with "dead code I can delete".
    scaffolding = _scaffolding_evidence(conn, name)
    if scaffolding:
        return OracleResult(
            False,
            f"unreachable from {len(entry_ids)} entry point(s) within {max_hops} hops; scaffolding evidence ({scaffolding})",
            "unreachable_scaffolding",
            "medium",
        )
    return OracleResult(
        False,
        f"unreachable from {len(entry_ids)} entry point(s) within {max_hops} hops",
        "unreachable_dead",
        "high",
    )


def oracle_is_clone_of(conn: sqlite3.Connection, name: str) -> OracleResult:
    """Does this symbol participate in a persisted clone cluster?

    Reads ``clone_pairs`` (populated by ``roam clones --persist``). When
    the table is absent we return ``value=None`` — we genuinely don't
    know whether clones exist; the user just hasn't computed them.
    """
    if not name:
        return OracleResult(False, "empty query", "definitive_no", "high")
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM clone_pairs WHERE qname_a = ? OR qname_b = ?   OR qname_a LIKE ? OR qname_b LIKE ?",
            (name, name, f"%.{name}", f"%.{name}"),
        ).fetchone()
    except sqlite3.OperationalError:
        return OracleResult(
            None,
            "clone tables not present; run `roam clones --persist` first",
            "indeterminate_no_data",
            "indeterminate",
        )
    count = int(row[0]) if row else 0
    if count == 0:
        return OracleResult(False, f"no clone siblings for '{name}'", "definitive_no", "high")
    return OracleResult(
        True,
        f"{count} persisted clone pair(s) for '{name}'",
        "definitive_yes",
        "high",
    )


# ---------------------------------------------------------------------------
# Click surface
# ---------------------------------------------------------------------------


def _emit(ctx, oracle_name: str, result: OracleResult, **extra) -> None:
    """Render the tri-state verdict (true | false | indeterminate)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    if result.value is True:
        verdict = "true"
    elif result.value is False:
        verdict = "false"
    else:
        verdict = "indeterminate"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    f"oracle:{oracle_name}",
                    summary={
                        "verdict": verdict,
                        "value": result.value,
                        "reason": result.reason,
                        "reason_class": result.reason_class,
                        "confidence": result.confidence,
                    },
                    **extra,
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict} — {result.reason}")
    if result.reason_class and result.reason_class not in ("definitive_yes", "definitive_no"):
        click.echo(f"  reason_class: {result.reason_class}, confidence: {result.confidence}")


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
        result = oracle_symbol_exists(conn, name)
    _emit(ctx, "symbol-exists", result, name=name)


@oracle.command("route-exists")
@click.argument("path")
@click.pass_context
def route_exists_cmd(ctx, path: str) -> None:
    """Does a route handler match this URL path?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        result = oracle_route_exists(conn, path)
    _emit(ctx, "route-exists", result, path=path)


@oracle.command("is-test-only")
@click.argument("name")
@click.pass_context
def is_test_only_cmd(ctx, name: str) -> None:
    """Are all callers of this symbol in test files?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        result = oracle_is_test_only(conn, name)
    _emit(ctx, "is-test-only", result, name=name)


@oracle.command("is-reachable-from-entry")
@click.argument("name")
@click.option("--max-hops", type=int, default=10, help="BFS depth cap (default 10).")
@click.pass_context
def is_reachable_cmd(ctx, name: str, max_hops: int) -> None:
    """Is the symbol reachable from any entry point via the call graph?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        result = oracle_is_reachable_from_entry(conn, name, max_hops=max_hops)
    _emit(ctx, "is-reachable-from-entry", result, name=name, max_hops=max_hops)


@oracle.command("is-clone-of")
@click.argument("name")
@click.pass_context
def is_clone_of_cmd(ctx, name: str) -> None:
    """Does this symbol have persisted clone siblings?"""
    ensure_index()
    with open_db(readonly=True) as conn:
        result = oracle_is_clone_of(conn, name)
    _emit(ctx, "is-clone-of", result, name=name)
