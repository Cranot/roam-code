"""Canonical health-score band -> label mapping.

This is the SINGLE SOURCE OF TRUTH for "what verdict label corresponds to a
0-100 health score?". Before this module existed, two commands disagreed on
the SAME score:

- ``roam health`` reserved "Healthy" for ``score >= 80`` and called 75 "Fair".
- ``roam understand`` called 75 "healthy" using a ``score >= 70`` cutoff.

That is Pattern 3a ("cross-command metric divergence") from the dogfood
``SYNTHESIS-2026-05-12.md`` and a LAW-6 violation: an agent reading
``roam understand`` then ``roam health`` sees contradictory health verdicts
for one number. The fix mirrors the ``cycles`` / ``god_components``
reconciliation: one canonical band table + a ``health_band_definition``
label any envelope can stamp.

The canonical cutoffs (owned here, consumed by ``roam health`` and
``roam understand``)::

    score >= 80  -> "Healthy"
    score >= 60  -> "Fair"
    score >= 40  -> "Needs attention"
    score <  40  -> "Unhealthy"

``roam health``'s verdict wording is the authority; this table mirrors its
``_compose_verdict`` thresholds exactly. ``health_band(75)`` returns
``"Fair"`` -- the same label ``roam health`` prints for 75/100.
"""

from __future__ import annotations

# (lower-bound-inclusive cutoff, canonical label).  Ordered high -> low; the
# first cutoff a score meets wins.  This mirrors cmd_health._compose_verdict.
_BANDS: tuple[tuple[int, str], ...] = (
    (80, "Healthy"),
    (60, "Fair"),
    (40, "Needs attention"),
    (0, "Unhealthy"),
)

# Single-line label any envelope reporting a health-band label should stamp
# under the key ``health_band_definition`` (Pattern 3a metric-definition
# sidecar).
DEFINITION = (
    "Health-score band label from `roam.quality.health_band.health_band(score)`: "
    ">=80 Healthy, >=60 Fair, >=40 Needs attention, <40 Unhealthy. "
    "Shared verbatim by `roam health` and `roam understand` so the same score "
    "never maps to two different verdict labels (Pattern 3a / LAW 6). "
    "Run `roam health` for the per-finding breakdown behind the score."
)


def health_band(score: float) -> str:
    """Return the canonical verdict label for a 0-100 health ``score``.

    Both ``roam health`` and ``roam understand`` MUST route their score->label
    decision through this function so the verdict line is self-consistent
    across commands (LAW 6 / Pattern 3a)::

        label = health_band(health_score)  # "Fair" for 75

    Scores are clamped implicitly: any non-negative number below 40 (including
    a degraded ``0``) maps to "Unhealthy". Negative or NaN inputs also fall
    through to "Unhealthy" (the conservative band) since the final cutoff is
    ``>= 0`` and the comparison is false for NaN.
    """
    for cutoff, label in _BANDS:
        if score >= cutoff:
            return label
    return "Unhealthy"


def definition() -> str:
    """Return the canonical health-band metric-definition string.

    Use when emitting a JSON envelope that includes a health-band label::

        summary["health_band_definition"] = definition()
    """
    return DEFINITION
