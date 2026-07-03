"""Graph-backend dispatcher — NetworkX-compatible (default) or rustworkx.

NetworkX is pure-Python and slows down past ~250k nodes. rustworkx is a
Rust-backed drop-in for many NetworkX algorithms; 3-100× speedup on the
algorithms roam uses heaviest (PageRank, BFS, SCC).

STATUS: this dispatcher is NOT wired into roam's default compute path.
``roam.graph.pagerank.compute_pagerank`` uses its own scipy power-iteration
core (``_pagerank_core``, hang-safe under numpy>=2.4) and does not consult
``ROAM_GRAPH_BACKEND`` — setting the env var changes nothing for built-in
roam commands today. The module is the pinned public surface (behavior
locked by ``tests/test_v12_2.py::TestGraphBackendDispatch``) for the
rustworkx wiring follow-up and for external/plugin callers::

    pip install "roam-code[graph-fast]"
    ROAM_GRAPH_BACKEND=rustworkx  # rustworkx | networkx | auto

The non-rustworkx path delegates to the same roam-owned scipy core the
production commands use, so callers get the identical (hang-safe) ranking;
``nx.pagerank`` remains only as the no-scipy last resort.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

import networkx as nx

# ``pagerank`` is the public alias for the private dispatcher implementation
# below. Built-in commands call ``roam.graph.pagerank`` directly; plugins and
# external callers can import this backend-dispatched compatibility surface.
__all__ = ["active_backend", "pagerank"]


def _backend_choice() -> str:
    """Resolve the active graph backend.

    Priority:
    1. ``ROAM_GRAPH_BACKEND`` env var (``rustworkx`` | ``networkx`` | ``auto``).
    2. ``auto`` — use rustworkx if importable, else networkx.
    3. ``networkx`` is the safe default.
    """
    forced = os.environ.get("ROAM_GRAPH_BACKEND", "auto").lower().strip()
    if forced in ("networkx", "nx"):
        return "networkx"
    if forced in ("rustworkx", "rx"):
        return "rustworkx"
    # auto
    try:
        import rustworkx  # noqa: F401  type: ignore

        return "rustworkx"
    except ImportError:
        return "networkx"


def active_backend() -> str:
    """Public name of the *selected* backend (``networkx`` or ``rustworkx``).

    NOTE: this is the *intended* backend per env-var / auto-detect; the
    actual backend that ran ``pagerank()`` may differ when rustworkx
    fell back to NetworkX on a version mismatch.
    """
    return _backend_choice()


def _try_rustworkx_preserving_networkx_ids(
    G: nx.DiGraph,
    alpha: float,
    personalization: dict[int, float] | None,
) -> dict[int, float] | None:
    """Run rustworkx PageRank while preserving NetworkX node-id semantics."""
    try:
        import rustworkx  # type: ignore

        # Convert the NetworkX DiGraph to a rustworkx PyDiGraph. rustworkx
        # uses integer node indices; build a translation table so we can
        # map results back to roam's symbol_ids.
        rx_g = rustworkx.PyDiGraph(check_cycle=False, multigraph=False)
        node_for: dict[Any, int] = {}
        for n in G.nodes():
            node_for[n] = rx_g.add_node(n)
        for u, v in G.edges():
            rx_g.add_edge(node_for[u], node_for[v], 1)

        # rustworkx pagerank takes node-indexed personalization.
        pers_rx: dict[int, float] | None = None
        if personalization:
            pers_rx = {node_for[n]: w for n, w in personalization.items() if n in node_for}

        scores = rustworkx.pagerank(rx_g, alpha=alpha, personalization=pers_rx)
        # rustworkx returns a numpy-array-shaped result keyed by index.
        inv = {idx: orig for orig, idx in node_for.items()}
        return {inv[idx]: float(score) for idx, score in scores.items()}
    except Exception as exc:  # noqa: BLE001 — version skew / numpy / API drift
        # rustworkx version skew or numpy missing — fall back cleanly to
        # NetworkX. Emit a ``RuntimeWarning`` (consistent with the
        # cycles.py / spectral.py loud-fallback pattern) so degradation
        # surfaces in pytest warnings and CI stderr without polluting the
        # happy path.
        warnings.warn(
            f"rustworkx pagerank failed ({type(exc).__name__}: {exc}); "
            f"falling back to the scipy core — active_backend() reports "
            f"{active_backend()!r} (intended) while pagerank() fell back",
            category=RuntimeWarning,
            stacklevel=2,
        )
        return None


def _pagerank(
    G: nx.DiGraph,
    alpha: float = 0.85,
    personalization: dict[int, float] | None = None,
) -> dict[int, float]:
    """Backend-dispatched PageRank.

    Same shape as ``nx.pagerank(G, alpha=..., personalization=...)`` —
    returns ``{node_id: score}``. Tries rustworkx first when active; falls
    back to NetworkX on any incompatibility (rustworkx's PageRank API
    differs slightly between versions).
    """
    if len(G) == 0:
        return {}

    if _backend_choice() == "rustworkx":
        scores = _try_rustworkx_preserving_networkx_ids(G, alpha, personalization)
        if scores is not None:
            return scores

    # Default path: the roam-owned scipy power-iteration core — same math
    # as nx.pagerank but avoids the sparse-array idioms that hang for
    # minutes on large dangling-heavy graphs under numpy>=2.4 (see the
    # _pagerank_core docstring). It raises ImportError when numpy/scipy
    # are absent; only then do we hand the small-graph case to networkx.
    try:
        from roam.graph.pagerank import _pagerank_core

        return _pagerank_core(G, alpha, personalization)
    except ImportError:
        return nx.pagerank(G, alpha=alpha, personalization=personalization)


pagerank = _pagerank
