"""W910 parity lint: cmd_alerts.py threshold/direction/label registries.

``cmd_alerts.py`` carries three parallel collections that govern alert
emission:

- ``_DEFAULT_THRESHOLDS`` — per-metric severity rule (op, value, level).
  Drives ``_check_thresholds`` (the threshold alert path).
- ``_WORSE_WHEN_HIGHER`` / ``_WORSE_WHEN_LOWER`` — directional
  classification driving the trend (Mann-Kendall + Sen's slope) and
  rate-of-change paths.
- ``_TREND_LABELS`` — human-readable trend labels per metric.

The pre-W910 drift: ``_WORSE_WHEN_HIGHER`` + ``_WORSE_WHEN_LOWER`` covered
six metrics, but ``_DEFAULT_THRESHOLDS`` only carried four — ``bottlenecks``
and ``dead_exports`` had a direction + a trend label but no threshold, so
the threshold-alert path silently skipped them. This lint pins the
registries in lockstep so the next missing-threshold drift fails loud at
import time instead of silently corrupting the alerts envelope.

Strategy A from the W910 fix memo: parity lint + missing-row backfill,
preserving the three separate dicts. The consolidation into a single
``_METRIC_REGISTRY`` is deferred to W914.
"""

from __future__ import annotations

from roam.commands.cmd_alerts import (
    _DEFAULT_THRESHOLDS,
    _TREND_LABELS,
    _WORSE_WHEN_HIGHER,
    _WORSE_WHEN_LOWER,
)


def test_default_thresholds_covers_directional_metrics() -> None:
    """Every metric tracked directionally MUST have a threshold rule.

    Otherwise ``_check_thresholds`` silently skips it — agents calling
    ``roam alerts`` see no severity signal for that metric even when its
    value would clearly cross a "bad" boundary.
    """
    directional = _WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER
    threshold_keys = set(_DEFAULT_THRESHOLDS.keys())
    missing = directional - threshold_keys
    assert not missing, (
        f"W910 parity drift: metrics tracked in _WORSE_WHEN_HIGHER / "
        f"_WORSE_WHEN_LOWER but missing from _DEFAULT_THRESHOLDS: "
        f"{sorted(missing)}. Add a threshold row in "
        f"src/roam/commands/cmd_alerts.py:_DEFAULT_THRESHOLDS or update "
        f"the directional classification."
    )


def test_trend_labels_covers_directional_metrics() -> None:
    """Every directional metric MUST have a trend label.

    ``_check_trends`` falls back to ``f"{metric} worsening"`` when the
    label is missing, which is a soft degradation but still a vocabulary
    drift the parity lint should catch loud.
    """
    directional = _WORSE_WHEN_HIGHER | _WORSE_WHEN_LOWER
    label_keys = set(_TREND_LABELS.keys())
    missing = directional - label_keys
    assert not missing, (
        f"W910 parity drift: metrics tracked in _WORSE_WHEN_HIGHER / "
        f"_WORSE_WHEN_LOWER but missing from _TREND_LABELS: "
        f"{sorted(missing)}. Add a label in "
        f"src/roam/commands/cmd_alerts.py:_TREND_LABELS."
    )


def test_metrics_have_consistent_directional_classification() -> None:
    """No metric may be in BOTH _WORSE_WHEN_HIGHER and _WORSE_WHEN_LOWER.

    A metric that is "worse when higher" AND "worse when lower"
    simultaneously is a contradiction; the trend and rate-of-change
    paths would emit alerts in both directions, producing nonsense
    output.
    """
    contradictions = _WORSE_WHEN_HIGHER & _WORSE_WHEN_LOWER
    assert not contradictions, (
        f"W910 parity drift: metrics classified in BOTH "
        f"_WORSE_WHEN_HIGHER and _WORSE_WHEN_LOWER (contradictory): "
        f"{sorted(contradictions)}. Pick one direction in "
        f"src/roam/commands/cmd_alerts.py."
    )
