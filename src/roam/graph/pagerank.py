"""PageRank and centrality metrics for the symbol graph."""

from __future__ import annotations

import sqlite3

import networkx as nx


def compute_pagerank(G: nx.DiGraph, alpha: float = 0.85) -> dict[int, float]:
    """Compute PageRank scores for every node in *G*.

    Returns ``{symbol_id: pagerank_score}``.  Returns an empty dict when the
    graph has no nodes.
    """
    if len(G) == 0:
        return {}
    return nx.pagerank(G, alpha=alpha)


def compute_centrality(G: nx.DiGraph) -> dict[int, dict]:
    """Compute in-degree, out-degree, and betweenness centrality.

    Returns ``{symbol_id: {"in_degree": int, "out_degree": int,
    "betweenness": float}}``.
    """
    if len(G) == 0:
        return {}

    betweenness = nx.betweenness_centrality(G, normalized=False)
    result: dict[int, dict] = {}
    for node in G.nodes:
        result[node] = {
            "in_degree": G.in_degree(node),
            "out_degree": G.out_degree(node),
            "betweenness": betweenness.get(node, 0.0),
        }
    return result


def store_metrics(conn: sqlite3.Connection, G: nx.DiGraph) -> int:
    """Compute and persist all graph metrics into the ``graph_metrics`` table.

    Returns the number of rows written.
    """
    if len(G) == 0:
        return 0

    pr = compute_pagerank(G)
    centrality = compute_centrality(G)

    rows = []
    for node in G.nodes:
        rows.append((
            node,
            pr.get(node, 0.0),
            centrality[node]["in_degree"],
            centrality[node]["out_degree"],
            centrality[node]["betweenness"],
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_metrics "
        "(symbol_id, pagerank, in_degree, out_degree, betweenness) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)
