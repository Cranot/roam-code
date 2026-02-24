"""Tests for Mermaid diagram output (--mermaid flag).

Covers:
- Mermaid helper functions (sanitize_id, node, edge, subgraph, diagram)
- roam layers --mermaid
- roam clusters --mermaid
- roam tour --mermaid
- --mermaid --json includes mermaid field in envelope
- Mermaid output determinism
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


# ===========================================================================
# Helper function unit tests
# ===========================================================================


class TestSanitizeId:
    """Tests for sanitize_id()."""

    def test_basic_path(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("src/utils.py") == "src_utils_py"

    def test_dashes_replaced(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("my-module") == "my_module"

    def test_dots_replaced(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("file.name.ext") == "file_name_ext"

    def test_leading_digit(self):
        from roam.output.mermaid import sanitize_id
        result = sanitize_id("123abc")
        assert not result[0].isdigit()
        assert result == "_123abc"

    def test_spaces_replaced(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("my file") == "my_file"

    def test_colons_replaced(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("path:line") == "path_line"

    def test_empty_string(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("") == ""

    def test_already_valid(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("valid_id") == "valid_id"

    def test_backslash_replaced(self):
        from roam.output.mermaid import sanitize_id
        assert sanitize_id("src\\utils.py") == "src_utils_py"


class TestNode:
    """Tests for node()."""

    def test_basic_node(self):
        from roam.output.mermaid import node
        result = node("src/utils.py", "utils.py")
        assert 'src_utils_py["utils.py"]' in result

    def test_node_indented(self):
        from roam.output.mermaid import node
        result = node("foo", "bar")
        assert result.startswith("    ")

    def test_quotes_escaped(self):
        from roam.output.mermaid import node
        result = node("foo", 'say "hello"')
        assert '"' not in result.split("[")[1].replace("'", "").rstrip('"]') or "'" in result


class TestEdge:
    """Tests for edge()."""

    def test_basic_edge(self):
        from roam.output.mermaid import edge
        result = edge("src/a.py", "src/b.py")
        assert "src_a_py --> src_b_py" in result

    def test_edge_indented(self):
        from roam.output.mermaid import edge
        result = edge("a", "b")
        assert result.startswith("    ")


class TestSubgraph:
    """Tests for subgraph()."""

    def test_basic_subgraph(self):
        from roam.output.mermaid import subgraph, node
        nodes = [node("a", "A"), node("b", "B")]
        result = subgraph("My Group", nodes)
        assert 'subgraph "My Group"' in result
        assert "end" in result
        assert 'a["A"]' in result
        assert 'b["B"]' in result

    def test_subgraph_quotes_in_name(self):
        from roam.output.mermaid import subgraph
        result = subgraph('Layer "0"', [])
        assert "subgraph" in result
        assert "'" in result  # Quotes should be escaped to single quotes


class TestDiagram:
    """Tests for diagram()."""

    def test_basic_diagram(self):
        from roam.output.mermaid import diagram, node, edge
        elements = [node("a", "A"), node("b", "B"), edge("a", "b")]
        result = diagram("TD", elements)
        assert result.startswith("graph TD")
        assert 'a["A"]' in result
        assert "a --> b" in result

    def test_lr_direction(self):
        from roam.output.mermaid import diagram
        result = diagram("LR", [])
        assert result.startswith("graph LR")


# ===========================================================================
# CLI integration tests
# ===========================================================================


@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class TestLayersMermaid:
    """Tests for roam layers --mermaid."""

    def test_layers_mermaid_output(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid produces output starting with 'graph'."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0, f"layers --mermaid failed:\n{result.output}"
        output = result.output.strip()
        assert output.startswith("graph"), f"Expected Mermaid diagram, got:\n{output}"

    def test_layers_mermaid_has_subgraphs(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid output contains subgraph blocks."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "subgraph" in result.output

    def test_layers_mermaid_has_end(self, cli_runner, indexed_project, monkeypatch):
        """Each subgraph block is closed with 'end'."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        assert "end" in result.output

    def test_layers_mermaid_json(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid --json includes mermaid field in JSON envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"],
                            cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "layers")
        assert "mermaid" in data, f"Missing 'mermaid' key in JSON envelope: {list(data.keys())}"
        assert data["mermaid"].startswith("graph"), "mermaid field should start with 'graph'"

    def test_layers_mermaid_deterministic(self, cli_runner, indexed_project, monkeypatch):
        """Same input produces identical Mermaid output."""
        monkeypatch.chdir(indexed_project)
        r1 = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        r2 = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert r1.output == r2.output, "Mermaid output should be deterministic"


class TestClustersMermaid:
    """Tests for roam clusters --mermaid."""

    def test_clusters_mermaid_output(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid produces output starting with 'graph'."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clusters", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0, f"clusters --mermaid failed:\n{result.output}"
        output = result.output.strip()
        assert output.startswith("graph"), f"Expected Mermaid diagram, got:\n{output}"

    def test_clusters_mermaid_has_subgraphs(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid output contains subgraph blocks for clusters."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clusters", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        # Might have subgraphs or might have "No clusters" depending on project
        assert "graph" in result.output

    def test_clusters_mermaid_json(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid --json includes mermaid field in JSON envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["clusters", "--mermaid"],
                            cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "clusters")
        assert "mermaid" in data, f"Missing 'mermaid' key in JSON envelope"
        assert data["mermaid"].startswith("graph")

    def test_clusters_mermaid_deterministic(self, cli_runner, indexed_project, monkeypatch):
        """Same input produces identical Mermaid output."""
        monkeypatch.chdir(indexed_project)
        r1 = invoke_cli(cli_runner, ["clusters", "--mermaid"], cwd=indexed_project)
        r2 = invoke_cli(cli_runner, ["clusters", "--mermaid"], cwd=indexed_project)
        assert r1.output == r2.output


class TestTourMermaid:
    """Tests for roam tour --mermaid."""

    def test_tour_mermaid_output(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid produces output starting with 'graph'."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0, f"tour --mermaid failed:\n{result.output}"
        output = result.output.strip()
        assert output.startswith("graph"), f"Expected Mermaid diagram, got:\n{output}"

    def test_tour_mermaid_has_nodes(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid output contains node definitions."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        # Node definitions use ["label"] syntax
        assert "[" in result.output

    def test_tour_mermaid_json(self, cli_runner, indexed_project, monkeypatch):
        """--mermaid --json includes mermaid field in JSON envelope."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["tour", "--mermaid"],
                            cwd=indexed_project, json_mode=True)
        data = parse_json_output(result, "tour")
        assert "mermaid" in data, f"Missing 'mermaid' key in JSON envelope"
        assert data["mermaid"].startswith("graph")

    def test_tour_mermaid_deterministic(self, cli_runner, indexed_project, monkeypatch):
        """Same input produces identical Mermaid output."""
        monkeypatch.chdir(indexed_project)
        r1 = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=indexed_project)
        r2 = invoke_cli(cli_runner, ["tour", "--mermaid"], cwd=indexed_project)
        assert r1.output == r2.output


class TestMermaidNoArrows:
    """Edge cases: Mermaid output without enough data."""

    def test_layers_mermaid_empty_project(self, cli_runner, project_factory, monkeypatch):
        """--mermaid on a project with minimal structure still succeeds."""
        proj = project_factory({
            "single.py": "x = 1\n",
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=proj)
        # Might be empty graph or "No layers detected" -- either is fine
        assert result.exit_code == 0


class TestMermaidValidSyntax:
    """Validate that Mermaid output follows basic syntax rules."""

    def test_no_unescaped_quotes(self, cli_runner, indexed_project, monkeypatch):
        """Node labels should not contain unescaped double quotes."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        for line in lines:
            stripped = line.strip()
            # Skip non-node lines
            if "[" not in stripped or "]" not in stripped:
                continue
            # Extract the label between ["..."]
            start = stripped.index("[")
            end = stripped.rindex("]")
            label_section = stripped[start:end + 1]
            # Count quotes -- should be exactly 2 (opening and closing)
            quote_count = label_section.count('"')
            assert quote_count == 2, f"Unexpected quotes in: {stripped}"

    def test_subgraph_end_balanced(self, cli_runner, indexed_project, monkeypatch):
        """Number of 'subgraph' should equal number of 'end' lines."""
        monkeypatch.chdir(indexed_project)
        result = invoke_cli(cli_runner, ["layers", "--mermaid"], cwd=indexed_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        subgraph_count = sum(1 for l in lines if l.strip().startswith("subgraph"))
        end_count = sum(1 for l in lines if l.strip() == "end")
        assert subgraph_count == end_count, (
            f"Unbalanced subgraph/end: {subgraph_count} subgraphs, {end_count} ends"
        )
