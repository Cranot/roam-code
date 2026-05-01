"""Tests for the runtime_score / runtime_score_max_for_symbols helpers.

These are the δ signal of the retrieve reranker and the impact-severity
bump in `roam critique`.
"""

from __future__ import annotations

import sqlite3

import pytest

from roam.runtime.hotspots import runtime_score, runtime_score_max_for_symbols


def _make_runtime_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE runtime_stats (
            symbol_id INTEGER PRIMARY KEY,
            call_count INTEGER,
            p99_latency_ms REAL,
            error_rate REAL
        );
        """
    )
    return conn


def _add(conn, sid, calls, p99, err):
    conn.execute(
        "INSERT INTO runtime_stats(symbol_id, call_count, p99_latency_ms, error_rate) VALUES (?, ?, ?, ?)",
        (sid, calls, p99, err),
    )
    conn.commit()


class TestRuntimeScore:
    def test_no_row_returns_zero(self):
        conn = _make_runtime_db()
        assert runtime_score(conn, 999) == 0.0

    def test_score_in_unit_interval(self):
        conn = _make_runtime_db()
        _add(conn, 1, 100, 200, 0.05)
        score = runtime_score(conn, 1)
        assert 0.0 < score <= 1.0

    def test_call_volume_saturates_at_baseline(self):
        """1k calls saturates the call_volume term."""
        conn = _make_runtime_db()
        _add(conn, 1, 1_000_000, 0, 0)  # huge calls, zero latency, zero errors
        # call_volume term ≈ 1.0; weight 0.6; latency=0; err=0
        # → score ≈ 0.6
        assert 0.55 <= runtime_score(conn, 1) <= 0.65

    def test_latency_one_second_saturates(self):
        conn = _make_runtime_db()
        _add(conn, 1, 0, 1500, 0)  # 1.5s p99 — capped at 1.0 in score
        # call_volume=0; latency=1.0 (capped); err=0 → 0.3
        assert runtime_score(conn, 1) == pytest.approx(0.3, abs=0.01)

    def test_error_rate_term(self):
        conn = _make_runtime_db()
        _add(conn, 1, 0, 0, 1.0)  # 100% error
        # call_volume=0; latency=0; err=1.0 → 0.1
        assert runtime_score(conn, 1) == pytest.approx(0.1, abs=0.01)

    def test_negative_inputs_clamped(self):
        conn = _make_runtime_db()
        _add(conn, 1, -10, -5, -0.5)
        score = runtime_score(conn, 1)
        assert score >= 0.0

    def test_log_baseline_tunable(self):
        """A lower baseline saturates faster — same calls produce a higher score."""
        conn = _make_runtime_db()
        _add(conn, 1, 100, 0, 0)
        with_default = runtime_score(conn, 1)
        with_lower_baseline = runtime_score(conn, 1, log_baseline=100)
        assert with_lower_baseline > with_default


class TestRuntimeScoreMaxForSymbols:
    def test_empty_set_returns_zero(self):
        conn = _make_runtime_db()
        assert runtime_score_max_for_symbols(conn, []) == 0.0
        assert runtime_score_max_for_symbols(conn, set()) == 0.0

    def test_picks_hottest_symbol(self):
        conn = _make_runtime_db()
        _add(conn, 1, 10, 50, 0)  # cool
        _add(conn, 2, 100_000, 800, 0.2)  # hot
        _add(conn, 3, 0, 0, 0)  # silent
        score = runtime_score_max_for_symbols(conn, [1, 2, 3])
        # max should match symbol 2
        assert score > 0.5

    def test_ignores_symbols_without_runtime_data(self):
        conn = _make_runtime_db()
        _add(conn, 1, 100, 200, 0.05)
        score_with_unknown = runtime_score_max_for_symbols(conn, [1, 999, 998])
        score_alone = runtime_score_max_for_symbols(conn, [1])
        assert score_with_unknown == score_alone

    def test_returns_zero_when_no_symbols_have_data(self):
        conn = _make_runtime_db()
        assert runtime_score_max_for_symbols(conn, [1, 2, 3]) == 0.0
