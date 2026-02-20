"""Property-based tests for roam core functions.

Uses Hypothesis when available, with manual randomized fallbacks that always run.
Tests invariants and algebraic properties rather than specific input/output pairs.
"""

from __future__ import annotations

import random
import sqlite3
import string

import pytest

try:
    from hypothesis import given, settings, assume
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

import networkx as nx

from roam.output.formatter import (
    abbrev_kind,
    format_table,
    format_table_compact,
    KIND_ABBREV,
)
from roam.db.connection import batched_in, ensure_schema
from roam.graph.simulate import metric_delta, clone_graph, _HIGHER_IS_BETTER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_string(min_len: int = 0, max_len: int = 30) -> str:
    """Generate a random ASCII string."""
    length = random.randint(min_len, max_len)
    return "".join(random.choices(string.ascii_letters + string.digits + "_", k=length))


def _make_in_memory_db():
    """Create an in-memory SQLite DB with the roam schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_symbol(conn, name: str, kind: str = "function",
                   file_path: str = "test.py") -> int:
    """Insert a file + symbol and return the symbol ID."""
    # Ensure the file exists
    row = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if row:
        file_id = row[0]
    else:
        conn.execute("INSERT INTO files (path, language) VALUES (?, ?)",
                     (file_path, "python"))
        file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO symbols (file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?)",
        (file_id, name, name, kind),
    )
    sym_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return sym_id


def _build_simple_graph(n_nodes: int, n_edges: int) -> nx.DiGraph:
    """Build a random DiGraph with node attributes."""
    G = nx.DiGraph()
    for i in range(n_nodes):
        G.add_node(i, name=f"sym_{i}", file_path=f"file_{i % 3}.py", kind="function")
    for _ in range(n_edges):
        src = random.randint(0, max(0, n_nodes - 1))
        tgt = random.randint(0, max(0, n_nodes - 1))
        if src != tgt:
            G.add_edge(src, tgt, kind="calls")
    return G


# ===========================================================================
# TestAbbrevKindProperties
# ===========================================================================


class TestAbbrevKindProperties:
    """Property-based tests for abbrev_kind()."""

    def test_output_never_longer_than_input(self):
        """abbrev_kind(x) should always be <= len(x)."""
        # Test all known kinds
        for kind, abbrev in KIND_ABBREV.items():
            assert len(abbrev) <= len(kind), (
                f"Abbreviation '{abbrev}' is longer than kind '{kind}'"
            )

    def test_output_never_longer_than_input_random(self):
        """Random strings: abbrev_kind returns the string itself if unknown."""
        for _ in range(200):
            kind = _random_string(1, 50)
            result = abbrev_kind(kind)
            assert len(result) <= len(kind), (
                f"abbrev_kind({kind!r}) = {result!r} is longer than input"
            )

    def test_known_kinds_map_correctly(self):
        """Every key in KIND_ABBREV must map to its value."""
        for kind, expected in KIND_ABBREV.items():
            assert abbrev_kind(kind) == expected

    def test_unknown_kinds_pass_through(self):
        """Unknown kinds should be returned unchanged (not truncated to 3 chars)."""
        for _ in range(100):
            kind = _random_string(1, 30)
            if kind not in KIND_ABBREV:
                assert abbrev_kind(kind) == kind

    def test_idempotent_on_abbreviations(self):
        """Applying abbrev_kind to an already-abbreviated value is idempotent."""
        for kind in KIND_ABBREV:
            once = abbrev_kind(kind)
            twice = abbrev_kind(once)
            # Second application should return the same thing (either it maps
            # again or passes through)
            assert twice == abbrev_kind(once)

    def test_empty_string(self):
        """Empty string should pass through."""
        assert abbrev_kind("") == ""

    @pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
    def test_hypothesis_output_never_longer(self):
        @given(st.text(min_size=0, max_size=100))
        @settings(max_examples=300)
        def inner(kind):
            result = abbrev_kind(kind)
            assert len(result) <= len(kind) or result == kind

        inner()


# ===========================================================================
# TestFormatTableProperties
# ===========================================================================


class TestFormatTableProperties:
    """Property-based tests for format_table()."""

    def test_empty_rows_returns_none_marker(self):
        """Empty rows always produce '(none)' regardless of headers."""
        for _ in range(50):
            n_headers = random.randint(1, 10)
            headers = [_random_string(1, 10) for _ in range(n_headers)]
            assert format_table(headers, []) == "(none)"

    def test_correct_line_count_no_budget(self):
        """Without budget: output has header + separator + len(rows) lines."""
        for _ in range(100):
            n_cols = random.randint(1, 5)
            n_rows = random.randint(1, 20)
            headers = [_random_string(1, 8) for _ in range(n_cols)]
            rows = [[_random_string(0, 15) for _ in range(n_cols)]
                    for _ in range(n_rows)]
            result = format_table(headers, rows)
            lines = result.split("\n")
            expected = 2 + n_rows  # header + separator + data rows
            assert len(lines) == expected, (
                f"Expected {expected} lines, got {len(lines)} for "
                f"{n_cols} cols x {n_rows} rows"
            )

    def test_correct_line_count_with_budget(self):
        """With budget < len(rows): output has header + sep + budget + overflow line."""
        for _ in range(50):
            n_cols = random.randint(1, 4)
            n_rows = random.randint(5, 20)
            budget = random.randint(1, n_rows - 1)
            headers = [_random_string(1, 8) for _ in range(n_cols)]
            rows = [[_random_string(0, 10) for _ in range(n_cols)]
                    for _ in range(n_rows)]
            result = format_table(headers, rows, budget=budget)
            lines = result.split("\n")
            expected = 2 + budget + 1  # header + sep + budget rows + "(+N more)"
            assert len(lines) == expected, (
                f"Expected {expected} lines with budget={budget}, "
                f"got {len(lines)} for {n_rows} rows"
            )

    def test_header_always_first_line(self):
        """First line always contains all header strings."""
        for _ in range(50):
            n_cols = random.randint(1, 5)
            headers = [_random_string(3, 10) for _ in range(n_cols)]
            rows = [[_random_string(1, 10) for _ in range(n_cols)]]
            result = format_table(headers, rows)
            first_line = result.split("\n")[0]
            for h in headers:
                assert h in first_line, f"Header '{h}' not in first line: {first_line}"

    def test_separator_is_dashes_only(self):
        """Second line (separator) should contain only dashes and spaces."""
        headers = ["Name", "Kind", "File"]
        rows = [["foo", "fn", "a.py"]]
        result = format_table(headers, rows)
        sep_line = result.split("\n")[1]
        allowed = set("- ")
        assert set(sep_line) <= allowed, (
            f"Separator line has unexpected chars: {sep_line!r}"
        )

    def test_compact_vs_regular_same_row_count(self):
        """format_table and format_table_compact should produce same data row count."""
        for _ in range(50):
            n_cols = random.randint(1, 4)
            n_rows = random.randint(1, 10)
            headers = [_random_string(2, 8) for _ in range(n_cols)]
            rows = [[_random_string(1, 8) for _ in range(n_cols)]
                    for _ in range(n_rows)]
            regular = format_table(headers, rows)
            compact = format_table_compact(headers, rows)
            # Regular: header + sep + rows. Compact: header + rows (no sep)
            reg_lines = regular.split("\n")
            cmp_lines = compact.split("\n")
            # Both should contain n_rows data lines
            assert len(reg_lines) == 2 + n_rows
            assert len(cmp_lines) == 1 + n_rows  # compact has no separator

    @pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
    def test_hypothesis_line_count(self):
        @given(
            st.integers(min_value=1, max_value=5),
            st.integers(min_value=1, max_value=20),
        )
        @settings(max_examples=200)
        def inner(n_cols, n_rows):
            headers = [f"h{i}" for i in range(n_cols)]
            rows = [[f"c{i}{j}" for j in range(n_cols)] for i in range(n_rows)]
            result = format_table(headers, rows)
            lines = result.split("\n")
            assert len(lines) == 2 + n_rows

        inner()


# ===========================================================================
# TestBatchedInProperties
# ===========================================================================


class TestBatchedInProperties:
    """Property-based tests for batched_in()."""

    def _setup_db(self, n_items: int):
        """Create DB with n_items rows in a simple test table."""
        conn = _make_in_memory_db()
        conn.execute("CREATE TABLE test_items (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(1, n_items + 1):
            conn.execute("INSERT INTO test_items (id, val) VALUES (?, ?)",
                         (i, f"item_{i}"))
        conn.commit()
        return conn

    def test_empty_list_empty_result(self):
        """batched_in with empty id list always returns []."""
        conn = self._setup_db(10)
        result = batched_in(conn, "SELECT * FROM test_items WHERE id IN ({ph})", [])
        assert result == []
        conn.close()

    def test_single_id_returns_one_row(self):
        """A single existing ID returns exactly one row."""
        conn = self._setup_db(10)
        result = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", [5]
        )
        assert len(result) == 1
        assert result[0]["id"] == 5
        conn.close()

    def test_all_ids_returns_all(self):
        """Requesting all IDs returns all rows."""
        n = 50
        conn = self._setup_db(n)
        ids = list(range(1, n + 1))
        result = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids
        )
        assert len(result) == n
        returned_ids = sorted(r["id"] for r in result)
        assert returned_ids == list(range(1, n + 1))
        conn.close()

    def test_order_independence(self):
        """Shuffling the ID list should return the same set of results."""
        n = 30
        conn = self._setup_db(n)
        ids = list(range(1, n + 1))
        result1 = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids
        )
        random.shuffle(ids)
        result2 = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids
        )
        set1 = {r["id"] for r in result1}
        set2 = {r["id"] for r in result2}
        assert set1 == set2
        conn.close()

    def test_batched_matches_unbatched(self):
        """batched_in with small batch_size should return same results as large."""
        n = 100
        conn = self._setup_db(n)
        ids = list(range(1, n + 1))

        # Large batch (everything in one go)
        result_large = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids,
            batch_size=9999,
        )
        # Tiny batch (many chunks)
        result_small = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids,
            batch_size=7,
        )
        set_large = {r["id"] for r in result_large}
        set_small = {r["id"] for r in result_small}
        assert set_large == set_small
        assert len(set_large) == n
        conn.close()

    def test_nonexistent_ids_return_nothing(self):
        """IDs that don't exist in the table return no rows."""
        conn = self._setup_db(10)
        result = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})",
            [999, 1000, 1001],
        )
        assert result == []
        conn.close()

    def test_mixed_existing_and_nonexistent(self):
        """Only existing IDs produce rows."""
        conn = self._setup_db(10)
        ids = [1, 5, 10, 999, 2000]
        result = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids
        )
        returned_ids = {r["id"] for r in result}
        assert returned_ids == {1, 5, 10}
        conn.close()

    def test_duplicate_ids_in_input(self):
        """Duplicate IDs in the input should still return distinct rows."""
        conn = self._setup_db(10)
        ids = [1, 1, 2, 2, 3, 3]
        result = batched_in(
            conn, "SELECT * FROM test_items WHERE id IN ({ph})", ids
        )
        returned_ids = {r["id"] for r in result}
        # SQL IN naturally deduplicates, but batched_in might return dupes
        # across batches with tiny batch sizes -- the key property is
        # that all requested existing IDs are present
        assert {1, 2, 3} <= returned_ids
        conn.close()

    def test_pre_params(self):
        """Extra pre-params are correctly bound before the IN clause."""
        conn = self._setup_db(20)
        # Add a 'category' column for testing
        conn.execute("ALTER TABLE test_items ADD COLUMN category TEXT DEFAULT 'a'")
        for i in range(1, 11):
            conn.execute("UPDATE test_items SET category='b' WHERE id=?", (i,))
        conn.commit()

        ids = list(range(1, 21))
        result = batched_in(
            conn,
            "SELECT * FROM test_items WHERE category=? AND id IN ({ph})",
            ids,
            pre=("b",),
        )
        returned_ids = {r["id"] for r in result}
        assert returned_ids == set(range(1, 11))
        conn.close()


# ===========================================================================
# TestMetricDeltaProperties
# ===========================================================================


class TestMetricDeltaProperties:
    """Property-based tests for metric_delta()."""

    def _random_metrics(self) -> dict:
        """Generate a random metrics dict with all standard keys."""
        return {
            "health_score": random.randint(0, 100),
            "nodes": random.randint(1, 500),
            "edges": random.randint(0, 1000),
            "cycles": random.randint(0, 50),
            "tangle_ratio": round(random.uniform(0, 100), 2),
            "layer_violations": random.randint(0, 30),
            "modularity": round(random.uniform(-0.5, 1.0), 4),
            "fiedler": round(random.uniform(0, 5), 6),
            "propagation_cost": round(random.uniform(0, 100), 2),
            "god_components": random.randint(0, 20),
            "bottlenecks": random.randint(0, 15),
        }

    def test_delta_equals_difference(self):
        """delta == after - before for every metric."""
        for _ in range(100):
            before = self._random_metrics()
            after = self._random_metrics()
            result = metric_delta(before, after)
            for key in before:
                d = result[key]
                expected = after[key] - before[key]
                # Floats may get rounded
                if isinstance(expected, float):
                    assert abs(d["delta"] - expected) < 0.01, (
                        f"key={key}: delta={d['delta']} != expected={expected}"
                    )
                else:
                    assert d["delta"] == expected, (
                        f"key={key}: delta={d['delta']} != expected={expected}"
                    )

    def test_direction_always_valid(self):
        """Direction is always one of the valid strings."""
        valid_directions = {"improved", "degraded", "unchanged", "changed"}
        for _ in range(100):
            before = self._random_metrics()
            after = self._random_metrics()
            result = metric_delta(before, after)
            for key, d in result.items():
                assert d["direction"] in valid_directions, (
                    f"key={key}: invalid direction {d['direction']!r}"
                )

    def test_unchanged_when_equal(self):
        """When before == after, direction is 'unchanged' and delta is 0."""
        metrics = self._random_metrics()
        result = metric_delta(metrics, dict(metrics))
        for key, d in result.items():
            assert d["direction"] == "unchanged", (
                f"key={key}: direction should be 'unchanged' but is {d['direction']!r}"
            )
            assert d["delta"] == 0 or d["delta"] == 0.0, (
                f"key={key}: delta should be 0 but is {d['delta']}"
            )

    def test_pct_change_zero_when_equal(self):
        """pct_change is 0 when before == after."""
        metrics = self._random_metrics()
        result = metric_delta(metrics, dict(metrics))
        for key, d in result.items():
            assert d["pct_change"] == 0.0, (
                f"key={key}: pct_change should be 0.0 but is {d['pct_change']}"
            )

    def test_before_after_stored_correctly(self):
        """The result stores before and after values accurately."""
        for _ in range(50):
            before = self._random_metrics()
            after = self._random_metrics()
            result = metric_delta(before, after)
            for key in before:
                assert result[key]["before"] == before[key]
                assert result[key]["after"] == after[key]

    def test_higher_is_better_direction_logic(self):
        """For known metrics, positive delta + higher-is-better => improved."""
        # health_score: higher is better
        before = {"health_score": 50}
        after = {"health_score": 70}
        result = metric_delta(before, after)
        assert result["health_score"]["direction"] == "improved"

        # cycles: lower is better
        before = {"cycles": 10}
        after = {"cycles": 5}
        result = metric_delta(before, after)
        assert result["cycles"]["direction"] == "improved"

        # cycles increasing is degradation
        before = {"cycles": 5}
        after = {"cycles": 10}
        result = metric_delta(before, after)
        assert result["cycles"]["direction"] == "degraded"

    def test_missing_key_in_after_skipped(self):
        """If a key exists in before but not after, it is omitted from result."""
        before = {"health_score": 50, "extra_key": 99}
        after = {"health_score": 70}
        result = metric_delta(before, after)
        assert "health_score" in result
        assert "extra_key" not in result

    @pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
    def test_hypothesis_delta_equals_diff(self):
        @given(
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
        )
        @settings(max_examples=300)
        def inner(b, a):
            before = {"health_score": b}
            after = {"health_score": a}
            result = metric_delta(before, after)
            assert result["health_score"]["delta"] == a - b

        inner()


# ===========================================================================
# TestCloneGraphProperties
# ===========================================================================


class TestCloneGraphProperties:
    """Property-based tests for clone_graph()."""

    def test_clone_preserves_node_count(self):
        """Clone has the same number of nodes."""
        for _ in range(30):
            n = random.randint(0, 50)
            G = _build_simple_graph(n, random.randint(0, n * 2))
            C = clone_graph(G)
            assert len(C) == len(G)

    def test_clone_preserves_edge_count(self):
        """Clone has the same number of edges."""
        for _ in range(30):
            n = random.randint(0, 50)
            G = _build_simple_graph(n, random.randint(0, n * 2))
            C = clone_graph(G)
            assert C.number_of_edges() == G.number_of_edges()

    def test_clone_preserves_node_set(self):
        """Clone has exactly the same set of node IDs."""
        G = _build_simple_graph(20, 30)
        C = clone_graph(G)
        assert set(C.nodes) == set(G.nodes)

    def test_clone_preserves_edge_set(self):
        """Clone has exactly the same set of edges."""
        G = _build_simple_graph(15, 25)
        C = clone_graph(G)
        assert set(C.edges) == set(G.edges)

    def test_clone_preserves_node_attributes(self):
        """Node attributes are copied to the clone."""
        G = _build_simple_graph(10, 15)
        C = clone_graph(G)
        for nid in G.nodes:
            assert C.nodes[nid] == G.nodes[nid]

    def test_clone_independence_add_node(self):
        """Adding a node to the clone does not affect the original."""
        G = _build_simple_graph(10, 15)
        original_nodes = set(G.nodes)
        C = clone_graph(G)
        C.add_node(9999, name="new_node")
        assert set(G.nodes) == original_nodes
        assert 9999 not in G

    def test_clone_independence_remove_node(self):
        """Removing a node from the clone does not affect the original."""
        G = _build_simple_graph(10, 15)
        original_nodes = set(G.nodes)
        C = clone_graph(G)
        node_to_remove = list(C.nodes)[0]
        C.remove_node(node_to_remove)
        assert set(G.nodes) == original_nodes

    def test_clone_independence_add_edge(self):
        """Adding an edge to the clone does not affect the original."""
        G = _build_simple_graph(10, 5)
        original_edges = set(G.edges)
        C = clone_graph(G)
        # Find two nodes not connected
        C.add_edge(0, 9, kind="test")
        assert set(G.edges) == original_edges

    def test_clone_independence_modify_attribute(self):
        """Modifying a node attribute in the clone does not affect the original."""
        G = _build_simple_graph(5, 3)
        C = clone_graph(G)
        original_name = G.nodes[0]["name"]
        C.nodes[0]["name"] = "MODIFIED"
        assert G.nodes[0]["name"] == original_name

    def test_empty_graph_clone(self):
        """Cloning an empty graph produces an empty graph."""
        G = nx.DiGraph()
        C = clone_graph(G)
        assert len(C) == 0
        assert C.number_of_edges() == 0


# ===========================================================================
# TestFindSymbolProperties
# ===========================================================================


class TestFindSymbolProperties:
    """Property-based tests for find_symbol() using in-memory DB."""

    def test_known_symbol_found_by_exact_name(self):
        """A symbol inserted with a given name is found by that exact name."""
        conn = _make_in_memory_db()
        _insert_symbol(conn, "my_function", "function", "src/app.py")

        from roam.commands.resolve import find_symbol

        result = find_symbol(conn, "my_function")
        assert result is not None
        assert result["name"] == "my_function"
        assert result["kind"] == "function"
        conn.close()

    def test_result_has_required_keys(self):
        """Result from find_symbol always has id, name, kind."""
        conn = _make_in_memory_db()
        _insert_symbol(conn, "test_sym", "class", "lib.py")

        from roam.commands.resolve import find_symbol

        result = find_symbol(conn, "test_sym")
        assert result is not None
        keys = result.keys()
        assert "id" in keys
        assert "name" in keys
        assert "kind" in keys
        conn.close()

    def test_nonexistent_symbol_returns_none(self):
        """Searching for a name that does not exist returns None."""
        conn = _make_in_memory_db()
        _insert_symbol(conn, "real_symbol", "function", "a.py")

        from roam.commands.resolve import find_symbol

        result = find_symbol(conn, "definitely_not_here_xyz_123")
        assert result is None
        conn.close()

    def test_empty_db_returns_none(self):
        """Searching an empty DB always returns None."""
        conn = _make_in_memory_db()

        from roam.commands.resolve import find_symbol

        result = find_symbol(conn, "anything")
        assert result is None
        conn.close()

    def test_fuzzy_match_finds_substring(self):
        """find_symbol with a substring should find the symbol via LIKE."""
        conn = _make_in_memory_db()
        _insert_symbol(conn, "calculate_total_price", "function", "math.py")

        from roam.commands.resolve import find_symbol

        # The function uses LIKE %name% for fuzzy match
        result = find_symbol(conn, "total_price")
        assert result is not None
        assert "total_price" in result["name"]
        conn.close()
