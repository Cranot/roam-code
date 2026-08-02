"""Canonical 0-100 composite health score — the SINGLE SOURCE OF TRUTH.

W1451. Before this module existed there were **two** implementations of the
same score, and they disagreed on the live index at the same instant::

    roam health                     score 71    tangle_ratio 0.0    cycles 0
    metrics_history.collect_metrics score 64    tangle_ratio 3.6    cycles 39

That divergence was not cosmetic — it produced a **confident false report**.
``collect_metrics`` is what gets written into the ``snapshots`` table, and
``roam health --baseline <ref>`` compares *today's* ``cmd_health`` number
against *yesterday's* stored ``collect_metrics`` number. Because the two
implementations computed different things, every ``--baseline`` run on an
unchanged repository reported a phantom ``+7`` score improvement and 39
"fixed" cycles that nobody had fixed. A user reads that as progress they did
not make.

Root cause
----------
``cmd_health`` filtered the tangle-ratio numerator to *actionable* SCCs
(SCCs spanning >= 2 files with no test file). Everything else in the estate
counts **all** non-trivial SCCs:

* :data:`roam.output.metric_definitions.TANGLE_RATIO_DEFINITION` — "fraction
  of symbols inside non-trivial SCCs". No actionability qualifier.
* ``roam fingerprint`` (:mod:`roam.graph.fingerprint`) — ``find_cycles(G,
  min_size=2)``, ``tangled_nodes / n_nodes``.
* ``collect_metrics`` — all SCC members / ``COUNT(*) FROM symbols``.

Measured on this repository the graph node count and the symbol row count are
the same population (44688), so ``fingerprint``'s ``0.0359`` and
``collect_metrics``' ``3.6`` are the *same measurement* differing only by the
percent factor, while ``cmd_health``'s ``0.0`` was a different measurement
wearing the same name. **collect_metrics was right; cmd_health was wrong.**

Units
-----
The health/snapshot family reports tangle ratio as a **percent** (``3.6``);
``roam fingerprint`` reports the same quantity as a **fraction** (``0.0359``).
Both conventions are load-bearing where they live — the percent feeds
``.roam-gates.yml``'s ``tangle_max`` threshold, the ``snapshots.tangle_ratio``
column's stored history and this module's :data:`TANGLE_RATIO_SCALE` of 10;
the fraction feeds ``fingerprint --compare``'s ``max_range: 1.0`` similarity
math. Rather than break either, the invariant is stated explicitly and
guarded::

    health_percent == round(fingerprint_fraction * 100, 1)

and the ``*_definition`` sidecars now name their unit so "fraction" can never
again be read as licence to emit a differently-filtered number.

Ownership
---------
Owned by this module. Consumed by ``roam health`` (:mod:`roam.commands.
cmd_health`) and the snapshot writer (:mod:`roam.commands.metrics_history`),
which are the two call sites ``roam health --baseline`` compares against each
other. ``roam trends`` / ``roam forecast`` / ``roam alerts`` read the same
``snapshots`` rows and therefore inherit the fix for free.

Every tuned constant below is carried over **verbatim** from the pre-W1451
``cmd_health`` implementation. This module de-duplicates the score; it does
not redesign it.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Iterable, NamedTuple, Sequence

# ---------------------------------------------------------------------------
# Tuned constants — carried over verbatim from cmd_health's pre-W1451 inline
# implementation. Do NOT retune here; a score redesign is a separate spec.
# ---------------------------------------------------------------------------

#: Sigmoid scales. ``h = e^(-signal / scale)``; 1.0 = pristine, -> 0 = worst.
TANGLE_RATIO_SCALE = 10.0
GOD_SIGNAL_SCALE = 1.5
BOTTLENECK_SIGNAL_SCALE = 1.0
LAYER_VIOLATION_SCALE = 5.0

#: Base factor weights. Sum to 1.0 before the optional coverage factor.
TANGLE_RATIO_WEIGHT = 0.30
GOD_COMPONENT_WEIGHT = 0.20
BOTTLENECK_WEIGHT = 0.15
LAYER_VIOLATION_WEIGHT = 0.15
FILE_HEALTH_WEIGHT = 0.20

#: Imported test coverage (#134): when available, reserve 10% of the score
#: weight and rescale the base factors to 90%.
COVERAGE_WEIGHT = 0.10
COVERAGE_RESCALE = 0.90
COVERAGE_FACTOR_FLOOR = 0.05

#: Signal derivation. ``size_norm`` normalises per 1k symbols so a 14k-symbol
#: repo with 23 actionable god components (0.16%) does not score the same as a
#: 100-symbol repo with 23 (23%).
SIZE_NORM_UNIT = 1000.0
GOD_CRITICAL_DEGREE = 50
GOD_CRITICAL_WEIGHT = 3.0
GOD_ITEM_WEIGHT = 0.5
BOTTLENECK_CRITICAL_WEIGHT = 2.0
BOTTLENECK_ITEM_WEIGHT = 0.3

#: ``file_stats.health_score`` is a 0-10 scale; map it onto a 0-1 factor.
FILE_HEALTH_MAX = 10.0

#: Factor names, positionally aligned with :meth:`HealthScore.factors`.
BASE_FACTOR_NAMES: tuple[str, ...] = (
    "tangle_ratio",
    "god_components",
    "bottlenecks",
    "layer_violations",
    "file_health",
)
COVERAGE_FACTOR_NAME = "imported_coverage"

#: Pattern-3a sidecar. Names the exact computation AND its unit, so the
#: percent-vs-fraction split between `roam health` and `roam fingerprint`
#: is a stated convention rather than an undetected divergence.
TANGLE_RATIO_DEFINITION = (
    "percent of indexed symbols inside a non-trivial SCC (size >= 2) of the symbol graph:"
    " |union of all SCC members| / COUNT(symbols) * 100, rounded to 1 decimal."
    " Counts ALL non-trivial SCCs, not just actionable ones."
    " `roam fingerprint` reports the identical quantity as a FRACTION (percent / 100)."
)

DEFINITION = (
    "Composite 0-100 health score from `roam.quality.health_score.compute_health_score`:"
    " weighted geometric mean of 5 sigmoid factors (tangle_ratio .30, god_components .20,"
    " bottlenecks .15, layer_violations .15, file_health .20), rescaled to .90 with a .10"
    " imported-coverage factor when coverage data is present. Non-compensatory: a zero in"
    " any dimension cannot be masked by the others. Shared verbatim by `roam health` and"
    " the `snapshots` writer so a --baseline comparison is apples-to-apples."
)


class TangleRatio(NamedTuple):
    """Tangle ratio plus the raw counts behind it."""

    #: Percent of symbols inside a non-trivial SCC, rounded to 1 decimal.
    ratio: float
    #: Size of the union of all non-trivial SCC member sets.
    tangled_symbols: int
    #: Denominator actually used.
    total_symbols: int

    @property
    def fraction(self) -> float:
        """The same measurement in ``roam fingerprint``'s fraction convention."""
        if self.total_symbols <= 0:
            return 0.0
        return self.tangled_symbols / self.total_symbols


class HealthScore(NamedTuple):
    """Result of one canonical score computation."""

    score: int
    tangle_ratio: float
    god_signal: float
    bottleneck_signal: float
    #: ``[(health_fraction, weight), ...]`` in :data:`BASE_FACTOR_NAMES` order,
    #: with the coverage factor appended when present.
    factors: tuple[tuple[float, float], ...]
    factor_names: tuple[str, ...]

    def breakdown(self) -> list[dict[str, Any]]:
        """Per-factor contributions for ``roam health --explain``.

        Each entry's ``loss_pp`` is the percentage points that factor is
        pulling off a perfect 100. Sorted worst-first.
        """
        rows = [
            {
                "factor": name,
                "health": round(h, 3),
                "weight": round(w, 2),
                "loss_pp": round((1 - h) * w * 100, 1),
            }
            for (h, w), name in zip(self.factors, self.factor_names)
        ]
        rows.sort(key=lambda b: b["loss_pp"], reverse=True)
        return rows


def health_factor(value: float, scale: float) -> float:
    """Sigmoid health factor: 1.0 for no issues, -> 0 for many."""
    return math.exp(-value / scale) if scale > 0 else 1.0


def tangled_symbol_ids(sccs: Iterable[Sequence[int]]) -> set[int]:
    """Union of every member of every non-trivial SCC.

    *sccs* is the output of :func:`roam.graph.cycles.find_cycles` (default
    ``min_size=2``), i.e. already restricted to non-trivial components. No
    actionability filter is applied — that filter is what made ``roam health``
    disagree with the rest of the estate (see the module docstring).
    """
    out: set[int] = set()
    for scc in sccs or ():
        out.update(scc)
    return out


def compute_tangle_ratio(sccs: Iterable[Sequence[int]], total_symbols: int) -> TangleRatio:
    """Percent of symbols inside a non-trivial SCC. See :data:`TANGLE_RATIO_DEFINITION`."""
    ids = tangled_symbol_ids(sccs)
    if total_symbols <= 0:
        return TangleRatio(0.0, len(ids), 0)
    return TangleRatio(round(len(ids) / total_symbols * 100, 1), len(ids), total_symbols)


def _is_utility(file_path: Any) -> bool:
    """Utility/infrastructure-path predicate.

    Imported lazily from ``cmd_health`` (which owns the pattern tables and has
    the test coverage for them) to keep ``roam.quality`` free of a module-level
    dependency on ``roam.commands``. Mirrors the same lazy import in
    :mod:`roam.quality.god_components`.
    """
    from roam.commands.cmd_health import _is_utility_path

    return _is_utility_path(file_path or "")


def _size_norm(total_symbols: int) -> float:
    return max(1.0, (total_symbols or 0) / SIZE_NORM_UNIT)


def god_component_signal(god_items: Iterable[dict], total_symbols: int) -> float:
    """Size-normalised god-component pressure.

    Counts *actionable* items only — utilities (string/path/datetime helpers,
    the Click root group, MCP dispatch) are expected to carry high fan-in and
    would otherwise dominate the formula. Each item needs a ``file`` and a
    ``degree`` key.

    Equivalent to ``cmd_health``'s pre-W1451 ``category``/``severity`` form:
    ``category == "actionable"`` iff the path is not a utility path, and an
    actionable god component is ``critical`` iff ``degree > 50``.
    """
    actionable = [g for g in god_items or () if not _is_utility(g.get("file"))]
    critical = sum(1 for g in actionable if (g.get("degree") or 0) > GOD_CRITICAL_DEGREE)
    return (critical * GOD_CRITICAL_WEIGHT + len(actionable) * GOD_ITEM_WEIGHT) / _size_norm(total_symbols)


def bottleneck_signal(bn_items: Iterable[dict], bn_p90: float, total_symbols: int) -> float:
    """Size-normalised bottleneck pressure. Actionable items only.

    Each item needs a ``file`` and a ``betweenness`` key. Equivalent to
    ``cmd_health``'s pre-W1451 ``category``/``severity`` form: the utility
    severity multiplier (1.5x) only ever applies to items already excluded
    here, so an actionable bottleneck is ``critical`` iff
    ``betweenness > bn_p90``.
    """
    actionable = [b for b in bn_items or () if not _is_utility(b.get("file"))]
    critical = sum(1 for b in actionable if (b.get("betweenness") or 0) > (bn_p90 or 0))
    return (critical * BOTTLENECK_CRITICAL_WEIGHT + len(actionable) * BOTTLENECK_ITEM_WEIGHT) / _size_norm(
        total_symbols
    )


def fetch_average_file_health(conn: sqlite3.Connection) -> float | None:
    """``AVG(file_stats.health_score)``, or ``None`` when unavailable.

    ``None`` means "no signal" and maps to the neutral 1.0 factor. A genuine
    average of ``0.0`` is NOT None and maps to a 0.0 factor — the pre-W1451
    ``(avg or 10)`` idiom in ``collect_metrics`` silently rewrote a measured
    zero into a perfect score, which is the same false-clean shape this module
    exists to close.
    """
    row = conn.execute("SELECT AVG(health_score) FROM file_stats WHERE health_score IS NOT NULL").fetchone()
    return row[0] if row else None


def file_health_factor(avg_file_health: float | None) -> float:
    """Map ``AVG(file_stats.health_score)`` (0-10) onto a 0-1 health factor."""
    if avg_file_health is None:
        return 1.0
    return min(1.0, max(0.0, avg_file_health / FILE_HEALTH_MAX))


def compute_health_score(
    *,
    tangle_ratio: float,
    god_items: Iterable[dict],
    bn_items: Iterable[dict],
    bn_p90: float,
    layer_violations: int,
    total_symbols: int,
    avg_file_health: float | None,
    coverage_pct: float | None = None,
    coverable_lines: int = 0,
) -> HealthScore:
    """Compose the canonical 0-100 health score.

    Weighted geometric mean: ``score = 100 * product(h_i ^ w_i)``.
    Non-compensatory — a zero in any dimension cannot be masked by high scores
    in others, unlike a linear sum.

    Parameters
    ----------
    tangle_ratio
        Percent, from :func:`compute_tangle_ratio`. Pass the ``.ratio`` field.
    god_items / bn_items
        Raw detector rows carrying ``file`` + ``degree`` / ``betweenness``.
        Actionability and criticality are derived here so the two call sites
        cannot classify differently.
    coverage_pct / coverable_lines
        Imported test coverage. Both call sites MUST pass these; supplying
        them at only one site is how a second divergence would be born.
    """
    g_signal = god_component_signal(god_items, total_symbols)
    b_signal = bottleneck_signal(bn_items, bn_p90, total_symbols)

    base = [
        (health_factor(tangle_ratio, TANGLE_RATIO_SCALE), TANGLE_RATIO_WEIGHT),
        (health_factor(g_signal, GOD_SIGNAL_SCALE), GOD_COMPONENT_WEIGHT),
        (health_factor(b_signal, BOTTLENECK_SIGNAL_SCALE), BOTTLENECK_WEIGHT),
        (health_factor(layer_violations or 0, LAYER_VIOLATION_SCALE), LAYER_VIOLATION_WEIGHT),
        (file_health_factor(avg_file_health), FILE_HEALTH_WEIGHT),
    ]

    names = list(BASE_FACTOR_NAMES)
    if (coverable_lines or 0) > 0 and coverage_pct is not None:
        cov = min(1.0, max(COVERAGE_FACTOR_FLOOR, coverage_pct / 100.0))
        factors = [(h, w * COVERAGE_RESCALE) for h, w in base]
        factors.append((cov, COVERAGE_WEIGHT))
        names.append(COVERAGE_FACTOR_NAME)
    else:
        factors = base

    log_score = sum(w * math.log(max(h, 1e-9)) for h, w in factors)
    score = max(0, min(100, int(100 * math.exp(log_score))))

    return HealthScore(
        score=score,
        tangle_ratio=tangle_ratio,
        god_signal=g_signal,
        bottleneck_signal=b_signal,
        factors=tuple(factors),
        factor_names=tuple(names),
    )
