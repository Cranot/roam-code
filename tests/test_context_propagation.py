"""Tests for context propagation through call graph (backlog item 72)."""
from __future__ import annotations
import sqlite3
import pytest
try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False
from click.testing import CliRunner


def _make_graph(edges):
    if not HAS_NX:
        pytest.skip("networkx not installed")
    G = nx.DiGraph()
    for src, tgt in edges:
        G.add_edge(src, tgt)
    return G

def _linear_graph():
    return _make_graph([(1, 2), (2, 3), (3, 4)])

def _tree_graph():
    return _make_graph([(1, 2), (1, 3), (2, 4), (3, 5), (3, 6)])

def _cycle_graph():
    return _make_graph([(1, 2), (2, 3), (3, 1), (1, 4)])

def _disconnected_graph():
    G = _make_graph([(1, 2), (3, 4)])
    G.add_node(5)
    return G


def _make_test_db():
    import sqlite3 as sq3
    conn = sq3.connect(":memory:")
    conn.row_factory = sq3.Row
    conn.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT NOT NULL, language TEXT);"
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,"
        " name TEXT NOT NULL, kind TEXT, line_start INTEGER, line_end INTEGER,"
        " qualified_name TEXT, signature TEXT, is_exported INTEGER DEFAULT 1);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,"
        " target_id INTEGER NOT NULL, kind TEXT, line INTEGER);"
        "CREATE TABLE graph_metrics (symbol_id INTEGER PRIMARY KEY, pagerank REAL,"
        " in_degree INTEGER, out_degree INTEGER, betweenness REAL);"
        "CREATE TABLE file_edges (source_file_id INTEGER, target_file_id INTEGER,"
        " kind TEXT, symbol_count INTEGER);"
    )
    rows = [
        ("files", (1, "src/a.py", "python")),
        ("files", (2, "src/b.py", "python")),
        ("files", (3, "src/c.py", "python")),
        ("files", (4, "src/d.py", "python")),
        ("symbols", (1, 1, "func_a", "function", 1, 10, "func_a", None, 1)),
        ("symbols", (2, 2, "func_b", "function", 1, 10, "func_b", None, 1)),
        ("symbols", (3, 3, "func_c", "function", 1, 10, "func_c", None, 1)),
        ("symbols", (4, 4, "func_d", "function", 1, 10, "func_d", None, 1)),
        ("edges", (1, 1, 2, "call", 5)),
        ("edges", (2, 2, 3, "call", 5)),
        ("edges", (3, 3, 4, "call", 5)),
        ("graph_metrics", (1, 0.4, 0, 1, 0.5)),
        ("graph_metrics", (2, 0.3, 1, 1, 0.3)),
        ("graph_metrics", (3, 0.2, 1, 1, 0.2)),
        ("graph_metrics", (4, 0.1, 1, 0, 0.0)),
    ]
    for table, vals in rows:
        ph = ",".join("?" * len(vals))
        conn.execute(f"INSERT INTO {table} VALUES ({ph})", vals)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# propagate_context tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_NX, reason="networkx not installed")
class TestPropagateContext:
    def test_linear_chain_all_nodes_reachable(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores[1] == 1.0
        assert 2 in scores
        assert 3 in scores
        assert 4 in scores

    def test_linear_chain_depth_limited(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=2, decay=0.5)
        assert 4 not in scores
        assert 3 in scores

    def test_decay_scoring_depth1(self):
        from roam.graph.propagation import propagate_context
        G = _make_graph([(1, 2)])
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores.get(2) == pytest.approx(0.5, abs=1e-9)

    def test_decay_scoring_depth2(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores.get(3) == pytest.approx(0.25, abs=1e-9)

    def test_decay_scoring_depth3(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores.get(4) == pytest.approx(0.125, abs=1e-9)

    def test_decay_monotone_decreasing(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores[2] >= scores[3] >= scores[4]

    def test_seed_always_scores_one(self):
        from roam.graph.propagation import propagate_context
        G = _tree_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores[1] == 1.0

    def test_tree_shaped_all_reachable(self):
        from roam.graph.propagation import propagate_context
        G = _tree_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert all(n in scores for n in [1, 2, 3, 4, 5, 6])

    def test_tree_direct_children_same_score(self):
        from roam.graph.propagation import propagate_context
        G = _tree_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores[2] == pytest.approx(scores[3], abs=1e-9)

    def test_cycle_no_infinite_loop(self):
        from roam.graph.propagation import propagate_context
        G = _cycle_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert 1 in scores
        assert scores[1] == 1.0

    def test_cycle_nodes_reachable(self):
        from roam.graph.propagation import propagate_context
        G = _cycle_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert 2 in scores
        assert 3 in scores
        assert 4 in scores

    def test_caller_lower_weight_than_callee(self):
        from roam.graph.propagation import propagate_context
        G = _make_graph([(2, 1), (1, 3)])
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores.get(3, 0) > scores.get(2, 0)

    def test_caller_depth1_score(self):
        from roam.graph.propagation import propagate_context
        G = _make_graph([(2, 1)])
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        expected = (0.5 * 0.5) ** 1
        assert scores.get(2) == pytest.approx(expected, abs=1e-9)

    def test_disconnected_not_reached(self):
        from roam.graph.propagation import propagate_context
        G = _disconnected_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert 2 in scores
        assert 3 not in scores
        assert 4 not in scores
        assert 5 not in scores

    def test_single_node_no_edges(self):
        from roam.graph.propagation import propagate_context
        G = nx.DiGraph()
        G.add_node(1)
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        assert scores == {1: 1.0}

    def test_empty_graph_empty_seeds(self):
        from roam.graph.propagation import propagate_context
        G = nx.DiGraph()
        scores = propagate_context(G, [], max_depth=3, decay=0.5)
        assert scores == {}

    def test_empty_seeds_nonempty_graph(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [], max_depth=3, decay=0.5)
        assert scores == {}

    def test_seed_not_in_graph(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [999], max_depth=3, decay=0.5)
        assert scores == {}

    def test_max_depth_zero(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        scores = propagate_context(G, [1], max_depth=0, decay=0.5)
        assert scores == {1: 1.0}

    def test_multiple_seeds_all_score_one(self):
        from roam.graph.propagation import propagate_context
        G = _tree_graph()
        scores = propagate_context(G, [2, 3], max_depth=2, decay=0.5)
        assert scores[2] == 1.0
        assert scores[3] == 1.0

    def test_custom_decay_applied(self):
        from roam.graph.propagation import propagate_context
        G = _make_graph([(1, 2)])
        scores = propagate_context(G, [1], max_depth=3, decay=0.8)
        assert scores.get(2) == pytest.approx(0.8, abs=1e-9)

    def test_scores_bounded_below_one(self):
        from roam.graph.propagation import propagate_context
        G = _tree_graph()
        scores = propagate_context(G, [1], max_depth=3, decay=0.5)
        non_seeds = {k: v for k, v in scores.items() if k != 1}
        assert all(v <= 1.0 for v in non_seeds.values())

    def test_return_type_is_dict(self):
        from roam.graph.propagation import propagate_context
        G = _linear_graph()
        assert isinstance(propagate_context(G, [1]), dict)


# ---------------------------------------------------------------------------
# callee_chain tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_NX, reason="networkx not installed")
class TestCalleeChain:
    def test_linear_chain_order(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        chain = callee_chain(G, 1, max_depth=3)
        node_ids = [n for n, d in chain]
        assert node_ids == [2, 3, 4]

    def test_linear_chain_depths(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        chain = callee_chain(G, 1, max_depth=3)
        depth_map = {n: d for n, d in chain}
        assert depth_map[2] == 1
        assert depth_map[3] == 2
        assert depth_map[4] == 3

    def test_max_depth_limits_chain(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        chain = callee_chain(G, 1, max_depth=2)
        node_ids = [n for n, d in chain]
        assert 4 not in node_ids
        assert 3 in node_ids

    def test_cycle_terminates_no_duplicates(self):
        from roam.graph.propagation import callee_chain
        G = _cycle_graph()
        chain = callee_chain(G, 1, max_depth=5)
        node_ids = [n for n, d in chain]
        assert len(node_ids) == len(set(node_ids))

    def test_seed_not_in_result(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        chain = callee_chain(G, 1, max_depth=3)
        node_ids = [n for n, d in chain]
        assert 1 not in node_ids

    def test_leaf_node_empty_chain(self):
        from roam.graph.propagation import callee_chain
        G = nx.DiGraph()
        G.add_node(1)
        assert callee_chain(G, 1, max_depth=3) == []

    def test_node_not_in_graph_empty(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        assert callee_chain(G, 999, max_depth=3) == []

    def test_returns_list_of_two_tuples(self):
        from roam.graph.propagation import callee_chain
        G = _linear_graph()
        chain = callee_chain(G, 1, max_depth=3)
        assert isinstance(chain, list)
        for item in chain:
            assert len(item) == 2


# ---------------------------------------------------------------------------
# merge_rankings tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_NX, reason="networkx not installed")
class TestMergeRankings:
    def test_basic_blend_in_range(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.1, 2: 0.05, 3: 0.2}
        prop = {1: 1.0, 2: 0.5, 3: 0.25}
        result = merge_rankings(pr, prop, alpha=0.6)
        for v in result.values():
            assert 0.0 <= v <= 1.0

    def test_alpha_one_propagation_dominates(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.0001, 2: 0.0002}
        prop = {1: 1.0, 2: 0.5}
        result = merge_rankings(pr, prop, alpha=1.0)
        assert result[1] > result[2]

    def test_alpha_zero_pagerank_dominates(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.01, 2: 0.001}
        prop = {1: 0.1, 2: 0.9}
        result = merge_rankings(pr, prop, alpha=0.0)
        assert result[1] > result[2]

    def test_empty_both(self):
        from roam.graph.propagation import merge_rankings
        assert merge_rankings({}, {}) == {}

    def test_empty_pagerank_uses_prop(self):
        from roam.graph.propagation import merge_rankings
        prop = {1: 1.0, 2: 0.5}
        result = merge_rankings({}, prop, alpha=0.6)
        assert 1 in result
        assert 2 in result

    def test_empty_propagation_uses_pr(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.01, 2: 0.005}
        result = merge_rankings(pr, {}, alpha=0.6)
        assert 1 in result
        assert 2 in result

    def test_union_of_keys(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.1, 2: 0.2}
        prop = {2: 0.8, 3: 0.4}
        result = merge_rankings(pr, prop, alpha=0.6)
        assert set(result.keys()) == {1, 2, 3}

    def test_high_propagation_ranks_higher(self):
        from roam.graph.propagation import merge_rankings
        pr = {1: 0.001, 2: 0.1}
        prop = {1: 1.0, 2: 0.1}
        result = merge_rankings(pr, prop, alpha=0.6)
        assert result[1] > result[2]


# ---------------------------------------------------------------------------
# DB-backed helper tests
# ---------------------------------------------------------------------------

class TestPropagationScoresDB:
    def test_callee_files_get_scores(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [1], use_propagation=True)
        assert "src/b.py" in scores
        assert scores["src/b.py"] > 0

    def test_transitive_callee_files_scored(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [1], use_propagation=True, max_depth=3)
        assert "src/c.py" in scores
        assert "src/d.py" in scores

    def test_decay_ordering(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [1], use_propagation=True, max_depth=3)
        b_score = scores.get("src/b.py", 0)
        c_score = scores.get("src/c.py", 0)
        d_score = scores.get("src/d.py", 0)
        assert b_score >= c_score >= d_score

    def test_no_propagation_returns_empty(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [1], use_propagation=False)
        assert scores == {}

    def test_empty_sym_ids_returns_empty(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [], use_propagation=True)
        assert scores == {}

    def test_multiple_seeds_all_expand(self):
        from roam.commands.context_helpers import _get_propagation_scores_for_paths
        conn = _make_test_db()
        scores = _get_propagation_scores_for_paths(conn, [1, 2], use_propagation=True, max_depth=2)
        assert "src/b.py" in scores or "src/c.py" in scores


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestContextCommandPropagation:
    def test_no_propagation_flag_accepted(self):
        from roam.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["context", "--no-propagation", "--help"])
        assert result.exit_code == 0
        assert "--no-propagation" in result.output

    def test_no_propagation_in_help_text(self):
        from roam.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["context", "--help"])
        assert result.exit_code == 0
        assert "no-propagation" in result.output

    def test_propagation_help_mentions_disable(self):
        from roam.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["context", "--help"])
        assert "propagation" in result.output.lower()


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------

class TestGatherSymbolContextSignature:
    def test_use_propagation_exists(self):
        import inspect
        from roam.commands.context_helpers import gather_symbol_context
        sig = inspect.signature(gather_symbol_context)
        assert "use_propagation" in sig.parameters

    def test_use_propagation_default_true(self):
        import inspect
        from roam.commands.context_helpers import gather_symbol_context
        sig = inspect.signature(gather_symbol_context)
        assert sig.parameters["use_propagation"].default is True

    def test_batch_context_use_propagation_exists(self):
        import inspect
        from roam.commands.context_helpers import batch_context
        sig = inspect.signature(batch_context)
        assert "use_propagation" in sig.parameters

    def test_batch_context_use_propagation_default_true(self):
        import inspect
        from roam.commands.context_helpers import batch_context
        sig = inspect.signature(batch_context)
        assert sig.parameters["use_propagation"].default is True

    def test_rank_single_files_propagation_scores_param(self):
        import inspect
        from roam.commands.context_helpers import _rank_single_files
        sig = inspect.signature(_rank_single_files)
        assert "propagation_scores" in sig.parameters

    def test_rank_batch_files_propagation_scores_param(self):
        import inspect
        from roam.commands.context_helpers import _rank_batch_files
        sig = inspect.signature(_rank_batch_files)
        assert "propagation_scores" in sig.parameters


# ---------------------------------------------------------------------------
# Module smoke tests
# ---------------------------------------------------------------------------

class TestPropagationModuleImport:
    def test_module_importable(self):
        import roam.graph.propagation as prop
        assert hasattr(prop, "propagate_context")
        assert hasattr(prop, "merge_rankings")
        assert hasattr(prop, "callee_chain")

    def test_propagate_context_callable(self):
        from roam.graph.propagation import propagate_context
        assert callable(propagate_context)

    def test_merge_rankings_callable(self):
        from roam.graph.propagation import merge_rankings
        assert callable(merge_rankings)

    def test_callee_chain_callable(self):
        from roam.graph.propagation import callee_chain
        assert callable(callee_chain)

    def test_propagation_py_has_from_future(self):
        import pathlib
        prop_path = pathlib.Path(__file__).parent.parent / "src" / "roam" / "graph" / "propagation.py"
        src = prop_path.read_text(encoding="utf-8")
        assert "from __future__ import annotations" in src
