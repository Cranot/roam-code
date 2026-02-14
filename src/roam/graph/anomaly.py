"""Statistical anomaly detection for small time-series (10-100 data points).

All algorithms use Python stdlib only (math, statistics, collections).
Designed for roam health trend analysis where n is typically 5-100 snapshots.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median


def _mad(values: list[float | int]) -> float:
    """Median Absolute Deviation -- robust scale estimator."""
    med = median(values)
    return median(abs(v - med) for v in values)


def modified_z_score(values: list[float | int], threshold: float = 3.5) -> list[dict]:
    """Detect point anomalies using Modified Z-Score (MAD-based).

    More robust than standard Z-score for small datasets because it uses
    Median Absolute Deviation instead of standard deviation.

    Returns list of dicts with keys: index, value, z_score, is_anomaly.
    Requires n >= 5.  Returns empty list if insufficient data.
    """
    if len(values) < 5:
        return []

    med = median(values)
    mad_val = _mad(values)

    results: list[dict] = []
    for i, v in enumerate(values):
        if mad_val == 0.0:
            # All deviations from median are zero (or nearly); z-score is 0
            # unless the point itself differs from the median.
            z = 0.0 if v == med else float("inf")
        else:
            # 0.6745 is the 0.75th quantile of the standard normal distribution.
            # Scaling by it makes the MAD consistent with std-dev for normal data.
            z = 0.6745 * (v - med) / mad_val
        results.append({
            "index": i,
            "value": v,
            "z_score": round(z, 4) if math.isfinite(z) else z,
            "is_anomaly": abs(z) > threshold,
        })
    return results


def theil_sen_slope(values: list[float | int]) -> dict | None:
    """Estimate robust trend using Theil-Sen median of pairwise slopes.

    Far more robust to outliers than least-squares regression.
    O(n^2) but n <= 100 so this is instant.

    Returns dict with: slope, intercept, direction.
    direction is "increasing" if slope > 0.01, "decreasing" if slope < -0.01,
    else "stable".
    Requires n >= 4.  Returns None if insufficient data.
    """
    n = len(values)
    if n < 4:
        return None

    # Compute all pairwise slopes (y_j - y_i) / (j - i) for i < j
    slopes: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            slopes.append((values[j] - values[i]) / (j - i))

    slope = median(slopes)

    # Intercept: median of (y_i - slope * i)
    intercepts = [values[i] - slope * i for i in range(n)]
    intercept = median(intercepts)

    if slope > 0.01:
        direction = "increasing"
    elif slope < -0.01:
        direction = "decreasing"
    else:
        direction = "stable"

    return {
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "direction": direction,
    }


def mann_kendall_test(values: list[float | int]) -> dict | None:
    """Mann-Kendall trend significance test.

    Non-parametric test for monotonic trend.  Uses normal approximation
    of the S statistic with math.erf for p-value.

    Returns dict with: S, z_score, p_value, significant (p < 0.05), direction.
    Requires n >= 8.  Returns None if insufficient data.
    """
    n = len(values)
    if n < 8:
        return None

    # Compute S = sum of sgn(x_j - x_i) for all i < j
    s_stat = 0
    for i in range(n):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            if diff > 0:
                s_stat += 1
            elif diff < 0:
                s_stat -= 1

    # Variance with tie correction
    # Count ties: groups of equal values
    tie_counts = Counter(values)
    tie_correction = 0
    for count in tie_counts.values():
        if count > 1:
            t = count
            tie_correction += t * (t - 1) * (2 * t + 5)

    var_s = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18.0

    if var_s == 0.0:
        # All values are identical -- no trend
        return {
            "S": 0,
            "z_score": 0.0,
            "p_value": 1.0,
            "significant": False,
            "direction": "stable",
        }

    # Continuity-corrected Z
    if s_stat > 0:
        z = (s_stat - 1) / math.sqrt(var_s)
    elif s_stat < 0:
        z = (s_stat + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    # Two-tailed p-value using complementary error function
    # P = 2 * (1 - Phi(|z|)) = erfc(|z| / sqrt(2))
    p_value = math.erfc(abs(z) / math.sqrt(2.0))

    if z > 0:
        direction = "increasing"
    elif z < 0:
        direction = "decreasing"
    else:
        direction = "stable"

    return {
        "S": s_stat,
        "z_score": round(z, 4),
        "p_value": round(p_value, 6),
        "significant": p_value < 0.05,
        "direction": direction,
    }


def western_electric_rules(values: list[float | int]) -> list[dict]:
    """Detect anomalous patterns using Western Electric rules.

    Six rules applied to control charts:
      Rule 1: Any single point > 3 sigma from mean
      Rule 2: 2 of 3 consecutive points > 2 sigma (same side)
      Rule 3: 4 of 5 consecutive points > 1 sigma (same side)
      Rule 4: 8+ consecutive points on same side of mean (run)
      Rule 5: 6+ consecutive increasing or decreasing points (trend)
      Rule 6: 14+ consecutive alternating up/down points (oscillation)

    Uses MAD-based sigma for robustness.
    Returns list of dicts with: rule, description, indices, metric_value.
    """
    n = len(values)
    if n < 3:
        return []

    med = median(values)
    sigma = _mad(values)
    # If MAD is zero, fall back to mean absolute deviation from median
    if sigma == 0.0:
        sigma = sum(abs(v - med) for v in values) / n
    # If still zero all values are identical -- no violations possible
    if sigma == 0.0:
        return []

    # Scale MAD to be consistent with std-dev for normal data
    sigma_scaled = sigma / 0.6745

    violations: list[dict] = []

    # Standardized residuals
    z = [(v - med) / sigma_scaled for v in values]

    # Rule 1: single point beyond 3 sigma
    for i in range(n):
        if abs(z[i]) > 3.0:
            violations.append({
                "rule": 1,
                "description": "single point > 3 sigma from center",
                "indices": [i],
                "metric_value": round(abs(z[i]), 4),
            })

    # Rule 2: 2 of 3 consecutive points beyond 2 sigma on same side
    for i in range(n - 2):
        window = z[i:i + 3]
        above = [j for j, v in enumerate(window) if v > 2.0]
        below = [j for j, v in enumerate(window) if v < -2.0]
        if len(above) >= 2:
            violations.append({
                "rule": 2,
                "description": "2 of 3 points > 2 sigma (same side, above)",
                "indices": [i + j for j in above],
                "metric_value": round(max(window), 4),
            })
        if len(below) >= 2:
            violations.append({
                "rule": 2,
                "description": "2 of 3 points > 2 sigma (same side, below)",
                "indices": [i + j for j in below],
                "metric_value": round(min(window), 4),
            })

    # Rule 3: 4 of 5 consecutive points beyond 1 sigma on same side
    for i in range(n - 4):
        window = z[i:i + 5]
        above = [j for j, v in enumerate(window) if v > 1.0]
        below = [j for j, v in enumerate(window) if v < -1.0]
        if len(above) >= 4:
            violations.append({
                "rule": 3,
                "description": "4 of 5 points > 1 sigma (same side, above)",
                "indices": [i + j for j in above],
                "metric_value": round(max(window), 4),
            })
        if len(below) >= 4:
            violations.append({
                "rule": 3,
                "description": "4 of 5 points > 1 sigma (same side, below)",
                "indices": [i + j for j in below],
                "metric_value": round(min(window), 4),
            })

    # Rule 4: 8+ consecutive points on same side of mean
    run_start = 0
    for i in range(1, n):
        same_side = (z[i] > 0 and z[run_start] > 0) or (z[i] < 0 and z[run_start] < 0)
        if not same_side or z[i] == 0.0:
            if i - run_start >= 8:
                indices = list(range(run_start, i))
                violations.append({
                    "rule": 4,
                    "description": f"{len(indices)} consecutive points on same side of center",
                    "indices": indices,
                    "metric_value": len(indices),
                })
            run_start = i
    # Check final run
    if n - run_start >= 8:
        indices = list(range(run_start, n))
        violations.append({
            "rule": 4,
            "description": f"{len(indices)} consecutive points on same side of center",
            "indices": indices,
            "metric_value": len(indices),
        })

    # Rule 5: 6+ consecutive increasing or decreasing points
    if n >= 6:
        inc_start = 0
        dec_start = 0
        for i in range(1, n):
            if values[i] <= values[i - 1]:
                if i - inc_start >= 6:
                    indices = list(range(inc_start, i))
                    violations.append({
                        "rule": 5,
                        "description": f"{len(indices)} consecutive increasing points",
                        "indices": indices,
                        "metric_value": len(indices),
                    })
                inc_start = i
            if values[i] >= values[i - 1]:
                if i - dec_start >= 6:
                    indices = list(range(dec_start, i))
                    violations.append({
                        "rule": 5,
                        "description": f"{len(indices)} consecutive decreasing points",
                        "indices": indices,
                        "metric_value": len(indices),
                    })
                dec_start = i
        # Check final runs
        if n - inc_start >= 6:
            indices = list(range(inc_start, n))
            violations.append({
                "rule": 5,
                "description": f"{len(indices)} consecutive increasing points",
                "indices": indices,
                "metric_value": len(indices),
            })
        if n - dec_start >= 6:
            indices = list(range(dec_start, n))
            violations.append({
                "rule": 5,
                "description": f"{len(indices)} consecutive decreasing points",
                "indices": indices,
                "metric_value": len(indices),
            })

    # Rule 6: 14+ consecutive alternating up/down points (oscillation)
    if n >= 14:
        alt_start = 1  # alternation is defined from index 1 onward
        for i in range(2, n):
            prev_dir = values[i - 1] - values[i - 2]
            curr_dir = values[i] - values[i - 1]
            # Alternation continues if direction changed, or curr is zero
            alternating = (prev_dir > 0 and curr_dir < 0) or (prev_dir < 0 and curr_dir > 0)
            if not alternating:
                run_len = i - alt_start + 1
                if run_len >= 14:
                    indices = list(range(alt_start - 1, i))
                    violations.append({
                        "rule": 6,
                        "description": f"{len(indices)} consecutive alternating points",
                        "indices": indices,
                        "metric_value": len(indices),
                    })
                alt_start = i
        # Check final run
        run_len = n - alt_start + 1
        if run_len >= 14:
            indices = list(range(alt_start - 1, n))
            violations.append({
                "rule": 6,
                "description": f"{len(indices)} consecutive alternating points",
                "indices": indices,
                "metric_value": len(indices),
            })

    return violations


def cusum(
    values: list[float | int],
    drift: float = 0.5,
    threshold: float = 4.0,
) -> list[dict]:
    """CUSUM (Cumulative Sum) change detection for slow degradation.

    Detects shifts in the mean that are too gradual for point anomaly detection.
    drift: allowance parameter in sigma units (default 0.5).
    threshold: decision boundary in sigma units (default 4.0).

    Returns list of dicts with: index, direction ("up"/"down"), cusum_value.
    Requires n >= 5.
    """
    n = len(values)
    if n < 5:
        return []

    mu = median(values)
    sigma = _mad(values)
    # Fall back to mean absolute deviation if MAD is zero
    if sigma == 0.0:
        sigma = sum(abs(v - mu) for v in values) / n
    if sigma == 0.0:
        return []

    # Scale MAD to std-dev equivalent
    sigma_scaled = sigma / 0.6745

    signals: list[dict] = []
    s_high = 0.0
    s_low = 0.0

    for i, v in enumerate(values):
        normalized = (v - mu) / sigma_scaled
        s_high = max(0.0, s_high + normalized - drift)
        s_low = max(0.0, s_low - normalized - drift)

        if s_high > threshold:
            signals.append({
                "index": i,
                "direction": "up",
                "cusum_value": round(s_high, 4),
            })
            s_high = 0.0  # reset after signal

        if s_low > threshold:
            signals.append({
                "index": i,
                "direction": "down",
                "cusum_value": round(s_low, 4),
            })
            s_low = 0.0  # reset after signal

    return signals


def forecast(values: list[float | int], target: float) -> dict | None:
    """Forecast when a metric will reach a target value.

    Uses Theil-Sen slope for projection.

    Returns dict with: current, target, slope, steps_until (int or None if
    direction is wrong or trend is stable), direction.
    Returns None if insufficient data or no trend.
    """
    trend = theil_sen_slope(values)
    if trend is None:
        return None

    slope = trend["slope"]
    if slope == 0.0 or trend["direction"] == "stable":
        return {
            "current": values[-1],
            "target": target,
            "slope": slope,
            "steps_until": None,
            "direction": "stable",
        }

    current = values[-1]
    gap = target - current

    # If the slope moves us away from the target, we will never reach it
    if (gap > 0 and slope < 0) or (gap < 0 and slope > 0):
        return {
            "current": current,
            "target": target,
            "slope": slope,
            "steps_until": None,
            "direction": trend["direction"],
        }

    if gap == 0:
        return {
            "current": current,
            "target": target,
            "slope": slope,
            "steps_until": 0,
            "direction": trend["direction"],
        }

    steps = math.ceil(abs(gap / slope))
    return {
        "current": current,
        "target": target,
        "slope": slope,
        "steps_until": steps,
        "direction": trend["direction"],
    }
