"""roam critique — graph-grounded patch verifier (A.2).

The verifier ingests a unified diff and asks four questions about the
patch, each grounded in a roam DB query (not vibes):

1. **What likely breaks** — blast radius of changed symbols, intersected
   with runtime hotspots and vuln-reach.
2. **What got missed** — clone siblings of changed symbols that did
   not receive analogous edits. (The killer signal, unblocked by A.0.)
3. **What was assumed** — invariants, gate-rule violations, dark-matter
   co-change partners that should have moved with this file but didn't.
4. **Did it do the task** — semantic-diff vs the stated PR/commit intent.

Currently implements (1), (2), and (4); (3) is reserved behind clear
hooks for future expansion.

Public API:

    from roam.critique.checks import (
        parse_diff,                   # unified diff → list of changed regions
        find_changed_symbols,         # changed regions → DB symbols
        check_clones_not_edited,      # the killer check (A.0-backed)
        check_impact,                 # blast radius
    )
    from roam.critique.aggregator import (
        Finding,
        aggregate,                    # checks → ranked verdict
    )
"""

from __future__ import annotations
