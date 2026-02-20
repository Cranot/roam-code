"""Tests for the roam intent command (doc-to-code intent graph)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, git_init, git_commit, parse_json_output


# ===========================================================================
# Helper: invoke intent directly (avoids cli.py registration requirement)
# ===========================================================================


def _invoke_intent(args, cwd, json_mode=False):
    """Invoke the intent command directly from its module.

    Returns a CliRunner Result.
    """
    from roam.commands.cmd_intent import intent

    runner = CliRunner()
    full_args = []
    if json_mode:
        # Simulate --json by providing a context object
        full_args.extend(args)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(cwd))
            result = runner.invoke(
                intent,
                full_args,
                obj={"json": True},
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)
    else:
        full_args.extend(args)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(cwd))
            result = runner.invoke(
                intent,
                full_args,
                obj={"json": False},
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)
    return result


def _parse_intent_json(result, label="intent"):
    """Parse JSON output from an intent CliRunner result."""
    assert result.exit_code == 0, (
        f"intent {label} failed (exit {result.exit_code}):\n{result.output}"
    )
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"Invalid JSON from intent {label}: {e}\n"
            f"Output was:\n{result.output[:500]}"
        )


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def intent_project(tmp_path):
    """Create a small project with code and documentation files."""
    proj = tmp_path / "intent_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Code files
    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def display(self):\n"
        "        return self.name\n"
    )
    (proj / "service.py").write_text(
        "from models import User\n\n"
        "def create_user(name):\n"
        "    return User(name)\n\n"
        "def process_order(data):\n"
        "    user = create_user(data['name'])\n"
        "    return user\n"
    )

    # Doc files with symbol references
    docs = proj / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "# Architecture\n\n"
        "The main model is `User` which represents a user.\n\n"
        "## Services\n\n"
        "The `create_user` function creates a new User instance.\n"
        "The `process_order` function handles order processing.\n"
    )
    (docs / "old-api.md").write_text(
        "# Old API\n\n"
        "The `calculate_tax_v2` function was removed in v3.\n"
        "Use `old_handler` for legacy support.\n"
    )
    (proj / "README.md").write_text(
        "# My Project\n\n"
        "Main entry: `create_user`\n"
    )

    git_init(proj)
    git_commit(proj, "add docs")
    old = os.getcwd()
    os.chdir(str(proj))
    index_in_process(proj)
    os.chdir(old)
    return proj


@pytest.fixture
def no_docs_project(tmp_path):
    """Create a project with code but NO documentation files."""
    proj = tmp_path / "no_docs_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "app.py").write_text(
        "def main():\n"
        "    pass\n"
    )

    git_init(proj)
    git_commit(proj, "init")
    old = os.getcwd()
    os.chdir(str(proj))
    index_in_process(proj)
    os.chdir(old)
    return proj


# ===========================================================================
# Tests
# ===========================================================================


class TestIntentBasic:
    """Basic smoke tests for the intent command."""

    def test_intent_runs(self, intent_project):
        """Command exits with code 0."""
        result = _invoke_intent([], cwd=intent_project)
        assert result.exit_code == 0, (
            f"intent command failed (exit {result.exit_code}):\n{result.output}"
        )

    def test_intent_json_envelope(self, intent_project):
        """JSON output follows the roam envelope contract."""
        result = _invoke_intent([], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        # Check required top-level keys
        assert "command" in data, "Missing 'command' key in envelope"
        assert "version" in data, "Missing 'version' key in envelope"
        assert "timestamp" in data, "Missing 'timestamp' key in envelope"
        assert "summary" in data, "Missing 'summary' key in envelope"
        assert data["command"] == "intent", (
            f"Expected command='intent', got {data['command']}"
        )
        summary = data["summary"]
        assert isinstance(summary, dict), f"summary should be dict, got {type(summary)}"
        assert "verdict" in summary, "summary missing 'verdict'"

    def test_intent_has_links(self, intent_project):
        """JSON output contains a 'links' list."""
        result = _invoke_intent([], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        assert "links" in data, "Expected 'links' key in JSON output"
        assert isinstance(data["links"], list), "'links' should be a list"

    def test_intent_link_fields(self, intent_project):
        """Each link in JSON output has doc, symbol, and line fields."""
        result = _invoke_intent([], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        links = data.get("links", [])
        assert len(links) > 0, "Expected at least one link in the output"
        for lnk in links:
            assert "doc" in lnk, f"Link missing 'doc' field: {lnk}"
            assert "symbol" in lnk, f"Link missing 'symbol' field: {lnk}"
            assert "line" in lnk, f"Link missing 'line' field: {lnk}"

    def test_intent_verdict_line(self, intent_project):
        """Text output starts with 'VERDICT:'."""
        result = _invoke_intent([], cwd=intent_project)
        assert result.exit_code == 0
        first_line = result.output.strip().splitlines()[0]
        assert first_line.startswith("VERDICT:"), (
            f"Expected first line to start with VERDICT:, got: {first_line!r}"
        )

    def test_intent_by_doc_grouping(self, intent_project):
        """JSON output groups links by doc file in 'by_doc'."""
        result = _invoke_intent([], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        assert "by_doc" in data, "Expected 'by_doc' key in JSON output"
        assert isinstance(data["by_doc"], dict), "'by_doc' should be a dict"
        # Should have entries for docs that have links
        assert len(data["by_doc"]) > 0, "Expected at least one doc in by_doc"


class TestIntentSymbolFilter:
    """Tests for --symbol filter mode."""

    def test_intent_finds_symbol_refs(self, intent_project):
        """Default mode finds User and create_user referenced in architecture.md."""
        result = _invoke_intent([], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        links = data.get("links", [])
        symbol_names = {lnk["symbol"] for lnk in links}
        # At least one of the key symbols from architecture.md should appear
        assert symbol_names & {"User", "create_user", "process_order"}, (
            f"Expected to find User, create_user, or process_order in links, "
            f"got: {symbol_names}"
        )

    def test_intent_symbol_filter(self, intent_project):
        """--symbol User finds docs mentioning User."""
        result = _invoke_intent(
            ["--symbol", "User"], cwd=intent_project, json_mode=True
        )
        data = _parse_intent_json(result)
        links = data.get("links", [])
        # All returned links should be for 'User'
        for lnk in links:
            assert lnk["symbol"] == "User", (
                f"Expected symbol='User', got {lnk['symbol']!r}"
            )
        # Should have found at least one mention in architecture.md
        assert len(links) > 0, "Expected at least one mention of 'User' in docs"

    def test_intent_symbol_filter_text_verdict(self, intent_project):
        """--symbol produces a VERDICT: line mentioning the symbol."""
        result = _invoke_intent(["--symbol", "User"], cwd=intent_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "User" in result.output


class TestIntentDocFilter:
    """Tests for --doc filter mode."""

    def test_intent_doc_filter(self, intent_project):
        """--doc docs/architecture.md finds symbols referenced there."""
        result = _invoke_intent(
            ["--doc", "docs/architecture.md"],
            cwd=intent_project,
            json_mode=True,
        )
        data = _parse_intent_json(result)
        links = data.get("links", [])
        assert len(links) > 0, (
            "Expected symbols referenced in docs/architecture.md"
        )
        # All links should reference architecture.md
        for lnk in links:
            assert "architecture" in lnk["doc"], (
                f"Expected doc to be architecture.md, got: {lnk['doc']}"
            )
        # Should find create_user or process_order or User
        sym_names = {lnk["symbol"] for lnk in links}
        assert sym_names & {"User", "create_user", "process_order"}, (
            f"Expected to find known symbols; got: {sym_names}"
        )


class TestIntentDrift:
    """Tests for --drift mode."""

    def test_intent_drift(self, intent_project):
        """--drift finds 'calculate_tax_v2' as drift (not in codebase)."""
        result = _invoke_intent(["--drift"], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        drift = data.get("drift", [])
        assert isinstance(drift, list), "'drift' should be a list"
        # calculate_tax_v2 appears in old-api.md but not in the codebase
        drift_symbols = {d["symbol"] for d in drift}
        assert "calculate_tax_v2" in drift_symbols, (
            f"Expected 'calculate_tax_v2' in drift, got: {drift_symbols}"
        )

    def test_intent_drift_text_output(self, intent_project):
        """--drift text output shows VERDICT and drift section."""
        result = _invoke_intent(["--drift"], cwd=intent_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Either shows DRIFT section or "No drift detected"
        assert (
            "DRIFT" in result.output
            or "drift" in result.output.lower()
            or "No drift" in result.output
        )


class TestIntentUndocumented:
    """Tests for --undocumented mode."""

    def test_intent_undocumented(self, intent_project):
        """--undocumented shows symbols not mentioned in any docs."""
        result = _invoke_intent(["--undocumented"], cwd=intent_project, json_mode=True)
        data = _parse_intent_json(result)
        assert "undocumented" in data, "Expected 'undocumented' key in JSON output"
        assert isinstance(data["undocumented"], list), "'undocumented' should be a list"
        assert result.exit_code == 0

    def test_intent_undocumented_verdict(self, intent_project):
        """--undocumented produces a VERDICT: line."""
        result = _invoke_intent(["--undocumented"], cwd=intent_project)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


class TestIntentEdgeCases:
    """Edge case tests for intent command."""

    def test_intent_no_docs(self, no_docs_project):
        """Project without doc files gives a graceful message."""
        result = _invoke_intent([], cwd=no_docs_project)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )
        assert "VERDICT:" in result.output
        # Should mention no docs found
        assert (
            "No documentation" in result.output
            or "no doc" in result.output.lower()
        )

    def test_intent_no_docs_json(self, no_docs_project):
        """Project without doc files returns valid JSON with doc_files=0."""
        result = _invoke_intent([], cwd=no_docs_project, json_mode=True)
        data = _parse_intent_json(result)
        assert data["summary"]["doc_files"] == 0

    def test_intent_skips_short_names(self, tmp_path):
        """Very short symbol names (< 3 chars) are not matched."""
        proj = tmp_path / "short_names_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        # Code with short and long function names
        (proj / "app.py").write_text(
            "def go():\n"
            "    pass\n\n"
            "def fn():\n"
            "    pass\n\n"
            "def long_func():\n"
            "    pass\n"
        )
        # Doc that mentions the short names and the long name
        (proj / "README.md").write_text(
            "# Readme\n\n"
            "Use `go` and `fn` everywhere.\n"
            "The `long_func` is the main entry.\n"
        )

        git_init(proj)
        git_commit(proj, "init")
        old = os.getcwd()
        os.chdir(str(proj))
        index_in_process(proj)
        os.chdir(old)

        result = _invoke_intent([], cwd=proj, json_mode=True)
        data = _parse_intent_json(result)
        links = data.get("links", [])
        sym_names = {lnk["symbol"] for lnk in links}
        # short names "go" and "fn" should NOT appear (length < 3)
        assert "go" not in sym_names, (
            "Short symbol 'go' (len=2) should be filtered out, found in links"
        )
        assert "fn" not in sym_names, (
            "Short symbol 'fn' (len=2) should be filtered out, found in links"
        )
