"""Loop3 (2026-06-02) — roam search enrichment.

Production telemetry (scripts/roam_efficacy.py) measured roam_search_symbol
at a 43% fallback rate: agents searched a symbol, then immediately re-grepped
it for occurrences (49%) or Read the file for the body (24%). The enrichment
attaches `references` (top reference locations) + `body_preview` to SMALL
result sets so the agent doesn't have to re-search.

These tests pin the enrichment helpers directly (no index needed for the
body-preview path; the references path is exercised via a tiny in-memory DB)."""

from __future__ import annotations

import sqlite3

from roam.commands.cmd_search import (
    _enrich_top_results,
    _extract_spans,
    _read_body_preview,
)


def test_body_preview_returns_definition(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("# header\ndef target_fn(x):\n    return x + 1\n\n", encoding="utf-8")
    # line_start = 2 (the def line)
    bp = _read_body_preview(str(f), 2, symbol_name="target_fn", n_lines=3)
    assert "def target_fn" in bp
    assert "return x + 1" in bp


def test_body_preview_staleness_guard(tmp_path):
    """If the line number points at content NOT containing the symbol (stale
    index), return '' rather than misleading content."""
    f = tmp_path / "mod.py"
    f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    # line_start = 1, but symbol 'target_fn' is nowhere → stale → ''
    bp = _read_body_preview(str(f), 1, symbol_name="target_fn")
    assert bp == ""


def test_body_preview_missing_file():
    assert _read_body_preview("/nonexistent/x.py", 5, symbol_name="foo") == ""
    assert _read_body_preview("", 5) == ""
    assert _read_body_preview("x.py", None) == ""


def _mk_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT, file_id INTEGER,
                              line_start INTEGER);
        CREATE TABLE edges (source_id INTEGER, target_id INTEGER, line INTEGER);
        INSERT INTO files VALUES (1, 'src/a.py'), (2, 'src/b.py');
        INSERT INTO symbols VALUES (10, 'target', 1, 5), (11, 'caller1', 1, 20),
                                   (12, 'caller2', 2, 7);
        INSERT INTO edges VALUES (11, 10, 21), (12, 10, 8);
        """
    )
    return conn


def test_enrich_attaches_reference_locations():
    conn = _mk_db()
    rows = [{"id": 10, "name": "target", "file_path": "src/a.py", "line_start": 5}]
    enr = _enrich_top_results(conn, rows)
    assert 10 in enr
    refs = enr[10].get("references", [])
    assert "src/a.py:21" in refs
    assert "src/b.py:8" in refs


def test_enrich_skips_large_result_sets():
    conn = _mk_db()
    rows = [
        {"id": i, "name": f"s{i}", "file_path": "x", "line_start": 1} for i in range(10)
    ]  # >3 → disambiguation list, stay lean
    assert _enrich_top_results(conn, rows) == {}


def test_extract_spans_merges_enrichment():
    rows = [
        {
            "id": 10,
            "name": "target",
            "qualified_name": "m.target",
            "kind": "function",
            "signature": "def target()",
            "pagerank": 0.1,
            "file_path": "src/a.py",
            "line_start": 5,
        }
    ]
    enrichment = {10: {"references": ["src/a.py:21"], "body_preview": "def target():\n    pass"}}
    out = _extract_spans(rows, ref_counts={10: 2}, explanations={}, explain=False, enrichment=enrichment)
    assert out[0]["references"] == ["src/a.py:21"]
    assert "body_preview" in out[0]
    assert out[0]["refs"] == 2  # original field preserved


def test_extract_spans_no_enrichment_back_compat():
    rows = [
        {
            "id": 10,
            "name": "target",
            "qualified_name": "",
            "kind": "function",
            "signature": "",
            "pagerank": 0,
            "file_path": "x",
            "line_start": 1,
        }
    ]
    out = _extract_spans(rows, ref_counts={}, explanations={}, explain=False)
    assert "references" not in out[0]
    assert "body_preview" not in out[0]
