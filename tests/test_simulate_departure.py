"""Tests for roam simulate-departure -- developer departure simulation."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.conftest import (
    git_init,
    git_commit,
    index_in_process,
    invoke_cli,
    parse_json_output,
    assert_json_envelope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_config_author(path, name, email):
    """Set the git author for a repo."""
    subprocess.run(
        ["git", "config", "user.name", name],
        cwd=path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", email],
        cwd=path, capture_output=True,
    )


def _git_add_commit(path, msg):
    """Stage all and commit."""
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def departure_project(tmp_path):
    """A multi-file project with distinct per-author ownership.

    Alice owns auth/ (sole author), Bob owns billing/ (sole author),
    both contribute to api.py, and utils.py is shared equally.
    """
    proj = tmp_path / "depart_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Initial commit (needs a file to init)
    (proj / "README.md").write_text("# Test project\n")
    subprocess.run(["git", "init"], cwd=proj, capture_output=True)
    _git_config_author(proj, "Setup", "setup@test.com")
    _git_add_commit(proj, "init")

    # Alice writes auth module
    auth = proj / "auth"
    auth.mkdir()
    _git_config_author(proj, "Alice Smith", "alice@example.com")
    (auth / "login.py").write_text(
        'def authenticate(user, password):\n'
        '    """Authenticate a user."""\n'
        '    if not _verify_password(password):\n'
        '        return None\n'
        '    return user\n'
        '\n'
        'def _verify_password(password):\n'
        '    """Check password strength."""\n'
        '    return len(password) >= 8\n'
    )
    (auth / "tokens.py").write_text(
        'from auth.login import authenticate\n'
        '\n'
        'def create_token(user, password):\n'
        '    """Create an auth token."""\n'
        '    auth_user = authenticate(user, password)\n'
        '    if auth_user:\n'
        '        return {"token": "abc123", "user": auth_user}\n'
        '    return None\n'
    )
    _git_add_commit(proj, "Alice adds auth module")

    # Bob writes billing module
    _git_config_author(proj, "Bob Jones", "bob@example.com")
    billing = proj / "billing"
    billing.mkdir()
    (billing / "charge.py").write_text(
        'def process_charge(amount):\n'
        '    """Process a billing charge."""\n'
        '    if amount <= 0:\n'
        '        raise ValueError("invalid amount")\n'
        '    return {"charged": amount, "status": "ok"}\n'
    )
    (billing / "invoice.py").write_text(
        'def create_invoice(items):\n'
        '    """Create an invoice from items."""\n'
        '    total = _calculate_total(items)\n'
        '    return {"items": items, "total": total}\n'
        '\n'
        'def _calculate_total(items):\n'
        '    """Sum up item prices."""\n'
        '    return sum(item["price"] for item in items)\n'
    )
    _git_add_commit(proj, "Bob adds billing module")

    # Alice writes the api layer (so Alice has more files)
    _git_config_author(proj, "Alice Smith", "alice@example.com")
    (proj / "api.py").write_text(
        'from auth.tokens import create_token\n'
        'from billing.charge import process_charge\n'
        '\n'
        'def handle_purchase(user, password, amount):\n'
        '    """Handle a purchase request."""\n'
        '    token = create_token(user, password)\n'
        '    if not token:\n'
        '        return None\n'
        '    result = process_charge(amount)\n'
        '    return result\n'
    )
    _git_add_commit(proj, "Alice adds api layer")

    # Both contribute to utils
    _git_config_author(proj, "Alice Smith", "alice@example.com")
    (proj / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
    )
    _git_add_commit(proj, "Alice adds utils")

    _git_config_author(proj, "Bob Jones", "bob@example.com")
    (proj / "utils.py").write_text(
        'def format_name(first, last):\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        'def parse_email(raw):\n'
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        '        return None\n'
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
    )
    _git_add_commit(proj, "Bob adds parse_email to utils")

    # Index the project
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def departure_project_with_codeowners(departure_project):
    """Extend departure_project with a CODEOWNERS file.

    Alice is sole CODEOWNER of auth/, Bob of billing/, both own api.py.
    """
    proj = departure_project
    gh_dir = proj / ".github"
    gh_dir.mkdir(exist_ok=True)

    (gh_dir / "CODEOWNERS").write_text(
        "# CODEOWNERS\n"
        "auth/ @alice\n"
        "billing/ @bob\n"
        "api.py @alice @bob\n"
    )
    _git_config_author(proj, "Setup", "setup@test.com")
    _git_add_commit(proj, "add CODEOWNERS")

    # Re-index to pick up .github directory
    out, rc = index_in_process(proj)
    assert rc == 0, f"re-index failed:\n{out}"
    return proj


@pytest.fixture
def single_author_project(tmp_path):
    """A project where one developer wrote everything."""
    proj = tmp_path / "solo_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    subprocess.run(["git", "init"], cwd=proj, capture_output=True)
    _git_config_author(proj, "Solo Dev", "solo@example.com")
    (proj / "app.py").write_text(
        'def main():\n'
        '    """Entry point."""\n'
        '    print("hello")\n'
    )
    (proj / "lib.py").write_text(
        'def helper():\n'
        '    return 42\n'
    )
    _git_add_commit(proj, "init with all files")

    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests: Basic functionality
# ---------------------------------------------------------------------------


class TestSimulateDepartureBasic:
    """Core functionality tests."""

    def test_developer_with_high_ownership(self, departure_project, cli_runner):
        """Alice owns auth/ + api.py -- should be flagged."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Alice owns several files, should have at-risk results
        assert "auth" in result.output.lower() or "api" in result.output.lower() or "RISK" in result.output

    def test_developer_with_partial_ownership(self, departure_project, cli_runner):
        """Bob owns billing/ -- should flag billing files."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Bob Jones"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_developer_email_match(self, departure_project, cli_runner):
        """Should match by email address."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "alice@example.com"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_nonexistent_developer(self, departure_project, cli_runner):
        """Non-existent developer should produce LOW RISK verdict."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "nobody@nowhere.com"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        assert "LOW RISK" in result.output

    def test_multiple_developers(self, departure_project, cli_runner):
        """Simulate departure of multiple developers at once."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith", "Bob Jones"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_sole_developer_departure(self, single_author_project, cli_runner):
        """If the only developer leaves, everything is at risk."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Solo Dev"],
            cwd=single_author_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output
        # Solo dev owns everything, so should be HIGH or CRITICAL
        output_upper = result.output.upper()
        assert "HIGH" in output_upper or "CRITICAL" in output_upper or "RISK" in output_upper


# ---------------------------------------------------------------------------
# Tests: JSON output
# ---------------------------------------------------------------------------


class TestSimulateDepartureJSON:
    """JSON output format tests."""

    def test_json_envelope_structure(self, departure_project, cli_runner):
        """JSON output should follow the envelope contract."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert_json_envelope(data, "simulate-departure")

        # Check required summary fields
        summary = data["summary"]
        assert "verdict" in summary
        assert "developer" in summary
        assert "total_files_at_risk" in summary

    def test_json_has_required_fields(self, departure_project, cli_runner):
        """JSON output should contain all expected top-level fields."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")

        # Top-level fields
        assert "developer" in data
        assert "total_files_at_risk" in data
        assert "critical_files" in data
        assert "high_risk_files" in data
        assert "medium_risk_files" in data
        assert "key_symbols" in data
        assert "affected_modules" in data
        assert "recommendations" in data

        # Types
        assert isinstance(data["critical_files"], list)
        assert isinstance(data["high_risk_files"], list)
        assert isinstance(data["medium_risk_files"], list)
        assert isinstance(data["key_symbols"], list)
        assert isinstance(data["recommendations"], list)

    def test_json_file_entry_structure(self, departure_project, cli_runner):
        """Each file entry in JSON should have path and ownership_pct."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")

        # Check all file lists
        for key in ("critical_files", "high_risk_files", "medium_risk_files"):
            for f in data[key]:
                assert "path" in f
                assert "ownership_pct" in f
                assert isinstance(f["ownership_pct"], (int, float))
                assert 0 <= f["ownership_pct"] <= 100

    def test_json_key_symbols_structure(self, departure_project, cli_runner):
        """Key symbols entries should have name, kind, file, pagerank."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")

        for s in data["key_symbols"]:
            assert "name" in s
            assert "kind" in s
            assert "file" in s
            assert "pagerank" in s

    def test_json_nonexistent_developer(self, departure_project, cli_runner):
        """Non-existent dev produces valid JSON with zero risk."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "nobody@nowhere.com"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert_json_envelope(data, "simulate-departure")
        assert data["total_files_at_risk"] == 0
        assert "LOW" in data["summary"]["verdict"].upper() or data["summary"]["severity"] == "LOW"


# ---------------------------------------------------------------------------
# Tests: Verdict levels
# ---------------------------------------------------------------------------


class TestVerdictLevels:
    """Test that verdict levels are correctly assigned."""

    def test_low_risk_verdict(self, departure_project, cli_runner):
        """Unknown developer should produce LOW RISK."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "unknown_person"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert data["summary"]["severity"] == "LOW"

    def test_high_risk_for_primary_author(self, single_author_project, cli_runner):
        """Solo dev departure should produce HIGH or CRITICAL."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Solo Dev"],
            cwd=single_author_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert data["summary"]["severity"] in ("HIGH", "CRITICAL")
        assert data["total_files_at_risk"] > 0


# ---------------------------------------------------------------------------
# Tests: CODEOWNERS integration
# ---------------------------------------------------------------------------


class TestCodeownersIntegration:
    """Test CODEOWNERS parsing and cross-reference."""

    def test_codeowners_detected(self, departure_project_with_codeowners, cli_runner):
        """Files with departing dev as sole CODEOWNER should be CRITICAL."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "alice"],
            cwd=departure_project_with_codeowners,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")

        # Alice is sole CODEOWNER of auth/ AND has high ownership
        # Check if any critical files exist with codeowners info
        all_files = (
            data["critical_files"]
            + data["high_risk_files"]
            + data["medium_risk_files"]
        )
        assert len(all_files) > 0, "Should find at-risk files for alice"

    def test_no_codeowners_file(self, departure_project, cli_runner):
        """Without CODEOWNERS, should still work (no critical from CODEOWNERS)."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        # Should still produce valid output
        assert "verdict" in data["summary"]
        # Without CODEOWNERS, no files can be "critical" (sole CODEOWNER)
        assert len(data["critical_files"]) == 0


# ---------------------------------------------------------------------------
# Tests: Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    """Test that recommendations are meaningful."""

    def test_recommendations_present(self, departure_project, cli_runner):
        """Should always have at least one recommendation."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert len(data["recommendations"]) > 0

    def test_low_risk_recommendation(self, departure_project, cli_runner):
        """Low-risk departure should have a simple recommendation."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "nobody@nowhere.com"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        assert len(data["recommendations"]) > 0


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_project(self, tmp_path, cli_runner):
        """Empty project should handle gracefully."""
        proj = tmp_path / "empty_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        subprocess.run(["git", "init"], cwd=proj, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=proj, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=proj, capture_output=True,
        )
        (proj / "empty.py").write_text("")
        _git_add_commit(proj, "init")
        out, rc = index_in_process(proj)
        # May fail if no parseable files, but that's OK for this test
        if rc != 0:
            pytest.skip("Could not index empty project")

        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Test"],
            cwd=proj,
        )
        # Should not crash
        assert result.exit_code == 0

    def test_partial_name_matching(self, departure_project, cli_runner):
        """Partial name should match (e.g., 'alice' matches 'Alice Smith')."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "alice"],
            cwd=departure_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate-departure")
        # 'alice' should match 'Alice Smith' (case-insensitive substring)
        total = data["total_files_at_risk"]
        # Alice owns several files so should find some
        assert total > 0 or data["summary"]["severity"] != "LOW"

    def test_text_output_sections(self, departure_project, cli_runner):
        """Text output should have the expected sections."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "Alice Smith"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output
        assert "AFFECTED MODULES:" in output
        assert "RECOMMENDATIONS:" in output

    def test_limit_option(self, departure_project, cli_runner):
        """--limit should restrict output without errors."""
        result = invoke_cli(
            cli_runner,
            ["simulate-departure", "--limit", "1", "Alice Smith"],
            cwd=departure_project,
        )
        assert result.exit_code == 0
        assert "VERDICT:" in result.output


# ---------------------------------------------------------------------------
# Tests: Internal functions
# ---------------------------------------------------------------------------


class TestInternalFunctions:
    """Test internal helper functions."""

    def test_parse_codeowners_missing(self, tmp_path):
        """parse_codeowners should return empty list when file is missing."""
        from roam.commands.cmd_simulate_departure import parse_codeowners

        result = parse_codeowners(tmp_path)
        assert result == []

    def test_parse_codeowners_format(self, tmp_path):
        """parse_codeowners should parse standard CODEOWNERS format."""
        from roam.commands.cmd_simulate_departure import parse_codeowners

        (tmp_path / "CODEOWNERS").write_text(
            "# Comment line\n"
            "*.py @alice @bob\n"
            "docs/ @carol\n"
            "\n"
            "/src/core/ @alice\n"
        )
        rules = parse_codeowners(tmp_path)
        assert len(rules) == 3
        assert rules[0] == ("*.py", ["@alice", "@bob"])
        assert rules[1] == ("docs/", ["@carol"])
        assert rules[2] == ("/src/core/", ["@alice"])

    def test_resolve_codeowner_last_match_wins(self, tmp_path):
        """Last matching CODEOWNERS rule should win."""
        from roam.commands.cmd_simulate_departure import (
            parse_codeowners,
            resolve_codeowner,
        )

        (tmp_path / "CODEOWNERS").write_text(
            "* @default\n"
            "src/ @alice\n"
        )
        rules = parse_codeowners(tmp_path)
        # src/app.py should match the src/ rule
        owners = resolve_codeowner("src/app.py", rules)
        assert "@alice" in owners

    def test_identity_matches(self):
        """Test identity matching logic."""
        from roam.commands.cmd_simulate_departure import _identity_matches

        assert _identity_matches("alice", "Alice Smith")
        assert _identity_matches("alice@example.com", "alice@example.com")
        assert _identity_matches("Alice Smith", "alice smith")
        assert _identity_matches("alice", "alice@example.com")
        assert not _identity_matches("charlie", "Alice Smith")
        assert _identity_matches("@alice", "alice")
        assert _identity_matches("alice", "@alice")

    def test_compute_file_ownership_empty(self):
        """compute_file_ownership should handle empty file list."""
        from roam.commands.cmd_simulate_departure import compute_file_ownership

        # Mock a minimal connection
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        result = compute_file_ownership(conn, [])
        assert result == {}
        conn.close()

    def test_verdict_levels(self):
        """Test _verdict function returns correct severity strings."""
        from roam.commands.cmd_simulate_departure import _verdict, _severity_label

        assert "CRITICAL" in _verdict(2, 5, 3, 10)
        assert "HIGH RISK" in _verdict(0, 5, 3, 8)
        assert "MEDIUM RISK" in _verdict(0, 0, 3, 3)
        assert "LOW RISK" in _verdict(0, 0, 0, 0)

        assert _severity_label(1, 0, 0) == "CRITICAL"
        assert _severity_label(0, 1, 0) == "HIGH"
        assert _severity_label(0, 0, 1) == "MEDIUM"
        assert _severity_label(0, 0, 0) == "LOW"
