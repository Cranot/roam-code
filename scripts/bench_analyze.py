#!/usr/bin/env python3
"""Account for every discovered bench-compile cell, including unreadable results.

Usage: python scripts/bench_analyze.py <out-dir> [<out-dir> ...] [--json]

The denominator is discovered t<task>_<condition>_<run>.json artifacts, NOT
assignments, dispatches, independent tasks, or verified task successes. Saved
files can be cached/retried and absent files cannot be inferred without an
assignment manifest. A successful result envelope is not an oracle verdict.

Metric means are conditional on recorded, finite, non-negative observations.
Missing cost, time, or turns stay unknown rather than becoming zero. Timeout
wall time is shown separately as an estimate using --timeout-cap (default 90s);
no missing cost is imputed. This tool reads saved artifacts and makes no calls.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import re
import statistics
import sys

_CELL_NAME = re.compile(r"^t\d+_(.+)_\d+\.json$")
_STATUSES = ("success", "timeout", "error", "invalid", "unreadable")
_METRICS = {"num_turns": "num_turns", "duration_ms": "duration_ms", "cost_usd": "total_cost_usd"}


def _metric(value: object) -> int | float | None:
    """Accept finite non-negative measurements, including genuine zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def _load_cell(path: str) -> dict:
    """Classify the result, without promoting a malformed or error envelope."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return {"status": "invalid", "reason": "result_not_object"}
    if data.get("type") == "error" and data.get("reason") == "timeout":
        status = "timeout"
    elif data.get("type") == "error" or data.get("subtype") != "success" or data.get("is_error", False) is not False:
        status = "error"
    elif not isinstance(data.get("result", ""), str) or not isinstance(data.get("usage", {}), dict):
        status = "invalid"
    else:
        status = "success"
    # Do not include raw stderr, prompts, or result prose in this projection.
    return {"status": status, **{key: _metric(data.get(source)) for key, source in _METRICS.items()}}


def _aggregate(directory: str) -> dict[str, list[dict]]:
    """Keep every discovered cell, even when its bytes cannot be parsed/read."""
    by_cond: dict[str, list[dict]] = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(directory, "t*_*_*.json"))):
        match = _CELL_NAME.fullmatch(os.path.basename(path))
        if match is None:
            continue
        try:
            cell = _load_cell(path)
        except (json.JSONDecodeError, UnicodeError):
            cell = {"status": "invalid", "reason": "invalid_json_or_encoding"}
        except OSError:
            cell = {"status": "unreadable", "reason": "cell_read_failed"}
        by_cond[match.group(1)].append(cell)
    return dict(by_cond)


def _metric_summary(cells: list[dict], key: str) -> dict:
    values = [value for cell in cells if (value := _metric(cell.get(key))) is not None]
    return {
        "observed": len(values),
        "unknown": len(cells) - len(values),
        "total": sum(values) if values else None,
        "mean": statistics.mean(values) if values else None,
    }


def summarize(directory: str, timeout_cap_ms: int) -> dict:
    """Produce bounded accounting with explicit status and metric denominators."""
    conditions = {}
    for condition, cells in _aggregate(directory).items():
        counts = {status: sum(cell["status"] == status for cell in cells) for status in _STATUSES}
        successful = [cell for cell in cells if cell["status"] == "success"]
        conditions[condition] = {
            "observed_cells": len(cells),
            "counts": counts,
            "successful_result_rate": len(successful) / len(cells),
            "all_observed_metrics": {key: _metric_summary(cells, key) for key in _METRICS},
            "successful_result_metrics": {key: _metric_summary(successful, key) for key in _METRICS},
            "timeout_wall_estimate_ms": counts["timeout"] * timeout_cap_ms,
        }
    return {
        "directory": directory,
        "denominator": "discovered_cell_artifacts",
        "assigned_cases": None,
        "dispatch_attempts": None,
        "verified_task_successes": None,
        "timeout_cap_ms": timeout_cap_ms,
        "conditions": conditions,
        "limitations": [
            "Assignments, dispatches, missing files, retries and cached reuse are not recoverable from cell files alone.",
            "Successful result envelopes do not establish verified task success or independent task counts.",
            "Metric means use recorded observations only; unknown measurements are not zero.",
            "Timeout wall estimates use the supplied cap; timeout costs and turns are not imputed.",
            "Model, effort, carrier and oracle qualification are not established by this report.",
        ],
    }


def _number(value: int | float | None) -> str:
    return "unknown" if value is None else f"{value:.3f}"


def _print_report(data: dict) -> None:
    print(f"\n{data['directory']}")
    print("Denominator: discovered cell artifacts (assignment/dispatch counts unknown).")
    if not data["conditions"]:
        print("No cell artifacts found; no benchmark outcome established.")
    for condition, row in data["conditions"].items():
        counts = " ".join(f"{status}={count}" for status, count in row["counts"].items())
        print(f"\n{condition}: observed={row['observed_cells']} {counts}")
        print(f"  Successful result envelopes: {row['successful_result_rate']:.1%} (not verified task success)")
        for group in ("all_observed_metrics", "successful_result_metrics"):
            print(f"  {group} (conditional on recorded measurements):")
            for metric, values in row[group].items():
                print(
                    f"    {metric}: mean={_number(values['mean'])} total={_number(values['total'])}"
                    f" observed={values['observed']} unknown={values['unknown']}"
                )
        print(f"  Timeout wall estimate: {row['timeout_wall_estimate_ms'] / 1000:.1f}s; no cost imputation.")
    for limitation in data["limitations"]:
        print(f"  Note: {limitation}")


def report(directory: str, timeout_cap_ms: int) -> None:
    _print_report(summarize(directory, timeout_cap_ms))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directories", nargs="+", help="Saved bench-compile output directories.")
    ap.add_argument("--timeout-cap", type=int, default=90, help="Timeout cap in seconds used by this run (default 90).")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable accounting.")
    args = ap.parse_args()
    if args.timeout_cap <= 0:
        ap.error("--timeout-cap must be positive")
    reports = []
    invalid = False
    for directory in args.directories:
        if not os.path.isdir(directory):
            print(f"not a directory: {directory}", file=sys.stderr)
            invalid = True
            continue
        data = summarize(directory, args.timeout_cap * 1000)
        invalid |= not bool(data["conditions"])
        reports.append(data)
        if not args.json:
            _print_report(data)
    if args.json:
        print(json.dumps(reports, indent=2, allow_nan=False))
    return 2 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
