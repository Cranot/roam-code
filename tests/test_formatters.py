"""Unit tests for roam.output.formatter — pure functions, no fixtures needed."""

from __future__ import annotations

import json

from roam.output.formatter import (
    abbrev_kind,
    compact_json_envelope,
    format_edge_kind,
    format_signature,
    format_table,
    format_table_compact,
    indent,
    json_envelope,
    loc,
    section,
    symbol_line,
    table_to_dicts,
    to_json,
    truncate_lines,
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


# ── format_table — byte-identity regression vs pre-refactor reference ──


def _format_table_old(headers, rows, budget=0):
    """Frozen copy of the pre-refactor :func:`format_table` implementation.

    Used as the byte-identity oracle for the single-pass refactor. Pulled
    verbatim from ``src/roam/output/formatter.py`` at HEAD before the
    optimization landed. DO NOT change without also updating the
    refactored function: any divergence here is a regression.
    """
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    num_cols = len(widths)
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                widths[i] = max(widths[i], len(str(cell)))
    lines = []
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * w for w in widths))
    display_rows = rows
    if budget and len(rows) > budget:
        display_rows = rows[:budget]
    for row in display_rows:
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
    if budget and len(rows) > budget:
        lines.append(f"(+{len(rows) - budget} more)")
    return "\n".join(lines)


class TestFormatTableByteIdentity:
    """Byte-identity regression: refactored format_table must match the
    pre-refactor implementation (``_format_table_old``) on every shape."""

    def test_synthetic_100_x_8(self):
        # 100 rows × 8 columns synthetic table — primary regression.
        headers = [f"col_{i}" for i in range(8)]
        rows = [[f"r{r}c{c}_value_{r * c}" for c in range(8)] for r in range(100)]
        assert format_table(headers, rows) == _format_table_old(headers, rows)

    def test_synthetic_with_budget(self):
        # Budget < len(rows) — widths must still come from ALL rows so the
        # truncated table looks identical to the un-truncated columns.
        headers = ["A", "B", "C"]
        rows = [["x", "y", "z"]] * 10 + [["a_long_value_here", "b", "c"]]  # widest row last
        assert format_table(headers, rows, budget=5) == _format_table_old(headers, rows, budget=5)

    def test_mixed_types(self):
        # Non-string cells (int/None/bool) must stringify identically.
        headers = ["name", "count", "flag", "note"]
        rows = [
            ["foo", 1, True, None],
            ["bar", 1234567, False, "ok"],
            ["baz", 0, None, ""],
        ]
        assert format_table(headers, rows) == _format_table_old(headers, rows)

    def test_short_rows_no_padding_on_last_visible(self):
        # When a row has fewer cells than headers, the original code does
        # NOT pad the trailing missing cells — must replicate exactly.
        headers = ["A", "BB", "CCC"]
        rows = [["x", "y"], ["1", "longvalue", "3"], ["a"]]
        assert format_table(headers, rows) == _format_table_old(headers, rows)

    def test_empty_rows(self):
        assert format_table(["A"], []) == _format_table_old(["A"], [])

    def test_single_column(self):
        headers = ["X"]
        rows = [[str(i)] for i in range(50)]
        assert format_table(headers, rows) == _format_table_old(headers, rows)


class TestFormatTableBenchmark:
    """Speedup benchmark for the single-pass refactor.

    Recorded baseline on a 200-row × 6-column synthetic table
    (Python 3.11.2, Windows, 2026-05-10, 3 trials × 1000 iters each):

        BEFORE (`_format_table_old`):  ~0.99 ms / call
        AFTER  (`format_table`):       ~0.86 ms / call
        Speedup:                       ~1.15x (10–22% faster)

    The win comes from eliminating the second ``str(cell)`` /
    ``len(str(cell))`` pass in the emit loop (rows are now stringified
    exactly once, in the width-computation pass) and dropping the
    ``max()`` call in favour of a direct ``>`` branch.

    Numbers vary by machine; the assertion below only requires the new
    implementation to be no slower than the old one (with slack for
    measurement noise).
    """

    def test_speedup_200_x_6(self):
        import timeit

        headers = [f"c{i}" for i in range(6)]
        rows = [[f"row{r}_col{c}_val_{r * 7 + c}" for c in range(6)] for r in range(200)]

        # Sanity check: byte-identical on this shape too.
        assert format_table(headers, rows) == _format_table_old(headers, rows)

        n = 200
        t_old = timeit.timeit(lambda: _format_table_old(headers, rows), number=n)
        t_new = timeit.timeit(lambda: format_table(headers, rows), number=n)

        ms_old = t_old / n * 1000
        ms_new = t_new / n * 1000
        # Print so `pytest -s` surfaces the numbers — useful for capturing
        # the BEFORE → AFTER speedup in commit messages and PR notes.
        print(f"\n[format_table 200x6] old={ms_old:.3f}ms  new={ms_new:.3f}ms  speedup={ms_old / ms_new:.2f}x")

        # New impl must not regress — allow 20% measurement slack.
        assert t_new <= t_old * 1.2, f"new impl regressed: old={ms_old:.3f}ms, new={ms_new:.3f}ms"


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


# ── json_envelope: explicit agent_contract kwarg preservation ────────


class TestJsonEnvelopeAgentContract:
    def test_json_envelope_preserves_explicit_agent_contract_facts(self):
        env = json_envelope(
            "foo",
            summary={"verdict": "x", "next_commands": ["roam y"]},
            agent_contract={"facts": ["fact1", "fact2"]},
        )
        assert "agent_contract" in env
        assert "fact1" in env["agent_contract"]["facts"]
        assert "fact2" in env["agent_contract"]["facts"]
        # next_commands still auto-derived from summary:
        assert any(
            "roam y" in nc for nc in env["agent_contract"].get("next_commands", [])
        )

    def test_json_envelope_explicit_next_commands_wins(self):
        env = json_envelope(
            "foo",
            summary={"verdict": "x", "next_commands": ["roam auto"]},
            agent_contract={
                "facts": ["caller-supplied"],
                "next_commands": ["roam explicit"],
            },
        )
        nc = env["agent_contract"].get("next_commands", [])
        assert "roam explicit" in nc
        # explicit should win — auto-derived should NOT be merged in when
        # caller supplied its own next_commands
        assert "roam auto" not in nc

    def test_json_envelope_no_explicit_contract_uses_auto(self):
        env = json_envelope(
            "foo",
            summary={"verdict": "x", "next_commands": ["roam y"]},
        )
        assert env["agent_contract"]["facts"] == ["x"]
        assert "roam y" in env["agent_contract"].get("next_commands", [])

    def test_json_envelope_agent_contract_not_in_payload(self):
        # The explicit agent_contract kwarg should be consumed, not also
        # appear as a top-level payload key.
        env = json_envelope(
            "foo",
            summary={"verdict": "x"},
            agent_contract={"facts": ["a"]},
            other_payload=[1, 2, 3],
        )
        # No duplicate stray key — agent_contract is the single canonical key.
        assert env["agent_contract"]["facts"] == ["a"]
        assert env["other_payload"] == [1, 2, 3]
