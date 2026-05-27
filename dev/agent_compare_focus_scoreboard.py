#!/usr/bin/env python3
"""Focused 2-agent scoreboard: vanilla vs roam-agent on 10 deep tasks.

Reads dev/agent_compare_focus_results.json and prints:
    1. Per-task table (head-to-head)
    2. Overall scoreboard (sums + ratios)
    3. Win counts (cheapest, fastest, fewest turns, fewest tools)
    4. Synthesis-quality marker (output length as a weak proxy for
       whether the model produced an actual artifact vs hedged)
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass


HERE = os.path.dirname(__file__)
PATH = os.path.join(HERE, "agent_compare_focus_results.json")


@dataclass
class Row:
    task: str
    name: str
    turns: int
    tools: int
    roam_pct: float
    wall: float
    cost: float
    in_tok: int
    out_tok: int
    cache_r: int
    is_error: bool
    text_len: int


def _roam_count(tc: dict) -> int:
    return sum(v for k, v in tc.items() if k.startswith("mcp__roam-code__"))


def load_rows() -> list[Row]:
    if not os.path.exists(PATH):
        return []
    with open(PATH) as f:
        data = json.load(f)
    rows: list[Row] = []
    for task, runs in data.items():
        for r in runs:
            tc = r.get("tool_counts", {})
            total = sum(tc.values()) or 1
            rows.append(
                Row(
                    task=task,
                    name=r["name"],
                    turns=r.get("total_turns", 0),
                    tools=sum(tc.values()),
                    roam_pct=100.0 * _roam_count(tc) / total,
                    wall=r.get("wall_time", 0.0),
                    cost=r.get("total_cost", 0.0),
                    in_tok=r.get("input_tokens", 0),
                    out_tok=r.get("output_tokens", 0),
                    cache_r=r.get("cache_read_tokens", 0),
                    is_error=bool(r.get("is_error", False)),
                    text_len=len(r.get("text_output", "") or ""),
                )
            )
    return rows


def per_task_table(rows: list[Row]) -> None:
    by_task: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_task[r.task].append(r)
    print("\n" + "=" * 120)
    print("PER-TASK HEAD-TO-HEAD (vanilla vs roam-agent)")
    print("=" * 120)
    print(f"{'task':<32} {'agent':<11} {'turns':>5} {'tools':>5} {'roam%':>6} {'wall':>7} {'cost':>8} {'out_tok':>7} {'ans_chars':>10} {'err':>4}")
    print("-" * 120)
    for task in sorted(by_task):
        rs = sorted(by_task[task], key=lambda x: 0 if x.name == "vanilla" else 1)
        for r in rs:
            err = "ERR" if r.is_error else "ok"
            print(
                f"{task:<32} {r.name:<11} {r.turns:>5} {r.tools:>5} {r.roam_pct:>5.0f}% "
                f"{r.wall:>6.1f}s ${r.cost:>7.4f} {r.out_tok:>7} {r.text_len:>10} {err:>4}"
            )
        print()


def overall(rows: list[Row]) -> None:
    by_agent: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_agent[r.name].append(r)
    print("=" * 120)
    print("OVERALL SCOREBOARD")
    print("=" * 120)
    print(f"{'agent':<11} {'#tasks':>6} {'Σ turns':>8} {'Σ tools':>8} {'Σ wall':>8} {'Σ cost':>9} {'Σ out_tok':>10} {'avg roam%':>10} {'Σ ans_chars':>13} {'errors':>7}")
    print("-" * 120)
    rows_summary = {}
    for a in ["vanilla", "roam-agent"]:
        rs = by_agent.get(a, [])
        if not rs:
            continue
        n = len(rs)
        sums = {
            "n": n,
            "turns": sum(r.turns for r in rs),
            "tools": sum(r.tools for r in rs),
            "wall": sum(r.wall for r in rs),
            "cost": sum(r.cost for r in rs),
            "out": sum(r.out_tok for r in rs),
            "ans": sum(r.text_len for r in rs),
            "errs": sum(1 for r in rs if r.is_error),
            "roam_avg": sum(r.roam_pct for r in rs) / n,
        }
        rows_summary[a] = sums
        print(
            f"{a:<11} {sums['n']:>6} {sums['turns']:>8} {sums['tools']:>8} "
            f"{sums['wall']:>7.0f}s ${sums['cost']:>8.4f} {sums['out']:>10} "
            f"{sums['roam_avg']:>9.0f}% {sums['ans']:>13} {sums['errs']:>7}"
        )
    if "vanilla" in rows_summary and "roam-agent" in rows_summary:
        v, r = rows_summary["vanilla"], rows_summary["roam-agent"]
        def ratio(a, b):
            return f"{(b - a) / max(a, 1) * 100:+.0f}%"
        print("-" * 120)
        print(
            f"{'Δ roam-agent vs vanilla':<11} "
            f"{'':>6} {ratio(v['turns'], r['turns']):>8} "
            f"{ratio(v['tools'], r['tools']):>8} "
            f"{ratio(v['wall'], r['wall']):>8} "
            f"{ratio(v['cost'], r['cost']):>9} "
            f"{ratio(v['out'], r['out']):>10} "
            f"{'':>10} "
            f"{ratio(v['ans'], r['ans']):>13}"
        )


def wins(rows: list[Row]) -> None:
    by_task: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.is_error:
            continue
        by_task[r.task].append(r)

    w = {"cheapest": Counter(), "fastest": Counter(), "fewest_turns": Counter(), "fewest_tools": Counter(), "longest_answer": Counter()}
    for task, rs in by_task.items():
        if len(rs) < 2:
            continue
        w["cheapest"][min(rs, key=lambda r: r.cost).name] += 1
        w["fastest"][min(rs, key=lambda r: r.wall).name] += 1
        w["fewest_turns"][min(rs, key=lambda r: r.turns).name] += 1
        w["fewest_tools"][min(rs, key=lambda r: r.tools).name] += 1
        w["longest_answer"][max(rs, key=lambda r: r.text_len).name] += 1

    print("\n" + "=" * 120)
    print("WIN COUNTS")
    print("=" * 120)
    print(f"{'metric':<18} {'vanilla':>10} {'roam-agent':>12}")
    print("-" * 50)
    for metric, c in w.items():
        print(f"{metric:<18} {c.get('vanilla', 0):>10} {c.get('roam-agent', 0):>12}")
    print("\n(longest_answer = weak proxy for synthesis depth; not a quality grade)")


def main() -> None:
    rows = load_rows()
    if not rows:
        print(f"No results at {PATH}")
        return
    per_task_table(rows)
    overall(rows)
    wins(rows)


if __name__ == "__main__":
    main()
