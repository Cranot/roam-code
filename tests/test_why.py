"""Tests for roam why -- explain why a symbol matters."""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def why_project(tmp_path):
    """Python project with clear caller/callee relationships.

    Call graph:
        handle_request -> authenticate -> validate_token
        handle_request -> fetch_user
        fetch_user     -> connect_db
        connect_db     (leaf)
    """
    proj = tmp_path / "why_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "db.py").write_text(
        '"""Database layer."""\n'
        "\n"
        "\n"
        "def connect_db(dsn: str):\n"
        '    """Open a database connection."""\n'
        "    return dsn\n"
        "\n"
        "\n"
        "def query_db(conn, sql: str):\n"
        '    """Run a SQL query and return rows."""\n'
        "    return []\n"
    )

    (proj / "auth.py").write_text(
        '"""Authentication helpers."""\n'
        "from db import connect_db, query_db\n"
        "\n"
        "\n"
        "def validate_token(token: str) -> bool:\n"
        '    """Return True if token is valid."""\n'
        "    return bool(token)\n"
        "\n"
        "\n"
        "def authenticate(user: str, token: str) -> bool:\n"
        '    """Authenticate user by token."""\n'
        "    return validate_token(token)\n"
        "\n"
        "\n"
        "def fetch_user(user_id: int):\n"
        '    """Fetch a user record from the DB."""\n'
        '    conn = connect_db("sqlite:///app.db")\n'
        '    rows = query_db(conn, f"SELECT * FROM users WHERE id={user_id}")\n'
        "    return rows[0] if rows else None\n"
    )

    (proj / "api.py").write_text(
        '"""API entry point."""\n'
        "from auth import authenticate, fetch_user\n"
        "\n"
        "\n"
        "def handle_request(user: str, token: str, user_id: int):\n"
        '    """Top-level request handler."""\n'
        "    if not authenticate(user, token):\n"
        "        return None\n"
        "    return fetch_user(user_id)\n"
        "\n"
        "\n"
        "def list_users():\n"
        '    """Return all users (stub)."""\n'
        "    return []\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestWhySmoke:
    def test_exits_zero_for_known_symbol(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        assert result.exit_code == 0

    def test_exits_zero_for_leaf_symbol(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "connect_db"], cwd=why_project)
        assert result.exit_code == 0

    def test_exits_zero_for_orchestrator_symbol(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "handle_request"], cwd=why_project)
        assert result.exit_code == 0

    def test_exits_nonzero_for_unknown_symbol(self, cli_runner, why_project, monkeypatch):
        """An unknown symbol name should cause exit code 1 or produce a 'not found' message."""
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "totally_nonexistent_sym_xyz_999"], cwd=why_project)
        # Either exits non-zero or emits an error message
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_batch_mode_exits_zero(self, cli_runner, why_project, monkeypatch):
        """Passing multiple symbol names should work (batch table mode)."""
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate", "fetch_user"], cwd=why_project)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON envelope tests
# ---------------------------------------------------------------------------


class TestWhyJSON:
    def test_json_envelope_structure(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert_json_envelope(data, "why")

    def test_json_command_field_is_why(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert data["command"] == "why"

    def test_json_summary_has_symbols_count(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert "symbols" in data["summary"]
        assert data["summary"]["symbols"] == 1

    def test_json_summary_has_critical_count(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert "critical" in data["summary"]
        assert isinstance(data["summary"]["critical"], int)

    def test_json_has_symbols_list(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert "symbols" in data
        assert isinstance(data["symbols"], list)
        assert len(data["symbols"]) == 1

    def test_json_symbol_entry_has_expected_fields(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        sym = data["symbols"][0]
        required = {"name", "role", "fan_in", "fan_out", "reach", "verdict"}
        missing = required - set(sym.keys())
        assert not missing, f"Symbol entry missing keys: {missing}"

    def test_json_symbol_verdict_is_string(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        sym = data["symbols"][0]
        assert isinstance(sym.get("verdict"), str)
        assert len(sym["verdict"]) > 0

    def test_json_batch_mode_returns_multiple_symbols(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate", "fetch_user"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        assert data["summary"]["symbols"] == 2
        assert len(data["symbols"]) == 2

    def test_json_unknown_symbol_returns_error_field(self, cli_runner, why_project, monkeypatch):
        """An unresolvable symbol in a batch should have an 'error' field, not crash."""
        monkeypatch.chdir(why_project)
        result = invoke_cli(
            cli_runner,
            ["why", "authenticate", "totally_nonexistent_sym_xyz"],
            cwd=why_project,
            json_mode=True,
        )
        # The command may exit 0 in batch JSON mode; the unknown symbol should be flagged
        if result.exit_code == 0:
            data = parse_json_output(result, "why")
            syms = data.get("symbols", [])
            errors = [s for s in syms if "error" in s]
            assert len(errors) >= 1, "Expected at least one error entry for unknown symbol"

    def test_json_reach_is_non_negative_int(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "handle_request"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        sym = data["symbols"][0]
        assert isinstance(sym.get("reach"), int)
        assert sym["reach"] >= 0

    def test_json_fan_in_and_fan_out_are_non_negative(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project, json_mode=True)
        data = parse_json_output(result, "why")
        sym = data["symbols"][0]
        assert isinstance(sym.get("fan_in"), int)
        assert isinstance(sym.get("fan_out"), int)
        assert sym["fan_in"] >= 0
        assert sym["fan_out"] >= 0


# ---------------------------------------------------------------------------
# Text output tests
# ---------------------------------------------------------------------------


class TestWhyText:
    def test_verdict_line_present_for_single_symbol(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        # Single-symbol mode emits VERDICT: indented under the symbol header
        assert "VERDICT:" in result.output

    def test_role_line_present(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        assert "ROLE:" in result.output

    def test_reach_line_present(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        assert "REACH:" in result.output

    def test_critical_line_present(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        assert "CRITICAL:" in result.output

    def test_symbol_name_appears_in_output(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate"], cwd=why_project)
        assert "authenticate" in result.output

    def test_unknown_symbol_shows_error_message(self, cli_runner, why_project, monkeypatch):
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "totally_nonexistent_sym_xyz_999"], cwd=why_project)
        # Should emit some kind of "not found" text, not crash silently
        assert "not found" in result.output.lower() or "Symbol not found" in result.output or result.exit_code != 0

    def test_batch_mode_shows_table(self, cli_runner, why_project, monkeypatch):
        """Batch mode (2+ symbols) renders a compact table instead of detailed output."""
        monkeypatch.chdir(why_project)
        result = invoke_cli(cli_runner, ["why", "authenticate", "fetch_user"], cwd=why_project)
        assert result.exit_code == 0
        # Both symbol names should appear somewhere in the table
        assert "authenticate" in result.output
        assert "fetch_user" in result.output
