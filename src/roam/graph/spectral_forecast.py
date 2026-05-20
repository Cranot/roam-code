"""B8 prototype: spectral-instability signal for architectural-drift forecasting.

Combines the spectral analysis in ``roam.graph.spectral`` (Fiedler vector /
algebraic connectivity / spectral gap) with the trend-projection approach used
by ``roam.commands.cmd_forecast`` (Theil-Sen regression over snapshot history).

The thesis (per ``dev/ARCHITECTURE-FUTURES.md`` B8): count-based metrics
(cycles, avg_complexity, dead_exports) tell you a codebase is degrading *after*
the damage is structural. Algebraic connectivity (lambda2, the spectral gap)
tells you a graph is *losing its modular separation* — clusters that used to be
distinct are merging into one tangled blob. A declining spectral gap is an
early warning that count-based metrics have not yet caught.

Design constraints (collision-safe prototype):
  * Pure functions over a NetworkX graph OR an adjacency mapping. No DB access,
    no CLI coupling, no import-time side effects.
  * scipy/numpy optional. ``spectral_gap`` already degrades to a 0.0 sentinel +
    RuntimeWarning when the eigensolver is unavailable; this module mirrors that
    lineage discipline (a degraded compute is disclosed, never silent).
  * No new vocabulary leaks into the closed-enum surfaces (findings registry,
    evidence vocabulary). Status strings here are PROTOTYPE-local and map onto
    the existing forecast ``stable / trending / warning / alert`` enum at wire
    time — see ``(internal memo)``.

Public API:
  - ``spectral_instability(graph_or_adj) -> SpectralInstability``
        one-shot spectral snapshot of a single graph.
  - ``forecast_spectral_decay(history, horizon=30, ...) -> SpectralForecast``
        project a sequence of spectral-gap snapshots forward.
  - ``forecast_from_graphs(graphs, horizon=30, ...) -> SpectralForecast``
        convenience: compute the gap series from a list of graphs, then project.
  - ``decay_alert_wording(forecast, *, cluster=None, unit="snapshots") -> str``
        render the "<cluster> becomes circular within <N> snapshots" frame.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

import networkx as nx

from roam.graph.anomaly import theil_sen_slope
from roam.graph.spectral import spectral_gap, verdict_from_gap

# ---------------------------------------------------------------------------
# Tunables (PROTOTYPE-local; promote to config at wire time if needed)
# ---------------------------------------------------------------------------

# Spectral gap below this is "structurally failed" — the graph has lost its
# modular separation (a single tangled component). Mirrors spectral._MED_GAP /
# _HIGH_GAP banding so the verdict vocabulary stays consistent across modules.
_FAILURE_GAP = 0.1

# A decline of at least this magnitude (gap units per snapshot) is needed to
# treat the spectral signal as "decaying" at all. Below it, call it stable.
_MIN_DECAY_SLOPE = 1e-3

# Theil-Sen needs n>=4. With fewer points we report current state only.
_MIN_HISTORY = 4

# Cap projection-window reporting so a vanishingly small slope doesn't print an
# astronomically large "days until failure". Anything beyond this is "no
# foreseeable failure within horizon".
_MAX_REPORTABLE_WINDOW = 10_000


# ---------------------------------------------------------------------------
# Result dataclasses (pure data; JSON-serializable via ``to_dict``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpectralInstability:
    """One-shot spectral health of a single graph."""

    spectral_gap: float
    verdict: str
    node_count: int
    component_count: int
    is_failed: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SpectralForecast:
    """Projection of a spectral-gap time series toward structural failure."""

    # PROTOTYPE-local status enum. Maps onto forecast's stable/trending/
    # warning/alert at wire time (see the integration plan doc).
    status: str  # stable | trending | warning | alert | insufficient_history
    current_gap: float
    slope: float  # gap units per snapshot (negative == decaying)
    forecast_gap: float
    forecast_horizon: int
    # predicted_topology_decay_rate: fraction of the current gap lost per
    # snapshot (0.0 == stable, 0.05 == losing 5% of modular separation/step).
    predicted_topology_decay_rate: float
    # snapshots_to_failure: projected steps until gap crosses _FAILURE_GAP.
    # None when not decaying toward failure within the reportable window.
    snapshots_to_failure: int | None
    history_points: int
    verdict: str
    compute_degraded: bool = False
    gap_series: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Adjacency -> graph coercion
# ---------------------------------------------------------------------------


def _coerce_graph(graph_or_adj) -> nx.Graph:
    """Accept a NetworkX graph OR an adjacency mapping {node: [neighbors]}.

    Returns an undirected ``nx.Graph`` (spectral analysis operates on the
    undirected projection regardless of input directionality).
    """
    if isinstance(graph_or_adj, nx.Graph):
        return graph_or_adj.to_undirected() if graph_or_adj.is_directed() else graph_or_adj
    if isinstance(graph_or_adj, Mapping):
        G = nx.Graph()
        G.add_nodes_from(graph_or_adj.keys())
        for node, neighbors in graph_or_adj.items():
            for nbr in neighbors:
                G.add_edge(node, nbr)
        return G
    raise TypeError(f"expected a networkx Graph or an adjacency Mapping, got {type(graph_or_adj).__name__}")


# ---------------------------------------------------------------------------
# One-shot spectral instability
# ---------------------------------------------------------------------------


def spectral_instability(graph_or_adj) -> SpectralInstability:
    """Compute the spectral health of a single graph.

    ``spectral_gap`` returns 0.0 for trivial/disconnected graphs and on
    eigensolver failure (with a RuntimeWarning in the failure case). A 0.0 gap
    is therefore reported as ``is_failed`` only when there is real structure to
    fail on — i.e. a connected graph of >= 2 nodes. A disconnected graph is
    *already* maximally un-modular, so 0.0 there is also a failure signal.
    """
    G = _coerce_graph(graph_or_adj)
    node_count = G.number_of_nodes()
    component_count = nx.number_connected_components(G) if node_count else 0
    gap = spectral_gap(G)
    # A connected non-trivial graph with gap below the failure band has lost
    # its modular separation. A multi-component graph is trivially "failed"
    # from a single-blob-cohesion standpoint only if it has real size.
    is_failed = node_count >= 2 and gap < _FAILURE_GAP
    return SpectralInstability(
        spectral_gap=round(gap, 6),
        verdict=verdict_from_gap(gap),
        node_count=node_count,
        component_count=component_count,
        is_failed=is_failed,
    )


# ---------------------------------------------------------------------------
# Spectral decay projection
# ---------------------------------------------------------------------------


def _project_snapshots_to_failure(current: float, slope: float) -> int | None:
    """Solve ``current + slope * n == _FAILURE_GAP`` for the smallest n >= 1.

    Only meaningful when the gap is *decaying* toward the failure band from
    above it. Returns None when the trajectory never reaches failure within the
    reportable window (rising, flat, or already below the band).
    """
    if current <= _FAILURE_GAP:
        # Already at/below the failure band — failure is "now", not a horizon.
        return 0
    if slope >= -_MIN_DECAY_SLOPE:
        # Flat or rising: no foreseeable failure.
        return None
    n = (_FAILURE_GAP - current) / slope  # both numerator and slope negative
    n_int = int(n) + 1  # ceil to the first snapshot at/under the band
    if n_int < 1 or n_int > _MAX_REPORTABLE_WINDOW:
        return None
    return n_int


def forecast_spectral_decay(
    history: Sequence[float],
    horizon: int = 30,
    *,
    compute_degraded: bool = False,
) -> SpectralForecast:
    """Project a sequence of spectral-gap snapshots forward via Theil-Sen.

    Parameters
    ----------
    history:
        Chronologically ordered spectral-gap values (oldest first). Typically
        one ``spectral_gap`` per health snapshot.
    horizon:
        Look-ahead window, in snapshots, for the forecast gap value. Mirrors
        ``cmd_forecast``'s ``--horizon``.
    compute_degraded:
        Set True by callers when the gap series was computed under a degraded
        eigensolver (scipy missing). Propagated to the result so the lineage is
        loud, never silent.

    Status mapping (PROTOTYPE-local):
      * insufficient_history — fewer than 4 usable points.
      * stable               — slope shallower than the decay threshold.
      * trending             — decaying, but failure beyond the horizon.
      * warning              — failure projected within the horizon.
      * alert                — current gap already in the failure band.
    """
    series = [float(v) for v in history]
    n = len(series)

    if n == 0:
        return SpectralForecast(
            status="insufficient_history",
            current_gap=0.0,
            slope=0.0,
            forecast_gap=0.0,
            forecast_horizon=horizon,
            predicted_topology_decay_rate=0.0,
            snapshots_to_failure=None,
            history_points=0,
            verdict="insufficient spectral history for a forecast",
            compute_degraded=compute_degraded,
            gap_series=series,
        )

    current = series[-1]

    if n < _MIN_HISTORY:
        return SpectralForecast(
            status="insufficient_history",
            current_gap=round(current, 6),
            slope=0.0,
            forecast_gap=round(current, 6),
            forecast_horizon=horizon,
            predicted_topology_decay_rate=0.0,
            snapshots_to_failure=None,
            history_points=n,
            verdict=(
                f"insufficient spectral history ({n} snapshot(s); need >= {_MIN_HISTORY}); "
                f"current gap {current:.3f} ({verdict_from_gap(current)})"
            ),
            compute_degraded=compute_degraded,
            gap_series=series,
        )

    ts = theil_sen_slope(series)
    slope = ts["slope"] if ts else 0.0
    forecast_gap = current + slope * horizon

    # decay rate: fraction of current gap lost per snapshot (only count decay).
    if current > 0 and slope < 0:
        decay_rate = min(1.0, abs(slope) / current)
    else:
        decay_rate = 0.0

    snapshots_to_failure = _project_snapshots_to_failure(current, slope)

    # Status classification (current state dominates, then trajectory).
    if current < _FAILURE_GAP:
        status = "alert"
    elif slope > -_MIN_DECAY_SLOPE:
        status = "stable"
    elif snapshots_to_failure is not None and snapshots_to_failure <= horizon:
        status = "warning"
    else:
        status = "trending"

    verdict = _build_verdict(status, current, slope, forecast_gap, horizon, snapshots_to_failure)

    return SpectralForecast(
        status=status,
        current_gap=round(current, 6),
        slope=round(slope, 6),
        forecast_gap=round(forecast_gap, 6),
        forecast_horizon=horizon,
        predicted_topology_decay_rate=round(decay_rate, 6),
        snapshots_to_failure=snapshots_to_failure,
        history_points=n,
        verdict=verdict,
        compute_degraded=compute_degraded,
        gap_series=series,
    )


def _build_verdict(status, current, slope, forecast_gap, horizon, snapshots_to_failure) -> str:
    """Build a single-line, LAW-6-self-sufficient verdict.

    Terminal token anchors on a concrete-noun plural (``snapshots`` / ``gap``)
    so the wire-time LAW-4 lint can accept the fact derived from it.
    """
    band = verdict_from_gap(current)
    if status == "alert":
        return (
            f"spectral gap {current:.3f} already in the failure band "
            f"({band}); structural separation lost across {horizon} snapshots"
        )
    if status == "warning":
        return (
            f"spectral gap decaying {slope:+.4f}/snapshot "
            f"(now {current:.3f}, {band}); structural failure projected within "
            f"{snapshots_to_failure} snapshots"
        )
    if status == "trending":
        return (
            f"spectral gap decaying {slope:+.4f}/snapshot "
            f"(now {current:.3f}, forecast {forecast_gap:.3f} in {horizon} snapshots); "
            f"no structural failure within {horizon} snapshots"
        )
    return f"spectral gap stable at {current:.3f} ({band}) across {horizon} snapshots"


def forecast_from_graphs(
    graphs: Sequence,
    horizon: int = 30,
) -> SpectralForecast:
    """Compute a spectral-gap series from a list of graphs, then project it.

    ``graphs`` is a chronologically ordered sequence (oldest first) of
    NetworkX graphs or adjacency mappings — e.g. one snapshot graph per health
    snapshot. Convenience wrapper over ``forecast_spectral_decay``.

    A degraded eigensolver surfaces as ``spectral_gap`` returning 0.0 + a
    RuntimeWarning (raised by ``spectral._compute_algebraic_connectivity``).
    We detect the all-zero-on-non-trivial-input shape and flag
    ``compute_degraded`` so the lineage is loud.
    """
    coerced = [_coerce_graph(g) for g in graphs]
    series = [spectral_gap(g) for g in coerced]
    # Loud-fallback lineage: if every graph had real structure (>=2 connected
    # nodes) yet every gap is exactly 0.0, the eigensolver is almost certainly
    # unavailable rather than the topology genuinely being a flat blob.
    degraded = (
        bool(series)
        and all(v == 0.0 for v in series)
        and any(g.number_of_nodes() >= 2 and nx.number_connected_components(g) == 1 for g in coerced)
    )
    return forecast_spectral_decay(series, horizon=horizon, compute_degraded=degraded)


# ---------------------------------------------------------------------------
# Alert wording (the B8 "<N> until structural failure" frame)
# ---------------------------------------------------------------------------


def decay_alert_wording(
    forecast: SpectralForecast,
    *,
    cluster: str | None = None,
    unit: str = "snapshots",
) -> str:
    """Render the user-proposed B8 alert wording.

    Shape (per ARCHITECTURE-FUTURES.md B8):
      "<cluster> becomes circular within <N> <unit>"

    Falls back to a generic subject when no cluster name is supplied. Returns a
    reassuring line for stable / non-failing forecasts so callers can always
    surface *something* without branching on status.
    """
    subject = cluster or "the largest cluster"
    if forecast.status == "alert":
        return f"{subject} has already lost its modular separation (spectral gap {forecast.current_gap:.3f})"
    if forecast.status == "warning" and forecast.snapshots_to_failure is not None:
        rate_pct = round(forecast.predicted_topology_decay_rate * 100, 1)
        return (
            f"At the current decay rate ({rate_pct}% of modular separation lost per {unit[:-1]}), "
            f"{subject} becomes circular within {forecast.snapshots_to_failure} {unit}"
        )
    if forecast.status == "trending":
        return (
            f"{subject} is slowly losing modular separation "
            f"(forecast gap {forecast.forecast_gap:.3f} in {forecast.forecast_horizon} {unit}); "
            f"no structural failure within {forecast.forecast_horizon} {unit}"
        )
    if forecast.status == "insufficient_history":
        return f"insufficient spectral history to forecast structural failure for {subject}"
    return f"{subject} retains stable modular separation (spectral gap {forecast.current_gap:.3f})"
