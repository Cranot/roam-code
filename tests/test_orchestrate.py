"""Tests for roam orchestrate — swarm orchestration command."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, invoke_cli, parse_json_output, assert_json_envelope


@pytest.fixture
def orch_project(project_factory):
    return project_factory({
        "auth/login.py": (
            "from auth.tokens import create_token\n"
            "def authenticate(u, p): return create_token(u)\n"
        ),
        "auth/tokens.py": (
            "def create_token(user): return 'tok'\n"
            "def verify_token(t): return True\n"
        ),
        "billing/invoice.py": (
            "from billing.tax import calc_tax\n"
            "def create_invoice(order): return calc_tax(order)\n"
        ),
        "billing/tax.py": (
            "def calc_tax(order): return order * 0.1\n"
        ),
        "api/routes.py": (
            "from auth.login import authenticate\n"
            "from billing.invoice import create_invoice\n"
            "def handle(r): authenticate(r, r); return create_invoice(r)\n"
        ),
        "models.py": (
            "class User:\n"
            "    pass\n"
            "class Order:\n"
            "    pass\n"
        ),
    })


# ---------------------------------------------------------------------------
# partition_for_agents unit tests
# ---------------------------------------------------------------------------


class TestPartitionForAgents:
    """Tests for the graph partitioning engine."""

    def test_partition_returns_agents(self, orch_project):
        """Result has an 'agents' list."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)
                assert "agents" in result
                assert isinstance(result["agents"], list)
        finally:
            os.chdir(old_cwd)

    def test_partition_correct_agent_count(self, orch_project):
        """len(agents) == n_agents."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                for n in [2, 3, 5]:
                    result = partition_for_agents(G, conn, n)
                    assert len(result["agents"]) == n, (
                        f"Expected {n} agents, got {len(result['agents'])}"
                    )
        finally:
            os.chdir(old_cwd)

    def test_partition_all_files_assigned(self, orch_project):
        """Every file with symbols appears in exactly one agent's write list."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)

                # Collect all files from the graph
                graph_files = set()
                for n in G.nodes:
                    fp = G.nodes[n].get("file_path")
                    if fp:
                        graph_files.add(fp)

                # Collect all write files across agents
                assigned_files = set()
                for agent in result["agents"]:
                    assigned_files.update(agent["write_files"])

                # Every graph file should appear in at least one agent
                for f in graph_files:
                    assert f in assigned_files, f"File {f} not assigned to any agent"
        finally:
            os.chdir(old_cwd)

    def test_partition_no_write_overlap(self, orch_project):
        """No file appears in multiple agents' write lists."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)

                seen = set()
                for agent in result["agents"]:
                    for f in agent["write_files"]:
                        assert f not in seen, (
                            f"File {f} assigned to multiple agents"
                        )
                        seen.add(f)
        finally:
            os.chdir(old_cwd)

    def test_partition_has_merge_order(self, orch_project):
        """merge_order is a list of agent IDs."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)
                assert "merge_order" in result
                assert isinstance(result["merge_order"], list)
                assert len(result["merge_order"]) == 3
                # All IDs should be 1-based agent IDs
                for aid in result["merge_order"]:
                    assert 1 <= aid <= 3
        finally:
            os.chdir(old_cwd)

    def test_partition_conflict_probability(self, orch_project):
        """conflict_probability is between 0 and 1."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)
                cp = result["conflict_probability"]
                assert 0.0 <= cp <= 1.0, f"Conflict probability {cp} out of range"
        finally:
            os.chdir(old_cwd)

    def test_partition_has_contracts(self, orch_project):
        """Each agent has a contracts list."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)
                for agent in result["agents"]:
                    assert "contracts" in agent
                    assert isinstance(agent["contracts"], list)
        finally:
            os.chdir(old_cwd)

    def test_partition_has_read_only(self, orch_project):
        """Agents have read_only_files field."""
        from roam.db.connection import open_db
        from roam.graph.builder import build_symbol_graph
        from roam.graph.partition import partition_for_agents

        old_cwd = os.getcwd()
        try:
            os.chdir(str(orch_project))
            with open_db(readonly=True) as conn:
                G = build_symbol_graph(conn)
                result = partition_for_agents(G, conn, 3)
                for agent in result["agents"]:
                    assert "read_only_files" in agent
                    assert isinstance(agent["read_only_files"], list)
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestOrchestrateCommand:
    """Tests for the `roam orchestrate` CLI command."""

    def test_cli_orchestrate_runs(self, orch_project, cli_runner):
        """Command exits with code 0."""
        result = invoke_cli(
            cli_runner, ["orchestrate", "--agents", "3"], cwd=orch_project
        )
        assert result.exit_code == 0, f"Failed:\n{result.output}"

    def test_cli_orchestrate_json(self, orch_project, cli_runner):
        """JSON output is a valid envelope with command='orchestrate'."""
        result = invoke_cli(
            cli_runner, ["orchestrate", "--agents", "3"],
            cwd=orch_project, json_mode=True,
        )
        data = parse_json_output(result, command="orchestrate")
        assert_json_envelope(data, command="orchestrate")
        assert "agents" in data
        assert "merge_order" in data
        assert "shared_interfaces" in data

    def test_cli_orchestrate_verdict(self, orch_project, cli_runner):
        """Text output starts with VERDICT."""
        result = invoke_cli(
            cli_runner, ["orchestrate", "--agents", "2"], cwd=orch_project
        )
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_cli_orchestrate_agents_flag(self, orch_project, cli_runner):
        """--agents is required."""
        result = invoke_cli(
            cli_runner, ["orchestrate"], cwd=orch_project
        )
        # Click should report missing --agents
        assert result.exit_code != 0

    def test_cli_orchestrate_with_files(self, orch_project, cli_runner):
        """--files flag restricts scope."""
        result = invoke_cli(
            cli_runner, ["orchestrate", "--agents", "2", "--files", "auth/"],
            cwd=orch_project, json_mode=True,
        )
        data = parse_json_output(result, command="orchestrate")
        assert_json_envelope(data, command="orchestrate")
        # All write files should be in auth/
        for agent in data["agents"]:
            for f in agent["write_files"]:
                assert f.startswith("auth/"), (
                    f"Expected auth/ prefix, got {f}"
                )

    def test_cli_orchestrate_help(self, cli_runner):
        """--help works."""
        from roam.cli import cli
        result = cli_runner.invoke(cli, ["orchestrate", "--help"])
        assert result.exit_code == 0
        assert "--agents" in result.output
