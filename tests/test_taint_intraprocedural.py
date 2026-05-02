"""Tests for intraprocedural co-call detection in the taint engine.

The pure forward BFS misses the ``y = source(); sink(y)`` shape: source
and sink are both *targets* of the enclosing function, never connected
by a forward call. The co-call pass catches functions that call BOTH.
"""

from __future__ import annotations

import sqlite3

from roam.security.taint_engine import _intraprocedural_co_calls


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL
        );
        """
    )
    return conn


def _add_call(conn, src, tgt):
    conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, 'calls')", (src, tgt))


class TestIntraproceduralCoCalls:
    def test_empty_inputs_returns_empty(self):
        conn = _make_conn()
        assert _intraprocedural_co_calls(conn, set(), {1}, set()) == []
        assert _intraprocedural_co_calls(conn, {1}, set(), set()) == []

    def test_function_calling_both_source_and_sink_flagged(self):
        # Function 100 calls source 1 and sink 2. Pure BFS would miss
        # this since there's no 1 -> 2 edge.
        conn = _make_conn()
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        result = _intraprocedural_co_calls(conn, {1}, {2}, set())
        assert len(result) == 1
        enclosing, src, sink, has_sanitizer = result[0]
        assert enclosing == 100
        assert src == 1
        assert sink == 2
        assert has_sanitizer is False

    def test_function_calling_only_source_skipped(self):
        conn = _make_conn()
        _add_call(conn, 100, 1)
        # No call to 2 from 100
        assert _intraprocedural_co_calls(conn, {1}, {2}, set()) == []

    def test_function_calling_only_sink_skipped(self):
        conn = _make_conn()
        _add_call(conn, 100, 2)
        assert _intraprocedural_co_calls(conn, {1}, {2}, set()) == []

    def test_sanitizer_in_path_detected(self):
        # Function 100 calls source 1, sanitizer 9, and sink 2. The
        # finding should report has_sanitizer=True so OpenVEX can claim
        # inline_mitigations_already_exist downstream.
        conn = _make_conn()
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 9)
        _add_call(conn, 100, 2)
        result = _intraprocedural_co_calls(conn, {1}, {2}, {9})
        assert len(result) == 1
        _, _, _, has_sanitizer = result[0]
        assert has_sanitizer is True

    def test_separate_functions_not_combined(self):
        # Function 100 calls only source. Function 200 calls only sink.
        # No single function co-calls both, so no co-call finding.
        conn = _make_conn()
        _add_call(conn, 100, 1)
        _add_call(conn, 200, 2)
        assert _intraprocedural_co_calls(conn, {1}, {2}, set()) == []

    def test_multiple_co_calling_functions_all_flagged(self):
        conn = _make_conn()
        _add_call(conn, 100, 1)
        _add_call(conn, 100, 2)
        _add_call(conn, 200, 1)
        _add_call(conn, 200, 2)
        result = _intraprocedural_co_calls(conn, {1}, {2}, set())
        enclosings = {r[0] for r in result}
        assert enclosings == {100, 200}
