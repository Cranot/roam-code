"""Tests for the B8 spectral-forecast prototype (synthetic graphs only).

These tests deliberately require NO built index and NO DB — they exercise the
pure ``roam.graph.spectral_forecast`` functions over hand-built NetworkX graphs
and adjacency mappings. Mirrors the scipy-optional discipline: a degraded
eigensolver must surface ``compute_degraded`` rather than silently mis-trend.
"""

from __future__ import annotations

import networkx as nx
import pytest

from roam.graph.spectral_forecast import (
    SpectralForecast,
    SpectralInstability,
    decay_alert_wording,
    forecast_from_graphs,
    forecast_spectral_decay,
    spectral_instability,
)

# ---------------------------------------------------------------------------
# Synthetic graph builders
# ---------------------------------------------------------------------------


def _two_clear_clusters() -> nx.Graph:
    """Two 5-cliques joined by a single bridge edge — high spectral gap."""
    G = nx.Graph()
    a = [f"a{i}" for i in range(5)]
    b = [f"b{i}" for i in range(5)]
    for clique in (a, b):
        for i in range(len(clique)):
            for j in range(i + 1, len(clique)):
                G.add_edge(clique[i], clique[j])
    G.add_edge(a[0], b[0])  # single bridge -> clear modular separation
    return G


def _tangled_blob(n: int = 10) -> nx.Graph:
    """A near-complete graph — no modular separation, low spectral gap."""
    return nx.complete_graph(n)


def _chain_graph(length: int) -> nx.Graph:
    """A path/chain of ``length`` nodes — a stretched-bottleneck topology.

    Algebraic connectivity (lambda2) of a path graph DECAYS monotonically as
    the chain lengthens: P4 ~= 0.59, P10 ~= 0.10, P20 ~= 0.02. This is the real
    "structural decay" shape B8 cares about — the graph develops an
    ever-longer bottleneck rather than merging into a cohesive blob (more
    cross-edges between clusters actually RAISE lambda2). Growing the chain
    over "time" gives a clean declining spectral-gap series.
    """
    return nx.path_graph(length)


# ---------------------------------------------------------------------------
# spectral_instability — one-shot
# ---------------------------------------------------------------------------


def test_instability_clear_clusters_not_failed():
    inst = spectral_instability(_two_clear_clusters())
    assert isinstance(inst, SpectralInstability)
    assert inst.node_count == 10
    assert inst.component_count == 1
    assert inst.spectral_gap > 0.0
    assert inst.is_failed is False
    assert inst.verdict in {"Well-modularized", "Moderately modular"}


def test_instability_tangled_blob_failed():
    inst = spectral_instability(_tangled_blob(12))
    # A complete graph has very high algebraic connectivity, so it is NOT a
    # failure by the spectral-gap definition (it is a single cohesive blob with
    # huge lambda2). Assert the gap is large and verdict reflects that.
    assert inst.spectral_gap > 0.5
    assert inst.is_failed is False
    assert inst.verdict == "Well-modularized"


def test_instability_disconnected_graph_with_singleton():
    # spectral_gap computes min(lambda2) over components with >= 2 nodes; a
    # size-1 component contributes nothing, but a graph whose ONLY non-trivial
    # structure is two isolated nodes yields no measurable component -> 0.0.
    G = nx.Graph()
    G.add_nodes_from(["lonely_a", "lonely_b"])  # two isolated singletons
    inst = spectral_instability(G)
    assert inst.component_count == 2
    # No component has >= 2 nodes -> spectral_gap returns the 0.0 sentinel.
    assert inst.spectral_gap == 0.0
    assert inst.is_failed is True  # >= 2 nodes and gap below the failure band


def test_instability_disconnected_pair_components():
    # Two K2 components: spectral_gap takes min over components, each K2 has
    # lambda2 == 2.0, so the gap is 2.0 (NOT a 0.0 sentinel). This documents
    # that disconnection alone is not automatically a low-gap signal.
    G = nx.Graph()
    G.add_edges_from([("x0", "x1"), ("y0", "y1")])
    inst = spectral_instability(G)
    assert inst.component_count == 2
    assert inst.spectral_gap == 2.0
    assert inst.is_failed is False


def test_instability_trivial_graph():
    inst = spectral_instability(nx.Graph())
    assert inst.node_count == 0
    assert inst.component_count == 0
    assert inst.spectral_gap == 0.0
    assert inst.is_failed is False  # nothing to fail


def test_instability_accepts_adjacency_mapping():
    adj = {"a": ["b", "c"], "b": ["a", "c"], "c": ["a", "b"]}
    inst = spectral_instability(adj)
    assert inst.node_count == 3
    assert inst.component_count == 1
    assert inst.spectral_gap > 0.0


def test_instability_accepts_directed_graph():
    DG = nx.DiGraph()
    DG.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
    inst = spectral_instability(DG)
    # undirected projection of a 3-cycle is connected
    assert inst.component_count == 1
    assert inst.node_count == 3


def test_instability_rejects_garbage_input():
    with pytest.raises(TypeError):
        spectral_instability(42)


# ---------------------------------------------------------------------------
# forecast_spectral_decay — projection
# ---------------------------------------------------------------------------


def test_forecast_insufficient_history_empty():
    fc = forecast_spectral_decay([])
    assert fc.status == "insufficient_history"
    assert fc.snapshots_to_failure is None
    assert fc.history_points == 0


def test_forecast_insufficient_history_short():
    fc = forecast_spectral_decay([0.5, 0.4, 0.3])  # n=3 < 4
    assert fc.status == "insufficient_history"
    assert fc.history_points == 3
    assert fc.current_gap == 0.3


def test_forecast_stable_series():
    fc = forecast_spectral_decay([0.5, 0.5, 0.5, 0.5, 0.5], horizon=30)
    assert fc.status == "stable"
    assert abs(fc.slope) < 1e-6
    assert fc.predicted_topology_decay_rate == 0.0
    assert fc.snapshots_to_failure is None
    assert "stable" in fc.verdict


def test_forecast_warning_when_failure_within_horizon():
    # Steady decline from 0.5; failure band is 0.1. Slope ~ -0.05/step ->
    # reaches 0.1 in ~8 steps, well within a 30-step horizon.
    series = [0.50, 0.45, 0.40, 0.35, 0.30, 0.25]
    fc = forecast_spectral_decay(series, horizon=30)
    assert fc.status == "warning"
    assert fc.slope < 0
    assert fc.snapshots_to_failure is not None
    assert fc.snapshots_to_failure <= 30
    assert fc.predicted_topology_decay_rate > 0.0
    assert "structural failure projected within" in fc.verdict


def test_forecast_trending_when_failure_beyond_horizon():
    # Very gentle decline; failure is far beyond a short horizon.
    series = [0.500, 0.499, 0.498, 0.497, 0.496, 0.495]
    fc = forecast_spectral_decay(series, horizon=5)
    assert fc.status == "trending"
    assert fc.slope < 0
    # failure exists eventually but not within horizon=5
    assert fc.snapshots_to_failure is None or fc.snapshots_to_failure > 5
    assert "no structural failure within" in fc.verdict


def test_forecast_alert_when_already_failed():
    # Current gap already below the failure band.
    series = [0.20, 0.15, 0.12, 0.09, 0.08]
    fc = forecast_spectral_decay(series, horizon=30)
    assert fc.status == "alert"
    assert fc.current_gap < 0.1
    assert fc.snapshots_to_failure == 0
    assert "already in the failure band" in fc.verdict


def test_forecast_rising_gap_is_stable():
    # Improving modular separation -> not a decay alert.
    series = [0.20, 0.30, 0.40, 0.50, 0.60]
    fc = forecast_spectral_decay(series, horizon=30)
    assert fc.status == "stable"
    assert fc.slope > 0
    assert fc.snapshots_to_failure is None


def test_forecast_decay_rate_is_fraction_of_current():
    series = [0.50, 0.45, 0.40, 0.35, 0.30]
    fc = forecast_spectral_decay(series, horizon=30)
    # slope ~ -0.05, current 0.30 -> rate ~ 0.05/0.30 ~= 0.167
    assert 0.0 < fc.predicted_topology_decay_rate <= 1.0
    # The stored rate is rounded to 6 places; compare against the same rounding.
    assert fc.predicted_topology_decay_rate == round(min(1.0, abs(fc.slope) / fc.current_gap), 6)


def test_forecast_to_dict_is_json_shaped():
    fc = forecast_spectral_decay([0.5, 0.45, 0.4, 0.35, 0.3], horizon=30)
    d = fc.to_dict()
    assert isinstance(d, dict)
    for key in (
        "status",
        "current_gap",
        "slope",
        "forecast_gap",
        "predicted_topology_decay_rate",
        "snapshots_to_failure",
        "verdict",
        "compute_degraded",
    ):
        assert key in d


def test_forecast_compute_degraded_propagates():
    fc = forecast_spectral_decay([0.5, 0.4, 0.3, 0.2, 0.1], compute_degraded=True)
    assert fc.compute_degraded is True


# ---------------------------------------------------------------------------
# forecast_from_graphs — end-to-end from graph series
# ---------------------------------------------------------------------------


def test_forecast_from_graphs_detects_decay():
    # A lengthening chain over "time" -> monotonically declining lambda2.
    graphs = [_chain_graph(length) for length in (4, 6, 8, 10, 14, 20)]
    fc = forecast_from_graphs(graphs, horizon=30)
    assert isinstance(fc, SpectralForecast)
    assert fc.history_points == 6
    # Path-graph lambda2 decays as the chain grows, so the trend is downward.
    assert fc.slope < 0.0
    assert fc.gap_series[0] > fc.gap_series[-1]
    assert fc.status in {"trending", "warning", "alert"}


def test_forecast_from_graphs_stable_when_unchanging():
    graphs = [_two_clear_clusters() for _ in range(5)]
    fc = forecast_from_graphs(graphs, horizon=30)
    assert fc.status == "stable"
    assert abs(fc.slope) < 1e-6


def test_forecast_from_graphs_accepts_adjacency():
    adj = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c"]}
    graphs = [adj for _ in range(5)]
    fc = forecast_from_graphs(graphs, horizon=10)
    assert fc.history_points == 5


def test_forecast_from_graphs_degraded_flag_on_all_zero(monkeypatch):
    # Force the eigensolver to report 0.0 for every non-trivial graph (the
    # scipy-missing shape) and assert the loud-fallback flag fires.
    import roam.graph.spectral_forecast as sf

    monkeypatch.setattr(sf, "spectral_gap", lambda g: 0.0)
    graphs = [_two_clear_clusters() for _ in range(5)]
    fc = sf.forecast_from_graphs(graphs, horizon=30)
    assert fc.compute_degraded is True


# ---------------------------------------------------------------------------
# decay_alert_wording — the B8 frame
# ---------------------------------------------------------------------------


def test_alert_wording_warning_uses_cluster_name():
    series = [0.50, 0.45, 0.40, 0.35, 0.30, 0.25]
    fc = forecast_spectral_decay(series, horizon=30)
    msg = decay_alert_wording(fc, cluster="auth/", unit="days")
    assert "auth/" in msg
    assert "becomes circular within" in msg
    assert str(fc.snapshots_to_failure) in msg
    assert "days" in msg


def test_alert_wording_warning_default_subject():
    series = [0.50, 0.45, 0.40, 0.35, 0.30, 0.25]
    fc = forecast_spectral_decay(series, horizon=30)
    msg = decay_alert_wording(fc)
    assert "the largest cluster" in msg
    assert "snapshots" in msg


def test_alert_wording_alert_state():
    series = [0.20, 0.12, 0.09, 0.08, 0.07]
    fc = forecast_spectral_decay(series, horizon=30)
    msg = decay_alert_wording(fc, cluster="core/")
    assert "core/" in msg
    assert "already lost its modular separation" in msg


def test_alert_wording_stable_state():
    fc = forecast_spectral_decay([0.5, 0.5, 0.5, 0.5, 0.5], horizon=30)
    msg = decay_alert_wording(fc, cluster="db/")
    assert "db/" in msg
    assert "stable modular separation" in msg


def test_alert_wording_insufficient_history():
    fc = forecast_spectral_decay([0.5, 0.4])
    msg = decay_alert_wording(fc)
    assert "insufficient spectral history" in msg
