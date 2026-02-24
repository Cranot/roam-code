#!/usr/bin/env python3
"""Summarize agent-eval JSON outputs into publishable artifacts.

Usage:
  python benchmarks/agent-eval/summarize_results.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


EXPECTED_AGENTS = ["claude-code", "claude-code-sonnet", "codex", "gemini-cli"]
EXPECTED_MODES = ["vanilla", "roam-cli", "roam-mcp"]
EXPECTED_TASKS = [
    "react-todo",
    "astro-landing",
    "python-crawler",
    "cpp-calculator",
    "go-loganalyzer",
]


def _is_eval_row(row: dict) -> bool:
    """True when row looks like a single evaluation payload."""
    return all(key in row for key in ("agent", "mode", "task", "scores"))


def _avg(values: list[float | int | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _completion(row: dict) -> bool:
    structure = row.get("structure", {})
    return bool(
        structure.get("readme")
        and structure.get("build", {}).get("has_build_config")
        and structure.get("tests", {}).get("tests_found")
    )


def summarize(results_dir: Path) -> dict:
    rows: list[dict] = []
    for path in sorted(results_dir.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if _is_eval_row(row):
            rows.append(row)

    by_agent: dict[str, list[dict]] = defaultdict(list)
    by_mode: dict[str, list[dict]] = defaultdict(list)
    by_task: dict[str, list[dict]] = defaultdict(list)
    matrix_seen: set[tuple[str, str, str]] = set()

    for row in rows:
        agent = row.get("agent", "unknown")
        mode = row.get("mode", "unknown")
        task = row.get("task", "unknown")
        by_agent[agent].append(row)
        by_mode[mode].append(row)
        by_task[task].append(row)
        matrix_seen.add((agent, mode, task))

    expected = {
        (a, m, t)
        for a in EXPECTED_AGENTS
        for m in EXPECTED_MODES
        for t in EXPECTED_TASKS
    }
    missing = sorted(expected - matrix_seen)

    per_agent = {}
    for agent in sorted(by_agent):
        rows_agent = by_agent[agent]
        per_agent[agent] = {
            "samples": len(rows_agent),
            "avg_health": _avg([r.get("scores", {}).get("health") for r in rows_agent]),
            "avg_aqs": _avg([r.get("aqs", {}).get("aqs") for r in rows_agent]),
            "avg_dead_symbols": _avg([r.get("scores", {}).get("dead_symbols") for r in rows_agent]),
            "completion_rate": round(
                100.0 * sum(1 for r in rows_agent if _completion(r)) / len(rows_agent), 2
            ),
            "grades": dict(Counter(r.get("aqs", {}).get("grade", "N/A") for r in rows_agent)),
        }

    per_mode = {}
    for mode in sorted(by_mode):
        rows_mode = by_mode[mode]
        per_mode[mode] = {
            "samples": len(rows_mode),
            "avg_health": _avg([r.get("scores", {}).get("health") for r in rows_mode]),
            "avg_aqs": _avg([r.get("aqs", {}).get("aqs") for r in rows_mode]),
            "completion_rate": round(
                100.0 * sum(1 for r in rows_mode if _completion(r)) / len(rows_mode), 2
            ),
        }

    per_task = {}
    for task in sorted(by_task):
        rows_task = by_task[task]
        per_task[task] = {
            "samples": len(rows_task),
            "avg_health": _avg([r.get("scores", {}).get("health") for r in rows_task]),
            "avg_aqs": _avg([r.get("aqs", {}).get("aqs") for r in rows_task]),
            "completion_rate": round(
                100.0 * sum(1 for r in rows_task if _completion(r)) / len(rows_task), 2
            ),
        }

    overall = {
        "samples": len(rows),
        "avg_health": _avg([r.get("scores", {}).get("health") for r in rows]),
        "avg_aqs": _avg([r.get("aqs", {}).get("aqs") for r in rows]),
        "completion_rate": round(
            100.0 * sum(1 for r in rows if _completion(r)) / len(rows), 2
        )
        if rows
        else 0.0,
        "grade_distribution": dict(Counter(r.get("aqs", {}).get("grade", "N/A") for r in rows)),
    }

    return {
        "results_path": str(results_dir.as_posix()),
        "expected_matrix_size": len(expected),
        "observed_matrix_size": len(rows),
        "missing_matrix_entries": [
            {"agent": a, "mode": m, "task": t} for (a, m, t) in missing
        ],
        "overall": overall,
        "per_agent": per_agent,
        "per_mode": per_mode,
        "per_task": per_task,
    }


def write_markdown(summary: dict, out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Agent Eval Summary")
    lines.append("")
    lines.append(f"- Expected matrix: `{summary['expected_matrix_size']}`")
    lines.append(f"- Observed results: `{summary['observed_matrix_size']}`")
    lines.append("")

    overall = summary["overall"]
    lines.append("## Overall")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Avg health | {overall['avg_health']} |")
    lines.append(f"| Avg AQS | {overall['avg_aqs']} |")
    lines.append(f"| Completion rate | {overall['completion_rate']}% |")
    lines.append(f"| Grade distribution | `{overall['grade_distribution']}` |")
    lines.append("")

    lines.append("## By Agent")
    lines.append("")
    lines.append("| Agent | Samples | Avg Health | Avg AQS | Completion | Grades |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for agent, payload in summary["per_agent"].items():
        lines.append(
            f"| {agent} | {payload['samples']} | {payload['avg_health']} | "
            f"{payload['avg_aqs']} | {payload['completion_rate']}% | `{payload['grades']}` |"
        )
    lines.append("")

    lines.append("## By Mode")
    lines.append("")
    lines.append("| Mode | Samples | Avg Health | Avg AQS | Completion |")
    lines.append("|---|---:|---:|---:|---:|")
    for mode, payload in summary["per_mode"].items():
        lines.append(
            f"| {mode} | {payload['samples']} | {payload['avg_health']} | "
            f"{payload['avg_aqs']} | {payload['completion_rate']}% |"
        )
    lines.append("")

    missing = summary["missing_matrix_entries"]
    lines.append("## Matrix Coverage")
    lines.append("")
    lines.append(f"- Missing entries: `{len(missing)}`")
    if missing:
        lines.append("- Missing combinations are intentionally listed in `summary.json` for reproducible reruns.")
    lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parent
    results_dir = root / "results"
    summary = summarize(results_dir)

    json_path = results_dir / "summary.json"
    md_path = results_dir / "summary.md"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    write_markdown(summary, md_path)

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
