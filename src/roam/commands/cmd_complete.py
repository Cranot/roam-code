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

import logging

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.output.formatter import json_envelope, to_json

log = logging.getLogger(__name__)

_VALID_KINDS = ("symbol", "path", "command", "all")
_DEFAULT_LIMIT = 30


def _prefix_symbols(prefix: str, *, limit: int, warnings_out: list[str] | None = None) -> list[str]:
    """Return symbol names that LITERALLY start with ``prefix``.

    Uses ``LIKE prefix%`` directly against ``symbols.name`` so camelCase
    tokenization (which the FTS5 indexer applies — ``MyUseFoo`` ->
    ``My Use Foo``) cannot widen the match. This is the contract the
    ``prefix`` argument promises.

    W607-F: when ``warnings_out`` is threaded in, the silent SQL
    fallback (``except Exception``) appends a structured
    ``complete_symbols_query_failed:<exc_class>:<detail>`` marker
    instead of dropping the substrate failure on the floor. Mirrors
    cmd_search W607-E ``_get_explain_data`` inner disclosure shape.
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
    except Exception as exc:  # noqa: BLE001 — W607-F inner-bucket disclosure
        if warnings_out is not None:
            warnings_out.append(f"complete_symbols_query_failed:{type(exc).__name__}:{exc}")
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
    index_ready = True
    try:
        ensure_index()
    except Exception as err:
        # Index failures shouldn't abort completion — agents call this
        # speculatively during typing; emit a partial-success envelope
        # rather than a fatal error. CP45/CP46 fail-loud: log the
        # underlying error so the lineage is observable instead of an
        # invisible swallow.
        log.warning("complete: ensure_index failed (%s); returning best-effort results", err)
        index_ready = False

    # We deliberately do NOT go through ``mcp_extras.completions``'s
    # FTS5 path for ``kind == 'symbol'``. The FTS5 indexer expands
    # camelCase identifiers at insert time (``MyUseFoo`` -> ``My Use
    # Foo``), so ``use*`` would match ``MyUseFoo`` even though the
    # symbol name doesn't start with "use". That's the exact bug this
    # command was fixing. Use a strict LIKE-based prefix matcher on
    # the raw ``symbols.name`` column instead — left-anchored, no
    # tokenization, byte-equivalent to what the user typed.
    from roam.mcp_extras.completions import complete_commands, complete_paths

    # W607-F: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the completion pipeline. cmd_complete does NOT
    # call the W605-plumbed substrate directly (search_fts /
    # fts5_available / fts5_populated / search_stored): it issues raw
    # SQL through ``_prefix_symbols`` + delegates to read-only helpers
    # in ``mcp_extras.completions`` (complete_paths / complete_commands)
    # which themselves wrap silent ``except Exception`` fallbacks. The
    # disclosure shape therefore mirrors cmd_search W607-E outer-guard
    # idioms but with three distinct sub-markers, one per kind bucket:
    #   * symbol pipeline raise (in _prefix_symbols) → threaded
    #     ``complete_symbols_query_failed:<exc>:<detail>`` (inner)
    #   * paths helper raise → ``complete_paths_query_failed:<exc>:<detail>``
    #   * commands helper raise → ``complete_commands_query_failed:<exc>:<detail>``
    # Marker family is ``complete_*`` (NOT ``search_*`` / ``semantic_*``)
    # — cmd_complete is the LEXICAL-PREFIX layer, distinct from the
    # substring (cmd_search) and semantic (cmd_search_semantic) scopes.
    # Empty bucket → byte-identical envelope (hash-stable). Non-empty
    # bucket → summary.warnings_out + summary.partial_success=True +
    # top-level mirror.
    warnings_out: list[str] = []

    payload: dict[str, list[str]] = {}
    if kind_norm in ("symbol", "all"):
        payload["symbols"] = _prefix_symbols(prefix, limit=limit, warnings_out=warnings_out)
    if kind_norm in ("path", "all"):
        try:
            payload["paths"] = complete_paths(prefix, limit=limit)
        except Exception as exc:  # noqa: BLE001 — W607-F outer-guard
            warnings_out.append(f"complete_paths_query_failed:{type(exc).__name__}:{exc}")
            payload["paths"] = []
    if kind_norm in ("command", "all"):
        try:
            payload["commands"] = complete_commands(prefix, limit=limit)
        except Exception as exc:  # noqa: BLE001 — W607-F outer-guard
            warnings_out.append(f"complete_commands_query_failed:{type(exc).__name__}:{exc}")
            payload["commands"] = []

    # Total count across all kinds (for ``--kind all``).
    total = sum(len(v) for v in payload.values())
    verdict = f"{total} prefix completions for '{prefix}' (kind={kind_norm})"
    # ``partial_success`` required on every envelope. ``complete`` is
    # signal-producing only when at least one match comes back; empty
    # results are partial since the prefix did not pin anything. W607-F:
    # any substrate marker also flips ``partial_success`` to True so the
    # agent can distinguish "valid prefix, 0 hits" from "completion
    # pipeline degraded".
    partial = total == 0 or bool(warnings_out)

    if json_mode:
        _complete_summary: dict = {
            "verdict": verdict,
            "prefix": prefix,
            "kind": kind_norm,
            "total": total,
            "match_mode": "prefix",
            "partial_success": partial,
            "index_ready": index_ready,
        }
        # W607-F disclosure: non-empty bucket → summary mirror + top-level
        # mirror. Empty bucket → byte-identical envelope (hash-stable on
        # clean happy path).
        if warnings_out:
            _complete_summary["warnings_out"] = list(warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "complete",
                    summary=_complete_summary,
                    prefix=prefix,
                    kind=kind_norm,
                    results=payload,
                    **({"warnings_out": list(warnings_out)} if warnings_out else {}),
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
