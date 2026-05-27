#!/usr/bin/env python3
"""Per-repo + overall scoreboard for the multi-repo benchmark.

Reads dev/agent_compare_multirepo_results_<tag>.json (or unsuffixed) and
prints:
    1. Per-repo table (vanilla vs roam-agent on each task)
    2. Per-repo aggregate (sums + Δ)
    3. Cross-repo overall (sums + Δ)
    4. Win counts per repo + overall

Tag selection:
    dev/.venv-agent/bin/python dev/agent_compare_multirepo_scoreboard.py [tag1 tag2 ...]

Compares multiple tagged runs side-by-side when given >1 tag.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

HERE = os.path.dirname(__file__)


@dataclass
class Row:
    repo: str
    task: str
    name: str
    turns: int
    tools: int
    roam_pct: float
    wall: float
    cost: float
    out_tok: int
    text_len: int
    is_error: bool


def _roam(tc: dict) -> int:
    return sum(v for k, v in tc.items() if k.startswith("mcp__roam-code__"))


def load(tag: str) -> tuple[str, list[Row]]:
    suffix = f"_{tag}" if tag else ""
    path = os.path.join(HERE, f"agent_compare_multirepo_results{suffix}.json")
    if not os.path.exists(path):
        return path, []
    with open(path) as f:
        data = json.load(f)
    rows: list[Row] = []
    for repo, tasks in data.items():
        for task, runs in tasks.items():
            for r in runs:
                tc = r.get("tool_counts", {})
                total = sum(tc.values()) or 1
                rows.append(
                    Row(
                        repo=repo, task=task, name=r["name"],
                        turns=r.get("total_turns", 0),
                        tools=sum(tc.values()),
                        roam_pct=100.0 * _roam(tc) / total,
                        wall=r.get("wall_time", 0.0),
                        cost=r.get("total_cost", 0.0),
                        out_tok=r.get("output_tokens", 0),
                        text_len=len(r.get("text_output", "") or ""),
                        is_error=bool(r.get("is_error", False)),
                    )
                )
    return path, rows


def per_repo_table(rows: list[Row]) -> None:
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r.repo][r.task].append(r)
    for repo in sorted(by):
        print(f"\n{'=' * 110}\nREPO: {repo}\n{'=' * 110}")
        print(f"{'task':<32} {'agent':<11} {'turns':>5} {'tools':>5} {'roam%':>6} {'wall':>7} {'cost':>8} {'out':>6} {'ans_chars':>10} {'err':>4}")
        print("-" * 110)
        for task in sorted(by[repo]):
            rs = sorted(by[repo][task], key=lambda x: 0 if x.name == "vanilla" else 1)
            for r in rs:
                err = "ERR" if r.is_error else "ok"
                print(
                    f"{task:<32} {r.name:<11} {r.turns:>5} {r.tools:>5} {r.roam_pct:>5.0f}% "
                    f"{r.wall:>6.1f}s ${r.cost:>7.4f} {r.out_tok:>6} {r.text_len:>10} {err:>4}"
                )
            print()


def agg(rows: list[Row]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    by_agent: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_agent[r.name].append(r)
    for a, rs in by_agent.items():
        n = len(rs) or 1
        out[a] = {
            "n": n,
            "turns": sum(r.turns for r in rs),
            "tools": sum(r.tools for r in rs),
            "wall": sum(r.wall for r in rs),
            "cost": sum(r.cost for r in rs),
            "out_tok": sum(r.out_tok for r in rs),
            "ans": sum(r.text_len for r in rs),
            "errs": sum(1 for r in rs if r.is_error),
            "roam_avg": sum(r.roam_pct for r in rs) / n,
        }
    return out


def _print_agg(name: str, a: dict[str, dict[str, float]]) -> None:
    print(f"\n{'=' * 110}\n{name}\n{'=' * 110}")
    print(f"{'agent':<11} {'#':>4} {'Σturns':>7} {'Σtools':>7} {'Σwall':>8} {'Σcost':>9} {'Σout':>8} {'Σans':>8} {'avg roam%':>10} {'err':>4}")
    print("-" * 100)
    for ag in ["vanilla", "roam-agent"]:
        if ag not in a:
            continue
        s = a[ag]
        print(
            f"{ag:<11} {int(s['n']):>4} {int(s['turns']):>7} {int(s['tools']):>7} "
            f"{s['wall']:>7.0f}s ${s['cost']:>8.4f} {int(s['out_tok']):>8} {int(s['ans']):>8} "
            f"{s['roam_avg']:>9.0f}% {int(s['errs']):>4}"
        )
    if "vanilla" in a and "roam-agent" in a:
        v, r = a["vanilla"], a["roam-agent"]
        def d(x, y):
            return f"{(y - x) / max(x, 1) * 100:+.0f}%"
        print(
            f"{'Δ ra/van':<11} {'':>4} {d(v['turns'], r['turns']):>7} {d(v['tools'], r['tools']):>7} "
            f"{d(v['wall'], r['wall']):>8} {d(v['cost'], r['cost']):>9} {d(v['out_tok'], r['out_tok']):>8} {d(v['ans'], r['ans']):>8}"
        )


def wins(rows: list[Row], label: str = "OVERALL") -> None:
    by_task: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.is_error:
            continue
        by_task[(r.repo, r.task)].append(r)
    w = {k: Counter() for k in ("cheapest", "fastest", "fewest_turns", "fewest_tools", "longest_answer")}
    for rs in by_task.values():
        if len(rs) < 2:
            continue
        w["cheapest"][min(rs, key=lambda r: r.cost).name] += 1
        w["fastest"][min(rs, key=lambda r: r.wall).name] += 1
        w["fewest_turns"][min(rs, key=lambda r: r.turns).name] += 1
        w["fewest_tools"][min(rs, key=lambda r: r.tools).name] += 1
        w["longest_answer"][max(rs, key=lambda r: r.text_len).name] += 1
    print(f"\nWIN COUNTS ({label}):")
    print(f"  {'metric':<18} {'vanilla':>10} {'roam-agent':>12}")
    for k, c in w.items():
        print(f"  {k:<18} {c.get('vanilla', 0):>10} {c.get('roam-agent', 0):>12}")


def main() -> None:
    tags = sys.argv[1:] if len(sys.argv) > 1 else ["round1"]
    all_rows: dict[str, list[Row]] = {}
    for tag in tags:
        path, rows = load(tag)
        if not rows:
            print(f"[no data at {path}]")
            continue
        all_rows[tag] = rows
        print(f"\n############## TAG = {tag} ({path}) ##############")
        per_repo_table(rows)
        for repo in sorted({r.repo for r in rows}):
            _print_agg(f"AGGREGATE — {repo}", agg([r for r in rows if r.repo == repo]))
            wins([r for r in rows if r.repo == repo], f"{repo}")
        _print_agg(f"OVERALL — all repos", agg(rows))
        wins(rows, "all repos")

    if len(all_rows) > 1:
        print(f"\n{'#' * 110}\nCROSS-TAG SUMMARY\n{'#' * 110}")
        for tag, rows in all_rows.items():
            a = agg(rows)
            if "roam-agent" in a and "vanilla" in a:
                ra = a["roam-agent"]
                van = a["vanilla"]
                print(
                    f"  {tag:<14} roam-agent ${ra['cost']:.4f} / {ra['ans']:.0f} chars  "
                    f"= ${ra['cost'] / max(ra['ans'], 1) * 1000:.4f}/kchar   "
                    f"vanilla ${van['cost']:.4f} / {van['ans']:.0f}  "
                    f"= ${van['cost'] / max(van['ans'], 1) * 1000:.4f}/kchar"
                )


if __name__ == "__main__":
    main()
