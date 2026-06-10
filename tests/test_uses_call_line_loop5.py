"""Loop5 (2026-06-02) — roam uses call-line enrichment.

Production telemetry (scripts/roam_fallback_diag.py) measured roam_uses at
31% fallback, 76% of which were re-greps of the exact same symbol: the agent
had the caller's location but wanted to SEE the calling line. The
`_read_call_line` helper embeds that line so no re-grep is needed."""

from __future__ import annotations

from roam.commands.cmd_uses import _read_call_line


def test_call_line_returns_trimmed_source(tmp_path):
    f = tmp_path / "caller.py"
    f.write_text("import x\n\n    conn = open_db(repo_dir)\nmore\n", encoding="utf-8")
    line = _read_call_line(str(f), 3, symbol_name="open_db")
    assert line == "conn = open_db(repo_dir)"


def test_call_line_staleness_guard(tmp_path):
    """If the line doesn't contain the symbol (stale index), return ''."""
    f = tmp_path / "caller.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    assert _read_call_line(str(f), 1, symbol_name="open_db") == ""


def test_call_line_out_of_range(tmp_path):
    f = tmp_path / "caller.py"
    f.write_text("only one line\n", encoding="utf-8")
    assert _read_call_line(str(f), 99, symbol_name="x") == ""


def test_call_line_missing_inputs():
    assert _read_call_line("", 5) == ""
    assert _read_call_line("x.py", None) == ""
    assert _read_call_line("/nonexistent/x.py", 1, symbol_name="x") == ""


def test_call_line_caps_length(tmp_path):
    f = tmp_path / "caller.py"
    long_call = "    result = my_symbol(" + "a, " * 200 + ")"
    f.write_text(long_call + "\n", encoding="utf-8")
    line = _read_call_line(str(f), 1, symbol_name="my_symbol")
    assert len(line) <= 200
