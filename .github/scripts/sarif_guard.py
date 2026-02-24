#!/usr/bin/env python3
"""Merge and guardrail SARIF files before GitHub upload.

This script combines one or more SARIF files and applies conservative
pre-upload limits aligned with GitHub code scanning constraints:

- Max runs per SARIF payload
- Max results per run
- Max file bytes (UTF-8 JSON size, conservative for compressed limits)

When limits are exceeded, findings are truncated from the tail and a summary
is emitted so CI can warn with concrete truncation counts.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
    "main/sarif-2.1/schema/sarif-schema-2.1.0.json"
)


def _json_size_bytes(data: dict) -> int:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8"))


def _read_sarif(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if data.get("version") != _SARIF_VERSION:
        return None
    runs = data.get("runs")
    if not isinstance(runs, list):
        return None
    return data


def _result_count(run: dict) -> int:
    results = run.get("results")
    if isinstance(results, list):
        return len(results)
    return 0


def _count_results(doc: dict) -> int:
    runs = doc.get("runs")
    if not isinstance(runs, list):
        return 0
    return sum(_result_count(r) for r in runs if isinstance(r, dict))


def _ensure_automation_id(run: dict, command: str, index: int):
    auto = run.get("automationDetails")
    if not isinstance(auto, dict):
        auto = {}
    if not auto.get("id"):
        suffix = command if index == 0 else f"{command}/run-{index + 1}"
        auto["id"] = f"roam/{suffix}"
    run["automationDetails"] = auto


def merge_sarif_files(paths: list[Path]) -> tuple[dict, list[str]]:
    """Merge valid SARIF documents into one payload."""
    merged_runs: list[dict] = []
    skipped: list[str] = []

    for path in paths:
        doc = _read_sarif(path)
        if doc is None:
            skipped.append(path.name)
            continue

        command = path.stem
        runs = doc.get("runs", [])
        for idx, run in enumerate(runs):
            if not isinstance(run, dict):
                continue
            run_copy = copy.deepcopy(run)
            _ensure_automation_id(run_copy, command, idx)
            merged_runs.append(run_copy)

    out = {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": merged_runs,
    }
    return out, skipped


def _prune_unused_rules(run: dict):
    tool = run.get("tool")
    if not isinstance(tool, dict):
        return
    driver = tool.get("driver")
    if not isinstance(driver, dict):
        return
    rules = driver.get("rules")
    if not isinstance(rules, list):
        return

    results = run.get("results")
    used = set()
    if isinstance(results, list):
        for res in results:
            if isinstance(res, dict):
                rid = res.get("ruleId")
                if isinstance(rid, str) and rid:
                    used.add(rid)

    if not used:
        driver["rules"] = []
        return
    driver["rules"] = [
        r for r in rules
        if isinstance(r, dict) and r.get("id") in used
    ]


def _drop_results_from_tail(doc: dict, n: int) -> int:
    if n <= 0:
        return 0
    runs = doc.get("runs")
    if not isinstance(runs, list):
        return 0

    dropped = 0
    for run in reversed(runs):
        if dropped >= n:
            break
        if not isinstance(run, dict):
            continue
        results = run.get("results")
        if not isinstance(results, list) or not results:
            continue
        take = min(n - dropped, len(results))
        del results[-take:]
        dropped += take
    return dropped


def _apply_run_cap(doc: dict, max_runs: int) -> tuple[int, int]:
    """Apply run count cap. Returns (dropped_runs, dropped_results)."""
    runs = doc.get("runs")
    if not isinstance(runs, list) or max_runs <= 0:
        return 0, 0
    if len(runs) <= max_runs:
        return 0, 0

    overflow = runs[max_runs:]
    dropped_results = sum(_result_count(r) for r in overflow if isinstance(r, dict))
    dropped_runs = len(overflow)
    del runs[max_runs:]
    return dropped_runs, dropped_results


def _apply_result_cap_per_run(doc: dict, max_results: int) -> int:
    """Cap results per run. Returns total dropped results."""
    if max_results <= 0:
        return 0
    runs = doc.get("runs")
    if not isinstance(runs, list):
        return 0

    dropped = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        results = run.get("results")
        if not isinstance(results, list):
            continue
        if len(results) > max_results:
            dropped += len(results) - max_results
            del results[max_results:]
        _prune_unused_rules(run)
    return dropped


def _apply_size_cap(doc: dict, max_bytes: int) -> tuple[int, int, int, bool]:
    """Trim tail results until payload fits max bytes.

    Returns: (dropped_results, before_bytes, after_bytes, still_oversized)
    """
    before = _json_size_bytes(doc)
    if max_bytes <= 0 or before <= max_bytes:
        return 0, before, before, False

    dropped = 0
    after = before
    # Drop in chunks to keep runtime bounded on large SARIF payloads.
    chunk = 200
    while after > max_bytes:
        just_dropped = _drop_results_from_tail(doc, chunk)
        if just_dropped <= 0:
            break
        dropped += just_dropped
        runs = doc.get("runs")
        if isinstance(runs, list):
            for run in runs:
                if isinstance(run, dict):
                    _prune_unused_rules(run)
        after = _json_size_bytes(doc)

    still_oversized = after > max_bytes
    return dropped, before, after, still_oversized


def apply_guardrails(doc: dict, max_runs: int, max_results: int, max_bytes: int) -> dict:
    """Apply caps in deterministic order and return summary."""
    results_before = _count_results(doc)
    runs_before = len(doc.get("runs", [])) if isinstance(doc.get("runs"), list) else 0

    dropped_runs, dropped_results_runs = _apply_run_cap(doc, max_runs)
    dropped_results_cap = _apply_result_cap_per_run(doc, max_results)
    dropped_results_size, bytes_before, bytes_after, oversized = _apply_size_cap(
        doc, max_bytes,
    )

    results_after = _count_results(doc)
    runs_after = len(doc.get("runs", [])) if isinstance(doc.get("runs"), list) else 0
    dropped_total = (
        dropped_results_runs + dropped_results_cap + dropped_results_size
    )

    return {
        "runs_before": runs_before,
        "runs_after": runs_after,
        "results_before": results_before,
        "results_after": results_after,
        "dropped_runs": dropped_runs,
        "dropped_results_for_run_cap": dropped_results_runs,
        "dropped_results_for_result_cap": dropped_results_cap,
        "dropped_results_for_size_cap": dropped_results_size,
        "results_dropped_total": dropped_total,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "truncated": dropped_runs > 0 or dropped_total > 0,
        "oversized_after_truncation": oversized,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge SARIF files and apply upload guardrails.",
    )
    parser.add_argument(
        "--output", required=True, help="Path to write merged SARIF JSON.",
    )
    parser.add_argument(
        "--summary-out", required=False, default="",
        help="Optional path to write guardrail summary JSON.",
    )
    parser.add_argument(
        "--max-runs", type=int, default=20,
        help="Maximum SARIF runs to keep (default: 20).",
    )
    parser.add_argument(
        "--max-results", type=int, default=25000,
        help="Maximum SARIF results per run (default: 25000).",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=10000000,
        help="Maximum SARIF JSON bytes (default: 10000000).",
    )
    parser.add_argument(
        "sarif_files", nargs="+", help="Input SARIF files to merge.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    in_paths = [Path(p) for p in args.sarif_files]
    merged, skipped = merge_sarif_files(in_paths)
    if not merged["runs"]:
        print("::warning::No valid SARIF inputs; nothing to upload.")
        return 2

    summary = apply_guardrails(
        merged,
        max_runs=args.max_runs,
        max_results=args.max_results,
        max_bytes=args.max_bytes,
    )
    summary["valid_input_files"] = len(in_paths) - len(skipped)
    summary["skipped_input_files"] = skipped

    out_path = Path(args.output)
    out_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    if args.summary_out:
        summary_path = Path(args.summary_out)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if skipped:
        print(f"::warning::Skipped invalid SARIF files: {', '.join(skipped)}")
    if summary["truncated"]:
        print(
            "::warning::SARIF truncated by guardrails "
            f"(dropped_results={summary['results_dropped_total']}, "
            f"dropped_runs={summary['dropped_runs']}).",
        )
    if summary["oversized_after_truncation"]:
        print("::error::SARIF remains oversized after truncation.")
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
