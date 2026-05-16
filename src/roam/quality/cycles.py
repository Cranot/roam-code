"""Canonical dependency-cycle summary.

This is the SINGLE SOURCE OF TRUTH for "how many cycles does this codebase
have?". Before this module existed, three commands disagreed:

- ``roam health`` reported 12 cycles (actionable + ignored mix).
- ``roam describe --agent-prompt`` reported 1 (actionable only).
- ``roam agent-export`` reported 13 (raw SCC count, no filtering).

This is Pattern 3 ("vocabulary mismatch across commands") from the
dogfood ``SYNTHESIS-2026-05-12.md``. The fix mirrors the W16.3 ``ai_rot``
reconciliation: one canonical computation + a ``cycles_definition``
label on every envelope that emits a cycle count.

Algorithm
---------

``find_cycles(G, min_size=2)`` on the full symbol graph returns every
strongly-connected component (SCC) with at least 2 members. Each SCC is
then annotated by ``format_cycles`` + ``mark_actionable_cycles``:

  * ``total``       — all SCCs of size >= 2.
  * ``actionable``  — SCCs that span >= 2 distinct files AND do not touch
                      any test file. Same-file SCCs (e.g. Vue ``<script
                      setup>`` intra-file refs) and test-helper cycles
                      are excluded; they are not architectural.
  * ``informational`` — total - actionable.

The actionable filter lives in ``roam.graph.cycles.mark_actionable_cycles``
and is shared across all consumers.

Each consumer must emit BOTH numbers + the definition label so agents
never see one number in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Single-line label that should appear in every envelope reporting a
# cycle count, under the key ``cycles_definition``.
DEFINITION = (
    "Cycle counts derived from `roam.graph.cycles.find_cycles(G, min_size=2)` "
    "on the symbol graph. `cycles_total` = all SCCs of size >= 2; "
    "`cycles_actionable` = SCCs spanning >=2 files AND no test files "
    "(same-file and test-only cycles are informational). Run `roam health` "
    "for the per-cycle breakdown."
)


def definition() -> str:
    """Return the canonical cycle metric definition string.

    Use this when emitting a JSON envelope that includes a cycle count::

        summary["cycles_definition"] = definition()
    """
    return DEFINITION


@dataclass
class CyclesSummary:
    """Canonical cycle counts.

    Attributes
    ----------
    total : int
        All SCCs of size >= 2 (raw SCC count).
    actionable : int
        SCCs spanning >=2 distinct files AND not touching any test file.
    informational : int
        ``total - actionable`` — same-file or test-only SCCs that are
        not architectural defects.
    definition : str
        The :data:`DEFINITION` string for downstream consumers.
    """

    total: int
    actionable: int
    informational: int
    definition: str = DEFINITION

    def as_envelope_dict(self) -> dict:
        """Render as a dict suitable for embedding in a JSON envelope.

        Includes the definition label inline so consumers that only
        read this nested dict still see the source-of-truth label.
        """
        return {
            "total": self.total,
            "actionable": self.actionable,
            "informational": self.informational,
            "cycles_definition": self.definition,
        }


def cycles_summary(conn) -> CyclesSummary:
    """Compute the canonical cycle summary on the indexed graph.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam DB connection (readonly is fine).

    Returns
    -------
    CyclesSummary
        ``total`` / ``actionable`` / ``informational`` counts. All-zero
        when the graph has zero cycles or the graph build fails.

    Notes
    -----
    Calls ``build_symbol_graph`` + ``find_cycles`` + ``format_cycles`` +
    ``mark_actionable_cycles`` in the same order ``cmd_health`` uses, so
    consumers that bypass this helper still get the same numbers when
    they call the same underlying functions.
    """
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import (
            find_cycles,
            format_cycles,
            mark_actionable_cycles,
        )
    except Exception:
        # Defensive: if networkx / graph module isn't importable we
        # return all-zero rather than crashing the caller. Cycles is an
        # advisory metric — its absence shouldn't break health/describe.
        return CyclesSummary(total=0, actionable=0, informational=0)

    try:
        G = build_symbol_graph(conn)
        raw = find_cycles(G)
        formatted = format_cycles(raw, conn) if raw else []
        mark_actionable_cycles(formatted)
    except Exception:
        return CyclesSummary(total=0, actionable=0, informational=0)

    total = len(formatted)
    actionable = sum(1 for c in formatted if c.get("actionable"))
    informational = total - actionable
    return CyclesSummary(
        total=total,
        actionable=actionable,
        informational=informational,
    )
