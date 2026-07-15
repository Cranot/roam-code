"""Battery for the shared PHP source scanner (php_source_scan).

This module is the single implementation behind brace/bracket matching in
auth-gaps / over-fetch / migration-safety. The battery pins the poison
patterns measured on a real Laravel app plus the PHP constructs a stranger
repo will throw at it (heredocs with unpaired apostrophes, nowdocs, PHP 8
attributes, cross-line strings/comments), and the byte-parity guarantee on
plain code.
"""

from __future__ import annotations

from roam.commands.php_source_scan import (
    brace_deltas,
    code_brace_deltas,
    matching_delim_end,
)


def _total(deltas):
    return sum(o for o, _ in deltas), sum(c for _, c in deltas)


class TestBraceDeltasCore:
    def test_plain_code_byte_parity_with_naive_count(self):
        src = "function f() {\n    if ($x) {\n        g();\n    }\n}\n"
        naive = [(line.count("{"), line.count("}")) for line in src.splitlines()]
        assert brace_deltas(src) == naive

    def test_braces_in_line_comment_ignored(self):
        src = "a {\n// registered before {id} to avoid }\nb }\n"
        assert brace_deltas(src) == [(1, 0), (0, 0), (0, 1)]

    def test_braces_in_hash_comment_ignored(self):
        assert brace_deltas("# a } comment {\nx {\n") == [(0, 0), (1, 0)]

    def test_braces_in_strings_ignored(self):
        src = "$r = '/users/{id}/edit';\n$s = \"x{$var}y\";\n"
        assert _total(brace_deltas(src)) == (0, 0)

    def test_escaped_quote_does_not_close_string(self):
        # The \' stays inside the string; the } after it is still in-string.
        src = "$s = 'it\\'s a {trap}';\n{\n"
        assert brace_deltas(src) == [(0, 0), (1, 0)]

    def test_block_comment_spanning_lines(self):
        src = "a {\n/* comment { with\nan apostrophe don't and } */ b }\n"
        assert brace_deltas(src) == [(1, 0), (0, 0), (0, 1)]

    def test_backtick_string_ignored(self):
        assert _total(brace_deltas("$o = `cmd {arg}`;\n")) == (0, 0)

    def test_dq_string_spanning_lines(self):
        src = '$s = "line one {\nline two }";\n{ }\n'
        assert brace_deltas(src) == [(0, 0), (0, 0), (1, 1)]

    def test_code_brace_deltas_matches_brace_deltas(self):
        src = "a {\n// {id}\n}\n"
        assert code_brace_deltas(src.splitlines()) == brace_deltas(src)


class TestHeredocs:
    """The construct the first-generation string-aware scanners got WRONG:
    an unpaired apostrophe in a heredoc body flipped them into string state
    and swallowed all following code (worse than the naive counter)."""

    def test_heredoc_with_unpaired_apostrophe_does_not_poison(self):
        src = "$sql = <<<SQL\n-- don't count this { or } or ] here\nSELECT 1\nSQL;\nif ($x) {\n}\n"
        # Body lines contribute nothing; the code after resumes counting.
        assert brace_deltas(src) == [(0, 0), (0, 0), (0, 0), (0, 0), (1, 0), (0, 1)]

    def test_nowdoc_body_ignored(self):
        src = "$s = <<<'EOT'\nO'Brien said {no}\nEOT;\n{\n"
        assert brace_deltas(src) == [(0, 0), (0, 0), (0, 0), (1, 0)]

    def test_real_corpus_shape_interpolated_identifiers(self):
        # Exact shape from the measured app (FileTableService.php): heredoc SQL
        # with quoted, interpolated identifiers containing braces.
        src = (
            "$sql = <<<SQL\n"
            '    INSERT INTO "{$schema}"."article_ledger_accounts"\n'
            '        ("id", "article_id")\n'
            "    SELECT gen_random_uuid()\n"
            "SQL;\n"
            "DB::statement($sql);\n"
            "if ($ok) {\n"
        )
        opens, closes = _total(brace_deltas(src))
        assert (opens, closes) == (1, 0)

    def test_indented_closing_label_php73(self):
        src = "$s = <<<TXT\n  body { don't\n  TXT;\nx {\n"
        assert brace_deltas(src) == [(0, 0), (0, 0), (0, 0), (1, 0)]

    def test_label_prefix_word_does_not_close(self):
        # Body line starting 'SQLSTATE' must not terminate a <<<SQL heredoc.
        src = "$s = <<<SQL\nSQLSTATE codes { here\nSQL;\n{\n"
        assert brace_deltas(src) == [(0, 0), (0, 0), (0, 0), (1, 0)]

    def test_code_after_closing_label_is_scanned(self):
        # `TXT;` may be followed by code on some formats — e.g. `TXT) . '{'`.
        src = "$s = <<<TXT\nbody\nTXT; $x = 1; {\n"
        assert brace_deltas(src) == [(0, 0), (0, 0), (1, 0)]


class TestPhp8Attributes:
    def test_attribute_is_not_a_comment(self):
        # `#[...]` is code: the line must not be dropped, so a trailing `{`
        # (e.g. an attributed closure/class opener style) is still counted.
        src = "#[Deprecated] class X {\n}\n"
        assert brace_deltas(src) == [(1, 0), (0, 1)]

    def test_attribute_string_arg_braces_still_ignored(self):
        src = "#[Route('/x/{id}')]\n{\n"
        assert brace_deltas(src) == [(0, 0), (1, 0)]

    def test_bare_hash_still_comments(self):
        assert brace_deltas("# just a comment {\n") == [(0, 0)]


class TestMatchingDelimEnd:
    def test_simple_array(self):
        text = "return [1, 2, 3];"
        start = text.index("[")
        assert matching_delim_end(text, start, "[", "]") == text.index("]")

    def test_string_and_comment_delims_skipped(self):
        text = "return [\n  'a]b',   // stray ] in comment\n  'c',\n];"
        start = text.index("[")
        assert matching_delim_end(text, start, "[", "]") == text.rindex("]")

    def test_heredoc_delims_skipped(self):
        text = "return [\n  <<<SQL\n  ] don't ]\nSQL\n,\n];"
        start = text.index("[")
        assert matching_delim_end(text, start, "[", "]") == text.rindex("]")

    def test_nested(self):
        text = "x = [ [1, 2], [3] ];"
        start = text.index("[")
        assert matching_delim_end(text, start, "[", "]") == text.rindex("]")

    def test_unbalanced_runs_to_eof(self):
        text = "x = [ 1, 2"
        assert matching_delim_end(text, text.index("["), "[", "]") == len(text) - 1

    def test_braces_variant(self):
        text = "function f() {\n  // note {id}\n  $s = '}';\n}"
        start = text.index("{")
        assert matching_delim_end(text, start, "{", "}") == text.rindex("}")
