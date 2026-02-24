
dest = r"D:\OneDrive - CosmoHac\Project\roam-code\tests\test_context_propagation.py"

parts = []

parts.append("""from __future__ import annotations
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

""")

with open(dest, "w", encoding="utf-8") as f:
    for p in parts:
        f.write(p)
print("OK")
