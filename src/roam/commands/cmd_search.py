"""Find symbols matching a name substring (case-insensitive).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because search outputs are invocation-scoped symbol-name match
enumerations — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import batched_in, open_db
from roam.output.formatter import (
    KIND_ABBREV,
    abbrev_kind,
    format_signature,
    format_table,
    json_envelope,
    loc,
    to_json,
)
from roam.output.structured_unknowns import (
    structured_unknown_filter,
    to_summary_payload,
)

# FTS5 column layout for symbol_fts: name=0, qualified_name=1, signature=2, kind=3, file_path=4
_FTS_COLUMNS = ["name", "qualified_name", "signature", "kind", "file_path"]
# BM25 weights matching index_embeddings.py: name=10, qname=5, sig=2, kind=1, path=3
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"


# ---------------------------------------------------------------------------
# W607-BR substrate-CALL boundaries (ADDITIVE to W607-E outer-guard)
# ---------------------------------------------------------------------------
# Module-level helpers that delegate to the underlying search substrate.
# Tests monkeypatch THESE shims (not the substrate modules) so the W607-BR
# marker plumbing inside ``search`` can disclose substrate-CALL failures
# without colliding with the existing W607-E outer-guard
# (``search_pipeline_failed:``) or the W607-E inner ``_get_explain_data``
# fallbacks (``search_explain_<phase>_failed:``).
#
# Each shim accepts the same arguments as the underlying substrate call
# and returns the same result. A raise inside any shim becomes a
# ``search_<phase>_failed:<exc_class>:<detail>`` marker via the
# ``_run_check_br`` closure inside the click command body.
#
# cmd_search is the EXACT-MATCH sibling of cmd_search_semantic
# (W607-BO) and cmd_retrieve (W607-BI). Closes the SEARCH TRIO with
# distinct marker prefixes (``search_*`` vs ``search_semantic_*`` vs
# ``retrieve_*``) so a 3-way envelope inspection can demultiplex
# every consumer's substrate axis.


def _load_search_config():
    """W607-BR substrate-CALL: configuration load.

    cmd_search does not currently consume a dedicated config block (the
    nine Click options carry the full state), so this shim returns an
    empty dict. The wrapper exists for parity with sibling W607-* layers
    (cmd_search_semantic W607-BO ``_load_search_semantic_config``,
    cmd_retrieve W607-BI ``_load_retrieve_config``, cmd_context W607-BF)
    so a future config addition can land without re-instrumenting the
    marker plumbing.
    """
    return {}


def _parse_query(pattern: str, mode: str):
    """W607-BR substrate-CALL: parse + normalize the user-supplied query.

    Normalizes the case-insensitive ``mode`` token, builds the LIKE
    pattern, and returns the ``(mode_lower, like_pattern)`` pair the
    SQL builder consumes downstream. A raise surfaces a marker via
    ``search_parse_query_failed:``; degraded default keeps substring
    mode + the raw pattern wrapped in ``%`` so the envelope still
    emits.
    """
    mode_lower = (mode or "substring").lower()
    like_pattern = f"%{pattern}%"
    return mode_lower, like_pattern


def _validate_kind_filter(kind_filter: str | None):
    """W607-BR substrate-CALL: validate ``--kind`` against the closed
    KIND_ABBREV vocabulary (W1068 Pattern-1D closest-match axis).

    Returns a tuple ``(is_valid, known_kinds, frag)`` where:
      * ``is_valid`` is True iff the kind is in KIND_ABBREV (full or
        abbrev form) OR ``kind_filter`` is None (no filter requested).
      * ``known_kinds`` is the sorted list of full+abbrev kinds.
      * ``frag`` is the ``structured_unknown_filter`` payload for the
        VERDICT/facts splice OR None when the kind is valid.

    A raise surfaces ``search_validate_kind_filter_failed:`` and the
    degraded default treats the kind as valid (no envelope-level
    rejection) so the SQL path still runs and emits structured
    results — preserves the SEARCH-TRIO contract that broken
    substrate doesn't crash the envelope wholesale.
    """
    if kind_filter is None:
        return True, [], None
    full_kinds = set(KIND_ABBREV.keys())
    abbrev_kinds = set(KIND_ABBREV.values())
    known_kinds = sorted(full_kinds | abbrev_kinds)
    frag = structured_unknown_filter(
        requested=kind_filter,
        known=known_kinds,
        state="unknown_kind",
        requested_field="requested_kind",
        known_field="known_kinds",
        fact_anchor="kinds",
    )
    if frag is None:
        return True, known_kinds, None
    return False, known_kinds, frag


def _apply_kind_filter(kind_filter: str | None):
    """W607-BR substrate-CALL: translate kind abbreviation to full kind.

    The SQL builder filters on the FULL kind name (e.g. ``function``,
    not ``fn``). This shim does the abbreviation-to-full translation
    via a reverse-lookup on KIND_ABBREV. A raise surfaces
    ``search_apply_kind_filter_failed:``; degraded default returns
    the input kind unchanged so the SQL filter still runs (possibly
    yielding 0 hits with the wrong kind, which is the same shape as
    a valid-but-unmatched kind).
    """
    if kind_filter is None:
        return None
    abbrev_to_kind = {v: k for k, v in KIND_ABBREV.items()}
    return abbrev_to_kind.get(kind_filter, kind_filter)


def _fts_search(conn, where_sql, params, *, recent_days):
    """W607-BR substrate-CALL: main symbol-search SQL execution.

    Runs the LIKE/REGEXP/exact lookup against ``symbols JOIN files``
    plus the ``graph_metrics`` LEFT JOIN for PageRank ordering, with
    the optional recency boost. A raise (malformed REGEXP, locked DB,
    missing column on stale schema, sqlite3 substrate corruption)
    surfaces a marker via ``search_fts_search_failed:``; degraded
    default returns an empty list so the envelope still emits a clean
    no-match path.

    Naming is preserved as ``fts_search`` per the W607-BR task brief
    (matching the SEARCH-TRIO substrate-vocabulary requested by the
    W607-BO agent's recommendation). The underlying query is LIKE-
    based, not FTS5 — FTS5 is reached only by the explain helper
    (W607-E ``_get_explain_data``).
    """
    if recent_days and recent_days > 0:
        import time as _time

        cutoff = _time.time() - recent_days * 86400
        return conn.execute(
            f"""
            SELECT s.*, f.path as file_path,
                   COALESCE(gm.pagerank, 0) as pagerank,
                   CASE WHEN f.mtime >= ? THEN 1 ELSE 0 END AS recency_boost
            FROM symbols s JOIN files f ON s.file_id = f.id
            LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
            WHERE {where_sql}
            ORDER BY recency_boost DESC, COALESCE(gm.pagerank, 0) DESC, s.name LIMIT ?
            """,
            [cutoff, *params],
        ).fetchall()
    return conn.execute(
        f"""
        SELECT s.*, f.path as file_path, COALESCE(gm.pagerank, 0) as pagerank
        FROM symbols s JOIN files f ON s.file_id = f.id
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE {where_sql}
        ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name LIMIT ?
        """,
        params,
    ).fetchall()


def _fallback_like_match(conn, like_pattern):
    """W607-BR substrate-CALL: LIKE-pattern fallback total-count lookup.

    Reached in the text-output path when the primary search returned
    exactly 50 hits (LIMIT cap) and the user did NOT pass ``--full``;
    we re-issue the COUNT(*) without the ORDER BY/LIMIT/JOIN to give
    the user the total match count for the "50 of <N>" hint. A raise
    surfaces ``search_fallback_like_match_failed:``; degraded default
    returns 0 so the envelope still emits.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE name LIKE ? COLLATE NOCASE",
        (like_pattern,),
    ).fetchone()
    return row[0] if row else 0


# Loop3 (2026-06-02): body-preview reader. Canonical implementation now lives
# in roam.output.source_context (shared with `roam uses`'s call-line reader);
# kept as a thin re-export so existing call sites + tests are unchanged.
from roam.output.source_context import read_body_preview as _read_body_preview


def _enrich_top_results(conn, rows) -> dict:
    """Loop3 (2026-06-02): for SMALL result sets (the agent clearly wants a
    specific symbol), attach the top reference locations + a body preview —
    exactly what production telemetry (scripts/roam_efficacy.py) showed
    agents re-grep/re-Read for after roam_search_symbol (43% fallback rate;
    49% re-grep occurrences, 24% Read the file). Disambiguation lists
    (>3 matches) stay lean. Returns {symbol_id: {references, body_preview}}."""
    if not rows or len(rows) > 3:
        return {}
    out: dict = {}
    target_ids = [r["id"] for r in rows]
    ph = ",".join("?" for _ in target_ids)
    ref_locs: dict = {}
    try:
        for row in conn.execute(
            f"""SELECT e.target_id AS tid, f.path AS path, e.line AS edge_line
                FROM edges e
                JOIN symbols s ON e.source_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE e.target_id IN ({ph})
                ORDER BY f.path
                LIMIT 80""",
            target_ids,
        ).fetchall():
            bucket = ref_locs.setdefault(row["tid"], [])
            if len(bucket) < 5:
                bucket.append(f"{row['path']}:{row['edge_line']}")
    except Exception:  # noqa: BLE001 — enrichment is best-effort
        ref_locs = {}
    # Loop9 (2026-06-03): the symbol NEIGHBORHOOD (outgoing callees) — the
    # RELATED_grep fallback (roam_fallback_diag) showed agents grep for the
    # symbol family/neighborhood right after a single-symbol hit. `references`
    # above gives the callers; this gives the callees (what it calls), so the
    # agent has the full local call-graph without re-grepping.
    callee_locs: dict = {}
    callee_seen: dict[int, set[str]] = {}
    try:
        for row in conn.execute(
            f"""SELECT e.source_id AS sid, ts.name AS callee_name,
                       ts.kind AS callee_kind, tf.path AS callee_path,
                       ts.line_start AS callee_line
                FROM edges e
                JOIN symbols ts ON e.target_id = ts.id
                JOIN files tf ON ts.file_id = tf.id
                WHERE e.source_id IN ({ph}) AND ts.kind != 'import'
                ORDER BY e.source_id, ts.name
                LIMIT 80""",
            target_ids,
        ).fetchall():
            bucket = callee_locs.setdefault(row["sid"], [])
            seen = callee_seen.setdefault(row["sid"], set())
            entry = (
                f"{row['callee_name']} ({abbrev_kind(row['callee_kind'])}) {row['callee_path']}:{row['callee_line']}"
            )
            if entry not in seen and len(bucket) < 8:
                bucket.append(entry)
                seen.add(entry)
    except Exception:  # noqa: BLE001 — enrichment is best-effort
        callee_locs = {}
    for r in rows:
        info: dict = {}
        refs = ref_locs.get(r["id"])
        if refs:
            info["references"] = refs
        callees = callee_locs.get(r["id"])
        if callees:
            info["callees"] = callees
        body = _read_body_preview(r["file_path"], r["line_start"], symbol_name=r["name"])
        if body:
            info["body_preview"] = body
        if info:
            out[r["id"]] = info
    return out


def _extract_spans(rows, *, ref_counts, explanations, explain, enrichment=None):
    """W607-BR substrate-CALL: build the JSON results dict-list.

    Mirrors the cmd_search_semantic W607-BO ``_extract_spans`` shape:
    a single helper that walks the SQL rows and emits the JSON-result
    dict-list. A raise surfaces ``search_extract_spans_failed:``;
    degraded default returns ``[]`` so the envelope still emits.
    """
    results_list = []
    enrichment = enrichment or {}
    for r in rows:
        entry = {
            "name": r["name"],
            "qualified_name": r["qualified_name"] or "",
            "kind": r["kind"],
            "signature": r["signature"] or "",
            "refs": ref_counts.get(r["id"], 0),
            "pagerank": round(r["pagerank"], 6) if r["pagerank"] else 0,
            "location": loc(r["file_path"], r["line_start"]),
        }
        if explain:
            entry["explanation"] = explanations.get(r["id"], {})
        # Loop3 (2026-06-02): merge body_preview + reference locations for
        # small result sets — see _enrich_top_results. Kills the re-grep /
        # re-Read fallback that production telemetry measured at 43% on
        # roam_search_symbol.
        extra = enrichment.get(r["id"])
        if extra:
            entry.update(extra)
        results_list.append(entry)
    return results_list


def _fts5_available(conn) -> bool:
    """Check if the symbol_fts virtual table exists."""
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _build_fts_query(pattern: str) -> str:
    """Convert a search pattern into an FTS5 MATCH expression."""
    from roam.search.index_embeddings import _build_fts_query as _bfq

    return _bfq(pattern)


def _get_explain_data(conn, symbol_id: int, pattern: str, *, warnings_out: list[str] | None = None) -> dict:
    """Build score explanation for a single symbol using FTS5 functions.

    Returns a dict with:
      - bm25_score: composite BM25 score (higher = better match)
      - matched_fields: list of field names that contain the pattern
      - highlights: {field: highlighted_snippet} for matching fields
      - term_counts: {field: count} number of query term occurrences per field

    W607-E: when ``warnings_out`` is threaded in, the three silent
    ``except Exception: pass`` substrate fallbacks below disclose the
    underlying failure via ``search_explain_<phase>_failed:<exc>:<detail>``
    markers (phases: ``bm25``, ``highlight``, ``term_counts``). Empty
    bucket / clean execution → byte-identical to pre-W607-E.
    """
    explanation: dict = {
        "bm25_score": None,
        "matched_fields": [],
        "highlights": {},
        "term_counts": {},
    }

    if not _fts5_available(conn):
        return explanation

    fts_query = _build_fts_query(pattern)
    if not fts_query:
        return explanation

    # --- BM25 composite score ---
    try:
        score_row = conn.execute(
            f"SELECT -bm25(symbol_fts, {_BM25_WEIGHTS}) as score "
            f"FROM symbol_fts WHERE rowid = ? AND symbol_fts MATCH ?",
            (symbol_id, fts_query),
        ).fetchone()
        if score_row:
            explanation["bm25_score"] = round(score_row["score"], 4)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_bm25_failed:{type(exc).__name__}:{exc}")

    # --- Per-field highlights and match detection ---
    # FTS5 highlight() wraps matched terms with <<>> markers (ASCII-safe).
    try:
        highlight_sql = (
            "SELECT "
            "highlight(symbol_fts, 0, '<<', '>>') as hl_name, "
            "highlight(symbol_fts, 1, '<<', '>>') as hl_qualified_name, "
            "highlight(symbol_fts, 2, '<<', '>>') as hl_signature, "
            "highlight(symbol_fts, 3, '<<', '>>') as hl_kind, "
            "highlight(symbol_fts, 4, '<<', '>>') as hl_file_path "
            "FROM symbol_fts WHERE rowid = ? AND symbol_fts MATCH ?"
        )
        hl_row = conn.execute(highlight_sql, (symbol_id, fts_query)).fetchone()
        if hl_row:
            highlighted = _highlighted_fts_fields(hl_row)
            explanation["matched_fields"].extend(highlighted)
            explanation["highlights"].update(highlighted)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_highlight_failed:{type(exc).__name__}:{exc}")

    # --- Per-field term counts ---
    # Count raw occurrences of each query term in the symbol's fields.
    try:
        sym_row = conn.execute(
            "SELECT s.name, s.qualified_name, s.signature, s.kind, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
            (symbol_id,),
        ).fetchone()
        if sym_row:
            terms = _unique_terms_preserve_count_signal(fts_query)
            explanation["term_counts"].update(
                _field_term_counts_preserve_single_row_signal(sym_row, terms)
            )
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_term_counts_failed:{type(exc).__name__}:{exc}")

    return explanation


def _highlighted_fts_fields(row) -> dict[str, str]:
    """Return fields that FTS5 marked while preserving output field order."""
    field_map = {
        "name": row["hl_name"],
        "qualified_name": row["hl_qualified_name"],
        "signature": row["hl_signature"],
        "kind": row["hl_kind"],
        "file_path": row["hl_file_path"],
    }
    return {field: value for field, value in field_map.items() if value and "<<" in value}


def _unique_terms_preserve_count_signal(fts_query: str) -> frozenset[str]:
    """Deduplicate query terms once so repeated tokens do not inflate counts."""
    return frozenset(term.lower() for term in re.sub(r'["*(){}^]', "", fts_query).split() if term)


def _field_term_counts_preserve_single_row_signal(sym_row, terms: frozenset[str]) -> dict[str, int]:
    """Keep one row's term-count signal independent from batch plumbing."""
    if not terms:
        return {}

    counts: dict[str, int] = {}
    for field in _FTS_COLUMNS:
        field_val = (sym_row[field] or "").lower()
        if not field_val:
            continue
        count = sum(field_val.count(term) for term in terms)
        if count > 0:
            counts[field] = count
    return counts


def _get_explain_data_batch(
    conn,
    symbol_ids,
    pattern: str,
    *,
    warnings_out: list[str] | None = None,
) -> dict[int, dict]:
    """Build score explanations for many symbols using FTS5 functions.

    Batched sibling of :func:`_get_explain_data`: the three per-row
    substrates (BM25 composite score, per-field ``highlight()``, and the
    symbol-field term-count lookup) are issued as ONE batched SELECT each
    instead of three per row, collapsing ``--explain`` mode from ~3N
    SELECTs to a constant 3 regardless of result-set size.

    Returns ``{symbol_id: explanation_dict}`` keyed by every id in
    ``symbol_ids``. Ids whose FTS5 row does not MATCH the query (or when
    FTS5 is unavailable / the pattern yields no FTS query) keep the same
    empty-shape dict the single-row helper returns, so the output is
    byte-identical to looping ``_get_explain_data`` over the same ids.

    W607-E: when ``warnings_out`` is threaded in, the three batched
    substrate fallbacks disclose failures via the same
    ``search_explain_<phase>_failed:<exc>:<detail>`` markers (phases:
    ``bm25``, ``highlight``, ``term_counts``) the single-row helper emits.
    Empty bucket / clean execution → no marker.
    """
    out: dict[int, dict] = {
        sid: {
            "bm25_score": None,
            "matched_fields": [],
            "highlights": {},
            "term_counts": {},
        }
        for sid in symbol_ids
    }

    if not symbol_ids or not _fts5_available(conn):
        return out

    fts_query = _build_fts_query(pattern)
    if not fts_query:
        return out

    # --- BM25 composite score (one batched SELECT) ---
    try:
        score_rows = batched_in(
            conn,
            "SELECT rowid, -bm25(symbol_fts, " + _BM25_WEIGHTS + ") as score "
            "FROM symbol_fts WHERE rowid IN ({ph}) AND symbol_fts MATCH ?",
            list(symbol_ids),
            post=[fts_query],
        )
        for row in score_rows:
            sid = row["rowid"]
            if sid in out:
                out[sid]["bm25_score"] = round(row["score"], 4)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_bm25_failed:{type(exc).__name__}:{exc}")

    # --- Per-field highlights and match detection (one batched SELECT) ---
    # FTS5 highlight() wraps matched terms with <<>> markers (ASCII-safe).
    try:
        hl_rows = batched_in(
            conn,
            "SELECT rowid, "
            "highlight(symbol_fts, 0, '<<', '>>') as hl_name, "
            "highlight(symbol_fts, 1, '<<', '>>') as hl_qualified_name, "
            "highlight(symbol_fts, 2, '<<', '>>') as hl_signature, "
            "highlight(symbol_fts, 3, '<<', '>>') as hl_kind, "
            "highlight(symbol_fts, 4, '<<', '>>') as hl_file_path "
            "FROM symbol_fts WHERE rowid IN ({ph}) AND symbol_fts MATCH ?",
            list(symbol_ids),
            post=[fts_query],
        )
        for row in hl_rows:
            sid = row["rowid"]
            if sid not in out:
                continue
            highlighted = _highlighted_fts_fields(row)
            out[sid]["matched_fields"].extend(highlighted)
            out[sid]["highlights"].update(highlighted)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_highlight_failed:{type(exc).__name__}:{exc}")

    # --- Per-field term counts (one batched SELECT) ---
    # Count raw occurrences of each query term in the symbol's fields.
    try:
        sym_rows = batched_in(
            conn,
            "SELECT s.id, s.name, s.qualified_name, s.signature, s.kind, "
            "f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.id IN ({ph})",
            list(symbol_ids),
        )
        sym_by_id = {row["id"]: row for row in sym_rows}
        terms = _unique_terms_preserve_count_signal(fts_query)
        for sid in symbol_ids:
            sym_row = sym_by_id.get(sid)
            if not sym_row:
                continue
            out[sid]["term_counts"].update(
                _field_term_counts_preserve_single_row_signal(sym_row, terms)
            )
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"search_explain_term_counts_failed:{type(exc).__name__}:{exc}")

    return out


def _format_explanation_text(expl: dict) -> list[str]:
    """Format an explanation dict into human-readable text lines."""
    lines = []
    bm25 = expl.get("bm25_score")
    pagerank = expl.get("pagerank")
    if bm25 is not None and pagerank is not None:
        # surface the structural boost so the user can see
        # whether ordering is BM25-driven or rerank-driven.
        lines.append(f"  score:  BM25={bm25:.4f}  PageRank={pagerank:.6f}")
    elif bm25 is not None:
        lines.append(f"  score:  BM25={bm25:.4f}")
    matched = expl.get("matched_fields", [])
    if matched:
        lines.append(f"  fields: {', '.join(matched)}")
    highlights = expl.get("highlights", {})
    for field, hl in highlights.items():
        if len(hl) > 80:
            hl = hl[:77] + "..."
        lines.append(f"  match:  [{field}] {hl}")
    term_counts = expl.get("term_counts", {})
    if term_counts:
        tc_parts = [f"{f}={c}" for f, c in term_counts.items()]
        lines.append(f"  terms:  {', '.join(tc_parts)}")
    return lines


@roam_capability(
    category="exploration",
    summary="Find symbols matching a name substring (FTS5-backed, case-insensitive).",
    inputs=["pattern"],
    outputs=["matches"],
    examples=[
        "roam search handleSave",
        "roam search auth -k cls",
        "roam search 'login.*flow'",
    ],
    tags=["exploration", "search"],
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
@click.command("search")
@click.argument("pattern")
@click.option("--full", is_flag=True, help="Show all results without truncation")
@click.option(
    "-k",
    "--kind",
    "kind_filter",
    default=None,
    help="Filter by symbol kind (fn, cls, meth, var, iface, etc.)",
)
@click.option(
    "--async",
    "async_only",
    is_flag=True,
    help="Show only async functions/methods (requires Python pivot v12.4 schema).",
)
@click.option(
    "--decorator",
    "decorator_filter",
    default=None,
    help=(
        "Filter to symbols carrying a decorator matching this substring "
        "(case-insensitive). E.g. ``--decorator pytest.fixture`` finds all "
        "fixtures. ``--decorator app.route`` finds Flask/FastAPI routes."
    ),
)
@click.option(
    "--fixtures-only",
    is_flag=True,
    default=False,
    help="Shortcut for ``--decorator pytest.fixture`` (Python pivot v12.4-iter).",
)
@click.option("--explain", is_flag=True, help="Show score breakdown for each result")
@click.option(
    "--mode",
    type=click.Choice(["substring", "regex", "exact"], case_sensitive=False),
    default="substring",
    show_default=True,
    help=(
        "match style: ``substring`` (default, LIKE %p%), "
        "``regex`` (SQLite REGEXP via stdlib re), or ``exact`` "
        "(name = pattern)."
    ),
)
@click.option(
    "--recent",
    "recent_days",
    type=int,
    default=0,
    show_default=True,
    help="boost results in files modified within <N> days (0 = no boost).",
)
@click.pass_context
def search_cmd(
    ctx, pattern, full, kind_filter, async_only, decorator_filter, fixtures_only, explain, mode, recent_days
):
    """Find symbols matching a name substring (case-insensitive).

    Unlike ``grep`` (which searches file contents) and ``search-semantic``
    (which uses natural-language queries), this command finds symbols by
    exact name substring.

    \b
    Examples:
      roam search Auth
      roam search login --kind function
      roam search "User\\.\\w+" --mode regex
      roam search handle --recent-days 7

    See also ``grep`` (file-content search), ``search-semantic``
    (natural-language queries), and ``complete`` (prefix completion).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    # empty pattern matches every symbol, which is
    # expensive (LIMIT 50 across the entire FTS table) and never
    # what a user meant. Reject early with a structured error
    # rather than returning the first 50 random symbols.
    if not pattern or not pattern.strip():
        from roam.output.errors import EMPTY_INPUT, structured_usage_error

        raise structured_usage_error(
            EMPTY_INPUT,
            "search pattern cannot be empty — pass a name substring or regex (e.g. `roam search Auth`)",
        )
    # W607-BR: ADDITIVE per-phase substrate-CALL marker plumbing on top
    # of the W607-E outer-guard below. cmd_search is the EXACT-MATCH
    # sibling of cmd_search_semantic (W607-BO) and cmd_retrieve
    # (W607-BI). A silent failure in any of its substrate boundaries
    # (config load, query parse, kind-filter validation, fts/SQL search,
    # fallback LIKE-count, kind-abbrev translation, span extraction,
    # serialize) directly degrades agent productivity. W607-BR wraps
    # each substrate call so a raise becomes a structured
    # ``search_<phase>_failed:<exc_class>:<detail>`` marker instead of
    # a Click traceback. The W607-E outer-guard remains for
    # ``search_pipeline_failed:`` AND the W607-E inner explain
    # fallbacks remain for ``search_explain_<phase>_failed:`` — both as
    # final safety nets.
    #
    # Empty W607-BR bucket -> byte-identical envelope (hash-stable).
    _w607br_warnings_out: list[str] = []

    def _run_check_br(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BR marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``search_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607br_warnings_out`` and return *default* —
        the envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607br_warnings_out.append(f"search_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-BR load_config substrate-CALL. cmd_search has no dedicated
    # config today; the wrapper exists for parity so a future config
    # addition can land without re-instrumenting.
    _cfg = _run_check_br("load_config", _load_search_config, default={})

    # W1068 (sibling of W1063 + W1064): when ``--kind`` is supplied,
    # validate against the closed KIND_ABBREV vocabulary BEFORE running
    # the query. Unknown kinds previously fell into the generic "no
    # matches" branch — indistinguishable from "valid kind, 0 hits"
    # (Pattern-1D silent-success on degraded filter resolution).
    # Accept both the full kind ("function") and its abbreviation
    # ("fn") so the disclosure matches the help-text contract.
    # W1080: delegated to the shared ``structured_unknown_filter`` helper.
    # W607-BR validate_kind_filter substrate-CALL: a raise here would
    # otherwise short-circuit the entire command. Degraded default
    # treats the kind as valid so the SQL path still runs and emits
    # results — preserves the SEARCH-TRIO contract that broken
    # substrate doesn't crash the envelope wholesale.
    _kf_result = _run_check_br(
        "validate_kind_filter",
        _validate_kind_filter,
        kind_filter,
        default=(True, [], None),
    )
    is_valid_kind, known_kinds, frag = _kf_result
    if kind_filter is not None and not is_valid_kind and frag is not None:
        verdict_unknown = f"unknown kind {kind_filter!r} ({len(known_kinds)} known){frag['verdict_suffix']}"
        if json_mode:
            # W1083: ``to_summary_payload(include_did_you_mean=False)``
            # extracts the splice subset MINUS ``did_you_mean`` —
            # pre-W1080 envelope did NOT carry the field in the
            # summary (close-match suggestion only lands in the
            # verdict suffix). Callsite-specific fields (``total``,
            # ``pattern``) compose around it.
            # W607-BR: when the W607-BR bucket is non-empty (a prior
            # substrate phase like load_config raised), mirror its
            # markers into the unknown-kind early-return envelope
            # too — otherwise the disclosure is lost on the early
            # branch.
            _unknown_summary = {
                "verdict": verdict_unknown,
                **to_summary_payload(frag, include_did_you_mean=False),
                "total": 0,
                "pattern": pattern,
            }
            if _w607br_warnings_out:
                _unknown_summary["warnings_out"] = list(_w607br_warnings_out)
                _unknown_summary["partial_success"] = True
            click.echo(
                to_json(
                    json_envelope(
                        "search",
                        summary=_unknown_summary,
                        budget=token_budget,
                        pattern=pattern,
                        results=[],
                        agent_contract={
                            # LAW 4: helper emits facts anchored on
                            # ``kinds`` (in the formatter anchor set).
                            "facts": frag["facts"],
                            "next_commands": ["roam search --help"],
                        },
                        **({"warnings_out": list(_w607br_warnings_out)} if _w607br_warnings_out else {}),
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict_unknown}")
        click.echo()
        click.echo("Known kinds: " + ", ".join(known_kinds))
        return
    ensure_index()
    # W607-E: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the search pipeline. cmd_search does NOT call the
    # W605-plumbed substrate directly (search_fts / fts5_available /
    # fts5_populated): it issues raw SQL against ``symbols`` /
    # ``files`` / ``graph_metrics`` and runs FTS5 explain via the local
    # ``_get_explain_data`` helper. The disclosure shape therefore
    # mirrors cmd_retrieve W607-B / cmd_dogfood W607-D outer-guard idioms:
    #   * outer pipeline raise → ``search_pipeline_failed:<exc>:<detail>``
    #   * inner ``_get_explain_data`` silent fallbacks → threaded
    #     ``search_explain_<phase>_failed:`` markers (only when --explain)
    # Empty bucket → byte-identical envelope (hash-stable). Non-empty
    # bucket → summary.warnings_out + summary.partial_success=True +
    # top-level mirror.
    warnings_out: list[str] = []
    # W607-BR parse_query substrate-CALL: normalize the case-insensitive
    # mode token + build the LIKE pattern. A raise here surfaces a marker
    # via ``search_parse_query_failed:``; degraded default keeps
    # substring mode + the raw pattern wrapped in ``%`` so the envelope
    # still emits.
    _pq_result = _run_check_br(
        "parse_query",
        _parse_query,
        pattern,
        mode,
        default=((mode or "substring").lower(), f"%{pattern}%"),
    )
    mode_lower, like_pattern = _pq_result
    with open_db(readonly=True) as conn:
        # register a REGEXP function so SQLite can route
        # ``WHERE name REGEXP ?`` through Python's ``re`` module. Exact
        # mode bypasses LIKE entirely. Substring mode (default) keeps
        # the existing ``%p%`` semantics.
        if mode_lower == "regex":
            import re as _re

            def _regexp(expr, val):
                if val is None:
                    return False
                try:
                    return _re.search(expr, val) is not None
                except _re.error:
                    return False

            conn.create_function("REGEXP", 2, _regexp)
        # Python pivot v12.4-iter: filters MUST be in SQL, not Python
        # post-filter, otherwise rare-shape symbols (async, specific
        # decorator) get stripped by the LIMIT 50 before they can be
        # selected. Build the WHERE clause dynamically.
        if mode_lower == "regex":
            where_parts = ["s.name REGEXP ?"]
            params: list = [pattern]
        elif mode_lower == "exact":
            where_parts = ["s.name = ?"]
            params: list = [pattern]
        else:
            where_parts = ["s.name LIKE ? COLLATE NOCASE"]
            params: list = [like_pattern]
        if async_only:
            where_parts.append("s.is_async = 1")
        if fixtures_only:
            # Shortcut: --fixtures-only ≡ --decorator pytest.fixture.
            # ``has_decorator`` post-filter is too slow for large indexes;
            # use a tight LIKE that's a superset and accept the rare
            # false positive (mentions in click.option help text).
            where_parts.append("LOWER(COALESCE(s.decorators, '')) LIKE '%@pytest.fixture%'")
        if decorator_filter:
            where_parts.append("LOWER(COALESCE(s.decorators, '')) LIKE ?")
            params.append(f"%{decorator_filter.lower()}%")
        if kind_filter:
            # W607-BR apply_kind_filter substrate-CALL: translate abbrev
            # (``fn``) -> full kind (``function``) for the SQL filter.
            # A raise surfaces ``search_apply_kind_filter_failed:``;
            # degraded default returns ``kind_filter`` unchanged.
            full_kind = _run_check_br(
                "apply_kind_filter",
                _apply_kind_filter,
                kind_filter,
                default=kind_filter,
            )
            where_parts.append("s.kind = ?")
            params.append(full_kind)
        where_sql = " AND ".join(where_parts)
        params.append(9999 if full else 50)
        # recency boost: when --recent N is set, add a
        # synthetic ``recency_boost`` column (1 for files modified in the
        # last N days, 0 otherwise) and add it to ORDER BY in front of
        # PageRank. ``files.mtime`` is set during indexing.
        # W607-E outer-guard: any SQL failure here (malformed REGEXP,
        # locked DB, missing column on stale schema, sqlite3 substrate
        # corruption) historically bubbled as a Click traceback with no
        # structured envelope. Emit the canonical
        # ``search_pipeline_failed:<exc_class>:<detail>`` marker and
        # short-circuit to the empty-results path so the rest of the
        # envelope still emits with consistent fields. Mirrors
        # cmd_retrieve W607-B / cmd_dogfood W607-D outer-guard idioms.
        # W607-BR fts_search substrate-CALL (ADDITIVE to W607-E outer-
        # guard). The same SQL is now executed via a top-level shim so
        # tests can monkeypatch ``_fts_search`` to simulate a substrate
        # failure WITHOUT going through SQLite. On raise: W607-BR
        # surfaces ``search_fts_search_failed:`` AND the W607-E
        # outer-guard preserves its established
        # ``search_pipeline_failed:`` marker. Degraded default returns
        # an empty list so the envelope still emits.
        try:
            rows = _run_check_br(
                "fts_search",
                _fts_search,
                conn,
                where_sql,
                params,
                recent_days=recent_days,
                default=None,
            )
            if rows is None:
                # The W607-BR shim either ran cleanly and returned a
                # list, OR raised and the default kicked in. If we got
                # ``None``, the shim raised AND W607-BR captured it —
                # mirror the W607-E outer-guard marker too so the
                # backwards-compat contract holds (a consumer parsing
                # ``search_pipeline_failed:`` still finds it).
                _captured = next(
                    (m for m in _w607br_warnings_out if m.startswith("search_fts_search_failed:")),
                    None,
                )
                if _captured is not None:
                    # Strip the W607-BR prefix and re-emit on the
                    # W607-E channel. Shape: ``search_pipeline_failed:
                    # <exc_class>:<detail>``.
                    _w607e_marker = _captured.replace("search_fts_search_failed:", "search_pipeline_failed:", 1)
                    warnings_out.append(_w607e_marker)
                rows = []
        except Exception as exc:  # noqa: BLE001 — W607-E outer-guard
            warnings_out.append(f"search_pipeline_failed:{type(exc).__name__}:{exc}")
            rows = []

        if not rows:
            suffix = f" of kind '{kind_filter}'" if kind_filter else ""
            _no_match_verdict = f"no matches for '{pattern}'{suffix}"
            if json_mode:
                # W607-E + W607-BR combined disclosure: surface the
                # outer-guard ``search_pipeline_failed:`` marker AND
                # any W607-BR per-substrate markers. Both buckets are
                # merged so consumers reading either channel see the
                # full lineage. Empty combined bucket → byte-identical
                # envelope (hash-stable on the clean no-match path).
                _combined_no_match = list(warnings_out) + list(_w607br_warnings_out)
                _no_match_summary: dict = {"verdict": _no_match_verdict, "total": 0}
                if _combined_no_match:
                    _no_match_summary["warnings_out"] = list(_combined_no_match)
                    _no_match_summary["partial_success"] = True
                # W607-BR serialize_envelope substrate-CALL: wrap to_json
                # so a serialize raise falls back to a minimal envelope
                # rather than crashing the entire search call.
                _envelope = json_envelope(
                    "search",
                    summary=_no_match_summary,
                    pattern=pattern,
                    results=[],
                    **({"warnings_out": list(_combined_no_match)} if _combined_no_match else {}),
                )
                _text = _run_check_br(
                    "serialize_envelope",
                    lambda env=_envelope: to_json(env),
                    default=None,
                )
                if _text is None:
                    _final_combined = list(warnings_out) + list(_w607br_warnings_out)
                    _text = to_json(
                        json_envelope(
                            "search",
                            summary={
                                "verdict": "search serialize failed",
                                "warnings_out": list(_final_combined),
                                "partial_success": True,
                            },
                            warnings_out=list(_final_combined),
                        )
                    )
                click.echo(_text)
            else:
                click.echo(f"VERDICT: {_no_match_verdict}")
                click.echo()
                click.echo(f"No symbols matching '{pattern}'{suffix}")
            return

        # Batch-fetch incoming edge counts
        sym_ids = [r["id"] for r in rows]
        ref_counts = {}
        for i in range(0, len(sym_ids), 500):
            batch = sym_ids[i : i + 500]
            ph = ",".join("?" for _ in batch)
            for rc in conn.execute(
                f"SELECT target_id, COUNT(*) as cnt FROM edges WHERE target_id IN ({ph}) GROUP BY target_id",
                batch,
            ).fetchall():
                ref_counts[rc["target_id"]] = rc["cnt"]

        # Gather explanations if requested
        explanations: dict[int, dict] = {}
        if explain:
            # W607-E: thread warnings_out into _get_explain_data_batch so
            # the three inner ``except: pass`` substrate fallbacks surface
            # ``search_explain_<phase>_failed:`` markers instead of silently
            # dropping the disclosure. The batched helper issues ONE SELECT
            # per substrate (BM25 / highlight / term-counts) across all
            # result ids, collapsing explain mode from ~3N SELECTs to a
            # constant 3 regardless of result-set size.
            explanations = _get_explain_data_batch(conn, [r["id"] for r in rows], pattern, warnings_out=warnings_out)
            # augment with the per-result PageRank so the user can see
            # structural boost contribution alongside BM25.
            for r in rows:
                explanations[r["id"]]["pagerank"] = round(r["pagerank"], 6) if r["pagerank"] else 0

        _search_verdict = f"{len(rows)} matches for '{pattern}'"
        if kind_filter:
            _search_verdict += f" (kind={kind_filter})"

        if json_mode:
            # W607-BR extract_spans substrate-CALL: build the JSON-result
            # dict-list. A raise surfaces ``search_extract_spans_failed:``;
            # degraded default returns ``[]`` so the envelope still emits.
            # Loop3 (2026-06-02): enrich small result sets with body preview
            # + reference locations. Best-effort; failure → no enrichment.
            try:
                _enrichment = _enrich_top_results(conn, rows)
            except Exception:  # noqa: BLE001
                _enrichment = {}
            results_list = _run_check_br(
                "extract_spans",
                _extract_spans,
                rows,
                ref_counts=ref_counts,
                explanations=explanations,
                explain=explain,
                enrichment=_enrichment,
                default=[],
            )

            # W607-E + W607-BR combined disclosure: merge BOTH buckets so
            # consumers see every marker (outer-guard ``search_pipeline_*``
            # + W607-E inner ``search_explain_*`` + W607-BR per-substrate
            # ``search_<phase>_*``). Empty combined bucket → byte-identical
            # envelope (hash-stable on clean happy path).
            _combined_match = list(warnings_out) + list(_w607br_warnings_out)
            _match_summary: dict = {
                "verdict": _search_verdict,
                "total": len(rows),
                "pattern": pattern,
            }
            if _combined_match:
                _match_summary["warnings_out"] = list(_combined_match)
                _match_summary["partial_success"] = True
            _envelope_match = json_envelope(
                "search",
                summary=_match_summary,
                budget=token_budget,
                pattern=pattern,
                total=len(rows),
                explain=explain,
                results=results_list,
                **({"warnings_out": list(_combined_match)} if _combined_match else {}),
            )
            # W607-BR serialize_envelope substrate-CALL: wrap to_json so
            # a serialize raise falls back to a minimal envelope rather
            # than crashing the entire search call. Mirrors
            # cmd_search_semantic W607-BO ``serialize_envelope`` discipline.
            _text_match = _run_check_br(
                "serialize_envelope",
                lambda env=_envelope_match: to_json(env),
                default=None,
            )
            if _text_match is None:
                _final_combined = list(warnings_out) + list(_w607br_warnings_out)
                _text_match = to_json(
                    json_envelope(
                        "search",
                        summary={
                            "verdict": "search serialize failed",
                            "warnings_out": list(_final_combined),
                            "partial_success": True,
                        },
                        warnings_out=list(_final_combined),
                    )
                )
            click.echo(_text_match)
            return

        # --- Text output ---
        click.echo(f"VERDICT: {_search_verdict}")
        click.echo()
        total = len(rows)
        if not full and total == 50:
            # W607-BR fallback_like_match substrate-CALL: count the full
            # match population so the user sees "50 of <N>". A raise
            # surfaces ``search_fallback_like_match_failed:``; degraded
            # default returns 0 (and the "50 of 0" hint is loud enough
            # to flag the disclosure inline).
            cnt = _run_check_br(
                "fallback_like_match",
                _fallback_like_match,
                conn,
                like_pattern,
                default=0,
            )
            click.echo(f"=== Symbols matching '{pattern}' ({total} of {cnt}, use --full for all) ===")
        else:
            click.echo(f"=== Symbols matching '{pattern}' ({total}) ===")

        table_rows = []
        for r in rows:
            refs = ref_counts.get(r["id"], 0)
            pr = r["pagerank"] or 0
            #  4-decimal PR rounded all
            # niche/test symbols to 0.0001 — the column lost
            # discrimination. Use significant-figures formatting so
            # 0.000123 → "0.000123" stays distinct from 0.0001.
            if pr <= 0:
                pr_str = ""
            elif pr < 0.001:
                pr_str = f"{pr:.2e}"
            else:
                pr_str = f"{pr:.4f}"
            qn = r["qualified_name"] or ""
            name_col = qn if qn and qn != r["name"] else r["name"]
            sig = format_signature(r["signature"], max_len=40) if r["signature"] else ""
            table_rows.append(
                [
                    name_col,
                    abbrev_kind(r["kind"]),
                    sig,
                    str(refs),
                    pr_str,
                    loc(r["file_path"], r["line_start"]),
                ]
            )
        click.echo(
            format_table(
                ["Name", "Kind", "Sig", "Refs", "PR", "Location"],
                table_rows,
                budget=0 if full else 50,
            )
        )

        if explain:
            click.echo("")
            click.echo("--- Score Explanations ---")
            for r in rows:
                expl = explanations.get(r["id"], {})
                qn = r["qualified_name"] or ""
                display_name = qn if qn and qn != r["name"] else r["name"]
                click.echo(f"{display_name}:")
                lines = _format_explanation_text(expl)
                if lines:
                    for line in lines:
                        click.echo(line)
                else:
                    click.echo("  (no FTS5 explanation available -- index may use TF-IDF fallback)")


# Keep the LazyGroup import target stable while avoiding a top-level function
# name collision with roam.search.tfidf.search.
search = search_cmd
