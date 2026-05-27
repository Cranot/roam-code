#!/usr/bin/env python3
"""Aggregate dev/agent_compare_results.json + dev/agent_compare_wide_results.json
into a single per-agent scoreboard, plus per-task win/loss table.

Outputs three blocks:
    1. Per-task table (one row per agent, all tasks)
    2. Overall scoreboard (sums + averages per agent)
    3. Win counts (cheapest, fastest, fewest turns, lowest tool-call count)

Usage: dev/.venv-agent/bin/python dev/agent_compare_scoreboard.py
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass


HERE = os.path.dirname(__file__)
V1_PATH = os.path.join(HERE, "agent_compare_results.json")
V2_PATH = os.path.join(HERE, "agent_compare_wide_results.json")


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
    rows: list[Row] = []
    for path in (V1_PATH, V2_PATH):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
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

    print("\n" + "=" * 110)
    print("PER-TASK TABLE (vanilla / wired / roam-agent / roam-bash on every task)")
    print("=" * 110)
    header = f"{'task':<32} {'agent':<11} {'turns':>5} {'tools':>5} {'roam%':>6} {'wall':>7} {'cost':>8} {'out_tok':>7} {'ans_chars':>10} {'err':>4}"
    print(header)
    print("-" * 110)
    for task in sorted(by_task):
        for r in sorted(by_task[task], key=lambda x: ["vanilla", "wired", "roam-agent", "roam-bash"].index(x.name) if x.name in ["vanilla", "wired", "roam-agent", "roam-bash"] else 99):
            err = "ERR" if r.is_error else "ok"
            print(
                f"{task:<32} {r.name:<11} {r.turns:>5} {r.tools:>5} {r.roam_pct:>5.0f}% "
                f"{r.wall:>6.1f}s ${r.cost:>7.4f} {r.out_tok:>7} {r.text_len:>10} {err:>4}"
            )


def overall_scoreboard(rows: list[Row]) -> None:
    agents = ["vanilla", "wired", "roam-agent", "roam-bash"]
    by_agent: dict[str, list[Row]] = {a: [r for r in rows if r.name == a] for a in agents}

    print("\n" + "=" * 110)
    print("OVERALL SCOREBOARD (sums across all tasks)")
    print("=" * 110)
    header = f"{'agent':<11} {'#tasks':>6} {'Σ turns':>8} {'Σ tools':>8} {'Σ wall':>8} {'Σ cost':>9} {'Σ out_tok':>10} {'avg roam%':>10} {'errors':>7}"
    print(header)
    print("-" * 110)
    for a in agents:
        rs = by_agent[a]
        if not rs:
            continue
        n = len(rs)
        s_turns = sum(r.turns for r in rs)
        s_tools = sum(r.tools for r in rs)
        s_wall = sum(r.wall for r in rs)
        s_cost = sum(r.cost for r in rs)
        s_out = sum(r.out_tok for r in rs)
        avg_roam = sum(r.roam_pct for r in rs) / n
        errs = sum(1 for r in rs if r.is_error)
        print(
            f"{a:<11} {n:>6} {s_turns:>8} {s_tools:>8} {s_wall:>7.0f}s ${s_cost:>8.4f} "
            f"{s_out:>10} {avg_roam:>9.0f}% {errs:>7}"
        )


def win_counts(rows: list[Row]) -> None:
    by_task: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.is_error:
            continue
        by_task[r.task].append(r)

    wins: dict[str, Counter] = {
        "cheapest": Counter(),
        "fastest": Counter(),
        "fewest_turns": Counter(),
        "fewest_tools": Counter(),
    }
    for task, rs in by_task.items():
        if len(rs) < 2:
            continue
        wins["cheapest"][min(rs, key=lambda r: r.cost).name] += 1
        wins["fastest"][min(rs, key=lambda r: r.wall).name] += 1
        wins["fewest_turns"][min(rs, key=lambda r: r.turns).name] += 1
        wins["fewest_tools"][min(rs, key=lambda r: r.tools).name] += 1

    print("\n" + "=" * 110)
    print("WIN COUNTS (per-task winner; ties counted for all tied agents)")
    print("=" * 110)
    agents = ["vanilla", "wired", "roam-agent", "roam-bash"]
    header = f"{'metric':<16} " + " ".join(f"{a:>11}" for a in agents)
    print(header)
    print("-" * 80)
    for metric, c in wins.items():
        line = f"{metric:<16} " + " ".join(f"{c.get(a, 0):>11}" for a in agents)
        print(line)


def main() -> None:
    rows = load_rows()
    if not rows:
        print("No results found.")
        return
    per_task_table(rows)
    overall_scoreboard(rows)
    win_counts(rows)


if __name__ == "__main__":
    main()
