"""Shared statistical helper functions used across graph and command modules."""

from __future__ import annotations


def gini_coefficient(values: list[int | float]) -> float:
    """Compute the Gini coefficient for a list of non-negative values.

    Returns a value in [0, 1] where 0 = perfectly uniform distribution and
    1 = maximally concentrated (all value in a single element).

    Returns 0.0 for empty lists, single-element lists, or all-zero inputs.
    Uses the mean-difference formulation (equivalent to the trapezoidal
    Lorenz-curve area calculation).
    """
    if not values or len(values) < 2:
        return 0.0
    total = sum(values)
    if total == 0:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    weighted_sum = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(sorted_vals))
    return weighted_sum / (n * total)
