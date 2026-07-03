"""Unit tests for roam adrs helper functions.

The end-to-end command path is covered by ``tests/test_adrs.py``. This module
exercises the pure parsing/extraction/discovery/resolution helpers in
``roam.commands.cmd_adrs`` directly, which carry the bulk of the command's
edge-case behavior (YAML frontmatter, status/title/date/ref extraction,
file discovery, and symbol-table linkage).
"""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

from roam.commands import cmd_adrs as m


# ---------------------------------------------------------------------------
# _parse_simple_yaml
# ---------------------------------------------------------------------------


class TestParseSimpleYaml:
    def test_lowercases_keys_and_strips_double_quotes(self):
        out = m._parse_simple_yaml('Title: "Use React"\nStatus: Accepted')
        assert out == {"title": "Use React", "status": "Accepted"}

    def test_strips_single_quotes(self):
        assert m._parse_simple_yaml("title: 'Hello'") == {"title": "Hello"}

    def test_hyphenated_keys_allowed(self):
        out = m._parse_simple_yaml("last-modified: 2024-01-01")
        assert out == {"last-modified": "2024-01-01"}

    def test_indented_lines_do_not_match(self):
        # The KV regex anchors a word char at line start, so indented
        # (nested) keys are intentionally ignored.
        out = m._parse_simple_yaml("  indented: x\ntop: y")
        assert out == {"top": "y"}

    def test_trailing_whitespace_stripped_from_value(self):
        assert m._parse_simple_yaml("status:   accepted   ") == {"status": "accepted"}

    def test_later_duplicate_key_overwrites_earlier(self):
        assert m._parse_simple_yaml("status: proposed\nstatus: accepted") == {"status": "accepted"}


# ---------------------------------------------------------------------------
# _extract_status
# ---------------------------------------------------------------------------


class TestExtractStatus:
    def test_frontmatter_status_wins_and_lowercases(self):
        assert m._extract_status({"status": "Accepted"}, "") == "accepted"

    def test_status_heading_block(self):
        assert m._extract_status({}, "## Status\n\nProposed\n") == "proposed"

    def test_inline_bold_status(self):
        assert m._extract_status({}, "**Status**: deprecated") == "deprecated"

    def test_unknown_when_absent(self):
        assert m._extract_status({}, "no status word here") == "unknown"

    def test_unrecognized_status_value_falls_through_to_unknown(self):
        # A frontmatter status not in the known set must not be returned.
        assert m._extract_status({"status": "banana"}, "") == "unknown"

    def test_frontmatter_precedence_over_body(self):
        out = m._extract_status({"status": "accepted"}, "## Status\n\nrejected\n")
        assert out == "accepted"


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------


class TestExtractTitle:
    def test_frontmatter_title_wins(self):
        assert m._extract_title({"title": "FM Title"}, "# Heading", "0001-x.md") == "FM Title"

    def test_heading_strips_adr_number_prefix(self):
        assert m._extract_title({}, "# ADR 1: Use SQLite", "0001-x.md") == "Use SQLite"

    def test_derives_from_filename_when_no_heading(self):
        out = m._extract_title({}, "no heading at all", "0001-use-react.md")
        assert out == "Use React"

    def test_filename_strips_adr_prefix_and_titlecases(self):
        out = m._extract_title({}, "", "adr-007-some_thing.md")
        assert out == "Some Thing"


# ---------------------------------------------------------------------------
# _extract_date
# ---------------------------------------------------------------------------


class TestExtractDate:
    def test_frontmatter_date_key(self):
        assert m._extract_date({"date": "2024-01-15"}, "") == "2024-01-15"

    def test_frontmatter_created_key(self):
        assert m._extract_date({"created": "2022-09-09"}, "") == "2022-09-09"

    def test_first_iso_date_in_body(self):
        assert m._extract_date({}, "Decided on 2023-12-01, revised later 2024-01-01") == "2023-12-01"

    def test_returns_none_when_no_date(self):
        assert m._extract_date({}, "no dates in this prose") is None


# ---------------------------------------------------------------------------
# _extract_file_refs
# ---------------------------------------------------------------------------


class TestExtractFileRefs:
    def test_backtick_and_bare_source_paths(self):
        out = m._extract_file_refs("Use `src/db.py` and also foo/bar.js here")
        assert out == ["foo/bar.js", "src/db.py"]

    def test_bare_path_without_backticks(self):
        assert m._extract_file_refs("file src/auth.py here") == ["src/auth.py"]

    def test_backtick_dotted_module_name(self):
        assert m._extract_file_refs("module `roam.index.parser` ref") == ["roam.index.parser"]

    def test_skips_url_like_refs(self):
        assert m._extract_file_refs("see `http://x.com/a.py` link") == []

    def test_skips_too_short_refs(self):
        # "a.b" is length 3, below the >3 threshold.
        assert m._extract_file_refs("token a.b is short") == []

    def test_result_is_sorted_and_deduplicated(self):
        out = m._extract_file_refs("`src/db.py` then again `src/db.py` and zzz/a.py")
        assert out == ["src/db.py", "zzz/a.py"]


# ---------------------------------------------------------------------------
# _git_ls_files
# ---------------------------------------------------------------------------


class TestGitLsFiles:
    def test_returns_files_in_git_repo(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "t@t.com"],
            ["git", "config", "user.name", "t"],
            ["git", "add", "-A"],
        ):
            subprocess.run(cmd, cwd=tmp_path, capture_output=True)
        out = m._git_ls_files(tmp_path)
        assert out is not None
        assert "a.txt" in out

    def test_returns_none_outside_git_repo(self, tmp_path):
        assert m._git_ls_files(tmp_path) is None


# ---------------------------------------------------------------------------
# _discover_adr_files
# ---------------------------------------------------------------------------


@pytest.fixture
def discover_project(tmp_path):
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0001-a.md").write_text("# A")
    # Any .md in a well-known dir counts, even without an ADR-pattern name.
    (tmp_path / "docs" / "adr" / "readme.md").write_text("# notes")
    (tmp_path / "architecture" / "decisions").mkdir(parents=True)
    (tmp_path / "architecture" / "decisions" / "adr-002-b.md").write_text("# B")
    # Pattern-matching file outside well-known dirs (caught by git scan).
    (tmp_path / "misc").mkdir()
    (tmp_path / "misc" / "0009-loose.md").write_text("# loose")
    # Non-pattern .md outside well-known dirs is excluded.
    (tmp_path / "misc" / "notes.md").write_text("# not an adr")
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "t@t.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
    ):
        subprocess.run(cmd, cwd=tmp_path, capture_output=True)
    return tmp_path


class TestDiscoverAdrFiles:
    def test_collects_well_known_and_pattern_files_sorted(self, discover_project):
        out = m._discover_adr_files(discover_project)
        assert out == [
            "architecture/decisions/adr-002-b.md",
            "docs/adr/0001-a.md",
            "docs/adr/readme.md",
            "misc/0009-loose.md",
        ]

    def test_excludes_nonpattern_md_outside_known_dirs(self, discover_project):
        assert "misc/notes.md" not in m._discover_adr_files(discover_project)

    def test_empty_project_returns_empty_list(self, tmp_path):
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "t@t.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(cmd, cwd=tmp_path, capture_output=True)
        assert m._discover_adr_files(tmp_path) == []


# ---------------------------------------------------------------------------
# _parse_adr
# ---------------------------------------------------------------------------


class TestParseAdr:
    def test_full_frontmatter_parse(self, tmp_path):
        adr = tmp_path / "docs" / "adr"
        adr.mkdir(parents=True)
        (adr / "0007-thing.md").write_text(
            "---\ntitle: My Decision\nstatus: accepted\ndate: 2024-03-03\n---\n# Heading\n\nUses `src/x.py`.\n"
        )
        rec = m._parse_adr(tmp_path, "docs/adr/0007-thing.md")
        assert rec == {
            "path": "docs/adr/0007-thing.md",
            "number": 7,
            "title": "My Decision",
            "status": "accepted",
            "date": "2024-03-03",
            "file_refs": ["src/x.py"],
        }

    def test_number_from_adr_prefixed_filename(self, tmp_path):
        adr = tmp_path / "docs" / "adr"
        adr.mkdir(parents=True)
        (adr / "adr-012-foo.md").write_text("# ADR-012: Foo Choice\n\nStatus: rejected\n")
        rec = m._parse_adr(tmp_path, "docs/adr/adr-012-foo.md")
        assert rec["number"] == 12
        assert rec["title"] == "Foo Choice"
        assert rec["status"] == "rejected"

    def test_missing_file_returns_none(self, tmp_path):
        assert m._parse_adr(tmp_path, "docs/adr/does-not-exist.md") is None

    def test_no_number_when_filename_has_no_leading_digits(self, tmp_path):
        adr = tmp_path / "docs" / "adr"
        adr.mkdir(parents=True)
        (adr / "use-react.md").write_text("# Use React\n\nStatus: accepted\n")
        rec = m._parse_adr(tmp_path, "docs/adr/use-react.md")
        assert rec["number"] is None


# ---------------------------------------------------------------------------
# _resolve_code_modules
# ---------------------------------------------------------------------------


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("CREATE TABLE symbols(id INTEGER PRIMARY KEY, file_id INTEGER, qualified_name TEXT)")
    conn.execute("INSERT INTO files VALUES (1, 'src/roam/db.py'), (2, 'src/auth.py')")
    conn.execute("INSERT INTO symbols VALUES (1, 1, 'roam.db.connect')")
    conn.commit()
    return conn


class TestResolveCodeModules:
    def test_direct_basename_and_qname_matches(self):
        conn = _make_db()
        adrs = [{"file_refs": ["src/auth.py", "db.py", "roam.db.connect", "missing.py"]}]
        out = m._resolve_code_modules(conn, adrs)
        # src/auth.py = direct match; db.py = basename match to src/roam/db.py;
        # roam.db.connect = qualified-name prefix match (also -> src/roam/db.py);
        # missing.py = no match. Result is sorted & deduplicated.
        assert out[0]["linked_modules"] == ["src/auth.py", "src/roam/db.py"]

    def test_no_refs_yields_empty_linkage(self):
        conn = _make_db()
        out = m._resolve_code_modules(conn, [{"file_refs": []}])
        assert out[0]["linked_modules"] == []

    def test_unmatched_refs_yield_empty_linkage(self):
        conn = _make_db()
        out = m._resolve_code_modules(conn, [{"file_refs": ["nowhere/ghost.py"]}])
        assert out[0]["linked_modules"] == []
