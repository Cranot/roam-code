#!/usr/bin/env python3
"""Measure performance only when host noise permits an honest conclusion.

The default cases exercise the three recently fixed performance paths:
``cmd_alerts``, ``clone_detect``, and ``clones_cross_layer``. Each case gets
warm-up executions followed by recorded executions. The median and median
absolute deviation (MAD) are reported only when MAD/median is at or below the
configured threshold; noisy samples are explicitly inconclusive.

Examples::

    python dev/perf_harness.py self-test
    python dev/perf_harness.py run --iterations 7 --warmups 2 \
        --json-output internal/benchmarks/perf-harness-20260728.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_SPREAD_THRESHOLD = 0.10
DEFAULT_ITERATIONS = 7
DEFAULT_WARMUPS = 2
DEFAULT_TIMEOUT_SECONDS = 120

CASES: dict[str, tuple[str, ...]] = {
    "cmd_alerts": ("alerts",),
    "clone_detect": ("clones",),
    "clones_cross_layer": ("smells", "--only", "cross-layer-clone"),
}


def summarize_timings(
    name: str,
    timings: Sequence[float],
    *,
    spread_threshold: float,
) -> dict[str, Any]:
    """Summarize timings in milliseconds, withholding figures when noisy.

    ``spread_percent`` is the relative median absolute deviation (MAD) as a
    percentage of the median. MAD is robust to one or two scheduler outliers,
    while the threshold prevents a broad noisy sample from becoming a trusted
    benchmark number. A noisy result deliberately has ``median`` and ``mad``
    set to ``None``: callers can inspect the raw samples and noise percentage,
    but cannot accidentally consume a performance figure that was refused.
    """
    if not timings:
        return {
            "name": name,
            "sample_count": 0,
            "median": None,
            "mad": None,
            "spread_percent": None,
            "spread_statistic": "mad",
            "spread_threshold_percent": round(spread_threshold * 100, 6),
            "conclusive": False,
            "status": "inconclusive",
        }

    median = float(statistics.median(timings))
    if median <= 0:
        raise ValueError("timings must have a positive median")
    mad = float(statistics.median(abs(value - median) for value in timings))
    spread_percent = mad / median * 100.0
    conclusive = mad / median <= spread_threshold
    return {
        "name": name,
        "sample_count": len(timings),
        "median": median if conclusive else None,
        "mad": mad if conclusive else None,
        "spread_percent": spread_percent,
        "spread_statistic": "mad",
        "spread_threshold_percent": round(spread_threshold * 100, 6),
        "conclusive": conclusive,
        "status": "conclusive" if conclusive else "inconclusive",
    }


def _run_once(argv: Sequence[str], cwd: Path, timeout: int) -> tuple[float, int, str]:
    started_ns = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "roam", "--json", *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        return elapsed_ms, 124, f"timeout after {timeout}s"
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    return elapsed_ms, completed.returncode, ""


def measure_case(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    warmups: int,
    iterations: int,
    timeout: int,
    spread_threshold: float,
    runner: Callable[[Sequence[str], Path, int], tuple[float, int, str]] = _run_once,
) -> dict[str, Any]:
    """Run one command's warm-ups and measured iterations."""
    for _ in range(warmups):
        _, exit_code, error = runner(argv, cwd, timeout)
        if exit_code != 0:
            return {
                "case": name,
                "command": ["python", "-m", "roam", "--json", *argv],
                "warmups": warmups,
                "iterations": iterations,
                "timings_ms": [],
                "summary": {
                    "name": name,
                    "sample_count": 0,
                    "median": None,
                    "mad": None,
                    "spread_percent": None,
                    "spread_statistic": "mad",
                    "spread_threshold_percent": round(spread_threshold * 100, 6),
                    "conclusive": False,
                    "status": "command_failed",
                },
                "error": f"warm-up failed with exit {exit_code}: {error or 'command produced a non-zero exit'}",
            }

    timings_ms: list[float] = []
    for _ in range(iterations):
        elapsed_ms, exit_code, error = runner(argv, cwd, timeout)
        if exit_code != 0:
            return {
                "case": name,
                "command": ["python", "-m", "roam", "--json", *argv],
                "warmups": warmups,
                "iterations": iterations,
                "timings_ms": timings_ms,
                "summary": {
                    "name": name,
                    "sample_count": len(timings_ms),
                    "median": None,
                    "mad": None,
                    "spread_percent": None,
                    "spread_statistic": "mad",
                    "spread_threshold_percent": round(spread_threshold * 100, 6),
                    "conclusive": False,
                    "status": "command_failed",
                },
                "error": f"measurement failed with exit {exit_code}: {error or 'command produced a non-zero exit'}",
            }
        timings_ms.append(elapsed_ms)

    return {
        "case": name,
        "command": ["python", "-m", "roam", "--json", *argv],
        "warmups": warmups,
        "iterations": iterations,
        "timings_ms": timings_ms,
        "summary": summarize_timings(name, timings_ms, spread_threshold=spread_threshold),
    }


def _synthetic_cases(spread_threshold: float) -> list[dict[str, Any]]:
    tight = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0]
    noisy = [80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0]
    return [
        {
            "case": "tight",
            "timings_ms": tight,
            "summary": summarize_timings("tight", tight, spread_threshold=spread_threshold),
        },
        {
            "case": "noisy",
            "timings_ms": noisy,
            "summary": summarize_timings("noisy", noisy, spread_threshold=spread_threshold),
        },
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_summary(results: Sequence[dict[str, Any]]) -> None:
    for result in results:
        summary = result["summary"]
        name = result["case"]
        if summary["status"] == "command_failed":
            print(f"{name}: INCONCLUSIVE (command failed: {result['error']})")
        elif summary["conclusive"]:
            print(
                f"{name}: MEDIAN={summary['median']:.3f}ms "
                f"MAD={summary['mad']:.3f}ms spread={summary['spread_percent']:.2f}%"
            )
        else:
            print(f"{name}: INCONCLUSIVE (host too noisy: spread={summary['spread_percent']:.2f}%)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("run", "self-test"), default="run")
    parser.add_argument("--case", action="append", choices=tuple(CASES), help="Measure only this case; repeatable.")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS, help="Recorded iterations per case.")
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS, help="Discarded warm-up iterations per case.")
    parser.add_argument(
        "--spread-threshold",
        type=float,
        default=DEFAULT_SPREAD_THRESHOLD,
        help="Maximum relative MAD as a fraction of the median (default: 0.10).",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Command timeout in seconds.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Indexed repository to benchmark.")
    parser.add_argument("--json-output", type=Path, default=Path("perf-results.json"), help="JSON result path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.iterations < 1 or args.warmups < 0 or args.timeout < 1:
        raise SystemExit("iterations must be positive; warmups must be non-negative; timeout must be positive")
    if not 0 <= args.spread_threshold:
        raise SystemExit("spread threshold must be non-negative")

    if args.mode == "self-test":
        results = _synthetic_cases(args.spread_threshold)
        payload = {
            "schema": "roam.perf-harness",
            "schema_version": 1,
            "mode": "self-test",
            "spread_threshold": args.spread_threshold,
            "results": results,
        }
        print("Synthetic timings (no host clock used):")
        _print_summary(results)
        _write_json(args.json_output, payload)
        print(f"JSON: {args.json_output}")
        return 0 if results[0]["summary"]["conclusive"] and not results[1]["summary"]["conclusive"] else 1

    selected = args.case or list(CASES)
    results = [
        measure_case(
            name,
            CASES[name],
            cwd=args.cwd.resolve(),
            warmups=args.warmups,
            iterations=args.iterations,
            timeout=args.timeout,
            spread_threshold=args.spread_threshold,
        )
        for name in selected
    ]
    payload = {
        "schema": "roam.perf-harness",
        "schema_version": 1,
        "mode": "run",
        "cwd": str(args.cwd.resolve()),
        "iterations": args.iterations,
        "warmups": args.warmups,
        "spread_threshold": args.spread_threshold,
        "spread_statistic": "mad",
        "results": results,
    }
    print(
        f"Performance harness: {len(results)} case(s), {args.iterations} measured iteration(s), "
        f"{args.warmups} warm-up(s), MAD threshold={args.spread_threshold * 100:.2f}%"
    )
    _print_summary(results)
    _write_json(args.json_output, payload)
    print(f"JSON: {args.json_output}")
    return 0 if all(result["summary"]["conclusive"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
