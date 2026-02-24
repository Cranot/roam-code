"""Tests for roam search --explain flag (backlog item #55).

Covers:
  - --explain flag adds explanation to text output
  - --explain flag adds explanation to JSON output
  - field-level match identification
  - highlight generation with <<term>> markers
  - term frequency per field
  - multi-term queries
  - no results case
  - combined with --budget
  - combined with --full
  - FTS5 unavailable fallback
  - backward compat: no --explain = unchanged output
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, index_in_process, git_init


def _make_project(tmp_path_factory, files=None):
    proj = tmp_path_factory.mktemp("project")
    (proj / ".gitignore").write_text(".roam/\n")
    py_files = {
        "auth.py": (
            "class AuthManager:\n"
            "    def authenticate_user(self, username, password):\n"
            "        return True\n"
            "\n"
            "    def create_user(self, username, email):\n"
            "        pass\n"
        ),
        "models.py": (
            "class UserProfile:\n"
            "    def __init__(self, name, email):\n"
            "        self.name = name\n"
            "\n"
            "def get_user_by_email(email):\n"
            "    return None\n"
        ),
        "search.py": (
            "def search_users(query):\n"
            "    pass\n"
            "\n"
            "def search_documents(query, limit=10):\n"
            "    return []\n"
        ),
        "utils.py": (
            "def validate_email(email):\n"
            "    return chr(64) in email\n"
            "\n"
            "def format_user_name(first, last):\n"
            "    return first + last\n"
        ),
    }
    if files:
        py_files.update(files)
    for fname, content in py_files.items():
        (proj / fname).write_text(content, encoding='utf-8')
    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# Unit tests: _format_explanation_text
# ===========================================================================

class TestFormatExplanationText:
    """Tests for _format_explanation_text()."""

    def test_empty_explanation(self):
        from roam.commands.cmd_search import _format_explanation_text
        result = _format_explanation_text({})
        assert result == []

    def test_bm25_score_formatted(self):
        from roam.commands.cmd_search import _format_explanation_text
        result = _format_explanation_text({"bm25_score": 3.14})
        assert len(result) == 1
        assert "BM25=3.1400" in result[0]
        assert "score:" in result[0]

    def test_bm25_zero_shown(self):
        from roam.commands.cmd_search import _format_explanation_text
        result = _format_explanation_text({"bm25_score": 0.0})
        assert any("BM25=0.0000" in l for l in result)

    def test_matched_fields_shown(self):
        from roam.commands.cmd_search import _format_explanation_text
        expl = {"matched_fields": ["name", "qualified_name"]}
        result = _format_explanation_text(expl)
        assert any("fields:" in l for l in result)
        joined = " ".join(result)
        assert "name" in joined
        assert "qualified_name" in joined

    def test_empty_matched_fields_not_shown(self):
        from roam.commands.cmd_search import _format_explanation_text
        result = _format_explanation_text({"matched_fields": []})
        assert not any("fields:" in l for l in result)

    def test_highlights_shown_with_markers(self):
        from roam.commands.cmd_search import _format_explanation_text
        expl = {"highlights": {"name": "<<user>>Manager"}}
        result = _format_explanation_text(expl)
        assert any("match:" in l for l in result)
        assert any("<<user>>" in l for l in result)

    def test_highlight_truncated_at_80(self):
        from roam.commands.cmd_search import _format_explanation_text
        long_hl = "<<x>>" + "a" * 100
        expl = {"highlights": {"name": long_hl}}
        result = _format_explanation_text(expl)
        hl_line = next(l for l in result if "match:" in l)
        assert "..." in hl_line

    def test_term_counts_shown(self):
        from roam.commands.cmd_search import _format_explanation_text
        expl = {"term_counts": {"name": 2, "signature": 1}}
        result = _format_explanation_text(expl)
        assert any("terms:" in l for l in result)
        joined = " ".join(result)
        assert "name=2" in joined

    def test_empty_term_counts_not_shown(self):
        from roam.commands.cmd_search import _format_explanation_text
        result = _format_explanation_text({"term_counts": {}})
        assert not any("terms:" in l for l in result)

    def test_full_explanation_all_sections(self):
        from roam.commands.cmd_search import _format_explanation_text
        expl = {
            "bm25_score": 5.5,
            "matched_fields": ["name"],
            "highlights": {"name": "<<auth>>Manager"},
            "term_counts": {"name": 1},
        }
        result = _format_explanation_text(expl)
        assert len(result) >= 4
        joined = " ".join(result)
        assert "BM25" in joined
        assert "fields" in joined
        assert "match" in joined
        assert "terms" in joined


class TestFts5Available:
    """Tests for _fts5_available()."""

    def test_false_on_empty_db(self):
        from roam.commands.cmd_search import _fts5_available
        import sqlite3 as _sq3
        conn = _sq3.connect(":memory:")
        conn.row_factory = _sq3.Row
        assert _fts5_available(conn) is False
        conn.close()

    def test_true_when_symbol_fts_exists(self):
        from roam.commands.cmd_search import _fts5_available
        import sqlite3 as _sq3
        conn = _sq3.connect(":memory:")
        conn.row_factory = _sq3.Row
        try:
            conn.execute("CREATE VIRTUAL TABLE symbol_fts USING fts5(name)")
            assert _fts5_available(conn) is True
        except Exception:
            pytest.skip("FTS5 not available in this SQLite build")
        finally:
            conn.close()


class TestBuildFtsQuery:
    """Tests for _build_fts_query()."""

    def test_simple_pattern_not_empty(self):
        from roam.commands.cmd_search import _build_fts_query
        assert _build_fts_query("user") != ""

    def test_empty_pattern_returns_empty(self):
        from roam.commands.cmd_search import _build_fts_query
        assert _build_fts_query("") == ""

    def test_multi_word_pattern(self):
        from roam.commands.cmd_search import _build_fts_query
        result = _build_fts_query("auth manager")
        assert result != ""

    def test_camelcase_pattern(self):
        from roam.commands.cmd_search import _build_fts_query
        result = _build_fts_query("AuthManager")
        assert result != ""


class TestFts5ColumnLayout:
    """Tests for module-level FTS5 constants."""

    def test_fts_columns_has_five_entries(self):
        from roam.commands.cmd_search import _FTS_COLUMNS
        assert len(_FTS_COLUMNS) == 5

    def test_fts_columns_order(self):
        from roam.commands.cmd_search import _FTS_COLUMNS
        assert _FTS_COLUMNS == ["name", "qualified_name", "signature", "kind", "file_path"]

    def test_bm25_weights_constant(self):
        from roam.commands.cmd_search import _BM25_WEIGHTS
        assert _BM25_WEIGHTS == "10.0, 5.0, 2.0, 1.0, 3.0"

    def test_name_is_column_zero(self):
        from roam.commands.cmd_search import _FTS_COLUMNS
        assert _FTS_COLUMNS[0] == "name"

    def test_file_path_is_column_four(self):
        from roam.commands.cmd_search import _FTS_COLUMNS
        assert _FTS_COLUMNS[4] == "file_path"


# ===========================================================================
# Integration: text output with --explain
# ===========================================================================


class TestSearchExplainText:
    """Integration tests for --explain in text output mode."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def _run(self, *args):
        from roam.cli import cli
        return self.runner.invoke(cli, list(args), catch_exceptions=False)

    def test_explain_help_shows_option(self):
        result = self._run("search", "--help")
        assert result.exit_code == 0
        assert "--explain" in result.output

    def test_explain_adds_explanation_section(self):
        result = self._run("search", "--explain", "user")
        assert result.exit_code == 0
        assert "Score Explanations" in result.output

    def test_no_explain_no_section(self):
        result = self._run("search", "user")
        assert result.exit_code == 0
        assert "Score Explanations" not in result.output

    def test_explain_no_results_no_section(self):
        result = self._run("search", "--explain", "xyznotfound99")
        assert result.exit_code == 0
        assert "No symbols matching" in result.output
        assert "Score Explanations" not in result.output

    def test_explain_shows_score_or_fallback(self):
        result = self._run("search", "--explain", "user")
        assert result.exit_code == 0
        has_bm25 = "BM25=" in result.output
        has_fallback = "no FTS5 explanation available" in result.output
        assert has_bm25 or has_fallback

    def test_explain_with_full_flag(self):
        result = self._run("search", "--explain", "--full", "user")
        assert result.exit_code == 0
        assert "Score Explanations" in result.output

    def test_explain_with_kind_filter(self):
        result = self._run("search", "--explain", "-k", "meth", "user")
        assert result.exit_code == 0

    def test_table_appears_before_explanation(self):
        result = self._run("search", "--explain", "user")
        assert result.exit_code == 0
        output = result.output
        if "Score Explanations" in output:
            assert output.index("===") < output.index("Score Explanations")

    def test_table_appears_exactly_once(self):
        result = self._run("search", "--explain", "user")
        assert result.exit_code == 0
        assert result.output.count("=== Symbols matching") == 1

    def test_no_explain_backward_compat(self):
        result = self._run("search", "user")
        assert result.exit_code == 0
        assert "=== Symbols matching" in result.output
        assert "--- Score Explanations ---" not in result.output


# ===========================================================================
# Integration: JSON output with --explain
# ===========================================================================


class TestSearchExplainJson:
    """Integration tests for --explain in JSON output mode."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def _run_json(self, *args):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--json", "search"] + list(args), catch_exceptions=False)
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        return json.loads(result.output)

    def test_envelope_contract(self):
        data = self._run_json("--explain", "user")
        assert_json_envelope(data, command="search")

    def test_explain_true_in_envelope(self):
        data = self._run_json("--explain", "user")
        assert data.get("explain") is True

    def test_no_explain_false_in_envelope(self):
        data = self._run_json("user")
        assert data.get("explain") is False

    def test_results_have_explanation_key(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            assert "explanation" in item

    def test_no_explain_no_explanation_key(self):
        data = self._run_json("user")
        for item in data.get("results", []):
            assert "explanation" not in item

    def test_explanation_has_all_subkeys(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            expl = item["explanation"]
            for key in ("bm25_score", "matched_fields", "highlights", "term_counts"):
                assert key in expl

    def test_no_results_valid_envelope(self):
        data = self._run_json("--explain", "zzznotfound99999")
        assert_json_envelope(data, command="search")
        assert data["summary"]["total"] == 0
        assert data["results"] == []

    def test_bm25_numeric_or_none(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            score = item["explanation"].get("bm25_score")
            assert score is None or isinstance(score, (int, float))

    def test_matched_fields_is_list(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            assert isinstance(item["explanation"].get("matched_fields", []), list)

    def test_highlights_is_dict(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            assert isinstance(item["explanation"].get("highlights", {}), dict)

    def test_term_counts_is_dict(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            assert isinstance(item["explanation"].get("term_counts", {}), dict)

    def test_summary_total_matches_results_len(self):
        data = self._run_json("--explain", "user")
        assert data["summary"]["total"] == len(data["results"])

    def test_command_name_is_search(self):
        data = self._run_json("--explain", "user")
        assert data["command"] == "search"

    def test_explain_with_full_flag_json(self):
        data = self._run_json("--explain", "--full", "user")
        assert_json_envelope(data, command="search")


# ===========================================================================
# Integration: field matching and highlight tests
# ===========================================================================


class TestSearchExplainFieldMatching:
    """Tests for field-level match detection."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def _run_json(self, *args):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--json", "search"] + list(args), catch_exceptions=False)
        assert result.exit_code == 0
        return json.loads(result.output)

    def test_matched_fields_are_valid_columns(self):
        data = self._run_json("--explain", "user")
        valid = {"name", "qualified_name", "signature", "kind", "file_path"}
        for item in data.get("results", []):
            for field in item["explanation"].get("matched_fields", []):
                assert field in valid

    def test_highlights_have_angle_markers(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            for field, hl in item["explanation"].get("highlights", {}).items():
                assert "<<" in hl
                assert ">>" in hl

    def test_highlights_subset_of_matched_fields(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            expl = item["explanation"]
            hl_fields = set(expl.get("highlights", {}).keys())
            matched = set(expl.get("matched_fields", []))
            assert hl_fields <= matched

    def test_term_counts_values_are_positive(self):
        data = self._run_json("--explain", "user")
        for item in data.get("results", []):
            for f, c in item["explanation"].get("term_counts", {}).items():
                assert c > 0


# ===========================================================================
# Integration: budget compatibility
# ===========================================================================


class TestSearchExplainBudget:
    """Tests for --explain with --budget."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def test_budget_with_explain_json(self):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--json", "--budget", "500", "search", "--explain", "user"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, command="search")

    def test_budget_with_explain_text(self):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--budget", "500", "search", "--explain", "user"], catch_exceptions=False)
        assert result.exit_code == 0


# ===========================================================================
# Edge cases
# ===========================================================================


class TestSearchExplainEdgeCases:
    """Edge case tests for --explain."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def _run(self, *args):
        from roam.cli import cli
        return self.runner.invoke(cli, list(args), catch_exceptions=False)

    def _run_json(self, *args):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--json", "search"] + list(args), catch_exceptions=False)
        assert result.exit_code == 0
        return json.loads(result.output)

    def test_empty_results_json_explain(self):
        data = self._run_json("--explain", "zzznotfound99999")
        assert data["summary"]["total"] == 0
        assert data["results"] == []

    def test_empty_results_text_explain(self):
        result = self._run("search", "--explain", "zzznotfound99999")
        assert result.exit_code == 0
        assert "No symbols matching" in result.output

    def test_underscore_pattern(self):
        result = self._run("search", "--explain", "user_")
        assert result.exit_code == 0

    def test_camelcase_pattern(self):
        data = self._run_json("--explain", "AuthManager")
        assert_json_envelope(data, command="search")

    def test_no_duplicate_table_output(self):
        result = self._run("search", "--explain", "user")
        assert result.exit_code == 0
        assert result.output.count("=== Symbols matching") == 1

    def test_total_matches_results_count(self):
        data = self._run_json("--explain", "user")
        assert data["summary"]["total"] == len(data["results"])


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestSearchBackwardCompat:
    """Ensure --explain does not alter behavior when not used."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path_factory, monkeypatch):
        self.proj = _make_project(tmp_path_factory)
        monkeypatch.chdir(self.proj)
        self.runner = CliRunner()

    def _run(self, *args):
        from roam.cli import cli
        return self.runner.invoke(cli, list(args), catch_exceptions=False)

    def _run_json(self, *args):
        from roam.cli import cli
        result = self.runner.invoke(cli, ["--json", "search"] + list(args), catch_exceptions=False)
        assert result.exit_code == 0
        return json.loads(result.output)

    def test_text_unchanged_without_explain(self):
        result = self._run("search", "user")
        assert result.exit_code == 0
        assert "=== Symbols matching" in result.output
        assert "--- Score Explanations ---" not in result.output

    def test_json_no_explanation_without_explain(self):
        data = self._run_json("user")
        assert_json_envelope(data, command="search")
        for item in data.get("results", []):
            assert "explanation" not in item

    def test_kind_filter_works_with_explain(self):
        result = self._run("search", "--explain", "-k", "fn", "user")
        assert result.exit_code == 0

    def test_full_flag_without_explain(self):
        result = self._run("search", "--full", "user")
        assert result.exit_code == 0
        assert "--- Score Explanations ---" not in result.output


# ===========================================================================
# Integration: text output with --explain
# ===========================================================================

