"""Tests for roam simulate -- counterfactual architecture simulator."""

from __future__ import annotations

import networkx as nx
import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixture: multi-file project for simulation
# ---------------------------------------------------------------------------


@pytest.fixture
def sim_project(tmp_path):
    """A multi-file project suitable for simulate testing.

    auth/login.py: authenticate(), _verify_password()
    auth/tokens.py: create_token() -- calls authenticate
    billing/charge.py: process_charge() -- calls create_token (cross-cluster)
    billing/invoice.py: create_invoice(), _calculate_total()
    api.py: handle_purchase() -- calls create_token + process_charge
    """
    proj = tmp_path / "sim_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    auth = proj / "auth"
    auth.mkdir()
    (auth / "login.py").write_text(
        "def authenticate(user, password):\n"
        '    """Authenticate a user."""\n'
        "    if not _verify_password(password):\n"
        "        return None\n"
        "    return user\n"
        "\n"
        "def _verify_password(password):\n"
        '    """Check password strength."""\n'
        "    return len(password) >= 8\n"
    )
    (auth / "tokens.py").write_text(
        "from auth.login import authenticate\n"
        "\n"
        "def create_token(user, password):\n"
        '    """Create an auth token."""\n'
        "    auth_user = authenticate(user, password)\n"
        "    if auth_user:\n"
        '        return {"token": "abc123", "user": auth_user}\n'
        "    return None\n"
    )

    billing = proj / "billing"
    billing.mkdir()
    (billing / "charge.py").write_text(
        "from auth.tokens import create_token\n"
        "\n"
        "def process_charge(amount, user, password):\n"
        '    """Process a billing charge."""\n'
        "    token = create_token(user, password)\n"
        "    if not token:\n"
        '        raise ValueError("auth failed")\n'
        '    return {"charged": amount, "token": token}\n'
    )
    (billing / "invoice.py").write_text(
        "def create_invoice(items):\n"
        '    """Create an invoice from items."""\n'
        "    total = _calculate_total(items)\n"
        '    return {"items": items, "total": total}\n'
        "\n"
        "def _calculate_total(items):\n"
        '    """Sum up item prices."""\n'
        '    return sum(item["price"] for item in items)\n'
    )

    (proj / "api.py").write_text(
        "from auth.tokens import create_token\n"
        "from billing.charge import process_charge\n"
        "\n"
        "def handle_purchase(user, password, amount):\n"
        '    """Handle a purchase request."""\n'
        "    token = create_token(user, password)\n"
        "    if not token:\n"
        "        return None\n"
        "    result = process_charge(amount, user, password)\n"
        "    return result\n"
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ===========================================================================
# Core engine tests
# ===========================================================================


class TestComputeGraphMetrics:
    """Test compute_graph_metrics returns correct structure."""

    def test_compute_graph_metrics(self):
        """Returns dict with all expected keys."""
        from roam.graph.simulate import compute_graph_metrics

        G = nx.DiGraph()
        G.add_node(1, name="a", kind="function", file_path="a.py")
        G.add_node(2, name="b", kind="function", file_path="b.py")
        G.add_node(3, name="c", kind="function", file_path="c.py")
        G.add_edge(1, 2, kind="calls")
        G.add_edge(2, 3, kind="calls")

        metrics = compute_graph_metrics(G)
        expected_keys = {
            "health_score",
            "nodes",
            "edges",
            "cycles",
            "tangle_ratio",
            "layer_violations",
            "modularity",
            "fiedler",
            "propagation_cost",
            "god_components",
            "bottlenecks",
        }
        assert expected_keys == set(metrics.keys())
        assert metrics["nodes"] == 3
        assert metrics["edges"] == 2

    def test_empty_graph(self):
        """Empty graph returns zeros."""
        from roam.graph.simulate import compute_graph_metrics

        G = nx.DiGraph()
        metrics = compute_graph_metrics(G)
        assert metrics["nodes"] == 0
        assert metrics["edges"] == 0
        assert metrics["health_score"] == 100


class TestMetricDelta:
    """Test metric_delta computes deltas correctly."""

    def test_metric_delta(self):
        """Computes deltas with correct direction."""
        from roam.graph.simulate import metric_delta

        before = {"health_score": 70, "cycles": 2, "modularity": 0.4}
        after = {"health_score": 80, "cycles": 1, "modularity": 0.5}
        deltas = metric_delta(before, after)

        assert deltas["health_score"]["delta"] == 10
        assert deltas["health_score"]["direction"] == "improved"
        assert deltas["cycles"]["delta"] == -1
        assert deltas["cycles"]["direction"] == "improved"
        assert deltas["modularity"]["direction"] == "improved"

    def test_unchanged_metrics(self):
        """Unchanged metrics get direction='unchanged'."""
        from roam.graph.simulate import metric_delta

        before = {"health_score": 70, "cycles": 2}
        after = {"health_score": 70, "cycles": 2}
        deltas = metric_delta(before, after)
        assert deltas["health_score"]["direction"] == "unchanged"
        assert deltas["cycles"]["direction"] == "unchanged"

    def test_degraded_direction(self):
        """Degraded metrics get correct direction."""
        from roam.graph.simulate import metric_delta

        before = {"health_score": 80, "cycles": 1}
        after = {"health_score": 70, "cycles": 3}
        deltas = metric_delta(before, after)
        assert deltas["health_score"]["direction"] == "degraded"
        assert deltas["cycles"]["direction"] == "degraded"


class TestCloneGraph:
    """Test clone_graph independence."""

    def test_clone_graph_independent(self):
        """Modifying clone doesn't affect original."""
        from roam.graph.simulate import clone_graph

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="a.py")
        G.add_node(2, name="b", file_path="b.py")
        G.add_edge(1, 2, kind="calls")

        G2 = clone_graph(G)
        G2.remove_node(2)

        assert 2 in G
        assert 2 not in G2
        assert G.number_of_edges() == 1
        assert G2.number_of_edges() == 0


class TestTransforms:
    """Test transform functions on simple graphs."""

    def test_apply_move_changes_file(self):
        """Move changes node's file_path."""
        from roam.graph.simulate import apply_move

        G = nx.DiGraph()
        G.add_node(1, name="foo", kind="function", file_path="old.py")
        G.add_node(2, name="bar", kind="function", file_path="other.py")
        G.add_edge(2, 1, kind="calls")

        result = apply_move(G, 1, "new.py")
        assert G.nodes[1]["file_path"] == "new.py"
        assert result["operation"] == "move"
        assert result["from_file"] == "old.py"
        assert result["to_file"] == "new.py"
        # Edge still exists
        assert G.has_edge(2, 1)

    def test_apply_delete_removes_nodes(self):
        """Delete removes nodes and edges."""
        from roam.graph.simulate import apply_delete

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="a.py")
        G.add_node(2, name="b", file_path="b.py")
        G.add_node(3, name="c", file_path="c.py")
        G.add_edge(1, 2, kind="calls")
        G.add_edge(2, 3, kind="calls")

        result = apply_delete(G, [2])
        assert 2 not in G
        assert result["affected"] == 1
        assert result["removed_edges"] == 2

    def test_apply_merge_unifies_files(self):
        """Merge moves all file_b nodes to file_a."""
        from roam.graph.simulate import apply_merge

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="file_a.py")
        G.add_node(2, name="b", file_path="file_b.py")
        G.add_node(3, name="c", file_path="file_b.py")

        result = apply_merge(G, "file_a.py", "file_b.py")
        assert G.nodes[2]["file_path"] == "file_a.py"
        assert G.nodes[3]["file_path"] == "file_a.py"
        assert result["affected"] == 2

    def test_apply_extract(self):
        """Extract moves node and private callees."""
        from roam.graph.simulate import apply_extract

        G = nx.DiGraph()
        G.add_node(1, name="main_func", file_path="src.py")
        G.add_node(2, name="_helper", file_path="src.py")
        G.add_node(3, name="public_dep", file_path="src.py")
        G.add_edge(1, 2, kind="calls")
        G.add_edge(1, 3, kind="calls")

        result = apply_extract(G, 1, "new.py")
        assert G.nodes[1]["file_path"] == "new.py"
        assert G.nodes[2]["file_path"] == "new.py"  # private callee moved
        assert G.nodes[3]["file_path"] == "src.py"  # public callee stays
        assert "main_func" in result["extracted"]
        assert "_helper" in result["extracted"]


# ===========================================================================
# CLI smoke tests
# ===========================================================================


class TestCLISmoke:
    """Smoke tests for CLI subcommands."""

    def test_simulate_move_runs(self, sim_project, cli_runner, monkeypatch):
        """simulate move exits 0."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "move", "authenticate", "new_auth.py"], cwd=sim_project)
        assert result.exit_code == 0

    def test_simulate_extract_runs(self, sim_project, cli_runner, monkeypatch):
        """simulate extract exits 0."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "extract", "authenticate", "new_auth.py"], cwd=sim_project)
        assert result.exit_code == 0

    def test_simulate_merge_runs(self, sim_project, cli_runner, monkeypatch):
        """simulate merge exits 0."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "merge", "auth/login.py", "auth/tokens.py"], cwd=sim_project)
        assert result.exit_code == 0

    def test_simulate_delete_runs(self, sim_project, cli_runner, monkeypatch):
        """simulate delete exits 0."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "delete", "_calculate_total"], cwd=sim_project)
        assert result.exit_code == 0


# ===========================================================================
# JSON contract tests
# ===========================================================================


class TestJSONContract:
    """Verify JSON envelope and required fields."""

    def test_simulate_move_json(self, sim_project, cli_runner, monkeypatch):
        """Move JSON has valid envelope."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(
            cli_runner,
            ["simulate", "move", "authenticate", "new_auth.py"],
            cwd=sim_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate")
        assert_json_envelope(data, "simulate")

    def test_simulate_delete_json(self, sim_project, cli_runner, monkeypatch):
        """Delete JSON has valid envelope."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "delete", "_calculate_total"], cwd=sim_project, json_mode=True)
        data = parse_json_output(result, "simulate")
        assert_json_envelope(data, "simulate")

    def test_simulate_json_has_metrics(self, sim_project, cli_runner, monkeypatch):
        """JSON output contains metrics dict."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(
            cli_runner,
            ["simulate", "move", "authenticate", "new_auth.py"],
            cwd=sim_project,
            json_mode=True,
        )
        data = parse_json_output(result, "simulate")
        assert "metrics" in data
        metrics = data["metrics"]
        assert "health_score" in metrics
        assert "before" in metrics["health_score"]
        assert "after" in metrics["health_score"]
        assert "direction" in metrics["health_score"]


# ===========================================================================
# Metric correctness tests
# ===========================================================================


class TestMetricCorrectness:
    """Verify metric changes make sense after transforms."""

    def test_delete_reduces_node_count(self):
        """Node count decreases after delete."""
        from roam.graph.simulate import (
            apply_delete,
            clone_graph,
            compute_graph_metrics,
        )

        G = nx.DiGraph()
        for i in range(5):
            G.add_node(i, name=f"n{i}", file_path="a.py")
        G.add_edge(0, 1, kind="calls")
        G.add_edge(1, 2, kind="calls")

        before = compute_graph_metrics(G)
        G_sim = clone_graph(G)
        apply_delete(G_sim, [3, 4])
        after = compute_graph_metrics(G_sim)

        assert after["nodes"] == before["nodes"] - 2

    def test_move_preserves_edge_count(self):
        """Edge count unchanged after move."""
        from roam.graph.simulate import (
            apply_move,
            clone_graph,
            compute_graph_metrics,
        )

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="old.py")
        G.add_node(2, name="b", file_path="other.py")
        G.add_edge(1, 2, kind="calls")

        before = compute_graph_metrics(G)
        G_sim = clone_graph(G)
        apply_move(G_sim, 1, "new.py")
        after = compute_graph_metrics(G_sim)

        assert after["edges"] == before["edges"]

    def test_delete_reduces_edges(self):
        """Edge count decreases after deleting a connected node."""
        from roam.graph.simulate import (
            apply_delete,
            clone_graph,
            compute_graph_metrics,
        )

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="a.py")
        G.add_node(2, name="b", file_path="b.py")
        G.add_node(3, name="c", file_path="c.py")
        G.add_edge(1, 2, kind="calls")
        G.add_edge(2, 3, kind="calls")

        before = compute_graph_metrics(G)
        G_sim = clone_graph(G)
        apply_delete(G_sim, [2])
        after = compute_graph_metrics(G_sim)

        assert after["edges"] < before["edges"]

    def test_metrics_have_direction(self):
        """Each metric delta has a direction field."""
        from roam.graph.simulate import (
            apply_delete,
            clone_graph,
            compute_graph_metrics,
            metric_delta,
        )

        G = nx.DiGraph()
        G.add_node(1, name="a", file_path="a.py")
        G.add_node(2, name="b", file_path="b.py")
        G.add_edge(1, 2, kind="calls")

        before = compute_graph_metrics(G)
        G_sim = clone_graph(G)
        apply_delete(G_sim, [2])
        after = compute_graph_metrics(G_sim)
        deltas = metric_delta(before, after)

        for key, d in deltas.items():
            assert "direction" in d, f"Missing direction for {key}"
            assert d["direction"] in ("improved", "degraded", "unchanged", "changed")


# ===========================================================================
# Edge case tests
# ===========================================================================


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_simulate_unknown_symbol(self, sim_project, cli_runner, monkeypatch):
        """Graceful error for unknown symbol."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "move", "nonexistent_xyz_42", "target.py"], cwd=sim_project)
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_simulate_verdict_line(self, sim_project, cli_runner, monkeypatch):
        """Text output starts with VERDICT:."""
        monkeypatch.chdir(sim_project)
        result = invoke_cli(cli_runner, ["simulate", "move", "authenticate", "new_auth.py"], cwd=sim_project)
        assert result.exit_code == 0
        assert result.output.strip().startswith("VERDICT:")

    def test_simulate_help(self, cli_runner):
        """--help works for simulate group."""
        from roam.cli import cli

        result = cli_runner.invoke(cli, ["simulate", "--help"])
        assert result.exit_code == 0
        assert "move" in result.output
        assert "extract" in result.output
        assert "merge" in result.output
        assert "delete" in result.output
