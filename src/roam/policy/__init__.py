"""Graph-aware policy clauses for ``roam rules``.

This package provides the four clause types added in R18:

* ``reachable_from`` — BFS reachability check on the call/import graph.
* ``imports_from`` — file-level import prefix match using ``file_edges``.
* ``clones_with`` — clone-cluster membership check via ``clone_pairs``.
* ``tested_by`` — reachability from test files (graph + ``file_roles``).

Each clause is a small pure function in :mod:`roam.policy.graph_clauses`
that takes a DB connection, the clause arguments, and a target file or
symbol, and returns ``(matches: bool, evidence: dict)``. ``matches=True``
means the clause is SATISFIED — the caller decides whether that is a
violation (``must_not``) or a pass (``must``).
"""

from __future__ import annotations
