"""``_betweenness_percentiles`` computes p70/p90 via exact interpolation points.

Guards the optimization at ``cmd_health.py`` (bottleneck percentile substrate):
percentile thresholds used to be computed by fetching and sorting ALL positive
betweenness rows on every health run. The helper now COUNTs the population,
derives the lo/hi indices the linear-interpolation formula needs, and SELECTs
only those rows positionally via ``ORDER BY`` + ``LIMIT``/``OFFSET``.

This test pins the correctness contract: for any population shape, the new
helper must return values bit-identical to the brute-force
``_percentile(sorted(all_positive), pct)`` it replaced. Severity classification
(``bn_p90 * mult`` etc.) and the JSON ``p70``/``p90`` report fields both depend
on these exact values, so any drift is a silent severity/contract regression.
"""

from __future__ import annotations

import sqlite3

import pytest

from roam.commands.cmd_health import _betweenness_percentiles, _percentile


def _conn_with_values(values):
    """In-memory graph_metrics populated with the given betweenness values."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE graph_metrics (symbol_id INTEGER PRIMARY KEY, betweenness REAL DEFAULT 0)")
    conn.executemany(
        "INSERT INTO graph_metrics (symbol_id, betweenness) VALUES (?, ?)",
        enumerate(values, start=1),
    )
    conn.commit()
    return conn


def _brute_force(values, percentiles):
    """The pre-optimization computation: sort all positives, interpolate."""
    positives = sorted(v for v in values if v > 0)
    return [_percentile(positives, pct) for pct in percentiles]


# Population shapes that stress the lo/hi index math: empty, single, odd,
# even, with duplicates/ties, and a realistic spread.
POPULATIONS = [
    pytest.param([], id="empty"),
    pytest.param([0, 0, 0], id="all-zero"),
    pytest.param([4.0], id="single-positive"),
    pytest.param([0, 7.0], id="one-positive-among-zeros"),
    pytest.param([1.0, 2.0], id="two-positives"),
    pytest.param([1.0, 2.0, 3.0], id="three-positives-odd"),
    pytest.param([1.0, 2.0, 3.0, 4.0], id="four-positives-even"),
    pytest.param([5.0, 5.0, 5.0, 5.0], id="ties"),
    pytest.param([0.0, 1.5, 1.5, 9.0, 9.0, 0.0, 20.0], id="mixed-dups-and-zeros"),
    pytest.param([float(i) for i in range(1, 101)], id="large-spread"),
]


@pytest.mark.parametrize("values", POPULATIONS)
def test_matches_brute_force_p70_p90(values):
    """Helper output == ``_percentile(sorted(positives), pct)`` for p70/p90."""
    conn = _conn_with_values(values)
    try:
        got = _betweenness_percentiles(conn, (70, 90)).values
    finally:
        conn.close()
    expected = _brute_force(values, (70, 90))
    assert got == pytest.approx(expected), (values, got, expected)


@pytest.mark.parametrize("values", POPULATIONS)
def test_matches_brute_force_many_percentiles(values):
    """Equivalence holds across the full percentile range, not just 70/90."""
    percentiles = [1, 25, 50, 70, 90, 95, 99, 100]
    conn = _conn_with_values(values)
    try:
        got = _betweenness_percentiles(conn, percentiles).values
    finally:
        conn.close()
    expected = _brute_force(values, percentiles)
    assert got == pytest.approx(expected), (values, got, expected)


@pytest.mark.parametrize("values", POPULATIONS)
def test_population_count(values):
    """``population`` reports the positive-betweenness row count."""
    conn = _conn_with_values(values)
    try:
        got = _betweenness_percentiles(conn, (70, 90)).population
    finally:
        conn.close()
    expected = sum(1 for v in values if v > 0)
    assert got == expected, (values, got, expected)


def test_empty_population_returns_zeros():
    conn = _conn_with_values([])
    try:
        bp = _betweenness_percentiles(conn, (70, 90))
    finally:
        conn.close()
    assert bp.values == [0, 0]
    assert bp.population == 0


def test_no_positive_returns_zeros():
    conn = _conn_with_values([0, 0, 0])
    try:
        bp = _betweenness_percentiles(conn, (70, 90))
    finally:
        conn.close()
    assert bp.values == [0, 0]
    assert bp.population == 0
