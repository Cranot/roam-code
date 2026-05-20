"""Tests for roam sbom -- Software Bill of Materials generation."""

from __future__ import annotations

import json

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def sbom_project(tmp_path):
    """Project with a requirements.txt for dependency discovery."""
    proj = tmp_path / "sbom_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("import requests\n\ndef fetch(url):\n    return requests.get(url)\n")
    (proj / "requirements.txt").write_text("requests==2.31.0\nclick>=8.0\n")
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def sbom_project_no_deps(tmp_path):
    """Project with no dependency manifests."""
    proj = tmp_path / "sbom_empty"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("x = 1\n")
    git_init(proj)
    index_in_process(proj)
    return proj


class TestSbomSmoke:
    def test_exits_zero(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert result.exit_code == 0

    def test_no_deps_exits_zero(self, cli_runner, sbom_project_no_deps, monkeypatch):
        monkeypatch.chdir(sbom_project_no_deps)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project_no_deps)
        assert result.exit_code == 0

    def test_no_reachability_flag(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom", "--no-reachability"], cwd=sbom_project)
        assert result.exit_code == 0

    def test_spdx_format(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom", "--format", "spdx"], cwd=sbom_project)
        assert result.exit_code == 0


class TestSbomJSON:
    def test_json_envelope(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        assert_json_envelope(data, "sbom")

    def test_json_summary_has_verdict(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        assert "verdict" in data["summary"]

    def test_json_has_sbom_data(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project, json_mode=True)
        data = parse_json_output(result, "sbom")
        # Should contain SBOM document
        assert (
            "sbom" in data or "document" in data or "components" in data["summary"] or "dependencies" in data["summary"]
        )


class TestSbomText:
    def test_verdict_line(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert "VERDICT:" in result.output

    def test_output_contains_dependency(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        result = invoke_cli(cli_runner, ["sbom"], cwd=sbom_project)
        assert "requests" in result.output.lower()


class TestSbomOutputFile:
    def test_write_to_file(self, cli_runner, sbom_project, monkeypatch):
        monkeypatch.chdir(sbom_project)
        out_path = sbom_project / "sbom.json"
        result = invoke_cli(cli_runner, ["sbom", "-o", str(out_path)], cwd=sbom_project)
        assert result.exit_code == 0
        if out_path.exists():
            content = json.loads(out_path.read_text(encoding="utf-8"))
            assert isinstance(content, dict)


class TestSbomReachabilityGraph:
    """Hand-built-graph unit tests for the reverse-BFS reachability helpers.

    Pins the O(V+E)-per-matched-node reverse traversal (``_entry_ancestors`` /
    ``_trace_entry_reach``) against the historical per-(entry, node)
    ``nx.has_path`` semantics: a node is reachable iff SOME in-degree-0 entry
    has a path to it, and ``entry_points`` is the reaching-entry set in
    canonical entry order. Replaces the quadratic loop that timed out on the
    ~14k-symbol roam-code corpus (>45s).
    """

    @staticmethod
    def _graph():
        import networkx as nx

        # Two entry points (in-degree 0): 1 and 2.
        #   1 -> 3 -> 4        (4 reachable from entry 1)
        #   2 -> 5             (5 reachable from entry 2)
        #   6                  (isolated: in-degree 0 AND out-degree 0 -> entry,
        #                        trivially reaches only itself)
        #   7 -> 8, 8 unreachable from any entry because 7 has an incoming edge
        #                        from 5 (so 7 is NOT an entry) and nothing else
        #                        feeds 9
        #   9                  (in-degree 1 from 8 -> reachable via 2 -> 5 -> 7 -> 8 -> 9)
        G = nx.DiGraph()
        for nid in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            G.add_node(nid, name=f"n{nid}", qualified_name=f"q{nid}", file_path=f"f{nid}.py")
        G.add_edges_from([(1, 3), (3, 4), (2, 5), (5, 7), (7, 8), (8, 9)])
        return G

    def _entries(self, G):
        return [n for n in G.nodes() if G.in_degree(n) == 0]

    def test_entries_are_indegree_zero(self):
        from roam.commands.cmd_sbom import _entry_ancestors

        G = self._graph()
        entries = self._entries(G)
        # 1, 2, 6 have no incoming edges.
        assert set(entries) == {1, 2, 6}
        # Sanity: helper agrees with the membership it filters against.
        assert _entry_ancestors(G, 4, set(entries)) == {1}

    def test_reverse_bfs_matches_has_path(self):
        """The reverse-BFS reaching set must equal the brute-force has_path set."""
        import networkx as nx

        from roam.commands.cmd_sbom import _entry_ancestors

        G = self._graph()
        entries = self._entries(G)
        entry_set = set(entries)
        for nid in G.nodes():
            expected = {e for e in entries if nx.has_path(G, e, nid)}
            assert _entry_ancestors(G, nid, entry_set) == expected, f"mismatch at node {nid}"

    def test_entry_points_ordered_by_entry_id(self):
        from roam.commands.cmd_sbom import _trace_entry_reach

        G = self._graph()
        entries = self._entries(G)  # [1, 2, 6] in node-iteration (id) order
        # Node 9 is reachable only from entry 2 (2 -> 5 -> 7 -> 8 -> 9).
        assert _trace_entry_reach(G, entries, 9) == [2]
        # Node 4 is reachable only from entry 1.
        assert _trace_entry_reach(G, entries, 4) == [1]

    def test_entry_is_self_reachable(self):
        """An entry node that is itself the matched node is trivially reachable
        (parity with the old ``nx.has_path(G, eid, eid) is True``)."""
        from roam.commands.cmd_sbom import _entry_ancestors

        G = self._graph()
        entries = self._entries(G)
        assert _entry_ancestors(G, 6, set(entries)) == {6}
        assert _entry_ancestors(G, 1, set(entries)) == {1}

    def test_record_match_short_circuits_on_first_reachable(self):
        """``_record_match`` populates entry_points from the first reachable
        matched node and short-circuits afterward — preserve that exactly."""
        from roam.commands.cmd_sbom import _record_match

        G = self._graph()
        entries = self._entries(G)
        entry_set = set(entries)
        info = {"reachable": False, "entry_points": [], "matched_symbols": []}
        # First matched node 4 -> reachable from entry 1 (q1).
        _record_match(info, "q4", G, entries, 4, entry_set)
        assert info["reachable"] is True
        assert info["entry_points"] == ["q1"]
        # Second matched node 9 -> reachable from entry 2 (q2), but short-circuit
        # means entry_points is unchanged; matched_symbols still grows.
        _record_match(info, "q9", G, entries, 9, entry_set)
        assert info["entry_points"] == ["q1"]
        assert info["matched_symbols"] == ["q4", "q9"]

    def test_unreachable_node_reports_no_entries(self):
        import networkx as nx

        from roam.commands.cmd_sbom import _record_match

        # A node with no incoming path from any entry: a lone cycle with no
        # entry feeding it.
        G = nx.DiGraph()
        for nid in (1, 10, 11):
            G.add_node(nid, name=f"n{nid}", qualified_name=f"q{nid}", file_path=f"f{nid}.py")
        # 10 <-> 11 cycle, neither is an entry (both have in-degree 1); 1 is an
        # isolated entry that does NOT reach the cycle.
        G.add_edges_from([(10, 11), (11, 10)])
        entries = [n for n in G.nodes() if G.in_degree(n) == 0]
        assert entries == [1]
        info = {"reachable": False, "entry_points": [], "matched_symbols": []}
        _record_match(info, "q10", G, entries, 10, set(entries))
        assert info["reachable"] is False
        assert info["entry_points"] == []
