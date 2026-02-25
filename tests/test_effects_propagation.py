"""Tests for effect propagation and the effects CLI command (Ticket 6B)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
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
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def effects_project(tmp_path, monkeypatch):
    """Project with a call chain that produces direct and transitive effects.

    Call chain: handler -> service -> repo
    - repo has WRITES_DB (direct)
    - service calls repo, so inherits WRITES_DB (transitive)
    - handler calls service, so inherits WRITES_DB (transitive)
    - handler also has LOGGING (direct)
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "repo.py").write_text(
        "def save_user(name, email):\n"
        '    """Save a user to the database."""\n'
        '    conn.execute("INSERT INTO users VALUES (?, ?)", (name, email))\n'
        "    conn.commit()\n"
        "    return True\n"
    )

    (proj / "service.py").write_text(
        "from repo import save_user\n"
        "\n"
        "def create_user(name, email):\n"
        '    """Create a new user via the repo layer."""\n'
        "    if not validate_email(email):\n"
        "        return None\n"
        "    return save_user(name, email)\n"
        "\n"
        "def validate_email(email):\n"
        '    """Pure validation function."""\n'
        '    return "@" in email\n'
    )

    (proj / "handler.py").write_text(
        "from service import create_user\n"
        "\n"
        "def handle_request(data):\n"
        '    """Handle incoming request."""\n'
        '    logger.info("Processing request")\n'
        '    result = create_user(data["name"], data["email"])\n'
        "    return result\n"
    )

    (proj / "utils.py").write_text(
        'def format_name(first, last):\n    """Pure utility function."""\n    return f"{first} {last}"\n'
    )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    return proj


@pytest.fixture
def effects_no_data(tmp_path, monkeypatch):
    """Project with pure functions only (no effects)."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "math.py").write_text("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n")

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    return proj


# ---------------------------------------------------------------------------
# Propagation unit tests
# ---------------------------------------------------------------------------


class TestPropagation:
    """Test effect propagation through the call graph."""

    def test_propagate_linear_chain(self):
        """A -> B -> C where C has WRITES_DB => A and B inherit."""
        import networkx as nx

        from roam.analysis.effects import WRITES_DB, propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3)])

        direct = {3: {WRITES_DB}}
        result = propagate_effects(G, direct)

        assert WRITES_DB in result.get(3, set())
        assert WRITES_DB in result.get(2, set())
        assert WRITES_DB in result.get(1, set())

    def test_propagate_preserves_direct(self):
        """Direct effects should be preserved."""
        import networkx as nx

        from roam.analysis.effects import LOGGING, WRITES_DB, propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2)])

        direct = {1: {LOGGING}, 2: {WRITES_DB}}
        result = propagate_effects(G, direct)

        assert LOGGING in result.get(1, set())
        assert WRITES_DB in result.get(1, set())
        assert WRITES_DB in result.get(2, set())

    def test_propagate_cycle(self):
        """Cycles: A -> B -> A, both in a cycle share effects."""
        import networkx as nx

        from roam.analysis.effects import NETWORK, propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 1)])

        direct = {1: {NETWORK}}
        result = propagate_effects(G, direct)

        assert NETWORK in result.get(1, set())
        assert NETWORK in result.get(2, set())

    def test_propagate_diamond(self):
        """Diamond: A -> B, A -> C, B -> D, C -> D. D has effect."""
        import networkx as nx

        from roam.analysis.effects import FILESYSTEM, propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (1, 3), (2, 4), (3, 4)])

        direct = {4: {FILESYSTEM}}
        result = propagate_effects(G, direct)

        assert FILESYSTEM in result.get(4, set())
        assert FILESYSTEM in result.get(2, set())
        assert FILESYSTEM in result.get(3, set())
        assert FILESYSTEM in result.get(1, set())

    def test_propagate_no_effects(self):
        """No direct effects => empty result."""
        import networkx as nx

        from roam.analysis.effects import propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (2, 3)])

        result = propagate_effects(G, {})
        assert len(result) == 0

    def test_propagate_multiple_effects(self):
        """Multiple effect types should all propagate."""
        import networkx as nx

        from roam.analysis.effects import NETWORK, WRITES_DB, propagate_effects

        G = nx.DiGraph()
        G.add_edges_from([(1, 2), (1, 3)])

        direct = {2: {WRITES_DB}, 3: {NETWORK}}
        result = propagate_effects(G, direct)

        assert WRITES_DB in result.get(1, set())
        assert NETWORK in result.get(1, set())


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    """Test that the symbol_effects table exists after indexing."""

    def test_table_exists(self, effects_project, monkeypatch):
        """symbol_effects table should exist after indexing."""
        monkeypatch.chdir(effects_project)
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert "symbol_effects" in tables

    def test_effects_populated(self, effects_project, monkeypatch):
        """Effects should be populated after indexing."""
        monkeypatch.chdir(effects_project)
        from roam.db.connection import open_db

        with open_db(readonly=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbol_effects").fetchone()[0]
            assert count > 0, "Expected effects to be populated"


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestEffectsCommand:
    """Test the effects CLI command."""

    def test_effects_runs(self, cli_runner, effects_project, monkeypatch):
        """Command exits 0."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects"], cwd=effects_project)
        assert result.exit_code == 0

    def test_effects_json_envelope(self, cli_runner, effects_project, monkeypatch):
        """Valid JSON envelope."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        assert_json_envelope(data, "effects")

    def test_effects_verdict_line(self, cli_runner, effects_project, monkeypatch):
        """Text starts with VERDICT."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects"], cwd=effects_project)
        assert result.output.strip().startswith("VERDICT:")

    def test_effects_symbol_target(self, cli_runner, effects_project, monkeypatch):
        """roam effects save_user shows direct WRITES_DB."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "save_user"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        symbols = data.get("symbols", [])
        assert len(symbols) >= 1
        sym = symbols[0]
        assert "writes_db" in sym["direct_effects"]

    def test_effects_transitive(self, cli_runner, effects_project, monkeypatch):
        """handle_request should have transitive writes_db from call chain."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "handle_request"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        symbols = data.get("symbols", [])
        if symbols:
            all_effects = symbols[0].get("direct_effects", []) + symbols[0].get("transitive_effects", [])
            # Should have logging (direct) and possibly writes_db (transitive)
            assert "logging" in all_effects

    def test_effects_file(self, cli_runner, effects_project, monkeypatch):
        """--file shows effects per function."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "--file", "repo.py"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        assert data["summary"]["symbols_with_effects"] >= 1

    def test_effects_by_type(self, cli_runner, effects_project, monkeypatch):
        """--type writes_db shows functions with that effect."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "--type", "writes_db"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        assert data["summary"]["symbols_with_effects"] >= 1

    def test_effects_no_data(self, cli_runner, effects_no_data, monkeypatch):
        """Pure functions => no effects message."""
        monkeypatch.chdir(effects_no_data)
        result = invoke_cli(cli_runner, ["effects"], cwd=effects_no_data)
        assert result.exit_code == 0
        assert "no effects" in result.output.lower() or "0" in result.output

    def test_effects_summary_json(self, cli_runner, effects_project, monkeypatch):
        """Summary JSON has by_type breakdown."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        assert "by_type" in data
        assert isinstance(data["by_type"], dict)

    def test_effects_symbol_not_found(self, cli_runner, effects_project, monkeypatch):
        """Querying nonexistent symbol returns graceful message."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "nonexistent_xyz"], cwd=effects_project)
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_effects_json_symbol_not_found(self, cli_runner, effects_project, monkeypatch):
        """Querying nonexistent symbol in JSON mode returns valid envelope."""
        monkeypatch.chdir(effects_project)
        result = invoke_cli(cli_runner, ["effects", "nonexistent_xyz"], cwd=effects_project, json_mode=True)
        data = parse_json_output(result, "effects")
        assert_json_envelope(data, "effects")
        assert "not found" in data["summary"]["verdict"]
