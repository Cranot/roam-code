#!/usr/bin/env python3
"""Evaluate roam CI gate expressions, including trend-aware functions.

Supported expressions:
- scalar metric: `health_score>=70`
- trend helpers: `latest(metric)>=N`, `delta(metric)<=N`, `slope(metric)<=N`
- velocity gate: `velocity(metric)<=N` (positive means worsening velocity)
- direction gate: `direction(metric)=worsening` (supports `!=` too)

Multiple expressions can be comma-separated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


_NUMERIC_RE = re.compile(
    r"^(?P<lhs>[A-Za-z_]\w*(?:\([A-Za-z_]\w*\))?)\s*"
    r"(?P<op>>=|<=|>|<|==|=|!=)\s*"
    r"(?P<rhs>[-+]?\d+(?:\.\d+)?)$"
)
_DIRECTION_RE = re.compile(
    r"^(?P<lhs>direction\([A-Za-z_]\w*\))\s*(?P<op>==|=|!=)\s*(?P<rhs>[A-Za-z_-]+)$"
)
_FN_RE = re.compile(r"^(?P<fn>[A-Za-z_]\w*)\((?P<metric>[A-Za-z_]\w*)\)$")

_HIGHER_IS_BETTER = {
    "health_score",
    "test_file_ratio",
    "coverage_ratio",
    "coverage_pct",
}
_HIGHER_IS_WORSE = {
    "risk_score",
    "issue_count",
    "tangle_ratio",
    "cycles",
    "cycle_count",
    "dead_symbols",
    "dead_exports",
    "layer_violations",
    "god_components",
    "bottlenecks",
    "avg_complexity",
    "max_complexity",
    "brain_methods",
}


def _load_results(results_dir: str) -> dict[str, dict]:
    """Load all JSON result files from a results directory."""
    out: dict[str, dict] = {}
    base = Path(results_dir)
    if not base.is_dir():
        return out

    for p in sorted(base.glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
    return out


def _series_item(data: dict, metric: str) -> dict | None:
    """Return matching metric entry from `metrics` list, if present."""
    metrics = data.get("metrics")
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, dict) and item.get("name") == metric:
                return item
    return None


def _trend_item(data: dict, metric: str) -> dict | None:
    """Return matching trend entry from `trends` list, if present."""
    trends = data.get("trends")
    if isinstance(trends, list):
        for item in trends:
            if isinstance(item, dict) and item.get("metric") == metric:
                return item
    return None


def _compute_slope(history: list[float]) -> float | None:
    """Compute ordinary least-squares slope for equally spaced snapshots."""
    if len(history) < 2:
        return None
    n = len(history)
    mean_x = (n - 1) / 2.0
    mean_y = sum(history) / n
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(history))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


def _to_float(value) -> float | None:
    """Coerce a scalar to float, returning None when impossible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _metric_latest(data: dict, metric: str) -> float | None:
    """Resolve latest metric value from known command output shapes."""
    item = _series_item(data, metric)
    if item:
        latest = _to_float(item.get("latest"))
        if latest is not None:
            return latest
        hist = item.get("history")
        if isinstance(hist, list) and hist:
            return _to_float(hist[-1])

    summary = data.get("summary")
    if isinstance(summary, dict):
        latest = _to_float(summary.get(metric))
        if latest is not None:
            return latest

    return _to_float(data.get(metric))


def _metric_delta(data: dict, metric: str) -> float | None:
    """Resolve first->last metric delta from output shapes."""
    item = _series_item(data, metric)
    if item:
        change = _to_float(item.get("change"))
        if change is not None:
            return change
        hist = item.get("history")
        if isinstance(hist, list) and len(hist) >= 2:
            first = _to_float(hist[0])
            last = _to_float(hist[-1])
            if first is not None and last is not None:
                return last - first
    return None


def _metric_slope(data: dict, metric: str) -> float | None:
    """Resolve metric slope from trend analysis or metric history."""
    trend = _trend_item(data, metric)
    if trend:
        slope = _to_float(trend.get("slope"))
        if slope is not None:
            return slope

    item = _series_item(data, metric)
    if item:
        slope = _to_float(item.get("slope"))
        if slope is not None:
            return slope
        hist = item.get("history")
        if isinstance(hist, list):
            nums = [_to_float(v) for v in hist]
            if all(v is not None for v in nums):
                return _compute_slope([v for v in nums if v is not None])
    return None


def _direction_from_slope(metric: str, slope: float) -> str:
    """Convert slope into semantic direction with metric polarity."""
    eps = 1e-9
    if abs(slope) <= eps:
        return "stable"

    if metric in _HIGHER_IS_BETTER:
        return "improving" if slope > 0 else "worsening"
    if metric in _HIGHER_IS_WORSE:
        return "worsening" if slope > 0 else "improving"
    return "increasing" if slope > 0 else "decreasing"


def _metric_direction(data: dict, metric: str) -> str | None:
    """Resolve direction from trend/metric entries or derive from slope."""
    trend = _trend_item(data, metric)
    if trend and isinstance(trend.get("direction"), str):
        return trend["direction"].lower()

    item = _series_item(data, metric)
    if item and isinstance(item.get("direction"), str):
        return item["direction"].lower()

    slope = _metric_slope(data, metric)
    if slope is None:
        return None
    return _direction_from_slope(metric, slope)


def _metric_velocity(data: dict, metric: str) -> float | None:
    """Resolve worsening velocity (positive means trending worse)."""
    slope = _metric_slope(data, metric)
    if slope is None:
        return None
    if metric in _HIGHER_IS_BETTER:
        return -slope
    if metric in _HIGHER_IS_WORSE:
        return slope
    return slope


def _resolve_lhs(lhs: str, data: dict):
    """Resolve an expression LHS into a comparable value or None."""
    fn_match = _FN_RE.match(lhs)
    if not fn_match:
        return _metric_latest(data, lhs), "number"

    fn = fn_match.group("fn").lower()
    metric = fn_match.group("metric")

    if fn == "latest":
        return _metric_latest(data, metric), "number"
    if fn == "delta":
        return _metric_delta(data, metric), "number"
    if fn == "slope":
        return _metric_slope(data, metric), "number"
    if fn == "velocity":
        return _metric_velocity(data, metric), "number"
    if fn == "direction":
        return _metric_direction(data, metric), "string"
    return None, "unknown"


def _compare_numeric(actual: float, op: str, target: float) -> bool:
    if op in ("==", "="):
        return actual == target
    if op == "!=":
        return actual != target
    if op == ">=":
        return actual >= target
    if op == "<=":
        return actual <= target
    if op == ">":
        return actual > target
    if op == "<":
        return actual < target
    return True


def _parse_expressions(exprs: str) -> list[str]:
    return [part.strip() for part in exprs.split(",") if part.strip()]


def _evaluate_on_payload(expr: str, payload: dict, source_name: str) -> tuple[str, str | None]:
    """Evaluate one expression on one payload.

    Returns:
    - ("unchecked", warning_message) when metric is not present for this payload.
    - ("passed", None) when expression evaluated true.
    - ("failed", failure_message) when expression evaluated false.
    """
    dm = _DIRECTION_RE.match(expr)
    if dm:
        lhs, op, rhs = dm.group("lhs"), dm.group("op"), dm.group("rhs").lower()
        actual, kind = _resolve_lhs(lhs, payload)
        if kind != "string" or actual is None:
            return "unchecked", f"{source_name}: metric not available for `{expr}`"
        actual_s = str(actual).lower()
        if op in ("==", "="):
            ok = actual_s == rhs
        else:
            ok = actual_s != rhs
        if ok:
            return "passed", None
        return "failed", f"{source_name}: {lhs}={actual_s} (required {op} {rhs})"

    nm = _NUMERIC_RE.match(expr)
    if not nm:
        return "unchecked", f"invalid gate expression: `{expr}`"

    lhs, op, rhs_str = nm.group("lhs"), nm.group("op"), nm.group("rhs")
    target = float(rhs_str)
    actual, kind = _resolve_lhs(lhs, payload)
    if kind != "number" or actual is None:
        return "unchecked", f"{source_name}: metric not available for `{expr}`"

    ok = _compare_numeric(float(actual), op, target)
    if ok:
        return "passed", None
    return "failed", f"{source_name}: {lhs}={actual} (required {op} {target})"


def evaluate_gate(gate_expr: str, results: dict[str, dict]) -> dict:
    """Evaluate one gate expression string across command result payloads."""
    expressions = _parse_expressions(gate_expr)
    if not expressions:
        return {
            "passed": True,
            "warnings": ["empty gate expression"],
            "failures": [],
            "checked_expressions": 0,
        }

    warnings: list[str] = []
    failures: list[str] = []
    checked_exprs = 0

    for expr in expressions:
        checked_for_expr = False
        for source, payload in results.items():
            status, msg = _evaluate_on_payload(expr, payload, source)
            if status == "unchecked":
                if msg:
                    warnings.append(msg)
                continue
            checked_for_expr = True
            if status == "failed" and msg:
                failures.append(msg)

        if checked_for_expr:
            checked_exprs += 1
        else:
            warnings.append(f"no compatible payload found for `{expr}`")

    return {
        "passed": len(failures) == 0,
        "warnings": warnings,
        "failures": failures,
        "checked_expressions": checked_exprs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate roam CI gate expressions")
    parser.add_argument("--expr", required=True, help="Gate expression string")
    parser.add_argument("--results-dir", required=True, help="Directory of *.json command outputs")
    args = parser.parse_args()

    results = _load_results(args.results_dir)
    report = evaluate_gate(args.expr, results)

    for w in report["warnings"]:
        print(f"::warning::{w}", file=sys.stderr)
    for f in report["failures"]:
        print(f"::error::Quality gate failed: {f}", file=sys.stderr)

    print("true" if report["passed"] else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
