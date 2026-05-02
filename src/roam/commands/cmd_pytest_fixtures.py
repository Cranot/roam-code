"""``roam pytest-fixtures`` — show the implicit fixture dependency chain
for a fixture or test, or for the whole project.

A pytest fixture's parameters are themselves fixtures. The relationship
is invisible to call-graph or import analysis, so a refactor that
renames a low-level fixture can break tests several files away with no
explicit edge to follow. Indexing materialises it as
``pytest_fixture_dep`` edges; this command surfaces them.
"""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json


def _fetch_chain(conn, symbol_id: int, max_depth: int = 6) -> list[dict]:
    """Walk ``pytest_fixture_dep`` edges out from ``symbol_id``.

    Returns a list of ``{depth, id, name, qualified_name, file_path,
    line_start, scope, autouse}`` dicts in BFS order (root first),
    capped at ``max_depth``.
    """
    from roam.index.pytest_fixtures import _fixture_autouse, _fixture_scope

    visited = {symbol_id}
    out: list[dict] = []
    frontier: list[tuple[int, int]] = [(symbol_id, 0)]
    while frontier:
        next_frontier: list[tuple[int, int]] = []
        for sid, depth in frontier:
            if depth >= max_depth:
                continue
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.qualified_name, s.line_start, s.decorators,
                       f.path AS file_path
                FROM edges e
                JOIN symbols s ON e.target_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE e.source_id = ? AND e.kind = 'pytest_fixture_dep'
                ORDER BY s.name
                """,
                (sid,),
            ).fetchall()
            for r in rows:
                if r["id"] in visited:
                    continue
                visited.add(r["id"])
                out.append(
                    {
                        "depth": depth + 1,
                        "id": r["id"],
                        "name": r["name"],
                        "qualified_name": r["qualified_name"],
                        "file_path": r["file_path"],
                        "line_start": r["line_start"],
                        "scope": _fixture_scope(r["decorators"]),
                        "autouse": _fixture_autouse(r["decorators"]),
                    }
                )
                next_frontier.append((r["id"], depth + 1))
        frontier = next_frontier
    return out


# Match the same fixture-decorator predicate as the resolver: require
# the ``@`` prefix so help-text mentions of ``pytest.fixture`` (e.g. in
# Click ``--help`` strings) don't masquerade as fixtures.
_FIXTURE_PREDICATE_SQL = "(s.decorators LIKE '%@pytest.fixture%' OR s.decorators LIKE '%@fixture%')"
_TEST_FILE_PREDICATE_SQL = "(f.file_role = 'test' OR f.path LIKE '%/conftest.py' OR f.path = 'conftest.py')"


def _project_summary(conn) -> dict:
    """Top-level counts when the user runs ``roam pytest-fixtures`` with
    no symbol argument."""
    total_fixtures = conn.execute(
        f"""
        SELECT COUNT(*) FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE f.language = 'python'
          AND s.kind IN ('function', 'method')
          AND {_FIXTURE_PREDICATE_SQL}
          AND {_TEST_FILE_PREDICATE_SQL}
        """
    ).fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'pytest_fixture_dep'").fetchone()[0]
    # Top fixtures by how many things depend on them — useful as a
    # blast-radius proxy for "if I change this fixture, what could
    # move".
    top_rows = conn.execute(
        """
        SELECT s.id, s.name, s.qualified_name, f.path AS file_path,
               COUNT(*) AS dependents
        FROM edges e
        JOIN symbols s ON e.target_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE e.kind = 'pytest_fixture_dep'
        GROUP BY e.target_id
        ORDER BY dependents DESC, s.name ASC
        LIMIT 15
        """
    ).fetchall()
    return {
        "total_fixtures": total_fixtures,
        "total_edges": total_edges,
        "top_fixtures": [dict(r) for r in top_rows],
    }


def _fetch_unused(conn) -> list[dict]:
    """Fixtures with zero ``pytest_fixture_dep`` dependents — i.e. dead
    test infrastructure. Pytest discovery still finds them at runtime
    (a future ``test_*`` could pick them up) so this is informational,
    not a hard error.
    """
    rows = conn.execute(
        f"""
        SELECT s.id, s.name, s.qualified_name, s.line_start, f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        LEFT JOIN edges e ON e.target_id = s.id AND e.kind = 'pytest_fixture_dep'
        WHERE f.language = 'python'
          AND s.kind IN ('function', 'method')
          AND {_FIXTURE_PREDICATE_SQL}
          AND {_TEST_FILE_PREDICATE_SQL}
          AND e.id IS NULL
        ORDER BY f.path, s.line_start
        """
    ).fetchall()
    return [dict(r) for r in rows]


@click.command("pytest-fixtures")
@click.argument("symbol", required=False)
@click.option(
    "--max-depth",
    default=6,
    show_default=True,
    type=int,
    help="Cap the dependency walk at this depth.",
)
@click.option(
    "--unused",
    is_flag=True,
    help="List fixtures with no dependents — orphaned test infrastructure.",
)
@click.pass_context
def pytest_fixtures(ctx, symbol: str | None, max_depth: int, unused: bool):
    """Show the pytest fixture chain for SYMBOL, or a project summary.

    With no SYMBOL, prints the project-wide fixture count and the top
    fixtures by dependent count — a blast-radius proxy. With a SYMBOL
    (fixture or ``test_*`` function), walks the
    ``pytest_fixture_dep`` edges out from it and prints the chain.

    With ``--unused``, lists fixtures that have no dependents — likely
    dead test infrastructure left behind by refactors. Pytest still
    discovers them at runtime, so this is informational.

    JSON output (with ``--json``) follows the standard envelope.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        if unused:
            rows = _fetch_unused(conn)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "pytest-fixtures",
                            summary={
                                "verdict": f"{len(rows)} unused fixture(s)",
                                "unused": len(rows),
                            },
                            unused=rows,
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {len(rows)} unused fixture(s)")
            if not rows:
                return
            click.echo()
            click.echo("Fixtures with no dependents:")
            for row in rows:
                line = f":{row['line_start']}" if row["line_start"] else ""
                click.echo(f"  {row['name']:<32} {row['file_path']}{line}")
            return

        if not symbol:
            summary = _project_summary(conn)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "pytest-fixtures",
                            summary={
                                "verdict": (
                                    f"{summary['total_fixtures']} fixture(s), "
                                    f"{summary['total_edges']} dependency edge(s)"
                                ),
                                "fixtures": summary["total_fixtures"],
                                "edges": summary["total_edges"],
                            },
                            top=summary["top_fixtures"],
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {summary['total_fixtures']} fixture(s), {summary['total_edges']} dependency edge(s)")
            if not summary["total_fixtures"]:
                click.echo()
                click.echo("  No pytest fixtures indexed.")
                click.echo("  If this project uses pytest, run: roam reindex")
                return
            if summary["top_fixtures"]:
                click.echo()
                click.echo("Top fixtures by dependent count:")
                for row in summary["top_fixtures"]:
                    click.echo(f"  {row['dependents']:>4}  {row['name']:<32} {row['file_path']}")
            return

        sym = find_symbol(conn, symbol)
        if not sym:
            click.echo(f"VERDICT: no symbol matched {symbol!r}", err=True)
            ctx.exit(1)
            return

        chain = _fetch_chain(conn, sym["id"], max_depth=max_depth)
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "pytest-fixtures",
                        summary={
                            "verdict": (
                                f"{sym['name']} depends on {len(chain)} fixture(s)"
                                if chain
                                else f"{sym['name']} has no fixture dependencies"
                            ),
                            "symbol": sym["name"],
                            "qualified_name": sym["qualified_name"],
                            "depth": max((r["depth"] for r in chain), default=0),
                            "count": len(chain),
                        },
                        chain=chain,
                    )
                )
            )
            return

        click.echo(
            f"VERDICT: {sym['name']} depends on {len(chain)} fixture(s)"
            if chain
            else f"VERDICT: {sym['name']} has no fixture dependencies"
        )
        if not chain:
            return
        click.echo()
        click.echo(f"Fixture chain for {sym['qualified_name'] or sym['name']}:")
        for row in chain:
            indent = "  " * row["depth"]
            line = f":{row['line_start']}" if row["line_start"] else ""
            badges = []
            if row.get("scope") and row["scope"] != "function":
                badges.append(f"scope={row['scope']}")
            if row.get("autouse"):
                badges.append("autouse")
            badge_str = f"  [{', '.join(badges)}]" if badges else ""
            click.echo(f"{indent}-> {row['name']:<28} {row['file_path']}{line}{badge_str}")
