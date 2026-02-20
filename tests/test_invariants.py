"""Tests for roam invariants command: invariant discovery for symbols."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    parse_json_output,
    assert_json_envelope,
    index_in_process,
    git_init,
)


# ===========================================================================
# Helper: invoke the invariants command directly (not via cli.py)
# ===========================================================================


def run_invariants(proj, args=None, json_mode=False):
    """Invoke the invariants command directly via CliRunner.

    Bypasses cli.py (which we cannot modify) and invokes the command
    function directly from cmd_invariants.
    """
    from roam.commands.cmd_invariants import invariants

    runner = CliRunner()
    full_args = list(args) if args else []

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        obj = {"json": json_mode}
        result = runner.invoke(
            invariants, full_args, obj=obj, catch_exceptions=False
        )
    finally:
        os.chdir(old_cwd)
    return result


# ===========================================================================
# Fixture
# ===========================================================================


@pytest.fixture
def invariants_project(tmp_path):
    """Small Python project with clear caller patterns for invariant testing.

    Layout:
      models.py  -- User class with __init__, display, validate methods
      service.py -- create_user (called by api + admin), get_display, _private_helper
      api.py     -- calls create_user and get_display
      admin.py   -- calls create_user
    """
    proj = tmp_path / "inv_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display(self):\n"
        "        return self.name.title()\n"
        "\n"
        "    def validate(self):\n"
        "        return '@' in self.email\n"
    )
    (proj / "service.py").write_text(
        "from models import User\n\n"
        "def create_user(name, email):\n"
        "    user = User(name, email)\n"
        "    if not user.validate():\n"
        "        raise ValueError('bad email')\n"
        "    return user\n\n"
        "def get_display(user):\n"
        "    return user.display()\n\n"
        "def _private_helper():\n"
        "    return 42\n"
    )
    (proj / "api.py").write_text(
        "from service import create_user, get_display\n\n"
        "def handle_create(data):\n"
        "    user = create_user(data['name'], data['email'])\n"
        "    return get_display(user)\n\n"
        "def handle_list():\n"
        "    return []\n"
    )
    (proj / "admin.py").write_text(
        "from service import create_user\n\n"
        "def admin_create(name, email):\n"
        "    return create_user(name, email)\n"
    )

    git_init(proj)
    old = os.getcwd()
    os.chdir(str(proj))
    index_in_process(proj)
    os.chdir(old)
    return proj


# ===========================================================================
# Tests
# ===========================================================================


class TestInvariantsCommand:

    def test_invariants_runs(self, invariants_project):
        """Command exits 0 when given a valid target symbol."""
        result = run_invariants(invariants_project, ["create_user"])
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        )

    def test_invariants_json_envelope(self, invariants_project):
        """JSON output follows the roam envelope contract."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert_json_envelope(data, "invariants")

    def test_invariants_has_symbols(self, invariants_project):
        """JSON output contains a 'symbols' list with at least one entry."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "symbols" in data, "Expected 'symbols' key in JSON output"
        assert isinstance(data["symbols"], list)
        assert len(data["symbols"]) >= 1

    def test_invariants_symbol_fields(self, invariants_project):
        """Each symbol entry has required fields: name, kind, caller_count, invariants, breaking_risk."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        sym = data["symbols"][0]
        for field in ("name", "kind", "caller_count", "invariants", "breaking_risk"):
            assert field in sym, f"Missing field '{field}' in symbol entry"

    def test_invariants_invariant_fields(self, invariants_project):
        """Each invariant entry has required fields: type, description, stability, detail."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        sym = data["symbols"][0]
        assert len(sym["invariants"]) >= 1, "Expected at least one invariant"
        for inv in sym["invariants"]:
            for field in ("type", "description", "stability", "detail"):
                assert field in inv, f"Missing field '{field}' in invariant entry"

    def test_invariants_caller_count(self, invariants_project):
        """create_user is called by at least two callers (api.py + admin.py)."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        sym = data["symbols"][0]
        assert sym["caller_count"] >= 2, (
            f"Expected create_user to have >= 2 callers, got {sym['caller_count']}"
        )

    def test_invariants_file_spread(self, invariants_project):
        """create_user is used across at least 2 distinct files."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        sym = data["symbols"][0]
        assert sym["file_spread"] >= 2, (
            f"Expected create_user file_spread >= 2, got {sym['file_spread']}"
        )

    def test_invariants_public_api_flag(self, invariants_project):
        """--public-api returns multiple symbols from the project."""
        result = run_invariants(invariants_project, ["--public-api"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["symbols"]) > 1, (
            f"Expected multiple symbols with --public-api, got {len(data['symbols'])}"
        )

    def test_invariants_breaking_risk_flag(self, invariants_project):
        """--breaking-risk returns symbols sorted by breaking_risk descending."""
        result = run_invariants(invariants_project, ["--breaking-risk"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        symbols = data["symbols"]
        assert len(symbols) >= 1
        # Verify descending order
        risks = [s["breaking_risk"] for s in symbols]
        assert risks == sorted(risks, reverse=True), (
            f"Expected breaking_risk sorted descending, got {risks}"
        )

    def test_invariants_verdict_line(self, invariants_project):
        """Text output starts with 'VERDICT:'."""
        result = run_invariants(invariants_project, ["create_user"])
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:"), (
            f"Expected output to start with 'VERDICT:', got:\n{result.output[:200]}"
        )

    def test_invariants_no_target(self, invariants_project):
        """Running with no target and no flags gives a non-zero exit code."""
        result = run_invariants(invariants_project, [])
        assert result.exit_code != 0, (
            "Expected non-zero exit code when no target/flag given"
        )

    def test_invariants_not_found(self, invariants_project):
        """Unknown symbol name gives exit 0 with a 'no symbols' verdict."""
        result = run_invariants(
            invariants_project, ["totally_unknown_symbol_xyz"], json_mode=True
        )
        # Should exit 0 but report 0 symbols
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["symbols_analyzed"] == 0

    def test_invariants_signature_contract(self, invariants_project):
        """Symbols that have a signature get a SIGNATURE invariant type."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        sym = data["symbols"][0]
        inv_types = [inv["type"] for inv in sym["invariants"]]
        # create_user has a signature -> SIGNATURE invariant should be present
        # (only when signature column is populated by the indexer)
        if sym.get("signature"):
            assert "SIGNATURE" in inv_types, (
                f"Expected SIGNATURE invariant for symbol with signature, got {inv_types}"
            )

    def test_invariants_file_target(self, invariants_project):
        """Passing a file path as target returns invariants for all symbols in that file."""
        result = run_invariants(invariants_project, ["service.py"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        # service.py has create_user, get_display, _private_helper (3 functions)
        assert data["summary"]["symbols_analyzed"] >= 2, (
            f"Expected >= 2 symbols from service.py file target,"
            f" got {data['summary']['symbols_analyzed']}"
        )
        names = [s["name"] for s in data["symbols"]]
        assert any("create_user" in n for n in names), (
            f"Expected create_user in symbols from service.py, got {names}"
        )

    def test_invariants_summary_fields(self, invariants_project):
        """JSON summary contains required fields: verdict, symbols_analyzed, total_invariants, high_risk_count."""
        result = run_invariants(invariants_project, ["create_user"], json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        summary = data["summary"]
        for field in ("verdict", "symbols_analyzed", "total_invariants", "high_risk_count"):
            assert field in summary, f"Missing field '{field}' in summary"
        assert isinstance(summary["verdict"], str)
        assert summary["symbols_analyzed"] >= 1
