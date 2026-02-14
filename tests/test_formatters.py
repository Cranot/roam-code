"""Unit tests for roam.output.formatter — pure functions, no fixtures needed."""

from __future__ import annotations

import json

from roam.output.formatter import (
    abbrev_kind,
    loc,
    symbol_line,
    section,
    indent,
    truncate_lines,
    format_signature,
    format_edge_kind,
    format_table,
    to_json,
    compact_json_envelope,
    table_to_dicts,
    format_table_compact,
    ws_loc,
)


# ── abbrev_kind ──────────────────────────────────────────────────────


class TestAbbrevKind:
    def test_function(self):
        assert abbrev_kind("function") == "fn"

    def test_class(self):
        assert abbrev_kind("class") == "cls"

    def test_method(self):
        assert abbrev_kind("method") == "meth"

    def test_variable(self):
        assert abbrev_kind("variable") == "var"

    def test_constant(self):
        assert abbrev_kind("constant") == "const"

    def test_interface(self):
        assert abbrev_kind("interface") == "iface"

    def test_constructor(self):
        assert abbrev_kind("constructor") == "ctor"

    def test_unknown_kind_passes_through(self):
        assert abbrev_kind("widget") == "widget"

    def test_empty_string_passes_through(self):
        assert abbrev_kind("") == ""


# ── loc ──────────────────────────────────────────────────────────────


class TestLoc:
    def test_with_line(self):
        assert loc("src/main.py", 42) == "src/main.py:42"

    def test_without_line(self):
        assert loc("src/main.py") == "src/main.py"

    def test_line_zero(self):
        assert loc("file.py", 0) == "file.py:0"


# ── symbol_line ──────────────────────────────────────────────────────


class TestSymbolLine:
    def test_basic(self):
        result = symbol_line("foo", "function", None, "a.py", 10)
        assert result == "fn  foo  a.py:10"

    def test_with_signature(self):
        result = symbol_line("bar", "method", "(self, x)", "b.py", 5)
        assert result == "meth  bar  (self, x)  b.py:5"

    def test_with_extra(self):
        result = symbol_line("baz", "class", None, "c.py", 1, extra="[hot]")
        assert result == "cls  baz  c.py:1  [hot]"

    def test_without_line(self):
        result = symbol_line("qux", "variable", None, "d.py")
        assert result == "var  qux  d.py"

    def test_all_parts(self):
        result = symbol_line("init", "constructor", "(self)", "e.py", 20, extra="core")
        assert result == "ctor  init  (self)  e.py:20  core"


# ── section ──────────────────────────────────────────────────────────


class TestSection:
    def test_no_budget(self):
        result = section("TITLE:", ["a", "b", "c"])
        assert result == "TITLE:\na\nb\nc"

    def test_budget_under(self):
        result = section("HEAD:", ["x", "y"], budget=5)
        assert result == "HEAD:\nx\ny"

    def test_budget_over(self):
        result = section("HEAD:", ["a", "b", "c", "d", "e"], budget=2)
        assert result == "HEAD:\na\nb\n  (+3 more)"

    def test_empty_lines(self):
        result = section("EMPTY:", [])
        assert result == "EMPTY:"


# ── indent ───────────────────────────────────────────────────────────


class TestIndent:
    def test_single_level(self):
        assert indent("hello") == "  hello"

    def test_multiple_levels(self):
        assert indent("hello", level=3) == "      hello"

    def test_multiline(self):
        result = indent("line1\nline2\nline3")
        assert result == "  line1\n  line2\n  line3"


# ── truncate_lines ───────────────────────────────────────────────────


class TestTruncateLines:
    def test_under_budget(self):
        lines = ["a", "b"]
        assert truncate_lines(lines, 5) == ["a", "b"]

    def test_at_budget(self):
        lines = ["a", "b", "c"]
        assert truncate_lines(lines, 3) == ["a", "b", "c"]

    def test_over_budget(self):
        lines = ["a", "b", "c", "d", "e"]
        result = truncate_lines(lines, 2)
        assert result == ["a", "b", "(+3 more)"]


# ── format_signature ────────────────────────────────────────────────


class TestFormatSignature:
    def test_none(self):
        assert format_signature(None) == ""

    def test_empty_string(self):
        assert format_signature("") == ""

    def test_short_signature(self):
        assert format_signature("(self, x: int) -> bool") == "(self, x: int) -> bool"

    def test_long_signature_truncated(self):
        sig = "a" * 100
        result = format_signature(sig, max_len=20)
        assert result == "a" * 17 + "..."
        assert len(result) == 20


# ── format_edge_kind ─────────────────────────────────────────────────


class TestFormatEdgeKind:
    def test_with_underscores(self):
        assert format_edge_kind("calls_into") == "calls into"

    def test_without_underscores(self):
        assert format_edge_kind("imports") == "imports"


# ── format_table ─────────────────────────────────────────────────────


class TestFormatTable:
    def test_empty_rows(self):
        assert format_table(["A", "B"], []) == "(none)"

    def test_single_row(self):
        result = format_table(["Name", "Val"], [["foo", "1"]])
        lines = result.split("\n")
        assert len(lines) == 3  # header, separator, one data row
        assert "Name" in lines[0]
        assert "foo" in lines[2]

    def test_multiple_rows(self):
        result = format_table(["X"], [["a"], ["b"], ["c"]])
        lines = result.split("\n")
        assert len(lines) == 5  # header + separator + 3 rows

    def test_with_budget(self):
        rows = [["r1"], ["r2"], ["r3"], ["r4"]]
        result = format_table(["Col"], rows, budget=2)
        lines = result.split("\n")
        # header + separator + 2 rows + "(+2 more)"
        assert len(lines) == 5
        assert "(+2 more)" in lines[-1]

    def test_column_alignment(self):
        result = format_table(["A", "B"], [["short", "x"], ["longvalue", "y"]])
        lines = result.split("\n")
        # The header separator dashes should reflect the widest column values
        dashes = lines[1]
        parts = dashes.split("  ")
        # First column width should be at least len("longvalue") = 9
        assert len(parts[0]) >= 9


# ── to_json ──────────────────────────────────────────────────────────


class TestToJson:
    def test_dict(self):
        result = to_json({"key": "value"})
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_nested_structure(self):
        data = {"a": [1, 2, 3], "b": {"c": True}}
        result = to_json(data)
        parsed = json.loads(result)
        assert parsed == data


# ── compact_json_envelope ────────────────────────────────────────────


class TestCompactJsonEnvelope:
    def test_basic(self):
        result = compact_json_envelope("health")
        assert result == {"command": "health"}

    def test_with_payload(self):
        result = compact_json_envelope("test", score=95, items=["a", "b"])
        assert result["command"] == "test"
        assert result["score"] == 95
        assert result["items"] == ["a", "b"]


# ── table_to_dicts ───────────────────────────────────────────────────


class TestTableToDicts:
    def test_basic_conversion(self):
        headers = ["name", "kind"]
        rows = [["foo", "fn"], ["bar", "cls"]]
        result = table_to_dicts(headers, rows)
        assert result == [
            {"name": "foo", "kind": "fn"},
            {"name": "bar", "kind": "cls"},
        ]

    def test_empty(self):
        assert table_to_dicts(["a", "b"], []) == []


# ── format_table_compact ────────────────────────────────────────────


class TestFormatTableCompact:
    def test_basic(self):
        result = format_table_compact(["A", "B"], [["1", "2"], ["3", "4"]])
        lines = result.split("\n")
        assert lines[0] == "A\tB"
        assert lines[1] == "1\t2"
        assert lines[2] == "3\t4"

    def test_with_budget(self):
        rows = [["a"], ["b"], ["c"], ["d"]]
        result = format_table_compact(["X"], rows, budget=2)
        lines = result.split("\n")
        assert len(lines) == 4  # header + 2 rows + "(+2 more)"
        assert "(+2 more)" in lines[-1]

    def test_empty(self):
        assert format_table_compact(["Col"], []) == "(none)"


# ── ws_loc ───────────────────────────────────────────────────────────


class TestWsLoc:
    def test_with_line(self):
        assert ws_loc("myrepo", "src/main.py", 10) == "[myrepo] src/main.py:10"

    def test_without_line(self):
        assert ws_loc("myrepo", "src/main.py") == "[myrepo] src/main.py"
