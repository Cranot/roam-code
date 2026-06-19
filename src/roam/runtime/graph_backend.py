"""Graph-backend dispatcher — NetworkX (default) or rustworkx (fast extra).

NetworkX is pure-Python and slows down past ~250k nodes. rustworkx is a
Rust-backed drop-in for many NetworkX algorithms; 3-100× speedup on the
algorithms roam uses heaviest (PageRank, BFS, SCC).

Activation: install with the ``graph-fast`` extra and set
``ROAM_GRAPH_BACKEND=rustworkx`` (or rely on auto-detect when both
modules are present)::

    pip install "roam-code[graph-fast]"
    ROAM_GRAPH_BACKEND=rustworkx roam impact MyClass

API surface is the small subset roam actually uses. Falls back to NetworkX
when rustworkx isn't installed or the algorithm isn't supported.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

import networkx as nx


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


def pagerank(G: nx.DiGraph, alpha: float = 0.85, personalization: dict[int, float] | None = None) -> dict[int, float]:
    """Backend-dispatched PageRank.

    Same shape as ``nx.pagerank(G, alpha=..., personalization=...)`` —
    returns ``{node_id: score}``. Tries rustworkx first when active; falls
    back to NetworkX on any incompatibility (rustworkx's PageRank API
    differs slightly between versions).
    """
    if len(G) == 0:
        return {}

    if _backend_choice() == "rustworkx":
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
                "falling back to NetworkX — active_backend() still reports 'rustworkx' "
                "while pagerank() used NetworkX",
                category=RuntimeWarning,
                stacklevel=2,
            )

    # NetworkX path
    return nx.pagerank(G, alpha=alpha, personalization=personalization)
