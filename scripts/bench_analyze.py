#!/usr/bin/env python3
"""Re-aggregate `roam bench-compile` output directories with timeouts
counted as cells (per-dispatched view), not dropped (per-parsed view).

Why this exists
---------------
`roam bench-compile` emits aggregates only over PARSED (non-timeout) cells.
That understates the real-world cost of running the corpus because timeout
cells did burn wall-time + tokens.  This analyzer re-walks the per-cell
JSON files and computes:

    parsed_only        (current bench-compile view)
    per_dispatched     (timeouts charged at the timeout cap; turns=0;
                        cost = avg parsed cost of that condition as a
                        lower-bound estimate)
    success_rate       (n_parsed / n_dispatched)

Usage
-----
    python3 scripts/bench_analyze.py <out-dir> [<out-dir> ...]
    python3 scripts/bench_analyze.py --timeout-cap 90 internal/benchmarks/bench-w11w13-2026-06-02/

By default the timeout cap is 90s (matches the `--timeout 90` flag the
2026-06-02 benches were run with). Override with `--timeout-cap N`.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics
import sys


def _load_cell(path: str) -> dict:
    """Classify a cell JSON into success / timeout / error.

    Three on-disk shapes (dogfood — there are more than two):
      - SUCCESS: full Claude SDK result envelope with `subtype == "success"`
      - TIMEOUT: `{reason, type}` (the bench wall-cap path)
      - ERROR:   `{returncode, stderr, type}` (subprocess crash / rate-limit /
                 non-zero exit) OR any envelope lacking `subtype == "success"`.

    The earlier analyzer treated everything that wasn't the timeout shape as a
    valid cell, so ERROR cells (e.g. rate-limit crashes) were averaged in with
    `num_turns=0, duration=0` — dragging the means to garbage. Classify
    explicitly so error cells go into the success-rate denominator but NOT the
    metric averages."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    keys = sorted(d.keys())
    if keys == ["reason", "type"]:
        return {"status": "timeout"}
    if d.get("subtype") != "success":
        # {returncode, stderr, type} crash shape, or any non-success envelope.
        return {"status": "error", "stderr": str(d.get("stderr", ""))[:120]}
    return {
        "status": "success",
        "num_turns": d.get("num_turns") or 0,
        "duration_ms": d.get("duration_ms") or 0,
        "cost_usd": d.get("total_cost_usd") or 0,
        "is_error": bool(d.get("is_error")),
    }


def _aggregate(directory: str) -> dict[str, list[dict]]:
    """Return {condition: [cell_dict, ...]} for all `t*_<cond>_*.json`."""
    by_cond: dict[str, list[dict]] = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(directory, "t*_*_*.json"))):
        # Filename: t<task>_<cond>_<run>.json
        base = os.path.basename(path).rsplit(".", 1)[0]
        try:
            _, cond, _run = base.split("_", 2)
        except ValueError:
            continue
        try:
            by_cond[cond].append(_load_cell(path))
        except (OSError, json.JSONDecodeError):
            continue
    return dict(by_cond)


def _percent(cur: float, base: float) -> str:
    if not base:
        return "  n/a"
    pct = (cur - base) / base * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def report(directory: str, timeout_cap_ms: int) -> None:
    by_cond = _aggregate(directory)
    print(f"\n{'=' * 72}")
    print(f"  {directory}")
    print("=" * 72)

    rows = []
    for cond in ("vanilla", "compile"):
        cells = by_cond.get(cond, [])
        if not cells:
            continue
        n_dispatched = len(cells)
        success = [c for c in cells if c["status"] == "success"]
        n_ok = len(success)
        n_timeout = sum(1 for c in cells if c["status"] == "timeout")
        n_error = sum(1 for c in cells if c["status"] == "error")

        if success:
            po_turns = statistics.mean([c["num_turns"] for c in success])
            po_wall = statistics.mean([c["duration_ms"] for c in success])
            po_cost = statistics.mean([c["cost_usd"] for c in success])
        else:
            po_turns = po_wall = po_cost = 0.0

        # per-dispatched: timeout cell contributes wall=timeout_cap_ms, turns=0,
        # cost=avg success cost. ERROR cells (rate-limit / crash) are
        # infrastructure noise, NOT compile-vs-vanilla signal — exclude from
        # the metric denominator but count in success_rate so they're visible.
        timeout_cost_est = po_cost
        sum_ok_turns = sum(c["num_turns"] for c in success)
        sum_ok_wall = sum(c["duration_ms"] for c in success)
        sum_ok_cost = sum(c["cost_usd"] for c in success)
        # denominator excludes pure infra errors; includes ok + timeout.
        denom = max(1, n_ok + n_timeout)
        pd_turns = (sum_ok_turns + n_timeout * 0) / denom
        pd_wall = (sum_ok_wall + n_timeout * timeout_cap_ms) / denom
        pd_cost = (sum_ok_cost + n_timeout * timeout_cost_est) / denom
        success_rate = n_ok / n_dispatched

        rows.append(
            (
                cond,
                n_dispatched,
                n_ok,
                n_timeout,
                n_error,
                po_turns,
                po_wall,
                po_cost,
                pd_turns,
                pd_wall,
                pd_cost,
                success_rate,
            )
        )

    if any(r[4] for r in rows):
        print(
            "\n  ⚠ ERROR cells detected (rate-limit / crash) — excluded from "
            "metric averages; bench may be contaminated. Re-run when clear."
        )

    print(
        f"\n{'cond':10} {'n':>3} {'ok':>3} {'to':>3} {'err':>3} | "
        f"{'ok_turns':>9} {'ok_wall_s':>10} {'ok_cost':>8} | "
        f"{'disp_turns':>10} {'disp_wall_s':>11} {'disp_cost':>9} | success%"
    )
    print("-" * 112)
    for r in rows:
        (cond, nd, no, nt, ne, pot, pow_, poc, pdt, pdw, pdc, sr) = r
        print(
            f"  {cond:10} {nd:>3} {no:>3} {nt:>3} {ne:>3} | "
            f"{pot:>9.2f} {pow_ / 1000:>9.1f}s ${poc:>6.2f} | "
            f"{pdt:>10.2f} {pdw / 1000:>10.1f}s ${pdc:>6.2f} | {sr * 100:>5.0f}%"
        )

    # Print compile-vs-vanilla deltas for per-DISPATCHED view (the honest one)
    if len(rows) == 2:
        v, c = rows
        # tuple: (cond,nd,no,nt,ne, po_turns,po_wall,po_cost, pd_turns,pd_wall,pd_cost, sr)
        print("\n  per-DISPATCHED deltas (compile vs vanilla — the honest view):")
        print(f"    turns:  vanilla={v[8]:>6.2f}  compile={c[8]:>6.2f}  delta={_percent(c[8], v[8])}")
        print(f"    wall:   vanilla={v[9] / 1000:>5.1f}s  compile={c[9] / 1000:>5.1f}s  delta={_percent(c[9], v[9])}")
        print(f"    cost:   vanilla=${v[10]:>5.2f}  compile=${c[10]:>5.2f}  delta={_percent(c[10], v[10])}")
        print(f"    success: vanilla={v[11] * 100:>3.0f}%  compile={c[11] * 100:>3.0f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directories", nargs="+", help="One or more bench-compile --out-dir paths.")
    ap.add_argument(
        "--timeout-cap", type=int, default=90, help="Wall cap in SECONDS used by the bench run (default 90)."
    )
    args = ap.parse_args()
    for directory in args.directories:
        if not os.path.isdir(directory):
            print(f"skip (not a directory): {directory}", file=sys.stderr)
            continue
        report(directory, args.timeout_cap * 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
