"""``roam api`` — list the public API surface (exported symbols + signatures).

useful for changelog generation and breaking-change detection.
A symbol is "public" when:

  * its name doesn't start with ``_`` (Python convention)
  * its file is not under ``tests/`` or has ``file_role='test'``
  * its kind is one of {function, method, class, interface, enum}

Output is sorted by file then line so it's stable across runs.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because api outputs are invocation-scoped public API surface
listings — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

_PUBLIC_KINDS = ("function", "method", "class", "interface", "enum")


@roam_capability(
    name="api",
    category="workflow",
    summary="List the public API surface (exported public symbols)",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("--limit", type=int, default=0, show_default=True, help="Cap output (0=all).")
@click.option(
    "--scope",
    type=str,
    default=None,
    help="Restrict to symbols in files under this path prefix.",
)
@click.pass_context
def api(ctx, limit, scope) -> None:
    """List the public API surface (exported public symbols)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    placeholders = ",".join("?" for _ in _PUBLIC_KINDS)
    where = (
        "s.name NOT LIKE '\\_%' ESCAPE '\\' "
        f"AND s.kind IN ({placeholders}) "
        "AND COALESCE(f.file_role, 'source') NOT IN ('test', 'tests') "
        "AND f.path NOT LIKE 'tests/%'"
    )
    params = list(_PUBLIC_KINDS)
    if scope:
        normalised = scope.replace("\\", "/").rstrip("/") + "/"
        where += " AND f.path LIKE ?"
        params.append(f"{normalised}%")

    sql = (
        "SELECT s.name, s.kind, s.qualified_name, s.signature, "
        "       s.docstring, f.path, s.line_start "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        f"WHERE {where} "
        "ORDER BY f.path, s.line_start"
    )

    with open_db(readonly=True) as conn:
        rows = conn.execute(sql, params).fetchall()

    if limit and limit > 0:
        rows = rows[:limit]

    items = []
    for r in rows:
        doc = (r["docstring"] or "").strip().splitlines()
        first_line = doc[0] if doc else ""
        items.append(
            {
                "name": r["name"],
                "qualified_name": r["qualified_name"],
                "kind": r["kind"],
                "signature": (r["signature"] or "").strip(),
                "first_doc_line": first_line,
                "file": r["path"],
                "line": r["line_start"],
            }
        )

    verdict = f"{len(items)} public symbol(s) in API surface"

    if json_mode:
        # W17.2 / Pattern 3c: name the inclusion criterion so consumers
        # know which subset of "public symbols" they are looking at.
        # `api` reports the syntactic (no-underscore) subset; the
        # semantic (export-marker) subset is what `docs-coverage` reports.
        from roam.quality.public_symbols import (
            CRITERION_NO_UNDERSCORE,
        )
        from roam.quality.public_symbols import (
            definition as _ps_def,
        )

        click.echo(
            to_json(
                json_envelope(
                    "api",
                    summary={
                        "verdict": verdict,
                        "count": len(items),
                        "public_symbols_inclusion_criterion": CRITERION_NO_UNDERSCORE,
                        "public_symbols_definition": _ps_def(),
                    },
                    api=items,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not items:
        return
    click.echo()
    last_file = None
    for it in items:
        if it["file"] != last_file:
            click.echo()
            click.echo(f"### {it['file']}")
            last_file = it["file"]
        sig = it["signature"] or it["name"]
        click.echo(f"  {it['kind']:<10}  {sig[:90]}  ({it['file']}:{it['line']})")
