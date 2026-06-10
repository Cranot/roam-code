"""Canonical source-context helpers (2026-06-02) — shared by `roam search`
(body_preview) and `roam uses` (call_line). The staleness guard is the
load-bearing behavior: never show wrong content when the index is stale."""

from __future__ import annotations

from roam.output.source_context import read_body_preview, read_source_line


def test_body_preview_basic(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("# h\ndef target(x):\n    return x\n", encoding="utf-8")
    assert "def target" in read_body_preview(str(f), 2, "target", n_lines=2, cwd=str(tmp_path))


def test_body_preview_staleness(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    assert read_body_preview(str(f), 1, "target", cwd=str(tmp_path)) == ""


def test_source_line_basic(tmp_path):
    f = tmp_path / "c.py"
    f.write_text("x\n    conn = open_db(d)\ny\n", encoding="utf-8")
    assert read_source_line(str(f), 2, "open_db", cwd=str(tmp_path)) == "conn = open_db(d)"


def test_source_line_staleness_and_bounds(tmp_path):
    f = tmp_path / "c.py"
    f.write_text("only\n", encoding="utf-8")
    assert read_source_line(str(f), 1, "missing", cwd=str(tmp_path)) == ""  # stale
    assert read_source_line(str(f), 99, "only", cwd=str(tmp_path)) == ""  # oob


def test_missing_inputs():
    assert read_body_preview("", 5) == ""
    assert read_source_line("x.py", None) == ""
    assert read_body_preview("/nope/x.py", 1, "f") == ""
