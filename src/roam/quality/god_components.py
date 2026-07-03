"""Canonical god-component count.

This is the SINGLE SOURCE OF TRUTH for "god component" — a symbol whose
fan-in + fan-out exceeds a degree threshold. Before this module existed,
two commands disagreed:

- ``roam health`` reported 50 god components (degree-threshold + utility-
  aware severity bands, top-50 sample).
- ``roam fingerprint`` reported 1015 "god_objects" (degree > 2 *
  avg_degree, computed across every node — a different algorithm).

This is Pattern 3 ("vocabulary mismatch across commands") from the
dogfood `the dogfood synthesis notes`. The fix mirrors the W16.3 ``ai_rot``
reconciliation: one canonical computation + a ``god_components_definition``
label on every envelope that emits the number.

Naming
------

The canonical name is ``god_components``. Earlier code used
``god_objects`` (fingerprint) and ``god_classes`` (rule check). These
are retained as transitional aliases inside ``GodComponentsSummary``
so downstream consumers that grep for the old keys keep working, but
new code MUST use ``god_components``.

Algorithm
---------

Reads ``graph_metrics`` rows (in_degree + out_degree) joined with
``symbols`` and ``files`` via the ``TOP_BY_DEGREE`` query. A symbol is
counted as a god component when ``in_degree + out_degree > 20`` and
its file is not a utility (i.e., not under ``utils/`` / ``helpers/`` /
``vendor/`` per ``cmd_health._is_utility_path``).

Severity bands (matching ``cmd_health``):

  Standard files (non-utility):
    > 50  → CRITICAL
    > 30  → WARNING
    other → INFO

  Utility files (utils/helpers/vendor):
    > 150 → CRITICAL
    > 90  → WARNING
    other → INFO
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

# Symbol kinds that are *data attributes*, not refactorable units of logic.
# A dataclass field / class property / module variable can accrue very high
# reference fan-in (e.g. `EvidenceArtifact.path`, degree 2408) without being a
# "god component" — there is no logic to decompose. Classification must consult
# the symbol kind so these never land in the actionable CRITICAL band. The set
# covers the kind vocabulary the extractors emit for plain attributes across
# languages (Python `prop`, JS/TS `field`/`property`, generic `var`/`const`).
_DATA_ATTRIBUTE_KINDS = frozenset({"prop", "property", "field", "attribute", "variable", "var", "const", "constant"})

# Single-line label that should appear in every envelope reporting a
# god-component count, under the key ``god_components_definition``.
DEFINITION = (
    "God components: symbols where `(in_degree + out_degree) > 20` from "
    "the `graph_metrics` table, with utility-aware severity bands "
    "(standard >50=CRITICAL >30=WARNING; utility >150=CRITICAL >90=WARNING). "
    "Run `roam health` for the per-symbol breakdown. "
    "Legacy aliases: `god_objects` (fingerprint), `god_classes` (rules)."
)


def definition() -> str:
    """Return the canonical god-component metric definition string."""
    return DEFINITION


@dataclass
class GodComponentsSummary:
    """Canonical god-component summary.

    Attributes
    ----------
    total : int
        Total god components after degree thresholding.
    critical : int
        Subset whose severity classification is CRITICAL.
    actionable : int
        Subset whose file is non-utility (i.e., real source code that
        an engineer can act on; vendor/helpers are excluded).
    utility : int
        Subset whose file matches a utility path heuristic.
    items : list
        Per-symbol records: ``{name, kind, degree, file, severity,
        category}``. Empty when ``include_items=False`` (the default for
        non-health consumers that just need a count).
    definition : str
        The :data:`DEFINITION` string.
    fallback_used : bool
        ``True`` when the all-zero result is a swallowed-exception
        fallback (``graph_metrics`` query failed, no rows, etc.) rather
        than a genuine god-component-free codebase. Per CLAUDE.md
        "Make fallback chains loud".
    fallback_reason : str
        Short identifier (``"query_failed"`` or ``""``).
    """

    total: int
    critical: int
    actionable: int
    utility: int
    items: list = field(default_factory=list)
    definition: str = DEFINITION
    fallback_used: bool = False
    fallback_reason: str = ""

    def as_envelope_dict(self) -> dict:
        """Render as a dict suitable for embedding in a JSON envelope."""
        out: dict = {
            "total": self.total,
            "critical": self.critical,
            "actionable": self.actionable,
            "utility": self.utility,
            "god_components_definition": self.definition,
        }
        if self.fallback_used:
            out["fallback_used"] = True
            out["fallback_reason"] = self.fallback_reason
        return out


def _severity_band(degree: int, critical_cut: int, warning_cut: int) -> str:
    """Map a degree onto a severity band.

    Exists so the utility (150/90) and standard (50/30) threshold pairs
    are band *data* passed as arguments, not two cloned if/elif ladders.
    """
    if degree > critical_cut:
        return "CRITICAL"
    if degree > warning_cut:
        return "WARNING"
    return "INFO"


def _classify_and_count(god_items: list[dict]) -> tuple[int, int, int]:
    """Annotate each item with ``category``/``severity``; return counts.

    Exists to keep severity *policy* (data-attribute guard, utility-aware
    bands) out of the fetch/filter pipeline in :func:`god_components`.
    Mutates ``god_items`` in place; returns ``(critical, actionable,
    utility)`` counts.
    """
    from roam.commands.cmd_health import _is_utility_path

    critical = 0
    actionable_count = 0
    utility_count = 0
    for g in god_items:
        # Kind-aware guard: a data attribute (dataclass field / property /
        # module var) with high fan-in is NOT a refactorable god component —
        # there is no logic to decompose. Band it non-actionable + cap at INFO
        # so it never pollutes the CRITICAL count or the refactor headline.
        if (g["kind"] or "").lower() in _DATA_ATTRIBUTE_KINDS:
            g["category"] = "data_attribute"
            g["severity"] = "INFO"
            continue
        if _is_utility_path(g["file"]):
            g["category"] = "utility"
            utility_count += 1
            g["severity"] = _severity_band(g["degree"], 150, 90)
        else:
            g["category"] = "actionable"
            actionable_count += 1
            g["severity"] = _severity_band(g["degree"], 50, 30)
        if g["severity"] == "CRITICAL":
            critical += 1
    return critical, actionable_count, utility_count


def god_components(
    conn,
    *,
    top_n: int = 50,
    degree_threshold: int = 20,
    include_items: bool = False,
) -> GodComponentsSummary:
    """Compute the canonical god-component summary.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open roam DB connection (readonly is fine).
    top_n : int, default 50
        How many top-degree symbols to fetch from ``graph_metrics``. The
        existing ``cmd_health`` uses 50; raise this to inspect deeper
        into the tail.
    degree_threshold : int, default 20
        Minimum ``in_degree + out_degree`` required to count as a god
        component. Matches ``cmd_health``.
    include_items : bool, default False
        Populate the ``items`` field with per-symbol records. Health
        needs them for severity breakdown; lightweight consumers
        (agent-export, fingerprint, etc.) just need a count.

    Returns
    -------
    GodComponentsSummary
        All counts derived from a single DB pass — deterministic for a
        fixed DB state.
    """
    from roam.db.queries import TOP_BY_DEGREE

    try:
        rows = conn.execute(TOP_BY_DEGREE, (top_n,)).fetchall()
    except sqlite3.Error:
        # Stamp the fallback flag so consumers can distinguish a
        # crashed query from a clean codebase (CLAUDE.md "Make
        # fallback chains loud").
        return GodComponentsSummary(
            total=0,
            critical=0,
            actionable=0,
            utility=0,
            fallback_used=True,
            fallback_reason="query_failed",
        )

    god_items: list[dict] = []
    for r in rows:
        total_deg = (r["in_degree"] or 0) + (r["out_degree"] or 0)
        if total_deg > degree_threshold:
            god_items.append(
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "degree": total_deg,
                    "file": r["file_path"],
                }
            )

    critical, actionable_count, utility_count = _classify_and_count(god_items)

    return GodComponentsSummary(
        total=len(god_items),
        critical=critical,
        actionable=actionable_count,
        utility=utility_count,
        items=god_items if include_items else [],
    )
