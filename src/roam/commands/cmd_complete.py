"""Prefix-completion CLI surface for symbols / file paths / command names.

Wraps :mod:`roam.mcp_extras.completions` (which is also used by the MCP
protocol completion handler) so both surfaces share the same prefix
semantics. Critical contract: the ``prefix`` argument means **literal
left-anchored prefix match**, not substring or fuzzy. ``use`` matches
``useFoo`` and ``useBar`` but NOT ``MyUseFoo`` — the previous behaviour
mismatched that promise and returned substring hits.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because complete outputs are invocation-scoped prefix-match
enumerations — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json

_VALID_KINDS = ("symbol", "path", "command", "all")
_DEFAULT_LIMIT = 30


def _prefix_symbols(prefix: str, *, limit: int) -> list[str]:
    """Return symbol names that LITERALLY start with ``prefix``.

    Uses ``LIKE prefix%`` directly against ``symbols.name`` so camelCase
    tokenization (which the FTS5 indexer applies — ``MyUseFoo`` ->
    ``My Use Foo``) cannot widen the match. This is the contract the
    ``prefix`` argument promises.
    """
    if not prefix:
        return []
    from roam.db.connection import open_db

    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM symbols "
                "WHERE name LIKE ? ESCAPE '\\' "
                "ORDER BY length(name) ASC, name ASC "
                "LIMIT ?",
                (like, limit * 3),
            ).fetchall()
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        n = r["name"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) >= limit:
            break
    return out


@roam_capability(
    name="complete",
    category="exploration",
    summary="Left-anchored prefix completion for symbols, paths, or command names.",
    inputs=["prefix"],
    outputs=["completions", "verdict"],
    examples=["roam complete use", "roam complete src/ --kind path"],
    tags=["completion", "search"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command("complete")
@click.argument("prefix")
@click.option(
    "--kind",
    type=click.Choice(_VALID_KINDS, case_sensitive=False),
    default="symbol",
    show_default=True,
    help=(
        "What to complete: ``symbol`` (FTS5-backed symbol names), "
        "``path`` (indexed file paths), ``command`` (roam CLI command "
        "names), or ``all`` (combined)."
    ),
)
@click.option(
    "--limit",
    type=int,
    default=_DEFAULT_LIMIT,
    show_default=True,
    help="Maximum number of completions to return.",
)
@click.pass_context
def complete(ctx, prefix, kind, limit):
    """Return left-anchored prefix completions for the given partial.

    Prefix-only: ``use`` matches ``useFoo`` and ``useBar`` but NOT
    ``MyUseFoo``. Use ``roam search`` for substring matches and
    ``roam search-semantic`` for natural-language queries.

    \b
    Examples:
      roam complete use                      # symbol names starting with "use"
      roam complete src/ --kind path         # files starting with "src/"
      roam complete pr- --kind command       # CLI commands starting with "pr-"
      roam complete log --kind all --limit 5

    See also ``search`` (substring), ``search-semantic`` (natural
    language), and ``hover`` (symbol detail by exact name).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    kind_norm = (kind or "symbol").lower()
    limit = max(1, int(limit))

    # ``complete`` is only meaningful against an index, but it should
    # never crash on an un-indexed project — the underlying helpers
    # already return ``[]`` when the DB is absent. Still, run
    # ``ensure_index`` so the first call after ``git init`` does the
    # right thing rather than silently returning empty.
    try:
        ensure_index()
    except Exception:
        # Index failures shouldn't abort completion — agents call this
        # speculatively during typing; emit a partial-success envelope
        # rather than a fatal error.
        pass

    # We deliberately do NOT go through ``mcp_extras.completions``'s
    # FTS5 path for ``kind == 'symbol'``. The FTS5 indexer expands
    # camelCase identifiers at insert time (``MyUseFoo`` -> ``My Use
    # Foo``), so ``use*`` would match ``MyUseFoo`` even though the
    # symbol name doesn't start with "use". That's the exact bug this
    # command was fixing. Use a strict LIKE-based prefix matcher on
    # the raw ``symbols.name`` column instead — left-anchored, no
    # tokenization, byte-equivalent to what the user typed.
    from roam.mcp_extras.completions import complete_commands, complete_paths

    payload: dict[str, list[str]] = {}
    if kind_norm in ("symbol", "all"):
        payload["symbols"] = _prefix_symbols(prefix, limit=limit)
    if kind_norm in ("path", "all"):
        payload["paths"] = complete_paths(prefix, limit=limit)
    if kind_norm in ("command", "all"):
        payload["commands"] = complete_commands(prefix, limit=limit)

    # Total count across all kinds (for ``--kind all``).
    total = sum(len(v) for v in payload.values())
    verdict = f"{total} prefix completions for '{prefix}' (kind={kind_norm})"
    # ``partial_success`` required on every envelope. ``complete`` is
    # signal-producing only when at least one match comes back; empty
    # results are partial since the prefix did not pin anything.
    partial = total == 0

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "complete",
                    summary={
                        "verdict": verdict,
                        "prefix": prefix,
                        "kind": kind_norm,
                        "total": total,
                        "match_mode": "prefix",
                        "partial_success": partial,
                    },
                    prefix=prefix,
                    kind=kind_norm,
                    results=payload,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    for bucket_name, values in payload.items():
        if not values:
            continue
        click.echo(f"\n=== {bucket_name} ({len(values)}) ===")
        for v in values:
            click.echo(f"  {v}")
