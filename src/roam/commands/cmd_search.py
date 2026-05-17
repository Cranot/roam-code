"""Find symbols matching a name substring (case-insensitive).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because search outputs are invocation-scoped symbol-name match
enumerations — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
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


def _fts5_available(conn) -> bool:
    """Check if the symbol_fts virtual table exists."""
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'").fetchone()
        return row is not None
    except Exception:
        return False


def _build_fts_query(pattern: str) -> str:
    """Convert a search pattern into an FTS5 MATCH expression."""
    from roam.search.index_embeddings import _build_fts_query as _bfq

    return _bfq(pattern)


def _get_explain_data(conn, symbol_id: int, pattern: str) -> dict:
    """Build score explanation for a single symbol using FTS5 functions.

    Returns a dict with:
      - bm25_score: composite BM25 score (higher = better match)
      - matched_fields: list of field names that contain the pattern
      - highlights: {field: highlighted_snippet} for matching fields
      - term_counts: {field: count} number of query term occurrences per field
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
    except Exception:
        pass

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
            field_map = {
                "name": hl_row["hl_name"],
                "qualified_name": hl_row["hl_qualified_name"],
                "signature": hl_row["hl_signature"],
                "kind": hl_row["hl_kind"],
                "file_path": hl_row["hl_file_path"],
            }
            for field, value in field_map.items():
                if value and "<<" in value:
                    explanation["matched_fields"].append(field)
                    explanation["highlights"][field] = value
    except Exception:
        pass

    # --- Per-field term counts ---
    # Count raw occurrences of each query term in the symbol's fields.
    try:
        sym_row = conn.execute(
            "SELECT s.name, s.qualified_name, s.signature, s.kind, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id = ?",
            (symbol_id,),
        ).fetchone()
        if sym_row:
            # Extract plain terms from the FTS5 query expression
            raw_terms = re.sub(r'["*(){}^]', "", fts_query).split()
            for field in _FTS_COLUMNS:
                col = "file_path" if field == "file_path" else field
                field_val = (sym_row[col] or "").lower()
                if not field_val:
                    continue
                count = sum(field_val.count(term.lower()) for term in raw_terms if term)
                if count > 0:
                    explanation["term_counts"][field] = count
    except Exception:
        pass

    return explanation


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
@click.command()
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
def search(ctx, pattern, full, kind_filter, async_only, decorator_filter, fixtures_only, explain, mode, recent_days):
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
    # W1068 (sibling of W1063 + W1064): when ``--kind`` is supplied,
    # validate against the closed KIND_ABBREV vocabulary BEFORE running
    # the query. Unknown kinds previously fell into the generic "no
    # matches" branch — indistinguishable from "valid kind, 0 hits"
    # (Pattern-1D silent-success on degraded filter resolution).
    # Accept both the full kind ("function") and its abbreviation
    # ("fn") so the disclosure matches the help-text contract.
    # W1080: delegated to the shared ``structured_unknown_filter`` helper.
    if kind_filter is not None:
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
        if frag is not None:
            verdict_unknown = f"unknown kind {kind_filter!r} ({len(known_kinds)} known){frag['verdict_suffix']}"
            if json_mode:
                # W1083: ``to_summary_payload(include_did_you_mean=False)``
                # extracts the splice subset MINUS ``did_you_mean`` —
                # pre-W1080 envelope did NOT carry the field in the
                # summary (close-match suggestion only lands in the
                # verdict suffix). Callsite-specific fields (``total``,
                # ``pattern``) compose around it.
                click.echo(
                    to_json(
                        json_envelope(
                            "search",
                            summary={
                                "verdict": verdict_unknown,
                                **to_summary_payload(frag, include_did_you_mean=False),
                                "total": 0,
                                "pattern": pattern,
                            },
                            budget=token_budget,
                            pattern=pattern,
                            results=[],
                            agent_contract={
                                # LAW 4: helper emits facts anchored on
                                # ``kinds`` (in the formatter anchor set).
                                "facts": frag["facts"],
                                "next_commands": ["roam search --help"],
                            },
                        )
                    )
                )
                return
            click.echo(f"VERDICT: {verdict_unknown}")
            click.echo()
            click.echo("Known kinds: " + ", ".join(known_kinds))
            return
    ensure_index()
    like_pattern = f"%{pattern}%"
    mode_lower = (mode or "substring").lower()
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
            abbrev_to_kind = {v: k for k, v in KIND_ABBREV.items()}
            full_kind = abbrev_to_kind.get(kind_filter, kind_filter)
            where_parts.append("s.kind = ?")
            params.append(full_kind)
        where_sql = " AND ".join(where_parts)
        params.append(9999 if full else 50)
        # recency boost: when --recent N is set, add a
        # synthetic ``recency_boost`` column (1 for files modified in the
        # last N days, 0 otherwise) and add it to ORDER BY in front of
        # PageRank. ``files.mtime`` is set during indexing.
        if recent_days and recent_days > 0:
            import time as _time

            cutoff = _time.time() - recent_days * 86400
            rows = conn.execute(
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
        else:
            rows = conn.execute(
                f"""
                SELECT s.*, f.path as file_path, COALESCE(gm.pagerank, 0) as pagerank
                FROM symbols s JOIN files f ON s.file_id = f.id
                LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
                WHERE {where_sql}
                ORDER BY COALESCE(gm.pagerank, 0) DESC, s.name LIMIT ?
                """,
                params,
            ).fetchall()

        if not rows:
            suffix = f" of kind '{kind_filter}'" if kind_filter else ""
            _no_match_verdict = f"no matches for '{pattern}'{suffix}"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "search",
                            summary={"verdict": _no_match_verdict, "total": 0},
                            pattern=pattern,
                            results=[],
                        )
                    )
                )
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
            for r in rows:
                expl = _get_explain_data(conn, r["id"], pattern)
                # augment with the per-result PageRank so the
                # user can see structural boost contribution alongside
                # BM25.
                expl["pagerank"] = round(r["pagerank"], 6) if r["pagerank"] else 0
                explanations[r["id"]] = expl

        _search_verdict = f"{len(rows)} matches for '{pattern}'"
        if kind_filter:
            _search_verdict += f" (kind={kind_filter})"

        if json_mode:
            results_list = []
            for r in rows:
                entry = {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"] or "",
                    "kind": r["kind"],
                    "signature": r["signature"] or "",
                    "refs": ref_counts.get(r["id"], 0),
                    # W361 — round to 6 decimals to match W336's cmd_impact
                    # widening. On a 25k-symbol graph per-symbol PageRank
                    # values fall in the 1e-6 to 1e-3 range, so 4-decimal
                    # rounding silently zeroed legitimate small values.
                    "pagerank": round(r["pagerank"], 6) if r["pagerank"] else 0,
                    "location": loc(r["file_path"], r["line_start"]),
                }
                if explain:
                    entry["explanation"] = explanations.get(r["id"], {})
                results_list.append(entry)

            click.echo(
                to_json(
                    json_envelope(
                        "search",
                        summary={"verdict": _search_verdict, "total": len(rows), "pattern": pattern},
                        budget=token_budget,
                        pattern=pattern,
                        total=len(rows),
                        explain=explain,
                        results=results_list,
                    )
                )
            )
            return

        # --- Text output ---
        click.echo(f"VERDICT: {_search_verdict}")
        click.echo()
        total = len(rows)
        if not full and total == 50:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE name LIKE ? COLLATE NOCASE",
                (like_pattern,),
            ).fetchone()[0]
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
