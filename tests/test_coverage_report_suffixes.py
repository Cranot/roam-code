"""Coverage ingestion must retain root-level suffix matches amid duplicate names."""

import sqlite3

import pytest

from roam.coverage_reports import ingest_coverage_reports


@pytest.mark.parametrize("indexed_path", ["c.py", "b/c.py", "a/b/c.py"])
def test_ingest_matches_indexed_suffix_with_duplicate_basenames(tmp_path, indexed_path):
    report = tmp_path / "coverage.info"
    report.write_text("SF:a/b/c.py\nDA:1,1\nDA:2,0\nend_of_record\n", encoding="utf-8")

    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);"
            "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, "
            "line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE file_stats (file_id INTEGER PRIMARY KEY, "
            "coverage_pct REAL, covered_lines INTEGER, coverable_lines INTEGER);"
            "CREATE TABLE symbol_metrics (symbol_id INTEGER PRIMARY KEY, "
            "coverage_pct REAL, covered_lines INTEGER, coverable_lines INTEGER);"
        )
        conn.executemany(
            "INSERT INTO files (id, path) VALUES (?, ?)",
            [(1, indexed_path), (2, "other/c.py"), (3, "unrelated/c.py")],
        )
        conn.execute("INSERT INTO symbols VALUES (10, 1, 1, 2)")

        result = ingest_coverage_reports(conn, [str(report)], project_root=tmp_path)

        assert result["unmatched_files"] == []
        assert result["matched_files"] == 1
        assert [
            tuple(row)
            for row in conn.execute("SELECT file_id, coverage_pct, covered_lines, coverable_lines FROM file_stats")
        ] == [(1, 50.0, 1, 2)]
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT symbol_id, coverage_pct, covered_lines, coverable_lines FROM symbol_metrics"
            )
        ] == [(10, 50.0, 1, 2)]
