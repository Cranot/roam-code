"""Find symbols matching a name substring (case-insensitive)."""

from __future__ import annotations

import re

import click

from roam.db.connection import open_db
from roam.db.queries import SEARCH_SYMBOLS
from roam.output.formatter import abbrev_kind, loc, format_signature, format_table, KIND_ABBREV, to_json, json_envelope
from roam.commands.resolve import ensure_index


# FTS5 column layout for symbol_fts: name=0, qualified_name=1, signature=2, kind=3, file_path=4
_FTS_COLUMNS = ["name", "qualified_name", "signature", "kind", "file_path"]
# BM25 weights matching index_embeddings.py: name=10, qname=5, sig=2, kind=1, path=3
_BM25_WEIGHTS = "10.0, 5.0, 2.0, 1.0, 3.0"


def _fts5_available(conn) -> bool:
    """Check if the symbol_fts virtual table exists."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_fts'"
        ).fetchone()
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
            raw_terms = re.sub(r'["*(){}^]', '', fts_query).split()
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
    if bm25 is not None:
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


@click.command()
@click.argument('pattern')
@click.option('--full', is_flag=True, help='Show all results without truncation')
@click.option('-k', '--kind', 'kind_filter', default=None,
              help='Filter by symbol kind (fn, cls, meth, var, iface, etc.)')
@click.option('--explain', is_flag=True, help='Show score breakdown for each result')
@click.pass_context
def search(ctx, pattern, full, kind_filter, explain):
    """Find symbols matching a name substring (case-insensitive)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    token_budget = ctx.obj.get('budget', 0) if ctx.obj else 0
    ensure_index()
    like_pattern = f"%{pattern}%"
    with open_db(readonly=True) as conn:
        rows = conn.execute(SEARCH_SYMBOLS, (like_pattern, 9999 if full else 50)).fetchall()

        if kind_filter:
            abbrev_to_kind = {v: k for k, v in KIND_ABBREV.items()}
            full_kind = abbrev_to_kind.get(kind_filter, kind_filter)
            rows = [r for r in rows if r["kind"] == full_kind]

        if not rows:
            suffix = f" of kind '{kind_filter}'" if kind_filter else ""
            if json_mode:
                click.echo(to_json(json_envelope("search",
                    summary={"total": 0},
                    pattern=pattern, results=[],
                )))
            else:
                click.echo(f"No symbols matching '{pattern}'{suffix}")
            return

        # Batch-fetch incoming edge counts
        sym_ids = [r["id"] for r in rows]
        ref_counts = {}
        for i in range(0, len(sym_ids), 500):
            batch = sym_ids[i:i + 500]
            ph = ",".join("?" for _ in batch)
            for rc in conn.execute(
                f"SELECT target_id, COUNT(*) as cnt FROM edges "
                f"WHERE target_id IN ({ph}) GROUP BY target_id",
                batch,
            ).fetchall():
                ref_counts[rc["target_id"]] = rc["cnt"]

        # Gather explanations if requested
        explanations: dict[int, dict] = {}
        if explain:
            for r in rows:
                explanations[r["id"]] = _get_explain_data(conn, r["id"], pattern)

        if json_mode:
            results_list = []
            for r in rows:
                entry = {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"] or "",
                    "kind": r["kind"],
                    "signature": r["signature"] or "",
                    "refs": ref_counts.get(r["id"], 0),
                    "pagerank": round(r["pagerank"], 4) if r["pagerank"] else 0,
                    "location": loc(r["file_path"], r["line_start"]),
                }
                if explain:
                    entry["explanation"] = explanations.get(r["id"], {})
                results_list.append(entry)

            click.echo(to_json(json_envelope("search",
                summary={"total": len(rows), "pattern": pattern},
                budget=token_budget,
                pattern=pattern,
                total=len(rows),
                explain=explain,
                results=results_list,
            )))
            return

        # --- Text output ---
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
            pr_str = f"{pr:.4f}" if pr > 0 else ""
            qn = r["qualified_name"] or ""
            name_col = qn if qn and qn != r["name"] else r["name"]
            sig = format_signature(r["signature"], max_len=40) if r["signature"] else ""
            table_rows.append([
                name_col,
                abbrev_kind(r["kind"]),
                sig,
                str(refs),
                pr_str,
                loc(r["file_path"], r["line_start"]),
            ])
        click.echo(format_table(
            ["Name", "Kind", "Sig", "Refs", "PR", "Location"],
            table_rows,
            budget=0 if full else 50,
        ))

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

